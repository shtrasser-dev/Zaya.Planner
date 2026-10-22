"""Sensors: next task, errors, the whole plan, errors in events, moments."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.util import dt as dt_util, slugify

from . import ZayaPlannerConfigEntry
from .const import (
    ATTR_TASKS,
    CONF_KIND,
    CONF_NAME,
    CONF_OFFSET,
    CONF_SOURCES,
    KIND_MOMENTS,
    SIGNAL_EVENTS_UPDATED,
    SUBENTRY_MOMENT,
    TaskStatus,
)
from .entity import ZayaPlannerEntity
from .messages import message
from .models import Task
from .moments import next_moment
from .services import entity_moment

# Upcoming moments listed in a moment's attributes
UPCOMING_LIMIT = 10

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
            EventErrorsSensor(entry, entry.runtime_data.manager, "event_errors"),
        ]
    )
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_MOMENT:
            continue
        sensor: MomentSensor
        if subentry.data.get(CONF_KIND) == KIND_MOMENTS:
            sensor = EarliestMomentSensor(entry, subentry_id, subentry.data)
        else:
            sensor = CalendarMomentSensor(entry, subentry_id, subentry.data)
        async_add_entities([sensor], config_subentry_id=subentry_id)


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



class EventErrorsSensor(ZayaPlannerSensor):
    """What is not understood in the events of the chosen calendars."""

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the calendars."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_EVENTS_UPDATED}_{self._entry_id}",
                self.async_write_ha_state,
            )
        )

    @property
    @override
    def native_value(self) -> int:
        """Return the number of errors."""
        return len(self.entry.runtime_data.events.errors)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the errors as lines."""
        reader = self.entry.runtime_data.events
        return {"errors": [reader.describe(error) for error in reader.errors]}



# ---------------------------------------------------------------- moments


class MomentSensor(SensorEntity):
    """A moment made with “Add moment”: when something happens next.

    When its value changes, rules are told to plan again (“time to plan”).
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False

    def __init__(
        self, entry: ZayaPlannerConfigEntry, subentry_id: str, data: Mapping[str, Any]
    ) -> None:
        """Initialize the moment."""
        self.entry = entry
        self._subentry_id = subentry_id
        self._attr_name = data[CONF_NAME]
        self._attr_unique_id = f"{entry.entry_id}_moment_{subentry_id}"
        # sensor.zaya_<name>, like the other entities of Zaya
        self.entity_id = f"sensor.zaya_{slugify(data[CONF_NAME])}"
        self._published: datetime | None = None
        self._written = False

    def _value(self) -> datetime | None:
        raise NotImplementedError

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the moment."""
        return self._value()

    @callback
    def _async_publish(self) -> None:
        """Write the state; tell rules if the moment changed."""
        value = self._value()
        self.async_write_ha_state()
        if self._written and value != self._published:
            self.entry.runtime_data.time_to_plan.async_fire()
        self._published, self._written = value, True


class CalendarMomentSensor(MomentSensor):
    """A moment worked out from events of the chosen calendars."""

    _unrecorded_attributes = frozenset({"upcoming"})

    def __init__(
        self, entry: ZayaPlannerConfigEntry, subentry_id: str, data: Mapping[str, Any]
    ) -> None:
        """Initialize the moment."""
        super().__init__(entry, subentry_id, data)
        self._unsub_passed: CALLBACK_TYPE | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the calendars; move on when the moment passes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_EVENTS_UPDATED}_{self.entry.entry_id}",
                self._async_publish,
            )
        )
        self.async_on_remove(self._async_cancel_passed)
        self._async_publish()

    def _moments(self) -> list:
        return self.entry.runtime_data.events.moments.get(self._subentry_id, [])

    @override
    def _value(self) -> datetime | None:
        moment = next_moment(self._moments(), dt_util.utcnow())
        return moment.at if moment else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the event the moment comes from, and the moments after it."""
        now = dt_util.utcnow()
        upcoming = [moment for moment in self._moments() if moment.at > now]
        if not upcoming:
            return None
        event = upcoming[0].event
        return {
            "summary": event.summary,
            "calendar": event.calendar,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "tags": list(event.reading.tags),
            "attributes": dict(event.reading.attributes),
            "upcoming": [
                {"at": moment.at.isoformat(), "summary": moment.event.summary}
                for moment in upcoming[:UPCOMING_LIMIT]
            ],
        }

    @callback
    @override
    def _async_publish(self) -> None:
        super()._async_publish()
        self._async_cancel_passed()
        if self._published is not None:
            self._unsub_passed = async_track_point_in_utc_time(
                self.hass, self._async_on_passed, self._published
            )

    @callback
    def _async_on_passed(self, _now: datetime) -> None:
        self._unsub_passed = None
        self._async_publish()

    @callback
    def _async_cancel_passed(self) -> None:
        if self._unsub_passed is not None:
            self._unsub_passed()
            self._unsub_passed = None


class EarliestMomentSensor(MomentSensor):
    """The earliest of other moments (any timestamp sensors), plus an offset."""

    def __init__(
        self, entry: ZayaPlannerConfigEntry, subentry_id: str, data: Mapping[str, Any]
    ) -> None:
        """Initialize the moment."""
        super().__init__(entry, subentry_id, data)
        self._sources: list[str] = list(data.get(CONF_SOURCES) or [])
        self._offset = timedelta(minutes=data.get(CONF_OFFSET) or 0)

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the sources."""
        self.async_on_remove(
            async_track_state_change_event(self.hass, self._sources, self._async_on_source)
        )
        self._async_publish()

    @callback
    def _async_on_source(self, _event: Event[EventStateChangedData]) -> None:
        self._async_publish()

    def _earliest(self) -> tuple[str, datetime] | None:
        known = [
            (source, moment)
            for source in self._sources
            if (moment := entity_moment(self.hass, source)) is not None
        ]
        return min(known, key=lambda item: item[1]) if known else None

    @override
    def _value(self) -> datetime | None:
        earliest = self._earliest()
        return earliest[1] + self._offset if earliest else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return which source is the earliest."""
        earliest = self._earliest()
        return {"source": earliest[0]} if earliest else None
