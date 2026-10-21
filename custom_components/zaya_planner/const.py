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

DEFAULT_ACTION_PREFIX: Final = "script.plan_"
DEFAULT_LATENESS_HOURS: Final = 5
DEFAULT_RETENTION_DAYS: Final = 3

# How often finished tasks older than the retention period are removed
CLEANUP_INTERVAL: Final = timedelta(hours=1)

# Length of a task's event on the calendar (a task happens at a moment)
EVENT_DURATION: Final = timedelta(minutes=1)

# Value of `expires` meaning "run no matter how late" (e.g. turning things off)
EXPIRES_ALWAYS: Final = "always"

# Services
SERVICE_UPSERT: Final = "upsert"
SERVICE_REMOVE: Final = "remove"
SERVICE_LIST: Final = "list"

ATTR_ID: Final = "id"
ATTR_TITLE: Final = "title"
ATTR_DUE: Final = "due"
ATTR_ACTION: Final = "action"
ATTR_PARAMS: Final = "params"
ATTR_EXPIRES: Final = "expires"
ATTR_RULE_ID: Final = "rule_id"
ATTR_STATUS: Final = "status"
ATTR_TASKS: Final = "tasks"

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
