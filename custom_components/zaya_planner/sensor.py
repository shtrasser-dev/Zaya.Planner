"""Sensors: next step, failed plans, all plans, errors in events, calendar sensors."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from . import ZayaPlannerConfigEntry
from .const import (
    ATTR_PLANS,
    ATTR_TIMES,
    OPEN_STATUSES,
    SIGNAL_EVENTS_UPDATED,
    SUBENTRY_CALENDAR_SENSOR,
    PlanStatus,
)
from .calendar_sensor import CalendarSensorSpec, EventTime, upcoming
from .display import plan_info, step_text
from .entity import ZayaPlannerEntity
from .models import Plan, Step

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    manager = entry.runtime_data.manager
    async_add_entities(
        [
            NextStepSensor(entry, manager, "next_step"),
            FailedPlansSensor(entry, manager, "failed_plans"),
            PlansSensor(entry, manager, "plans"),
            EventErrorsSensor(entry, manager, "event_errors"),
        ]
    )
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_CALENDAR_SENSOR:
            async_add_entities(
                [CalendarSensor(entry, subentry)], config_subentry_id=subentry_id
            )


class ZayaPlannerSensor(ZayaPlannerEntity, SensorEntity):
    """Base sensor."""

    _entity_platform = "sensor"


class NextStepSensor(ZayaPlannerSensor):
    """When the next step of an open plan is due."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def _next(self) -> tuple[Plan, Step] | None:
        pending = [
            (plan, step)
            for plan in self.manager.find(statuses=OPEN_STATUSES)
            if (step := plan.current_step()) is not None
        ]
        return min(pending, key=lambda item: item[1].next_attempt or item[1].at, default=None)

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return when the next step is due."""
        pending = self._next()
        return (pending[1].next_attempt or pending[1].at) if pending else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the plan and what the step does."""
        if (pending := self._next()) is None:
            return None
        plan, step = pending
        return {
            "plan": plan.display_summary,
            "owner": plan.owner,
            "status": str(plan.status),
            "step": step_text(self.hass, step),
        }


class FailedPlansSensor(ZayaPlannerSensor):
    """How many plans failed (kept for the retention period)."""

    def _failed(self) -> list[Plan]:
        return self.manager.find(statuses=[PlanStatus.FAILED])

    @property
    @override
    def native_value(self) -> int:
        """Return the number of failed plans."""
        return len(self._failed())

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the failed plans."""
        return {ATTR_PLANS: [plan_info(self.hass, plan) for plan in self._failed()]}


class PlansSensor(ZayaPlannerSensor):
    """Number of open plans; all plans in attributes (for a markdown card)."""

    # The list can be long and changes often: keep it out of the history
    _unrecorded_attributes = frozenset({ATTR_PLANS})

    @property
    @override
    def native_value(self) -> int:
        """Return the number of open plans."""
        return len(self.manager.find(statuses=OPEN_STATUSES))

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all plans ordered by their first step."""
        return {ATTR_PLANS: [plan_info(self.hass, plan) for plan in self.manager.plans]}


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


def _duration_text(value: timedelta) -> str:
    """Return a duration as people write it in events: 00:40:00."""
    minutes, seconds = divmod(int(value.total_seconds()), 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}:{seconds:02d}"


def _event_info(time: EventTime) -> dict[str, Any]:
    """Return the event a time comes from."""
    event = time.event
    return {
        "summary": event.summary,
        "calendar": event.calendar,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "location": event.location,
    }


class CalendarSensor(SensorEntity):
    """A sensor made with “Add calendar sensor”: the next time of events with tags.

    With a horizon, the `times` attribute lists every time within it. When the
    next time passes, rules are told to plan again (“time to plan”); changes of
    the calendars tell them anyway. Until the calendars are read it is
    unavailable: nothing is known yet, which is not “no time”.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: ZayaPlannerConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the sensor; its entity id comes from its name."""
        self.entry = entry
        self._subentry_id = subentry.subentry_id
        self._spec = CalendarSensorSpec.from_data(subentry.data)
        self._attr_name = subentry.title
        # The unique id of its first version (departures) is kept
        self._attr_unique_id = f"{entry.entry_id}_departure_{subentry.subentry_id}"
        self._unsub_passed: CALLBACK_TYPE | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the calendars; move on when the next time passes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_EVENTS_UPDATED}_{self.entry.entry_id}",
                self._async_publish,
            )
        )
        self.async_on_remove(self._async_cancel_passed)
        self._async_publish()

    def _times(self) -> list[EventTime]:
        return self.entry.runtime_data.events.sensor_times.get(self._subentry_id, [])

    def _next(self) -> EventTime | None:
        return next(iter(upcoming(self._times(), dt_util.utcnow(), None)), None)

    @property
    @override
    def available(self) -> bool:
        """Return False until the calendars are read."""
        return self._subentry_id in self.entry.runtime_data.events.sensor_times

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the next time."""
        time = self._next()
        return time.at if time else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the event of the next time, its offset, and the times within the horizon."""
        attributes: dict[str, Any] = {}
        if (time := self._next()) is not None:
            attributes = {
                **_event_info(time),
                "offset": _duration_text(time.offset),
                "default_offset": time.default_offset,
            }
        if self._spec.horizon is not None:
            attributes[ATTR_TIMES] = [
                {"at": dt_util.as_local(item.at).isoformat(), **_event_info(item)}
                for item in upcoming(self._times(), dt_util.utcnow(), self._spec.horizon)
            ]
        return attributes or None

    @callback
    def _async_publish(self) -> None:
        """Write the state; wake up when the next time passes."""
        self.async_write_ha_state()
        self._async_cancel_passed()
        if (time := self._next()) is not None:
            self._unsub_passed = async_track_point_in_utc_time(
                self.hass, self._async_on_passed, time.at
            )

    @callback
    def _async_on_passed(self, _now: datetime) -> None:
        self._unsub_passed = None
        self._async_publish()
        self.entry.runtime_data.time_to_plan.async_fire()

    @callback
    def _async_cancel_passed(self) -> None:
        if self._unsub_passed is not None:
            self._unsub_passed()
            self._unsub_passed = None
