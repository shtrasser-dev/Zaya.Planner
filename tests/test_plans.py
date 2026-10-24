"""Tests of making plans: owners, next plans, tags, freezing, conflicts."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.zaya_planner.config_flow import DEFAULT_OPTIONS
from custom_components.zaya_planner.const import CONF_TAGS, DOMAIN
from homeassistant.const import EVENT_LOGBOOK_ENTRY
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .common import (
    OWNER,
    build_plan,
    call,
    cancel_plan,
    get_plans,
    next_plan,
    set_plan,
    step,
)

# NOW (2026-10-21 12:00 UTC) is 15:00 in Moscow
LIGHTS = [step("2026-10-21T13:00:00+00:00"), step("2026-10-21T13:30:00+00:00", state="off")]
# Charging tonight: 04:50–06:20 in Moscow
NIGHT = [
    step("2026-10-22T01:50:00+00:00", "switch.charger"),
    step("2026-10-22T03:20:00+00:00", "switch.charger", "off"),
]


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """The dictionary has the tag “night”."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zaya Planner",
        data={},
        options={**DEFAULT_OPTIONS, CONF_TAGS: ["night"]},
        version=1,
        minor_version=4,
    )


@pytest.fixture(autouse=True)
async def moscow(hass: HomeAssistant) -> None:
    """Moscow time: UTC+3 all year."""
    await hass.config.async_set_time_zone("Europe/Moscow")


def messages(logbook, text: str) -> list[str]:
    return [e.data["message"] for e in logbook if text in e.data["message"]]


