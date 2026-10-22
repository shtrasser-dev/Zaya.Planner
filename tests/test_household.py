"""Tests of people, cars, their settings and person calendars with trip fields."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaya_planner.config_flow import DEFAULT_OPTIONS
from custom_components.zaya_planner.const import (
    CONF_PEOPLE,
    DOMAIN,
    SETTINGS_STORAGE_KEY,
    SUBENTRY_CAR,
)
from homeassistant.config_entries import SOURCE_USER, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

MASHA = "calendar.zaya_masha"
DAD = "calendar.zaya_dad"


@pytest.fixture(autouse=True)
async def household(hass: HomeAssistant, tmp_path: Path) -> None:
    """Moscow time, a config folder of our own, and two people."""
    await hass.config.async_set_time_zone("Europe/Moscow")
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(
        hass,
        "person",
        {"person": [{"id": "dad", "name": "Dad"}, {"id": "masha", "name": "Masha"}]},
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Zaya Planner with Dad, Masha and the Leaf."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zaya Planner",
        data={},
        version=1,
        minor_version=2,
        options={**DEFAULT_OPTIONS, CONF_PEOPLE: ["person.dad", "person.masha"]},
        subentries_data=[
            ConfigSubentryData(
                data={"name": "Leaf", "charger": "switch.leaf_charger"},
                subentry_type=SUBENTRY_CAR,
                title="Leaf",
                unique_id=None,
            )
        ],
    )


async def call(
    hass: HomeAssistant, service: str, entity_id: str, **data: Any
) -> dict[str, Any]:
    """Call an action on a person calendar and return its response."""
    response = await hass.services.async_call(
        DOMAIN,
        service,
        data,
        target={"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    return response[entity_id]


async def select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    """Choose a select option."""
    await hass.services.async_call(
        "select", "select_option", {"entity_id": entity_id, "option": option}, blocking=True
    )


async def events(hass: HomeAssistant, entity_id: str, start: str, end: str) -> list[dict]:
    """Return events of a person calendar with their Zaya fields."""
    response = await call(
        hass, "get_events", entity_id, start_date_time=start, end_date_time=end
    )
    return response["events"]


async def dad_drives_masha(hass: HomeAssistant) -> None:
    """Dad drives the Leaf and usually drives Masha."""
    await select(hass, "select.zaya_dad_car", "Leaf")
    await select(hass, "select.zaya_masha_usually_drives", "Dad")


# ------------------------------------------------------------- devices


async def test_people_and_cars_are_devices(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Each person and car is a device with its settings."""
    devices = {
        device.name
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), setup_entry.entry_id
        )
    }
    assert {"Zaya: Dad", "Zaya: Masha", "Zaya: Leaf"} <= devices

    assert hass.states.get(MASHA) is not None
    assert hass.states.get(DAD) is not None
    assert hass.states.get("number.zaya_dad_getting_ready").state == "60"
    assert hass.states.get("switch.zaya_masha_wake_for_trips").state == "on"
    car = hass.states.get("select.zaya_dad_car")
    assert car.state == "none"
    assert car.attributes["options"] == ["none", "Leaf"]
    assert hass.states.get("select.zaya_masha_usually_drives").attributes["options"] == [
        "none",
        "Dad",
    ]
    assert hass.states.get("number.zaya_leaf_charge_margin").state == "30"


async def test_settings_are_saved(
    hass: HomeAssistant, setup_entry: MockConfigEntry, hass_storage
) -> None:
    """Changed settings are kept on disk right away."""
    await dad_drives_masha(hass)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.zaya_dad_getting_ready", "value": 45},
        blocking=True,
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.zaya_masha_wake_for_trips"}, blocking=True
    )

    assert hass.states.get("select.zaya_dad_car").state == "Leaf"
    assert hass.states.get("select.zaya_masha_usually_drives").state == "Dad"
    assert hass.states.get("number.zaya_dad_getting_ready").state == "45"
    assert hass.states.get("switch.zaya_masha_wake_for_trips").state == "off"

    people = hass_storage[SETTINGS_STORAGE_KEY]["data"]["people"]
    assert people["dad"]["getting_ready"] == 45
    assert people["masha"]["usually_drives"] == "person.dad"


async def test_add_car(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """A car is added with “Add car”; its device appears."""
    result = await hass.config_entries.subentries.async_init(
        (setup_entry.entry_id, SUBENTRY_CAR), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": " Chery "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert hass.states.get("number.zaya_chery_charge_margin") is not None
    assert hass.states.get("select.zaya_dad_car").attributes["options"] == [
        "none",
        "Chery",
        "Leaf",
    ]


async def test_person_removed_from_settings(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """A person no longer taking part loses their device."""
    hass.config_entries.async_update_entry(
        setup_entry, options={**setup_entry.options, CONF_PEOPLE: ["person.dad"]}
    )
    await hass.async_block_till_done()

    names = {
        device.name
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), setup_entry.entry_id
        )
    }
    assert "Zaya: Masha" not in names
    assert "Zaya: Dad" in names


# ------------------------------------------------------- person calendars


async def test_add_trip_with_defaults(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """A trip gets riders, driver and car from the settings."""
    await dad_drives_masha(hass)
    added = await call(
        hass,
        "add_event",
        MASHA,
        summary="School",
        start="2026-10-26 08:00:00",
        end="2026-10-26 13:45:00",
        trip="drop",
        travel_minutes=40,
    )
    assert added["riders"] == ["person.masha", "person.dad"]
    assert added["driver"] == "person.dad"
    assert added["travel_minutes"] == 40

    [event] = await events(
        hass, MASHA, "2026-10-26T00:00:00+03:00", "2026-10-27T00:00:00+03:00"
    )
    assert event["summary"] == "School"
    assert event["start"] == "2026-10-26T08:00:00+03:00"
    assert event["trip"] == "drop"
    assert event["car_name"] == "Leaf"
    assert event["uid"] == added["uid"]

    # The standard calendar shows it too
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": MASHA,
            "start_date_time": "2026-10-26T00:00:00+03:00",
            "end_date_time": "2026-10-27T00:00:00+03:00",
        },
        blocking=True,
        return_response=True,
    )
    assert [e["summary"] for e in response[MASHA]["events"]] == ["School"]


