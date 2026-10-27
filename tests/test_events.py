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
    EVENT_PLAN_DONE,
    EVENT_TIME_TO_PLAN,
)
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import EVENT_LOGBOOK_ENTRY
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .common import next_plan

# NOW (2026-10-21 12:00 UTC) is Wednesday 15:00 in Moscow
ERRORS = "sensor.zaya_planner_event_errors"


def event(
    summary: str, start: str, end: str, description: str = "", location: str = ""
) -> CalendarEvent:
    """An event in Moscow time."""
    return CalendarEvent(
        start=dt_util.parse_datetime(f"{start}+03:00"),
        end=dt_util.parse_datetime(f"{end}+03:00"),
        summary=summary,
        description=description,
        location=location or None,
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
            "location": "",
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
    found = await find(hass, filter="#leaf !#отвезти")
    assert [e["summary"] for e in found] == ["Work"]
    found = await find(hass, start="2026-10-23 00:00:00")
    assert [e["summary"] for e in found] == ["Trip", "Pool"]


def calendar_sensor(name: str = "Выезд Leaf", **data: Any) -> ConfigSubentryData:
    """A calendar sensor “Выезд Leaf”: #leaf, the start minus “В пути”, 30 minutes by default."""
    return ConfigSubentryData(
        subentry_type="departure",
        title=name,
        unique_id=None,
        data={
            "name": name,
            "tags": ["leaf"],
            "edge": "start",
            "offset_key": "В пути",
            "sign": "minus",
            "default_offset": {"hours": 0, "minutes": 30, "seconds": 0},
            "first_of_day": True,
            **data,
        },
    )


async def setup_sensors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer,
    *subentries: ConfigSubentryData,
    options: dict[str, Any] | None = None,
    minor_version: int = 5,
) -> MockConfigEntry:
    """Set up Zaya Planner with calendar sensors and let the calendars be read."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zaya Planner",
        data={},
        version=1,
        minor_version=minor_version,
        options={**config_entry.options, **(options or {})},
        subentries_data=list(subentries) or [calendar_sensor()],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await settle(hass, freezer)
    return entry


async def test_calendar_sensor(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    config_entry: MockConfigEntry,
    frozen_now,
) -> None:
    """The first #leaf event of a day, its start minus the travel time; then the next day."""
    time_to_plan = async_capture_events(hass, EVENT_TIME_TO_PLAN)
    await setup_sensors(hass, config_entry, frozen_now)
    # The times are known: rules plan again
    assert time_to_plan

    # Thursday: Dad's work at 7:00 comes first; it has no travel time: 30 minutes
    state = hass.states.get("sensor.vyezd_leaf")
    assert state.state == "2026-10-22T03:30:00+00:00"
    assert state.attributes["summary"] == "Work"
    assert state.attributes["offset"] == "00:30:00"
    assert state.attributes["default_offset"] is True
    # Without a horizon: no list
    assert "times" not in state.attributes
    assert (
        "Work (Dad, 22.10): no “В пути”, the default offset is used"
        in hass.states.get(ERRORS).attributes["errors"]
    )

    # Once it passes, rules plan again; no more #leaf events
    fired = len(time_to_plan)
    frozen_now.move_to("2026-10-22T03:31:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.vyezd_leaf").state == "unknown"
    assert len(time_to_plan) > fired


async def test_departure_of_an_older_version(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    config_entry: MockConfigEntry,
    frozen_now,
) -> None:
    """A departure of 0.12 becomes a calendar sensor: its travel time is subtracted."""
    old = ConfigSubentryData(
        subentry_type="departure",
        title="Выезд Leaf",
        unique_id=None,
        data={
            "name": "Выезд Leaf",
            "tags": ["leaf"],
            "edge": "start",
            "travel_key": "В пути",
            "first_of_day": True,
        },
    )
    entry = await setup_sensors(hass, config_entry, frozen_now, old, minor_version=4)
    assert entry.minor_version == 5
    (subentry,) = entry.subentries.values()
    assert subentry.data == {
        "name": "Выезд Leaf",
        "tags": ["leaf"],
        "edge": "start",
        "offset_key": "В пути",
        "default_offset": {"minutes": 30},
        "sign": "minus",
        "first_of_day": True,
    }
    assert hass.states.get("sensor.vyezd_leaf").state == "2026-10-22T03:30:00+00:00"


