"""Actions (services) of Zaya Planner."""

from __future__ import annotations

from datetime import datetime, timedelta

import probatio

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, service
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ACTION,
    ATTR_CAR,
    ATTR_DEVICES,
    ATTR_DUE,
    ATTR_DRIVER,
    ATTR_DUE_ENTITY,
    ATTR_ENTITY_ID,
    ATTR_EXPIRES,
    ATTR_ID,
    ATTR_KIND,
    ATTR_OFFSET,
    ATTR_PARAMS,
    ATTR_RIDERS,
    ATTR_RULE_ID,
    ATTR_STATE,
    ATTR_STATUS,
    ATTR_TASKS,
    ATTR_TITLE,
    ATTR_TRAVEL_MINUTES,
    ATTR_TRIP,
    BUILTIN_ACTIONS,
    CONF_ACTION_PREFIX,
    DEFAULT_ACTION_PREFIX,
    DOMAIN,
    EXPIRES_ALWAYS,
    SERVICE_LIST,
    SERVICE_REMOVE,
    SERVICE_SET_EVENT_DETAILS,
    SERVICE_ADD_EVENT,
    SERVICE_ENSURE_STATE,
    SERVICE_GET_EVENTS,
    SERVICE_UPSERT,
    EventKind,
    TaskStatus,
    TripKind,
)
from .ensure import async_ensure_state
from .store import TaskDefinition, TaskManager, as_aware

UPSERT_SCHEMA = probatio.All(
    probatio.Schema(
        {
            probatio.Required(ATTR_ID): cv.string,
            probatio.Optional(ATTR_TITLE): cv.string,
            # When: a moment, or the time of an entity; plus an offset in minutes
            probatio.Optional(ATTR_DUE): cv.datetime,
            probatio.Optional(ATTR_DUE_ENTITY): cv.entity_id,
            probatio.Optional(ATTR_OFFSET, default=0): probatio.Coerce(int),
            # What: devices to turn on/off, or an action with params
            probatio.Optional(ATTR_DEVICES): cv.entity_ids,
            probatio.Optional(ATTR_STATE): probatio.In(["on", "off"]),
            probatio.Optional(ATTR_ACTION): cv.entity_id,
            probatio.Optional(ATTR_PARAMS): dict,
            probatio.Optional(ATTR_EXPIRES): probatio.Any(EXPIRES_ALWAYS, cv.datetime),
            probatio.Optional(ATTR_RULE_ID): cv.string,
        }
    ),
    cv.has_at_least_one_key(ATTR_DUE, ATTR_DUE_ENTITY),
    cv.has_at_most_one_key(ATTR_DUE, ATTR_DUE_ENTITY),
    cv.has_at_least_one_key(ATTR_ACTION, ATTR_DEVICES),
    cv.has_at_most_one_key(ATTR_ACTION, ATTR_DEVICES),
)

REMOVE_SCHEMA = probatio.All(
    probatio.Schema(
        {
            probatio.Optional(ATTR_ID): cv.string,
            probatio.Optional(ATTR_RULE_ID): cv.string,
        }
    ),
    cv.has_at_least_one_key(ATTR_ID, ATTR_RULE_ID),
)

LIST_SCHEMA = probatio.Schema(
    {
        probatio.Optional(ATTR_ID): cv.string,
        probatio.Optional(ATTR_RULE_ID): cv.string,
        probatio.Optional(ATTR_STATUS): probatio.All(
            cv.ensure_list, [probatio.In([status.value for status in TaskStatus])]
        ),
    }
)

DETAILS_FIELDS = {
    probatio.Optional(ATTR_KIND): probatio.In([kind.value for kind in EventKind]),
    probatio.Optional(ATTR_TRIP): probatio.In([trip.value for trip in TripKind]),
    probatio.Optional(ATTR_RIDERS): cv.entity_ids,
    probatio.Optional(ATTR_DRIVER): probatio.Any(None, cv.entity_id),
    probatio.Optional(ATTR_CAR): probatio.Any(None, cv.string),
    probatio.Optional(ATTR_TRAVEL_MINUTES): probatio.Any(
        None, probatio.All(probatio.Coerce(int), probatio.Range(min=0, max=1440))
    ),
}

ADD_EVENT_FIELDS = {
    probatio.Required("summary"): cv.string,
    probatio.Required("start"): probatio.Any(cv.datetime, cv.date),
    probatio.Required("end"): probatio.Any(cv.datetime, cv.date),
    probatio.Optional("rrule"): cv.string,
    probatio.Optional("description"): cv.string,
    probatio.Optional("location"): cv.string,
    **DETAILS_FIELDS,
}

SET_EVENT_DETAILS_FIELDS = {
    probatio.Required("uid"): cv.string,
    probatio.Optional("recurrence_id"): cv.string,
    probatio.Optional("recurrence_range"): probatio.In(["", "this_and_future", "THISANDFUTURE"]),
    **DETAILS_FIELDS,
}

GET_EVENTS_FIELDS = {
    probatio.Required("start_date_time"): cv.datetime,
    probatio.Required("end_date_time"): cv.datetime,
}

