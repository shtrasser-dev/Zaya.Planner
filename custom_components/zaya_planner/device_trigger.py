"""Device triggers of Zaya Planner: “the house plan changed”."""

from __future__ import annotations

import probatio

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, Event, HassJob, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_PLAN_CHANGED

TRIGGER_PLAN_CHANGED = "plan_changed"
TRIGGER_TYPES = {TRIGGER_PLAN_CHANGED}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {probatio.Required(CONF_TYPE): probatio.In(TRIGGER_TYPES)}
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """List triggers of the Zaya Planner device (not of people or cars)."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return []
    entry_ids = {
        identifier
        for domain, identifier in device.identifiers
        if domain == DOMAIN
    }
    if not entry_ids & set(device.config_entries):
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_PLAN_CHANGED,
        }
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Run the action when the house plan changes."""
    job = HassJob(action)
    trigger_data = trigger_info["trigger_data"]

    @callback
    def _handle_event(event: Event) -> None:
        hass.async_run_hass_job(
            job,
            {"trigger": {**trigger_data, **config, "description": "house plan changed"}},
            event.context,
        )

    return hass.bus.async_listen(EVENT_PLAN_CHANGED, _handle_event)
