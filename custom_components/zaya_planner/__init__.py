"""Zaya Planner: plan and reliably run smart home tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import (
    CLEANUP_INTERVAL,
    CONF_RETENTION_DAYS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)
from .executor import Executor
from .house import HousePlanner
from .household import Car, HouseholdSettings, Person, cars_from_entry, people_from_entry
from .services import async_setup_services
from .store import TaskManager

if TYPE_CHECKING:
    from .person_calendar import PersonCalendar

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CALENDAR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


@dataclass(slots=True)
class ZayaPlannerData:
    """What a loaded Zaya Planner keeps."""

    manager: TaskManager
    executor: Executor
    settings: HouseholdSettings
    people: list[Person]
    cars: list[Car]
    house: HousePlanner
    # Person calendars by person entity id (filled by the calendar platform)
    calendars: dict[str, PersonCalendar] = field(default_factory=dict)


type ZayaPlannerConfigEntry = ConfigEntry[ZayaPlannerData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Zaya Planner actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZayaPlannerConfigEntry) -> bool:
    """Set up Zaya Planner from a config entry."""
    manager = TaskManager(hass, entry.entry_id)
    await manager.async_load()
    executor = Executor(hass, entry, manager)
    settings = HouseholdSettings(hass, entry.entry_id)
    await settings.async_load()
    entry.runtime_data = ZayaPlannerData(
        manager=manager,
        executor=executor,
        settings=settings,
        people=people_from_entry(hass, entry),
        cars=cars_from_entry(entry),
        house=HousePlanner(hass, entry),
    )
    _async_remove_stale_devices(hass, entry)

    retention = timedelta(
        days=entry.options.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
    )
    await manager.async_cleanup(retention)

    async def _async_cleanup(_now: object) -> None:
        await manager.async_cleanup(retention)

    entry.async_on_unload(
        async_track_time_interval(hass, _async_cleanup, CLEANUP_INTERVAL)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Calendars are loaded by now: work out the house plan and keep it fresh
    entry.runtime_data.house.async_start()

    # Run tasks once Home Assistant has started: scripts are loaded by then
    entry.async_on_unload(async_at_started(hass, executor.async_start))
    # New settings, people or cars: set up again
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    hass.config_entries.async_schedule_reload(entry.entry_id)


def _async_remove_stale_devices(hass: HomeAssistant, entry: ZayaPlannerConfigEntry) -> None:
    """Remove devices of people and cars that are no longer set up.

    Their calendar files stay on disk, so adding a person back restores them.
    """
    data = entry.runtime_data
    wanted = {(DOMAIN, entry.entry_id)}
    wanted |= {(DOMAIN, f"{entry.entry_id}_person_{p.key}") for p in data.people}
    wanted |= {(DOMAIN, f"{entry.entry_id}_car_{c.car_id}") for c in data.cars}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & wanted:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ZayaPlannerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring an entry saved by an older version up to date."""
    if entry.version == 1 and entry.minor_version < 2:
        # 1.2: rules are automations now, not subentries of the integration
        for subentry_id, subentry in list(entry.subentries.items()):
            if subentry.subentry_type == "rule":
                hass.config_entries.async_remove_subentry(entry, subentry_id)
        hass.config_entries.async_update_entry(entry, minor_version=2)
        _LOGGER.info("Rules set up in the integration were removed: use automations")
    return True
