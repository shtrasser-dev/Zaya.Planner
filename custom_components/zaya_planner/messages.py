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
        "reason_unavailable": "not connected",
        "reason_not_confirmed": "did not confirm the command",
        "reason_not_allowed": "{action} is not allowed: only scripts starting with {prefix}",
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
        "label_devices": "Devices",
        "label_state": "State",
        "window_on": "on",
        "window_off": "off",
        "notify_title_event_errors": "Zaya Planner: errors in events",
        "event_where": "{summary} ({calendar}, {day})",
        "event_unknown_tag": "{where}: tag #{value} is not in the dictionary",
        "event_empty_value": "{where}: “{value}” has no value",
        "event_too_long": "{where}: the event is longer than a day, check its dates",
        "event_no_calendar": "Calendar {calendar} is not found",
        "event_no_value": "{where}: no “{value}” for the moment “{moment}”",
        "event_not_duration": "{where}: “{value}” is not a duration (moment “{moment}”), write it as 00:40:00",
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
        "reason_unavailable": "нет связи",
        "reason_not_confirmed": "не подтвердило команду",
        "reason_not_allowed": "{action} не разрешён: только скрипты, начинающиеся с {prefix}",
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
        "label_devices": "Устройства",
        "label_state": "Состояние",
        "window_on": "вкл",
        "window_off": "выкл",
        "notify_title_event_errors": "Zaya Planner: ошибки в событиях",
        "event_where": "{summary} ({calendar}, {day})",
        "event_unknown_tag": "{where}: тега #{value} нет в словаре",
        "event_empty_value": "{where}: у «{value}» нет значения",
        "event_too_long": "{where}: событие длиннее суток, проверьте даты",
        "event_no_calendar": "Календарь {calendar} не найден",
        "event_no_value": "{where}: нет «{value}» для момента «{moment}»",
        "event_not_duration": "{where}: «{value}» — не длительность (момент «{moment}»), пишите как 00:40:00",
    },
}


def language(hass: HomeAssistant) -> str:
    """Return the language of Home Assistant if there are texts in it, else English."""
    code = (hass.config.language or "en").split("-")[0]
    return code if code in _MESSAGES else "en"


def message(hass: HomeAssistant, key: str, **kwargs: object) -> str:
    """Return the text in the language of Home Assistant (English by default)."""
    texts = _MESSAGES[language(hass)]
    return texts.get(key, _MESSAGES["en"][key]).format(**kwargs)
