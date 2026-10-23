"""Config flow for Zaya Planner."""

from __future__ import annotations

import re
from typing import Any, override

import probatio

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_ACTION_PREFIX,
    CONF_CALENDARS,
    CONF_FIX_MINUTES,
    CONF_KEYS,
    CONF_LATENESS_HOURS,
    CONF_NOTIFY_ACTION,
    CONF_RETENTION_DAYS,
    CONF_TAGS,
    DEFAULT_ACTION_PREFIX,
    DEFAULT_FIX_MINUTES,
    DEFAULT_LATENESS_HOURS,
    DEFAULT_NOTIFY_ACTION,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)
from .description import norm

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_LATENESS_HOURS: DEFAULT_LATENESS_HOURS,
    CONF_FIX_MINUTES: DEFAULT_FIX_MINUTES,
    CONF_ACTION_PREFIX: DEFAULT_ACTION_PREFIX,
    CONF_RETENTION_DAYS: DEFAULT_RETENTION_DAYS,
    CONF_NOTIFY_ACTION: DEFAULT_NOTIFY_ACTION,
    CONF_CALENDARS: [],
    CONF_TAGS: [],
    CONF_KEYS: [],
}

# domain.action, e.g. notify.mobile_app_phone or script.alert
ACTION_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
# A tag as it is written after “#”: a letter, then letters, digits, “_”, “-”
TAG_RE = re.compile(r"^[^\W\d_][\w-]*$")


def _words(values: list[str], strip: str = "") -> list[str]:
    """Return the words without spaces (and `strip` characters), each once."""
    words: dict[str, str] = {}
    for value in values:
        word = value.strip().lstrip(strip).strip()
        if word:
            words.setdefault(norm(word), word)
    return list(words.values())


def _options_schema(options: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(
                CONF_LATENESS_HOURS, default=options[CONF_LATENESS_HOURS]
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.5,
                    max=48,
                    step=0.5,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="h",
                )
            ),
            probatio.Required(
                CONF_FIX_MINUTES, default=options[CONF_FIX_MINUTES]
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=120, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min"
                )
            ),
            probatio.Required(
                CONF_ACTION_PREFIX, default=options[CONF_ACTION_PREFIX]
            ): TextSelector(),
            probatio.Required(
                CONF_RETENTION_DAYS, default=options[CONF_RETENTION_DAYS]
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=30, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="d"
                )
            ),
            # The calendars that are read, and the words of their descriptions
            probatio.Required(CONF_CALENDARS, default=options[CONF_CALENDARS]): EntitySelector(
                EntitySelectorConfig(domain="calendar", multiple=True)
            ),
            probatio.Required(CONF_TAGS, default=options[CONF_TAGS]): TextSelector(
                TextSelectorConfig(multiple=True)
            ),
            probatio.Required(CONF_KEYS, default=options[CONF_KEYS]): TextSelector(
                TextSelectorConfig(multiple=True)
            ),
            # Optional so that it can be cleared (back to a persistent notification)
            probatio.Optional(
                CONF_NOTIFY_ACTION,
                description={"suggested_value": options[CONF_NOTIFY_ACTION]},
            ): TextSelector(),
        }
    )


class ZayaPlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add Zaya Planner (one per Home Assistant)."""

    VERSION = 1
    # 1.2: rules moved out of the integration into automations
    # 1.3: people, cars and the house plan removed
    # 1.4: tasks became plans, moments removed
    MINOR_VERSION = 4

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding the integration."""
        if user_input is not None:
            return self.async_create_entry(
                title="Zaya Planner", data={}, options=dict(DEFAULT_OPTIONS)
            )
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> ZayaPlannerOptionsFlow:
        """Return the options flow."""
        return ZayaPlannerOptionsFlow()


class ZayaPlannerOptionsFlow(OptionsFlow):
    """Change the settings of Zaya Planner."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_RETENTION_DAYS] = int(user_input[CONF_RETENTION_DAYS])
            user_input[CONF_FIX_MINUTES] = int(user_input[CONF_FIX_MINUTES])
            prefix = user_input[CONF_ACTION_PREFIX].strip()
            user_input[CONF_ACTION_PREFIX] = prefix
            notify = user_input.get(CONF_NOTIFY_ACTION, "").strip()
            user_input[CONF_NOTIFY_ACTION] = notify
            if notify and not ACTION_RE.match(notify):
                errors[CONF_NOTIFY_ACTION] = "invalid_action"
            user_input[CONF_TAGS] = _words(user_input.get(CONF_TAGS, []), "#")
            user_input[CONF_KEYS] = _words(user_input.get(CONF_KEYS, []))
            if not all(TAG_RE.match(tag) for tag in user_input[CONF_TAGS]):
                errors[CONF_TAGS] = "invalid_tag"
            if any(":" in key or key.startswith("#") for key in user_input[CONF_KEYS]):
                errors[CONF_KEYS] = "invalid_key"
            # Only scripts may be run, and only a named subset of them
            if not prefix.startswith("script.") or prefix == "script.":
                errors[CONF_ACTION_PREFIX] = "invalid_prefix"
            if not errors:
                return self.async_create_entry(data=user_input)
        options = {**DEFAULT_OPTIONS, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(options), errors=errors
        )

