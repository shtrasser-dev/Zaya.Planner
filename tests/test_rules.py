"""Tests of rules: run times, runs, catch-up, setup in the UI."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.zaya_planner.config_flow import DEFAULT_OPTIONS
from custom_components.zaya_planner.const import (
    CONF_NOTIFY_ACTION,
    DOMAIN,
    EVENT_RULE_RUN,
    STORAGE_KEY,
    SUBENTRY_RULE,
    Repeat,
)
from custom_components.zaya_planner.models import Rule
from custom_components.zaya_planner.rules import next_run_after
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigSubentryData,
)
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .common import upsert

# NOW (12:00 UTC) is Wednesday 15:00 in Moscow
RULE = {
    "rule_id": "floor",
    "planner": "script.plan_rule_floor",
    "params": {"minutes": 20},
    "repeat": "daily",
    "time": "20:00:00",
    "enabled": True,
}


@pytest.fixture(autouse=True)
async def moscow(hass: HomeAssistant) -> None:
    """Moscow time: UTC+3 all year, easy to count."""
    await hass.config.async_set_time_zone("Europe/Moscow")


@pytest.fixture
def rule_data() -> dict[str, Any]:
    """Data of the rule set up in the entry."""
    return dict(RULE)


@pytest.fixture
def config_entry(rule_data: dict[str, Any]) -> MockConfigEntry:
    """An entry with one rule."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zaya Planner",
        data={},
        options={**DEFAULT_OPTIONS, CONF_NOTIFY_ACTION: "test.notify"},
        subentries_data=[
            ConfigSubentryData(
                data=rule_data,
                subentry_type=SUBENTRY_RULE,
                title="Floor heating",
                unique_id=rule_data["rule_id"],
            )
        ],
    )


