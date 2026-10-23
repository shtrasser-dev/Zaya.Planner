"""Calendar “Plans”: one event per plan, from its first step to its last.

A person may delete an event (the plan is cancelled, even a frozen or running
one) or move it (all steps of a plan that has not started move with it).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, override

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ZayaPlannerConfigEntry
from .const import ATTR_STATE, DISPLAY_STEPS, DOMAIN, EVENT_DURATION, PlanStatus, StepStatus
from .display import plan_description, step_text
from .entity import ZayaPlannerEntity
from .messages import message
from .models import Plan, Step
from .store import as_aware

PARALLEL_UPDATES = 0

# Shown before the summary so the state is visible without opening the event
STATUS_ICONS: dict[PlanStatus, str] = {
    PlanStatus.FROZEN: "❄️",
    PlanStatus.RUNNING: "⏳",
    PlanStatus.DONE: "✅",
    PlanStatus.FAILED: "❌",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the plans calendar."""
    async_add_entities([PlansCalendar(entry)], update_before_add=True)


def _event_end(plan: Plan) -> datetime:
    return max(plan.end, plan.start + EVENT_DURATION)


class PlansCalendar(ZayaPlannerEntity, CalendarEntity):
    """Plans: planned, frozen, running and recently finished.

    Cancelled plans are not shown: a calendar does not return cancelled events.
    """

    _entity_platform = "calendar"
    _attr_supported_features = (
        CalendarEntityFeature.DELETE_EVENT | CalendarEntityFeature.UPDATE_EVENT
    )

    def __init__(self, entry: ZayaPlannerConfigEntry) -> None:
        """Initialize the calendar."""
        super().__init__(entry, entry.runtime_data.manager, "plans")

    @property
    @override
    def event(self) -> CalendarEvent | None:
        """Return the current or next event of an open plan."""
        now = dt_util.utcnow()
        events = [
            event
            for plan in self.manager.plans
            if plan.is_open
            for event in self._events(plan)
            if event.end > now
        ]
        return min(events, key=lambda event: event.start, default=None)

    @override
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the events of plans between the given dates."""
        return [
            event
            for plan in self.manager.plans
            if plan.status != PlanStatus.CANCELLED
            for event in self._events(plan)
            if event.start < end_date and event.end > start_date
        ]

    @override
    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """A person cancels a plan (deleting any of its events)."""
        if await self.manager.async_cancel_by_person(uid.split(":")[0]) is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="plan_not_open")

    @override
    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """A person moves a plan: all its steps move by as much as the moved event."""
        plan_uid, _, index = uid.partition(":")
        plan = self.manager.get(plan_uid)
        start = event.get("dtstart")
        if plan is None or not isinstance(start, (date, datetime)):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="plan_not_open")
        moved = plan.steps[int(index)].at if index else plan.start
        if not isinstance(start, datetime):
            start = datetime.combine(start, moved.time())
        delta = as_aware(start) - moved
        if await self.manager.async_move_by_person(plan_uid, delta) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="plan_started"
            )

    def _events(self, plan: Plan) -> list[CalendarEvent]:
        """One event from the first step to the last, or one short event per step."""
        icon = STATUS_ICONS.get(plan.status)
        summary = f"{icon} {plan.display_summary}" if icon else plan.display_summary
        description = plan_description(self.hass, plan)
        if plan.display != DISPLAY_STEPS:
            return [
                CalendarEvent(
                    start=dt_util.as_local(plan.start),
                    end=dt_util.as_local(_event_end(plan)),
                    summary=summary,
                    description=description,
                    uid=plan.uid,
                )
            ]
        return [
            CalendarEvent(
                start=dt_util.as_local(step.at),
                end=dt_util.as_local(step.at + EVENT_DURATION),
                summary=f"{summary}: {self._step_summary(step)}",
                description=description,
                uid=f"{plan.uid}:{index}",
            )
            for index, step in enumerate(plan.steps)
            if step.status != StepStatus.CANCELLED
        ]

    def _step_summary(self, step: Step) -> str:
        """Only the state (“on”) when the step turns things on or off; the rest is in the description."""
        state = step.data.get(ATTR_STATE)
        if state in ("on", "off"):
            return message(self.hass, f"state_{state}")
        return step_text(self.hass, step)
