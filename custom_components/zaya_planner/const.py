"""Constants for Zaya Planner."""

from datetime import timedelta
from enum import StrEnum
from typing import Final

DOMAIN: Final = "zaya_planner"

STORAGE_KEY: Final = f"{DOMAIN}.plans"
STORAGE_VERSION: Final = 1

# Options (set in the integration's "Configure" dialog)
CONF_ACTION_PREFIX: Final = "action_prefix"
CONF_LATENESS_HOURS: Final = "lateness_hours"
CONF_FIX_MINUTES: Final = "fix_minutes"
CONF_RETENTION_DAYS: Final = "retention_days"
CONF_NOTIFY_ACTION: Final = "notify_action"

# Scripts that may be steps of a plan (planning modules are script.plan_*)
DEFAULT_ACTION_PREFIX: Final = "script.step_"
# The first step of a plan is not run if it is later than this
DEFAULT_LATENESS_HOURS: Final = 5
# A plan is frozen no sooner than this after it last changed: time to undo a mistake
DEFAULT_FIX_MINUTES: Final = 10
DEFAULT_RETENTION_DAYS: Final = 3
# Empty: a persistent notification in Home Assistant
DEFAULT_NOTIFY_ACTION: Final = ""

# How often finished plans older than the retention period are removed
CLEANUP_INTERVAL: Final = timedelta(hours=1)

# Executor: pauses between attempts of a step (the last one repeats)
RETRY_DELAYS: Final = (
    timedelta(minutes=1),
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=5),
)
# How long a step's script (or failure instructions) may run
SCRIPT_TIMEOUT: Final = timedelta(minutes=2)
# Notify when a step keeps failing this long
ERROR_NOTIFY_AFTER: Final = timedelta(minutes=10)
# Steps of a draft are kept this long, waiting for set_plan
DRAFT_LIFETIME: Final = timedelta(minutes=10)

# Events
EVENT_PLAN_DONE: Final = f"{DOMAIN}_plan_done"
EVENT_PLAN_FAILED: Final = f"{DOMAIN}_plan_failed"

# A plan with one step is shown on the calendar this long
EVENT_DURATION: Final = timedelta(minutes=1)

# Services
SERVICE_ADD_STEP: Final = "add_step"
SERVICE_SET_PLAN: Final = "set_plan"
SERVICE_CANCEL_PLAN: Final = "cancel_plan"
SERVICE_GET_PLANS: Final = "get_plans"
SERVICE_ENSURE_STATE: Final = "ensure_state"
SERVICE_FIND_EVENTS: Final = "find_events"

# Built-in action a step may run besides scripts: turn on/off and confirm
BUILTIN_ACTIONS: Final = frozenset({f"{DOMAIN}.{SERVICE_ENSURE_STATE}"})
# How long ensure_state waits for a device to confirm
ENSURE_STATE_TIMEOUT: Final = timedelta(seconds=20)

ATTR_OWNER: Final = "owner"
ATTR_SUMMARY: Final = "summary"
ATTR_STEPS: Final = "steps"
ATTR_AT: Final = "at"
# Shortcut for the built-in action: turn these devices on/off and confirm
ATTR_DEVICES: Final = "devices"
ATTR_STATE: Final = "state"
ATTR_ACTION: Final = "action"
ATTR_DATA: Final = "data"
ATTR_FREEZE_AT: Final = "freeze_at"
ATTR_FREEZE_BEFORE: Final = "freeze_before"
ATTR_ON_FAILURE: Final = "on_failure"
ATTR_FORCE: Final = "force"
# How a plan is shown on the calendar: one event from first to last step, or one per step
ATTR_DISPLAY: Final = "display"
DISPLAY_SPAN: Final = "span"
DISPLAY_STEPS: Final = "steps"
# Where the time of a step comes from: a date and time (at), the time an entity
# shows (at_entity), or the next event of the chosen calendars that fits a
# filter (event, minus the duration in the attribute `subtract`); then moved by
# `offset` minutes (minus: earlier)
ATTR_AT_ENTITY: Final = "at_entity"
ATTR_EVENT: Final = "event"
ATTR_OFFSET: Final = "offset"
ATTR_STATUS: Final = "status"
ATTR_PLANS: Final = "plans"
ATTR_ENTITY_ID: Final = "entity_id"