async def test_travel_time_from_last_trip(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Without a travel time, the last trip with the same title gives it."""
    await dad_drives_masha(hass)
    await call(
        hass, "add_event", MASHA, summary="Pool", start="2026-10-27 17:00:00",
        end="2026-10-27 18:00:00", trip="both", travel_minutes=25,
    )
    added = await call(
        hass, "add_event", MASHA, summary="Pool", start="2026-10-29 17:00:00",
        end="2026-10-29 18:00:00", trip="both",
    )
    assert added["travel_minutes"] == 25


async def test_one_occurrence_of_a_series(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """The trip of one occurrence changes; the rest of the series stays."""
    await dad_drives_masha(hass)
    added = await call(
        hass, "add_event", MASHA, summary="School", start="2026-10-26 08:00:00",
        end="2026-10-26 13:45:00", rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        trip="drop", travel_minutes=40,
    )
    week = await events(
        hass, MASHA, "2026-10-26T00:00:00+03:00", "2026-10-31T00:00:00+03:00"
    )
    assert len(week) == 5
    wednesday = week[2]

    await call(
        hass, "set_event_details", MASHA, uid=added["uid"],
        recurrence_id=wednesday["recurrence_id"], travel_minutes=55,
    )
    week = await events(
        hass, MASHA, "2026-10-26T00:00:00+03:00", "2026-10-31T00:00:00+03:00"
    )
    assert [e["travel_minutes"] for e in week] == [40, 40, 55, 40, 40]
    assert all(e["trip"] == "drop" and e["car_name"] == "Leaf" for e in week)


async def test_editing_in_calendar_keeps_trip(
    hass: HomeAssistant, setup_entry: MockConfigEntry, hass_ws_client
) -> None:
    """Changing an event in the standard Calendar panel keeps its trip."""
    await dad_drives_masha(hass)
    added = await call(
        hass, "add_event", MASHA, summary="School", start="2026-10-26 08:00:00",
        end="2026-10-26 13:45:00", trip="drop", travel_minutes=40,
    )
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "calendar/event/update",
            "entity_id": MASHA,
            "uid": added["uid"],
            "event": {
                "summary": "School (late)",
                "dtstart": "2026-10-26T09:00:00+03:00",
                "dtend": "2026-10-26T13:45:00+03:00",
            },
        }
    )
    assert (await client.receive_json())["success"]

    [event] = await events(
        hass, MASHA, "2026-10-26T00:00:00+03:00", "2026-10-27T00:00:00+03:00"
    )
    assert event["summary"] == "School (late)"
    assert event["start"] == "2026-10-26T09:00:00+03:00"
    assert (event["trip"], event["travel_minutes"], event["car_name"]) == ("drop", 40, "Leaf")


async def test_events_survive_restart(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Person calendars are files: after a reload everything is there."""
    await call(
        hass, "add_event", DAD, summary="Doctor", start="2026-10-27 11:00:00",
        end="2026-10-27 12:00:00", kind="event", trip="drop", travel_minutes=25,
        location="Clinic",
    )
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()

    [event] = await events(
        hass, DAD, "2026-10-27T00:00:00+03:00", "2026-10-28T00:00:00+03:00"
    )
    assert (event["summary"], event["location"], event["travel_minutes"]) == (
        "Doctor",
        "Clinic",
        25,
    )


async def test_wake_up_event(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """A wake-up is an event of its own kind."""
    await call(
        hass, "add_event", DAD, summary="Wake up", start="2026-10-26 06:30:00",
        end="2026-10-26 06:35:00", kind="wake", rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    )
    week = await events(
        hass, DAD, "2026-10-26T00:00:00+03:00", "2026-11-02T00:00:00+03:00"
    )
    assert [e["kind"] for e in week] == ["wake"] * 5
    assert week[0]["trip"] == "none"


@pytest.mark.parametrize(
    ("fields", "key"),
    [
        ({"trip": "drop", "car": "Tesla"}, "unknown_car"),
        ({"trip": "drop", "riders": ["person.masha"], "driver": "person.dad"}, "driver_not_rider"),
        ({"trip": "drop", "riders": ["person.ghost"]}, "unknown_person"),
    ],
)
async def test_wrong_trip_fields(
    hass: HomeAssistant, setup_entry: MockConfigEntry, fields: dict, key: str
) -> None:
    """Wrong people or cars are explained."""
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass, "add_event", MASHA, summary="School", start="2026-10-26 08:00:00",
            end="2026-10-26 13:45:00", **fields,
        )
    assert err.value.translation_key == key


async def test_not_on_the_schedule(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Person-calendar actions refuse the schedule calendar."""
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass, "get_events", "calendar.zaya_planner_schedule",
            start_date_time="2026-10-26T00:00:00+03:00",
            end_date_time="2026-10-27T00:00:00+03:00",
        )
    assert err.value.translation_key == "not_a_person_calendar"
