"""Plan storage of Zaya Planner: owners, next plans, freezing, conflicts.

An owner (an automation or a script) has at most one next plan, which it may
change, plus the plans that are already frozen or running, which only a person
may change. Every change is written to disk right away.
"""

from __future__ import annotations

from collections.abc import Iterable
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
    DISPLAY_SPAN,
    DOMAIN,
    LOGBOOK_NAME,
    SIGNAL_PLANS_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
    PlanResult,
    PlanStatus,
    StepStatus,
)
from .messages import message
from .models import Freeze, Plan, Step

_LOGGER = logging.getLogger(__name__)


def as_aware(value: datetime) -> datetime:
    """Treat a naive datetime as local time and return it in UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(value)


def local_text(value: datetime) -> str:
    """Return a moment as people read it: 23.10 04:50."""
    return f"{dt_util.as_local(value):%d.%m %H:%M}"


def _same_steps(first: list[Step], second: list[Step]) -> bool:
    return len(first) == len(second) and all(a.same_as(b) for a, b in zip(first, second))


class PlanManager:
    """Keep the plans and save every change to disk right away."""

    def __init__(self, hass: HomeAssistant, entry_id: str, fix: timedelta) -> None:
        """Initialize the manager; `fix` is the time to fix before a plan freezes."""
        self.hass = hass
        self._entry_id = entry_id
        self._fix = fix
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True
        )
        self._plans: dict[str, Plan] = {}

    async def async_load(self) -> None:
        """Load plans from disk."""
        data = await self._store.async_load() or {}
        plans = (Plan.from_dict(item) for item in data.get("plans", []))
        self._plans = {plan.uid: plan for plan in plans if plan.steps}

    @property
    def plans(self) -> list[Plan]:
        """Return all plans ordered by their first step."""
        return sorted(self._plans.values(), key=lambda plan: plan.start)

    def get(self, uid: str) -> Plan | None:
        """Return the plan with this uid, if any."""
        return self._plans.get(uid)

    def find(
        self, *, owner: str | None = None, statuses: Iterable[PlanStatus] | None = None
    ) -> list[Plan]:
        """Return plans matching all given filters, ordered by their first step."""
        wanted = set(statuses) if statuses is not None else None
        return [
            plan
            for plan in self.plans
            if (owner is None or plan.owner == owner)
            and (wanted is None or plan.status in wanted)
        ]

    def _next_plan(self, owner: str) -> Plan | None:
        return next(iter(self.find(owner=owner, statuses=[PlanStatus.PLANNED])), None)

    def _locked(self, owner: str) -> list[Plan]:
        return [plan for plan in self.find(owner=owner) if plan.is_locked]

    def freeze_moment(self, plan: Plan) -> datetime:
        """Return when a plan freezes: its freeze time, but not before the time to fix."""
        moment = max(plan.freeze.moment(plan.start), plan.updated_at + self._fix)
        return min(moment, plan.start)

    # ---------------------------------------------------------------- changes

    async def async_set_plan(
        self,
        owner: str,
        steps: list[Step],
        *,
        summary: str = "",
        freeze: Freeze | None = None,
        on_failure: str | None = None,
        display: str = DISPLAY_SPAN,
        force: bool = False,
    ) -> tuple[Plan | None, PlanResult]:
        """Make `steps` the owner's next plan.

        The next plan must start after the owner's frozen and running plans end;
        those are changed only with `force` (a person's choice).
        """
        now = dt_util.utcnow()
        freeze = freeze or Freeze()
        locked = self._locked(owner)
        overlapping = [plan for plan in locked if steps[0].at <= plan.end]
        if overlapping:
            if any(_same_steps(plan.steps, steps) for plan in overlapping):
                # Planned again while it is frozen or running: nothing to do
                return overlapping[0], PlanResult.OVERLAPS
            frozen = [plan for plan in overlapping if plan.status == PlanStatus.FROZEN]
            running = [plan for plan in overlapping if plan.status == PlanStatus.RUNNING]
            if not force or running:
                plan = (running or frozen)[0]
                self._conflict(plan, "change")
                await self._async_changed()
                return plan, PlanResult.OVERLAPS if running else PlanResult.FROZEN
            for plan in frozen:
                self._close(plan, PlanStatus.CANCELLED, message(self.hass, "reason_manual"))

        plan = self._next_plan(owner)
        if plan is not None and (
            _same_steps(plan.steps, steps)
            and (plan.summary, plan.freeze, plan.on_failure, plan.display)
            == (summary, freeze, on_failure, display)
        ):
            if overlapping:
                await self._async_changed()
            return plan, PlanResult.UNCHANGED

        if plan is None:
            plan = Plan(uid=uuid4().hex, owner=owner, steps=steps)
            self._plans[plan.uid] = plan
            result = PlanResult.CREATED
        else:
            plan.steps = steps
            result = PlanResult.UPDATED
        plan.summary = summary
        plan.freeze = freeze
        plan.on_failure = on_failure
        plan.display = display
        plan.updated_at = now
        plan.freeze_moment = self.freeze_moment(plan)
        self.log(
            message(
                self.hass,
                f"plan_{result}",
                summary=plan.display_summary,
                start=local_text(plan.start),
                freeze=local_text(plan.freeze_moment),
            )
        )
        await self._async_changed()
        return plan, result

    async def async_cancel(
        self, owner: str, *, force: bool = False
    ) -> tuple[list[Plan], PlanResult]:
        """Cancel the owner's next plan; with `force` also its frozen and running ones."""
        cancelled: list[Plan] = []
        if (plan := self._next_plan(owner)) is not None:
            self._close(plan, PlanStatus.CANCELLED, message(self.hass, "reason_owner"))
            cancelled.append(plan)
        locked = self._locked(owner)
        if force:
            for plan in locked:
                self._close(plan, PlanStatus.CANCELLED, message(self.hass, "reason_manual"))
                cancelled.append(plan)
        elif locked and not cancelled:
            for plan in locked:
                self._conflict(plan, "cancel")
            await self._async_changed()
            return [], PlanResult.FROZEN
        if not cancelled:
            return [], PlanResult.UNCHANGED
        for plan in cancelled:
            self.log(message(self.hass, "plan_cancelled", summary=plan.display_summary))
        await self._async_changed()
        return cancelled, PlanResult.CANCELLED

    async def async_cancel_by_person(self, uid: str) -> Plan | None:
        """Cancel a plan whatever its state (deleted in the calendar)."""
        plan = self.get(uid)
        if plan is None or not plan.is_open:
            return None
        self._close(plan, PlanStatus.CANCELLED, message(self.hass, "reason_manual"))
        self.log(message(self.hass, "plan_cancelled", summary=plan.display_summary))
        await self._async_changed()
        return plan

    async def async_move_by_person(self, uid: str, delta: timedelta) -> Plan | None:
        """Move all steps of a plan that has not started (moved in the calendar)."""
        plan = self.get(uid)
        if plan is None or plan.status not in (PlanStatus.PLANNED, PlanStatus.FROZEN):
            return None
        for step in plan.steps:
            step.at += delta
        if plan.status == PlanStatus.PLANNED:
            plan.updated_at = dt_util.utcnow()
            plan.freeze_moment = self.freeze_moment(plan)
        self.log(
            message(self.hass, "plan_moved", summary=plan.display_summary, start=local_text(plan.start))
        )
        await self._async_changed()
        return plan

    def _close(self, plan: Plan, status: PlanStatus, reason: str) -> None:
        """Close a plan; its unfinished steps are cancelled."""
        now = dt_util.utcnow()
        plan.status = status
        plan.reason = reason
        plan.finished_at = now
        plan.updated_at = now
        for step in plan.steps:
            if not step.is_finished:
                step.status = StepStatus.CANCELLED
                step.next_attempt = None

    def _conflict(self, plan: Plan, kind: str) -> None:
        """Note (once per plan and kind) that an automation tried to change a frozen plan."""
        text = message(
            self.hass,
            f"conflict_{kind}",
            summary=plan.display_summary,
            frozen=local_text(plan.freeze_moment or plan.start),
            owner=plan.owner,
            now=local_text(dt_util.utcnow()),
        )
        if any(line.startswith(f"{kind}:") for line in plan.conflicts):
            return
        plan.conflicts.append(f"{kind}: {text}")
        self.log(text)

    async def async_cleanup(self, retention: timedelta) -> int:
        """Remove finished plans older than the retention period."""
        limit = dt_util.utcnow() - retention
        old = [
            uid
            for uid, plan in self._plans.items()
            if not plan.is_open and (plan.finished_at or plan.updated_at) < limit
        ]
        for uid in old:
            del self._plans[uid]
        if old:
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
        """Save plans changed in place (by the executor) and tell the entities."""
        await self._async_changed()

    async def _async_changed(self) -> None:
        await self._store.async_save(
            {"plans": [plan.as_dict() for plan in self._plans.values()]}
        )
        async_dispatcher_send(self.hass, f"{SIGNAL_PLANS_UPDATED}_{self._entry_id}")
