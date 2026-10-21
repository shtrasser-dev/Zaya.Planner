"""Calling scripts (tasks and rule planners) and sending notifications."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_NOTIFY_ACTION, DEFAULT_NOTIFY_ACTION, SCRIPT_TIMEOUT
from .messages import message

_LOGGER = logging.getLogger(__name__)


async def async_call_script(
    hass: HomeAssistant, action: str, data: dict[str, Any]
) -> tuple[bool, str, Any]:
    """Call a script that answers {ok, reason}; return (ok, reason, response).

    A missing script, an error, a timeout or an answer without `ok` count as
    not ok, with the reason in the language of Home Assistant.
    """
    domain, _, service = action.partition(".")
    if not hass.services.has_service(domain, service):
        return False, message(hass, "reason_not_found", action=action), None
    try:
        async with asyncio.timeout(SCRIPT_TIMEOUT.total_seconds()):
            response = await hass.services.async_call(
                domain, service, data, blocking=True, return_response=True
            )
    except TimeoutError:
        seconds = int(SCRIPT_TIMEOUT.total_seconds())
        return False, message(hass, "reason_timeout", seconds=seconds), None
    except HomeAssistantError as err:
        return False, str(err) or type(err).__name__, None
    except Exception as err:  # noqa: BLE001 - a script may fail in any way
        _LOGGER.exception("Script %s failed", action)
        return False, str(err) or type(err).__name__, None

    if not isinstance(response, dict) or "ok" not in response:
        return False, message(hass, "reason_no_ok"), response
    reason = str(response.get("reason") or "") or message(hass, "reason_unknown")
    return bool(response["ok"]), reason, response


async def async_notify(
    hass: HomeAssistant, entry: ConfigEntry, title: str, text: str
) -> None:
    """Send a notification with the action from the settings."""
    action = entry.options.get(CONF_NOTIFY_ACTION, DEFAULT_NOTIFY_ACTION)
    domain, _, service = (action or "persistent_notification.create").partition(".")
    try:
        await hass.services.async_call(
            domain, service, {"title": title, "message": text}, blocking=False
        )
    except HomeAssistantError as err:
        _LOGGER.warning("Could not send notification with %s: %s", action, err)
