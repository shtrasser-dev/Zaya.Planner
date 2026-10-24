"""Tests of making plans: owners, next plans, freezing, conflicts."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from homeassistant.const import EVENT_LOGBOOK_ENTRY
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .common import OWNER, build_plan, cancel_plan, get_plans, set_plan, step

# NOW (2026-10-21 12:00 UTC) is 15:00 in Moscow
LIGHTS = [step("2026-10-21T13:00:00+00:00"), step("2026-10-21T13:30:00+00:00", state="off")]
# Charging tonight: 04:50–06:20 in Moscow
NIGHT = [
    step("2026-10-22T01:50:00+00:00", "switch.charger"),
    step("2026-10-22T03:20:00+00:00", "switch.charger", "off"),
]


@pytest.fixture(autouse=True)
async def moscow(hass: HomeAssistant) -> None:
    """Moscow time: UTC+3 all year."""
    await hass.config.async_set_time_zone("Europe/Moscow")


async def move_to(hass: HomeAssistant, freezer, when: str) -> None:
    """Move time and let the planner do what is due."""
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def conflicts(logbook) -> list[str]:
    return [e.data["message"] for e in logbook if "wanted to" in e.data["message"]]


async def test_set_plan_results(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Created, the same again, changed, cancelled by planning nothing."""
    response = await set_plan(hass, LIGHTS, summary="Lights")
    assert response["result"] == "created"
    plan = response["plan"]
    assert (plan["owner"], plan["summary"], plan["status"]) == (OWNER, "Lights", "planned")
    assert [s["summary"] for s in plan["steps"]] == ["switch.light: on", "switch.light: off"]
    assert plan["start"] == "2026-10-21T16:00:00+03:00"

    assert (await set_plan(hass, LIGHTS, summary="Lights"))["result"] == "unchanged"
    assert (await set_plan(hass, LIGHTS, summary="Yard"))["result"] == "updated"
    assert len(await get_plans(hass, owner=OWNER)) == 1

    # Nothing to plan any more: the next plan is cancelled
    assert (await build_plan(hass, []))["result"] == "cancelled"
    assert [p["status"] for p in await get_plans(hass)] == ["cancelled"]
    assert (await cancel_plan(hass))["result"] == "unchanged"


