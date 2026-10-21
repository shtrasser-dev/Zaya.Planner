"""Zaya Planner: plan and reliably run smart home tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
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
from .services import async_setup_services
from .store import TaskManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


@dataclass(slots=True)
class ZayaPlannerData:
    """What a loaded Zaya Planner keeps."""

    manager: TaskManager
    executor: Executor


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
    entry.runtime_data = ZayaPlannerData(manager=manager, executor=executor)

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

    # Run tasks once Home Assistant has started: scripts are loaded by then
    entry.async_on_unload(async_at_started(hass, executor.async_start))
    return True


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
