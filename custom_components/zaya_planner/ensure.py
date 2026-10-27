"""Built-in step action: turn devices on or off and wait until they confirm."""

from __future__ import annotations

import asyncio

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event

from .const import ENSURE_STATE_TIMEOUT
from .messages import message


async def async_ensure_state(
    hass: HomeAssistant, entity_ids: list[str], state: str
) -> tuple[bool, str]:
    """Bring every entity to `state` ("on"/"off"); return (ok, reason).

    An entity already in that state is left alone. The rest are switched with
    their domain's turn_on/turn_off and must report the new state within
    ENSURE_STATE_TIMEOUT. The reason names each entity that did not.
    """
    results = await asyncio.gather(
        *(_async_ensure_one(hass, entity_id, state) for entity_id in entity_ids)
    )
    failures = [reason for reason in results if reason]
    return not failures, "; ".join(failures)


async def _async_ensure_one(hass: HomeAssistant, entity_id: str, state: str) -> str:
    """Bring one entity to the state; return "" or why it failed."""
    current = hass.states.get(entity_id)
    name = current.name if current else entity_id
    if current is None or current.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return f"{name}: {message(hass, 'reason_unavailable')}"
    if current.state == state:
        return ""

    confirmed = asyncio.Event()

    @callback
    def _async_changed(event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is not None and new_state.state == state:
            confirmed.set()

    unsub = async_track_state_change_event(hass, [entity_id], _async_changed)
    try:
        domain = entity_id.split(".", 1)[0]
        try:
            await hass.services.async_call(
                domain, f"turn_{state}", {"entity_id": entity_id}, blocking=True
            )
        except HomeAssistantError as err:
            return f"{name}: {err}"
        if (now := hass.states.get(entity_id)) is not None and now.state == state:
            return ""
        try:
            async with asyncio.timeout(ENSURE_STATE_TIMEOUT.total_seconds()):
                await confirmed.wait()
        except TimeoutError:
            return f"{name}: {message(hass, 'reason_not_confirmed')}"
        return ""
    finally:
        unsub()
