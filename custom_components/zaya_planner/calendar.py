"""Calendar “Schedule”: one event per task."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import override

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ZayaPlannerConfigEntry
from .const import DOMAIN, EVENT_DURATION, SIGNAL_PLAN_UPDATED, TaskStatus, TripKind
from .entity import ZayaPlannerEntity
from .messages import message
from .models import Task
from .person_calendar import CalendarFile, PersonCalendar
from .plan import Plan

PARALLEL_UPDATES = 0

# Shown before the title so the outcome is visible without opening the event
STATUS_ICONS: dict[TaskStatus, str] = {
    TaskStatus.RUNNING: "⏳",
    TaskStatus.DONE: "✅",
    TaskStatus.ERROR: "⚠️",
    TaskStatus.FAILED: "❌",
    TaskStatus.SKIPPED: "⏭️",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZayaPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the schedule and a calendar for each person."""
    entities: list[CalendarEntity] = [ZayaPlannerCalendar(entry), HousePlanCalendar(entry)]
    for person in entry.runtime_data.people:
        file = CalendarFile(hass, person.key)
        calendar = PersonCalendar(entry, person, file, await file.async_load())
        entry.runtime_data.calendars[person.entity_id] = calendar
        entities.append(calendar)
    async_add_entities(entities, update_before_add=True)



def _not_a_person_calendar() -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_a_person_calendar"
    )


def _event_end(task: Task) -> datetime:
    end = task.due + EVENT_DURATION
    if task.finished_at is not None and task.finished_at > end:
        end = task.finished_at
    return end


class ZayaPlannerCalendar(ZayaPlannerEntity, CalendarEntity):
    """Tasks of the schedule, planned and recently finished.

    Cancelled tasks are not shown: a calendar does not return cancelled events.
    """

    _entity_platform = "calendar"

    def __init__(self, entry: ZayaPlannerConfigEntry) -> None:
        """Initialize the calendar."""
        super().__init__(entry, entry.runtime_data.manager, "schedule")

    @property
    @override
    def event(self) -> CalendarEvent | None:
        """Return the current or next open task."""
        now = dt_util.utcnow()
        for task in self.manager.tasks:
            if task.is_open and _event_end(task) > now:
                return self._event(task)
        return None

    @override
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return tasks between the given dates."""
        return [
            self._event(task)
            for task in self.manager.tasks
            if task.status != TaskStatus.CANCELLED
            and task.due < end_date
            and _event_end(task) > start_date
        ]

    # Actions on person calendars do not apply to the schedule
    async def async_service_add_event(self, **data: object) -> None:
        """Refuse: the schedule is not a person calendar."""
        raise _not_a_person_calendar()

    async_service_set_event_details = async_service_add_event
    async_service_get_events = async_service_add_event

    def _event(self, task: Task) -> CalendarEvent:
        hass = self.hass
        lines = [
            f"{message(hass, 'label_status')}: {message(hass, f'status_{task.status}')}",
            f"{message(hass, 'label_action')}: {task.action}",
        ]
        if task.params:
            params = json.dumps(task.params, ensure_ascii=False, default=str)
            lines.append(f"{message(hass, 'label_params')}: {params}")
        if task.attempts:
            lines.append(f"{message(hass, 'label_attempts')}: {task.attempts}")
        if task.last_error:
            lines.append(f"{message(hass, 'label_error')}: {task.last_error}")
        return CalendarEvent(
            start=dt_util.as_local(task.due),
            end=dt_util.as_local(_event_end(task)),
            summary=f"{icon} {task.display_title}"
            if (icon := STATUS_ICONS.get(task.status))
            else task.display_title,
            description="\n".join(lines),
            uid=task.uid,
        )


# A wake-up is a moment; it is shown as a short event
WAKE_EVENT_DURATION = timedelta(minutes=5)


class HousePlanCalendar(ZayaPlannerEntity, CalendarEntity):
    """The house plan: departures and wake-ups worked out from person calendars."""

    _entity_platform = "calendar"

    def __init__(self, entry: ZayaPlannerConfigEntry) -> None:
        """Initialize the calendar."""
        super().__init__(entry, entry.runtime_data.manager, "house_plan")

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the plan."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_PLAN_UPDATED}_{self._entry_id}",
                self._async_tasks_updated,
            )
        )

    @property
    @override
    def event(self) -> CalendarEvent | None:
        """Return the current or next item of the plan."""
        now = dt_util.now()
        events = [e for e in self._events(self.entry.runtime_data.house.plan) if e.end > now]
        return min(events, key=lambda e: e.start, default=None)

    @override
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the plan of the period."""
        plan = await self.entry.runtime_data.house.async_plan_for(start_date, end_date)
        return sorted(self._events(plan), key=lambda e: e.start)

    async def async_service_add_event(self, **data: object) -> None:
        """Refuse: the house plan is not a person calendar."""
        raise _not_a_person_calendar()

    async_service_set_event_details = async_service_add_event
    async_service_get_events = async_service_add_event

    def _events(self, plan: Plan) -> list[CalendarEvent]:
        data = self.entry.runtime_data
        names = {person.entity_id: person.name for person in data.people}
        cars = {car.car_id: car.name for car in data.cars}
        hass = self.hass
        events = []
        for departure in plan.departures:
            owner = names.get(departure.owner, departure.owner)
            key = "plan_drop" if departure.trip == TripKind.DROP else "plan_pickup"
            lines = [
                message(
                    hass,
                    "plan_riders",
                    riders=", ".join(names.get(r, r) for r in departure.riders),
                ),
                message(hass, "plan_travel", minutes=departure.travel_minutes),
            ]
            if departure.driver:
                lines.insert(0, message(hass, "plan_driver", name=names.get(departure.driver, departure.driver)))
            if departure.car:
                lines.insert(0, message(hass, "plan_car", car=cars.get(departure.car, departure.car)))
            events.append(
                CalendarEvent(
                    start=dt_util.as_local(departure.at),
                    end=dt_util.as_local(departure.arrive),
                    summary=message(hass, key, summary=departure.summary, owner=owner),
                    description="\n".join(lines),
                    uid=f"departure_{departure.uid}_{departure.trip}_{departure.at.isoformat()}",
                )
            )
        for wake in plan.wake_ups:
            if wake.departure is not None:
                reason = message(
                    hass,
                    "plan_wake_for",
                    time=f"{dt_util.as_local(wake.departure.at):%H:%M}",
                    summary=wake.departure.summary,
                    owner=names.get(wake.departure.owner, wake.departure.owner),
                )
            else:
                reason = message(hass, "plan_wake_set", summary=wake.summary or "")
            start = dt_util.as_local(wake.at)
            events.append(
                CalendarEvent(
                    start=start,
                    end=start + WAKE_EVENT_DURATION,
                    summary=message(hass, "plan_wake", name=names.get(wake.person, wake.person)),
                    description=reason,
                    uid=f"wake_{wake.person}_{wake.at.isoformat()}",
                )
            )
        return events
