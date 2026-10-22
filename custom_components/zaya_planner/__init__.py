"""Zaya Planner: plan and reliably run smart home tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import (
    CLEANUP_INTERVAL,
    CONF_RETENTION_DAYS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)
from .events import EventReader
from .executor import Executor
from .planning import TimeToPlan
from .services import async_setup_services
from .store import TaskManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

# Entities of the integration (by key); others left by older versions are removed
ENTITY_KEYS = ("schedule", "next_task", "errors", "tasks", "event_errors")


@dataclass(slots=True)
class ZayaPlannerData:
    """What a loaded Zaya Planner keeps."""

    manager: TaskManager
    executor: Executor
    events: EventReader
    time_to_plan: TimeToPlan


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
    entry.runtime_data = ZayaPlannerData(
        manager=manager,
        executor=executor,
        events=EventReader(hass, entry),
        time_to_plan=TimeToPlan(hass, entry),
    )

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

    # Once Home Assistant has started scripts, automations and calendars are
    # loaded: run tasks, read the chosen calendars, tell rules to plan
    entry.async_on_unload(async_at_started(hass, executor.async_start))
    entry.async_on_unload(async_at_started(hass, entry.runtime_data.events.async_start))
    entry.async_on_unload(async_at_started(hass, entry.runtime_data.time_to_plan.async_start))
    # New settings: set up again
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    hass.config_entries.async_schedule_reload(entry.entry_id)


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
    if entry.version == 1 and entry.minor_version < 3:
        await _async_remove_household(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=3)
        _LOGGER.info(
            "People, cars, their calendars and the house plan were removed: "
            "use calendars of your choice and tags"
        )
    return True


async def _async_remove_household(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """1.3: people, cars, person calendars and the house plan are gone."""
    for subentry_id, subentry in list(entry.subentries.items()):
        if subentry.subentry_type == "car":
            hass.config_entries.async_remove_subentry(entry, subentry_id)
    options = {key: value for key, value in entry.options.items() if key != "people"}
    hass.config_entries.async_update_entry(entry, options=options)

    # Devices of people and cars (their entities go with them)
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if (DOMAIN, entry.entry_id) not in device.identifiers:
            devices.async_remove_device(device.id)
    # The house plan calendar, its sensors and other entities of older versions
    entities = er.async_get(hass)
    keep = {f"{entry.entry_id}_{key}" for key in ENTITY_KEYS}
    for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
        if entity.unique_id not in keep:
            entities.async_remove(entity.entity_id)

    # Saved settings of people and cars, person calendar files
    storage = Path(hass.config.path(".storage"))

    def _remove_files() -> None:
        (storage / f"{DOMAIN}.settings").unlink(missing_ok=True)
        for path in storage.glob(f"{DOMAIN}.calendar.*.ics"):
            path.unlink(missing_ok=True)

    await hass.async_add_executor_job(_remove_files)
