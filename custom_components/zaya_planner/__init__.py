"""Zaya Planner: plan and reliably run smart home tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import (
    CLEANUP_INTERVAL,
    CONF_RETENTION_DAYS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    SIGNAL_TASKS_UPDATED,
)
from .executor import Executor
from .rules import RuleScheduler
from .services import async_setup_services
from .store import TaskManager

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


@dataclass(slots=True)
class ZayaPlannerData:
    """What a loaded Zaya Planner keeps."""

    manager: TaskManager
    executor: Executor
    rules: RuleScheduler
    # Options the entry was loaded with: a change means a reload
    options: dict[str, Any]


type ZayaPlannerConfigEntry = ConfigEntry[ZayaPlannerData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Zaya Planner actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZayaPlannerConfigEntry) -> bool:
    """Set up Zaya Planner from a config entry."""
    manager = TaskManager(hass, entry.entry_id)
    await manager.async_load()
    entry.runtime_data = ZayaPlannerData(
        manager=manager,
        executor=Executor(hass, entry, manager),
        rules=RuleScheduler(hass, entry, manager),
        options=dict(entry.options),
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

    # Run tasks and rules once Home Assistant has started: scripts are loaded by then
    entry.async_on_unload(async_at_started(hass, entry.runtime_data.executor.async_start))
    entry.async_on_unload(async_at_started(hass, entry.runtime_data.rules.async_start))
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(
    hass: HomeAssistant, entry: ZayaPlannerConfigEntry
) -> None:
    """Reload on new settings; follow added, changed or removed rules."""
    data = entry.runtime_data
    if dict(entry.options) != data.options:
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    data.rules.async_sync()
    # Rules are shown by the calendar and the sensors
    async_dispatcher_send(hass, f"{SIGNAL_TASKS_UPDATED}_{entry.entry_id}")


async def async_unload_entry(hass: HomeAssistant, entry: ZayaPlannerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
