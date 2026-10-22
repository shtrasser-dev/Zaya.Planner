"""People and cars of the household, and their settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .const import (
    CONF_CAR_NAME,
    CONF_CHARGER,
    CONF_PEOPLE,
    DEFAULT_ALARM_TIME,
    DEFAULT_CHARGE_MARGIN,
    DEFAULT_GETTING_READY,
    SETTINGS_STORAGE_KEY,
    SIGNAL_SETTINGS_UPDATED,
    STORAGE_VERSION,
    SUBENTRY_CAR,
)


@dataclass(slots=True, frozen=True)
class Person:
    """A person taking part in the schedule (a Home Assistant person)."""

    entity_id: str
    # Stable key: the person's unique id (survives renaming the entity)
    key: str
    name: str

    @property
    def slug(self) -> str:
        """Return the part used in entity ids, e.g. "masha"."""
        return split_entity_id(self.entity_id)[1]


@dataclass(slots=True, frozen=True)
class Car:
    """A car (a config subentry)."""

    car_id: str
    name: str
    charger: str | None

    @property
    def slug(self) -> str:
        """Return the part used in entity ids, e.g. "leaf"."""
        return slugify(self.name) or self.car_id.lower()


def people_from_entry(hass: HomeAssistant, entry: ConfigEntry) -> list[Person]:
    """Return the people chosen in the settings, in that order."""
    registry = er.async_get(hass)
    people = []
    for entity_id in entry.options.get(CONF_PEOPLE, []):
        reg_entry = registry.async_get(entity_id)
        state = hass.states.get(entity_id)
        name = (
            (state.name if state else None)
            or ((reg_entry.name or reg_entry.original_name) if reg_entry else None)
            or split_entity_id(entity_id)[1]
        )
        key = (reg_entry.unique_id if reg_entry else None) or split_entity_id(entity_id)[1]
        people.append(Person(entity_id=entity_id, key=str(key), name=name))
    return people


def cars_from_entry(entry: ConfigEntry) -> list[Car]:
    """Return the cars set up in the integration, ordered by name."""
    cars = [
        Car(
            car_id=sub.subentry_id,
            name=sub.data.get(CONF_CAR_NAME) or sub.title,
            charger=sub.data.get(CONF_CHARGER) or None,
        )
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_CAR
    ]
    return sorted(cars, key=lambda car: car.name.lower())


PERSON_DEFAULTS: dict[str, Any] = {
    "getting_ready": DEFAULT_GETTING_READY,
    "wake_for_trips": True,
    # car_id of the car this person drives by default, or None
    "car": None,
    # entity_id of the person who usually drives them (children), or None
    "usually_drives": None,
    # notify service of an Android phone (mobile_app_…) that gets the alarm, or None
    "alarm_notify": None,
    # when tomorrow's alarm is set
    "alarm_time": DEFAULT_ALARM_TIME,
}

CAR_DEFAULTS: dict[str, Any] = {
    "charge_margin": DEFAULT_CHARGE_MARGIN,
}


class HouseholdSettings:
    """Settings of people and cars, saved to disk right away."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the settings."""
        self.hass = hass
        self._entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, SETTINGS_STORAGE_KEY, atomic_writes=True
        )
        self._data: dict[str, dict[str, dict[str, Any]]] = {
            "people": {},
            "cars": {},
            # Alarms set on phones: {person key: {date, time, warned}}
            "alarms": {},
        }

    async def async_load(self) -> None:
        """Load the settings."""
        data = await self._store.async_load() or {}
        self._data = {
            "people": dict(data.get("people", {})),
            "cars": dict(data.get("cars", {})),
            "alarms": dict(data.get("alarms", {})),
        }

    def person(self, person: Person) -> dict[str, Any]:
        """Return the settings of a person (defaults filled in)."""
        return {**PERSON_DEFAULTS, **self._data["people"].get(person.key, {})}

    def car(self, car: Car) -> dict[str, Any]:
        """Return the settings of a car (defaults filled in)."""
        return {**CAR_DEFAULTS, **self._data["cars"].get(car.car_id, {})}

    def alarm(self, person: Person) -> dict[str, Any]:
        """Return what alarm was set on the person's phone."""
        return dict(self._data["alarms"].get(person.key, {}))

    async def async_set_alarm(self, person: Person, record: dict[str, Any]) -> None:
        """Remember the alarm set on the person's phone."""
        self._data["alarms"][person.key] = record
        await self._store.async_save(self._data)

    async def async_set_person(self, person: Person, field: str, value: Any) -> None:
        """Change a setting of a person."""
        await self._async_set("people", person.key, field, value)

    async def async_set_car(self, car: Car, field: str, value: Any) -> None:
        """Change a setting of a car."""
        await self._async_set("cars", car.car_id, field, value)

    async def _async_set(self, group: str, key: str, field: str, value: Any) -> None:
        self._data[group].setdefault(key, {})[field] = value
        await self._store.async_save(self._data)
        async_dispatcher_send(self.hass, f"{SIGNAL_SETTINGS_UPDATED}_{self._entry_id}")
