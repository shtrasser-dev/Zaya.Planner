"""Executor: runs the steps of plans in turn, retries, reports failures.

A step's action is called with the step's data and must answer `{ok: true}` or
`{ok: false, reason: "..."}` (zaya_planner.ensure_state does). A step that is
not done in time fails its plan: the remaining steps are not run and the plan's
failure instructions (a script) run instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .calls import async_call_script, async_notify, async_run_instructions
from .const import (
    CONF_LATENESS_HOURS,
    DEFAULT_LATENESS_HOURS,
    DOMAIN,
    ERROR_NOTIFY_AFTER,
    EVENT_PLAN_DONE,
    EVENT_PLAN_FAILED,
    RETRY_DELAYS,
    SIGNAL_PLANS_UPDATED,
    PlanStatus,
    StepStatus,
)
from .display import step_info, step_text
from .messages import message
from .models import Plan, Step
from .store import PlanManager, local_text

_LOGGER = logging.getLogger(__name__)


class Executor:
    """Move plans through their statuses; one attempt per plan at a time."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, manager: PlanManager) -> None:
        """Initialize the executor."""
        self.hass = hass
        self.entry = entry
        self.manager = manager
        self._unsub_timer: CALLBACK_TYPE | None = None
        # uids of plans with an attempt in progress
        self._running: set[str] = set()

    @property
    def _lateness(self) -> timedelta:
        hours = self.entry.options.get(CONF_LATENESS_HOURS, DEFAULT_LATENESS_HOURS)
        return timedelta(hours=hours)

    @callback
    def async_start(self, _hass: HomeAssistant | None = None) -> None:
        """Start once Home Assistant has started (scripts are loaded by then)."""
        now = dt_util.utcnow()
        interrupted = False
        for plan in self.manager.find(statuses=[PlanStatus.RUNNING]):
            step = plan.current_step()
            if step is not None and step.status == StepStatus.RUNNING:
                # Home Assistant stopped during the attempt: try again
                step.status = StepStatus.RETRYING
                step.last_error = self._text("reason_restart")
                step.error_since = step.error_since or now
                step.next_attempt = now
                interrupted = True
        self.entry.async_on_unload(
            async_dispatcher_connect(
                self.hass, f"{SIGNAL_PLANS_UPDATED}_{self.entry.entry_id}", self._async_schedule
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

    def _wake(self, plan: Plan) -> datetime | None:
        """Return when something next happens to a plan."""
        if plan.status in (PlanStatus.PLANNED, PlanStatus.FROZEN):
            return plan.start
        if plan.status == PlanStatus.RUNNING and (step := plan.current_step()) is not None:
            return step.next_attempt or step.at
        return None

    @callback
    def _async_schedule(self) -> None:
        """Do what is due and wake up for what comes next."""
        self._async_cancel_timer()
        now = dt_util.utcnow()
        due: list[Plan] = []
        wake: datetime | None = None
        for plan in self.manager.plans:
            if not plan.is_open or plan.uid in self._running:
                continue
            moment = self._wake(plan)
            if moment is None:
                continue
            if moment <= now:
                due.append(plan)
            elif wake is None or moment < wake:
                wake = moment
        if wake is not None:
            self._unsub_timer = async_track_point_in_utc_time(self.hass, self._async_on_timer, wake)
        for plan in due:
            self._running.add(plan.uid)
            self.entry.async_create_task(
                self.hass, self._async_advance(plan.uid), f"{DOMAIN} {plan.owner}", eager_start=False
            )

    @callback
    def _async_on_timer(self, _now: datetime) -> None:
        self._unsub_timer = None
        self._async_schedule()

    async def _async_advance(self, uid: str) -> None:
        """Start a plan or make one attempt of its current step."""
        try:
            plan = self.manager.get(uid)
            if plan is None or not plan.is_open:
                return
            now = dt_util.utcnow()
            if plan.status in (PlanStatus.PLANNED, PlanStatus.FROZEN):
                if now > plan.start + self._lateness:
                    # The first step could not start in time (Home Assistant was off)
                    await self._async_fail(
                        plan, plan.steps[0], self._text("reason_late", due=local_text(plan.start))
                    )
                    return
                # What has started is frozen: it is carried through
                plan.status = PlanStatus.RUNNING
                plan.frozen_at = plan.frozen_at or now
                for step in plan.steps:
                    step.status = StepStatus.WAITING
            step = plan.current_step()
            if step is None:
                await self._async_done(plan)
                return
            await self._async_attempt(plan, step)
        finally:
            self._running.discard(uid)
            self._async_schedule()

    def _deadline(self, plan: Plan, step: Step) -> datetime:
        """Return by when a step must be done: the next step's time, or the lateness limit."""
        index = plan.steps.index(step)
        if index + 1 < len(plan.steps):
            return plan.steps[index + 1].at
        return step.at + self._lateness

    async def _async_attempt(self, plan: Plan, step: Step) -> None:
        """Make one attempt of the current step."""
        step.status = StepStatus.RUNNING
        step.attempts += 1
        plan.updated_at = dt_util.utcnow()
        await self.manager.async_save()

        ok, reason, response = await async_call_script(self.hass, step.action, dict(step.data))

        if plan.status != PlanStatus.RUNNING or step.status != StepStatus.RUNNING:
            # Cancelled while the action was running: leave it cancelled
            return
        now = dt_util.utcnow()
        if ok:
            step.status = StepStatus.DONE
            step.result = response
            step.last_error = None
            step.next_attempt = None
            step.finished_at = now
            if (following := plan.current_step()) is not None:
                following.next_attempt = following.at
                await self.manager.async_save()
            else:
                await self._async_done(plan)
            return
        await self._async_error(plan, step, reason, now)

    async def _async_error(self, plan: Plan, step: Step, reason: str, now: datetime) -> None:
        deadline = self._deadline(plan, step)
        if now >= deadline:
            await self._async_fail(plan, step, reason)
            return

        first = step.error_since is None
        changed = reason != step.last_error
        step.status = StepStatus.RETRYING
        step.last_error = reason
        step.error_since = step.error_since or now

        delay = RETRY_DELAYS[min(step.attempts, len(RETRY_DELAYS)) - 1]
        next_attempt = now + delay
        notify_at = step.error_since + ERROR_NOTIFY_AFTER
        if not step.notified and now < notify_at < next_attempt:
            # Try once more exactly when the error is due to be reported
            next_attempt = notify_at
        step.next_attempt = min(next_attempt, deadline)

        notify = not step.notified and now >= notify_at
        if notify:
            step.notified = True
        await self.manager.async_save()

        what = step_text(self.hass, step)
        next_text = f"{dt_util.as_local(step.next_attempt):%H:%M}"
        if first or changed:
            self.manager.log(
                self._text(
                    "step_error", summary=plan.display_summary, step=what, reason=reason, next=next_text
                )
            )
        if notify:
            await async_notify(
                self.hass,
                self.entry,
                self._text("notify_title_error"),
                self._text(
                    "notify_error", summary=plan.display_summary, step=what, reason=reason, next=next_text
                ),
            )

    async def _async_done(self, plan: Plan) -> None:
        now = dt_util.utcnow()
        plan.status = PlanStatus.DONE
        plan.finished_at = now
        plan.updated_at = now
        await self.manager.async_save()
        self.manager.log(self._text("plan_done", summary=plan.display_summary))
        self.hass.bus.async_fire(EVENT_PLAN_DONE, self._event_data(plan))

    async def _async_fail(self, plan: Plan, step: Step, reason: str) -> None:
        """The step was not done: the plan fails, its failure instructions run."""
        now = dt_util.utcnow()
        step.status = StepStatus.FAILED
        step.last_error = reason
        step.finished_at = now
        step.next_attempt = None
        for other in plan.steps:
            if not other.is_finished:
                other.status = StepStatus.CANCELLED
                other.next_attempt = None
        plan.status = PlanStatus.FAILED
        plan.reason = reason
        plan.finished_at = now
        plan.updated_at = now
        await self.manager.async_save()

        what = step_text(self.hass, step)
        self.manager.log(
            self._text("plan_failed", summary=plan.display_summary, step=what, reason=reason)
        )
        self.hass.bus.async_fire(
            EVENT_PLAN_FAILED, {**self._event_data(plan), "step": step_info(self.hass, step), "reason": reason}
        )
        if plan.on_failure:
            variables = {
                "plan": {"summary": plan.display_summary, "owner": plan.owner, "uid": plan.uid},
                "step": step_info(self.hass, step),
                "reason": reason,
                "done": [step_info(self.hass, s) for s in plan.steps if s.status == StepStatus.DONE],
            }
            ok, error = await async_run_instructions(self.hass, plan.on_failure, variables)
            if ok:
                return
            self.manager.log(
                self._text("instructions_failed", script=plan.on_failure, reason=error)
            )
        await async_notify(
            self.hass,
            self.entry,
            self._text("notify_title_failed"),
            self._text("notify_failed", summary=plan.display_summary, step=what, reason=reason),
        )

    def _event_data(self, plan: Plan) -> dict[str, Any]:
        return {"uid": plan.uid, "owner": plan.owner, "summary": plan.display_summary}

    def _text(self, key: str, **kwargs: object) -> str:
        return message(self.hass, key, **kwargs)
