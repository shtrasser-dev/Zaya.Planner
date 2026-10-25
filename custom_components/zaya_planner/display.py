"""How plans and steps are shown to people: calendar, logbook, notifications."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import ATTR_ENTITY_ID, ATTR_STATE, BUILTIN_ACTIONS, PlanStatus, StepStatus
from .messages import message
from .models import Plan, Step


def _name(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    return state.name if state else entity_id


def step_text(hass: HomeAssistant, step: Step) -> str:
    """Return what a step does: “Leaf charger: on”, the script and its data, or a mark's name."""
    if step.is_mark:
        return step.name
    if step.action in BUILTIN_ACTIONS and ATTR_ENTITY_ID in step.data:
        names = ", ".join(_name(hass, e) for e in cv.ensure_list(step.data[ATTR_ENTITY_ID]))
        state = step.data.get(ATTR_STATE)
        return f"{names}: {message(hass, f'state_{state}')}" if state else names
    text = _name(hass, step.action)
    data = dict(step.data)
    state = data.pop(ATTR_STATE, None)
    if state in ("on", "off"):
        text += f": {message(hass, f'state_{state}')}"
    elif state is not None:
        data = {ATTR_STATE: state, **data}
    if data:
        values = ", ".join(
            f"{key}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)}"
            for key, value in data.items()
        )
        text += f" ({values})"
    return text


def step_info(hass: HomeAssistant, step: Step) -> dict[str, Any]:
    """Return a step for an action response, an event or failure instructions."""
    return {
        "at": dt_util.as_local(step.at).isoformat(),
        "summary": step_text(hass, step),
        "action": step.action,
        "data": step.data,
        "status": str(step.status),
        "attempts": step.attempts,
        "last_error": step.last_error,
    }


def plan_info(hass: HomeAssistant, plan: Plan) -> dict[str, Any]:
    """Return a plan for an action response or a sensor attribute."""
    return {
        "uid": plan.uid,
        "owner": plan.owner,
        "summary": plan.display_summary,
        "status": str(plan.status),
        "status_text": message(hass, f"plan_status_{plan.status}"),
        "start": dt_util.as_local(plan.start).isoformat(),
        "end": dt_util.as_local(plan.end).isoformat(),
        "tags": plan.tags,
        "frozen_at": dt_util.as_local(plan.frozen_at).isoformat() if plan.frozen_at else None,
        "on_failure": plan.on_failure,
        "reason": plan.reason,
        "conflicts": plan.conflicts,
        "steps": [step_info(hass, step) for step in plan.steps],
    }


def plan_description(hass: HomeAssistant, plan: Plan) -> str:
    """Return the description of a plan's calendar event: its steps, one per line."""
    lines = [
        f"{message(hass, 'label_status')}: {message(hass, f'plan_status_{plan.status}')}",
        f"{message(hass, 'label_owner')}: {plan.owner}",
    ]
    if plan.tags:
        lines.append(" ".join(f"#{tag}" for tag in plan.tags))
    if plan.status == PlanStatus.FROZEN and plan.frozen_at:
        lines.append(
            f"{message(hass, 'label_frozen')}: {dt_util.as_local(plan.frozen_at):%d.%m %H:%M}"
        )
    for step in plan.steps:
        line = (
            f"{dt_util.as_local(step.at):%H:%M} {step_text(hass, step)}"
            f" — {message(hass, f'step_status_{step.status}')}"
        )
        if step.last_error and step.status != StepStatus.DONE:
            line += f" ({step.last_error})"
        lines.append(line)
    if plan.reason:
        lines.append(f"{message(hass, 'label_reason')}: {plan.reason}")
    if plan.conflicts:
        lines.append(f"{message(hass, 'label_conflicts')}:")
        lines.extend(conflict.split(": ", 1)[-1] for conflict in plan.conflicts)
    return "\n".join(lines)
