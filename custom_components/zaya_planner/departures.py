"""Departures: “the first event of the day with #leaf, its start minus the travel time”.

A departure is added with “Add departure” on the integration page (a subentry).
Pure calculation, no Home Assistant objects: the events are given, durations
are read with the function given (Home Assistant's `parse_duration`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from .const import (
    CONF_CALENDARS,
    CONF_DEFAULT_TRAVEL,
    CONF_EDGE,
    CONF_EXCLUDE_TAGS,
    CONF_FIRST_OF_DAY,
    CONF_TAGS,
    CONF_TRAVEL_KEY,
    DEFAULT_TRAVEL,
    EDGE_START,
)
from .description import Filter, Reading, first_of_each_day, norm

Edge = Literal["start", "end"]


class Event(Protocol):
    """An event of a chosen calendar (see events.FoundEvent)."""

    @property
    def calendar(self) -> str: ...

    @property
    def summary(self) -> str: ...

    @property
    def start(self) -> Any: ...

    @property
    def end(self) -> Any: ...

    @property
    def location(self) -> str: ...

    @property
    def reading(self) -> Reading: ...


def duration_of(value: Mapping[str, Any] | None) -> timedelta:
    """Return a duration as the duration selector gives it ({hours, minutes, seconds})."""
    units = ("days", "hours", "minutes", "seconds")
    return timedelta(**{unit: value[unit] for unit in units if unit in (value or {})})


@dataclass(slots=True, frozen=True)
class DepartureSpec:
    """How a departure is worked out from events."""

    tags: tuple[str, ...]
    # … and none of these
    exclude_tags: tuple[str, ...] = ()
    # Events of these calendars only (empty: all the chosen ones)
    calendars: tuple[str, ...] = ()
    # Leave for the start of the event (take there) or for its end (pick up)
    edge: Edge = "start"
    # The travel time is the value of this key in the event (e.g. “Travel: 00:40:00”)
    travel_key: str | None = None
    # … or this, when the event has no such value
    default_travel: timedelta = DEFAULT_TRAVEL
    first_of_day: bool = True

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> DepartureSpec:
        """Return the spec of a departure from its subentry data."""
        return cls(
            tags=tuple(data.get(CONF_TAGS) or ()),
            exclude_tags=tuple(data.get(CONF_EXCLUDE_TAGS) or ()),
            calendars=tuple(data.get(CONF_CALENDARS) or ()),
            edge=data.get(CONF_EDGE, EDGE_START),
            travel_key=data.get(CONF_TRAVEL_KEY) or None,
            default_travel=(
                duration_of(data[CONF_DEFAULT_TRAVEL])
                if CONF_DEFAULT_TRAVEL in data
                else DEFAULT_TRAVEL
            ),
            first_of_day=data.get(CONF_FIRST_OF_DAY, True),
        )


@dataclass(slots=True, frozen=True)
class Departure:
    """When to leave, and for which event."""

    at: datetime
    event: Event
    travel: timedelta
    # The event has no travel time of its own
    default_travel: bool


@dataclass(slots=True, frozen=True)
class DepartureProblem:
    """The travel time of an event is not known: the default one is used."""

    # no_travel_time: the event has no such key; not_duration: its value is not a duration
    code: str
    event: Event
    value: str


def calculate(
    spec: DepartureSpec,
    events: Iterable[Event],
    parse_duration: Callable[[str], timedelta | None],
) -> tuple[list[Departure], list[DepartureProblem]]:
    """Return the departures for the events, by time, and the events without a travel time.

    All-day events are left out: there is no time to leave for.
    """
    event_filter = Filter(tags=spec.tags, excluded=spec.exclude_tags)
    fitting = [
        event
        for event in events
        if isinstance(event.start, datetime)
        and (not spec.calendars or event.calendar in spec.calendars)
        and event_filter.matches(event.reading)
    ]
    if spec.first_of_day:
        fitting = first_of_each_day(fitting)

    departures: list[Departure] = []
    problems: list[DepartureProblem] = []
    for event in fitting:
        travel: timedelta | None = None
        if spec.travel_key:
            attributes = event.reading.attributes
            value = next(
                (v for k, v in attributes.items() if norm(k) == norm(spec.travel_key)),
                None,
            )
            if value is None:
                problems.append(DepartureProblem("no_travel_time", event, spec.travel_key))
            elif (travel := parse_duration(value)) is None:
                problems.append(DepartureProblem("not_duration", event, value))
        edge = event.start if spec.edge == EDGE_START else event.end
        used = travel if travel is not None else spec.default_travel
        departures.append(
            Departure(edge - used, event, used, default_travel=travel is None)
        )
    departures.sort(key=lambda departure: departure.at)
    return departures, problems


def next_departure(departures: Sequence[Departure], now: datetime) -> Departure | None:
    """Return the first departure that has not passed."""
    return next((departure for departure in departures if departure.at > now), None)
