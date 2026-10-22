"""Events of the chosen calendars: tags, attributes and what is wrong in them."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.calendar import CalendarEntity
from homeassistant.components.calendar.const import DATA_COMPONENT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .calls import async_notify
from .const import (
    CONF_CALENDARS,
    CONF_CANCEL_TAGS,
    CONF_CONDITIONS,
    CONF_EDGE,
    CONF_FILTER_TAGS,
    CONF_KEY_SIGN,
    CONF_KEYS,
    CONF_KIND,
    CONF_MOMENT_CALENDARS,
    CONF_NAME,
    CONF_OFFSET,
    CONF_OFFSET_KEY,
    CONF_TAGS,
    CONF_WHICH,
    KIND_CALENDARS,
    SUBENTRY_MOMENT,
    WHICH_FIRST_OF_DAY,
    EVENTS_HORIZON,
    EVENTS_REFRESH_INTERVAL,
    LONGEST_TIMED_EVENT,
    SIGNAL_EVENTS_UPDATED,
)
from .description import Dictionary, Filter, Reading, by_start, first_of_each_day
from .messages import message
from .moments import Moment, MomentSpec, calculate

_LOGGER = logging.getLogger(__name__)

# Changes of a calendar come in bursts (a series edited): read once they settle
REFRESH_COOLDOWN = 2


@dataclass(slots=True, frozen=True)
class FoundEvent:
    """An event of a chosen calendar with what its description says."""

    calendar: str
    summary: str
    # Local times, or dates for all-day events
    start: date | datetime
    end: date | datetime
    description: str
    reading: Reading

    def as_dict(self) -> dict[str, Any]:
        """Return the event for an action response."""
        return {
            "calendar": self.calendar,
            "summary": self.summary,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": not isinstance(self.start, datetime),
            "tags": list(self.reading.tags),
            "attributes": dict(self.reading.attributes),
            "description": self.description,
        }


@dataclass(slots=True, frozen=True)
class EventError:
    """Something wrong in an event (or a calendar) of the chosen calendars."""

    # unknown_tag, empty_value, too_long, no_calendar; of a moment: no_value, not_duration
    code: str
    calendar: str
    summary: str = ""
    day: date | None = None
    value: str = ""
    moment: str = ""

    @property
    def key(self) -> tuple[str, str, str, str]:
        """The same mistake in every event of a series is one error."""
        return (self.code, self.calendar, self.summary, self.value)


def moment_spec(data: Mapping[str, Any]) -> MomentSpec:
    """Return how a moment “from calendars” is worked out, from its subentry data."""
    conditions = Filter.parse(data.get(CONF_CONDITIONS, ""))
    return MomentSpec(
        name=data[CONF_NAME],
        calendars=tuple(data.get(CONF_MOMENT_CALENDARS) or ()),
        event_filter=Filter(
            tuple(data.get(CONF_FILTER_TAGS) or ()) + conditions.tags,
            conditions.equals,
            conditions.present,
        ),
        edge=data.get(CONF_EDGE, "start"),
        offset_key=data.get(CONF_OFFSET_KEY) or None,
        key_sign=1 if data.get(CONF_KEY_SIGN) == "plus" else -1,
        offset=timedelta(minutes=data.get(CONF_OFFSET) or 0),
        first_of_day=data.get(CONF_WHICH, WHICH_FIRST_OF_DAY) == WHICH_FIRST_OF_DAY,
        cancel_tags=tuple(data.get(CONF_CANCEL_TAGS) or ()),
    )


def _day(start: date | datetime) -> date:
    return start.date() if isinstance(start, datetime) else start


def _local(value: date | datetime) -> date | datetime:
    return dt_util.as_local(value) if isinstance(value, datetime) else value


class EventReader:
    """Reads the chosen calendars, keeps their errors, follows their changes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Take the calendars and the dictionary from the options."""
        self.hass = hass
        self.entry = entry
        self.calendars: list[str] = list(entry.options.get(CONF_CALENDARS, []))
        self.dictionary = Dictionary(
            entry.options.get(CONF_TAGS, []), entry.options.get(CONF_KEYS, [])
        )
        self.errors: list[EventError] = []
        # Moments “from calendars” by subentry id, and what they worked out
        self.specs: dict[str, MomentSpec] = {
            subentry_id: moment_spec(subentry.data)
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_MOMENT
            and subentry.data.get(CONF_KIND) == KIND_CALENDARS
        }
        self.moments: dict[str, list[Moment]] = {}
        self._subscribed: dict[str, tuple[CalendarEntity, CALLBACK_TYPE]] = {}
        self._first_read = True
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=REFRESH_COOLDOWN,
            immediate=False,
            function=self.async_refresh,
        )

    @callback
    def async_start(self, _hass: HomeAssistant | None = None) -> None:
        """Read the calendars now and whenever they change; stop on unload."""
        self.entry.async_on_unload(self._async_stop)
        self.entry.async_on_unload(
            async_track_time_interval(self.hass, self._async_on_interval, EVENTS_REFRESH_INTERVAL)
        )
        self._debouncer.async_schedule_call()

    @callback
    def _async_stop(self) -> None:
        self._debouncer.async_shutdown()
        for _entity, unsubscribe in self._subscribed.values():
            unsubscribe()
        self._subscribed.clear()

    @callback
    def _async_on_interval(self, _now: datetime) -> None:
        self._debouncer.async_schedule_call()

    @callback
    def _async_on_change(self, _events: Any) -> None:
        self._debouncer.async_schedule_call()

    def _entity(self, entity_id: str) -> CalendarEntity | None:
        component = self.hass.data.get(DATA_COMPONENT)
        return component.get_entity(entity_id) if component else None

    @callback
    def _async_follow(self) -> None:
        """Follow the changes of every chosen calendar that is loaded.

        A calendar may load after Zaya Planner, or be loaded again (a new
        entity object): it is followed from the next read on.
        """
        now = dt_util.now()
        for entity_id in self.calendars:
            entity = self._entity(entity_id)
            followed = self._subscribed.get(entity_id)
            if followed is not None and followed[0] is entity:
                continue
            if followed is not None:
                followed[1]()
                del self._subscribed[entity_id]
            if entity is not None:
                unsubscribe = entity.async_subscribe_events(
                    now, now + EVENTS_HORIZON, self._async_on_change
                )
                self._subscribed[entity_id] = (entity, unsubscribe)

    async def async_events(
        self,
        start: datetime,
        end: datetime,
        calendars: Iterable[str] | None = None,
        errors: list[EventError] | None = None,
    ) -> list[FoundEvent]:
        """Return the events of the chosen calendars in a period, by start.

        A calendar that is not loaded is left out (and reported in `errors`).
        """
        found: list[FoundEvent] = []
        for entity_id in calendars if calendars is not None else self.calendars:
            entity = self._entity(entity_id)
            if entity is None:
                if errors is not None:
                    errors.append(EventError("no_calendar", entity_id))
                continue
            try:
                events = await entity.async_get_events(self.hass, start, end)
            except Exception:  # noqa: BLE001 - one broken calendar must not stop the others
                _LOGGER.exception("Could not read the events of %s", entity_id)
                if errors is not None:
                    errors.append(EventError("no_calendar", entity_id))
                continue
            for event in events:
                description = event.description or ""
                found.append(
                    FoundEvent(
                        calendar=entity_id,
                        summary=event.summary,
                        start=_local(event.start),
                        end=_local(event.end),
                        description=description,
                        reading=self.dictionary.read(description),
                    )
                )
        return sorted(found, key=by_start)

    async def async_find(
        self,
        event_filter: Filter,
        start: datetime,
        end: datetime,
        calendars: Iterable[str] | None = None,
        first_of_day: bool = False,
    ) -> list[FoundEvent]:
        """Return the events that fit a filter, by start."""
        events = [
            event
            for event in await self.async_events(start, end, calendars)
            if event_filter.matches(event.reading)
        ]
        return first_of_each_day(events) if first_of_day else events

    async def async_refresh(self) -> None:
        """Read the chosen calendars again and check their events."""
        self._async_follow()
        now = dt_util.now()
        errors: list[EventError] = []
        events = await self.async_events(now, now + EVENTS_HORIZON, errors=errors)
        for event in events:
            errors.extend(
                EventError(
                    problem.code, event.calendar, event.summary, _day(event.start), problem.value
                )
                for problem in event.reading.problems
            )
            if isinstance(event.start, datetime) and isinstance(event.end, datetime):
                if event.end - event.start > LONGEST_TIMED_EVENT:
                    errors.append(
                        EventError("too_long", event.calendar, event.summary, _day(event.start))
                    )

        tz = dt_util.get_default_time_zone()
        for subentry_id, spec in self.specs.items():
            moments, problems = calculate(spec, events, tz, dt_util.parse_duration)
            self.moments[subentry_id] = moments
            errors.extend(
                EventError(
                    problem.code,
                    problem.event.calendar,
                    problem.event.summary,
                    _day(problem.event.start),
                    problem.value,
                    spec.name,
                )
                for problem in problems
            )

        # One error per mistake, at its first day
        unique: dict[tuple[str, str, str, str], EventError] = {}
        for error in errors:
            unique.setdefault(error.key, error)
        previous = {error.key for error in self.errors}
        self.errors = list(unique.values())
        new = [error for error in self.errors if error.key not in previous]
        async_dispatcher_send(self.hass, f"{SIGNAL_EVENTS_UPDATED}_{self.entry.entry_id}")

        # After a start the errors are known already: only new ones are told
        if new and not self._first_read:
            await async_notify(
                self.hass,
                self.entry,
                message(self.hass, "notify_title_event_errors"),
                "\n".join(self.describe(error) for error in new),
            )
        self._first_read = False

    def describe(self, error: EventError) -> str:
        """Return an error as a line for people."""
        hass = self.hass
        state = hass.states.get(error.calendar)
        calendar = state.name if state else error.calendar
        if error.code == "no_calendar":
            return message(hass, "event_no_calendar", calendar=calendar)
        where = message(
            hass,
            "event_where",
            summary=error.summary,
            calendar=calendar,
            day=f"{error.day:%d.%m}" if error.day else "",
        )
        return message(
            hass, f"event_{error.code}", where=where, value=error.value, moment=error.moment
        )