@pytest.mark.parametrize(
    ("edge", "sign", "expected"),
    [
        ("start", "minus", "2026-10-22T04:20:00+00:00"),
        ("end", "minus", "2026-10-22T10:05:00+00:00"),
        ("end", "plus", "2026-10-22T11:25:00+00:00"),
    ],
)
async def test_calendar_sensor_offset(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    config_entry: MockConfigEntry,
    frozen_now,
    edge: str,
    sign: str,
    expected: str,
) -> None:
    """School 8:00–13:45 (Moscow), 40 minutes away: 7:20, 13:05, or 14:25."""
    await setup_sensors(
        hass,
        config_entry,
        frozen_now,
        calendar_sensor(tags=["leaf", "отвезти"], edge=edge, sign=sign),
    )
    state = hass.states.get("sensor.vyezd_leaf")
    assert state.state == expected
    assert state.attributes["offset"] == "00:40:00"
    assert state.attributes["default_offset"] is False


@pytest.mark.parametrize(
    ("data", "expected", "location"),
    [
        # Not #отвезти: Dad's work, not Masha's walk with the dog
        (
            {"exclude_tags": ["отвезти"], "calendars": ["calendar.dad"]},
            "2026-10-22T03:30:00+00:00",
            "",
        ),
        # Masha's calendar only: school, not Dad's work
        (
            {"tags": ["leaf", "отвезти"], "calendars": ["calendar.masha"]},
            "2026-10-22T04:20:00+00:00",
            "School No. 5",
        ),
    ],
)
async def test_calendar_sensor_without_tags_in_calendars(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    config_entry: MockConfigEntry,
    frozen_now,
    data: dict[str, Any],
    expected: str,
    location: str,
) -> None:
    """Events without some tags, of some calendars; the location is shown."""
    calendars["masha"].set(
        [
            event(
                "School",
                "2026-10-22T08:00:00",
                "2026-10-22T13:45:00",
                "#leaf #отвезти\nВ пути: 00:40:00",
                "School No. 5",
            ),
            event("Walk", "2026-10-22T06:00:00", "2026-10-22T06:30:00", "#leaf"),
        ]
    )
    await setup_sensors(hass, config_entry, frozen_now, calendar_sensor(**data))
    state = hass.states.get("sensor.vyezd_leaf")
    assert state.state == expected
    assert state.attributes["location"] == location


PLANS = "calendar.zaya_planner_plans"
MARKS = "automation.departures"
CHARGING = "automation.charging"
LESSON = "#leaf #отвезти\nВ пути: 00:40:00"


async def plans_of(hass: HomeAssistant, owner: str) -> list[dict[str, Any]]:
    """Return the plans of an owner."""
    response = await hass.services.async_call(
        DOMAIN, "get_plans", {"owner": owner}, blocking=True, return_response=True
    )
    return response["plans"]


async def plan_marks(hass: HomeAssistant, **data: Any) -> list[tuple[str, str, str]]:
    """Call zaya_planner.plan_marks; return the marks: (status, start, summary)."""
    await hass.services.async_call(
        DOMAIN,
        "plan_marks",
        {
            "owner": MARKS,
            "time_entity": "sensor.vyezd_leaf",
            "summary": "Departure",
            "tags": ["leaf", "отвезти"],
            **data,
        },
        blocking=True,
    )
    return [(p["status"], p["start"], p["summary"]) for p in await plans_of(hass, MARKS)]


