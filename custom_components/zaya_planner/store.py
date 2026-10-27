"""Plan storage of Zaya Planner: owners, next plans, freezing, conflicts.

An owner (an automation or a script) changes only its planned plans; the plans
that are frozen or running only a person may change. With “Set plan” an owner
has one next plan; with “Turn on for a while” and “Plan marks” a set of them,
one for each time of its entities.

What a person decided stays: a plan a person moved is frozen, and a plan a
person moved or cancelled is not made again while its owner plans the same.
Every change is written to disk right away.
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
from .description import norm
from .models import Plan, Step

_LOGGER = logging.getLogger(__name__)


def as_aware(value: datetime) -> datetime:
    """Treat a naive datetime as local time and return it in UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(value)


def local_text(value: datetime) -> str:
    """Return a moment as people read it: 23.10 04:50."""
    return f"{dt_util.as_local(value):%d.%m %H:%M}"


def _has_tag(plan: Plan, tags: list[str]) -> bool:
    """Return True if the plan has any of the tags; no tags asked: any plan."""
    have = {norm(tag) for tag in plan.tags}
    return not tags or any(norm(tag) in have for tag in tags)


def _same_steps(first: list[Step], second: list[Step]) -> bool:
    return len(first) == len(second) and all(a.same_as(b) for a, b in zip(first, second))


