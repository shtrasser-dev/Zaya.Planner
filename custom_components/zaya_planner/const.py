"""Constants for Zaya Planner."""

from datetime import timedelta
from enum import StrEnum
from typing import Final

DOMAIN: Final = "zaya_planner"

STORAGE_KEY: Final = f"{DOMAIN}.tasks"
STORAGE_VERSION: Final = 1

# Options (set in the integration's "Configure" dialog)
CONF_ACTION_PREFIX: Final = "action_prefix"
CONF_LATENESS_HOURS: Final = "lateness_hours"
CONF_RETENTION_DAYS: Final = "retention_days"
CONF_NOTIFY_ACTION: Final = "notify_action"

DEFAULT_ACTION_PREFIX: Final = "script.plan_"
DEFAULT_LATENESS_HOURS: Final = 5
DEFAULT_RETENTION_DAYS: Final = 3
# Empty: a persistent notification in Home Assistant
DEFAULT_NOTIFY_ACTION: Final = ""

# How often finished tasks older than the retention period are removed
CLEANUP_INTERVAL: Final = timedelta(hours=1)

# Executor: pauses between attempts (the last one repeats)
RETRY_DELAYS: Final = (
    timedelta(minutes=1),
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=5),
)
# How long a task's script may run before the attempt counts as failed
SCRIPT_TIMEOUT: Final = timedelta(minutes=2)
# Notify when a task keeps failing this long
ERROR_NOTIFY_AFTER: Final = timedelta(minutes=10)

# Events
EVENT_TASK_DONE: Final = f"{DOMAIN}_task_done"
EVENT_TASK_FAILED: Final = f"{DOMAIN}_task_failed"

# Length of a task's event on the calendar (a task happens at a moment)
EVENT_DURATION: Final = timedelta(minutes=1)

# Value of `expires` meaning "run no matter how late" (e.g. turning things off)
EXPIRES_ALWAYS: Final = "always"

# Services
SERVICE_UPSERT: Final = "upsert"
SERVICE_REMOVE: Final = "remove"
SERVICE_LIST: Final = "list"
SERVICE_ENSURE_STATE: Final = "ensure_state"

# Built-in action a task may run besides scripts: turn on/off and confirm
BUILTIN_ACTIONS: Final = frozenset({f"{DOMAIN}.{SERVICE_ENSURE_STATE}"})
# How long ensure_state waits for a device to confirm
ENSURE_STATE_TIMEOUT: Final = timedelta(seconds=20)

ATTR_ID: Final = "id"
ATTR_TITLE: Final = "title"
ATTR_DUE: Final = "due"
ATTR_ACTION: Final = "action"
ATTR_PARAMS: Final = "params"
ATTR_EXPIRES: Final = "expires"
ATTR_RULE_ID: Final = "rule_id"
ATTR_STATUS: Final = "status"
ATTR_TASKS: Final = "tasks"
ATTR_ENTITY_ID: Final = "entity_id"
ATTR_STATE: Final = "state"

# Logbook entries are written under this name
LOGBOOK_NAME: Final = "Zaya Planner"

# Signal sent (with the entry id) whenever the task list changes
SIGNAL_TASKS_UPDATED: Final = f"{DOMAIN}_tasks_updated"


class TaskStatus(StrEnum):
    """Status of a scheduled task."""

    SCHEDULED = "scheduled"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


# A task is "open" until the executor (or the user) closes it
OPEN_STATUSES: Final = frozenset(
    {TaskStatus.SCHEDULED, TaskStatus.RUNNING, TaskStatus.ERROR}
)


class UpsertResult(StrEnum):
    """What `upsert` did with the task."""

    CREATED = "created"
    UPDATED = "updated"
    # An open task with this id is already due: it belongs to the executor
    LOCKED = "locked"
    # A rule planned a task in the past: rules plan only the future
    PAST = "past"

