"""Calendar sensors: times of events with tags, e.g. “#leaf events, their start minus the travel”.

A calendar sensor is added with “Add calendar sensor” on the integration page
(a subentry). The time of an event is its start (or end) minus (or plus) the
duration in one of its attributes (“Travel: 00:40:00”), or a default offset.
The sensor shows the next time; with a horizon it lists all times within it.
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
    CONF_DEFAULT_OFFSET,
    CONF_EDGE,
    CONF_EXCLUDE_TAGS,
    CONF_FIRST_OF_DAY,
    CONF_HORIZON,
    CONF_OFFSET_KEY,
    CONF_SIGN,
    CONF_TAGS,
    EDGE_START,
    SIGN_MINUS,
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
class CalendarSensorSpec:
    """Which events, and how their times are worked out."""

    tags: tuple[str, ...]
    # … and none of these
    exclude_tags: tuple[str, ...] = ()
    # Events of these calendars only (empty: all the chosen ones)
    calendars: tuple[str, ...] = ()
    # The start of the event or its end
    edge: Edge = "start"
    # The offset is the value of this key in the event (e.g. “Travel: 00:40:00”) …
    offset_key: str | None = None
    # … or this, when the event has no such value
    default_offset: timedelta = timedelta(0)
    # Earlier (minus) or later (plus) than the edge
    minus: bool = True
    # List the times within this period from now (None: only the next time)
    horizon: timedelta | None = None
    first_of_day: bool = False

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> CalendarSensorSpec:
        """Return the spec of a calendar sensor from its subentry data."""
        horizon = duration_of(data.get(CONF_HORIZON)) if data.get(CONF_HORIZON) else None
        return cls(
            tags=tuple(data.get(CONF_TAGS) or ()),
            exclude_tags=tuple(data.get(CONF_EXCLUDE_TAGS) or ()),
            calendars=tuple(data.get(CONF_CALENDARS) or ()),
            edge=data.get(CONF_EDGE, EDGE_START),
            offset_key=data.get(CONF_OFFSET_KEY) or None,
            default_offset=duration_of(data.get(CONF_DEFAULT_OFFSET)),
            minus=data.get(CONF_SIGN, SIGN_MINUS) == SIGN_MINUS,
            horizon=horizon if horizon else None,
            first_of_day=data.get(CONF_FIRST_OF_DAY, False),
        )


@dataclass(slots=True, frozen=True)
class EventTime:
    """A time worked out from an event."""

    at: datetime
    event: Event
    offset: timedelta
    # The event has no offset of its own: the default one is used
    default_offset: bool


@dataclass(slots=True, frozen=True)
class OffsetProblem:
    """The offset of an event is not known: the default one is used."""

    # no_offset: the event has no such key; not_duration: its value is not a duration
    code: str
    event: Event
    value: str


def calculate(
    spec: CalendarSensorSpec,
    events: Iterable[Event],
    parse_duration: Callable[[str], timedelta | None],
) -> tuple[list[EventTime], list[OffsetProblem]]:
    """Return the times of the events, in order, and the events without an offset.

    All-day events are left out: they have no time.
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

    times: list[EventTime] = []
    problems: list[OffsetProblem] = []
    for event in fitting:
        offset: timedelta | None = None
        if spec.offset_key:
            attributes = event.reading.attributes
            value = next(
                (v for k, v in attributes.items() if norm(k) == norm(spec.offset_key)),
                None,
            )
            if value is None:
                problems.append(OffsetProblem("no_offset", event, spec.offset_key))
            elif (offset := parse_duration(value)) is None:
                problems.append(OffsetProblem("not_duration", event, value))
        edge = event.start if spec.edge == EDGE_START else event.end
        used = offset if offset is not None else spec.default_offset
        at = edge - used if spec.minus else edge + used
        times.append(EventTime(at, event, used, default_offset=offset is None))
    times.sort(key=lambda time: time.at)
    return times, problems


def upcoming(
    times: Sequence[EventTime], now: datetime, horizon: timedelta | None
) -> list[EventTime]:
    """Return the times that have not come yet: within the horizon, or only the next one."""
    future = [time for time in times if time.at > now]
    if horizon is None:
        return future[:1]
    return [time for time in future if time.at <= now + horizon]
