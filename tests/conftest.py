"""Fixtures for Zaya Planner tests."""

from __future__ import annotations

import ical.calendar_stream  # noqa: F401
import ical.event  # noqa: F401
import ical.store  # noqa: F401
import ical.timeline  # noqa: F401
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaya_planner.config_flow import DEFAULT_OPTIONS
from custom_components.zaya_planner.const import DOMAIN
from homeassistant.core import HomeAssistant

from .common import NOW

# The iCalendar library above is imported before any test freezes time: it
# remembers the date and time types it was imported with, and they must all be
# the real ones (importing parts of it while time is frozen mixes in fake ones)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom integrations in all tests."""


@pytest.fixture
def frozen_now(freezer):
    """Start every test at the same moment."""
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry with default options."""
    return MockConfigEntry(
        domain=DOMAIN, title="Zaya Planner", data={}, options=dict(DEFAULT_OPTIONS)
    )


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, frozen_now
) -> MockConfigEntry:
    """Set up Zaya Planner."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry

