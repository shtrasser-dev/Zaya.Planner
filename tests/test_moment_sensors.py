"""Tests of moment sensors: from calendar events, and the earliest of moments."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.zaya_planner.config_flow import DEFAULT_OPTIONS
from custom_components.zaya_planner.const import (
    CONF_CALENDARS,
    CONF_KEYS,
    CONF_TAGS,
    DOMAIN,
    EVENT_TIME_TO_PLAN,
    SUBENTRY_MOMENT,
)
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant

from .test_events import FakeCalendar, calendars, event, settle  # noqa: F401

LEAF = "sensor.zaya_leaf_departure"
WAKE = "sensor.zaya_dad_wake_up"
ERRORS = "sensor.zaya_planner_event_errors"
SCHOOL = "#leaf #отвезти\nВ пути: 40:00"


@pytest.fixture
def config_entry(calendars: dict[str, FakeCalendar]) -> MockConfigEntry:  # noqa: F811
    """Zaya Planner with two moments."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zaya Planner",
        data={},
        version=1,
        minor_version=3,
        options={
            **DEFAULT_OPTIONS,
            CONF_CALENDARS: ["calendar.masha", "calendar.dad"],
            CONF_TAGS: ["leaf", "отвезти", "лиф"],
            CONF_KEYS: ["В пути", "Едут"],
        },
        subentries_data=[
            ConfigSubentryData(
                data={
                    "kind": "calendars",
                    "name": "Leaf departure",
                    "tags": ["leaf", "отвезти"],
                    "edge": "start",
                    "offset_key": "В пути",
                    "key_sign": "minus",
                    "offset": 0,
                    "which": "first_of_day",
                },
                subentry_type=SUBENTRY_MOMENT,
                title="Leaf departure",
                unique_id=None,
            ),
            ConfigSubentryData(
                data={
                    "kind": "moments",
                    "name": "Dad wake-up",
                    "sources": [LEAF, "sensor.dad_alarm"],
                    "offset": -60,
                },
                subentry_type=SUBENTRY_MOMENT,
                title="Dad wake-up",
                unique_id=None,
            ),
        ],
    )


async def test_moments(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],  # noqa: F811
    setup_entry: MockConfigEntry,
    frozen_now,
) -> None:
    """Moments follow the calendars; a change tells rules to plan again."""
    time_to_plan = async_capture_events(hass, EVENT_TIME_TO_PLAN)
    hass.states.async_set("sensor.dad_alarm", "2026-10-22T05:00:00+00:00")
    calendars["masha"].set(
        [
            event("School", "2026-10-22T08:00:00", "2026-10-22T13:45:00", SCHOOL),
            # A trip without a travel time: the moment cannot be worked out
            event("Club", "2026-10-23T10:00:00", "2026-10-23T11:00:00", "#leaf #отвезти"),
        ]
    )
    await settle(hass, frozen_now)

    # School at 8:00 (Moscow), 40 minutes away
    state = hass.states.get(LEAF)
    assert state.state == "2026-10-22T04:20:00+00:00"
    assert state.name == "Leaf departure"
    assert state.attributes["summary"] == "School"
    assert state.attributes["calendar"] == "calendar.masha"
    assert state.attributes["tags"] == ["leaf", "отвезти"]
    assert state.attributes["attributes"] == {"В пути": "40:00"}
    assert [m["summary"] for m in state.attributes["upcoming"]] == ["School"]
    assert "Club (Masha, 23.10): no “В пути” for the moment “Leaf departure”" in (
        hass.states.get(ERRORS).attributes["errors"]
    )

    # The earliest of the departure and Dad's alarm, an hour before
    state = hass.states.get(WAKE)
    assert state.state == "2026-10-22T03:20:00+00:00"
    assert state.attributes["source"] == LEAF
    assert len(time_to_plan) >= 1

    # School starts at 9:00 this time
    fired = len(time_to_plan)
    calendars["masha"].set(
        [event("School", "2026-10-22T09:00:00", "2026-10-22T13:45:00", SCHOOL)]
    )
    await settle(hass, frozen_now)
    assert hass.states.get(LEAF).state == "2026-10-22T05:20:00+00:00"
    # Dad's alarm (5:00 UTC) is earlier now
    assert hass.states.get(WAKE).state == "2026-10-22T04:00:00+00:00"
    assert hass.states.get(WAKE).attributes["source"] == "sensor.dad_alarm"
    assert len(time_to_plan) > fired

    # The moment passes: there is no next one
    frozen_now.move_to("2026-10-22T05:20:01+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(LEAF).state == "unknown"
