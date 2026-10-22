"""“Time to plan”: tell rules (automations) to plan again."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import EVENT_TASK_DONE, EVENT_TASK_FAILED, EVENT_TIME_TO_PLAN, TIME_TO_PLAN_INTERVAL


class TimeToPlan:
    """Fires “time to plan” when rules should plan again.

    After Home Assistant starts, when automations are saved, when a task ends,
    and every quarter of an hour as a safety net.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry

    @callback
    def async_start(self, _hass: HomeAssistant | None = None) -> None:
        """Listen for the reasons to plan again, and plan once now."""
        for event_type in (EVENT_TASK_DONE, EVENT_TASK_FAILED, "automation_reloaded"):
            self.entry.async_on_unload(self.hass.bus.async_listen(event_type, self._async_on_event))
        self.entry.async_on_unload(
            async_track_time_interval(self.hass, self._async_on_interval, TIME_TO_PLAN_INTERVAL)
        )
        self.async_fire()

    @callback
    def async_fire(self) -> None:
        """Tell rules to plan again."""
        self.hass.bus.async_fire(EVENT_TIME_TO_PLAN)

    @callback
    def _async_on_event(self, _event: Any) -> None:
        self.async_fire()

    @callback
    def _async_on_interval(self, _now: datetime) -> None:
        self.async_fire()