async def plan_charging(hass: HomeAssistant, entity_id: str, start: int) -> list[tuple[str, str]]:
    """Call zaya_planner.plan_window from a calendar sensor; return the windows."""
    await hass.services.async_call(
        DOMAIN,
        "plan_window",
        {
            "owner": CHARGING,
            "devices": ["switch.charger"],
            "start_entity": entity_id,
            "start_offset": start,
            "end_entity": entity_id,
            "end_offset": -20,
            "tags": ["night"],
        },
        blocking=True,
    )
    return [
        (p["start"], p["end"])
        for p in await plans_of(hass, CHARGING)
        if p["status"] == "planned"
    ]


async def setup_trips(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """Masha's trips for two days; marks of them on “Plans” read by another sensor."""
    calendars["masha"].set(
        [
            event("Maths", "2026-10-22T08:00:00", "2026-10-22T08:35:00", LESSON, "School"),
            event("Music", "2026-10-22T12:00:00", "2026-10-22T13:00:00", LESSON),
            event("Pool", "2026-10-22T17:00:00", "2026-10-22T18:00:00", LESSON, "Pool"),
            event("Maths", "2026-10-23T08:00:00", "2026-10-23T08:35:00", LESSON, "School"),
            # Beyond the horizon of two days
            event("Maths", "2026-10-26T08:00:00", "2026-10-26T08:35:00", LESSON, "School"),
        ]
    )
    calendars["dad"].set([])
    hass.states.async_set("switch.charger", "off", {"friendly_name": "Charger"})
    await setup_sensors(
        hass,
        config_entry,
        freezer,
        # Every trip within two days
        calendar_sensor(
            tags=["leaf", "отвезти"],
            calendars=["calendar.masha"],
            first_of_day=False,
            horizon={"days": 2},
        ),
        # The first departure of each day among the marks on “Plans”
        calendar_sensor(
            "Leaf",
            tags=["leaf", "отвезти"],
            calendars=[PLANS],
            offset_key=None,
            default_offset={"hours": 0, "minutes": 0, "seconds": 0},
            horizon={"days": 2},
        ),
        options={CONF_CALENDARS: ["calendar.masha", "calendar.dad", PLANS], CONF_TAGS: ["leaf", "отвезти", "night"]},
    )


async def test_plan_marks(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    config_entry: MockConfigEntry,
    frozen_now,
    hass_ws_client,
) -> None:
    """A mark for each time of a sensor; another sensor reads them on “Plans”; a person decides."""
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    done = async_capture_events(hass, EVENT_PLAN_DONE)
    await setup_trips(hass, calendars, config_entry, frozen_now)
    times = hass.states.get("sensor.vyezd_leaf").attributes["times"]
    assert [(t["at"], t["summary"], t["location"]) for t in times] == [
        ("2026-10-22T07:20:00+03:00", "Maths", "School"),
        ("2026-10-22T11:20:00+03:00", "Music", ""),
        ("2026-10-22T16:20:00+03:00", "Pool", "Pool"),
        ("2026-10-23T07:20:00+03:00", "Maths", "School"),
    ]

    marks = await plan_marks(hass)
    assert marks == [
        ("planned", "2026-10-22T07:20:00+03:00", "Departure — School"),
        ("planned", "2026-10-22T11:20:00+03:00", "Departure"),
        ("planned", "2026-10-22T16:20:00+03:00", "Departure — Pool"),
        ("planned", "2026-10-23T07:20:00+03:00", "Departure — School"),
    ]
    assert len(logbook) == 4
    # The same again: nothing changes, nothing is written
    assert await plan_marks(hass) == marks
    assert len(logbook) == 4
    school = (await plans_of(hass, MARKS))[0]
    assert school["steps"][0]["summary"] == "Departure — School"
    assert school["tags"] == ["leaf", "отвезти"]

    # The marks are on “Plans”: the other sensor lists the first of each day
    await settle(hass, frozen_now)
    state = hass.states.get("sensor.leaf")
    assert (state.state, state.attributes["summary"]) == (
        "2026-10-22T04:20:00+00:00",
        "Departure — School",
    )
    assert [t["at"] for t in state.attributes["times"]] == [
        "2026-10-22T07:20:00+03:00",
        "2026-10-23T07:20:00+03:00",
    ]

    # Music is gone from the calendar: so is its mark
    calendars["masha"].set([e for e in calendars["masha"].events if e.summary != "Music"])
    await settle(hass, frozen_now)
    marks = await plan_marks(hass)
    assert [m[0] for m in marks] == ["planned", "cancelled", "planned", "planned"]

    # A person moves the school mark to 7:00: frozen; the automation does not move it back
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "calendar/event/update",
            "entity_id": PLANS,
            "uid": school["uid"],
            "event": {
                "summary": "Departure — School",
                "dtstart": "2026-10-22T07:00:00+03:00",
                "dtend": "2026-10-22T07:01:00+03:00",
            },
        }
    )
    assert (await client.receive_json())["success"]
    marks = await plan_marks(hass)
    assert marks[0] == ("frozen", "2026-10-22T07:00:00+03:00", "Departure — School")
    assert len(marks) == 4
    await settle(hass, frozen_now)
    assert hass.states.get("sensor.leaf").state == "2026-10-22T04:00:00+00:00"

    # A person deletes the pool mark: it is not made again
    pool = (await plans_of(hass, MARKS))[2]["uid"]
    await client.send_json_auto_id(
        {"type": "calendar/event/delete", "entity_id": PLANS, "uid": pool}
    )
    assert (await client.receive_json())["success"]
    marks = await plan_marks(hass)
    assert [m[0] for m in marks] == ["frozen", "cancelled", "cancelled", "planned"]
    # It stays on “Plans” as 🚫, but searches do not find it
    await settle(hass, frozen_now)
    found = await find(hass, filter="#leaf", calendars=[PLANS])
    assert not any("Pool" in e["summary"] for e in found)
    assert any("School" in e["summary"] for e in found)

    # At 7:00 the mark is done: there is nothing to run; it is not made again
    frozen_now.move_to("2026-10-22T04:00:30+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    marks = await plan_marks(hass)
    assert [m[0] for m in marks] == ["done", "cancelled", "cancelled", "planned"]
    assert [e.data["summary"] for e in done] == ["Departure — School"]


async def test_plan_marks_from_several_sensors(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    config_entry: MockConfigEntry,
    frozen_now,
) -> None:
    """“Take there” from the start of lessons, “pick up” from their end: one set of departures."""
    calendars["masha"].set(
        [
            event("Maths", "2026-10-22T08:00:00", "2026-10-22T08:35:00", LESSON, "School"),
            event("Maths", "2026-10-23T08:00:00", "2026-10-23T08:35:00", LESSON, "School"),
        ]
    )
    lessons = {"tags": ["leaf"], "calendars": ["calendar.masha"], "horizon": {"days": 2}}
    await setup_sensors(
        hass,
        config_entry,
        frozen_now,
        calendar_sensor("Take", **lessons),
        calendar_sensor("Pick up", edge="end", **lessons),
    )
    # The same sensor twice: a time given twice is one mark
    marks = await plan_marks(
        hass, time_entity=["sensor.take", "sensor.pick_up", "sensor.take"]
    )
    assert marks == [
        ("planned", "2026-10-22T07:20:00+03:00", "Departure — School"),
        ("planned", "2026-10-22T07:55:00+03:00", "Departure — School"),
        ("planned", "2026-10-23T07:20:00+03:00", "Departure — School"),
        ("planned", "2026-10-23T07:55:00+03:00", "Departure — School"),
    ]

    # One of them is unavailable: nothing changes
    hass.states.async_set("sensor.pick_up", "unavailable")
    assert await plan_marks(hass, time_entity=["sensor.take", "sensor.pick_up"]) == marks
    # Only “take there” now: the “pick up” marks are cancelled
    marks = await plan_marks(hass, time_entity=["sensor.take"])
    assert [m[0] for m in marks] == ["planned", "cancelled", "planned", "cancelled"]


async def test_first_of_the_day_stays_first(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    config_entry: MockConfigEntry,
    frozen_now,
) -> None:
    """Once the morning trip is over, the afternoon one does not become the first of the day."""
    calendars["masha"].set(
        [
            event("Maths", "2026-10-22T08:00:00", "2026-10-22T08:35:00", LESSON, "School"),
            event("Football", "2026-10-22T17:00:00", "2026-10-22T18:00:00", LESSON),
        ]
    )
    await setup_sensors(
        hass,
        config_entry,
        frozen_now,
        calendar_sensor(
            tags=["leaf", "отвезти"], calendars=["calendar.masha"], horizon={"days": 2}
        ),
    )
    assert hass.states.get("sensor.vyezd_leaf").state == "2026-10-22T04:20:00+00:00"

    # Thursday 8:40: the lesson is over; football is not the first of the day
    frozen_now.move_to("2026-10-22T05:40:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await settle(hass, frozen_now)
    state = hass.states.get("sensor.vyezd_leaf")
    assert state.state == "unknown"
    assert state.attributes["times"] == []


async def test_plan_window_for_each_time(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    config_entry: MockConfigEntry,
    frozen_now,
) -> None:
    """A window before each time of a sensor; overlapping windows become one."""
    await setup_trips(hass, calendars, config_entry, frozen_now)
    await plan_marks(hass)
    await settle(hass, frozen_now)

    # Charging before the first departure of each day
    assert await plan_charging(hass, "sensor.leaf", -90) == [
        ("2026-10-22T05:50:00+03:00", "2026-10-22T07:00:00+03:00"),
        ("2026-10-23T05:50:00+03:00", "2026-10-23T07:00:00+03:00"),
    ]
    # Five hours before every trip: the morning and the noon windows overlap
    assert await plan_charging(hass, "sensor.vyezd_leaf", -300) == [
        ("2026-10-22T02:20:00+03:00", "2026-10-22T11:00:00+03:00"),
        ("2026-10-22T11:20:00+03:00", "2026-10-22T16:00:00+03:00"),
        ("2026-10-23T02:20:00+03:00", "2026-10-23T07:00:00+03:00"),
    ]
    # Tonight's window is frozen; the schedule changes: tomorrow's follows, tonight's stays
    await hass.services.async_call(
        DOMAIN, "freeze_plans", {"tags": ["night"], "within_hours": 12}, blocking=True
    )
    calendars["masha"].set(
        [
            event("Maths", "2026-10-22T09:00:00", "2026-10-22T09:35:00", LESSON, "School"),
            event("Maths", "2026-10-23T09:00:00", "2026-10-23T09:35:00", LESSON, "School"),
        ]
    )
    await settle(hass, frozen_now)
    await plan_charging(hass, "sensor.vyezd_leaf", -300)
    plans = [(p["status"], p["start"]) for p in await plans_of(hass, CHARGING)]
    assert ("frozen", "2026-10-22T02:20:00+03:00") in plans
    assert ("planned", "2026-10-23T03:20:00+03:00") in plans
    assert ("planned", "2026-10-22T03:20:00+03:00") not in plans


async def test_step_at_an_event(
    hass: HomeAssistant,
    calendars: dict[str, FakeCalendar],
    setup_entry: MockConfigEntry,
) -> None:
    """A step at the next fitting event: its start, 70 minutes earlier."""
    data = {"owner": "automation.charging", "event": "#leaf #отвезти"}
    context = Context()
    await hass.services.async_call(
        DOMAIN,
        "add_step",
        {**data, "offset": -70, "devices": ["switch.charger"], "state": "off"},
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
    # School at 8:00 (Moscow)
    assert [s["at"] for s in plan["steps"]] == ["2026-10-22T06:50:00+03:00"]

    # “Minus attribute” is gone: a calendar sensor does that
    with pytest.raises(Exception):
        await hass.services.async_call(
            DOMAIN,
            "add_step",
            {**data, "subtract": "В пути", "devices": ["switch.charger"], "state": "on"},
            blocking=True,
            context=context,
        )


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
