"""Calendar “Schedule”: an event per task and per next rule run."""

from __future__ import annotations

from datetime import datetime
import json
from typing import override

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ZayaPlannerConfigEntry
from .const import EVENT_DURATION, TaskStatus
from .entity import ZayaPlannerEntity
from .messages import message
from .models import Task
from .rules import rules_from_entry

PARALLEL_UPDATES = 0

# Shown before the title so the outcome is visible without opening the event
STATUS_ICONS: dict[TaskStatus, str] = {
    TaskStatus.RUNNING: "⏳",
    TaskStatus.DONE: "✅",
    TaskStatus.ERROR: "⚠️",
    TaskStatus.FAILED: "❌",
    TaskStatus.SKIPPED: "⏭️",
}
# A rule run: planning, not doing
RULE_ICON = "🗓️"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calendar."""
    async_add_entities([ZayaPlannerCalendar(entry)])


def _event_end(task: Task) -> datetime:
    end = task.due + EVENT_DURATION
    if task.finished_at is not None and task.finished_at > end:
        end = task.finished_at
    return end


class ZayaPlannerCalendar(ZayaPlannerEntity, CalendarEntity):
    """Tasks of the schedule, planned and recently finished.

    Cancelled tasks are not shown: a calendar does not return cancelled events.
    """

    _entity_platform = "calendar"

    def __init__(self, entry: ZayaPlannerConfigEntry) -> None:
        """Initialize the calendar."""
        super().__init__(entry, entry.runtime_data.manager, "schedule")

    @property
    @override
    def event(self) -> CalendarEvent | None:
        """Return the current or next open task or rule run."""
        now = dt_util.now()
        events = [
            self._event(task)
            for task in self.manager.tasks
            if task.is_open and _event_end(task) > dt_util.utcnow()
        ] + [event for event in self._rule_events() if event.end > now]
        return min(events, key=lambda event: event.start, default=None)

    @override
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return tasks and rule runs between the given dates."""
        tasks = [
            self._event(task)
            for task in self.manager.tasks
            if task.status != TaskStatus.CANCELLED
            and task.due < end_date
            and _event_end(task) > start_date
        ]
        rules = [
            event
            for event in self._rule_events()
            if event.start < end_date and event.end > start_date
        ]
        return sorted(tasks + rules, key=lambda event: event.start)

    def _rule_events(self) -> list[CalendarEvent]:
        """Return the next run of each enabled rule."""
        events = []
        for rule in rules_from_entry(self.entry):
            state = self.manager.rule_states.get(rule.rule_id)
            if not rule.enabled or state is None or state.next_run is None:
                continue
            start = dt_util.as_local(state.next_run)
            events.append(
                CalendarEvent(
                    start=start,
                    end=start + EVENT_DURATION,
                    summary=f"{RULE_ICON} {rule.title}",
                    description=f"{message(self.hass, 'label_planner')}: {rule.planner}",
                    uid=f"rule_{rule.rule_id}",
                )
            )
        return events

    def _event(self, task: Task) -> CalendarEvent:
        hass = self.hass
        lines = [
            f"{message(hass, 'label_status')}: {message(hass, f'status_{task.status}')}",
            f"{message(hass, 'label_action')}: {task.action}",
        ]
        if task.params:
            params = json.dumps(task.params, ensure_ascii=False, default=str)
            lines.append(f"{message(hass, 'label_params')}: {params}")
        if task.attempts:
            lines.append(f"{message(hass, 'label_attempts')}: {task.attempts}")
        if task.last_error:
            lines.append(f"{message(hass, 'label_error')}: {task.last_error}")
        return CalendarEvent(
            start=dt_util.as_local(task.due),
            end=dt_util.as_local(_event_end(task)),
            summary=f"{icon} {task.display_title}"
            if (icon := STATUS_ICONS.get(task.status))
            else task.display_title,
            description="\n".join(lines),
            uid=task.uid,
        )