class PlanManager:
    """Keep the plans and save every change to disk right away."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the manager."""
        self.hass = hass
        self._entry_id = entry_id
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

    def _decided(self, owner: str, steps: list[Step]) -> Plan | None:
        """Return the owner's plan with these steps that a person moved or cancelled."""
        return next(
            (
                plan
                for plan in self.find(owner=owner)
                if plan.by_person and _same_steps(plan.planned_steps, steps)
            ),
            None,
        )

    # ---------------------------------------------------------------- changes

    async def async_set_plan(
        self,
        owner: str,
        steps: list[Step],
        *,
        summary: str = "",
        tags: list[str] | None = None,
        on_failure: str | None = None,
        display: str = DISPLAY_SPAN,
        force: bool = False,
    ) -> None:
        """Make `steps` the owner's next plan.

        The next plan must start after the owner's frozen and running plans end;
        those are changed only with `force` (a person's choice). A plan a person
        moved or cancelled is not made again.
        """
        now = dt_util.utcnow()
        tags = list(tags or [])
        if not force and (decided := self._decided(owner, steps)) is not None:
            return
        locked = self._locked(owner)
        overlapping = [plan for plan in locked if steps[0].at <= plan.end]
        if overlapping:
            if any(_same_steps(plan.steps, steps) for plan in overlapping):
                # Planned again while it is frozen or running: nothing to do
                return
            frozen = [plan for plan in overlapping if plan.status == PlanStatus.FROZEN]
            running = [plan for plan in overlapping if plan.status == PlanStatus.RUNNING]
            if not force or running:
                plan = (running or frozen)[0]
                self._conflict(plan, "change")
                await self._async_changed()
                return
            for plan in frozen:
                self._close(plan, PlanStatus.CANCELLED, message(self.hass, "reason_manual"))

        plan = self._next_plan(owner)
        if plan is not None and (
            _same_steps(plan.steps, steps)
            and (plan.summary, plan.tags, plan.on_failure, plan.display)
            == (summary, tags, on_failure, display)
        ):
            if overlapping:
                await self._async_changed()
            return

        if plan is None:
            plan = Plan(uid=uuid4().hex, owner=owner, steps=steps)
            self._plans[plan.uid] = plan
            result = PlanResult.CREATED
        else:
            plan.steps = steps
            # The owner plans something else now: what a person decided is over
            plan.origin = None
            plan.by_person = False
            result = PlanResult.UPDATED
        plan.summary = summary
        plan.tags = tags
        plan.on_failure = on_failure
        plan.display = display
        plan.updated_at = now
        self.log(
            message(
                self.hass, f"plan_{result}", summary=plan.display_summary, start=local_text(plan.start)
            )
        )
        await self._async_changed()

    async def async_set_plans(
        self,
        owner: str,
        plans: list[tuple[str, list[Step]]],
        *,
        tags: list[str] | None = None,
        display: str = DISPLAY_SPAN,
    ) -> None:
        """Make these (summary, steps) the owner's plans, as a set.

        A plan that is there already (by its steps as the owner made them) is
        kept, also when frozen, running, finished, or moved or cancelled by a
        person; the others are made; the owner's planned plans that are not
        among them are cancelled.
        """
        tags = list(tags or [])
        kept: set[str] = set()
        changed = False
        for summary, steps in plans:
            plan = next(
                (
                    plan
                    for plan in self.find(owner=owner)
                    # Not a plan its owner cancelled: one that is open, finished, or
                    # decided by a person (moved earlier and done, deleted)
                    if (plan.status != PlanStatus.CANCELLED or plan.by_person)
                    and _same_steps(plan.planned_steps, steps)
                ),
                None,
            )
            if plan is None and (
                locked := [
                    plan
                    for plan in self._locked(owner)
                    if steps[0].at <= plan.end and steps[-1].at >= plan.start
                ]
            ):
                # It would overlap a frozen or running plan: that one stays
                noted = len(locked[0].conflicts)
                self._conflict(locked[0], "change")
                changed = changed or len(locked[0].conflicts) > noted
                continue
            if plan is None:
                plan = Plan(
                    uid=uuid4().hex,
                    owner=owner,
                    steps=steps,
                    summary=summary,
                    tags=list(tags),
                    display=display,
                )
                self._plans[plan.uid] = plan
                result = PlanResult.CREATED
            elif plan.status == PlanStatus.PLANNED and (
                plan.summary,
                plan.tags,
                plan.display,
            ) != (summary, tags, display):
                plan.summary = summary
                plan.tags = list(tags)
                plan.display = display
                plan.updated_at = dt_util.utcnow()
                result = PlanResult.UPDATED
            else:
                kept.add(plan.uid)
                continue
            kept.add(plan.uid)
            changed = True
            self.log(
                message(
                    self.hass,
                    f"plan_{result}",
                    summary=plan.display_summary,
                    start=local_text(plan.start),
                )
            )
        now = dt_util.utcnow()
        for plan in self.find(owner=owner, statuses=[PlanStatus.PLANNED]):
            # A plan whose time has come is left to run: its time may be gone
            # from the owner's entity just because it has come
            if plan.uid not in kept and plan.start > now:
                self._close(plan, PlanStatus.CANCELLED, message(self.hass, "reason_owner"))
                self.log(message(self.hass, "plan_cancelled", summary=plan.display_summary))
                changed = True
        if changed:
            await self._async_changed()

    async def async_cancel(self, owner: str, *, force: bool = False) -> None:
        """Cancel the owner's planned plans; with `force` also its frozen and running ones."""
        cancelled: list[Plan] = []
        for plan in self.find(owner=owner, statuses=[PlanStatus.PLANNED]):
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
            return
        if not cancelled:
            return
        for plan in cancelled:
            self.log(message(self.hass, "plan_cancelled", summary=plan.display_summary))
        await self._async_changed()

    async def async_cancel_by_person(self, uid: str) -> Plan | None:
        """Cancel a plan whatever its state (deleted in the calendar)."""
        plan = self.get(uid)
        if plan is None or not plan.is_open:
            return None
        self._close(plan, PlanStatus.CANCELLED, message(self.hass, "reason_manual"))
        plan.by_person = True
        self.log(message(self.hass, "plan_cancelled", summary=plan.display_summary))
        await self._async_changed()
        return plan

    async def async_restore_by_person(self, uid: str, delta: timedelta) -> Plan | None:
        """Bring back a plan a person cancelled (saved in the calendar), moved by `delta`.

        Only a plan that never started, and only to a time to come; it is frozen:
        a person decided.
        """
        plan = self.get(uid)
        if (
            plan is None
            or plan.status != PlanStatus.CANCELLED
            or not plan.by_person
            or any(step.attempts for step in plan.steps)
            or plan.start + delta <= dt_util.utcnow()
        ):
            return None
        if delta and plan.origin is None:
            plan.origin = [step.copy() for step in plan.steps]
        for step in plan.steps:
            step.at += delta
            step.status = StepStatus.PLANNED
        plan.status = PlanStatus.PLANNED
        plan.reason = None
        plan.finished_at = None
        plan.updated_at = dt_util.utcnow()
        self.log(
            message(
                self.hass, "plan_restored", summary=plan.display_summary, start=local_text(plan.start)
            )
        )
        self.freeze(plan)
        await self._async_changed()
        return plan

    async def async_forget_by_person(self, uid: str) -> Plan | None:
        """Forget that a person cancelled a plan (its event deleted again): its owner decides again."""
        plan = self.get(uid)
        if plan is None or plan.status != PlanStatus.CANCELLED or not plan.by_person:
            return None
        del self._plans[uid]
        self.log(message(self.hass, "plan_forgotten", summary=plan.display_summary))
        await self._async_changed()
        return plan

    async def async_move_by_person(self, uid: str, delta: timedelta) -> Plan | None:
        """Move all steps of a plan that has not started (moved in the calendar).

        The plan is frozen: a person decided, automations do not move it back.
        """
        plan = self.get(uid)
        if plan is None or plan.status not in (PlanStatus.PLANNED, PlanStatus.FROZEN):
            return None
        if plan.origin is None:
            plan.origin = [step.copy() for step in plan.steps]
        for step in plan.steps:
            step.at += delta
        plan.by_person = True
        plan.updated_at = dt_util.utcnow()
        self.log(
            message(self.hass, "plan_moved", summary=plan.display_summary, start=local_text(plan.start))
        )
        if plan.status == PlanStatus.PLANNED:
            self.freeze(plan)
        await self._async_changed()
        return plan

    async def async_freeze(self, tags: list[str], within: timedelta) -> list[Plan]:
        """Freeze the planned plans with any of the tags (none: all) that start within a time."""
        limit = dt_util.utcnow() + within
        plans = [
            plan
            for plan in self.find(statuses=[PlanStatus.PLANNED])
            if plan.start <= limit and _has_tag(plan, tags)
        ]
        for plan in plans:
            self.freeze(plan)
        if plans:
            await self._async_changed()
        return plans

    def freeze(self, plan: Plan) -> None:
        """Freeze a plan: from now on only a person may change or cancel it."""
        plan.status = PlanStatus.FROZEN
        plan.frozen_at = dt_util.utcnow()
        self.log(
            message(self.hass, "plan_frozen", summary=plan.display_summary, start=local_text(plan.start))
        )

    async def async_unfreeze(self, tags: list[str]) -> list[Plan]:
        """Unfreeze the frozen plans with any of the tags (none: all) that have not started."""
        plans = [plan for plan in self.find(statuses=[PlanStatus.FROZEN]) if _has_tag(plan, tags)]
        for plan in plans:
            plan.status = PlanStatus.PLANNED
            plan.frozen_at = None
            plan.conflicts = []
            self.log(message(self.hass, "plan_unfrozen", summary=plan.display_summary))
        if plans:
            await self._async_changed()
        return plans

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
            frozen=local_text(plan.frozen_at or plan.start),
            owner=plan.owner,
            now=local_text(dt_util.utcnow()),
        )
        if any(line.startswith(f"{kind}:") for line in plan.conflicts):
            return
        plan.conflicts.append(f"{kind}: {text}")
        self.log(text)

    async def async_cleanup(self, retention: timedelta) -> int:
        """Remove finished plans older than the retention period.

        A plan a person cancelled is kept until its own time is that old: until
        then its owner must not make it again.
        """
        limit = dt_util.utcnow() - retention
        old = [
            uid
            for uid, plan in self._plans.items()
            if not plan.is_open
            and (plan.finished_at or plan.updated_at) < limit
            and not (plan.by_person and plan.status == PlanStatus.CANCELLED and plan.end >= limit)
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
