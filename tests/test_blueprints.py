"""Tests of ensure_state and of the blueprints, run as real automations."""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.zaya_planner.const import DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.setup import async_setup_component

BLUEPRINTS = Path(__file__).parent.parent / "blueprints"
RULE = "automation.floor_heating"


@pytest.fixture(autouse=True)
async def moscow(hass: HomeAssistant) -> None:
    """Moscow time: UTC+3 all year. NOW (12:00 UTC) is Wednesday 15:00."""
    await hass.config.async_set_time_zone("Europe/Moscow")


@pytest.fixture
def switches(hass: HomeAssistant) -> SimpleNamespace:
    """switch.turn_on/turn_off that change the state, unless told to ignore."""
    fake = SimpleNamespace(calls=[], ignore=set())

    async def handler(call: ServiceCall) -> None:
        for entity_id in cv.ensure_list(call.data["entity_id"]):
            fake.calls.append((call.service, entity_id))
            if entity_id not in fake.ignore:
                hass.states.async_set(entity_id, call.service.removeprefix("turn_"))

    hass.services.async_register("switch", "turn_on", handler)
    hass.services.async_register("switch", "turn_off", handler)
    return fake


@pytest.fixture
def blueprints(hass: HomeAssistant, tmp_path: Path) -> None:
    """Make the repository's blueprints available to automations."""
    hass.config.config_dir = str(tmp_path)
    shutil.copytree(BLUEPRINTS, tmp_path / "blueprints")


async def setup_rule(hass: HomeAssistant, blueprint: str, inputs: dict[str, Any]) -> None:
    """Create the automation “Floor heating” from a blueprint and run it once."""
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "id": "floor_heating",
                    "alias": "Floor heating",
                    "use_blueprint": {
                        "path": f"zaya_planner/{blueprint}",
                        "input": inputs,
                    },
                }
            ]
        },
    )
    await hass.async_block_till_done()
    assert hass.states.get(RULE) is not None, "the blueprint did not load"
    await run_rule(hass)


async def run_rule(hass: HomeAssistant) -> None:
    """Run the automation as if its trigger fired."""
    await hass.services.async_call(
        "automation", "trigger", {"entity_id": RULE}, blocking=True
    )
    await hass.async_block_till_done()


async def open_tasks(hass: HomeAssistant) -> dict[str, tuple[str, Any]]:
    """Return {id: (due, expires)} of the open tasks."""
    response = await hass.services.async_call(
        DOMAIN,
        "list",
        {"status": ["scheduled", "running", "error"]},
        blocking=True,
        return_response=True,
    )
    return {task["id"]: (task["due"], task["expires"]) for task in response["tasks"]}


async def move_to(hass: HomeAssistant, freezer, when: str) -> None:
    """Move time and let the planner and the automation do what is due."""
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# ---------------------------------------------------------------- ensure_state


async def ensure(hass: HomeAssistant, entity_id: Any, state: str) -> dict[str, Any]:
    """Call zaya_planner.ensure_state."""
    return await hass.services.async_call(
        DOMAIN,
        "ensure_state",
        {"entity_id": entity_id, "state": state},
        blocking=True,
        return_response=True,
    )


async def test_ensure_state_switches_and_confirms(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches
) -> None:
    """Devices are switched and confirmed; those already right are left alone."""
    hass.states.async_set("switch.a", "off")
    hass.states.async_set("switch.b", "on")

    assert await ensure(hass, ["switch.a", "switch.b"], "on") == {"ok": True, "reason": ""}
    assert switches.calls == [("turn_on", "switch.a")]
    assert hass.states.get("switch.a").state == "on"


async def test_ensure_state_reports_unavailable(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches
) -> None:
    """A device that is not connected is named in the reason."""
    hass.states.async_set("switch.a", "unavailable", {"friendly_name": "Gate light"})
    hass.states.async_set("switch.b", "off")

    response = await ensure(hass, ["switch.a", "switch.b"], "on")
    assert response["ok"] is False
    assert response["reason"] == "Gate light: not connected"
    assert hass.states.get("switch.b").state == "on"


# ------------------------------------------------------------- on for a while


async def test_on_for_a_while(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, switches, blueprints
) -> None:
    """On at the start, off after the duration, then the next day is planned."""
    hass.states.async_set("switch.floor", "off")
    await setup_rule(
        hass,
        "on_for_a_while.yaml",
        {"devices": ["switch.floor"], "start": "16:00:00", "minutes": 30},
    )
    assert await open_tasks(hass) == {
        f"{RULE}:on": ("2026-10-21T13:00:00+00:00", "2026-10-21T13:30:00+00:00"),
        f"{RULE}:off": ("2026-10-21T13:30:00+00:00", "always"),
    }

    await move_to(hass, frozen_now, "2026-10-21T13:00:00+00:00")
    assert hass.states.get("switch.floor").state == "on"
    # The “on” task is done; the device is on, so nothing new to turn on
    assert set(await open_tasks(hass)) == {f"{RULE}:off"}

    await move_to(hass, frozen_now, "2026-10-21T13:30:00+00:00")
    assert hass.states.get("switch.floor").state == "off"
    # The window is over: Thursday is planned
    assert await open_tasks(hass) == {
        f"{RULE}:on": ("2026-10-22T13:00:00+00:00", "2026-10-22T13:30:00+00:00"),
        f"{RULE}:off": ("2026-10-22T13:30:00+00:00", "always"),
    }


