"""Actions (services) of Zaya Planner.

Plans: add_step (to a draft of the current run), set_plan, cancel_plan, get_plans.
Steps: ensure_state (turn on/off and confirm). Calendars: find_events.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import probatio

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ACTION,
    ATTR_AFTER,
    ATTR_AT,
    ATTR_BEFORE,
    ATTR_CALENDARS,
    ATTR_DATA,
    ATTR_DEVICES,
    ATTR_DISPLAY,
    ATTR_END,
    ATTR_ENTITY_ID,
    ATTR_EVENTS,
    ATTR_FILTER,
    ATTR_FIRST_OF_DAY,
    ATTR_FORCE,
    ATTR_FREEZE_AT,
    ATTR_FREEZE_BEFORE,
    ATTR_ON_FAILURE,
    ATTR_ONLY_FUTURE,
    ATTR_OWNER,
    ATTR_PLANS,
    ATTR_START,
    ATTR_STATE,
    ATTR_STATUS,
    ATTR_STEPS,
    ATTR_SUBTRACT,
    ATTR_SUMMARY,
    BUILTIN_ACTIONS,
    CONF_ACTION_PREFIX,
    DEFAULT_ACTION_PREFIX,
    DISPLAY_SPAN,
    DISPLAY_STEPS,
    DOMAIN,
    DRAFT_LIFETIME,
    EVENTS_HORIZON,
    SERVICE_ADD_STEP,
    SERVICE_CANCEL_PLAN,
    SERVICE_ENSURE_STATE,
    SERVICE_FIND_EVENTS,
    SERVICE_GET_PLANS,
    SERVICE_SET_PLAN,
    PlanResult,
    PlanStatus,
)
from .description import Filter
from .display import plan_info
from .ensure import async_ensure_state
from .models import Freeze, Step
from .store import as_aware

DRAFTS = f"{DOMAIN}_drafts"

STEP_FIELDS = {
    # No time (empty): the step is left out, e.g. a module called without a departure
    probatio.Optional(ATTR_AT): probatio.Any(None, "", cv.datetime),
    # Earlier or later than `at` by minutes
    probatio.Optional(ATTR_BEFORE, default=0): probatio.Coerce(int),
    probatio.Optional(ATTR_AFTER, default=0): probatio.Coerce(int),
    # What: devices to turn on/off, or an action with data
    probatio.Optional(ATTR_DEVICES): cv.entity_ids,
    probatio.Optional(ATTR_STATE): probatio.In(["on", "off"]),
    probatio.Optional(ATTR_ACTION): cv.entity_id,
    probatio.Optional(ATTR_DATA): dict,
}


def _step_schema(fields: dict[Any, Any]) -> Any:
    return probatio.All(
        probatio.Schema(fields),
        cv.has_at_least_one_key(ATTR_ACTION, ATTR_DEVICES),
        cv.has_at_most_one_key(ATTR_ACTION, ATTR_DEVICES),
    )


ADD_STEP_SCHEMA = _step_schema({probatio.Required(ATTR_OWNER): cv.string, **STEP_FIELDS})

SET_PLAN_SCHEMA = probatio.All(
    probatio.Schema(
        {
            probatio.Required(ATTR_OWNER): cv.string,
            probatio.Optional(ATTR_SUMMARY, default=""): cv.string,
            probatio.Optional(ATTR_FREEZE_AT): cv.time,
            probatio.Optional(ATTR_FREEZE_BEFORE): probatio.All(
                probatio.Coerce(int), probatio.Range(min=0, max=7 * 24 * 60)
            ),
            probatio.Optional(ATTR_ON_FAILURE): cv.entity_domain("script"),
            probatio.Optional(ATTR_STEPS): [_step_schema(STEP_FIELDS)],
            probatio.Optional(ATTR_DISPLAY, default=DISPLAY_SPAN): probatio.In(
                [DISPLAY_SPAN, DISPLAY_STEPS]
            ),
            probatio.Optional(ATTR_FORCE, default=False): cv.boolean,
        }
    ),
    cv.has_at_most_one_key(ATTR_FREEZE_AT, ATTR_FREEZE_BEFORE),
)

CANCEL_PLAN_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_OWNER): cv.string,
        probatio.Optional(ATTR_FORCE, default=False): cv.boolean,
    }
)

GET_PLANS_SCHEMA = probatio.Schema(
    {
        probatio.Optional(ATTR_OWNER): cv.string,
        probatio.Optional(ATTR_STATUS): probatio.All(
            cv.ensure_list, [probatio.In([status.value for status in PlanStatus])]
        ),
    }
)

ENSURE_STATE_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_ENTITY_ID): cv.entity_ids,
        probatio.Required(ATTR_STATE): probatio.In(["on", "off"]),
    }
)

FIND_EVENTS_SCHEMA = probatio.Schema(
    {
        probatio.Optional(ATTR_CALENDARS): cv.entity_ids,
        probatio.Optional(ATTR_FILTER, default=""): cv.string,
        probatio.Optional(ATTR_START): cv.datetime,
        probatio.Optional(ATTR_END): cv.datetime,
        probatio.Optional(ATTR_FIRST_OF_DAY, default=False): cv.boolean,
        probatio.Optional(ATTR_ONLY_FUTURE, default=False): cv.boolean,
        probatio.Optional(ATTR_SUBTRACT): cv.string,
    }
)


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _get_entry(hass: HomeAssistant) -> Any:
    """Return the loaded config entry of Zaya Planner."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise _error("not_loaded")
    return entries[0]


