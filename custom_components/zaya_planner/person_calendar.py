"""A person's calendar: an iCalendar file with Zaya fields on events.

Events are stored the way Home Assistant's Local Calendar stores them (same
library, floating local times), so recurrence, exceptions and editing one
occurrence or "this and following" behave the same. Zaya fields (trip, riders,
driver, car, travel time, kind) are iCalendar X- properties: editing an event
in the standard Calendar panel keeps them.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, override

from ical.calendar import Calendar
from ical.calendar_stream import IcsCalendarStream
from ical.event import Event
from ical.exceptions import CalendarParseError
from ical.store import EventStore, EventStoreError
from ical.timeline import Timeline, materialize_timeline
from ical.types import Range, Recur
from ical.types.recur import RecurrenceId

from homeassistant.components.calendar import (
    EVENT_END,
    EVENT_RRULE,
    EVENT_START,
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import CALENDAR_STORAGE_PATH, DOMAIN, TripKind
from .details import EventDetails
from .entity import HouseholdEntity
from .household import Person

PRODID = "-//shtrasser-dev//Zaya Planner//EN"

# The state shows the next event; materialize a bounded timeline for it
MAX_LOOKAHEAD_EVENTS = 20
MAX_LOOKAHEAD_TIME = timedelta(days=365)


class CalendarFile:
    """The .ics file of one person."""

    def __init__(self, hass: HomeAssistant, key: str) -> None:
        """Initialize the file."""
        self._hass = hass
        self._path = Path(hass.config.path(CALENDAR_STORAGE_PATH.format(key=key)))
        self._lock = asyncio.Lock()

    async def async_load(self) -> Calendar:
        """Load the calendar (empty if there is no file yet)."""
        async with self._lock:
            ics = await self._hass.async_add_executor_job(self._read)
        calendar = await self._hass.async_add_executor_job(
            IcsCalendarStream.calendar_from_ics, ics
        )
        calendar.prodid = PRODID
        return calendar

    async def async_save(self, calendar: Calendar) -> None:
        """Write the calendar to disk."""
        ics = IcsCalendarStream.calendar_to_ics(calendar)
        async with self._lock:
            await self._hass.async_add_executor_job(self._write, ics)

    def _read(self) -> str:
        return self._path.read_text(encoding="utf-8") if self._path.exists() else ""

    def _write(self, ics: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(ics, encoding="utf-8")


def _floating(value: date | datetime) -> date | datetime:
    """Store times as floating local times, as Local Calendar does."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return dt_util.as_local(value).replace(tzinfo=None)
    return value


def _parse_event(data: dict[str, Any], details: EventDetails | None = None) -> Event:
    """Build an iCalendar event from Home Assistant event fields."""
    data = dict(data)
    if rrule := data.get(EVENT_RRULE):
        data[EVENT_RRULE] = Recur.from_rrule(rrule)
    for key in (EVENT_START, EVENT_END):
        if key in data and data[key] is not None:
            data[key] = _floating(data[key])
    if isinstance(recur := data.get(EVENT_RRULE), Recur) and isinstance(
        recur.until, datetime
    ):
        recur.until = _floating(recur.until)
    if details is not None:
        data["extras"] = details.to_extras()
    try:
        return Event(**data)
    except CalendarParseError as err:
        raise ServiceValidationError(f"Invalid event: {err}") from err


def _to_calendar_event(event: Event) -> CalendarEvent:
    """Return the Home Assistant view of an event."""
    start: date | datetime = event.start
    end: date | datetime = event.end
    if isinstance(start, datetime) and isinstance(end, datetime):
        start = dt_util.as_local(start)
        end = dt_util.as_local(end)
        if end <= start:
            end = start + timedelta(minutes=30)
    elif end <= start:
        end = start + timedelta(days=1)
    return CalendarEvent(
        summary=event.summary or "",
        start=start,
        end=end,
        description=event.description,
        location=event.location,
        uid=event.uid,
        rrule=event.rrule.as_rrule_str() if event.rrule else None,
        recurrence_id=event.recurrence_id,
    )


def _range(recurrence_range: str | None) -> Range:
    if recurrence_range in (Range.THIS_AND_FUTURE, "this_and_future"):
        return Range.THIS_AND_FUTURE
    return Range.NONE


