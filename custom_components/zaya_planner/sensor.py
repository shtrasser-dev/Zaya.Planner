"""Sensors: next task, errors and the whole plan."""

from __future__ import annotations

from datetime import datetime
from typing import Any, override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZayaPlannerConfigEntry
from .const import ATTR_TASKS, SIGNAL_PLAN_UPDATED, TaskStatus
from .entity import HouseholdEntity, ZayaPlannerEntity
from .household import Car, Person
from .messages import message
from .models import Task
from .plan import Departure, WakeUp

PARALLEL_UPDATES = 0

ERROR_STATUSES = frozenset({TaskStatus.ERROR, TaskStatus.FAILED, TaskStatus.SKIPPED})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(
        [
            NextTaskSensor(entry, entry.runtime_data.manager, "next_task"),
            ErrorsSensor(entry, entry.runtime_data.manager, "errors"),
            TasksSensor(entry, entry.runtime_data.manager, "tasks"),
            HouseFirstWakeUpSensor(entry, entry.runtime_data.manager, "first_wake_up"),
        ]
    )
    async_add_entities(
        [
            sensor
            for person in entry.runtime_data.people
            for sensor in (
                PersonWakeUpSensor(entry, person),
                PersonDepartureSensor(entry, person),
            )
        ]
    )
    for car in entry.runtime_data.cars:
        async_add_entities(
            [CarFirstDepartureSensor(entry, car)], config_subentry_id=car.car_id
        )


class ZayaPlannerSensor(ZayaPlannerEntity, SensorEntity):
    """Base sensor."""

    _entity_platform = "sensor"

    def _brief(self, task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "title": task.display_title,
            "due": task.due.isoformat(),
            "status": str(task.status),
            "status_text": message(self.hass, f"status_{task.status}"),
        }


class NextTaskSensor(ZayaPlannerSensor):
    """When the next planned task is due."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def _next(self) -> Task | None:
        return next(
            (task for task in self.manager.tasks if task.status == TaskStatus.SCHEDULED),
            None,
        )

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the due time of the next task."""
        task = self._next()
        return task.due if task else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return what the next task is."""
        task = self._next()
        if task is None:
            return None
        return {**self._brief(task), "action": task.action, "params": task.params}


class ErrorsSensor(ZayaPlannerSensor):
    """How many tasks are in error, failed or skipped."""

    def _errors(self) -> list[Task]:
        return [task for task in self.manager.tasks if task.status in ERROR_STATUSES]

    @property
    @override
    def native_value(self) -> int:
        """Return the number of problem tasks."""
        return len(self._errors())

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the problem tasks."""
        return {
            ATTR_TASKS: [
                {**self._brief(task), "last_error": task.last_error}
                for task in self._errors()
            ]
        }


class TasksSensor(ZayaPlannerSensor):
    """Number of open tasks; all tasks in attributes (for a markdown card)."""

    # The list can be long and changes often: keep it out of the history
    _unrecorded_attributes = frozenset({ATTR_TASKS})

    @property
    @override
    def native_value(self) -> int:
        """Return the number of open tasks."""
        return sum(1 for task in self.manager.tasks if task.is_open)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all tasks ordered by due time."""
        return {
            ATTR_TASKS: [
                {**task.as_dict(), "status_text": message(self.hass, f"status_{task.status}")}
                for task in self.manager.tasks
            ]
        }



# ---------------------------------------------------------------- anchors


class PlanFollower(Entity):
    """Mixin: write the state when the house plan changes."""

    _entry_id: str

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the plan."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_PLAN_UPDATED}_{self._entry_id}",
                self.async_write_ha_state,
            )
        )


def _departure_attributes(entry: ZayaPlannerConfigEntry, departure: Departure) -> dict[str, Any]:
    data = entry.runtime_data
    names = {person.entity_id: person.name for person in data.people}
    cars = {car.car_id: car.name for car in data.cars}
    return {
        "summary": departure.summary,
        "owner": names.get(departure.owner, departure.owner),
        "trip": str(departure.trip),
        "arrive": departure.arrive.isoformat(),
        "travel_minutes": departure.travel_minutes,
        "riders": [names.get(r, r) for r in departure.riders],
        "driver": names.get(departure.driver) if departure.driver else None,
        "car": cars.get(departure.car) if departure.car else None,
    }


class PersonWakeUpSensor(PlanFollower, HouseholdEntity, SensorEntity):
    """When the person gets up next."""

    _entity_platform = "sensor"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: ZayaPlannerConfigEntry, person: Person) -> None:
        """Initialize the anchor."""
        super().__init__(entry, person=person, key="next_wake_up")
        self.person = person

    def _wake(self) -> WakeUp | None:
        return self.entry.runtime_data.house.next_wake_up(self.person.entity_id)

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the time of the next wake-up."""
        wake = self._wake()
        return wake.at if wake else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return why: the departure it is for, or the calendar's wake-up."""
        wake = self._wake()
        if wake is None:
            return None
        if wake.departure is None:
            return {"reason": "calendar", "summary": wake.summary}
        return {
            "reason": "departure",
            "departure": wake.departure.at.isoformat(),
            **_departure_attributes(self.entry, wake.departure),
        }


class PersonDepartureSensor(PlanFollower, HouseholdEntity, SensorEntity):
    """When the person leaves next."""

    _entity_platform = "sensor"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: ZayaPlannerConfigEntry, person: Person) -> None:
        """Initialize the anchor."""
        super().__init__(entry, person=person, key="next_departure")
        self.person = person

    def _departure(self) -> Departure | None:
        return self.entry.runtime_data.house.next_departure(self.person.entity_id)

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the time of the next departure."""
        departure = self._departure()
        return departure.at if departure else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return where and how."""
        departure = self._departure()
        return _departure_attributes(self.entry, departure) if departure else None


class CarFirstDepartureSensor(PlanFollower, HouseholdEntity, SensorEntity):
    """The car's next first departure of a day (it is charged for it)."""

    _entity_platform = "sensor"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: ZayaPlannerConfigEntry, car: Car) -> None:
        """Initialize the anchor."""
        super().__init__(entry, car=car, key="first_departure")
        self.car = car

    def _departure(self) -> Departure | None:
        return self.entry.runtime_data.house.next_first_departure(self.car.car_id)

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the time of the departure."""
        departure = self._departure()
        return departure.at if departure else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return where and who, and the car's charging settings for rules."""
        departure = self._departure()
        settings = self.entry.runtime_data.settings.car(self.car)
        return {
            "charger": self.car.charger,
            "charge_margin": settings["charge_margin"],
            **(_departure_attributes(self.entry, departure) if departure else {}),
        }


class HouseFirstWakeUpSensor(PlanFollower, ZayaPlannerSensor):
    """The next wake-up of anyone in the house."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the time of the next wake-up."""
        wake = self.entry.runtime_data.house.first_wake_up()
        return wake.at if wake else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return who gets up."""
        wake = self.entry.runtime_data.house.first_wake_up()
        if wake is None:
            return None
        names = {person.entity_id: person.name for person in self.entry.runtime_data.people}
        return {"person": names.get(wake.person, wake.person)}
