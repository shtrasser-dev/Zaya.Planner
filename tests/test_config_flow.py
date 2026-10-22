"""Tests of adding and configuring Zaya Planner."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaya_planner.const import (
    CONF_ACTION_PREFIX,
    CONF_CALENDARS,
    CONF_KEYS,
    CONF_TAGS,
    CONF_LATENESS_HOURS,
    CONF_NOTIFY_ACTION,
    CONF_PEOPLE,
    CONF_RETENTION_DAYS,
    DEFAULT_ACTION_PREFIX,
    DEFAULT_LATENESS_HOURS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


async def test_user_flow(hass: HomeAssistant) -> None:
    """The integration is added with default settings."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Zaya Planner"
    assert result["options"] == {
        CONF_LATENESS_HOURS: DEFAULT_LATENESS_HOURS,
        CONF_ACTION_PREFIX: DEFAULT_ACTION_PREFIX,
        CONF_RETENTION_DAYS: DEFAULT_RETENTION_DAYS,
        CONF_NOTIFY_ACTION: "",
        CONF_PEOPLE: [],
    }


async def test_single_instance(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Only one planner per Home Assistant."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Settings are changed; the prefix must name scripts."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LATENESS_HOURS: 2,
            CONF_ACTION_PREFIX: "light.",
            CONF_RETENTION_DAYS: 7,
            CONF_NOTIFY_ACTION: "notify",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        CONF_ACTION_PREFIX: "invalid_prefix",
        CONF_NOTIFY_ACTION: "invalid_action",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LATENESS_HOURS: 2,
            CONF_ACTION_PREFIX: " script.task_ ",
            CONF_RETENTION_DAYS: 7.0,
            CONF_NOTIFY_ACTION: " notify.mobile_app_phone ",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_entry.options == {
        CONF_LATENESS_HOURS: 2,
        CONF_ACTION_PREFIX: "script.task_",
        CONF_RETENTION_DAYS: 7,
        CONF_NOTIFY_ACTION: "notify.mobile_app_phone",
        CONF_PEOPLE: [],
        CONF_CALENDARS: [],
        CONF_TAGS: [],
        CONF_KEYS: [],
    }


async def test_options_dictionary(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Tags are kept without “#”, each once; tags and keys must be words."""
    base = {
        CONF_LATENESS_HOURS: 5,
        CONF_ACTION_PREFIX: "script.plan_",
        CONF_RETENTION_DAYS: 3,
        CONF_CALENDARS: ["calendar.masha"],
    }
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**base, CONF_TAGS: ["#leaf", "1st"], CONF_KEYS: ["В пути:"]},
    )
    assert result["errors"] == {CONF_TAGS: "invalid_tag", CONF_KEYS: "invalid_key"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**base, CONF_TAGS: [" #leaf", "Leaf", "отвезти", ""], CONF_KEYS: [" В пути ", "в пути"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_entry.options[CONF_CALENDARS] == ["calendar.masha"]
    assert setup_entry.options[CONF_TAGS] == ["leaf", "отвезти"]
    assert setup_entry.options[CONF_KEYS] == ["В пути"]


async def test_rules_from_older_version_are_removed(hass: HomeAssistant) -> None:
    """Rules set up in the integration (1.1) are dropped: rules are automations now."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zaya Planner",
        data={},
        options={},
        version=1,
        minor_version=1,
        subentries_data=[
            ConfigSubentryData(
                data={"rule_id": "floor"},
                subentry_type="rule",
                title="Floor",
                unique_id="floor",
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2
    assert dict(entry.subentries) == {}
