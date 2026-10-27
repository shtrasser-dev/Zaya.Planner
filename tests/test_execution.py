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
    plan = await set_plan(hass, LIGHTS, summary="Lights", display="steps")
    uid = plan["uid"]
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
    plan = await set_plan(hass, LIGHTS, summary="Lights")
    uid = plan["uid"]

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


async def test_saving_scripts_is_time_to_plan(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now
) -> None:
    """A script saved in the editor (script.reload): rules plan again once it is loaded."""
    time_to_plan = async_capture_events(hass, EVENT_TIME_TO_PLAN)
    hass.services.async_register("script", "reload", lambda call: None)
    await hass.services.async_call("script", "reload", blocking=True)
    await hass.async_block_till_done()
    assert time_to_plan == []

    await move_to(hass, frozen_now, "2026-10-21T12:00:03+00:00")
    assert len(time_to_plan) == 1


async def test_what_a_person_decided_stays(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, hass_ws_client
) -> None:
    """Moved: frozen, not moved back; deleted: not made again; something else: made."""
    uid = (await set_plan(hass, LIGHTS, summary="Lights"))["uid"]
    client = await hass_ws_client(hass)
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
    # The automation plans the same as before: the plan stays where the person put it
    await set_plan(hass, LIGHTS, summary="Lights")
    plans = await get_plans(hass)
    assert [(p["status"], p["start"]) for p in plans] == [
        ("frozen", "2026-10-21T17:00:00+03:00")
    ]
    assert plans[0]["conflicts"] == []

    # Deleted: the same is not made again
    await client.send_json_auto_id(
        {"type": "calendar/event/delete", "entity_id": CALENDAR, "uid": uid}
    )
    assert (await client.receive_json())["success"]
    await set_plan(hass, LIGHTS, summary="Lights")
    assert [p["status"] for p in await get_plans(hass)] == ["cancelled"]

    # Something else is planned (the schedule changed): it is made
    later = [step("2026-10-21T15:00:00+00:00"), step("2026-10-21T15:30:00+00:00", state="off")]
    plan = await set_plan(hass, later, summary="Lights")
    assert (plan["status"], plan["start"]) == ("planned", "2026-10-21T18:00:00+03:00")


async def update_event(client, uid: str, start: str, end: str, **data: Any) -> dict[str, Any]:
    """Save an event of the “Plans” calendar as a person does; return the answer."""
    await client.send_json_auto_id(
        {
            "type": "calendar/event/update",
            "entity_id": CALENDAR,
            "uid": uid,
            "event": {"dtstart": start, "dtend": end, **data},
        }
    )
    return await client.receive_json()


async def delete_event(client, uid: str) -> dict[str, Any]:
    """Delete an event of the “Plans” calendar as a person does; return the answer."""
    await client.send_json_auto_id({"type": "calendar/event/delete", "entity_id": CALENDAR, "uid": uid})
    return await client.receive_json()


async def calendar_events(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Return the events of the “Plans” calendar for two days: (start, summary)."""
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": CALENDAR,
            "start_date_time": "2026-10-21T00:00:00+00:00",
            "end_date_time": "2026-10-23T00:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    return [(e["start"], e["summary"]) for e in events[CALENDAR]["events"]]


async def test_a_person_cancels_and_brings_back(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, hass_ws_client
) -> None:
    """Deleted: shown as 🚫 and not made again; saved: back and frozen; deleted again: forgotten."""
    uid = (await set_plan(hass, LIGHTS, summary="Lights"))["uid"]
    client = await hass_ws_client(hass)

    # Saved as it is: nothing changes
    assert (await update_event(client, uid, "2026-10-21T16:00:00+03:00", "2026-10-21T16:30:00+03:00", summary="Lights"))["success"]
    assert (await get_plans(hass))[0]["status"] == "planned"
    # The name and the description are the plan's
    answer = await update_event(
        client, uid, "2026-10-21T16:00:00+03:00", "2026-10-21T16:30:00+03:00", description="Status: done"
    )
    assert not answer["success"]
    assert (await get_plans(hass))[0]["status"] == "planned"

    # Deleted: cancelled, still on the calendar, not made again
    assert (await delete_event(client, uid))["success"]
    assert await calendar_events(hass) == [("2026-10-21T16:00:00+03:00", "🚫 Lights")]
    await set_plan(hass, LIGHTS, summary="Lights")
    assert [p["status"] for p in await get_plans(hass)] == ["cancelled"]

    # Saved: back, at the time saved, and frozen
    assert (await update_event(client, uid, "2026-10-21T17:00:00+03:00", "2026-10-21T17:30:00+03:00", summary="🚫 Lights"))["success"]
    plan = (await get_plans(hass))[0]
    assert (plan["status"], plan["start"]) == ("frozen", "2026-10-21T17:00:00+03:00")
    # Its owner plans the same as before: the person's plan stays
    await set_plan(hass, LIGHTS, summary="Lights")
    assert [(p["status"], p["start"]) for p in await get_plans(hass)] == [
        ("frozen", "2026-10-21T17:00:00+03:00")
    ]

    # Deleted, then deleted again: forgotten; its owner makes it again
    assert (await delete_event(client, uid))["success"]
    assert (await delete_event(client, uid))["success"]
    assert await get_plans(hass) == []
    assert await calendar_events(hass) == []
    plan = await set_plan(hass, LIGHTS, summary="Lights")
    assert (plan["status"], plan["start"]) == ("planned", "2026-10-21T16:00:00+03:00")

    # Too late to bring back
    assert (await delete_event(client, plan["uid"]))["success"]
    frozen_now.move_to("2026-10-21T13:10:00+00:00")
    answer = await update_event(
        client, plan["uid"], "2026-10-21T16:00:00+03:00", "2026-10-21T16:30:00+03:00"
    )
    assert not answer["success"]


async def test_a_cancelled_plan_is_kept_until_its_time(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, hass_ws_client
) -> None:
    """Cancelled by a person a week ahead: kept, so that its owner does not make it again."""
    later = [step("2026-10-26T13:00:00+00:00"), step("2026-10-26T13:30:00+00:00", state="off")]
    uid = (await set_plan(hass, later, summary="Lights"))["uid"]
    client = await hass_ws_client(hass)
    assert (await delete_event(client, uid))["success"]

    # Four days on: older than the retention of 3 days, but its time has not come
    await move_to(hass, frozen_now, "2026-10-25T12:00:00+00:00")
    assert [p["status"] for p in await get_plans(hass)] == ["cancelled"]
    # Three days after its time: removed
    await move_to(hass, frozen_now, "2026-10-29T14:00:00+00:00")
    assert await get_plans(hass) == []
