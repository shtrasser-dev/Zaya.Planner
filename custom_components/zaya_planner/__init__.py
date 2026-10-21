"""Zaya Planner: plan and reliably run smart home tasks."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import (
    CLEANUP_INTERVAL,
    CONF_RETENTION_DAYS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)
from .services import async_setup_services
from .store import TaskManager

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

type ZayaPlannerConfigEntry = ConfigEntry[TaskManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Zaya Planner actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZayaPlannerConfigEntry) -> bool:
    """Set up Zaya Planner from a config entry."""
    manager = TaskManager(hass, entry.entry_id)
    await manager.async_load()
    entry.runtime_data = manager

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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZayaPlannerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
