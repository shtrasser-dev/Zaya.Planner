"""Tests of “Key: value” lines in event descriptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from custom_components.zaya_planner.const import EventKind, TripKind
from custom_components.zaya_planner.description import (
    Problem,
    attributes,
    build,
    build_details,
    find,
    first_of_day,
    matches,
    parse_details,
    travel_minutes,
)
from custom_components.zaya_planner.details import EventDetails

PEOPLE = {"Папа": "person.dad", "Мама": "person.mum", "Маша": "person.masha"}
CARS = {"Leaf": "car_leaf", "Чери": "car_chery"}
NAMES = {entity_id: name for name, entity_id in PEOPLE.items()}
CAR_NAMES = {car_id: name for name, car_id in CARS.items()}


def parse(text: str) -> tuple[EventDetails, list[Problem]]:
    return parse_details(text, PEOPLE, CARS)


# ------------------------------------------------------------ generic lines


def test_attributes() -> None:
    """Known keys by id in any language and case; other keys as written; notes ignored."""
    text = "Взять форму\nпоездка : Отвезти\nMachine:  x \nCar: Leaf\nАдрес: Ленина 5\nCar: Чери"
    assert attributes(text) == {
        "trip": "Отвезти",
        "machine": "x",
        "car": "Leaf",
        "адрес": "Ленина 5",
    }
    assert attributes(None) == {}
    assert attributes("За рулем: Папа") == {"driver": "Папа"}


def test_build() -> None:
    """Empty values are left out, lists are joined."""
    assert build(
        [("Статус", "выполнена"), ("Ошибка", None), ("Устройства", ["Лампа", "Пол"]), ("Попыток", 2)]
    ) == "Статус: выполнена\nУстройства: Лампа, Пол\nПопыток: 2"


@pytest.mark.parametrize(
    ("value", "minutes"),
    [
        ("40", 40),
        ("40 мин", 40),
        ("40мин.", 40),
        ("40 минут", 40),
        ("40 min", 40),
        ("1:10", 70),
        ("1 ч 10 мин", 70),
        ("1 час", 60),
        ("1h10m", 70),
        ("сорок", None),
        ("0", None),
        ("25:00", None),
        ("", None),
    ],
)
def test_travel_minutes(value: str, minutes: int | None) -> None:
    """Travel time is written in a few usual ways."""
    assert travel_minutes(value) == minutes


# ----------------------------------------------------------- planner fields


def test_school_trip() -> None:
    """The usual trip: only what is not a default is written."""
    details, problems = parse("Поездка: отвезти\nВ пути: 40\nВзять сменку")
    assert problems == []
    assert details == EventDetails(trip=TripKind.DROP, travel_minutes=40)


def test_everything_written() -> None:
    """Riders, driver and car by name, in any case; English keys work too."""
    details, problems = parse(
        "Trip: take there and pick up\nRiders: маша и Мама\nDriver: мама\nCar: чери\nTravel: 1:10"
    )
    assert problems == []
    assert details == EventDetails(
        trip=TripKind.BOTH,
        riders=("person.masha", "person.mum"),
        driver="person.mum",
        car="car_chery",
        travel_minutes=70,
    )


@pytest.mark.parametrize(
    ("text", "kind"),
    [("Тип: подъём", EventKind.WAKE), ("тип: подъем", EventKind.WAKE), ("Kind: cancel", EventKind.CANCEL)],
)
def test_kinds(text: str, kind: EventKind) -> None:
    """Wake-ups and cancellations."""
    assert parse(text) == (EventDetails(kind=kind), [])


def test_no_fields() -> None:
    """An event without known keys is an ordinary event, not an error."""
    assert parse("Кабинет: 12\nВзять паспорт") == (EventDetails(), [])
    assert parse("") == (EventDetails(), [])


def test_problems() -> None:
    """A known key that cannot be understood is reported and left empty."""
    details, problems = parse(
        "Поездка: отвезти\nВ пути: сорок\nМашина: Лиф\nЕдут: Маша, Петя\nТип: обед\nПоездка: забрать"
    )
    assert details == EventDetails(trip=TripKind.DROP, riders=("person.masha",))
    assert problems == [
        Problem("travel", "not_minutes", "сорок"),
        Problem("car", "unknown_car", "Лиф"),
        Problem("riders", "unknown_person", "Петя"),
        Problem("kind", "unknown_value", "обед"),
        Problem("trip", "repeated", "забрать"),
    ]


def test_trip_needs_travel_time() -> None:
    """Without a travel time there is no departure to work out."""
    assert parse("Поездка: забрать")[1] == [Problem("travel", "no_travel")]


def test_driver_must_ride() -> None:
    """A driver who is not among the riders written."""
    _, problems = parse("Поездка: отвезти\nВ пути: 30\nЕдут: Маша\nЗа рулём: Папа")
    assert problems == [Problem("driver", "driver_not_rider", "Папа")]
    # Without riders written the defaults are filled in later
    assert parse("Поездка: отвезти\nВ пути: 30\nЗа рулём: Папа")[1] == []


@pytest.mark.parametrize("language", ["ru", "en", "de"])
def test_build_and_parse_back(language: str) -> None:
    """What is written can be read back, in any language."""
    details = EventDetails(
        kind=EventKind.EVENT,
        trip=TripKind.PICKUP,
        riders=("person.masha", "person.dad"),
        driver="person.dad",
        car="car_leaf",
        travel_minutes=40,
    )
    text = build_details(details, language, NAMES, CAR_NAMES)
    assert parse(text) == (details, [])


def test_build_details_in_russian() -> None:
    """The lines a person would write."""
    details = EventDetails(
        trip=TripKind.DROP, riders=("person.masha",), car="car_leaf", travel_minutes=40
    )
    assert build_details(details, "ru", NAMES, CAR_NAMES) == (
        "Поездка: отвезти\nЕдут: Маша\nМашина: Leaf\nВ пути: 40 мин"
    )


# ------------------------------------------------------------ finding events


@dataclass
class Event:
    summary: str
    start: date | datetime
    description: str | None = None


EVENTS = [
    Event("Бассейн", datetime(2026, 10, 26, 17, 0), "Поездка: отвезти\nМашина: Leaf\nВ пути: 30"),
    Event("Школа", datetime(2026, 10, 26, 8, 0), "Поездка: отвезти и забрать\nМашина: Leaf\nВ пути: 40"),
    Event("Мороз", date(2026, 10, 27), "Тип: отмена"),
    Event("Врач", datetime(2026, 10, 26, 7, 0), "Кабинет: 12"),
    Event("Школа", datetime(2026, 10, 27, 8, 0), "Поездка: отвезти\nМашина: Чери\nВ пути: 40"),
]


def test_matches() -> None:
    """Keys in any language; values compared by meaning; None asks only for the key."""
    text = "Поездка: Отвезти\nМашина: Leaf\nВ пути: 40 мин"
    assert matches(text, {"car": "leaf", "Trip": "take there", "в пути": "40"})
    assert matches(text, {"Поездка": None})
    assert not matches(text, {"Поездка": "забрать"})
    assert not matches(text, {"За рулём": None})
    assert matches(text, {})


def test_find() -> None:
    """All events with a trip on the Leaf, by start."""
    found = find(EVENTS, {"Машина": "Leaf", "Поездка": None})
    assert [(e.summary, e.start.hour) for e in found] == [("Школа", 8), ("Бассейн", 17)]


def test_first_of_day() -> None:
    """The first trip of a day; all-day events come first."""
    assert first_of_day(EVENTS, date(2026, 10, 26), {"Поездка": None}).summary == "Школа"
    assert first_of_day(EVENTS, date(2026, 10, 27), {}).summary == "Мороз"
    assert first_of_day(EVENTS, date(2026, 10, 27), {"car": "Leaf"}) is None
