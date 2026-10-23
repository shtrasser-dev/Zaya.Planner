"""Sensors: next step, failed plans, all plans, errors in events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZayaPlannerConfigEntry
from .const import ATTR_PLANS, OPEN_STATUSES, SIGNAL_EVENTS_UPDATED, PlanStatus
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
