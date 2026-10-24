"""Sensors: next step, failed plans, all plans, errors in events, departures."""

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
    OPEN_STATUSES,
    SIGNAL_EVENTS_UPDATED,
    SUBENTRY_DEPARTURE,
    PlanStatus,
)
from .departures import Departure, next_departure
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
        if subentry.subentry_type == SUBENTRY_DEPARTURE:
            async_add_entities(
                [DepartureSensor(entry, subentry)], config_subentry_id=subentry_id
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


class DepartureSensor(SensorEntity):
    """A departure made with “Add departure”: when to leave next.

    When a departure passes, rules are told to plan again (“time to plan”) for
    the next one; changes of the calendars tell them anyway.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False
    _attr_icon = "mdi:car-clock"

    def __init__(self, entry: ZayaPlannerConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the departure; its entity id comes from its name."""
        self.entry = entry
        self._subentry_id = subentry.subentry_id
        self._attr_name = subentry.title
        self._attr_unique_id = f"{entry.entry_id}_departure_{subentry.subentry_id}"
        self._unsub_passed: CALLBACK_TYPE | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the calendars; move on when the departure passes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_EVENTS_UPDATED}_{self.entry.entry_id}",
                self._async_publish,
            )
        )
        self.async_on_remove(self._async_cancel_passed)
        self._async_publish()

    def _next(self) -> Departure | None:
        departures = self.entry.runtime_data.events.departures.get(self._subentry_id, [])
        return next_departure(departures, dt_util.utcnow())

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return when to leave."""
        departure = self._next()
        return departure.at if departure else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the event and the travel time the departure comes from."""
        if (departure := self._next()) is None:
            return None
        event = departure.event
        return {
            "summary": event.summary,
            "calendar": event.calendar,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "travel": _duration_text(departure.travel),
            "default_travel": departure.default_travel,
        }

    @callback
    def _async_publish(self) -> None:
        """Write the state; wake up when the departure passes."""
        self.async_write_ha_state()
        self._async_cancel_passed()
        if (departure := self._next()) is not None:
            self._unsub_passed = async_track_point_in_utc_time(
                self.hass, self._async_on_passed, departure.at
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
