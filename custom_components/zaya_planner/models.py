"""Data model of Zaya Planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import EXPIRES_ALWAYS, OPEN_STATUSES, TaskStatus


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_from_str(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


@dataclass(slots=True)
class Task:
    """A task on the schedule.

    `id` is the key of the *current* task: among open tasks it is unique, while
    finished tasks with the same id stay in history. `uid` identifies one
    particular task forever.
    """

    uid: str
    id: str
    due: datetime
    action: str
    title: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    # datetime, EXPIRES_ALWAYS, or None (= default lateness limit)
    expires: datetime | str | None = None
    rule_id: str | None = None
    status: TaskStatus = TaskStatus.SCHEDULED
    attempts: int = 0
    last_error: str | None = None
    result: Any = None
    created_at: datetime = field(default_factory=dt_util.utcnow)
    updated_at: datetime = field(default_factory=dt_util.utcnow)
    finished_at: datetime | None = None
    # Executor bookkeeping: when to try next, since when it fails, alert sent
    next_attempt: datetime | None = None
    error_since: datetime | None = None
    notified: bool = False

    @property
    def is_open(self) -> bool:
        """Return True while the task is not closed."""
        return self.status in OPEN_STATUSES

    @property
    def display_title(self) -> str:
        """Return the title for humans, falling back to the id."""
        return self.title or self.id

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        expires = self.expires
        return {
            "uid": self.uid,
            "id": self.id,
            "title": self.title,
            "due": _dt_to_str(self.due),
            "action": self.action,
            "params": self.params,
            "expires": _dt_to_str(expires)
            if isinstance(expires, datetime)
            else expires,
            "rule_id": self.rule_id,
            "status": str(self.status),
            "attempts": self.attempts,
            "last_error": self.last_error,
            "result": self.result,
            "created_at": _dt_to_str(self.created_at),
            "updated_at": _dt_to_str(self.updated_at),
            "finished_at": _dt_to_str(self.finished_at),
            "next_attempt": _dt_to_str(self.next_attempt),
            "error_since": _dt_to_str(self.error_since),
            "notified": self.notified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        """Restore a task saved with as_dict."""
        expires = data.get("expires")
        if expires is not None and expires != EXPIRES_ALWAYS:
            expires = _dt_from_str(expires)
        status = TaskStatus(data.get("status", TaskStatus.SCHEDULED))
        due = _dt_from_str(data["due"])
        next_attempt = _dt_from_str(data.get("next_attempt"))
        if next_attempt is None and status in OPEN_STATUSES:
            # Saved by a version without the executor: run it when due
            next_attempt = due
        return cls(
            uid=data["uid"],
            id=data["id"],
            title=data.get("title") or "",
            due=due,
            action=data["action"],
            params=data.get("params") or {},
            expires=expires,
            rule_id=data.get("rule_id"),
            status=status,
            attempts=data.get("attempts", 0),
            last_error=data.get("last_error"),
            result=data.get("result"),
            created_at=_dt_from_str(data.get("created_at")) or dt_util.utcnow(),
            updated_at=_dt_from_str(data.get("updated_at")) or dt_util.utcnow(),
            finished_at=_dt_from_str(data.get("finished_at")),
            next_attempt=next_attempt,
            error_since=_dt_from_str(data.get("error_since")),
            notified=data.get("notified", False),
        )
