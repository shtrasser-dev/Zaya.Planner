"""Tests of working out moments from events (no Home Assistant)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.zaya_planner.description import Dictionary, Filter, Reading
from custom_components.zaya_planner.moments import (
    MomentProblem,
    MomentSpec,
    calculate,
    earliest,
    next_moment,
)
from homeassistant.util.dt import parse_duration

TZ = ZoneInfo("Europe/Moscow")
WORDS = Dictionary(["leaf", "отвезти", "забрать", "отмена"], ["В пути"])


@dataclass
class Event:
    summary: str
    start: date | datetime
    end: date | datetime
    description: str = ""
    calendar: str = "calendar.masha"
    reading: Reading = field(init=False)

    def __post_init__(self) -> None:
        self.reading = WORDS.read(self.description)


def at(day: int, clock: str) -> datetime:
    """A time in October 2026, Moscow."""
    hour, minute = map(int, clock.split(":"))
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def school(day: int, start: str = "08:00", end: str = "13:45", **kwargs) -> Event:
    return Event("School", at(day, start), at(day, end), "#leaf #отвезти\nВ пути: 40:00", **kwargs)


DEPARTURE = MomentSpec(
    name="Leaf: departure", event_filter=Filter(("leaf",)), offset_key="В пути"
)


def times(spec: MomentSpec, events: list[Event]) -> list[str]:
    moments, problems = calculate(spec, events, TZ, parse_duration)
    assert problems == []
    return [f"{m.at:%d %H:%M}" for m in moments]


def test_start_minus_travel_first_of_day() -> None:
    """The first event of each day with #leaf, its start minus the travel time."""
    events = [
        school(26),
        Event("Pool", at(26, "17:00"), at(26, "18:00"), "#leaf\nВ пути: 30:00"),
        school(27, start="09:00"),
        Event("Work", at(26, "07:00"), at(26, "16:00"), "без тегов"),
    ]
    assert times(DEPARTURE, events) == ["26 07:20", "27 08:20"]
    every = MomentSpec(name="x", event_filter=Filter(("leaf",)), offset_key="В пути", first_of_day=False)
    assert times(every, events) == ["26 07:20", "26 16:30", "27 08:20"]


def test_end_edge_offset_and_calendars() -> None:
    """Pick-up: the end minus the travel time, minus 5 more minutes; one calendar only."""
    spec = MomentSpec(
        name="Pick up",
        calendars=("calendar.masha",),
        event_filter=Filter(("leaf",)),
        edge="end",
        offset_key="в пути",
        offset=timedelta(minutes=-5),
    )
    events = [school(26), school(27, calendar="calendar.dad")]
    assert times(spec, events) == ["26 13:00"]
    # Plus instead of minus
    plus = MomentSpec(name="x", event_filter=Filter(("leaf",)), offset_key="В пути", key_sign=1)
    assert times(plus, [school(26)]) == ["26 08:40"]


def test_filter_by_attribute() -> None:
    """Conditions on attributes work too."""
    spec = MomentSpec(name="x", event_filter=Filter.parse("#leaf\nВ пути: 30:00"))
    events = [school(26), Event("Pool", at(26, "17:00"), at(26, "18:00"), "#leaf\nВ пути: 30:00")]
    assert times(spec, events) == ["26 17:00"]


def test_problems() -> None:
    """No travel time, or one that is not a duration: the event is left out and reported."""
    events = [
        Event("Club", at(26, "15:00"), at(26, "16:00"), "#leaf"),
        Event("Pool", at(27, "17:00"), at(27, "18:00"), "#leaf\nВ пути: сорок"),
        school(28),
    ]
    moments, problems = calculate(DEPARTURE, events, TZ, parse_duration)
    assert [f"{m.at:%d %H:%M}" for m in moments] == ["28 07:20"]
    assert problems == [
        MomentProblem("no_value", events[0], "В пути"),
        MomentProblem("not_duration", events[1], "сорок"),
    ]


def test_cancellations() -> None:
    """A cancellation in the same calendar cuts the event; one inside it is gone."""
    spec = MomentSpec(
        name="x", event_filter=Filter(("leaf",)), offset_key="В пути", cancel_tags=("отмена",)
    )
    events = [
        school(26),
        # Monday: no first two lessons
        Event("No first lessons", at(26, "08:00"), at(26, "09:40"), "#отмена"),
        school(27),
        # Tuesday: frost, all day
        Event("Frost", date(2026, 10, 27), date(2026, 10, 28), "#отмена"),
        school(28),
        # In another calendar it changes nothing
        Event("Frost", date(2026, 10, 28), date(2026, 10, 29), "#отмена", calendar="calendar.dad"),
    ]
    assert times(spec, events) == ["26 09:00", "28 07:20"]
    # Pick-up: an end inside a cancellation moves to its start
    pick_up = MomentSpec(name="x", event_filter=Filter(("leaf",)), edge="end", cancel_tags=("отмена",))
    events = [school(26), Event("Early", at(26, "12:00"), at(26, "14:00"), "#отмена")]
    assert times(pick_up, events) == ["26 12:00"]


def test_next_and_earliest() -> None:
    """The first moment not passed; the earliest of several, plus an offset."""
    moments, _ = calculate(DEPARTURE, [school(26), school(27)], TZ, parse_duration)
    assert next_moment(moments, at(26, "07:20")).at == at(27, "07:20")
    assert next_moment(moments, at(28, "00:00")) is None
    assert earliest([None, at(27, "06:20"), at(26, "08:30")], timedelta(minutes=-60)) == at(26, "07:30")
    assert earliest([None]) is None
