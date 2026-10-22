"""Config flow for Zaya Planner."""

from __future__ import annotations

import re
from typing import Any, override

import probatio

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
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
    CONF_CAR_NAME,
    CONF_CHARGER,
    CONF_KEYS,
    CONF_LATENESS_HOURS,
    CONF_PEOPLE,
    CONF_NOTIFY_ACTION,
    CONF_RETENTION_DAYS,
    CONF_TAGS,
    DEFAULT_ACTION_PREFIX,
    DEFAULT_LATENESS_HOURS,
    DEFAULT_NOTIFY_ACTION,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    SUBENTRY_CAR,
)
from .description import norm

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_LATENESS_HOURS: DEFAULT_LATENESS_HOURS,
    CONF_ACTION_PREFIX: DEFAULT_ACTION_PREFIX,
    CONF_RETENTION_DAYS: DEFAULT_RETENTION_DAYS,
    CONF_NOTIFY_ACTION: DEFAULT_NOTIFY_ACTION,
    CONF_PEOPLE: [],
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
                CONF_ACTION_PREFIX, default=options[CONF_ACTION_PREFIX]
            ): TextSelector(),
            probatio.Required(
                CONF_RETENTION_DAYS, default=options[CONF_RETENTION_DAYS]
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=30, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="d"
                )
            ),
            # Who takes part in the family schedule
            probatio.Required(CONF_PEOPLE, default=options[CONF_PEOPLE]): EntitySelector(
                EntitySelectorConfig(domain="person", multiple=True)
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
    MINOR_VERSION = 2

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

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Cars are added as subentries (“Add car”)."""
        return {SUBENTRY_CAR: CarSubentryFlow}



class ZayaPlannerOptionsFlow(OptionsFlow):
    """Change the settings of Zaya Planner."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_RETENTION_DAYS] = int(user_input[CONF_RETENTION_DAYS])
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



def _car_schema() -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(CONF_CAR_NAME): TextSelector(),
            probatio.Optional(CONF_CHARGER): EntitySelector(
                EntitySelectorConfig(domain=["switch", "input_boolean"])
            ),
        }
    )


class CarSubentryFlow(ConfigSubentryFlow):
    """Add or change a car."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a car."""
        if user_input is not None:
            name = user_input[CONF_CAR_NAME].strip()
            return self.async_create_entry(
                title=name,
                data={CONF_CAR_NAME: name, CONF_CHARGER: user_input.get(CONF_CHARGER)},
            )
        return self.async_show_form(step_id="user", data_schema=_car_schema())

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a car."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            name = user_input[CONF_CAR_NAME].strip()
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=name,
                data={CONF_CAR_NAME: name, CONF_CHARGER: user_input.get(CONF_CHARGER)},
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _car_schema(), dict(subentry.data)
            ),
        )
