"""Actions (services) of Zaya Planner."""

from __future__ import annotations

from datetime import datetime

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

from .const import (
    ATTR_ACTION,
    ATTR_DUE,
    ATTR_EXPIRES,
    ATTR_ID,
    ATTR_PARAMS,
    ATTR_RULE_ID,
    ATTR_STATUS,
    ATTR_TASKS,
    ATTR_TITLE,
    CONF_ACTION_PREFIX,
    DEFAULT_ACTION_PREFIX,
    DOMAIN,
    EXPIRES_ALWAYS,
    SERVICE_LIST,
    SERVICE_REMOVE,
    SERVICE_RUN_RULE,
    SERVICE_UPSERT,
    TaskStatus,
)
from .store import TaskDefinition, TaskManager, as_aware

UPSERT_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_ID): cv.string,
        probatio.Optional(ATTR_TITLE): cv.string,
        probatio.Required(ATTR_DUE): cv.datetime,
        probatio.Required(ATTR_ACTION): cv.entity_id,
        probatio.Optional(ATTR_PARAMS): dict,
        probatio.Optional(ATTR_EXPIRES): probatio.Any(EXPIRES_ALWAYS, cv.datetime),
        probatio.Optional(ATTR_RULE_ID): cv.string,
    }
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

RUN_RULE_SCHEMA = probatio.Schema({probatio.Required(ATTR_RULE_ID): cv.string})


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


async def _async_upsert(call: ServiceCall) -> ServiceResponse:
    manager, prefix = _get_manager(call.hass)
    data = call.data

    action: str = data[ATTR_ACTION]
    if not action.startswith(prefix):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="action_not_allowed",
            translation_placeholders={"action": action, "prefix": prefix},
        )
    expires = data.get(ATTR_EXPIRES)
    if isinstance(expires, datetime) and as_aware(expires) < as_aware(data[ATTR_DUE]):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="expires_before_due"
        )

    task, result = await manager.async_upsert(
        TaskDefinition(
            id=data[ATTR_ID],
            due=data[ATTR_DUE],
            action=action,
            title=data.get(ATTR_TITLE, ""),
            params=data.get(ATTR_PARAMS),
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


async def _async_run_rule(call: ServiceCall) -> ServiceResponse:
    _get_manager(call.hass)
    entry = call.hass.config_entries.async_loaded_entries(DOMAIN)[0]
    outcome = await entry.runtime_data.rules.async_run_now(call.data[ATTR_RULE_ID])
    if outcome is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_rule",
            translation_placeholders={"rule_id": call.data[ATTR_RULE_ID]},
        )
    if not call.return_response:
        return None
    ok, reason = outcome
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
    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_RULE,
        _async_run_rule,
        schema=RUN_RULE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
