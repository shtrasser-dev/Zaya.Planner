"""Data model of Zaya Planner: plans and their steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import DISPLAY_SPAN, LOCKED_STATUSES, OPEN_STATUSES, PlanStatus, StepStatus


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_from_str(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


@dataclass(slots=True)
class Step:
    """One step of a plan: at a time, run an action with data.

    A step with no action is a mark (“Departure — School”): a moment with a name,
    done when it comes.
    """

    at: datetime
    # zaya_planner.ensure_state or an allowed script; empty: a mark
    action: str
    data: dict[str, Any] = field(default_factory=dict)
    # The name of a mark
    name: str = ""
    status: StepStatus = StepStatus.PLANNED
    attempts: int = 0
    last_error: str | None = None
    result: Any = None
    finished_at: datetime | None = None
    # Executor bookkeeping: when to try next, since when it fails, alert sent
    next_attempt: datetime | None = None
    error_since: datetime | None = None
    notified: bool = False

    @property
    def is_mark(self) -> bool:
        """Return True if the step only marks a moment."""
        return not self.action

    @property
    def is_finished(self) -> bool:
        """Return True once the step is done, failed or cancelled."""
        return self.status in (StepStatus.DONE, StepStatus.FAILED, StepStatus.CANCELLED)

    def same_as(self, other: Step) -> bool:
        """Return True if both steps do the same thing at the same time."""
        return (self.at, self.action, self.data, self.name) == (
            other.at,
            other.action,
            other.data,
            other.name,
        )

    def copy(self) -> Step:
        """Return a new step that does the same thing at the same time."""
        return Step(at=self.at, action=self.action, data=dict(self.data), name=self.name)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "at": _dt_to_str(self.at),
            "action": self.action,
            "data": self.data,
            "name": self.name,
            "status": str(self.status),
            "attempts": self.attempts,
            "last_error": self.last_error,
            "result": self.result,
            "finished_at": _dt_to_str(self.finished_at),
            "next_attempt": _dt_to_str(self.next_attempt),
            "error_since": _dt_to_str(self.error_since),
            "notified": self.notified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Step:
        """Restore a step saved with as_dict."""
        return cls(
            at=_dt_from_str(data["at"]),  # type: ignore[arg-type]
            action=data["action"],
            data=data.get("data") or {},
            name=data.get("name") or "",
            status=StepStatus(data.get("status", StepStatus.PLANNED)),
            attempts=data.get("attempts", 0),
            last_error=data.get("last_error"),
            result=data.get("result"),
            finished_at=_dt_from_str(data.get("finished_at")),
            next_attempt=_dt_from_str(data.get("next_attempt")),
            error_since=_dt_from_str(data.get("error_since")),
            notified=data.get("notified", False),
        )


@dataclass(slots=True)
class Plan:
    """A sequence of steps run as one whole, made by an owner.

    `uid` identifies the plan forever; `owner` is the automation or script that
    made it (one next plan per owner).
    """

    uid: str
    owner: str
    steps: list[Step]
    summary: str = ""
    status: PlanStatus = PlanStatus.PLANNED
    # Tags from the dictionary: for people, and to freeze or unfreeze plans together
    tags: list[str] = field(default_factory=list)
    # When it was frozen (by “freeze plans”, or when its first step was due)
    frozen_at: datetime | None = None
    # Script run when the plan fails
    on_failure: str | None = None
    # On the calendar: one event (span) or one per step (steps)
    display: str = DISPLAY_SPAN
    created_at: datetime = field(default_factory=dt_util.utcnow)
    updated_at: datetime = field(default_factory=dt_util.utcnow)
    finished_at: datetime | None = None
    # Why it failed or who cancelled it
    reason: str | None = None
    # Attempts of automations to change it once frozen (for people to see)
    conflicts: list[str] = field(default_factory=list)
    # The steps as its owner made them, before a person moved them (None: not moved)
    origin: list[Step] | None = None
    # A person moved or cancelled it: its owner does not make it again
    by_person: bool = False

    @property
    def start(self) -> datetime:
        """Return the time of the first step."""
        return self.steps[0].at

    @property
    def end(self) -> datetime:
        """Return the time of the last step."""
        return self.steps[-1].at

    @property
    def planned_steps(self) -> list[Step]:
        """Return the steps as its owner made them."""
        return self.origin if self.origin is not None else self.steps

    @property
    def is_open(self) -> bool:
        """Return True until the plan is done, failed or cancelled."""
        return self.status in OPEN_STATUSES

    @property
    def is_locked(self) -> bool:
        """Return True once frozen: its owner cannot change it."""
        return self.status in LOCKED_STATUSES

    @property
    def display_summary(self) -> str:
        """Return the name for people, falling back to the owner."""
        return self.summary or self.owner

    def current_step(self) -> Step | None:
        """Return the first step that is not finished."""
        return next((step for step in self.steps if not step.is_finished), None)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "uid": self.uid,
            "owner": self.owner,
            "summary": self.summary,
            "status": str(self.status),
            "steps": [step.as_dict() for step in self.steps],
            "tags": self.tags,
            "frozen_at": _dt_to_str(self.frozen_at),
            "on_failure": self.on_failure,
            "display": self.display,
            "created_at": _dt_to_str(self.created_at),
            "updated_at": _dt_to_str(self.updated_at),
            "finished_at": _dt_to_str(self.finished_at),
            "reason": self.reason,
            "conflicts": self.conflicts,
            "origin": [step.as_dict() for step in self.origin] if self.origin is not None else None,
            "by_person": self.by_person,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        """Restore a plan saved with as_dict."""
        return cls(
            uid=data["uid"],
            owner=data["owner"],
            summary=data.get("summary") or "",
            status=PlanStatus(data.get("status", PlanStatus.PLANNED)),
            steps=[Step.from_dict(step) for step in data.get("steps", [])],
            tags=list(data.get("tags") or []),
            frozen_at=_dt_from_str(data.get("frozen_at")),
            on_failure=data.get("on_failure"),
            display=data.get("display") or DISPLAY_SPAN,
            created_at=_dt_from_str(data.get("created_at")) or dt_util.utcnow(),
            updated_at=_dt_from_str(data.get("updated_at")) or dt_util.utcnow(),
            finished_at=_dt_from_str(data.get("finished_at")),
            reason=data.get("reason"),
            conflicts=list(data.get("conflicts") or []),
            origin=(
                [Step.from_dict(step) for step in data["origin"]]
                if data.get("origin") is not None
                else None
            ),
            by_person=data.get("by_person", False),
        )
