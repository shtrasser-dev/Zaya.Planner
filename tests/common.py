"""Helpers for Zaya Planner tests."""

from __future__ import annotations

from typing import Any

from custom_components.zaya_planner.const import DOMAIN
from homeassistant.core import Context, HomeAssistant

# Wednesday 2026-10-21 15:00 in Moscow
NOW = "2026-10-21T12:00:00+00:00"
OWNER = "automation.yard_lights"


def step(at: str, entity_id: str = "switch.light", state: str = "on") -> dict[str, Any]:
    """A step that turns a switch on or off."""
    return {"at": at, "devices": [entity_id], "state": state}


async def set_plan(
    hass: HomeAssistant, steps: list[dict[str, Any]], **data: Any
) -> dict[str, Any]:
    """Call zaya_planner.set_plan with the steps as a list."""
    data.setdefault("owner", OWNER)
    return await hass.services.async_call(
        DOMAIN,
        "set_plan",
        {**data, "steps": steps},
        blocking=True,
        return_response=True,
    )


async def build_plan(
    hass: HomeAssistant, steps: list[dict[str, Any]], **data: Any
) -> dict[str, Any]:
    """Make a plan as an automation does: add_step for each step, then set_plan (one run)."""
    data.setdefault("owner", OWNER)
    context = Context()
    for item in steps:
        await hass.services.async_call(
            DOMAIN, "add_step", {"owner": data["owner"], **item}, blocking=True, context=context
        )
    return await hass.services.async_call(
        DOMAIN, "set_plan", data, blocking=True, return_response=True, context=context
    )


async def cancel_plan(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    """Call zaya_planner.cancel_plan."""
    data.setdefault("owner", OWNER)
    return await hass.services.async_call(
        DOMAIN, "cancel_plan", data, blocking=True, return_response=True
    )


async def get_plans(hass: HomeAssistant, **data: Any) -> list[dict[str, Any]]:
    """Call zaya_planner.get_plans."""
    response = await hass.services.async_call(
        DOMAIN, "get_plans", data, blocking=True, return_response=True
    )
    return response["plans"]
