"""Event fields written as “Key: value” lines in an event description.

    Trip: take there
    Travel: 40
    Take the sports kit

Keys and values are recognised in Russian and English, in any letter case.
Lines without a colon, and lines with other keys, are notes and are ignored.

- `attributes` splits a description into fields, `build` writes fields back;
- `parse_details` reads the planner's fields and reports what it cannot understand;
- `build_details` writes them in the language of Home Assistant;
- `find` and `first_of_day` pick events by their fields.

Pure functions, no Home Assistant objects.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
import re
from typing import Protocol, TypeVar

from .const import EventKind, TripKind
from .details import EventDetails

KIND = "kind"
TRIP = "trip"
TRAVEL = "travel"
RIDERS = "riders"
DRIVER = "driver"
CAR = "car"

LANGUAGES = ("ru", "en")

# Key names by language; they are also used when writing
KEY_NAMES: dict[str, dict[str, str]] = {
    KIND: {"ru": "Тип", "en": "Kind"},
    TRIP: {"ru": "Поездка", "en": "Trip"},
    RIDERS: {"ru": "Едут", "en": "Riders"},
    DRIVER: {"ru": "За рулём", "en": "Driver"},
    CAR: {"ru": "Машина", "en": "Car"},
    TRAVEL: {"ru": "В пути", "en": "Travel"},
}

# Value names by language; the first one is used when writing
KIND_NAMES: dict[EventKind, dict[str, tuple[str, ...]]] = {
    EventKind.EVENT: {"ru": ("событие",), "en": ("event",)},
    EventKind.WAKE: {"ru": ("подъём",), "en": ("wake-up", "wake")},
    EventKind.CANCEL: {"ru": ("отмена",), "en": ("cancel", "cancellation")},
}
TRIP_NAMES: dict[TripKind, dict[str, tuple[str, ...]]] = {
    TripKind.NONE: {"ru": ("нет",), "en": ("none", "no")},
    TripKind.DROP: {"ru": ("отвезти",), "en": ("take there", "drop")},
    TripKind.PICKUP: {"ru": ("забрать",), "en": ("pick up", "pickup")},
    TripKind.BOTH: {
        "ru": ("отвезти и забрать",),
        "en": ("take there and pick up", "both"),
    },
}
MINUTES = {"ru": "мин", "en": "min"}

# Longest travel time that is believed
MAX_TRAVEL_MINUTES = 24 * 60

_RIDER_SEPARATOR = re.compile(r"\s*(?:[,;]|\s(?:и|and)\s)\s*", re.IGNORECASE)
_MIN = r"(?:мин\w*|min\w*|m)\.?"
_TRAVEL_FORMS = (
    # 40, 40 мин, 40 min
    re.compile(rf"^(?P<m>\d+)\s*(?:{_MIN})?$"),
    # 1:10
    re.compile(r"^(?P<h>\d+):(?P<m>\d{2})$"),
    # 1 ч, 1 ч 10 мин, 1 h 10 min
    re.compile(rf"^(?P<h>\d+)\s*(?:ч|час\w*|h|hours?|hrs?)\.?(?:\s*(?P<m>\d+)\s*(?:{_MIN})?)?$"),
)


def _norm(text: str) -> str:
    """Return text for comparing: lower case, “ё” as “е”, single spaces."""
    return " ".join(text.casefold().replace("ё", "е").split())


_KEY_IDS = {
    _norm(name): key for key, names in KEY_NAMES.items() for name in names.values()
}
_KINDS = {
    _norm(name): kind
    for kind, languages in KIND_NAMES.items()
    for names in languages.values()
    for name in names
}
_TRIPS = {
    _norm(name): trip
    for trip, languages in TRIP_NAMES.items()
    for names in languages.values()
    for name in names
}


def _language(language: str) -> str:
    return language if language in LANGUAGES else "en"


def key_id(name: str) -> str:
    """Return the id of a known key (in any language), or the name for comparing."""
    return _KEY_IDS.get(_norm(name), _norm(name))


def key_name(key: str, language: str) -> str:
    """Return the name of a known key in a language."""
    return KEY_NAMES[key][_language(language)]


def _lines(text: str | None) -> list[tuple[str, str]]:
    """Return (key id, value) of the “Key: value” lines."""
    pairs = []
    for line in (text or "").splitlines():
        name, colon, value = line.partition(":")
        if colon and name.strip():
            pairs.append((key_id(name), value.strip()))
    return pairs


def attributes(text: str | None) -> dict[str, str]:
    """Return the fields of a description.

    Known keys are given by their id (“car”), other keys in lower case as
    written. When a key is repeated the first line counts.
    """
    fields: dict[str, str] = {}
    for key, value in _lines(text):
        fields.setdefault(key, value)
    return fields


def build(fields: Iterable[tuple[str, object]]) -> str:
    """Return “Key: value” lines; empty values are left out, lists joined with commas."""
    lines = []
    for name, value in fields:
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        if value is None or value == "":
            continue
        lines.append(f"{name}: {value}")
    return "\n".join(lines)


# ---------------------------------------------------------------- planner fields


@dataclass(slots=True, frozen=True)
class Problem:
    """A line of the description that could not be understood."""

    key: str
    # unknown_value, not_minutes, unknown_person, unknown_car, driver_not_rider,
    # no_travel, repeated
    code: str
    value: str = ""


def travel_minutes(value: str) -> int | None:
    """Return minutes from “40”, “40 мин”, “1:10”, “1 ч 10 мин”, or None."""
    text = _norm(value)
    for form in _TRAVEL_FORMS:
        if match := form.match(text):
            parts = match.groupdict()
            minutes = int(parts.get("h") or 0) * 60 + int(parts.get("m") or 0)
            return minutes if 0 < minutes <= MAX_TRAVEL_MINUTES else None
    return None


def _names(names: Mapping[str, str]) -> dict[str, str]:
    return {_norm(name): value for name, value in names.items()}


def parse_details(
    text: str | None,
    people: Mapping[str, str],
    cars: Mapping[str, str],
) -> tuple[EventDetails, list[Problem]]:
    """Return the planner's fields of a description and the problems found.

    `people` maps a person's name to their entity id, `cars` a car's name to its
    id. A field that cannot be understood is left empty and reported.
    """
    people_by_name, cars_by_name = _names(people), _names(cars)
    problems: list[Problem] = []
    seen: set[str] = set()
    fields: dict[str, object] = {}

    for key, value in _lines(text):
        if key not in KEY_NAMES:
            continue
        if key in seen:
            problems.append(Problem(key, "repeated", value))
            continue
        seen.add(key)
        if key == KIND:
            if (kind := _KINDS.get(_norm(value))) is None:
                problems.append(Problem(key, "unknown_value", value))
            else:
                fields["kind"] = kind
        elif key == TRIP:
            if (trip := _TRIPS.get(_norm(value))) is None:
                problems.append(Problem(key, "unknown_value", value))
            else:
                fields["trip"] = trip
        elif key == TRAVEL:
            if (minutes := travel_minutes(value)) is None:
                problems.append(Problem(key, "not_minutes", value))
            else:
                fields["travel_minutes"] = minutes
        elif key == RIDERS:
            riders = []
            for name in filter(None, _RIDER_SEPARATOR.split(value.strip())):
                if (rider := people_by_name.get(_norm(name))) is None:
                    problems.append(Problem(key, "unknown_person", name))
                elif rider not in riders:
                    riders.append(rider)
            fields["riders"] = tuple(riders)
        elif key == DRIVER:
            if (driver := people_by_name.get(_norm(value))) is None:
                problems.append(Problem(key, "unknown_person", value))
            else:
                fields["driver"] = driver
        elif (car := cars_by_name.get(_norm(value))) is None:
            problems.append(Problem(key, "unknown_car", value))
        else:
            fields["car"] = car

    details = EventDetails(**fields)  # type: ignore[arg-type]
    if details.driver and RIDERS in seen and details.driver not in details.riders:
        problems.append(Problem(DRIVER, "driver_not_rider", _name_of(people, details.driver)))
    if (
        details.trip != TripKind.NONE
        and details.travel_minutes is None
        and not any(problem.key == TRAVEL for problem in problems)
    ):
        problems.append(Problem(TRAVEL, "no_travel"))
    return details, problems


def _name_of(names: Mapping[str, str], value: str) -> str:
    return next((name for name, item in names.items() if item == value), value)


def build_details(
    details: EventDetails,
    language: str,
    people: Mapping[str, str],
    cars: Mapping[str, str],
) -> str:
    """Return description lines for the planner's fields.

    `people` maps an entity id to a name, `cars` a car id to a name.
    """
    language = _language(language)
    fields: list[tuple[str, object]] = []
    if details.kind != EventKind.EVENT:
        fields.append((key_name(KIND, language), KIND_NAMES[details.kind][language][0]))
    if details.trip != TripKind.NONE:
        fields.append((key_name(TRIP, language), TRIP_NAMES[details.trip][language][0]))
    fields.append(
        (key_name(RIDERS, language), [people.get(rider, rider) for rider in details.riders])
    )
    if details.driver:
        fields.append((key_name(DRIVER, language), people.get(details.driver, details.driver)))
    if details.car:
        fields.append((key_name(CAR, language), cars.get(details.car, details.car)))
    if details.travel_minutes is not None:
        fields.append(
            (key_name(TRAVEL, language), f"{details.travel_minutes} {MINUTES[language]}")
        )
    return build(fields)


# ---------------------------------------------------------------- finding events


class DescribedEvent(Protocol):
    """An event as calendars return it: local start, optional description."""

    @property
    def start(self) -> date | datetime: ...

    @property
    def description(self) -> str | None: ...


E = TypeVar("E", bound=DescribedEvent)


def _comparable(key: str, value: str) -> str:
    """Return a value for comparing: known values by their id."""
    if key == KIND and (kind := _KINDS.get(_norm(value))) is not None:
        return str(kind)
    if key == TRIP and (trip := _TRIPS.get(_norm(value))) is not None:
        return str(trip)
    if key == TRAVEL and (minutes := travel_minutes(value)) is not None:
        return str(minutes)
    return _norm(value)


def matches(text: str | None, criteria: Mapping[str, str | None]) -> bool:
    """Return True if a description has all the fields asked for.

    Keys may be given in any language; a value of None asks only that the key
    is there.
    """
    fields = attributes(text)
    for name, wanted in criteria.items():
        key = key_id(name)
        if key not in fields:
            return False
        if wanted is not None and _comparable(key, fields[key]) != _comparable(key, wanted):
            return False
    return True


def _day(start: date | datetime) -> date:
    return start.date() if isinstance(start, datetime) else start


def _order(event: DescribedEvent) -> tuple[date, time]:
    """All-day events first, then by start."""
    start = event.start
    return (_day(start), start.time() if isinstance(start, datetime) else time.min)


def find(events: Iterable[E], criteria: Mapping[str, str | None]) -> list[E]:
    """Return the events whose descriptions match, by start."""
    return sorted(
        (event for event in events if matches(event.description, criteria)), key=_order
    )


def first_of_day(
    events: Iterable[E], day: date, criteria: Mapping[str, str | None]
) -> E | None:
    """Return the first event of a day whose description matches."""
    found: Sequence[E] = find((e for e in events if _day(e.start) == day), criteria)
    return found[0] if found else None
