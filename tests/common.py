"""Helpers for Zaya Planner tests."""

from __future__ import annotations

from typing import Any

from custom_components.zaya_planner.const import DOMAIN, SERVICE_UPSERT
from homeassistant.core import HomeAssistant

NOW = "2026-10-21T12:00:00+00:00"


async def upsert(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    """Call zaya_planner.upsert and return its response."""
    data.setdefault("action", "script.plan_switch")
    return await hass.services.async_call(
        DOMAIN, SERVICE_UPSERT, data, blocking=True, return_response=True
    )
