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
# Due time taken from an entity (sun, a helper, a timestamp sensor), plus an offset
ATTR_DUE_ENTITY: Final = "due_entity"
ATTR_OFFSET: Final = "offset"
# Shortcut for the built-in action: turn these devices on/off and confirm
ATTR_DEVICES: Final = "devices"
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
    # The task is already planned exactly so
    UNCHANGED = "unchanged"
    # An open task with this id is already due: it belongs to the executor
    LOCKED = "locked"
    # A rule planned a task in the past: rules plan only the future
    PAST = "past"



# ---------------------------------------------------------------- household

# Options: who takes part (person entities from Settings → People)
CONF_PEOPLE: Final = "people"

# Cars are config subentries of this type
SUBENTRY_CAR: Final = "car"
CONF_CAR_NAME: Final = "name"
CONF_CHARGER: Final = "charger"

# Settings of people and cars (their device entities), saved right away
SETTINGS_STORAGE_KEY: Final = f"{DOMAIN}.settings"
# One iCalendar file per person
CALENDAR_STORAGE_PATH: Final = ".storage/" + DOMAIN + ".calendar.{key}.ics"

DEFAULT_GETTING_READY: Final = 60
DEFAULT_CHARGE_MARGIN: Final = 30

SIGNAL_SETTINGS_UPDATED: Final = f"{DOMAIN}_settings_updated"

# Actions on person calendars
SERVICE_ADD_EVENT: Final = "add_event"
SERVICE_SET_EVENT_DETAILS: Final = "set_event_details"
SERVICE_GET_EVENTS: Final = "get_events"

ATTR_KIND: Final = "kind"
ATTR_TRIP: Final = "trip"
ATTR_RIDERS: Final = "riders"
ATTR_DRIVER: Final = "driver"
ATTR_CAR: Final = "car"
ATTR_TRAVEL_MINUTES: Final = "travel_minutes"


class EventKind(StrEnum):
    """What an event in a person calendar is."""

    EVENT = "event"
    WAKE = "wake"
    CANCEL = "cancel"


class TripKind(StrEnum):
    """Whether an event needs a trip, and to which end of it."""

    NONE = "none"
    # Departure to arrive by the start
    DROP = "drop"
    # Departure to arrive by the end
    PICKUP = "pickup"
    BOTH = "both"

# House plan: how far ahead, how often it is worked out again (it also follows
# every change of calendars and settings)
PLAN_HORIZON: Final = timedelta(days=8)
PLAN_REFRESH_INTERVAL: Final = timedelta(minutes=5)
SIGNAL_CALENDARS_UPDATED: Final = f"{DOMAIN}_calendars_updated"
SIGNAL_PLAN_UPDATED: Final = f"{DOMAIN}_plan_updated"
# Fired when departures or wake-ups change (device trigger “plan changed”)
EVENT_PLAN_CHANGED: Final = f"{DOMAIN}_plan_changed"
DEFAULT_ALARM_TIME: Final = "21:00:00"
