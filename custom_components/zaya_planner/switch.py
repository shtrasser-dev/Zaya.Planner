"""Switch settings: whether trips create wake-ups for a person."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZayaPlannerConfigEntry
from .entity import HouseholdEntity
from .household import Person

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch settings."""
    async_add_entities(
        [WakeForTripsSwitch(entry, person) for person in entry.runtime_data.people]
    )


class WakeForTripsSwitch(HouseholdEntity, SwitchEntity):
    """Trips create wake-ups for this person."""

    _entity_platform = "switch"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ZayaPlannerConfigEntry, person: Person) -> None:
        """Initialize the setting."""
        super().__init__(entry, person=person, key="wake_for_trips")
        self.person = person

    @property
    @override
    def is_on(self) -> bool:
        """Return whether trips wake this person."""
        return self.entry.runtime_data.settings.person(self.person)["wake_for_trips"]

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Wake this person for trips."""
        await self.entry.runtime_data.settings.async_set_person(
            self.person, "wake_for_trips", True
        )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Do not wake this person for trips."""
        await self.entry.runtime_data.settings.async_set_person(
            self.person, "wake_for_trips", False
        )