def _step(hass: HomeAssistant, data: dict[str, Any]) -> Step | None:
    """Return a step from action data (None without a time), or explain what is wrong."""
    if not data.get(ATTR_AT):
        return None
    entry = _get_entry(hass)
    if ATTR_DEVICES in data:
        if ATTR_STATE not in data:
            raise _error("state_required")
        action = f"{DOMAIN}.{SERVICE_ENSURE_STATE}"
        step_data = {ATTR_ENTITY_ID: data[ATTR_DEVICES], ATTR_STATE: data[ATTR_STATE]}
    else:
        action = data[ATTR_ACTION]
        step_data = dict(data.get(ATTR_DATA) or {})
        prefix = entry.options.get(CONF_ACTION_PREFIX, DEFAULT_ACTION_PREFIX)
        # Only the built-in action and a named subset of scripts may be steps
        if action not in BUILTIN_ACTIONS and not action.startswith(prefix):
            raise _error("action_not_allowed", action=action, prefix=prefix)
    at = as_aware(data[ATTR_AT]) - timedelta(minutes=data.get(ATTR_BEFORE, 0))
    at += timedelta(minutes=data.get(ATTR_AFTER, 0))
    return Step(at=at, action=action, data=step_data)


def _drafts(hass: HomeAssistant) -> dict[tuple[str, str], tuple[datetime, list[Step]]]:
    """Steps added in a run, by (owner, run), waiting for set_plan."""
    drafts = hass.data.setdefault(DRAFTS, {})
    limit = dt_util.utcnow() - DRAFT_LIFETIME
    for key in [key for key, (created, _) in drafts.items() if created < limit]:
        del drafts[key]
    return drafts


async def _async_add_step(call: ServiceCall) -> None:
    if (step := _step(call.hass, dict(call.data))) is None:
        return
    key = (call.data[ATTR_OWNER], call.context.id)
    created, steps = _drafts(call.hass).get(key, (dt_util.utcnow(), []))
    _drafts(call.hass)[key] = (created, [*steps, step])