# Logbook entries are written under this name
LOGBOOK_NAME: Final = "Zaya Planner"

# Signal sent (with the entry id) whenever the plans change
SIGNAL_PLANS_UPDATED: Final = f"{DOMAIN}_plans_updated"


class PlanStatus(StrEnum):
    """Status of a plan."""

    # Its owner may change or cancel it
    PLANNED = "planned"
    # Nobody but a person may change or cancel it
    FROZEN = "frozen"
    # Its first step is due; the others wait for their turn
    RUNNING = "running"
    DONE = "done"
    # A step was not done: the rest was not run
    FAILED = "failed"
    CANCELLED = "cancelled"


# A plan is “locked” once frozen: its owner cannot change it any more
LOCKED_STATUSES: Final = frozenset({PlanStatus.FROZEN, PlanStatus.RUNNING})
OPEN_STATUSES: Final = frozenset({PlanStatus.PLANNED, *LOCKED_STATUSES})


class StepStatus(StrEnum):
    """Status of a step of a plan."""

    # Not run yet: the plan has not started
    PLANNED = "planned"
    # The plan is running; the step waits for its turn
    WAITING = "waiting"
    RUNNING = "running"
    # Failed, will be tried again
    RETRYING = "retrying"
    DONE = "done"
    # Not done: retries ran out, or it was too late
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlanResult(StrEnum):
    """What `set_plan` / `cancel_plan` did."""

    CREATED = "created"
    UPDATED = "updated"
    # The same plan is there already
    UNCHANGED = "unchanged"
    CANCELLED = "cancelled"
    # The plan starts before the owner's last frozen or running plan ends
    OVERLAPS = "overlaps"
    # It would change or cancel a frozen plan
    FROZEN = "frozen"
    # The first step is in the past: nothing is planned
    PAST = "past"


# Fired when rules should plan again (device trigger “time to plan”): Home
# Assistant started, automations were saved, events of the chosen calendars
# changed, a plan ended, and every TIME_TO_PLAN_INTERVAL as a safety net
EVENT_TIME_TO_PLAN: Final = f"{DOMAIN}_time_to_plan"
TIME_TO_PLAN_INTERVAL: Final = timedelta(minutes=15)


# ---------------------------------------------------------------- calendars and dictionary

# Options: the calendars that are read, and the user's tags and keys
CONF_CALENDARS: Final = "calendars"
CONF_TAGS: Final = "tags"
CONF_KEYS: Final = "keys"

# Events of the chosen calendars are checked this far ahead, and read again this
# often (they are also read again whenever a chosen calendar changes)
EVENTS_HORIZON: Final = timedelta(days=8)
EVENTS_REFRESH_INTERVAL: Final = timedelta(minutes=5)
SIGNAL_EVENTS_UPDATED: Final = f"{DOMAIN}_events_updated"
# A timed event longer than this is almost always a wrong date
LONGEST_TIMED_EVENT: Final = timedelta(hours=24)

ATTR_CALENDARS: Final = "calendars"
ATTR_FILTER: Final = "filter"
ATTR_START: Final = "start"
ATTR_END: Final = "end"
ATTR_FIRST_OF_DAY: Final = "first_of_day"
# Only events whose time (start, or start minus the attribute) has not come yet
ATTR_ONLY_FUTURE: Final = "only_future"
# The time of an event is its start minus the duration in this attribute
ATTR_SUBTRACT: Final = "subtract"
ATTR_EVENTS: Final = "events"
