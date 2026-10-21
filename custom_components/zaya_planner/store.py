"""Task storage of Zaya Planner."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any
from uuid import uuid4

from homeassistant.const import EVENT_LOGBOOK_ENTRY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    LOGBOOK_NAME,
    SIGNAL_TASKS_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
    TaskStatus,
    UpsertResult,
)
from .messages import message
from .models import Task

_LOGGER = logging.getLogger(__name__)


def as_aware(value: datetime) -> datetime:
    """Treat a naive datetime as local time and return it in UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(value)


@dataclass(slots=True, frozen=True)
class TaskDefinition:
    """What the caller of `upsert` says a task should be."""

    id: str
    due: datetime
    action: str
    title: str = ""
    params: dict[str, Any] | None = None
    expires: datetime | str | None = None
    rule_id: str | None = None


class TaskManager:
    """Keep the tasks and save every change to disk right away.

    Tasks survive a restart of Home Assistant exactly as they were: unlike
    restore_state there is no 15 minute delay before the state is written.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the manager."""
        self.hass = hass
        self._entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True
        )
        self._tasks: dict[str, Task] = {}

    async def async_load(self) -> None:
        """Load tasks from disk."""
        data = await self._store.async_load() or {}
        tasks = (Task.from_dict(item) for item in data.get("tasks", []))
        self._tasks = {task.uid: task for task in tasks}

    @property
    def tasks(self) -> list[Task]:
        """Return all tasks ordered by due time."""
        return sorted(self._tasks.values(), key=lambda task: task.due)

    def get(self, uid: str) -> Task | None:
        """Return the task with this uid, if any."""
        return self._tasks.get(uid)

    def get_open(self, task_id: str) -> Task | None:
        """Return the open task with this id, if any."""
        for task in self._tasks.values():
            if task.id == task_id and task.is_open:
                return task
        return None

    def find(
        self,
        *,
        task_id: str | None = None,
        rule_id: str | None = None,
        statuses: Iterable[TaskStatus] | None = None,
    ) -> list[Task]:
        """Return tasks matching all given filters, ordered by due time."""
        wanted = set(statuses) if statuses is not None else None
        return [
            task
            for task in self.tasks
            if (task_id is None or task.id == task_id)
            and (rule_id is None or task.rule_id == rule_id)
            and (wanted is None or task.status in wanted)
        ]

    async def async_upsert(self, definition: TaskDefinition) -> tuple[Task, UpsertResult]:
        """Create the task, or change it while its due time has not come.

        An open task whose due time has come belongs to the executor and is
        left as it is.
        """
        now = dt_util.utcnow()
        due = as_aware(definition.due)
        expires = definition.expires
        if isinstance(expires, datetime):
            expires = as_aware(expires)

        task = self.get_open(definition.id)
        if task is not None and task.due <= now:
            self.log(
                message(
                    self.hass,
                    "locked",
                    title=task.display_title,
                    due=f"{dt_util.as_local(task.due):%d.%m %H:%M}",
                )
            )
            return task, UpsertResult.LOCKED

        if task is None:
            task = Task(uid=uuid4().hex, id=definition.id, due=due, action=definition.action)
            self._tasks[task.uid] = task
            result = UpsertResult.CREATED
        else:
            result = UpsertResult.UPDATED

        task.title = definition.title
        task.due = due
        task.next_attempt = due
        task.action = definition.action
        task.params = dict(definition.params or {})
        task.expires = expires
        task.rule_id = definition.rule_id
        task.updated_at = now
        await self._async_changed()
        return task, result

    async def async_cancel(
        self, *, task_id: str | None = None, rule_id: str | None = None
    ) -> list[Task]:
        """Cancel open tasks.

        By `task_id`: the open task with this id, whatever its state.
        By `rule_id`: the rule's open tasks whose due time has not come yet
        (a rule plans only the future and does not interfere with the executor).
        """
        now = dt_util.utcnow()
        cancelled: list[Task] = []
        for task in self._tasks.values():
            if not task.is_open:
                continue
            if task_id is not None and task.id != task_id:
                continue
            if rule_id is not None and (task.rule_id != rule_id or task.due <= now):
                continue
            task.status = TaskStatus.CANCELLED
            task.finished_at = now
            task.next_attempt = None
            task.updated_at = now
            cancelled.append(task)
        if cancelled:
            await self._async_changed()
        return cancelled

    async def async_cleanup(self, retention: timedelta) -> int:
        """Remove finished tasks older than the retention period."""
        limit = dt_util.utcnow() - retention
        old = [
            uid
            for uid, task in self._tasks.items()
            if not task.is_open
            and (task.finished_at or task.updated_at) < limit
        ]
        for uid in old:
            del self._tasks[uid]
        if old:
            _LOGGER.debug("Removed %d finished tasks", len(old))
            await self._async_changed()
        return len(old)

    @callback
    def log(self, text: str) -> None:
        """Write a message to the Home Assistant logbook."""
        _LOGGER.info(text)
        self.hass.bus.async_fire(
            EVENT_LOGBOOK_ENTRY,
            {"name": LOGBOOK_NAME, "message": text, "domain": DOMAIN},
        )

    async def async_save(self) -> None:
        """Save tasks changed in place (by the executor) and tell the entities."""
        await self._async_changed()

    async def _async_changed(self) -> None:
        await self._store.async_save(
            {"tasks": [task.as_dict() for task in self._tasks.values()]}
        )
        async_dispatcher_send(self.hass, f"{SIGNAL_TASKS_UPDATED}_{self._entry_id}")
