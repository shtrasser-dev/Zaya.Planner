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
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)

from .const import (
    CONF_ACTION_PREFIX,
    CONF_ENABLED,
    CONF_LATENESS_HOURS,
    CONF_NOTIFY_ACTION,
    CONF_PARAMS,
    CONF_PLANNER,
    CONF_REPEAT,
    CONF_RETENTION_DAYS,
    CONF_RULE_ID,
    CONF_TIME,
    DEFAULT_ACTION_PREFIX,
    DEFAULT_LATENESS_HOURS,
    DEFAULT_NOTIFY_ACTION,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    SUBENTRY_RULE,
    Repeat,
)

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_LATENESS_HOURS: DEFAULT_LATENESS_HOURS,
    CONF_ACTION_PREFIX: DEFAULT_ACTION_PREFIX,
    CONF_RETENTION_DAYS: DEFAULT_RETENTION_DAYS,
    CONF_NOTIFY_ACTION: DEFAULT_NOTIFY_ACTION,
}

# domain.action, e.g. notify.mobile_app_phone or script.alert
ACTION_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


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
        """Rules are added and edited as subentries."""
        return {SUBENTRY_RULE: RuleSubentryFlow}


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
            # Only scripts may be run, and only a named subset of them
            if not prefix.startswith("script.") or prefix == "script.":
                errors[CONF_ACTION_PREFIX] = "invalid_prefix"
            if not errors:
                return self.async_create_entry(data=user_input)
        options = {**DEFAULT_OPTIONS, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(options), errors=errors
        )


CONF_TITLE = "title"
# Rule ids end up in tasks and automations: keep them simple
RULE_ID_RE = re.compile(r"^[a-z0-9_]+$")

RULE_DEFAULTS: dict[str, Any] = {
    CONF_REPEAT: Repeat.DAILY,
    CONF_TIME: "20:00:00",
    CONF_ENABLED: True,
}


def _rule_schema(*, with_id: bool) -> probatio.Schema:
    fields: dict[Any, Any] = {}
    if with_id:
        fields[probatio.Required(CONF_RULE_ID)] = TextSelector()
    fields.update(
        {
            probatio.Required(CONF_TITLE): TextSelector(),
            probatio.Required(CONF_PLANNER): EntitySelector(
                EntitySelectorConfig(domain="script")
            ),
            probatio.Optional(CONF_PARAMS): ObjectSelector(),
            probatio.Required(CONF_REPEAT): SelectSelector(
                SelectSelectorConfig(
                    options=[repeat.value for repeat in Repeat],
                    translation_key="repeat",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            probatio.Required(CONF_TIME): TimeSelector(),
            probatio.Required(CONF_ENABLED): BooleanSelector(),
        }
    )
    return probatio.Schema(fields)


class RuleSubentryFlow(ConfigSubentryFlow):
    """Add or change a rule."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a rule."""
        errors: dict[str, str] = {}
        if user_input is not None:
            rule_id = user_input[CONF_RULE_ID].strip()
            if not RULE_ID_RE.match(rule_id):
                errors[CONF_RULE_ID] = "invalid_rule_id"
            elif any(
                sub.data.get(CONF_RULE_ID) == rule_id
                for sub in self._get_entry().subentries.values()
            ):
                errors[CONF_RULE_ID] = "rule_id_exists"
            errors.update(self._validate(user_input))
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_TITLE].strip(),
                    data={CONF_RULE_ID: rule_id, **self._rule_data(user_input)},
                    unique_id=rule_id,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _rule_schema(with_id=True), user_input or RULE_DEFAULTS
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a rule (its id stays)."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate(user_input)
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=user_input[CONF_TITLE].strip(),
                    data={
                        CONF_RULE_ID: subentry.data[CONF_RULE_ID],
                        **self._rule_data(user_input),
                    },
                )
        suggested = user_input or {CONF_TITLE: subentry.title, **subentry.data}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _rule_schema(with_id=False), suggested
            ),
            errors=errors,
            description_placeholders={"rule_id": subentry.data[CONF_RULE_ID]},
        )

    def _validate(self, user_input: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        prefix = self._get_entry().options.get(CONF_ACTION_PREFIX, DEFAULT_ACTION_PREFIX)
        if not user_input[CONF_PLANNER].startswith(prefix):
            errors[CONF_PLANNER] = "planner_not_allowed"
        if not isinstance(user_input.get(CONF_PARAMS) or {}, dict):
            errors[CONF_PARAMS] = "invalid_params"
        return errors

    @staticmethod
    def _rule_data(user_input: dict[str, Any]) -> dict[str, Any]:
        return {
            CONF_PLANNER: user_input[CONF_PLANNER],
            CONF_PARAMS: dict(user_input.get(CONF_PARAMS) or {}),
            CONF_REPEAT: user_input[CONF_REPEAT],
            CONF_TIME: user_input[CONF_TIME],
            CONF_ENABLED: user_input[CONF_ENABLED],
        }
