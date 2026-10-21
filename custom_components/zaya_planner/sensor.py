"""Sensors: next task, errors and the whole plan."""

from __future__ import annotations

from datetime import datetime
from typing import Any, override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZayaPlannerConfigEntry
from .const import ATTR_TASKS, TaskStatus
from .entity import ZayaPlannerEntity
from .messages import message
from .models import Task

PARALLEL_UPDATES = 0

ERROR_STATUSES = frozenset({TaskStatus.ERROR, TaskStatus.FAILED})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(
        [
            NextTaskSensor(entry, entry.runtime_data, "next_task"),
            ErrorsSensor(entry, entry.runtime_data, "errors"),
            TasksSensor(entry, entry.runtime_data, "tasks"),
        ]
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
    """How many tasks are in error or failed."""

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
