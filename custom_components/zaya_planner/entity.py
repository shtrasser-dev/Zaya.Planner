"""Base entity of Zaya Planner."""

from __future__ import annotations

from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_SETTINGS_UPDATED, SIGNAL_TASKS_UPDATED
from .household import Car, Person
from .store import TaskManager


class ZayaPlannerEntity(Entity):
    """An entity that shows the task list and updates when it changes."""

    _attr_has_entity_name = True
    # Platform of the entity, used for a stable entity id
    _entity_platform: str
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, manager: TaskManager, key: str) -> None:
        """Initialize the entity."""
        self.entry = entry
        self.manager = manager
        self._entry_id = entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        # Same entity id whatever the language of Home Assistant
        self.entity_id = f"{self._entity_platform}.{DOMAIN}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Zaya Planner",
            entry_type=DeviceEntryType.SERVICE,
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Follow changes of the task list."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_TASKS_UPDATED}_{self._entry_id}",
                self._async_tasks_updated,
            )
        )

    @callback
    def _async_tasks_updated(self) -> None:
        self.async_write_ha_state()


def person_device(entry: ConfigEntry, person: Person) -> DeviceInfo:
    """Return the device of a person: “Zaya: Masha”."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_person_{person.key}")},
        name=f"Zaya: {person.name}",
        entry_type=DeviceEntryType.SERVICE,
    )


def car_device(entry: ConfigEntry, car: Car) -> DeviceInfo:
    """Return the device of a car: “Zaya: Leaf”."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_car_{car.car_id}")},
        name=f"Zaya: {car.name}",
        entry_type=DeviceEntryType.SERVICE,
    )


class HouseholdEntity(Entity):
    """An entity of a person or a car; follows changes of their settings."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Platform of the entity, used for a stable entity id
    _entity_platform: str

    def __init__(
        self,
        entry: ConfigEntry,
        *,
        key: str,
        person: Person | None = None,
        car: Car | None = None,
    ) -> None:
        """Initialize the entity of `person` or `car`."""
        self.entry = entry
        self._entry_id = entry.entry_id
        self._attr_translation_key = key
        if person is not None:
            owner, slug = f"person_{person.key}", person.slug
            self._attr_device_info = person_device(entry, person)
        else:
            assert car is not None
            owner, slug = f"car_{car.car_id}", car.slug
            self._attr_device_info = car_device(entry, car)
        self._attr_unique_id = f"{entry.entry_id}_{owner}_{key}"
        # Same entity id whatever the language: number.zaya_masha_getting_ready
        self.entity_id = f"{self._entity_platform}.zaya_{slug}_{key}"

    @override
    async def async_added_to_hass(self) -> None:
        """Follow changes of the settings."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_SETTINGS_UPDATED}_{self._entry_id}",
                self._async_settings_updated,
            )
        )

    @callback
    def _async_settings_updated(self) -> None:
        self.async_write_ha_state()
