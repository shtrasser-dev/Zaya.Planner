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
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_ACTION_PREFIX,
    CONF_CALENDARS,
    CONF_CANCEL_TAGS,
    CONF_CONDITIONS,
    CONF_EDGE,
    CONF_FILTER_TAGS,
    CONF_KEY_SIGN,
    CONF_KEYS,
    CONF_KIND,
    CONF_LATENESS_HOURS,
    CONF_MOMENT_CALENDARS,
    CONF_NAME,
    CONF_NOTIFY_ACTION,
    CONF_OFFSET,
    CONF_OFFSET_KEY,
    CONF_RETENTION_DAYS,
    CONF_SOURCES,
    CONF_TAGS,
    CONF_WHICH,
    DEFAULT_ACTION_PREFIX,
    DEFAULT_LATENESS_HOURS,
    DEFAULT_NOTIFY_ACTION,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    KIND_CALENDARS,
    KIND_MOMENTS,
    SUBENTRY_MOMENT,
    WHICH_EVERY,
    WHICH_FIRST_OF_DAY,
)
from .description import Dictionary, Filter, norm

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_LATENESS_HOURS: DEFAULT_LATENESS_HOURS,
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
    MINOR_VERSION = 3

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
        """Moments are added as subentries (“Add moment”)."""
        return {SUBENTRY_MOMENT: MomentSubentryFlow}


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


# ---------------------------------------------------------------- moments


def _tag_selector(entry: ConfigEntry) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=tag, label=f"#{tag}")
                for tag in entry.options.get(CONF_TAGS, [])
            ],
            multiple=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _choice(options: list[str], key: str) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=options, translation_key=key, mode=SelectSelectorMode.LIST)
    )


def _minutes() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=-1440, max=1440, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min"
        )
    )


def _calendar_moment_schema(entry: ConfigEntry) -> probatio.Schema:
    """A moment from events: which events, which edge, how much before or after."""
    return probatio.Schema(
        {
            probatio.Required(CONF_NAME): TextSelector(),
            probatio.Optional(CONF_MOMENT_CALENDARS): EntitySelector(
                EntitySelectorConfig(
                    domain="calendar",
                    multiple=True,
                    include_entities=list(entry.options.get(CONF_CALENDARS, [])),
                )
            ),
            probatio.Optional(CONF_FILTER_TAGS): _tag_selector(entry),
            probatio.Optional(CONF_CONDITIONS): TextSelector(TextSelectorConfig(multiline=True)),
            probatio.Required(CONF_EDGE, default="start"): _choice(["start", "end"], "edge"),
            probatio.Optional(CONF_OFFSET_KEY): SelectSelector(
                SelectSelectorConfig(
                    options=list(entry.options.get(CONF_KEYS, [])),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            probatio.Required(CONF_KEY_SIGN, default="minus"): _choice(
                ["minus", "plus"], "key_sign"
            ),
            probatio.Required(CONF_OFFSET, default=0): _minutes(),
            probatio.Required(CONF_WHICH, default=WHICH_FIRST_OF_DAY): _choice(
                [WHICH_FIRST_OF_DAY, WHICH_EVERY], "which"
            ),
            probatio.Optional(CONF_CANCEL_TAGS): _tag_selector(entry),
        }
    )


def _earliest_moment_schema() -> probatio.Schema:
    """A moment from moments: the earliest of timestamp sensors, plus an offset."""
    return probatio.Schema(
        {
            probatio.Required(CONF_NAME): TextSelector(),
            probatio.Required(CONF_SOURCES): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="timestamp", multiple=True)
            ),
            probatio.Required(CONF_OFFSET, default=0): _minutes(),
        }
    )


def _calendar_moment_errors(entry: ConfigEntry, data: dict[str, Any]) -> dict[str, str]:
    """Words of the conditions must be in the dictionary."""
    conditions = Filter.parse(data.get(CONF_CONDITIONS, ""))
    dictionary = Dictionary(entry.options.get(CONF_TAGS, []), entry.options.get(CONF_KEYS, []))
    return {CONF_CONDITIONS: "unknown_words"} if conditions.unknown_words(dictionary) else {}


def _moment_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    data = {**data, CONF_KIND: kind, CONF_NAME: data[CONF_NAME].strip()}
    data[CONF_OFFSET] = int(data.get(CONF_OFFSET) or 0)
    return data


class MomentSubentryFlow(ConfigSubentryFlow):
    """Add or change a moment."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose what the moment comes from."""
        return self.async_show_menu(step_id="user", menu_options=[KIND_CALENDARS, KIND_MOMENTS])

    async def async_step_calendars(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """A moment from calendar events."""
        entry = self._get_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _calendar_moment_errors(entry, user_input)
            if not errors:
                data = _moment_data(KIND_CALENDARS, user_input)
                return self.async_create_entry(title=data[CONF_NAME], data=data)
        return self.async_show_form(
            step_id=KIND_CALENDARS,
            data_schema=self.add_suggested_values_to_schema(
                _calendar_moment_schema(entry), user_input or {}
            ),
            errors=errors,
        )

    async def async_step_moments(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """The earliest of other moments."""
        if user_input is not None:
            data = _moment_data(KIND_MOMENTS, user_input)
            return self.async_create_entry(title=data[CONF_NAME], data=data)
        return self.async_show_form(step_id=KIND_MOMENTS, data_schema=_earliest_moment_schema())

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a moment (of the same kind)."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        kind = subentry.data.get(CONF_KIND, KIND_CALENDARS)
        errors: dict[str, str] = {}
        if user_input is not None:
            if kind == KIND_CALENDARS:
                errors = _calendar_moment_errors(entry, user_input)
            if not errors:
                data = _moment_data(kind, user_input)
                return self.async_update_and_abort(
                    entry, subentry, title=data[CONF_NAME], data=data
                )
        schema = (
            _calendar_moment_schema(entry) if kind == KIND_CALENDARS else _earliest_moment_schema()
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or dict(subentry.data)
            ),
            errors=errors,
        )
