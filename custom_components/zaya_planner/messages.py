"""Logbook and notification texts.

Home Assistant translates only UI strings, so free-form messages are kept here
and picked by the language of the Home Assistant instance.
"""

from homeassistant.core import HomeAssistant

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "locked": "task “{title}” was not changed: it is already due ({due}) and belongs to the executor",
        "done": "task “{title}” done",
        "done_retry": "task “{title}” done on attempt {attempts}",
        "error": "task “{title}”: {reason}; next attempt at {next}",
        "failed": "task “{title}” failed: {reason}",
        "skipped": "task “{title}” skipped: {reason}",
        "notify_title_error": "Zaya Planner: task error",
        "notify_error": "“{title}”: {reason}. Retrying, next attempt at {next}.",
        "notify_title_failed": "Zaya Planner: task failed",
        "notify_title_skipped": "Zaya Planner: task skipped",
        "notify_closed": "“{title}”: {reason}",
        "reason_no_ok": "the script did not answer {{ok: true/false}}",
        "reason_timeout": "the script did not finish in {seconds} s",
        "reason_not_found": "script {action} not found",
        "reason_unknown": "no reason given",
        "reason_restart": "Home Assistant restarted during the attempt",
        "reason_late": "due {due}, now too late to run",
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
        "label_attempts": "Attempts",
    },
    "ru": {
        "locked": "задача «{title}» не изменена: её срок уже наступил ({due}), она у исполнителя",
        "done": "задача «{title}» выполнена",
        "done_retry": "задача «{title}» выполнена с {attempts}-й попытки",
        "error": "задача «{title}»: {reason}; следующая попытка в {next}",
        "failed": "задача «{title}» не выполнена: {reason}",
        "skipped": "задача «{title}» пропущена: {reason}",
        "notify_title_error": "Zaya Planner: ошибка задачи",
        "notify_error": "«{title}»: {reason}. Повторяю, следующая попытка в {next}.",
        "notify_title_failed": "Zaya Planner: задача не выполнена",
        "notify_title_skipped": "Zaya Planner: задача пропущена",
        "notify_closed": "«{title}»: {reason}",
        "reason_no_ok": "скрипт не ответил {{ok: true/false}}",
        "reason_timeout": "скрипт не завершился за {seconds} с",
        "reason_not_found": "скрипт {action} не найден",
        "reason_unknown": "причина не указана",
        "reason_restart": "Home Assistant перезапустился во время попытки",
        "reason_late": "срок {due}, выполнять уже поздно",
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
        "label_attempts": "Попыток",
    },
}


def message(hass: HomeAssistant, key: str, **kwargs: object) -> str:
    """Return the text in the language of Home Assistant (English by default)."""
    language = (hass.config.language or "en").split("-")[0]
    texts = _MESSAGES.get(language, _MESSAGES["en"])
    return texts.get(key, _MESSAGES["en"][key]).format(**kwargs)
