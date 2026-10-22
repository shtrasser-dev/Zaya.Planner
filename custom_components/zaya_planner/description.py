"""Tags and attributes in event descriptions.

    #leaf #отвезти
    В пути: 00:40:00
    Take the sports kit

- `#word` anywhere is a tag; a line `Key: value` is an attribute when its key is
  in the user's dictionary; everything else is notes.
- A filter is written the same way: `#tag` (the tag is there), `Key: value`
  (the attribute has this value), `Key` (the attribute is there).
- Words are compared without letter case, with “ё” as “е”.

The integration knows no words of its own: tags and keys come from the user's
dictionary (the options). Pure functions, no Home Assistant objects.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
import re
from typing import Protocol, TypeVar

# A tag starts with a letter; letters, digits, “_” and “-” follow. It is not
# part of a word (“a#b”) or of another tag (“##b”).
_TAG = re.compile(r"(?<![\w#])#([^\W\d_][\w-]*)")


def norm(text: str) -> str:
    """Return a word for comparing: lower case, “ё” as “е”, single spaces."""
    return " ".join(text.casefold().replace("ё", "е").split())


def tags(text: str | None) -> list[str]:
    """Return the tags of a text as written, each once, in order."""
    found: dict[str, str] = {}
    for match in _TAG.finditer(text or ""):
        found.setdefault(norm(match.group(1)), match.group(1))
    return list(found.values())


def lines(text: str | None) -> list[tuple[str, str]]:
    """Return (key, value) of the “Key: value” lines, stripped, as written."""
    pairs = []
    for line in (text or "").splitlines():
        name, colon, value = line.partition(":")
        if colon and name.strip() and not name.strip().startswith("#"):
            pairs.append((name.strip(), value.strip()))
    return pairs


def build(fields: Iterable[tuple[str, object]]) -> str:
    """Return “Key: value” lines; empty values are left out, lists joined with commas."""
    result = []
    for name, value in fields:
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        if value is None or value == "":
            continue
        result.append(f"{name}: {value}")
    return "\n".join(result)


# ---------------------------------------------------------------- dictionary


@dataclass(slots=True, frozen=True)
class Problem:
    """Something in a description that is not understood."""

    # unknown_tag: a tag not in the dictionary; empty_value: a key with no value
    code: str
    value: str


@dataclass(slots=True, frozen=True)
class Reading:
    """What a description says, in the dictionary's spelling."""

    tags: tuple[str, ...] = ()
    attributes: dict[str, str] = field(default_factory=dict)
    problems: tuple[Problem, ...] = ()


class Dictionary:
    """The user's tags and keys."""

    def __init__(self, tag_words: Iterable[str] = (), key_words: Iterable[str] = ()) -> None:
        """Keep the words, spelled as the user wrote them in the options."""
        words = [word.strip().lstrip("#") for word in tag_words]
        self._tags = {norm(word): word for word in words if word}
        self._keys = {norm(word): word.strip() for word in key_words if word.strip()}

    @property
    def tags(self) -> list[str]:
        """Return the tags."""
        return list(self._tags.values())

    @property
    def keys(self) -> list[str]:
        """Return the keys."""
        return list(self._keys.values())

    def tag(self, word: str) -> str | None:
        """Return a tag as spelled in the dictionary, or None."""
        return self._tags.get(norm(word.lstrip("#")))

    def key(self, word: str) -> str | None:
        """Return a key as spelled in the dictionary, or None."""
        return self._keys.get(norm(word))

    def read(self, text: str | None) -> Reading:
        """Return the tags and attributes of a description and what is wrong in it.

        Lines whose key is not in the dictionary are notes. When a key is
        repeated, the first line counts.
        """
        found_tags: list[str] = []
        problems: list[Problem] = []
        for word in tags(text):
            if (tag := self.tag(word)) is None:
                problems.append(Problem("unknown_tag", word))
            elif tag not in found_tags:
                found_tags.append(tag)
        attributes: dict[str, str] = {}
        for name, value in lines(text):
            if (key := self.key(name)) is None or key in attributes:
                continue
            if not value:
                problems.append(Problem("empty_value", key))
                continue
            attributes[key] = value
        return Reading(tuple(found_tags), attributes, tuple(problems))


# ---------------------------------------------------------------- filters


@dataclass(slots=True, frozen=True)
class Filter:
    """Which events to pick; all conditions must hold."""

    tags: tuple[str, ...] = ()
    # key → value the attribute must have
    equals: tuple[tuple[str, str], ...] = ()
    # keys the event must have, any value
    present: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str | None) -> Filter:
        """Read a filter: `#tag`, `Key: value`, `Key`, one condition per line (tags may share one)."""
        filter_tags: list[str] = []
        equals: list[tuple[str, str]] = []
        present: list[str] = []
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                filter_tags.extend(tags(line))
                continue
            name, colon, value = line.partition(":")
            if colon and value.strip():
                equals.append((name.strip(), value.strip()))
            else:
                present.append(name.strip())
        return cls(tuple(filter_tags), tuple(equals), tuple(present))

    def unknown_words(self, dictionary: Dictionary) -> list[str]:
        """Return the tags (with “#”) and keys of the filter not in the dictionary."""
        unknown = [f"#{tag}" for tag in self.tags if dictionary.tag(tag) is None]
        keys = [key for key, _ in self.equals] + list(self.present)
        return unknown + [key for key in keys if dictionary.key(key) is None]

    def matches(self, reading: Reading) -> bool:
        """Return True if a description read with the dictionary fits."""
        have_tags = {norm(tag) for tag in reading.tags}
        attributes = {norm(key): value for key, value in reading.attributes.items()}
        return (
            all(norm(tag) in have_tags for tag in self.tags)
            and all(norm(key) in attributes for key in self.present)
            and all(
                norm(key) in attributes and norm(attributes[norm(key)]) == norm(value)
                for key, value in self.equals
            )
        )


class DatedEvent(Protocol):
    """An event with a local start: a date (all day) or a date and time."""

    @property
    def start(self) -> date | datetime: ...


E = TypeVar("E", bound=DatedEvent)


def day_of(start: date | datetime) -> date:
    """Return the day an event starts."""
    return start.date() if isinstance(start, datetime) else start


def by_start(event: DatedEvent) -> tuple[date, time]:
    """Order of events: by day, all-day events first, then by start."""
    start = event.start
    return (day_of(start), start.time() if isinstance(start, datetime) else time.min)


def first_of_each_day(events: Iterable[E]) -> list[E]:
    """Return the first event of each day, by start."""
    first: dict[date, E] = {}
    for event in sorted(events, key=by_start):
        first.setdefault(day_of(event.start), event)
    return list(first.values())