ENSURE_STATE_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_ENTITY_ID): cv.entity_ids,
        probatio.Required(ATTR_STATE): probatio.In(["on", "off"]),
    }
)


def _get_manager(hass: HomeAssistant) -> tuple[TaskManager, str]:
    """Return the task manager and the allowed action prefix."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )
    entry = entries[0]
    prefix = entry.options.get(CONF_ACTION_PREFIX, DEFAULT_ACTION_PREFIX)
    return entry.runtime_data.manager, prefix


def _entity_time(hass: HomeAssistant, entity_id: str) -> datetime:
    """Return the moment an entity shows: a timestamp, a date and time, or a time.

    A time without a date (an input_datetime with time only) means its next
    occurrence. The value is read once, when the task is planned.
    """
    state = hass.states.get(entity_id)
    if state is not None and state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        if (moment := dt_util.parse_datetime(state.state)) is not None:
            return as_aware(moment)
        if (clock := dt_util.parse_time(state.state)) is not None:
            now = dt_util.now()
            moment = now.replace(
                hour=clock.hour, minute=clock.minute, second=clock.second, microsecond=0
            )
            if moment <= now:
                moment += timedelta(days=1)
            return dt_util.as_utc(moment)
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="due_entity_no_time",
        translation_placeholders={"entity_id": entity_id},
    )


async def _async_upsert(call: ServiceCall) -> ServiceResponse:
    manager, prefix = _get_manager(call.hass)
    data = call.data

    if ATTR_DEVICES in data:
        if ATTR_STATE not in data:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="state_required"
            )
        action = f"{DOMAIN}.{SERVICE_ENSURE_STATE}"
        params = {ATTR_ENTITY_ID: data[ATTR_DEVICES], ATTR_STATE: data[ATTR_STATE]}
    else:
        action = data[ATTR_ACTION]
        params = data.get(ATTR_PARAMS)
    if not action.startswith(prefix) and action not in BUILTIN_ACTIONS:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="action_not_allowed",
            translation_placeholders={"action": action, "prefix": prefix},
        )
    if ATTR_DUE_ENTITY in data:
        due = _entity_time(call.hass, data[ATTR_DUE_ENTITY])
    else:
        due = as_aware(data[ATTR_DUE])
    due += timedelta(minutes=data[ATTR_OFFSET])

    expires = data.get(ATTR_EXPIRES)
    if isinstance(expires, datetime) and as_aware(expires) < due:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="expires_before_due"
        )

    task, result = await manager.async_upsert(
        TaskDefinition(
            id=data[ATTR_ID],
            due=due,
            action=action,
            title=data.get(ATTR_TITLE, ""),
            params=params,
            expires=expires,
            rule_id=data.get(ATTR_RULE_ID),
        )
    )
    if not call.return_response:
        return None
    return {"result": str(result), "task": task.as_dict() if task else None}


async def _async_remove(call: ServiceCall) -> ServiceResponse:
    manager, _ = _get_manager(call.hass)
    cancelled = await manager.async_cancel(
        task_id=call.data.get(ATTR_ID), rule_id=call.data.get(ATTR_RULE_ID)
    )
    if not call.return_response:
        return None
    return {"cancelled": [task.id for task in cancelled]}


async def _async_list(call: ServiceCall) -> ServiceResponse:
    manager, _ = _get_manager(call.hass)
    statuses = call.data.get(ATTR_STATUS)
    tasks = manager.find(
        task_id=call.data.get(ATTR_ID),
        rule_id=call.data.get(ATTR_RULE_ID),
        statuses=[TaskStatus(status) for status in statuses] if statuses else None,
    )
    return {ATTR_TASKS: [task.as_dict() for task in tasks]}


async def _async_ensure_state(call: ServiceCall) -> ServiceResponse:
    ok, reason = await async_ensure_state(
        call.hass, call.data[ATTR_ENTITY_ID], call.data[ATTR_STATE]
    )
    if not call.return_response:
        return None
    return {"ok": ok, "reason": reason}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions of Zaya Planner."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPSERT,
        _async_upsert,
        schema=UPSERT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE,
        _async_remove,
        schema=REMOVE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST,
        _async_list,
        schema=LIST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # Actions on person calendars (entities of the calendar platform)
    for name, fields, func, response in (
        (SERVICE_ADD_EVENT, ADD_EVENT_FIELDS, "async_service_add_event", SupportsResponse.OPTIONAL),
        (
            SERVICE_SET_EVENT_DETAILS,
            SET_EVENT_DETAILS_FIELDS,
            "async_service_set_event_details",
            SupportsResponse.OPTIONAL,
        ),
        (SERVICE_GET_EVENTS, GET_EVENTS_FIELDS, "async_service_get_events", SupportsResponse.ONLY),
    ):
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain="calendar",
            schema=fields,
            func=func,
            supports_response=response,
        )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENSURE_STATE,
        _async_ensure_state,
        schema=ENSURE_STATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
