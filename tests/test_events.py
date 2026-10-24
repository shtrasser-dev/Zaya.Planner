"""Tests of the chosen calendars: errors in events and zaya_planner.find_events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, override

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    MockPlatform,
    async_fire_time_changed,
    async_capture_events,
    async_mock_service,
    mock_integration,
    mock_platform,
)

from custom_components.zaya_planner.config_flow import DEFAULT_OPTIONS
from custom_components.zaya_planner.const import (
    CONF_CALENDARS,
    CONF_KEYS,
    CONF_TAGS,
    DOMAIN,
    EVENT_TIME_TO_PLAN,
)
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .common import next_plan

# NOW (2026-10-21 12:00 UTC) is Wednesday 15:00 in Moscow
ERRORS = "sensor.zaya_planner_event_errors"


def event(summary: str, start: str, end: str, description: str = "") -> CalendarEvent:
    """An event in Moscow time."""
    return CalendarEvent(
        start=dt_util.parse_datetime(f"{start}+03:00"),
        end=dt_util.parse_datetime(f"{end}+03:00"),
        summary=summary,
        description=description,
    )


class FakeCalendar(CalendarEntity):
    """A calendar whose events the test sets."""

    _attr_should_poll = False

    def __init__(self, name: str, events: list[CalendarEvent]) -> None:
        """Start with some events."""
        self._attr_name = name
        self.entity_id = f"calendar.{name.lower()}"
        self.events = events

    @property
    @override
    def event(self) -> CalendarEvent | None:
        return None

    @override
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [e for e in self.events if e.start < end_date and e.end > start_date]

    def set(self, events: list[CalendarEvent]) -> None:
        """Change the events, as an edit in the calendar does."""
        self.events = events
        self.async_write_ha_state()


MASHA_EVENTS = [
    event("School", "2026-10-22T08:00:00", "2026-10-22T13:45:00", "#leaf #отвезти\nВ пути: 00:40:00"),
    # The same mistakes on two days are one error each
    event("Pool", "2026-10-22T17:00:00", "2026-10-22T18:00:00", "#лиф\nЕдут:"),
    event("Pool", "2026-10-23T17:00:00", "2026-10-23T18:00:00", "#лиф\nЕдут:"),
]
DAD_EVENTS = [
    event("Work", "2026-10-22T07:00:00", "2026-10-22T16:00:00", "#leaf"),
    # A wrong date: Friday instead of Thursday
    event("Trip", "2026-10-23T09:00:00", "2026-10-25T10:00:00"),
]


@pytest.fixture
async def calendars(hass: HomeAssistant) -> dict[str, FakeCalendar]:
    """Masha's and Dad's calendars, in Moscow time."""
    await hass.config.async_set_time_zone("Europe/Moscow")
    found = {
        "masha": FakeCalendar("Masha", list(MASHA_EVENTS)),
        "dad": FakeCalendar("Dad", list(DAD_EVENTS)),
    }

    async def setup_platform(hass, config, async_add_entities, discovery_info=None) -> None:
        async_add_entities(found.values())

    mock_integration(hass, MockModule("test"))
    mock_platform(hass, "test.calendar", MockPlatform(async_setup_platform=setup_platform))
    assert await async_setup_component(hass, "calendar", {"calendar": {"platform": "test"}})
    await hass.async_block_till_done()
    return found


@pytest.fixture
def config_entry(calendars: dict[str, FakeCalendar]) -> MockConfigEntry:
    """Zaya Planner reads Masha's and Dad's calendars and one that is gone."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zaya Planner",
        data={},
        version=1,
        minor_version=4,
        options={
            **DEFAULT_OPTIONS,
            CONF_CALENDARS: ["calendar.masha", "calendar.dad", "calendar.gone"],
            CONF_TAGS: ["leaf", "отвезти"],
            CONF_KEYS: ["В пути", "Едут"],
        },
    )


async def settle(hass: HomeAssistant, freezer) -> None:
    """Let calendar changes be read (they are read once they settle)."""
    for _ in range(2):
        freezer.tick(3)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def find(hass: HomeAssistant, **data: Any) -> list[dict[str, Any]]:
    """Call zaya_planner.find_events."""
    response = await hass.services.async_call(
        DOMAIN, "find_events", data, blocking=True, return_response=True
    )
    return response["events"]


