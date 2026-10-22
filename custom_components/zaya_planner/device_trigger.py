"""Device triggers of Zaya Planner: “time to plan”."""

from __future__ import annotations

import probatio

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, Event, HassJob, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_TIME_TO_PLAN

TRIGGER_TIME_TO_PLAN = "time_to_plan"
TRIGGER_TYPES = {TRIGGER_TIME_TO_PLAN}

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
            CONF_TYPE: TRIGGER_TIME_TO_PLAN,
        }
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Run the action when rules should plan again."""
    job = HassJob(action)
    trigger_data = trigger_info["trigger_data"]

    @callback
    def _handle_event(event: Event) -> None:
        hass.async_run_hass_job(
            job,
            {"trigger": {**trigger_data, **config, "description": "time to plan"}},
            event.context,
        )

    return hass.bus.async_listen(EVENT_TIME_TO_PLAN, _handle_event)
