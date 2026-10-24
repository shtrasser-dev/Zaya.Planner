"""Helpers for Zaya Planner tests.

Planning actions answer nothing: the helpers call them and return what is
there afterwards (the plans of the owner), as an automation would see it.
"""

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


async def call(
    hass: HomeAssistant, service: str, data: dict[str, Any], context: Context | None = None
) -> None:
    """Call a planning action of Zaya Planner."""
    await hass.services.async_call(DOMAIN, service, data, blocking=True, context=context)


async def get_plans(hass: HomeAssistant, **data: Any) -> list[dict[str, Any]]:
    """Call zaya_planner.get_plans."""
    response = await hass.services.async_call(
        DOMAIN, "get_plans", data, blocking=True, return_response=True
    )
    return response["plans"]


async def next_plan(hass: HomeAssistant, owner: str = OWNER) -> dict[str, Any] | None:
    """Return the owner's plan that is not finished yet and starts last, if any."""
    plans = [
        plan
        for plan in await get_plans(hass, owner=owner)
        if plan["status"] in ("planned", "frozen", "running")
    ]
    return plans[-1] if plans else None


async def set_plan(
    hass: HomeAssistant, steps: list[dict[str, Any]], **data: Any
) -> dict[str, Any] | None:
    """Call zaya_planner.set_plan with the steps as a list; return the owner's plan."""
    data.setdefault("owner", OWNER)
    await call(hass, "set_plan", {**data, "steps": steps})
    return await next_plan(hass, data["owner"])


async def build_plan(
    hass: HomeAssistant, steps: list[dict[str, Any]], **data: Any
) -> dict[str, Any] | None:
    """Make a plan as an automation does: add_step for each step, then set_plan (one run)."""
    data.setdefault("owner", OWNER)
    context = Context()
    for item in steps:
        await call(hass, "add_step", {"owner": data["owner"], **item}, context)
    await call(hass, "set_plan", data, context)
    return await next_plan(hass, data["owner"])


async def cancel_plan(hass: HomeAssistant, **data: Any) -> None:
    """Call zaya_planner.cancel_plan."""
    data.setdefault("owner", OWNER)
    await call(hass, "cancel_plan", data)
