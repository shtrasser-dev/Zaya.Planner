"""Tests of zaya_planner.plan_window and zaya_planner.ensure_state."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.zaya_planner.const import DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.setup import async_setup_component

# NOW (2026-10-21 12:00 UTC) is Wednesday 15:00 in Moscow (UTC+3)
FLOOR = "window.floor"


@pytest.fixture(autouse=True)
async def moscow(hass: HomeAssistant) -> None:
    """Moscow time: UTC+3 all year."""
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


async def plan(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    """Call zaya_planner.plan_window."""
    data.setdefault("name", "Floor")
    data.setdefault("devices", ["switch.floor"])
    response = await hass.services.async_call(
        DOMAIN, "plan_window", data, blocking=True, return_response=True
    )
    await hass.async_block_till_done()
    return response


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
    """Move time and let the planner do what is due."""
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# ------------------------------------------------------------- windows


async def test_fixed_time_on_weekdays(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches
) -> None:
    """06:40 for 20 minutes on weekdays: Thursday is the next one."""
    hass.states.async_set("switch.floor", "off")
    response = await plan(
        hass, start_time="06:40", duration=20, days=["mon", "tue", "wed", "thu", "fri"]
    )
    assert response["start"] == "2026-10-22T06:40:00+03:00"
    assert await open_tasks(hass) == {
        f"{FLOOR}:on": ("2026-10-22T03:40:00+00:00", "2026-10-22T04:00:00+00:00"),
        f"{FLOOR}:off:2026-10-22": ("2026-10-22T04:00:00+00:00", "always"),
    }

    again = await plan(
        hass, start_time="06:40", duration=20, days=["mon", "tue", "wed", "thu", "fri"]
    )
    assert (again["on"], again["off"]) == ("unchanged", "unchanged")

    weekend = await plan(hass, start_time="06:40", duration=20, days=["sat"])
    assert weekend["start"] == "2026-10-24T06:40:00+03:00"


async def test_around_a_wake_up_that_moves_on(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, switches
) -> None:
    """−20/+20 around a wake-up; when it jumps to tomorrow, today still ends."""
    hass.states.async_set("switch.floor", "off")
    hass.states.async_set("sensor.dad_wake", "2026-10-22T03:50:00+00:00")
    window = {"start_entity": ["sensor.dad_wake"], "start_offset": -20, "duration": 40}
    await plan(hass, **window)
    assert await open_tasks(hass) == {
        f"{FLOOR}:on": ("2026-10-22T03:30:00+00:00", "2026-10-22T04:10:00+00:00"),
        f"{FLOOR}:off:2026-10-22": ("2026-10-22T04:10:00+00:00", "always"),
    }

    await move_to(hass, frozen_now, "2026-10-22T03:30:00+00:00")
    assert hass.states.get("switch.floor").state == "on"

    await move_to(hass, frozen_now, "2026-10-22T03:50:01+00:00")
    hass.states.async_set("sensor.dad_wake", "2026-10-23T03:50:00+00:00")
    await plan(hass, **window)
    assert await open_tasks(hass) == {
        f"{FLOOR}:on": ("2026-10-23T03:30:00+00:00", "2026-10-23T04:10:00+00:00"),
        f"{FLOOR}:off:2026-10-22": ("2026-10-22T04:10:00+00:00", "always"),
        f"{FLOOR}:off:2026-10-23": ("2026-10-23T04:10:00+00:00", "always"),
    }

    await move_to(hass, frozen_now, "2026-10-22T04:10:00+00:00")
    assert hass.states.get("switch.floor").state == "off"


async def test_earliest_of_several_anchors(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches
) -> None:
    """With several anchors the earliest counts."""
    hass.states.async_set("sensor.dad_wake", "2026-10-22T05:30:00+00:00")
    hass.states.async_set("sensor.mum_wake", "2026-10-22T03:50:00+00:00")
    response = await plan(
        hass,
        start_entity=["sensor.dad_wake", "sensor.mum_wake"],
        start_offset=-20,
        duration=40,
    )
    assert response["start"] == "2026-10-22T06:30:00+03:00"


async def test_night_light(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches
) -> None:
    """From sunset + 30 to sunrise − 30."""
    hass.states.async_set("sensor.sun_next_setting", "2026-10-21T14:30:00+00:00")
    hass.states.async_set("sensor.sun_next_rising", "2026-10-22T03:50:00+00:00")
    response = await plan(
        hass,
        name="Gate light",
        devices=["switch.gate"],
        start_entity=["sensor.sun_next_setting"],
        start_offset=30,
        end_entity=["sensor.sun_next_rising"],
        end_offset=-30,
    )
    assert (response["start"], response["end"]) == (
        "2026-10-21T18:00:00+03:00",
        "2026-10-22T06:20:00+03:00",
    )


async def test_charging_before_a_departure(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches
) -> None:
    """Charging ends 30 min before the departure; its length comes from a sensor."""
    hass.states.async_set("sensor.leaf_first_departure", "2026-10-22T04:20:00+00:00")
    hass.states.async_set("sensor.charge_minutes", "90")
    window = {
        "name": "Leaf",
        "devices": ["switch.charger"],
        "end_entity": ["sensor.leaf_first_departure"],
        "end_offset": -30,
        "start_before_end_entity": "sensor.charge_minutes",
        "start_before_end": 120,
    }
    response = await plan(hass, **window)
    assert (response["start"], response["end"]) == (
        "2026-10-22T05:20:00+03:00",
        "2026-10-22T06:50:00+03:00",
    )

    # No value from the sensor: the number is used
    hass.states.async_set("sensor.charge_minutes", "unknown")
    response = await plan(hass, **window)
    assert response["start"] == "2026-10-22T04:50:00+03:00"


async def test_inside_the_window(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches
) -> None:
    """Planned inside the window: on right away, unless the devices are on."""
    hass.states.async_set("switch.floor", "off")
    await plan(hass, start_time="14:50", duration=30)
    assert await open_tasks(hass) == {
        "window.floor:on": ("2026-10-21T12:00:30+00:00", "2026-10-21T12:20:00+00:00"),
        "window.floor:off:2026-10-21": ("2026-10-21T12:20:00+00:00", "always"),
    }

    hass.states.async_set("switch.lamp", "on")
    response = await plan(hass, name="Lamp", devices=["switch.lamp"], start_time="14:50", duration=30)
    assert response["on"] is None


async def test_no_window_cancels_the_on(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches
) -> None:
    """When the anchor has no time, the planned “on” goes; the “off” stays."""
    hass.states.async_set("sensor.dad_wake", "2026-10-22T03:50:00+00:00")
    window = {"start_entity": ["sensor.dad_wake"], "start_offset": -20, "duration": 40}
    await plan(hass, **window)
    hass.states.async_set("sensor.dad_wake", "unknown")
    response = await plan(hass, **window)
    assert response["start"] is None
    assert set(await open_tasks(hass)) == {f"{FLOOR}:off:2026-10-22"}


@pytest.mark.parametrize(
    "window",
    [
        {"duration": 20},
        {"start_time": "06:40"},
        {"start_time": "06:40", "start_entity": ["sensor.x"], "duration": 20},
        {"start_before_end": 30, "duration": 20},
    ],
)
async def test_wrong_window(
    hass: HomeAssistant, setup_entry: MockConfigEntry, window: dict
) -> None:
    """Each end needs exactly one source; not both counted from each other."""
    with pytest.raises(ServiceValidationError) as err:
        await plan(hass, **window)
    assert err.value.translation_key == "window_sources"


async def test_automation_plans_when_it_is_time(
    hass: HomeAssistant, setup_entry: MockConfigEntry, switches
) -> None:
    """An automation with “time to plan” plans its window, e.g. after saving."""
    main = next(
        device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), setup_entry.entry_id)
        if (DOMAIN, setup_entry.entry_id) in device.identifiers
    )
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "Floor heating",
                "triggers": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": main.id,
                    "type": "time_to_plan",
                },
                "actions": {
                    "action": "zaya_planner.plan_window",
                    "data": {
                        "name": "Floor",
                        "devices": ["switch.floor"],
                        "start_time": "06:40",
                        "duration": 20,
                    },
                },
            }
        },
    )
    hass.bus.async_fire("automation_reloaded")
    await hass.async_block_till_done()
    assert set(await open_tasks(hass)) == {f"{FLOOR}:on", f"{FLOOR}:off:2026-10-22"}


# --------------------------------------------------------- ensure_state


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
