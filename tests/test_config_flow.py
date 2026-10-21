"""Tests of adding and configuring Zaya Planner."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaya_planner.const import (
    CONF_ACTION_PREFIX,
    CONF_LATENESS_HOURS,
    CONF_RETENTION_DAYS,
    DEFAULT_ACTION_PREFIX,
    DEFAULT_LATENESS_HOURS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
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
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ACTION_PREFIX: "invalid_prefix"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LATENESS_HOURS: 2,
            CONF_ACTION_PREFIX: " script.task_ ",
            CONF_RETENTION_DAYS: 7.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_entry.options == {
        CONF_LATENESS_HOURS: 2,
        CONF_ACTION_PREFIX: "script.task_",
        CONF_RETENTION_DAYS: 7,
    }
