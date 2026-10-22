"""Number settings: getting ready time of a person, charging margin of a car."""

from __future__ import annotations

from typing import override

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZayaPlannerConfigEntry
from .entity import HouseholdEntity
from .household import Car, Person

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number settings."""
    data = entry.runtime_data
    async_add_entities(
        [GettingReadyNumber(entry, person) for person in data.people]
    )
    for car in data.cars:
        async_add_entities(
            [ChargeMarginNumber(entry, car)], config_subentry_id=car.car_id
        )


class GettingReadyNumber(HouseholdEntity, NumberEntity):
    """How long before their departure a person gets up."""

    _entity_platform = "number"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 240
    _attr_native_step = 5
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, entry: ZayaPlannerConfigEntry, person: Person) -> None:
        """Initialize the setting."""
        super().__init__(entry, person=person, key="getting_ready")
        self.person = person

    @property
    @override
    def native_value(self) -> float:
        """Return the minutes."""
        return self.entry.runtime_data.settings.person(self.person)["getting_ready"]

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Change the minutes."""
        await self.entry.runtime_data.settings.async_set_person(
            self.person, "getting_ready", int(value)
        )


class ChargeMarginNumber(HouseholdEntity, NumberEntity):
    """How long before the departure the car must be charged."""

    _entity_platform = "number"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 240
    _attr_native_step = 5
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, entry: ZayaPlannerConfigEntry, car: Car) -> None:
        """Initialize the setting."""
        super().__init__(entry, car=car, key="charge_margin")
        self.car = car

    @property
    @override
    def native_value(self) -> float:
        """Return the minutes."""
        return self.entry.runtime_data.settings.car(self.car)["charge_margin"]

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Change the minutes."""
        await self.entry.runtime_data.settings.async_set_car(
            self.car, "charge_margin", int(value)
        )