@pytest.fixture
def notifications(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Collect notifications."""
    calls: list[dict[str, Any]] = []

    async def handler(call: ServiceCall) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("test", "notify", handler)
    return calls


@pytest.fixture
def planner(hass: HomeAssistant) -> SimpleNamespace:
    """A fake planner script: plans `tasks` and answers `answer`."""
    fake = SimpleNamespace(calls=[], tasks=[], answer={"ok": True})

    async def handler(call: ServiceCall) -> dict[str, Any]:
        fake.calls.append(dict(call.data))
        for task in fake.tasks:
            await hass.services.async_call(
                DOMAIN,
                "upsert",
                {**task, "action": "script.plan_switch", "rule_id": call.data["rule_id"]},
                blocking=True,
            )
        return fake.answer

    hass.services.async_register(
        "script", "plan_rule_floor", handler, supports_response=SupportsResponse.OPTIONAL
    )
    return fake


def state_of(entry: MockConfigEntry, rule_id: str = "floor"):
    """Return the rule's state."""
    return entry.runtime_data.manager.rule_states[rule_id]


def make_rule(repeat: Repeat, time: str) -> Rule:
    """Return a rule with this schedule."""
    return Rule("r", "R", "script.plan_rule_r", {}, repeat, time, True)


@pytest.mark.parametrize(
    ("repeat", "time", "after", "expected"),
    [
        # Wednesday 15:00 Moscow
        (Repeat.DAILY, "20:00:00", "2026-10-21T12:00:00+00:00", "2026-10-21T17:00:00+00:00"),
        (Repeat.DAILY, "06:00", "2026-10-21T12:00:00+00:00", "2026-10-22T03:00:00+00:00"),
        (Repeat.DAILY, "15:00", "2026-10-21T12:00:00+00:00", "2026-10-22T12:00:00+00:00"),
        # Friday 20:00 Moscow: next weekday is Monday
        (Repeat.WEEKDAYS, "06:00", "2026-10-23T17:00:00+00:00", "2026-10-26T03:00:00+00:00"),
        (Repeat.WEEKDAYS, "06:00", "2026-10-21T12:00:00+00:00", "2026-10-22T03:00:00+00:00"),
        (Repeat.WEEKENDS, "10:00", "2026-10-21T12:00:00+00:00", "2026-10-24T07:00:00+00:00"),
        # Saturday 12:00 Moscow: Sunday
        (Repeat.WEEKENDS, "10:00", "2026-10-24T09:00:00+00:00", "2026-10-25T07:00:00+00:00"),
        (Repeat.HOURLY, "00:30", "2026-10-21T12:00:00+00:00", "2026-10-21T12:30:00+00:00"),
        (Repeat.HOURLY, "07:00", "2026-10-21T12:00:00+00:00", "2026-10-21T13:00:00+00:00"),
    ],
)
async def test_next_run_after(
    hass: HomeAssistant, repeat: Repeat, time: str, after: str, expected: str
) -> None:
    """Run times follow the repeat, in local time."""
    result = next_run_after(make_rule(repeat, time), dt_util.parse_datetime(after))
    assert result == dt_util.parse_datetime(expected)


async def test_rule_runs_on_time(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, planner
) -> None:
    """At its time the rule calls the planner, which plans tasks."""
    runs = async_capture_events(hass, EVENT_RULE_RUN)
    planner.tasks = [
        {"id": "floor_on", "title": "Floor on", "due": "2026-10-22T03:40:00+00:00"},
        # In the past: not created
        {"id": "floor_old", "due": "2026-10-21T11:00:00+00:00"},
    ]
    assert state_of(setup_entry).next_run == dt_util.parse_datetime(
        "2026-10-21T17:00:00+00:00"
    )

    frozen_now.move_to("2026-10-21T16:59:59+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert planner.calls == []

    frozen_now.move_to("2026-10-21T17:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert planner.calls == [{"minutes": 20, "rule_id": "floor"}]
    state = state_of(setup_entry)
    assert state.last_result == "ok"
    assert state.last_run == dt_util.parse_datetime("2026-10-21T17:00:00+00:00")
    assert state.next_run == dt_util.parse_datetime("2026-10-22T17:00:00+00:00")
    assert [(event.data["rule_id"], event.data["ok"]) for event in runs] == [("floor", True)]

    tasks = setup_entry.runtime_data.manager.find(rule_id="floor")
    assert [task.id for task in tasks] == ["floor_on"]

    rules = hass.states.get("sensor.zaya_planner_rules")
    assert rules.state == "1"
    assert rules.attributes["rules"][0]["id"] == "floor"
    assert rules.attributes["rules"][0]["last_result"] == "ok"


async def test_missed_runs_caught_up_once(
    hass: HomeAssistant, config_entry: MockConfigEntry, frozen_now, hass_storage, planner
) -> None:
    """Runs missed while Home Assistant was off are made once."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "tasks": [],
            "rules": {
                "floor": {
                    "next_run": "2026-10-19T17:00:00+00:00",
                    "schedule": "daily 20:00:00",
                }
            },
        },
    }
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert len(planner.calls) == 1
    assert state_of(config_entry).next_run == dt_util.parse_datetime(
        "2026-10-21T17:00:00+00:00"
    )


async def test_planner_error_is_reported(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, planner, notifications
) -> None:
    """A planner that fails is logged and reported; the rule runs again next time."""
    planner.answer = {"ok": False, "reason": "no forecast"}
    runs = async_capture_events(hass, EVENT_RULE_RUN)

    frozen_now.move_to("2026-10-21T17:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = state_of(setup_entry)
    assert state.last_result == "error"
    assert state.last_error == "no forecast"
    assert state.next_run == dt_util.parse_datetime("2026-10-22T17:00:00+00:00")
    assert runs[0].data["ok"] is False
    assert len(notifications) == 1
    assert "no forecast" in notifications[0]["message"]


async def test_run_rule_action(
    hass: HomeAssistant, setup_entry: MockConfigEntry, planner
) -> None:
    """zaya_planner.run_rule runs a rule now and keeps its schedule."""
    next_run = state_of(setup_entry).next_run
    response = await hass.services.async_call(
        DOMAIN, "run_rule", {"rule_id": "floor"}, blocking=True, return_response=True
    )
    assert response == {"ok": True, "reason": ""}
    assert len(planner.calls) == 1
    assert state_of(setup_entry).next_run == next_run

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "run_rule", {"rule_id": "nope"}, blocking=True
        )


async def test_rule_upsert_in_past_is_skipped(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """A task planned by a rule for a moment that has passed is not created."""
    response = await upsert(
        hass, id="t", due="2026-10-21T11:00:00+00:00", rule_id="floor"
    )
    assert response == {"result": "past", "task": None}


@pytest.mark.parametrize("rule_data", [{**RULE, "enabled": False}])
async def test_disabled_rule_does_not_run(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, planner
) -> None:
    """A disabled rule has no next run."""
    assert state_of(setup_entry).next_run is None
    frozen_now.move_to("2026-10-21T17:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert planner.calls == []
    assert hass.states.get("sensor.zaya_planner_rules").state == "0"


async def test_calendar_shows_next_rule_run(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """The next run of a rule is an event on the calendar."""
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.zaya_planner_schedule",
            "start_date_time": "2026-10-21T00:00:00+00:00",
            "end_date_time": "2026-10-23T00:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    events = response["calendar.zaya_planner_schedule"]["events"]
    assert [event["summary"] for event in events] == ["🗓️ Floor heating"]
    assert datetime.fromisoformat(events[0]["start"]) == dt_util.parse_datetime(
        "2026-10-21T17:00:00+00:00"
    )


async def test_add_rule_in_ui(
    hass: HomeAssistant, setup_entry: MockConfigEntry, planner
) -> None:
    """A rule is added with a form; wrong values are explained."""
    flow = hass.config_entries.subentries
    result = await flow.async_init(
        (setup_entry.entry_id, SUBENTRY_RULE), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    form = {
        "rule_id": "Light Night",
        "title": "Light at night",
        "planner": "script.turn_on_everything",
        "params": ["not", "a", "mapping"],
        "repeat": "weekdays",
        "time": "18:00:00",
        "enabled": True,
    }
    result = await flow.async_configure(result["flow_id"], form)
    assert result["errors"] == {
        "rule_id": "invalid_rule_id",
        "planner": "planner_not_allowed",
        "params": "invalid_params",
    }

    result = await flow.async_configure(
        result["flow_id"], {**form, "rule_id": "floor", "planner": "script.plan_rule_light", "params": {}}
    )
    assert result["errors"] == {"rule_id": "rule_id_exists"}

    result = await flow.async_configure(
        result["flow_id"], {**form, "rule_id": "light", "planner": "script.plan_rule_light", "params": {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert state_of(setup_entry, "light").next_run == dt_util.parse_datetime(
        "2026-10-21T15:00:00+00:00"
    )
    assert hass.states.get("sensor.zaya_planner_rules").state == "2"


async def test_change_rule_in_ui(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Changing the time plans the next run anew; the id stays."""
    subentry_id = next(iter(setup_entry.subentries))
    flow = hass.config_entries.subentries
    result = await flow.async_init(
        (setup_entry.entry_id, SUBENTRY_RULE),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM

    result = await flow.async_configure(
        result["flow_id"],
        {
            "title": "Floor heating (early)",
            "planner": "script.plan_rule_floor",
            "params": {"minutes": 30},
            "repeat": "daily",
            "time": "19:00:00",
            "enabled": True,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    subentry = setup_entry.subentries[subentry_id]
    assert subentry.title == "Floor heating (early)"
    assert subentry.data["rule_id"] == "floor"
    assert subentry.data["params"] == {"minutes": 30}
    assert state_of(setup_entry).next_run == dt_util.parse_datetime(
        "2026-10-21T16:00:00+00:00"
    )


async def test_removed_rule_is_forgotten(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Removing a rule removes its state."""
    subentry_id = next(iter(setup_entry.subentries))
    hass.config_entries.async_remove_subentry(setup_entry, subentry_id)
    await hass.async_block_till_done()
    assert "floor" not in setup_entry.runtime_data.manager.rule_states
    assert hass.states.get("sensor.zaya_planner_rules").state == "0"
