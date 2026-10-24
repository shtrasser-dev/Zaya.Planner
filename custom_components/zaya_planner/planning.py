"""“Time to plan”: tell rules (automations) to plan again."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CALL_SERVICE, SERVICE_RELOAD
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import EVENT_PLAN_DONE, EVENT_PLAN_FAILED, EVENT_TIME_TO_PLAN, TIME_TO_PLAN_INTERVAL

# Scripts are saved by reloading them; rules plan once the reload is over
SCRIPTS_RELOAD_DELAY = 2


class TimeToPlan:
    """Fires “time to plan” when rules should plan again.

    After Home Assistant starts, when automations or scripts are saved, when a
    plan ends, when events of the chosen calendars change (see EventReader),
    and every quarter of an hour as a safety net.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry

    @callback
    def async_start(self, _hass: HomeAssistant | None = None) -> None:
        """Listen for the reasons to plan again, and plan once now."""
        for event_type in (EVENT_PLAN_DONE, EVENT_PLAN_FAILED, "automation_reloaded"):
            self.entry.async_on_unload(self.hass.bus.async_listen(event_type, self._async_on_event))
        # Saving a script in the editor calls script.reload; there is no event of its own
        self.entry.async_on_unload(
            self.hass.bus.async_listen(
                EVENT_CALL_SERVICE, self._async_on_scripts_reload, event_filter=_is_scripts_reload
            )
        )
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
    def _async_on_scripts_reload(self, _event: Event) -> None:
        self.entry.async_on_unload(
            async_call_later(self.hass, SCRIPTS_RELOAD_DELAY, self._async_on_interval)
        )

    @callback
    def _async_on_interval(self, _now: datetime) -> None:
        self.async_fire()


@callback
def _is_scripts_reload(event_data: dict[str, Any]) -> bool:
    return event_data.get("domain") == "script" and event_data.get("service") == SERVICE_RELOAD
