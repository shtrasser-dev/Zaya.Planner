"""The house plan kept up to date, and Android alarms for wake-ups."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    PLAN_HORIZON,
    PLAN_REFRESH_INTERVAL,
    SIGNAL_CALENDARS_UPDATED,
    SIGNAL_PLAN_UPDATED,
    SIGNAL_SETTINGS_UPDATED,
)
from .messages import message
from .plan import Departure, Plan, PlanEvent, PersonInput, WakeUp, calculate
from .details import EventDetails

if TYPE_CHECKING:
    from . import ZayaPlannerData

_LOGGER = logging.getLogger(__name__)


def _local(value: date | datetime) -> date | datetime:
    """Return a timed value as local aware time (calendar times are floating)."""
    return dt_util.as_local(value) if isinstance(value, datetime) else value


class HousePlanner:
    """Keep the plan of the next days and tell the entities when it changes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the planner."""
        self.hass = hass
        self.entry = entry
        self.plan = Plan()
        self._unsub_next: CALLBACK_TYPE | None = None

    @property
    def _data(self) -> ZayaPlannerData:
        return self.entry.runtime_data

    @callback
    def async_start(self, _hass: HomeAssistant | None = None) -> None:
        """Follow changes and keep the plan fresh."""
        signal_suffix = self.entry.entry_id
        for signal in (SIGNAL_CALENDARS_UPDATED, SIGNAL_SETTINGS_UPDATED):
            self.entry.async_on_unload(
                async_dispatcher_connect(
                    self.hass, f"{signal}_{signal_suffix}", self._async_schedule_refresh
                )
            )
        self.entry.async_on_unload(
            async_track_time_interval(self.hass, self._async_refresh_now, PLAN_REFRESH_INTERVAL)
        )
        # Alarms are set at a minute chosen per person
        self.entry.async_on_unload(
            async_track_time_change(self.hass, self._async_minute, second=0)
        )
        self.entry.async_on_unload(self._async_cancel_next)
        self._async_schedule_refresh()

    @callback
    def _async_schedule_refresh(self) -> None:
        self.entry.async_create_task(self.hass, self.async_refresh(), "zaya_planner plan")

    async def _async_refresh_now(self, _now: datetime) -> None:
        await self.async_refresh()

    async def async_refresh(self) -> None:
        """Work out the plan from today to the horizon."""
        start = dt_util.start_of_local_day()
        self.plan = await self.async_plan_for(start, start + PLAN_HORIZON)
        await self._async_check_alarms()
        self._async_updated()

    async def async_plan_for(self, start: datetime, end: datetime) -> Plan:
        """Return the plan of a period (events a day around it are read too)."""
        data = self._data
        people = []
        for person in data.people:
            calendar = data.calendars.get(person.entity_id)
            if calendar is None:
                continue
            settings = data.settings.person(person)
            ical_events = await calendar.async_get_ical_events(
                start - timedelta(days=1), end + timedelta(days=1)
            )
            people.append(
                PersonInput(
                    entity_id=person.entity_id,
                    name=person.name,
                    getting_ready=int(settings["getting_ready"]),
                    wake_for_trips=bool(settings["wake_for_trips"]),
                    events=tuple(
                        PlanEvent(
                            uid=event.uid or "",
                            summary=event.summary or "",
                            start=_local(event.start),
                            end=_local(event.end),
                            details=EventDetails.from_extras(event.extras),
                        )
                        for event in ical_events
                    ),
                )
            )
        tz = dt_util.get_default_time_zone()
        plan = calculate(people, tz)
        plan.departures = [d for d in plan.departures if start <= d.at < end]
        plan.wake_ups = [w for w in plan.wake_ups if start <= w.at < end]
        return plan

    @callback
    def _async_updated(self) -> None:
        """Tell the entities; wake up again when the next item comes."""
        async_dispatcher_send(self.hass, f"{SIGNAL_PLAN_UPDATED}_{self.entry.entry_id}")
        self._async_cancel_next()
        now = dt_util.utcnow()
        moments = [d.at for d in self.plan.departures] + [w.at for w in self.plan.wake_ups]
        upcoming = [moment for moment in moments if moment > now]
        if upcoming:
            self._unsub_next = async_track_point_in_utc_time(
                self.hass, self._async_on_next, min(upcoming) + timedelta(seconds=1)
            )

    @callback
    def _async_on_next(self, _now: datetime) -> None:
        self._unsub_next = None
        self._async_updated()

    @callback
    def _async_cancel_next(self) -> None:
        if self._unsub_next is not None:
            self._unsub_next()
            self._unsub_next = None

    # ----------------------------------------------------------- queries

    def next_wake_up(self, entity_id: str) -> WakeUp | None:
        """Return the person's next wake-up."""
        now = dt_util.utcnow()
        return next(
            (w for w in self.plan.wake_ups if w.person == entity_id and w.at > now), None
        )

    def first_wake_up(self) -> WakeUp | None:
        """Return the next wake-up of anyone in the house."""
        now = dt_util.utcnow()
        return next((w for w in self.plan.wake_ups if w.at > now), None)

    def wake_up_on(self, entity_id: str, day: date) -> WakeUp | None:
        """Return the person's wake-up on a local day."""
        return next(
            (
                w
                for w in self.plan.wake_ups
                if w.person == entity_id and dt_util.as_local(w.at).date() == day
            ),
            None,
        )

    def next_departure(self, entity_id: str) -> Departure | None:
        """Return the next departure the person goes on."""
        now = dt_util.utcnow()
        return next(
            (d for d in self.plan.departures if entity_id in d.riders and d.at > now), None
        )

    def next_first_departure(self, car_id: str) -> Departure | None:
        """Return the car's next departure that is its first of the day."""
        now = dt_util.utcnow()
        seen: set[date] = set()
        for departure in self.plan.departures:
            if departure.car != car_id:
                continue
            day = dt_util.as_local(departure.at).date()
            if day in seen:
                continue
            seen.add(day)
            if departure.at > now:
                return departure
        return None

    # ------------------------------------------------------------ alarms

    async def _async_minute(self, now: datetime) -> None:
        """Set tomorrow's alarm for people whose alarm time it is."""
        local = dt_util.as_local(now)
        tomorrow = local.date() + timedelta(days=1)
        for person in self._data.people:
            settings = self._data.settings.person(person)
            service = settings["alarm_notify"]
            if not service or local.strftime("%H:%M") != settings["alarm_time"][:5]:
                continue
            record = self._data.settings.alarm(person)
            if record.get("date") == tomorrow.isoformat():
                continue
            wake = self.wake_up_on(person.entity_id, tomorrow)
            if wake is None:
                continue
            wake_local = dt_util.as_local(wake.at)
            await self._async_notify(
                service,
                {
                    "message": "command_activity",
                    "data": {
                        "intent_action": "android.intent.action.SET_ALARM",
                        "intent_extras": (
                            f"android.intent.extra.alarm.HOUR:{wake_local.hour},"
                            f"android.intent.extra.alarm.MINUTES:{wake_local.minute},"
                            "android.intent.extra.alarm.MESSAGE:Zaya Planner,"
                            "android.intent.extra.alarm.SKIP_UI:true"
                        ),
                    },
                },
            )
            await self._data.settings.async_set_alarm(
                person,
                {"date": tomorrow.isoformat(), "time": f"{wake_local:%H:%M}", "warned": None},
            )

    async def _async_check_alarms(self) -> None:
        """Warn when a wake-up moved after its alarm was set."""
        today = dt_util.now().date()
        for person in self._data.people:
            record = self._data.settings.alarm(person)
            service = self._data.settings.person(person)["alarm_notify"]
            if not service or not record.get("date"):
                continue
            day = date.fromisoformat(record["date"])
            if day < today:
                continue
            wake = self.wake_up_on(person.entity_id, day)
            now_time = f"{dt_util.as_local(wake.at):%H:%M}" if wake else "—"
            if now_time in (record["time"], record.get("warned")):
                continue
            key = "alarm_moved" if wake else "alarm_cancelled"
            await self._async_notify(
                service,
                {
                    "title": message(self.hass, "alarm_title"),
                    "message": message(
                        self.hass, key, name=person.name, alarm=record["time"], time=now_time
                    ),
                },
            )
            await self._data.settings.async_set_alarm(person, {**record, "warned": now_time})

    async def _async_notify(self, service: str, data: dict[str, Any]) -> None:
        try:
            await self.hass.services.async_call("notify", service, data, blocking=False)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not notify %s: %s", service, err)