class PersonCalendar(HouseholdEntity, CalendarEntity):
    """The calendar of a person: school, clubs, work, appointments, wake-ups."""

    _entity_platform = "calendar"
    _attr_name = None
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
    )

    def __init__(
        self, entry: ConfigEntry, person: Person, file: CalendarFile, calendar: Calendar
    ) -> None:
        """Initialize the calendar."""
        super().__init__(entry, person=person, key="calendar")
        # calendar.zaya_masha
        self.entity_id = f"calendar.zaya_{person.slug}"
        self.person = person
        self._file = file
        self._calendar = calendar
        self._lock = asyncio.Lock()
        self._timeline: Timeline | None = None

    # ------------------------------------------------------------ reading

    @property
    @override
    def event(self) -> CalendarEvent | None:
        """Return the current or next event."""
        if self._timeline is None:
            return None
        if event := next(self._timeline.active_after(dt_util.now()), None):
            return _to_calendar_event(event)
        return None

    @override
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return events between the given dates."""
        events = await self.async_get_ical_events(start_date, end_date)
        return [_to_calendar_event(event) for event in events]

    async def async_get_ical_events(
        self, start_date: datetime, end_date: datetime
    ) -> list[Event]:
        """Return iCalendar events (with Zaya fields) between the given dates."""

        def overlapping() -> list[Event]:
            timeline = self._calendar.timeline_tz(start_date.tzinfo)
            return list(timeline.overlapping(start_date, end_date))

        return await self.hass.async_add_executor_job(overlapping)

    async def async_update(self) -> None:
        """Find the next events for the state."""

        def timeline() -> Timeline:
            now = dt_util.now()
            return materialize_timeline(
                self._calendar.timeline_tz(now.tzinfo),
                start=now,
                stop=now + MAX_LOOKAHEAD_TIME,
                max_number_of_events=MAX_LOOKAHEAD_EVENTS,
            )

        self._timeline = await self.hass.async_add_executor_job(timeline)

    # ------------------------------------------------- the Calendar panel

    @override
    async def async_create_event(self, **kwargs: Any) -> None:
        """Add an event (from the Calendar panel or calendar.create_event)."""
        await self.async_add_event(_parse_event(kwargs))

    @override
    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Change an event; its Zaya fields are kept."""
        await self._async_edit(uid, _parse_event(event), recurrence_id, recurrence_range)

    @override
    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete an event, an occurrence, or this and following ones."""
        async with self._lock:
            try:
                EventStore(self._calendar).delete(
                    uid, recurrence_id=recurrence_id, recurrence_range=_range(recurrence_range)
                )
            except EventStoreError as err:
                raise HomeAssistantError(f"Error while deleting event: {err}") from err
            await self._file.async_save(self._calendar)
        await self.async_update_ha_state(force_refresh=True)

    # ------------------------------------------------------ Zaya actions

    async def async_add_event(self, event: Event) -> None:
        """Add an event built by the caller."""
        async with self._lock:
            await self.hass.async_add_executor_job(EventStore(self._calendar).add, event)
            await self._file.async_save(self._calendar)
        await self.async_update_ha_state(force_refresh=True)

    async def async_set_details(
        self,
        uid: str,
        changes: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> EventDetails:
        """Change the Zaya fields of an event (or an occurrence)."""
        instance = await self.hass.async_add_executor_job(
            self._find_instance, uid, recurrence_id
        )
        details = EventDetails.from_extras(instance.extras).updated(changes)
        # Same times and title: only the extra properties change
        edit = Event(
            summary=instance.summary,
            dtstart=instance.dtstart,
            dtend=instance.dtend,
            extras=details.to_extras(instance.extras),
        )
        await self._async_edit(uid, edit, recurrence_id, recurrence_range)
        return details

    def _find_instance(self, uid: str, recurrence_id: str | None) -> Event:
        """Return the series (or one occurrence of it) with this uid."""
        if recurrence_id is None:
            for event in self._calendar.events:
                if event.uid == uid and event.recurrence_id is None:
                    return event
        else:
            moment = RecurrenceId.to_value(recurrence_id)
            if isinstance(moment, datetime):
                start = moment.replace(tzinfo=None) - timedelta(days=1)
            else:
                start = datetime.combine(moment, datetime.min.time()) - timedelta(days=1)
            for event in self._calendar.timeline.overlapping(
                start, start + timedelta(days=3)
            ):
                if event.uid == uid and event.recurrence_id == recurrence_id:
                    return event
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="event_not_found",
            translation_placeholders={"uid": uid},
        )

    async def _async_edit(
        self,
        uid: str,
        event: Event,
        recurrence_id: str | None,
        recurrence_range: str | None,
    ) -> None:
        async with self._lock:

            def edit() -> None:
                EventStore(self._calendar).edit(
                    uid,
                    event,
                    recurrence_id=recurrence_id,
                    recurrence_range=_range(recurrence_range),
                )

            try:
                await self.hass.async_add_executor_job(edit)
            except EventStoreError as err:
                raise HomeAssistantError(f"Error while updating event: {err}") from err
            await self._file.async_save(self._calendar)
        await self.async_update_ha_state(force_refresh=True)

    # ------------------------------------------------- actions (services)

    async def async_service_add_event(self, **data: Any) -> dict[str, Any]:
        """zaya_planner.add_event: add an event with its Zaya fields."""
        changes = _detail_changes(data)
        details = self._with_defaults(
            EventDetails().updated(self._checked(changes)), data["summary"], changes
        )
        fields = {
            key: data[key]
            for key in ("summary", "description", "location", "rrule")
            if data.get(key) is not None
        }
        fields[EVENT_START] = data["start"]
        fields[EVENT_END] = data["end"]
        event = _parse_event(fields, details)
        await self.async_add_event(event)
        return {"uid": event.uid, **details.as_dict()}

    async def async_service_set_event_details(self, **data: Any) -> dict[str, Any]:
        """zaya_planner.set_event_details: change the Zaya fields of an event."""
        uid = data["uid"]
        recurrence_id = data.get("recurrence_id")
        instance = await self.hass.async_add_executor_job(
            self._find_instance, uid, recurrence_id
        )
        changes = _detail_changes(data)
        current = EventDetails.from_extras(instance.extras).updated(self._checked(changes))
        details = self._with_defaults(current, instance.summary or "", changes)
        await self.async_set_details(
            uid,
            details.as_changes(),
            recurrence_id=recurrence_id,
            recurrence_range=data.get("recurrence_range"),
        )
        return {"uid": uid, **details.as_dict()}

    async def async_service_get_events(self, **data: Any) -> dict[str, Any]:
        """zaya_planner.get_events: events with their Zaya fields."""
        events = await self.async_get_ical_events(
            data["start_date_time"], data["end_date_time"]
        )
        cars = {car.car_id: car.name for car in self.entry.runtime_data.cars}
        result = []
        for event in events:
            view = _to_calendar_event(event)
            details = EventDetails.from_extras(event.extras)
            result.append(
                {
                    "uid": event.uid,
                    "recurrence_id": event.recurrence_id,
                    "summary": view.summary,
                    "start": view.start.isoformat(),
                    "end": view.end.isoformat(),
                    "rrule": view.rrule,
                    "location": view.location,
                    **details.as_dict(),
                    "car_name": cars.get(details.car) if details.car else None,
                }
            )
        return {"events": result}

    # ------------------------------------------------------------ helpers

    def _checked(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Check people and cars; cars may be given by name."""
        data = self.entry.runtime_data
        people = {person.entity_id for person in data.people}
        for entity_id in [*changes.get("riders") or [], changes.get("driver")]:
            if entity_id and entity_id not in people:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_person",
                    translation_placeholders={"person": entity_id},
                )
        if car := changes.get("car"):
            match = next(
                (c.car_id for c in data.cars if car in (c.car_id, c.name)), None
            )
            if match is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_car",
                    translation_placeholders={"car": car},
                )
            changes = {**changes, "car": match}
        return changes

    def _with_defaults(
        self, details: EventDetails, summary: str, given: dict[str, Any]
    ) -> EventDetails:
        """Fill in what was not given for a trip; check the driver."""
        if details.trip == TripKind.NONE:
            return details
        data = self.entry.runtime_data
        owner = self.person
        riders = list(details.riders)
        if "riders" not in given and not riders:
            riders = [owner.entity_id]
            if usual := data.settings.person(owner)["usually_drives"]:
                riders.append(usual)
        drivers = [
            p for p in data.people
            if p.entity_id in riders and data.settings.person(p)["car"]
        ]
        driver = details.driver
        if "driver" not in given and not driver and len(drivers) == 1:
            driver = drivers[0].entity_id
        if driver and driver not in riders:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="driver_not_rider"
            )
        car = details.car
        if "car" not in given and not car and driver:
            person = next(p for p in data.people if p.entity_id == driver)
            car = data.settings.person(person)["car"]
        travel = details.travel_minutes
        if "travel_minutes" not in given and travel is None:
            travel = self._last_travel(summary)
        return details.updated(
            {"riders": riders, "driver": driver, "car": car, "travel_minutes": travel}
        )

    def _last_travel(self, summary: str) -> int | None:
        """Return the travel time of the latest trip with the same title."""
        latest: tuple[Any, int] | None = None
        for event in self._calendar.events:
            if event.summary != summary:
                continue
            travel = EventDetails.from_extras(event.extras).travel_minutes
            if travel is None:
                continue
            stamp = event.last_modified or event.dtstamp
            if latest is None or stamp > latest[0]:
                latest = (stamp, travel)
        return latest[1] if latest else None


def _detail_changes(data: dict[str, Any]) -> dict[str, Any]:
    """Return the Zaya fields given in an action call."""
    names = ("kind", "trip", "riders", "driver", "car", "travel_minutes")
    return {name: data[name] for name in names if name in data}
