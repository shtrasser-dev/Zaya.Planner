"""Executor: runs tasks when they are due, retries and reports.

A task's script is called with the task's params as data and must answer
`{ok: true}` or `{ok: false, reason: "..."}` (stop with response_variable).
Failed attempts are retried with pauses until the task expires.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LATENESS_HOURS,
    CONF_NOTIFY_ACTION,
    DEFAULT_LATENESS_HOURS,
    DEFAULT_NOTIFY_ACTION,
    DOMAIN,
    ERROR_NOTIFY_AFTER,
    EVENT_TASK_DONE,
    EVENT_TASK_FAILED,
    EXPIRES_ALWAYS,
    RETRY_DELAYS,
    SCRIPT_TIMEOUT,
    SIGNAL_TASKS_UPDATED,
    TaskStatus,
)
from .messages import message
from .models import Task
from .store import TaskManager

_LOGGER = logging.getLogger(__name__)


class Executor:
    """Run due tasks; one attempt per task at a time."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, manager: TaskManager
    ) -> None:
        """Initialize the executor."""
        self.hass = hass
        self.entry = entry
        self.manager = manager
        self._unsub_timer: CALLBACK_TYPE | None = None
        # uids of tasks with an attempt in progress
        self._running: set[str] = set()

    @callback
    def async_start(self, _hass: HomeAssistant | None = None) -> None:
        """Start once Home Assistant has started (scripts are loaded by then)."""
        now = dt_util.utcnow()
        interrupted = False
        for task in self.manager.tasks:
            if task.status == TaskStatus.RUNNING:
                # Home Assistant stopped during the attempt: try again
                task.status = TaskStatus.ERROR
                task.last_error = self._text("reason_restart")
                task.error_since = task.error_since or now
                task.next_attempt = now
                interrupted = True
        self.entry.async_on_unload(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_TASKS_UPDATED}_{self.entry.entry_id}",
                self._async_schedule,
            )
        )
        self.entry.async_on_unload(self._async_cancel_timer)
        if interrupted:
            self.entry.async_create_task(self.hass, self.manager.async_save())
        self._async_schedule()

    @callback
    def _async_cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _async_schedule(self) -> None:
        """Start attempts that are due and wake up for the next one."""
        self._async_cancel_timer()
        now = dt_util.utcnow()
        due: list[Task] = []
        wake: datetime | None = None
        for task in self.manager.tasks:
            if not task.is_open or task.next_attempt is None:
                continue
            if task.uid in self._running:
                continue
            if task.next_attempt <= now:
                due.append(task)
            elif wake is None or task.next_attempt < wake:
                wake = task.next_attempt
        if wake is not None:
            self._unsub_timer = async_track_point_in_utc_time(
                self.hass, self._async_on_timer, wake
            )
        for task in due:
            self._running.add(task.uid)
            self.entry.async_create_task(
                self.hass,
                self._async_run(task.uid),
                f"{DOMAIN} {task.id}",
                eager_start=False,
            )

    @callback
    def _async_on_timer(self, _now: datetime) -> None:
        self._unsub_timer = None
        self._async_schedule()

    async def _async_run(self, uid: str) -> None:
        """Make one attempt of a task."""
        try:
            task = self.manager.get(uid)
            if task is None or not task.is_open:
                return
            deadline = self._deadline(task)
            if deadline is not None and dt_util.utcnow() > deadline:
                await self._async_close_late(task)
                return

            task.status = TaskStatus.RUNNING
            task.attempts += 1
            task.updated_at = dt_util.utcnow()
            await self.manager.async_save()

            ok, reason, response = await self._async_call(task)

            if task.status != TaskStatus.RUNNING:
                # Cancelled while the script was running: leave it cancelled
                return
            if ok:
                await self._async_done(task, response)
            else:
                await self._async_error(task, reason, deadline)
        finally:
            self._running.discard(uid)
            self._async_schedule()

    def _deadline(self, task: Task) -> datetime | None:
        """Return when the task is too late to run (None: never)."""
        if task.expires == EXPIRES_ALWAYS:
            return None
        if isinstance(task.expires, datetime):
            return task.expires
        hours = self.entry.options.get(CONF_LATENESS_HOURS, DEFAULT_LATENESS_HOURS)
        return task.due + timedelta(hours=hours)

    async def _async_call(self, task: Task) -> tuple[bool, str, Any]:
        """Call the task's script; return (ok, reason, response)."""
        domain, _, service = task.action.partition(".")
        if not self.hass.services.has_service(domain, service):
            return False, self._text("reason_not_found", action=task.action), None
        try:
            async with asyncio.timeout(SCRIPT_TIMEOUT.total_seconds()):
                response = await self.hass.services.async_call(
                    domain,
                    service,
                    dict(task.params),
                    blocking=True,
                    return_response=True,
                )
        except TimeoutError:
            seconds = int(SCRIPT_TIMEOUT.total_seconds())
            return False, self._text("reason_timeout", seconds=seconds), None
        except HomeAssistantError as err:
            return False, str(err) or type(err).__name__, None
        except Exception as err:  # noqa: BLE001 - a script may fail in any way
            _LOGGER.exception("Task %s: script %s failed", task.id, task.action)
            return False, str(err) or type(err).__name__, None

        if not isinstance(response, dict) or "ok" not in response:
            return False, self._text("reason_no_ok"), response
        reason = str(response.get("reason") or "") or self._text("reason_unknown")
        return bool(response["ok"]), reason, response

    async def _async_done(self, task: Task, response: Any) -> None:
        now = dt_util.utcnow()
        task.status = TaskStatus.DONE
        task.result = response
        task.last_error = None
        task.error_since = None
        task.notified = False
        task.next_attempt = None
        task.finished_at = now
        task.updated_at = now
        await self.manager.async_save()

        if task.attempts > 1:
            text = self._text("done_retry", title=task.display_title, attempts=task.attempts)
        else:
            text = self._text("done", title=task.display_title)
        self.manager.log(text)
        self.hass.bus.async_fire(EVENT_TASK_DONE, self._event_data(task))

    async def _async_error(
        self, task: Task, reason: str, deadline: datetime | None
    ) -> None:
        now = dt_util.utcnow()
        if deadline is not None and now >= deadline:
            task.last_error = reason
            await self._async_close(task, TaskStatus.FAILED, reason)
            return

        first = task.error_since is None
        changed = reason != task.last_error
        task.status = TaskStatus.ERROR
        task.last_error = reason
        task.error_since = task.error_since or now
        task.updated_at = now

        delay = RETRY_DELAYS[min(task.attempts, len(RETRY_DELAYS)) - 1]
        next_attempt = now + delay
        notify_at = task.error_since + ERROR_NOTIFY_AFTER
        if not task.notified and now < notify_at < next_attempt:
            # Try once more exactly when the error is due to be reported
            next_attempt = notify_at
        if deadline is not None and next_attempt > deadline:
            next_attempt = deadline
        task.next_attempt = next_attempt

        notify = not task.notified and now >= notify_at
        if notify:
            task.notified = True
        await self.manager.async_save()

        next_text = f"{dt_util.as_local(next_attempt):%H:%M}"
        if first or changed:
            self.manager.log(
                self._text("error", title=task.display_title, reason=reason, next=next_text)
            )
        if notify:
            await self._async_notify(
                self._text("notify_title_error"),
                self._text(
                    "notify_error", title=task.display_title, reason=reason, next=next_text
                ),
            )

    async def _async_close_late(self, task: Task) -> None:
        """Close a task that is past its deadline."""
        if task.attempts == 0:
            reason = self._text(
                "reason_late", due=f"{dt_util.as_local(task.due):%d.%m %H:%M}"
            )
            await self._async_close(task, TaskStatus.SKIPPED, reason)
        else:
            await self._async_close(
                task, TaskStatus.FAILED, task.last_error or self._text("reason_unknown")
            )

    async def _async_close(self, task: Task, status: TaskStatus, reason: str) -> None:
        """Close the task as failed or skipped and report it."""
        now = dt_util.utcnow()
        task.status = status
        task.last_error = reason
        task.next_attempt = None
        task.finished_at = now
        task.updated_at = now
        await self.manager.async_save()

        kind = "skipped" if status == TaskStatus.SKIPPED else "failed"
        self.manager.log(self._text(kind, title=task.display_title, reason=reason))
        self.hass.bus.async_fire(
            EVENT_TASK_FAILED,
            {**self._event_data(task), "status": str(status), "reason": reason},
        )
        await self._async_notify(
            self._text(f"notify_title_{kind}"),
            self._text("notify_closed", title=task.display_title, reason=reason),
        )

    async def _async_notify(self, title: str, text: str) -> None:
        """Send a notification with the configured action."""
        action = self.entry.options.get(CONF_NOTIFY_ACTION, DEFAULT_NOTIFY_ACTION)
        domain, _, service = (action or "persistent_notification.create").partition(".")
        try:
            await self.hass.services.async_call(
                domain, service, {"title": title, "message": text}, blocking=False
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not send notification with %s: %s", action, err)

    def _event_data(self, task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "uid": task.uid,
            "title": task.display_title,
            "action": task.action,
            "rule_id": task.rule_id,
            "attempts": task.attempts,
            "result": task.result,
        }

    def _text(self, key: str, **kwargs: object) -> str:
        return message(self.hass, key, **kwargs)