async def test_errors_in_events(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    setup_entry: MockConfigEntry,
    frozen_now,
) -> None:
    """Mistakes are listed once; a new one is notified."""
    notifications = async_mock_service(hass, "persistent_notification", "create")
    time_to_plan = async_capture_events(hass, EVENT_TIME_TO_PLAN)
    await settle(hass, frozen_now)

    state = hass.states.get(ERRORS)
    assert state.attributes["errors"] == [
        "Calendar calendar.gone is not found",
        "Pool (Masha, 22.10): tag #лиф is not in the dictionary",
        "Pool (Masha, 22.10): “Едут” has no value",
        "Trip (Dad, 23.10): the event is longer than a day, check its dates",
    ]
    assert state.state == "4"
    # Known at the start: not notified
    assert notifications == []
    fired = len(time_to_plan)

    calendars["masha"].set(
        [
            *MASHA_EVENTS,
            event("Club", "2026-10-24T10:00:00", "2026-10-24T11:00:00", "#leaf #lief"),
        ]
    )
    await settle(hass, frozen_now)
    assert hass.states.get(ERRORS).state == "5"
    # The calendar changed: rules plan again
    assert len(time_to_plan) > fired
    assert len(notifications) == 1
    assert notifications[0].data["message"] == (
        "Club (Masha, 24.10): tag #lief is not in the dictionary"
    )

    # Fixed: the error goes, nothing is notified
    calendars["masha"].set(list(MASHA_EVENTS))
    await settle(hass, frozen_now)
    assert hass.states.get(ERRORS).state == "4"
    assert len(notifications) == 1


async def test_find_events(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    setup_entry: MockConfigEntry,
    frozen_now,
) -> None:
    """Events by tags and attributes, with what their descriptions say."""
    found = await find(hass, filter="#leaf\nВ пути: 00:40:00")
    assert found == [
        {
            "calendar": "calendar.masha",
            "summary": "School",
            "at": "2026-10-22T08:00:00+03:00",
            "start": "2026-10-22T08:00:00+03:00",
            "end": "2026-10-22T13:45:00+03:00",
            "all_day": False,
            "tags": ["leaf", "отвезти"],
            "attributes": {"В пути": "00:40:00"},
            "description": "#leaf #отвезти\nВ пути: 00:40:00",
        }
    ]

    found = await find(hass, filter="#leaf")
    assert [e["summary"] for e in found] == ["Work", "School"]

    # The time of an event: its start minus the travel time; without one, none
    found = await find(hass, filter="#leaf", subtract="в пути")
    assert [(e["summary"], e["at"]) for e in found] == [
        ("Work", None),
        ("School", "2026-10-22T07:20:00+03:00"),
    ]
    found = await find(hass, filter="#leaf", subtract="В пути", only_future=True)
    assert [e["summary"] for e in found] == ["School"]
    found = await find(hass, filter="#LEAF", first_of_day=True)
    assert [e["summary"] for e in found] == ["Work"]
    found = await find(hass, filter="#leaf", calendars=["calendar.masha"])
    assert [e["summary"] for e in found] == ["School"]
    found = await find(hass, start="2026-10-23 00:00:00")
    assert [e["summary"] for e in found] == ["Trip", "Pool"]


async def test_step_at_an_event(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    setup_entry: MockConfigEntry,
) -> None:
    """A step at the next fitting event: its start minus the travel time, minus 30 minutes."""
    data = {"owner": "automation.charging", "event": "#leaf #отвезти", "subtract": "В пути"}
    context = Context()
    await hass.services.async_call(
        DOMAIN,
        "add_step",
        {**data, "offset": -30, "devices": ["switch.charger"], "state": "off"},
        blocking=True,
        context=context,
    )
    # No such event: no step
    await hass.services.async_call(
        DOMAIN,
        "add_step",
        {**data, "event": "#отвезти\nЕдут", "devices": ["switch.charger"], "state": "on"},
        blocking=True,
        context=context,
    )
    await hass.services.async_call(
        DOMAIN, "set_plan", {"owner": "automation.charging"}, blocking=True, context=context
    )
    plan = await next_plan(hass, "automation.charging")
    # School at 8:00 (Moscow), 40 minutes away, 30 minutes to spare
    assert [s["at"] for s in plan["steps"]] == ["2026-10-22T06:50:00+03:00"]


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({"filter": "#leaf #лиф\nВремя"}, "unknown_filter_words"),
        ({"subtract": "Время"}, "unknown_filter_words"),
        ({"calendars": ["calendar.other"]}, "not_a_chosen_calendar"),
        ({"start": "2026-10-23 00:00:00", "end": "2026-10-22 00:00:00"}, "end_before_start"),
    ],
)
async def test_find_events_refuses(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    setup_entry: MockConfigEntry,
    data: dict[str, Any],
    key: str,
) -> None:
    """Words not in the dictionary and calendars not chosen are refused."""
    with pytest.raises(ServiceValidationError) as err:
        await find(hass, **data)
    assert err.value.translation_key == key
