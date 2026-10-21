"""Config flow for Zaya Planner."""

from __future__ import annotations

from typing import Any, override

import probatio

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_ACTION_PREFIX,
    CONF_LATENESS_HOURS,
    CONF_RETENTION_DAYS,
    DEFAULT_ACTION_PREFIX,
    DEFAULT_LATENESS_HOURS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_LATENESS_HOURS: DEFAULT_LATENESS_HOURS,
    CONF_ACTION_PREFIX: DEFAULT_ACTION_PREFIX,
    CONF_RETENTION_DAYS: DEFAULT_RETENTION_DAYS,
}


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
        }
    )


class ZayaPlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add Zaya Planner (one per Home Assistant)."""

    VERSION = 1

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


class ZayaPlannerOptionsFlow(OptionsFlowWithReload):
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
            # Only scripts may be run, and only a named subset of them
            if not prefix.startswith("script.") or prefix == "script.":
                errors[CONF_ACTION_PREFIX] = "invalid_prefix"
            else:
                return self.async_create_entry(data=user_input)
        options = {**DEFAULT_OPTIONS, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(options), errors=errors
        )
