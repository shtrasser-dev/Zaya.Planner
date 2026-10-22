"""Windows: keep devices on from a start to an end.

The start and the end come from one of three sources each:

- a time of day (with days of the week): 06:40 on weekdays;
- the time an entity shows, plus an offset: a wake-up − 20 min, sunset + 30 min;
- a length counted from the other end: “N minutes before the end” for the
  start (charging), “N minutes after the start” for the end (duration).

`next_window` returns the window that has not ended yet: the current one or the
next one. Pure calculation, no Home Assistant objects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Fixed times are looked for this many days back and ahead
SEARCH_DAYS = range(-1, 9)


@dataclass(slots=True, frozen=True)
class Edge:
    """Where one end of the window comes from (exactly one source is set)."""

    time: time | None = None
    # Moments shown by entities; the earliest one counts (None: no value)
    moments: tuple[datetime | None, ...] = ()
    offset: timedelta = timedelta()
    # Length from the other end of the window
    length: timedelta | None = None

    @property
    def is_time(self) -> bool:
        """Return True for a time of day."""
        return self.time is not None

    @property
    def is_moment(self) -> bool:
        """Return True for an entity's time."""
        return self.time is None and self.length is None

    def moment(self) -> datetime | None:
        """Return the earliest moment plus the offset."""
        known = [m for m in self.moments if m is not None]
        return min(known) + self.offset if known else None


@dataclass(slots=True, frozen=True)
class Window:
    """From start to end, in local time."""

    start: datetime
    end: datetime


def _at(day: date, clock: time, tz: Any) -> datetime:
    return datetime.combine(day, clock, tzinfo=tz)


def _next_time_after(moment: datetime, clock: time, tz: Any) -> datetime:
    """Return the first occurrence of a time of day after a moment."""
    candidate = _at(moment.astimezone(tz).date(), clock, tz)
    return candidate if candidate > moment else candidate + timedelta(days=1)


def _end_for(start: datetime, end: Edge, tz: Any) -> datetime | None:
    """Return the end of a window that starts at `start`."""
    if end.is_time:
        return _next_time_after(start, end.time, tz)  # type: ignore[arg-type]
    if end.length is not None:
        return start + end.length
    return end.moment()


def next_window(
    start: Edge,
    end: Edge,
    days: Sequence[str],
    now: datetime,
    tz: Any,
) -> Window | None:
    """Return the window that has not ended yet, or None."""
    today = now.astimezone(tz).date()
    allowed: Callable[[date], bool] = lambda day: WEEKDAYS[day.weekday()] in days  # noqa: E731

    candidates: Iterable[Window]
    if start.length is not None:
        # The start is counted back from the end
        if end.is_time:
            ends = (
                _at(today + timedelta(days=n), end.time, tz)  # type: ignore[arg-type]
                for n in SEARCH_DAYS
                if allowed(today + timedelta(days=n))
            )
        else:
            moment = end.moment()
            ends = iter([moment] if moment is not None else [])
        candidates = (Window(e - start.length, e) for e in ends)
    elif start.is_time:
        starts = (
            _at(today + timedelta(days=n), start.time, tz)  # type: ignore[arg-type]
            for n in SEARCH_DAYS
            if allowed(today + timedelta(days=n))
        )
        candidates = (
            Window(s, e) for s in starts if (e := _end_for(s, end, tz)) is not None
        )
    else:
        moment = start.moment()
        if moment is None:
            return None
        if end.is_time:
            # A fixed end on the chosen days, the first one after the start
            end_moment = next(
                (
                    e
                    for n in SEARCH_DAYS
                    if allowed(day := (moment.astimezone(tz).date() + timedelta(days=n)))
                    and (e := _at(day, end.time, tz)) > moment  # type: ignore[arg-type]
                ),
                None,
            )
        else:
            end_moment = _end_for(moment, end, tz)
        candidates = iter([Window(moment, end_moment)] if end_moment else [])

    return next((w for w in candidates if w.start < w.end and w.end > now), None)
