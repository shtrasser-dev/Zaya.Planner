"""Zaya fields of a person-calendar event, kept as iCalendar X- properties."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ical.types import ExtraProperty

from .const import EventKind, TripKind

X_KIND = "X-ZAYA-KIND"
X_TRIP = "X-ZAYA-TRIP"
X_RIDERS = "X-ZAYA-RIDERS"
X_DRIVER = "X-ZAYA-DRIVER"
X_CAR = "X-ZAYA-CAR"
X_TRAVEL = "X-ZAYA-TRAVEL"
ZAYA_PROPERTIES = frozenset({X_KIND, X_TRIP, X_RIDERS, X_DRIVER, X_CAR, X_TRAVEL})


def _enum(enum: Any, value: str | None, default: Any) -> Any:
    """Return the enum member for value, or default if missing or unknown."""
    try:
        return enum(value) if value else default
    except ValueError:
        return default


@dataclass(slots=True, frozen=True)
class EventDetails:
    """What Zaya Planner knows about an event besides its time and title."""

    kind: EventKind = EventKind.EVENT
    trip: TripKind = TripKind.NONE
    # entity ids of the people going
    riders: tuple[str, ...] = field(default_factory=tuple)
    # entity id of the person driving
    driver: str | None = None
    # car_id (config subentry id)
    car: str | None = None
    # minutes on the way, parking included
    travel_minutes: int | None = None

    @classmethod
    def from_extras(cls, extras: list[ExtraProperty]) -> EventDetails:
        """Read the details from an event's extra properties."""
        values = {extra.name.upper(): extra.value for extra in extras}

        travel = values.get(X_TRAVEL)
        return cls(
            kind=_enum(EventKind, values.get(X_KIND), EventKind.EVENT),
            trip=_enum(TripKind, values.get(X_TRIP), TripKind.NONE),
            riders=tuple(values.get(X_RIDERS, "").split()),
            driver=values.get(X_DRIVER) or None,
            car=values.get(X_CAR) or None,
            travel_minutes=int(travel) if travel and travel.isdigit() else None,
        )

    def to_extras(self, keep: list[ExtraProperty] | None = None) -> list[ExtraProperty]:
        """Return extra properties: other ones from `keep`, then ours."""
        extras = [extra for extra in keep or [] if extra.name.upper() not in ZAYA_PROPERTIES]
        if self.kind != EventKind.EVENT:
            extras.append(ExtraProperty(name=X_KIND, value=str(self.kind)))
        if self.trip != TripKind.NONE:
            extras.append(ExtraProperty(name=X_TRIP, value=str(self.trip)))
        if self.riders:
            extras.append(ExtraProperty(name=X_RIDERS, value=" ".join(self.riders)))
        if self.driver:
            extras.append(ExtraProperty(name=X_DRIVER, value=self.driver))
        if self.car:
            extras.append(ExtraProperty(name=X_CAR, value=self.car))
        if self.travel_minutes is not None:
            extras.append(ExtraProperty(name=X_TRAVEL, value=str(self.travel_minutes)))
        return extras

    def updated(self, changes: dict[str, Any]) -> EventDetails:
        """Return a copy with the given fields changed."""
        if "riders" in changes:
            changes = {**changes, "riders": tuple(changes["riders"] or ())}
        if "kind" in changes:
            changes = {**changes, "kind": EventKind(changes["kind"])}
        if "trip" in changes:
            changes = {**changes, "trip": TripKind(changes["trip"])}
        return replace(self, **changes)

    def as_changes(self) -> dict[str, Any]:
        """Return all fields as changes for updated()."""
        return {
            "kind": self.kind,
            "trip": self.trip,
            "riders": self.riders,
            "driver": self.driver,
            "car": self.car,
            "travel_minutes": self.travel_minutes,
        }

    def as_dict(self) -> dict[str, Any]:
        """Return the details for an action response."""
        return {
            "kind": str(self.kind),
            "trip": str(self.trip),
            "riders": list(self.riders),
            "driver": self.driver,
            "car": self.car,
            "travel_minutes": self.travel_minutes,
        }
