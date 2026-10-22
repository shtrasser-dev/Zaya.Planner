"""Select settings: a person's car, and who usually drives them."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZayaPlannerConfigEntry
from .entity import HouseholdEntity
from .household import Person

PARALLEL_UPDATES = 0

# Shown when nothing is chosen; translated as the “none” state
NONE_OPTION = "none"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select settings."""
    people = entry.runtime_data.people
    async_add_entities(
        [
            entity
            for person in people
            for entity in (
                PersonCarSelect(entry, person),
                UsuallyDrivesSelect(entry, person),
                AlarmPhoneSelect(entry, person),
            )
        ]
    )


class PersonCarSelect(HouseholdEntity, SelectEntity):
    """The car this person drives (their trips use it by default)."""

    _entity_platform = "select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ZayaPlannerConfigEntry, person: Person) -> None:
        """Initialize the setting."""
        super().__init__(entry, person=person, key="car")
        self.person = person

    @property
    @override
    def options(self) -> list[str]:
        """Return “none” and the names of the cars."""
        return [NONE_OPTION, *(car.name for car in self.entry.runtime_data.cars)]

    @property
    @override
    def current_option(self) -> str:
        """Return the name of the chosen car."""
        car_id = self.entry.runtime_data.settings.person(self.person)["car"]
        for car in self.entry.runtime_data.cars:
            if car.car_id == car_id:
                return car.name
        return NONE_OPTION

    @override
    async def async_select_option(self, option: str) -> None:
        """Choose the car."""
        car_id = next(
            (car.car_id for car in self.entry.runtime_data.cars if car.name == option),
            None,
        )
        await self.entry.runtime_data.settings.async_set_person(self.person, "car", car_id)


class UsuallyDrivesSelect(HouseholdEntity, SelectEntity):
    """Who is added to this person's trips by default (for children)."""

    _entity_platform = "select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ZayaPlannerConfigEntry, person: Person) -> None:
        """Initialize the setting."""
        super().__init__(entry, person=person, key="usually_drives")
        self.person = person

    def _others(self) -> list[Person]:
        return [p for p in self.entry.runtime_data.people if p.key != self.person.key]

    @property
    @override
    def options(self) -> list[str]:
        """Return “none” and the other people."""
        return [NONE_OPTION, *(person.name for person in self._others())]

    @property
    @override
    def current_option(self) -> str:
        """Return the name of the person who usually drives."""
        entity_id = self.entry.runtime_data.settings.person(self.person)["usually_drives"]
        for person in self._others():
            if person.entity_id == entity_id:
                return person.name
        return NONE_OPTION

    @override
    async def async_select_option(self, option: str) -> None:
        """Choose who usually drives."""
        entity_id = next(
            (p.entity_id for p in self._others() if p.name == option), None
        )
        await self.entry.runtime_data.settings.async_set_person(
            self.person, "usually_drives", entity_id
        )


class AlarmPhoneSelect(HouseholdEntity, SelectEntity):
    """Android phone that gets the alarm for the person's wake-up."""

    _entity_platform = "select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ZayaPlannerConfigEntry, person: Person) -> None:
        """Initialize the setting."""
        super().__init__(entry, person=person, key="alarm_phone")
        self.person = person

    def _phones(self) -> list[str]:
        """Return notify actions of phones with the Home Assistant app."""
        services = self.hass.services.async_services_for_domain("notify")
        return sorted(name for name in services if name.startswith("mobile_app_"))

    @property
    @override
    def options(self) -> list[str]:
        """Return “none” and the phones."""
        current = self.entry.runtime_data.settings.person(self.person)["alarm_notify"]
        phones = self._phones()
        if current and current not in phones:
            # A phone that is off right now stays selectable
            phones.append(current)
        return [NONE_OPTION, *phones]

    @property
    @override
    def current_option(self) -> str:
        """Return the chosen phone."""
        return self.entry.runtime_data.settings.person(self.person)["alarm_notify"] or NONE_OPTION

    @override
    async def async_select_option(self, option: str) -> None:
        """Choose the phone."""
        await self.entry.runtime_data.settings.async_set_person(
            self.person, "alarm_notify", None if option == NONE_OPTION else option
        )