async def _async_set_plan(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    manager = _get_entry(hass).runtime_data.manager
    owner = call.data[ATTR_OWNER]
    force = call.data[ATTR_FORCE]
    # The steps added in this run (also by scripts it called), then those given here
    _, steps = _drafts(hass).pop((owner, call.context.id), (None, []))
    given = (_step(hass, dict(step)) for step in call.data.get(ATTR_STEPS, []))
    steps = [*steps, *(step for step in given if step is not None)]

    if not steps:
        cancelled, result = await manager.async_cancel(owner, force=force)
        return {"result": str(result), ATTR_PLANS: [plan_info(hass, plan) for plan in cancelled]}

    if any(later.at < earlier.at for earlier, later in zip(steps, steps[1:])):
        raise _error("steps_not_in_order")
    if steps[0].at <= dt_util.utcnow():
        # Too late for this plan (e.g. its first step was a while ago): nothing changes
        return {"result": str(PlanResult.PAST), "plan": None}
    freeze = Freeze(at=call.data.get(ATTR_FREEZE_AT), before=call.data.get(ATTR_FREEZE_BEFORE))
    plan, result = await manager.async_set_plan(
        owner,
        steps,
        summary=call.data[ATTR_SUMMARY],
        freeze=freeze,
        on_failure=call.data.get(ATTR_ON_FAILURE),
        display=call.data[ATTR_DISPLAY],
        force=force,
    )
    return {"result": str(result), "plan": plan_info(hass, plan) if plan else None}


async def _async_cancel_plan(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    manager = _get_entry(hass).runtime_data.manager
    cancelled, result = await manager.async_cancel(
        call.data[ATTR_OWNER], force=call.data[ATTR_FORCE]
    )
    return {"result": str(result), ATTR_PLANS: [plan_info(hass, plan) for plan in cancelled]}


async def _async_get_plans(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    manager = _get_entry(hass).runtime_data.manager
    statuses = call.data.get(ATTR_STATUS)
    plans = manager.find(
        owner=call.data.get(ATTR_OWNER),
        statuses=[PlanStatus(status) for status in statuses] if statuses else None,
    )
    return {ATTR_PLANS: [plan_info(hass, plan) for plan in plans]}


async def _async_ensure_state(call: ServiceCall) -> ServiceResponse:
    ok, reason = await async_ensure_state(
        call.hass, call.data[ATTR_ENTITY_ID], call.data[ATTR_STATE]
    )
    if not call.return_response:
        return None
    return {"ok": ok, "reason": reason}


async def _async_find_events(call: ServiceCall) -> ServiceResponse:
    reader = _get_entry(call.hass).runtime_data.events
    calendars = call.data.get(ATTR_CALENDARS) or None
    if calendars and (other := [c for c in calendars if c not in reader.calendars]):
        raise _error("not_a_chosen_calendar", calendars=", ".join(other))
    event_filter = Filter.parse(call.data[ATTR_FILTER])
    unknown = event_filter.unknown_words(reader.dictionary)
    subtract = call.data.get(ATTR_SUBTRACT)
    if subtract and reader.dictionary.key(subtract) is None:
        unknown.append(subtract)
    if unknown:
        raise _error("unknown_filter_words", words=", ".join(unknown))
    start = as_aware(call.data[ATTR_START]) if ATTR_START in call.data else dt_util.utcnow()
    end = as_aware(call.data[ATTR_END]) if ATTR_END in call.data else start + EVENTS_HORIZON
    if end <= start:
        raise _error("end_before_start")
    events = await reader.async_find(
        event_filter,
        start,
        end,
        calendars,
        first_of_day=call.data[ATTR_FIRST_OF_DAY],
        only_future=call.data[ATTR_ONLY_FUTURE],
        subtract=subtract,
    )
    return {ATTR_EVENTS: [event.as_dict() for event in events]}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions of Zaya Planner."""
    for name, handler, schema, response in (
        (SERVICE_ADD_STEP, _async_add_step, ADD_STEP_SCHEMA, SupportsResponse.NONE),
        (SERVICE_SET_PLAN, _async_set_plan, SET_PLAN_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_CANCEL_PLAN, _async_cancel_plan, CANCEL_PLAN_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_GET_PLANS, _async_get_plans, GET_PLANS_SCHEMA, SupportsResponse.ONLY),
        (SERVICE_ENSURE_STATE, _async_ensure_state, ENSURE_STATE_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_FIND_EVENTS, _async_find_events, FIND_EVENTS_SCHEMA, SupportsResponse.ONLY),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=response
        )
