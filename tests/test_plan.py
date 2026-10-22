"""Tests of the house plan: departures, wake-ups, cancellations, anchors, alarms."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.zaya_planner.config_flow import DEFAULT_OPTIONS
from custom_components.zaya_planner.const import CONF_PEOPLE, DOMAIN, SUBENTRY_CAR
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component

# NOW (2026-10-21 12:00 UTC) is Wednesday 15:00 in Moscow
DAD, MUM, MASHA = "calendar.zaya_dad", "calendar.zaya_mum", "calendar.zaya_masha"
PLAN = "calendar.zaya_planner_house_plan"


@pytest.fixture(autouse=True)
async def household(hass: HomeAssistant, tmp_path: Path) -> None:
    """Moscow time, a config folder of our own, three people."""
    await hass.config.async_set_time_zone("Europe/Moscow")
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(
        hass,
        "person",
        {
            "person": [
                {"id": "dad", "name": "Dad"},
                {"id": "mum", "name": "Mum"},
                {"id": "masha", "name": "Masha"},
            ]
        },
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Zaya Planner with Dad, Mum, Masha and the Leaf."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zaya Planner",
        data={},
        version=1,
        minor_version=2,
        options={
            **DEFAULT_OPTIONS,
            CONF_PEOPLE: ["person.dad", "person.mum", "person.masha"],
        },
        subentries_data=[
            ConfigSubentryData(
                data={"name": "Leaf", "charger": "switch.leaf_charger"},
                subentry_type=SUBENTRY_CAR,
                title="Leaf",
                unique_id=None,
            )
        ],
    )


@pytest.fixture
async def family(hass: HomeAssistant, setup_entry: MockConfigEntry) -> MockConfigEntry:
    """Dad drives the Leaf and usually drives Masha; Masha is not woken by trips."""
    await select(hass, "select.zaya_dad_car", "Leaf")
    await select(hass, "select.zaya_masha_usually_drives", "Dad")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.zaya_masha_wake_for_trips"}, blocking=True
    )
    await hass.async_block_till_done()
    return setup_entry


async def select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    """Choose a select option."""
    await hass.services.async_call(
        "select", "select_option", {"entity_id": entity_id, "option": option}, blocking=True
    )


async def add(hass: HomeAssistant, calendar: str, **data: Any) -> dict[str, Any]:
    """Add an event to a person calendar."""
    response = await hass.services.async_call(
        DOMAIN, "add_event", data, target={"entity_id": calendar}, blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()
    return response[calendar]


async def plan_events(hass: HomeAssistant, start: str, end: str) -> list[tuple[str, str]]:
    """Return (start, summary) of the house plan in the period."""
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": PLAN, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    return [(e["start"], e["summary"]) for e in response[PLAN]["events"]]


def state(hass: HomeAssistant, entity_id: str) -> str:
    """Return the state of an entity."""
    return hass.states.get(entity_id).state


async def test_school_trip(hass: HomeAssistant, family: MockConfigEntry) -> None:
    """School at 8:00, 40 min away: Dad leaves at 7:20 and gets up at 6:20."""
    await add(
        hass, MASHA, summary="School", start="2026-10-26 08:00:00",
        end="2026-10-26 13:45:00", trip="drop", travel_minutes=40,
    )
    assert await plan_events(
        hass, "2026-10-26T00:00:00+03:00", "2026-10-27T00:00:00+03:00"
    ) == [
        ("2026-10-26T06:20:00+03:00", "⏰ Wake-up: Dad"),
        ("2026-10-26T07:20:00+03:00", "🚗 School (Masha): take there"),
    ]
    assert state(hass, "sensor.zaya_dad_next_wake_up") == "2026-10-26T03:20:00+00:00"
    assert state(hass, "sensor.zaya_dad_next_departure") == "2026-10-26T04:20:00+00:00"
    assert state(hass, "sensor.zaya_masha_next_departure") == "2026-10-26T04:20:00+00:00"
    assert state(hass, "sensor.zaya_masha_next_wake_up") == "unknown"
    assert state(hass, "sensor.zaya_leaf_first_departure") == "2026-10-26T04:20:00+00:00"
    assert state(hass, "sensor.zaya_planner_first_wake_up") == "2026-10-26T03:20:00+00:00"

    attributes = hass.states.get("sensor.zaya_dad_next_wake_up").attributes
    assert attributes["reason"] == "departure"
    assert attributes["summary"] == "School"
    assert attributes["car"] == "Leaf"


async def test_take_and_pick_up(hass: HomeAssistant, family: MockConfigEntry) -> None:
    """“Take there and pick up” makes two departures."""
    await add(
        hass, MASHA, summary="School", start="2026-10-26 08:00:00",
        end="2026-10-26 13:45:00", trip="both", travel_minutes=40,
    )
    events = await plan_events(
        hass, "2026-10-26T00:00:00+03:00", "2026-10-27T00:00:00+03:00"
    )
    assert [start for start, _ in events] == [
        "2026-10-26T06:20:00+03:00",
        "2026-10-26T07:20:00+03:00",
        "2026-10-26T13:05:00+03:00",
    ]
    assert events[2][1] == "🚗 School (Masha): pick up"


async def test_own_wake_up_and_the_earliest(
    hass: HomeAssistant, family: MockConfigEntry
) -> None:
    """A wake-up set in the calendar; on a trip day the earlier one counts."""
    await add(
        hass, MUM, summary="Wake up", start="2026-10-22 08:30:00",
        end="2026-10-22 08:35:00", kind="wake", rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    )
    # Thursday Mum takes Masha to the pool at 8:00, 30 min away
    await add(
        hass, MASHA, summary="Pool", start="2026-10-22 08:00:00",
        end="2026-10-22 09:00:00", trip="drop", travel_minutes=30,
        riders=["person.masha", "person.mum"], driver="person.mum",
    )
    events = await plan_events(
        hass, "2026-10-22T00:00:00+03:00", "2026-10-24T00:00:00+03:00"
    )
    assert ("2026-10-22T06:30:00+03:00", "⏰ Wake-up: Mum") in events
    assert ("2026-10-23T08:30:00+03:00", "⏰ Wake-up: Mum") in events
    assert ("2026-10-22T08:30:00+03:00", "⏰ Wake-up: Mum") not in events


async def test_cancellations(hass: HomeAssistant, family: MockConfigEntry) -> None:
    """Cancellations trim or drop events of the same person; wake-ups inside go."""
    await add(
        hass, MASHA, summary="School", start="2026-10-26 08:00:00",
        end="2026-10-26 13:45:00", rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        trip="both", travel_minutes=40,
    )
    # Monday: no first two lessons
    await add(
        hass, MASHA, summary="No first lessons", start="2026-10-26 08:00:00",
        end="2026-10-26 09:40:00", kind="cancel",
    )
    # Tuesday: frost, no school
    await add(
        hass, MASHA, summary="Frost", start="2026-10-27", end="2026-10-28", kind="cancel"
    )
    events = await plan_events(
        hass, "2026-10-26T00:00:00+03:00", "2026-10-28T00:00:00+03:00"
    )
    assert events == [
        ("2026-10-26T08:00:00+03:00", "⏰ Wake-up: Dad"),
        ("2026-10-26T09:00:00+03:00", "🚗 School (Masha): take there"),
        ("2026-10-26T13:05:00+03:00", "🚗 School (Masha): pick up"),
    ]


async def test_trip_without_travel_time_is_left_out(
    hass: HomeAssistant, family: MockConfigEntry
) -> None:
    """Without a travel time there is no departure to work out."""
    await add(
        hass, MASHA, summary="Club", start="2026-10-26 17:00:00",
        end="2026-10-26 18:00:00", trip="drop",
    )
    assert await plan_events(
        hass, "2026-10-26T00:00:00+03:00", "2026-10-27T00:00:00+03:00"
    ) == []


async def test_android_alarm(
    hass: HomeAssistant, family: MockConfigEntry, frozen_now
) -> None:
    """In the evening the alarm is set; if the wake-up moves, a warning is sent."""
    calls: list[dict[str, Any]] = []

    async def notify(call: ServiceCall) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_pixel", notify)
    await select(hass, "select.zaya_dad_alarm_phone", "mobile_app_pixel")
    added = await add(
        hass, MASHA, summary="School", start="2026-10-22 08:00:00",
        end="2026-10-22 13:45:00", trip="drop", travel_minutes=40,
    )

    # 21:00 Moscow
    frozen_now.move_to("2026-10-21T18:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0]["message"] == "command_activity"
    extras = calls[0]["data"]["intent_extras"]
    assert "alarm.HOUR:6" in extras and "alarm.MINUTES:20" in extras

    # The trip now takes an hour: wake-up 6:00, the alarm says 6:20
    await hass.services.async_call(
        DOMAIN, "set_event_details", {"uid": added["uid"], "travel_minutes": 60},
        target={"entity_id": MASHA}, blocking=True,
    )
    await hass.async_block_till_done()
    assert len(calls) == 2
    assert "06:00" in calls[1]["message"] and "06:20" in calls[1]["message"]


async def test_plan_changed_device_trigger(
    hass: HomeAssistant, family: MockConfigEntry
) -> None:
    """The Zaya Planner device has a trigger “house plan changed”."""
    from homeassistant.components.device_automation import DeviceAutomationType
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import (
        async_capture_events,
        async_get_device_automations,
    )

    assert await async_setup_component(hass, "device_automation", {})
    registry = dr.async_get(hass)
    main = registry.async_get_device(identifiers={(DOMAIN, family.entry_id)})
    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, main.id
    )
    assert [t["type"] for t in triggers] == ["plan_changed"]

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "triggers": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": main.id,
                    "type": "plan_changed",
                },
                "actions": {"event": "test_plan_changed"},
            }
        },
    )
    fired = async_capture_events(hass, "test_plan_changed")
    await add(
        hass, MASHA, summary="School", start="2026-10-26 08:00:00",
        end="2026-10-26 13:45:00", trip="drop", travel_minutes=40,
    )
    assert len(fired) == 1
