"""Texts of the logbook, notifications and plan descriptions.

They are kept under `common` in the translation files and loaded once, when
Zaya Planner is set up, in the language of Home Assistant (English where a
text has no translation).
"""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN

DATA_MESSAGES = f"{DOMAIN}_messages"
CATEGORY = "common"


async def async_load_messages(hass: HomeAssistant) -> None:
    """Load the texts in the language of Home Assistant, over the English ones."""
    texts: dict[str, str] = {}
    for language in dict.fromkeys(("en", hass.config.language)):
        texts |= await async_get_translations(hass, language, CATEGORY, [DOMAIN])
    prefix = f"component.{DOMAIN}.{CATEGORY}."
    hass.data[DATA_MESSAGES] = {
        key.removeprefix(prefix): text for key, text in texts.items() if key.startswith(prefix)
    }


def message(hass: HomeAssistant, key: str, **kwargs: object) -> str:
    """Return a text in the language of Home Assistant."""
    return hass.data.get(DATA_MESSAGES, {}).get(key, key).format(**kwargs)
