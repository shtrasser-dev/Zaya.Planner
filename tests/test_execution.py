"""Tests of running plans: steps in turn, failures, failure instructions, calendar, sensors."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.zaya_planner.const import (
    EVENT_PLAN_DONE,
    EVENT_PLAN_FAILED,
    EVENT_TIME_TO_PLAN,
)
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .common import get_plans, set_plan, step

CALENDAR = "calendar.zaya_planner_plans"
LIGHTS = [step("2026-10-21T13:00:00+00:00"), step("2026-10-21T13:30:00+00:00", state="off")]


@pytest.fixture(autouse=True)
async def moscow(hass: HomeAssistant) -> None:
    """Moscow time: UTC+3 all year."""
    await hass.config.async_set_time_zone("Europe/Moscow")


@pytest.fixture
def switches(hass: HomeAssistant) -> SimpleNamespace:
    """switch.turn_on/turn_off that change the state."""
    fake = SimpleNamespace(calls=[])

    async def handler(call: ServiceCall) -> None:
        for entity_id in cv.ensure_list(call.data["entity_id"]):
            fake.calls.append((call.service, entity_id))
            hass.states.async_set(entity_id, call.service.removeprefix("turn_"))

    hass.services.async_register("switch", "turn_on", handler)
    hass.services.async_register("switch", "turn_off", handler)
    hass.states.async_set("switch.light", "off")
    hass.states.async_set("switch.charger", "off")
    return fake


@pytest.fixture
def scripts(hass: HomeAssistant) -> SimpleNamespace:
    """script.step_relay that always fails, script.charging_failed that records its fields."""
    fake = SimpleNamespace(failures=[])

    async def relay(call: ServiceCall) -> dict[str, Any]:
        return {"ok": False, "reason": "no relay"}

    async def charging_failed(call: ServiceCall) -> None:
        fake.failures.append(dict(call.data))

    hass.services.async_register("script", "step_relay", relay, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("script", "charging_failed", charging_failed)
    return fake


async def move_to(hass: HomeAssistant, freezer, when: str) -> None:
    """Move time and let the planner do what is due."""
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_steps_run_in_turn(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, switches
) -> None:
    """On at 16:00, off at 16:30; then the plan is done and rules plan again."""
    done = async_capture_events(hass, EVENT_PLAN_DONE)
    time_to_plan = async_capture_events(hass, EVENT_TIME_TO_PLAN)
    await set_plan(hass, LIGHTS, summary="Lights")

    await move_to(hass, frozen_now, "2026-10-21T13:00:00+00:00")
    assert hass.states.get("switch.light").state == "on"
    plan = (await get_plans(hass))[0]
    assert plan["status"] == "running"
    assert [s["status"] for s in plan["steps"]] == ["done", "waiting"]

    await move_to(hass, frozen_now, "2026-10-21T13:30:00+00:00")
    assert hass.states.get("switch.light").state == "off"
    assert (await get_plans(hass))[0]["status"] == "done"
    assert len(done) == 1
    assert len(time_to_plan) >= 1


async def test_failed_step_runs_failure_instructions(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, switches, scripts
) -> None:
    """The first step keeps failing until the second is due: the plan fails."""
    failed = async_capture_events(hass, EVENT_PLAN_FAILED)
    notifications = async_mock_service(hass, "persistent_notification", "create")
    await set_plan(
        hass,
        [
            {"at": "2026-10-21T13:00:00+00:00", "action": "script.step_relay"},
            step("2026-10-21T13:05:00+00:00", "switch.charger", "off"),
        ],
        summary="Charging",
        on_failure="script.charging_failed",
    )
    for when in ("13:00", "13:01", "13:02", "13:04"):
        await move_to(hass, frozen_now, f"2026-10-21T{when}:00+00:00")
        assert (await get_plans(hass))[0]["status"] == "running"
    await move_to(hass, frozen_now, "2026-10-21T13:05:00+00:00")

    plan = (await get_plans(hass))[0]
    assert plan["status"] == "failed"
    assert [s["status"] for s in plan["steps"]] == ["failed", "cancelled"]
    assert plan["steps"][0]["attempts"] == 5
    # The “off” of the failed plan is not run: the instructions decide
    assert switches.calls == []
    assert len(failed) == 1
    assert len(scripts.failures) == 1
    fields = scripts.failures[0]
    assert fields["plan"]["summary"] == "Charging"
    assert fields["step"]["summary"] == "script.step_relay"
    assert fields["reason"] == "no relay"
    assert fields["done"] == []
    assert notifications == []


async def test_failed_plan_without_instructions_notifies(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, scripts
) -> None:
    """No failure instructions: the usual notification."""
    notifications = async_mock_service(hass, "persistent_notification", "create")
    await set_plan(hass, [{"at": "2026-10-21T13:00:00+00:00", "action": "script.step_relay"}])
    # The last step has the lateness limit (5 hours)
    await move_to(hass, frozen_now, "2026-10-21T13:00:00+00:00")
    await move_to(hass, frozen_now, "2026-10-21T18:00:00+00:00")
    assert (await get_plans(hass))[0]["status"] == "failed"
    assert [n.data["title"] for n in notifications][-1] == "Zaya Planner: plan failed"


async def test_first_step_too_late(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, switches
) -> None:
    """Home Assistant was off when the plan should have started: the plan fails."""
    await set_plan(hass, LIGHTS)
    await move_to(hass, frozen_now, "2026-10-21T18:00:01+00:00")
    plan = (await get_plans(hass))[0]
    assert plan["status"] == "failed"
    assert "too late" in plan["reason"]
    assert switches.calls == []


async def test_calendar_by_steps(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, switches, hass_ws_client
) -> None:
    """A plan shown by steps: a short event per step; deleting one cancels the plan."""
    response = await set_plan(hass, LIGHTS, summary="Lights", display="steps")
    uid = response["plan"]["uid"]
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": CALENDAR,
            "start_date_time": "2026-10-21T00:00:00+00:00",
            "end_date_time": "2026-10-22T00:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    assert [(e["start"], e["summary"]) for e in events[CALENDAR]["events"]] == [
        ("2026-10-21T16:00:00+03:00", "Lights: on"),
        ("2026-10-21T16:30:00+03:00", "Lights: off"),
    ]

    client = await hass_ws_client(hass)
    # Moving the “off” moves the whole plan
    await client.send_json_auto_id(
        {
            "type": "calendar/event/update",
            "entity_id": CALENDAR,
            "uid": f"{uid}:1",
            "event": {
                "summary": "Lights: off",
                "dtstart": "2026-10-21T16:45:00+03:00",
                "dtend": "2026-10-21T16:46:00+03:00",
            },
        }
    )
    assert (await client.receive_json())["success"]
    assert (await get_plans(hass))[0]["start"] == "2026-10-21T16:15:00+03:00"

    await client.send_json_auto_id(
        {"type": "calendar/event/delete", "entity_id": CALENDAR, "uid": f"{uid}:1"}
    )
    assert (await client.receive_json())["success"]
    assert (await get_plans(hass))[0]["status"] == "cancelled"


async def test_calendar_and_sensors(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, switches, hass_ws_client
) -> None:
    """Plans on the calendar; a person deletes one and moves another."""
    response = await set_plan(hass, LIGHTS, summary="Lights")
    uid = response["plan"]["uid"]

    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": CALENDAR,
            "start_date_time": "2026-10-21T00:00:00+00:00",
            "end_date_time": "2026-10-22T00:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    event = events[CALENDAR]["events"][0]
    assert (event["summary"], event["start"], event["end"]) == (
        "Lights",
        "2026-10-21T16:00:00+03:00",
        "2026-10-21T16:30:00+03:00",
    )
    assert "16:00 light: on — not run yet" in event["description"]
    assert hass.states.get("sensor.zaya_planner_next_step").state == "2026-10-21T13:00:00+00:00"
    assert hass.states.get("sensor.zaya_planner_plans").state == "1"

    client = await hass_ws_client(hass)
    # Moved by an hour: every step moves
    await client.send_json_auto_id(
        {
            "type": "calendar/event/update",
            "entity_id": CALENDAR,
            "uid": uid,
            "event": {
                "summary": "Lights",
                "dtstart": "2026-10-21T17:00:00+03:00",
                "dtend": "2026-10-21T17:30:00+03:00",
            },
        }
    )
    assert (await client.receive_json())["success"]
    plan = (await get_plans(hass))[0]
    assert [s["at"] for s in plan["steps"]] == [
        "2026-10-21T17:00:00+03:00",
        "2026-10-21T17:30:00+03:00",
    ]

    # Deleted: cancelled
    await client.send_json_auto_id(
        {"type": "calendar/event/delete", "entity_id": CALENDAR, "uid": uid}
    )
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert (await get_plans(hass))[0]["status"] == "cancelled"
    assert hass.states.get("sensor.zaya_planner_plans").state == "0"
