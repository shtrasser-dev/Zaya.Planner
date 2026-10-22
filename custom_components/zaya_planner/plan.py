"""The house plan: departures and wake-ups worked out from person calendars.

Pure calculation: it takes the events of each person (with their Zaya fields)
and the settings, and returns what happens when. Nothing here talks to Home
Assistant, so it is easy to test and to reason about.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .const import EventKind, TripKind
from .details import EventDetails


@dataclass(slots=True, frozen=True)
class PlanEvent:
    """An event of a person calendar, as the plan sees it."""

    uid: str
    summary: str
    # Timed events are aware datetimes; all-day events are dates
    start: datetime | date
    end: datetime | date
    details: EventDetails

    @property
    def all_day(self) -> bool:
        """Return True for an all-day event."""
        return not isinstance(self.start, datetime)


@dataclass(slots=True, frozen=True)
class PersonInput:
    """What the plan needs to know about a person."""

    entity_id: str
    name: str
    getting_ready: int
    wake_for_trips: bool
    events: tuple[PlanEvent, ...]


@dataclass(slots=True, frozen=True)
class Departure:
    """Someone leaves home to arrive somewhere by a given time."""

    at: datetime
    arrive: datetime
    trip: TripKind  # DROP (to the start) or PICKUP (to the end)
    summary: str
    owner: str  # entity id of the person whose calendar has the event
    riders: tuple[str, ...]
    driver: str | None
    car: str | None
    travel_minutes: int
    uid: str


@dataclass(slots=True, frozen=True)
class WakeUp:
    """A person gets up."""

    at: datetime
    person: str
    # Departure that needs it, or None for a wake-up set in the calendar
    departure: Departure | None = None
    summary: str | None = None


@dataclass(slots=True)
class Plan:
    """Departures and wake-ups, ordered by time."""

    departures: list[Departure] = field(default_factory=list)
    wake_ups: list[WakeUp] = field(default_factory=list)


def _span(event: PlanEvent, tz: Any) -> tuple[datetime, datetime]:
    """Return the event's time span; all-day events cover whole local days."""
    if event.all_day:
        start = datetime.combine(event.start, datetime.min.time(), tzinfo=tz)
        end = datetime.combine(event.end, datetime.min.time(), tzinfo=tz)
        return start, end
    return event.start, event.end  # type: ignore[return-value]


def _apply_cancellations(
    events: Iterable[PlanEvent], tz: Any
) -> tuple[list[PlanEvent], list[tuple[datetime, datetime]]]:
    """Trim or drop timed events by the cancellations among them.

    Returns the remaining timed events (not cancellations) and the cancelled spans.
    """
    events = list(events)
    cancelled = [_span(e, tz) for e in events if e.details.kind == EventKind.CANCEL]
    result: list[PlanEvent] = []
    for event in events:
        if event.details.kind == EventKind.CANCEL or event.all_day:
            continue
        start, end = event.start, event.end
        if event.details.kind == EventKind.WAKE:
            # A wake-up inside a cancellation does not happen
            if not any(c_start <= start < c_end for c_start, c_end in cancelled):
                result.append(event)
            continue
        dropped = False
        for c_start, c_end in cancelled:
            if c_start <= start and end <= c_end:
                dropped = True
                break
            if c_start <= start < c_end:
                # Starts inside: starts when the cancellation ends
                start = c_end
            elif start < c_start < end <= c_end:
                # Ends inside: ends when the cancellation starts
                end = c_start
        if dropped or end <= start:
            continue
        result.append(
            PlanEvent(event.uid, event.summary, start, end, event.details)
        )
    return result, cancelled


def calculate(
    people: Iterable[PersonInput], tz: Any
) -> Plan:
    """Work out departures and the wake-up of each person for each day."""
    people = list(people)
    plan = Plan()
    manual_wakes: list[WakeUp] = []

    for person in people:
        events, _ = _apply_cancellations(person.events, tz)
        for event in events:
            details = event.details
            if details.kind == EventKind.WAKE:
                manual_wakes.append(
                    WakeUp(at=event.start, person=person.entity_id, summary=event.summary)
                )
                continue
            if details.trip == TripKind.NONE or details.travel_minutes is None:
                continue
            travel = timedelta(minutes=details.travel_minutes)
            riders = details.riders or (person.entity_id,)
            ends: list[tuple[TripKind, datetime]] = []
            if details.trip in (TripKind.DROP, TripKind.BOTH):
                ends.append((TripKind.DROP, event.start))
            if details.trip in (TripKind.PICKUP, TripKind.BOTH):
                ends.append((TripKind.PICKUP, event.end))
            for kind, arrive in ends:
                plan.departures.append(
                    Departure(
                        at=arrive - travel,
                        arrive=arrive,
                        trip=kind,
                        summary=event.summary,
                        owner=person.entity_id,
                        riders=tuple(riders),
                        driver=details.driver,
                        car=details.car,
                        travel_minutes=details.travel_minutes,
                        uid=event.uid,
                    )
                )
    plan.departures.sort(key=lambda d: d.at)

    # Wake-ups created by trips: departure − getting ready, for each rider
    by_id: Mapping[str, PersonInput] = {p.entity_id: p for p in people}
    candidates = list(manual_wakes)
    for departure in plan.departures:
        for rider in departure.riders:
            person = by_id.get(rider)
            if person is None or not person.wake_for_trips:
                continue
            candidates.append(
                WakeUp(
                    at=departure.at - timedelta(minutes=person.getting_ready),
                    person=rider,
                    departure=departure,
                )
            )

    # A person's wake-up of the day is the earliest one
    earliest: dict[tuple[str, date], WakeUp] = {}
    for wake in candidates:
        key = (wake.person, wake.at.astimezone(tz).date())
        if key not in earliest or wake.at < earliest[key].at:
            earliest[key] = wake
    plan.wake_ups = sorted(earliest.values(), key=lambda w: w.at)
    return plan


def first_departures_by_car(plan: Plan, tz: Any) -> dict[str, list[Departure]]:
    """Return, for each car, its first departure of each day, ordered by time."""
    firsts: dict[tuple[str, date], Departure] = {}
    for departure in plan.departures:
        if departure.car is None:
            continue
        key = (departure.car, departure.at.astimezone(tz).date())
        if key not in firsts or departure.at < firsts[key].at:
            firsts[key] = departure
    result: dict[str, list[Departure]] = {}
    for (car, _), departure in sorted(firsts.items(), key=lambda kv: kv[1].at):
        result.setdefault(car, []).append(departure)
    return result
