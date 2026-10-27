"""Tests of tags and attributes in event descriptions (no Home Assistant)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from custom_components.zaya_planner.description import (
    Dictionary,
    Filter,
    Problem,
    Reading,
    first_of_each_day,
    lines,
    tags,
)

WORDS = Dictionary(["leaf", "Отвезти", "забрать", "подъём"], ["В пути", "Едут"])


def test_tags() -> None:
    """Tags anywhere, as written, each once; not inside words or after “##”."""
    text = "#leaf #Отвезти\nВзять форму #leaf, потом #забрать.\nmail a#b, ##x, #1st, #_x"
    assert tags(text) == ["leaf", "Отвезти", "забрать"]
    assert tags(None) == []


def test_lines() -> None:
    """“Key: value” lines as written; tag lines and lines without a colon are not."""
    text = "#leaf: x\nВ пути : 00:40:00\nВзять форму\nАдрес: Ленина 5\n: nothing"
    assert lines(text) == [("В пути", "00:40:00"), ("Адрес", "Ленина 5")]


def test_read() -> None:
    """Words in the dictionary's spelling; other keys are notes; the first key counts."""
    reading = WORDS.read(
        "#LEAF #отвезти\nв  пути: 00:40:00\nВ пути: 01:00:00\nАдрес: Ленина 5\nВзять форму"
    )
    assert reading == Reading(("leaf", "Отвезти"), {"В пути": "00:40:00"}, ())


def test_read_problems() -> None:
    """A tag not in the dictionary and a key with no value are reported."""
    reading = WORDS.read("#leaf #лиф\nЕдут:\nВ пути: сорок")
    assert reading.tags == ("leaf",)
    # The value is text: it is not checked here
    assert reading.attributes == {"В пути": "сорок"}
    assert reading.problems == (Problem("unknown_tag", "лиф"), Problem("empty_value", "Едут"))


def test_ё_and_case() -> None:
    """“ё” is “е”, letter case does not matter."""
    assert WORDS.read("#ПОДЪЕМ").tags == ("подъём",)
    assert Dictionary(["#Leaf", " "], [" Едут "]).tags == ["Leaf"]


def test_filter() -> None:
    """#tag: there; Key: value: equal; Key: there."""
    reading = WORDS.read("#leaf #отвезти\nВ пути: 00:40:00")
    assert Filter.parse("#leaf").matches(reading)
    assert Filter.parse("#Leaf #ОТВЕЗТИ\nв пути: 00:40:00").matches(reading)
    assert Filter.parse("В пути").matches(reading)
    assert Filter.parse("").matches(reading)
    assert not Filter.parse("#забрать").matches(reading)
    assert not Filter.parse("В пути: 01:00:00").matches(reading)
    assert not Filter.parse("Едут").matches(reading)
    assert not Filter.parse("Едут: Маша").matches(reading)


def test_filter_without_tags() -> None:
    """!#tag: the tag is not there."""
    reading = WORDS.read("#leaf #отвезти")
    assert Filter.parse("!#Забрать").matches(reading)
    assert Filter.parse("#leaf !#забрать").matches(reading)
    assert not Filter.parse("#leaf\n!#отвезти").matches(reading)
    assert Filter.parse("#leaf !#забрать") == Filter(tags=("leaf",), excluded=("забрать",))
    assert Filter.parse("!#лиф").unknown_words(WORDS) == ["#лиф"]


def test_filter_words() -> None:
    """Words of a filter that are not in the dictionary."""
    event_filter = Filter.parse("#leaf #лиф\nВ пути: 00:40:00\nВпути")
    assert event_filter == Filter(("leaf", "лиф"), (("В пути", "00:40:00"),), ("Впути",))
    assert event_filter.unknown_words(WORDS) == ["#лиф", "Впути"]


@dataclass
class Event:
    summary: str
    start: date | datetime


def test_first_of_each_day() -> None:
    """All-day events come first, then by start."""
    events = [
        Event("Pool", datetime(2026, 10, 26, 17, 0)),
        Event("School", datetime(2026, 10, 26, 8, 0)),
        Event("School", datetime(2026, 10, 27, 8, 0)),
        Event("Frost", date(2026, 10, 27)),
    ]
    assert [e.summary for e in first_of_each_day(events)] == ["School", "Frost"]

