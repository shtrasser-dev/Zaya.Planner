"""Moments worked out from events: “the first event of the day with #leaf, minus Travel”.

Pure calculation, no Home Assistant objects: the events are given, durations
are read with the function given (Home Assistant's `parse_duration`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Literal, Protocol

from .description import Filter, Reading, norm

Edge = Literal["start", "end"]


class Event(Protocol):
    """An event of a chosen calendar (see events.FoundEvent)."""

    @property
    def calendar(self) -> str: ...

    @property
    def summary(self) -> str: ...

    @property
    def start(self) -> date | datetime: ...

    @property
    def end(self) -> date | datetime: ...

    @property
    def reading(self) -> Reading: ...


@dataclass(slots=True, frozen=True)
class MomentSpec:
    """How a moment is worked out from events (a moment “from calendars”)."""

    name: str
    # Calendars to look in; empty: all chosen calendars
    calendars: tuple[str, ...] = ()
    event_filter: Filter = Filter()
    edge: Edge = "start"
    # Minus (or plus) the duration in this attribute, e.g. “Travel”
    offset_key: str | None = None
    key_sign: int = -1
    offset: timedelta = timedelta()
    first_of_day: bool = True
    # Events with these tags cancel what happens in their time in the same calendar
    cancel_tags: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class Moment:
    """A moment and the event it comes from."""

    at: datetime
    event: Event


@dataclass(slots=True, frozen=True)
class MomentProblem:
    """An event fits the filter but its moment cannot be worked out."""

    # no_value: the attribute is missing; not_duration: its value is not a duration
    code: str
    event: Event
    value: str


def _as_datetime(value: date | datetime, tz: tzinfo) -> datetime:
    """All-day events start and end at midnight."""
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min, tzinfo=tz)


def _is_cancellation(event: Event, cancel_tags: Sequence[str]) -> bool:
    tags = {norm(tag) for tag in event.reading.tags}
    return bool(cancel_tags) and any(norm(tag) in tags for tag in cancel_tags)


def _after_cancellations(
    start: datetime, end: datetime, cancellations: Sequence[tuple[datetime, datetime]]
) -> tuple[datetime, datetime] | None:
    """Return the event's time with cancellations cut out, or None if nothing is left.

    An event entirely inside a cancellation does not happen; one that starts
    inside starts when it ends; one that ends inside ends when it starts.
    """
    changed = True
    while changed:
        changed = False
        for c_start, c_end in cancellations:
            if c_start <= start and end <= c_end:
                return None
            if c_start <= start < c_end:
                start, changed = c_end, True
            elif c_start < end <= c_end:
                end, changed = c_start, True
            if start >= end:
                return None
    return start, end


def calculate(
    spec: MomentSpec,
    events: Iterable[Event],
    tz: tzinfo,
    parse_duration: Callable[[str], timedelta | None],
) -> tuple[list[Moment], list[MomentProblem]]:
    """Return the moments of the events, by time, and the events that fail."""
    events = [e for e in events if not spec.calendars or e.calendar in spec.calendars]
    cancellations: dict[str, list[tuple[datetime, datetime]]] = {}
    for event in events:
        if _is_cancellation(event, spec.cancel_tags):
            cancellations.setdefault(event.calendar, []).append(
                (_as_datetime(event.start, tz), _as_datetime(event.end, tz))
            )

    moments: list[Moment] = []
    problems: list[MomentProblem] = []
    for event in events:
        if _is_cancellation(event, spec.cancel_tags) or not spec.event_filter.matches(event.reading):
            continue
        span = _after_cancellations(
            _as_datetime(event.start, tz),
            _as_datetime(event.end, tz),
            cancellations.get(event.calendar, []),
        )
        if span is None:
            continue
        at = (span[0] if spec.edge == "start" else span[1]) + spec.offset
        if spec.offset_key:
            value = next(
                (v for k, v in event.reading.attributes.items() if norm(k) == norm(spec.offset_key)),
                None,
            )
            if value is None:
                problems.append(MomentProblem("no_value", event, spec.offset_key))
                continue
            if (duration := parse_duration(value)) is None:
                problems.append(MomentProblem("not_duration", event, value))
                continue
            at += spec.key_sign * duration
        moments.append(Moment(at, event))

    moments.sort(key=lambda moment: moment.at)
    if spec.first_of_day:
        # The earliest moment of the events of each day
        first: dict[date, Moment] = {}
        for moment in moments:
            start = _as_datetime(moment.event.start, tz).astimezone(tz)
            first.setdefault(start.date(), moment)
        moments = sorted(first.values(), key=lambda moment: moment.at)
    return moments, problems


def next_moment(moments: Sequence[Moment], now: datetime) -> Moment | None:
    """Return the first moment that has not passed."""
    return next((moment for moment in moments if moment.at > now), None)


def earliest(
    values: Iterable[datetime | None], offset: timedelta = timedelta()
) -> datetime | None:
    """Return the earliest of the known values plus an offset (a moment “from moments”)."""
    known = [value for value in values if value is not None]
    return min(known) + offset if known else None