async def test_set_plan(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Made, the same again (nothing written), changed, cancelled by planning nothing."""
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    plan = await set_plan(hass, LIGHTS, summary="Lights")
    assert (plan["owner"], plan["summary"], plan["status"]) == (OWNER, "Lights", "planned")
    assert [s["summary"] for s in plan["steps"]] == ["switch.light: on", "switch.light: off"]
    assert plan["start"] == "2026-10-21T16:00:00+03:00"
    assert len(messages(logbook, "made")) == 1

    await set_plan(hass, LIGHTS, summary="Lights")
    assert len(logbook) == 1
    assert (await set_plan(hass, LIGHTS, summary="Yard"))["summary"] == "Yard"
    assert len(messages(logbook, "changed")) == 1
    assert len(await get_plans(hass, owner=OWNER)) == 1

    # Nothing to plan any more: the next plan is cancelled
    assert await build_plan(hass, []) is None
    assert [p["status"] for p in await get_plans(hass)] == ["cancelled"]


async def test_steps_of_one_run(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Steps added in a run make its plan; steps of another run do not."""
    await call(hass, "add_step", {"owner": OWNER, **step("2026-10-21T12:30:00+00:00")}, Context())
    plan = await build_plan(hass, LIGHTS, summary="Lights")
    assert [s["at"] for s in plan["steps"]] == [
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
    """Steps out of order, not allowed, without a state."""
    with pytest.raises(ServiceValidationError) as err:
        await set_plan(hass, steps)
    assert err.value.translation_key == key


async def test_step_times(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Minutes earlier or later; a step without a time is left out."""
    plan = await build_plan(
        hass,
        [
            {**step("2026-10-21T14:00:00+00:00"), "offset": "-90"},
            {**step("2026-10-21T14:00:00+00:00", state="off"), "offset": -20},
            {**step("", state="off")},
        ],
    )
    assert [s["at"] for s in plan["steps"]] == [
        "2026-10-21T15:30:00+03:00",
        "2026-10-21T16:40:00+03:00",
    ]

    # A module called without a departure adds no steps: its plan is cancelled
    assert await build_plan(hass, [step("")]) is None


async def test_time_of_an_entity(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """The time a sensor shows (the next sunset), or a time-only helper; none: no step."""
    hass.states.async_set("sensor.sun_next_setting", "2026-10-21T14:51:00+00:00")
    hass.states.async_set("input_datetime.wake", "06:40:00")
    hass.states.async_set("sensor.unknown_time", "unknown")
    plan = await build_plan(
        hass,
        [
            {"at_entity": "sensor.sun_next_setting", "devices": ["switch.light"], "state": "on"},
            {"at_entity": "input_datetime.wake", "offset": 10, "devices": ["switch.light"], "state": "off"},
            {"at_entity": "sensor.unknown_time", "devices": ["switch.light"], "state": "off"},
        ],
    )
    assert [s["at"] for s in plan["steps"]] == [
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
    plan = await set_plan(hass, [step("2026-10-21T11:00:00+00:00"), LIGHTS[1]])
    assert plan["start"] == "2026-10-21T16:00:00+03:00"


async def test_freeze_and_conflicts(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now
) -> None:
    """Frozen by tag: automations cannot change it; the next plan is planned apart."""
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    await set_plan(hass, NIGHT, summary="Charging", tags=["#Night"])
    # Tomorrow afternoon: not within the 12 hours
    tomorrow_lights = [step("2026-10-22T13:00:00+00:00"), step("2026-10-22T13:30:00+00:00", state="off")]
    await set_plan(hass, tomorrow_lights, summary="Lights", owner="automation.other", tags=["night"])

    # At 23:00 the night plans are frozen: those with the tag, starting within 12 hours
    frozen_now.move_to("2026-10-21T20:00:00+00:00")
    await call(hass, "freeze_plans", {"tags": ["night"], "within_hours": 12})
    plans = {p["summary"]: p for p in await get_plans(hass)}
    assert (plans["Charging"]["status"], plans["Charging"]["tags"]) == ("frozen", ["night"])
    assert plans["Lights"]["status"] == "planned"

    # At night a mistake: the automation plans something else, then cancels
    other = [step("2026-10-22T01:40:00+00:00", "switch.charger"), NIGHT[1]]
    await set_plan(hass, other, summary="Charging")
    await set_plan(hass, other, summary="Charging")
    await cancel_plan(hass)
    plan = await next_plan(hass)
    assert (plan["status"], plan["start"]) == ("frozen", "2026-10-22T04:50:00+03:00")
    # Each kind of conflict is written once
    assert len(messages(logbook, "wanted to")) == 2
    assert len(plan["conflicts"]) == 2

    # Planning the frozen plan again is nothing to tell
    await set_plan(hass, NIGHT, summary="Charging", tags=["night"])
    assert len(messages(logbook, "wanted to")) == 2

    # Tomorrow night is planned apart from it
    tomorrow = [
        step("2026-10-23T01:50:00+00:00", "switch.charger"),
        step("2026-10-23T03:20:00+00:00", "switch.charger", "off"),
    ]
    await set_plan(hass, tomorrow, summary="Charging")
    assert [p["status"] for p in await get_plans(hass, owner=OWNER)] == ["frozen", "planned"]

    # Unfrozen: its owner may change it again
    await call(hass, "unfreeze_plans", {"tags": ["night"]})
    assert [p["status"] for p in await get_plans(hass, owner=OWNER)] == ["planned", "planned"]

    # A person cancels them all
    await cancel_plan(hass, force=True)
    assert await next_plan(hass) is None


async def test_unknown_tags(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Tags must be in the dictionary."""
    with pytest.raises(ServiceValidationError) as err:
        await set_plan(hass, LIGHTS, tags=["cats"])
    assert err.value.translation_key == "unknown_filter_words"


async def test_plan_window(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Turn on for a while: from sunset to sunrise, shown by steps; at night no window."""
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    hass.states.async_set("switch.yard", "off", {"friendly_name": "Yard lights"})
    hass.states.async_set("sensor.sun_next_setting", "2026-10-21T14:51:00+00:00")
    hass.states.async_set("sensor.sun_next_rising", "2026-10-22T04:43:00+00:00")
    window = {
        "owner": "automation.yard",
        "devices": ["switch.yard"],
        "start_entity": "sensor.sun_next_setting",
        "end_entity": "sensor.sun_next_rising",
        "end_offset": -10,
    }

    await call(hass, "plan_window", {**window, "tags": ["night"]})
    plan = await next_plan(hass, "automation.yard")
    assert (plan["summary"], plan["tags"]) == ("Yard lights", ["night"])
    assert [(s["at"], s["summary"]) for s in plan["steps"]] == [
        ("2026-10-21T17:51:00+03:00", "Yard lights: on"),
        ("2026-10-22T07:33:00+03:00", "Yard lights: off"),
    ]
    await call(hass, "plan_window", {**window, "tags": ["night"]})
    assert len(messages(logbook, "made")) == 1
    assert len(logbook) == 1

    # At night the next sunset is tomorrow and the next sunrise this morning: no window
    hass.states.async_set("sensor.sun_next_setting", "2026-10-22T14:49:00+00:00")
    await call(hass, "plan_window", window)
    assert len(logbook) == 1


async def test_owner_is_who_started_the_run(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Without an owner, the plan belongs to the automation that started the run."""
    context = Context()
    hass.bus.async_fire("automation_triggered", {"entity_id": "automation.charging"}, context=context)
    # A planning module called by it runs in the same context
    hass.bus.async_fire("script_started", {"entity_id": "script.plan_charging"}, context=context)
    # The run goes on at once, before the events are handled
    await call(hass, "add_step", LIGHTS[0], context)
    await call(hass, "set_plan", {"summary": "Lights"}, context)
    assert (await next_plan(hass, "automation.charging"))["summary"] == "Lights"

    # Called by hand, outside any run: whose plan would it be?
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "set_plan", {"steps": [LIGHTS[1]]})
    assert err.value.translation_key == "owner_unknown"
