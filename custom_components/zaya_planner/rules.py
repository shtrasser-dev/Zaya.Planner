"""Rules: run planner scripts on a schedule.

A rule is a config subentry. At its next run time the rule calls its planner
script with the rule's params and `rule_id`; the planner works out the tasks
and creates them with zaya_planner.upsert (passing the same rule_id). Then
the next run moves on by the rule's repeat. Runs missed while Home Assistant
was off are caught up once.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .calls import async_call_script, async_notify
from .const import (
    CONF_ACTION_PREFIX,
    CONF_ENABLED,
    CONF_PARAMS,
    CONF_PLANNER,
    CONF_REPEAT,
    CONF_RULE_ID,
    CONF_TIME,
    DEFAULT_ACTION_PREFIX,
    DOMAIN,
    EVENT_RULE_RUN,
    SUBENTRY_RULE,
    Repeat,
)
from .messages import message
from .models import Rule, RuleState
from .store import TaskManager

_LOGGER = logging.getLogger(__name__)


def rules_from_entry(entry: ConfigEntry) -> list[Rule]:
    """Return the rules set up in the integration, ordered by title."""
    rules = [
        Rule(
            rule_id=sub.data[CONF_RULE_ID],
            title=sub.title,
            planner=sub.data[CONF_PLANNER],
            params=dict(sub.data.get(CONF_PARAMS) or {}),
            repeat=Repeat(sub.data[CONF_REPEAT]),
            time=sub.data[CONF_TIME],
            enabled=sub.data.get(CONF_ENABLED, True),
        )
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_RULE
    ]
    return sorted(rules, key=lambda rule: rule.title.lower())


def next_run_after(rule: Rule, after: datetime) -> datetime:
    """Return the first run time of the rule later than `after` (in UTC)."""
    local = dt_util.as_local(after)
    hour, minute = (int(part) for part in rule.time.split(":")[:2])

    if rule.repeat == Repeat.HOURLY:
        candidate = local.replace(minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(hours=1)
        return dt_util.as_utc(candidate)

    for days in range(8):
        day = (local + timedelta(days=days)).date()
        if rule.repeat == Repeat.WEEKDAYS and day.weekday() >= 5:
            continue
        if rule.repeat == Repeat.WEEKENDS and day.weekday() < 5:
            continue
        candidate = datetime.combine(day, time(hour, minute), tzinfo=local.tzinfo)
        if candidate > local:
            return dt_util.as_utc(candidate)
    raise ValueError(f"No run time for rule {rule.rule_id}")  # pragma: no cover


class RuleScheduler:
    """Run rules when their time comes."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, manager: TaskManager
    ) -> None:
        """Initialize the scheduler."""
        self.hass = hass
        self.entry = entry
        self.manager = manager
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._running: set[str] = set()
        self._started = False

    @callback
    def async_start(self, _hass: HomeAssistant | None = None) -> None:
        """Start once Home Assistant has started (scripts are loaded by then)."""
        self._started = True
        self.entry.async_on_unload(self._async_cancel_timer)
        self.async_sync()

    @callback
    def async_sync(self) -> None:
        """Follow the rules as they are set up now (added, changed, removed)."""
        if not self._started:
            return
        rules = {rule.rule_id: rule for rule in rules_from_entry(self.entry)}
        states = self.manager.rule_states
        now = dt_util.utcnow()
        changed = False

        for rule_id in list(states):
            if rule_id not in rules:
                del states[rule_id]
                changed = True
        for rule in rules.values():
            if rule.rule_id not in states:
                states[rule.rule_id] = RuleState()
                changed = True
            state = states[rule.rule_id]
            if not rule.enabled:
                if state.next_run is not None or state.schedule is not None:
                    state.next_run = None
                    state.schedule = None
                    changed = True
            elif state.schedule != rule.schedule or state.next_run is None:
                # New, re-enabled or rescheduled rule: plan from now on.
                # An unchanged rule keeps its next_run, so a missed run is caught up.
                state.next_run = next_run_after(rule, now)
                state.schedule = rule.schedule
                changed = True

        if changed:
            self.entry.async_create_task(self.hass, self.manager.async_save())
        self._async_schedule()

    @callback
    def _async_cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _async_schedule(self) -> None:
        """Start rules that are due and wake up for the next one."""
        self._async_cancel_timer()
        now = dt_util.utcnow()
        due: list[Rule] = []
        wake: datetime | None = None
        for rule in rules_from_entry(self.entry):
            state = self.manager.rule_states.get(rule.rule_id)
            if not rule.enabled or state is None or state.next_run is None:
                continue
            if rule.rule_id in self._running:
                continue
            if state.next_run <= now:
                due.append(rule)
            elif wake is None or state.next_run < wake:
                wake = state.next_run
        if wake is not None:
            self._unsub_timer = async_track_point_in_utc_time(
                self.hass, self._async_on_timer, wake
            )
        for rule in due:
            self._running.add(rule.rule_id)
            self.entry.async_create_task(
                self.hass,
                self._async_run(rule, scheduled=True),
                f"{DOMAIN} rule {rule.rule_id}",
                eager_start=False,
            )

    @callback
    def _async_on_timer(self, _now: datetime) -> None:
        self._unsub_timer = None
        self._async_schedule()

    async def async_run_now(self, rule_id: str) -> tuple[bool, str] | None:
        """Run a rule right away (its schedule stays as it is)."""
        rule = next(
            (rule for rule in rules_from_entry(self.entry) if rule.rule_id == rule_id),
            None,
        )
        if rule is None:
            return None
        if rule_id in self._running:
            return False, message(self.hass, "rule_busy")
        self._running.add(rule_id)
        return await self._async_run(rule, scheduled=False)

    async def _async_run(self, rule: Rule, *, scheduled: bool) -> tuple[bool, str]:
        """Call the rule's planner and remember the outcome."""
        try:
            state = self.manager.rule_states.setdefault(rule.rule_id, RuleState())
            now = dt_util.utcnow()
            if scheduled:
                # Missed runs are caught up once: the next one is after now
                state.next_run = next_run_after(rule, now)
            state.last_run = now
            await self.manager.async_save()

            prefix = self.entry.options.get(CONF_ACTION_PREFIX, DEFAULT_ACTION_PREFIX)
            if not rule.planner.startswith(prefix):
                ok = False
                reason = message(
                    self.hass, "reason_not_allowed", action=rule.planner, prefix=prefix
                )
            else:
                ok, reason, _ = await async_call_script(
                    self.hass, rule.planner, {**rule.params, "rule_id": rule.rule_id}
                )

            state.last_result = "ok" if ok else "error"
            state.last_error = None if ok else reason
            await self.manager.async_save()

            if ok:
                self.manager.log(message(self.hass, "rule_done", title=rule.title))
            else:
                self.manager.log(
                    message(self.hass, "rule_error", title=rule.title, reason=reason)
                )
                await async_notify(
                    self.hass,
                    self.entry,
                    message(self.hass, "notify_title_rule"),
                    message(self.hass, "notify_closed", title=rule.title, reason=reason),
                )
            self.hass.bus.async_fire(
                EVENT_RULE_RUN,
                {
                    "rule_id": rule.rule_id,
                    "title": rule.title,
                    "ok": ok,
                    "reason": None if ok else reason,
                    "next_run": state.next_run.isoformat() if state.next_run else None,
                },
            )
            return ok, ("" if ok else reason)
        finally:
            self._running.discard(rule.rule_id)
            self._async_schedule()