async def test_steps_of_one_run(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Steps added in a run make its plan; steps of another run do not."""
    await hass.services.async_call(
        "zaya_planner",
        "add_step",
        {"owner": OWNER, **step("2026-10-21T12:30:00+00:00")},
        blocking=True,
        context=Context(),
    )
    response = await build_plan(hass, LIGHTS, summary="Lights")
    assert [s["at"] for s in response["plan"]["steps"]] == [
        "2026-10-21T16:00:00+03:00",
        "2026-10-21T16:30:00+03:00",
    ]


@pytest.mark.parametrize(
    ("steps", "key"),
    [
        ([LIGHTS[1], LIGHTS[0]], "steps_not_in_order"),
        ([{"at": "2026-10-21T13:00:00+00:00", "action": "script.other"}], "action_not_allowed"),
        ([{"at": "2026-10-21T13:00:00+00:00", "devices": ["switch.light"]}], "state_required"),
    ],
)
async def test_wrong_plans(
    hass: HomeAssistant, setup_entry: MockConfigEntry, steps: list, key: str
) -> None:
    """Steps out of order, in the past, not allowed, without a state."""
    with pytest.raises(ServiceValidationError) as err:
        await set_plan(hass, steps)
    assert err.value.translation_key == key


async def test_step_times(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Minutes earlier or later; a step without a time is left out."""
    response = await build_plan(
        hass,
        [
            {**step("2026-10-21T14:00:00+00:00"), "offset": "-90"},
            {**step("2026-10-21T14:00:00+00:00", state="off"), "offset": -20},
            {**step("", state="off")},
        ],
    )
    assert [s["at"] for s in response["plan"]["steps"]] == [
        "2026-10-21T15:30:00+03:00",
        "2026-10-21T16:40:00+03:00",
    ]

    # A module called without a departure adds no steps: its plan is cancelled
    assert (await build_plan(hass, [step("")]))["result"] == "cancelled"


async def test_time_of_an_entity(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """The time a sensor shows (the next sunset), or a time-only helper; none: no step."""
    hass.states.async_set("sensor.sun_next_setting", "2026-10-21T14:51:00+00:00")
    hass.states.async_set("input_datetime.wake", "06:40:00")
    hass.states.async_set("sensor.unknown_time", "unknown")
    response = await build_plan(
        hass,
        [
            {"at_entity": "sensor.sun_next_setting", "devices": ["switch.light"], "state": "on"},
            {"at_entity": "input_datetime.wake", "offset": 10, "devices": ["switch.light"], "state": "off"},
            {"at_entity": "sensor.unknown_time", "devices": ["switch.light"], "state": "off"},
        ],
    )
    assert [s["at"] for s in response["plan"]["steps"]] == [
        "2026-10-21T17:51:00+03:00",
        "2026-10-22T06:50:00+03:00",
    ]

    with pytest.raises(ServiceValidationError) as err:
        await build_plan(
            hass,
            [{**step("2026-10-21T14:00:00+00:00"), "at_entity": "sensor.sun_next_setting"}],
        )
    assert err.value.translation_key == "one_time_source"


async def test_past_plan_is_not_made(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Too late for a plan: nothing changes, no error."""
    await set_plan(hass, LIGHTS)
    response = await set_plan(hass, [step("2026-10-21T11:00:00+00:00"), LIGHTS[1]])
    assert response == {"result": "past", "plan": None}
    assert [p["start"] for p in await get_plans(hass)] == ["2026-10-21T16:00:00+03:00"]


async def test_freeze_and_conflicts(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now
) -> None:
    """Frozen at 23:00: automations cannot change it; the next plan is planned apart."""
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    response = await set_plan(hass, NIGHT, summary="Charging", freeze_at="23:00")
    assert response["plan"]["freeze"] == "2026-10-21T23:00:00+03:00"

    await move_to(hass, frozen_now, "2026-10-21T20:00:01+00:00")
    assert [p["status"] for p in await get_plans(hass)] == ["frozen"]

    # At night a mistake: the automation plans something else, then cancels
    other = [step("2026-10-22T01:40:00+00:00", "switch.charger"), NIGHT[1]]
    assert (await set_plan(hass, other, summary="Charging"))["result"] == "frozen"
    assert (await set_plan(hass, other, summary="Charging"))["result"] == "frozen"
    assert (await cancel_plan(hass))["result"] == "frozen"
    # Each kind of conflict is written once
    assert len(conflicts(logbook)) == 2
    plan = (await get_plans(hass))[0]
    assert plan["status"] == "frozen"
    assert len(plan["conflicts"]) == 2

    # Planning the frozen plan again is nothing to tell
    assert (await set_plan(hass, NIGHT, summary="Charging"))["result"] == "overlaps"
    assert len(conflicts(logbook)) == 2

    # Tomorrow night is planned apart from it
    tomorrow = [
        step("2026-10-23T01:50:00+00:00", "switch.charger"),
        step("2026-10-23T03:20:00+00:00", "switch.charger", "off"),
    ]
    assert (await set_plan(hass, tomorrow, summary="Charging"))["result"] == "created"
    assert [p["status"] for p in await get_plans(hass, owner=OWNER)] == ["frozen", "planned"]

    # A person cancels them all
    response = await cancel_plan(hass, force=True)
    assert (response["result"], len(response["plans"])) == ("cancelled", 2)


async def test_time_to_fix(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now
) -> None:
    """Made after its freeze time: it freezes 10 minutes later, not at once."""
    frozen_now.move_to("2026-10-21T20:30:00+00:00")
    response = await set_plan(hass, NIGHT, freeze_at="23:00")
    assert response["plan"]["freeze"] == "2026-10-21T23:40:00+03:00"

    # Minutes before the first step
    response = await set_plan(hass, NIGHT, freeze_before=120)
    assert response["plan"]["freeze"] == "2026-10-22T02:50:00+03:00"

    # Neither: when the first step is due
    response = await set_plan(hass, NIGHT)
    assert response["plan"]["freeze"] == "2026-10-22T04:50:00+03:00"


async def test_owner_is_who_started_the_run(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Without an owner, the plan belongs to the automation that started the run."""
    context = Context()
    hass.bus.async_fire("automation_triggered", {"entity_id": "automation.charging"}, context=context)
    # A planning module called by it runs in the same context
    hass.bus.async_fire("script_started", {"entity_id": "script.plan_charging"}, context=context)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "zaya_planner", "add_step", LIGHTS[0], blocking=True, context=context
    )
    response = await hass.services.async_call(
        "zaya_planner", "set_plan", {"summary": "Lights"}, blocking=True,
        return_response=True, context=context,
    )
    assert response["plan"]["owner"] == "automation.charging"

    # Called by hand, outside any run: whose plan would it be?
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "zaya_planner", "set_plan", {"steps": [LIGHTS[1]]}, blocking=True, return_response=True
        )
    assert err.value.translation_key == "owner_unknown"
