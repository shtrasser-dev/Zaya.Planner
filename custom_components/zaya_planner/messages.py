"""Logbook and notification texts.

Home Assistant translates only UI strings, so free-form messages are kept here
and picked by the language of the Home Assistant instance.
"""

from homeassistant.core import HomeAssistant

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "locked": "task “{title}” was not changed: it is already due ({due}) and belongs to the executor",
        "status_scheduled": "scheduled",
        "status_running": "running",
        "status_done": "done",
        "status_error": "error (retrying)",
        "status_failed": "failed",
        "status_skipped": "skipped",
        "status_cancelled": "cancelled",
        "label_status": "Status",
        "label_action": "Action",
        "label_params": "Parameters",
        "label_error": "Error",
    },
    "ru": {
        "locked": "задача «{title}» не изменена: её срок уже наступил ({due}), она у исполнителя",
        "status_scheduled": "запланирована",
        "status_running": "выполняется",
        "status_done": "выполнена",
        "status_error": "ошибка (повтор)",
        "status_failed": "не выполнена",
        "status_skipped": "пропущена",
        "status_cancelled": "отменена",
        "label_status": "Статус",
        "label_action": "Действие",
        "label_params": "Параметры",
        "label_error": "Ошибка",
    },
}


def message(hass: HomeAssistant, key: str, **kwargs: object) -> str:
    """Return the text in the language of Home Assistant (English by default)."""
    language = (hass.config.language or "en").split("-")[0]
    texts = _MESSAGES.get(language, _MESSAGES["en"])
    return texts.get(key, _MESSAGES["en"][key]).format(**kwargs)
