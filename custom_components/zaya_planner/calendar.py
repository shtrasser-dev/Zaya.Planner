"""Calendar “Schedule”: one event per task."""

from __future__ import annotations

from datetime import datetime
import json
from typing import override

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ZayaPlannerConfigEntry
from .const import (
    ATTR_ENTITY_ID,
    ATTR_STATE,
    BUILTIN_ACTIONS,
    EVENT_DURATION,
    TaskStatus,
)
from .description import build
from .entity import ZayaPlannerEntity
from .messages import message
from .models import Task

PARALLEL_UPDATES = 0

# Shown before the title so the outcome is visible without opening the event
STATUS_ICONS: dict[TaskStatus, str] = {
    TaskStatus.RUNNING: "⏳",
    TaskStatus.DONE: "✅",
    TaskStatus.ERROR: "⚠️",
    TaskStatus.FAILED: "❌",
    TaskStatus.SKIPPED: "⏭️",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the schedule."""
    async_add_entities([ZayaPlannerCalendar(entry)], update_before_add=True)


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
        """Return the current or next open task."""
        now = dt_util.utcnow()
        for task in self.manager.tasks:
            if task.is_open and _event_end(task) > now:
                return self._event(task)
        return None

    @override
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return tasks between the given dates."""
        return [
            self._event(task)
            for task in self.manager.tasks
            if task.status != TaskStatus.CANCELLED
            and task.due < end_date
            and _event_end(task) > start_date
        ]

    def _event(self, task: Task) -> CalendarEvent:
        hass = self.hass
        fields: list[tuple[str, object]] = [
            (message(hass, "label_status"), message(hass, f"status_{task.status}"))
        ]
        if task.action in BUILTIN_ACTIONS and ATTR_ENTITY_ID in task.params:
            # zaya_planner.ensure_state: the devices by name and the state
            fields.append((message(hass, "label_devices"), self._names(task.params[ATTR_ENTITY_ID])))
            state = task.params.get(ATTR_STATE)
            fields.append(
                (message(hass, "label_state"), message(hass, f"window_{state}") if state else None)
            )
        else:
            fields.append((message(hass, "label_action"), task.action))
            if task.params:
                fields.append(
                    (
                        message(hass, "label_params"),
                        json.dumps(task.params, ensure_ascii=False, default=str),
                    )
                )
        if task.attempts:
            fields.append((message(hass, "label_attempts"), task.attempts))
        fields.append((message(hass, "label_error"), task.last_error))
        return CalendarEvent(
            start=dt_util.as_local(task.due),
            end=dt_util.as_local(_event_end(task)),
            summary=f"{icon} {task.display_title}"
            if (icon := STATUS_ICONS.get(task.status))
            else task.display_title,
            description=build(fields),
            uid=task.uid,
        )

    def _names(self, entity_ids: object) -> list[str]:
        """Return the friendly names of entities, or their ids."""
        return [
            state.name if (state := self.hass.states.get(entity_id)) else entity_id
            for entity_id in cv.ensure_list(entity_ids)
        ]

