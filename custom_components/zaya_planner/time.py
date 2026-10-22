"""Time settings: when tomorrow's alarm is set on a person's phone."""

from __future__ import annotations

from datetime import time
from typing import override

from homeassistant.components.time import TimeEntity
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
    """Set up the time settings."""
    async_add_entities(
        [AlarmTimeEntity(entry, person) for person in entry.runtime_data.people]
    )


class AlarmTimeEntity(HouseholdEntity, TimeEntity):
    """When the alarm for tomorrow's wake-up is set on the phone."""

    _entity_platform = "time"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ZayaPlannerConfigEntry, person: Person) -> None:
        """Initialize the setting."""
        super().__init__(entry, person=person, key="alarm_time")
        self.person = person

    @property
    @override
    def native_value(self) -> time:
        """Return the time."""
        value = self.entry.runtime_data.settings.person(self.person)["alarm_time"]
        return time.fromisoformat(value)

    @override
    async def async_set_value(self, value: time) -> None:
        """Change the time."""
        await self.entry.runtime_data.settings.async_set_person(
            self.person, "alarm_time", value.replace(second=0, microsecond=0).isoformat()
        )