async def test_on_for_a_while_only_chosen_days(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches, blueprints
) -> None:
    """Only the chosen days are planned."""
    hass.states.async_set("switch.floor", "off")
    await setup_rule(
        hass,
        "on_for_a_while.yaml",
        {"devices": ["switch.floor"], "start": "06:40:00", "minutes": 20, "days": ["sat"]},
    )
    assert await open_tasks(hass) == {
        f"{RULE}:on": ("2026-10-24T03:40:00+00:00", "2026-10-24T04:00:00+00:00"),
        f"{RULE}:off": ("2026-10-24T04:00:00+00:00", "always"),
    }


async def test_on_for_a_while_inside_the_window(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches, blueprints
) -> None:
    """Planned inside the window (e.g. after a restart): turned on right away."""
    hass.states.async_set("switch.floor", "off")
    await setup_rule(
        hass,
        "on_for_a_while.yaml",
        {"devices": ["switch.floor"], "start": "14:50:00", "minutes": 30},
    )
    assert await open_tasks(hass) == {
        f"{RULE}:on": ("2026-10-21T12:00:30+00:00", "2026-10-21T12:20:00+00:00"),
        f"{RULE}:off": ("2026-10-21T12:20:00+00:00", "always"),
    }


async def test_on_for_a_while_inside_the_window_already_on(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches, blueprints
) -> None:
    """Inside the window with the device on: only the “off” is planned."""
    hass.states.async_set("switch.floor", "on")
    await setup_rule(
        hass,
        "on_for_a_while.yaml",
        {"devices": ["switch.floor"], "start": "14:50:00", "minutes": 30},
    )
    assert set(await open_tasks(hass)) == {f"{RULE}:off"}


# --------------------------------------------------------------- night light


async def test_night_light_by_day(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches, blueprints
) -> None:
    """By day: on after tonight's sunset, off before tomorrow's sunrise."""
    hass.states.async_set("switch.gate", "off")
    hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "next_setting": "2026-10-21T14:30:00+00:00",
            "next_rising": "2026-10-22T03:50:00+00:00",
        },
    )
    await setup_rule(hass, "night_light.yaml", {"devices": ["switch.gate"]})
    assert await open_tasks(hass) == {
        f"{RULE}:on": ("2026-10-21T15:00:00+00:00", "2026-10-22T03:20:00+00:00"),
        f"{RULE}:off": ("2026-10-22T03:20:00+00:00", "always"),
    }


async def test_night_light_at_night(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches, blueprints
) -> None:
    """At night with the lights off: on right away, off before sunrise."""
    hass.states.async_set("switch.gate", "off")
    hass.states.async_set(
        "sun.sun",
        "below_horizon",
        {
            "next_setting": "2026-10-22T10:00:00+00:00",
            "next_rising": "2026-10-21T13:00:00+00:00",
        },
    )
    await setup_rule(hass, "night_light.yaml", {"devices": ["switch.gate"]})
    assert await open_tasks(hass) == {
        f"{RULE}:on": ("2026-10-21T12:00:30+00:00", "2026-10-21T12:30:00+00:00"),
        f"{RULE}:off": ("2026-10-21T12:30:00+00:00", "always"),
    }


# -------------------------------------------------------- charge by departure


async def test_charge_by_departure(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches, blueprints
) -> None:
    """Charging ends before the departure; a removed departure cancels the “on”."""
    hass.states.async_set("switch.charger", "off")
    hass.states.async_set("sensor.departure", "2026-10-22T05:00:00+00:00")
    hass.states.async_set("sensor.charge_minutes", "90")
    await setup_rule(
        hass,
        "charge_by_departure.yaml",
        {
            "charger": "switch.charger",
            "departure": "sensor.departure",
            "minutes_sensor": "sensor.charge_minutes",
            "horizon": 24,
        },
    )
    # 05:00 − 90 min − 30 min margin; off 20 min after the departure
    assert await open_tasks(hass) == {
        f"{RULE}:on": ("2026-10-22T03:00:00+00:00", "2026-10-22T05:20:00+00:00"),
        f"{RULE}:off": ("2026-10-22T05:20:00+00:00", "always"),
    }

    # The departure changes: the tasks follow (the automation reacts by itself)
    hass.states.async_set("sensor.departure", "2026-10-22T06:00:00+00:00")
    await hass.async_block_till_done()
    assert (await open_tasks(hass))[f"{RULE}:on"][0] == "2026-10-22T04:00:00+00:00"

    # No departure: the “on” is cancelled, the “off” stays
    hass.states.async_set("sensor.departure", "unknown")
    await hass.async_block_till_done()
    assert set(await open_tasks(hass)) == {f"{RULE}:off"}


async def test_charge_waits_for_horizon(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches, blueprints
) -> None:
    """A departure further away than the horizon is not planned yet."""
    hass.states.async_set("switch.charger", "off")
    hass.states.async_set("sensor.departure", "2026-10-23T05:00:00+00:00")
    await setup_rule(
        hass,
        "charge_by_departure.yaml",
        {"charger": "switch.charger", "departure": "sensor.departure"},
    )
    assert await open_tasks(hass) == {}
