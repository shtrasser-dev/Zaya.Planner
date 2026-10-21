"""Tests of the executor: running due tasks, retries, deadlines, notifications."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.zaya_planner.config_flow import DEFAULT_OPTIONS
from custom_components.zaya_planner.const import (
    CONF_NOTIFY_ACTION,
    DOMAIN,
    EVENT_TASK_DONE,
    EVENT_TASK_FAILED,
    STORAGE_KEY,
)
from homeassistant.const import EVENT_LOGBOOK_ENTRY
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util

from .common import upsert

DUE = "2026-10-21T12:10:00+00:00"
CALENDAR = "calendar.zaya_planner_schedule"


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Send notifications to a test action."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zaya Planner",
        data={},
        options={**DEFAULT_OPTIONS, CONF_NOTIFY_ACTION: "test.notify"},
    )


@pytest.fixture
def notifications(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Collect notifications."""
    calls: list[dict[str, Any]] = []

    async def handler(call: ServiceCall) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("test", "notify", handler)
    return calls


@pytest.fixture
def script(hass: HomeAssistant) -> SimpleNamespace:
    """A fake script.plan_switch.

    It answers with `answers` in turn (the last one repeats) and, when `gate`
    is set, waits for it before answering.
    """
    fake = SimpleNamespace(calls=[], answers=[{"ok": True}], gate=None)

    async def handler(call: ServiceCall) -> dict[str, Any]:
        fake.calls.append(dict(call.data))
        if fake.gate is not None:
            await fake.gate.wait()
        if len(fake.answers) > 1:
            return fake.answers.pop(0)
        return fake.answers[0]

    hass.services.async_register(
        "script", "plan_switch", handler, supports_response=SupportsResponse.OPTIONAL
    )
    return fake


async def move_to(hass: HomeAssistant, freezer, when: str | Any) -> None:
    """Move time and let the executor do what is due."""
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def get_task(hass: HomeAssistant, task_id: str = "t") -> dict[str, Any]:
    """Return the latest task with this id."""
    response = await hass.services.async_call(
        DOMAIN, "list", {"id": task_id}, blocking=True, return_response=True
    )
    return response["tasks"][-1]


async def test_task_runs_when_due(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, script, notifications
) -> None:
    """At its due time the task's script is called with the task's params."""
    done = async_capture_events(hass, EVENT_TASK_DONE)
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    await upsert(hass, id="t", title="Floor", due=DUE, params={"state": "on"})

    await move_to(hass, frozen_now, "2026-10-21T12:09:59+00:00")
    assert script.calls == []

    await move_to(hass, frozen_now, DUE)
    assert script.calls == [{"state": "on"}]
    task = await get_task(hass)
    assert task["status"] == "done"
    assert task["attempts"] == 1
    assert task["result"] == {"ok": True}
    assert task["finished_at"] == DUE
    assert [event.data["id"] for event in done] == ["t"]
    assert "Floor" in logbook[-1].data["message"]
    assert notifications == []

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": CALENDAR,
            "start_date_time": "2026-10-21T00:00:00+00:00",
            "end_date_time": "2026-10-22T00:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    assert response[CALENDAR]["events"][0]["summary"] == "✅ Floor"


async def test_retry_until_ok(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, script
) -> None:
    """A failed attempt is retried a minute later."""
    script.answers = [{"ok": False, "reason": "no connection"}, {"ok": True}]
    await upsert(hass, id="t", due=DUE)

    await move_to(hass, frozen_now, DUE)
    task = await get_task(hass)
    assert task["status"] == "error"
    assert task["last_error"] == "no connection"
    assert task["next_attempt"] == "2026-10-21T12:11:00+00:00"
    assert hass.states.get("sensor.zaya_planner_errors").state == "1"

    await move_to(hass, frozen_now, "2026-10-21T12:11:00+00:00")
    task = await get_task(hass)
    assert task["status"] == "done"
    assert task["attempts"] == 2
    assert task["last_error"] is None
    assert hass.states.get("sensor.zaya_planner_errors").state == "0"


async def test_error_notifies_once_after_10_minutes(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, script, notifications
) -> None:
    """A task that keeps failing is reported once, 10 minutes after the first error."""
    script.answers = [{"ok": False, "reason": "no connection"}]
    await upsert(hass, id="t", title="Floor", due=DUE)
    start = dt_util.parse_datetime(DUE)

    for minute in range(10):
        await move_to(hass, frozen_now, start + timedelta(minutes=minute))
    assert notifications == []

    for minute in range(10, 30):
        await move_to(hass, frozen_now, start + timedelta(minutes=minute))
    assert len(notifications) == 1
    assert "Floor" in notifications[0]["message"]
    assert "no connection" in notifications[0]["message"]

    task = await get_task(hass)
    assert task["status"] == "error"
    # Pauses grow: 1, 1, 2, 5 minutes, then every 5 minutes
    assert task["attempts"] == 9


async def test_fails_at_deadline(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now, script, notifications
) -> None:
    """Attempts stop when the task expires; the task is failed and reported."""
    failed = async_capture_events(hass, EVENT_TASK_FAILED)
    script.answers = [{"ok": False, "reason": "no connection"}]
    await upsert(hass, id="t", title="Floor", due=DUE, expires="2026-10-21T12:13:00+00:00")
    start = dt_util.parse_datetime(DUE)

    for minute in range(6):
        await move_to(hass, frozen_now, start + timedelta(minutes=minute))

    task = await get_task(hass)
    assert task["status"] == "failed"
    assert task["attempts"] == 4
    assert task["last_error"] == "no connection"
    assert [event.data["status"] for event in failed] == ["failed"]
    assert len(notifications) == 1
    assert "no connection" in notifications[0]["message"]


async def test_late_task_is_skipped(
    hass: HomeAssistant, setup_entry: MockConfigEntry, script, notifications
) -> None:
    """A task later than the lateness limit (5 h) is not run."""
    failed = async_capture_events(hass, EVENT_TASK_FAILED)
    await upsert(hass, id="t", due="2026-10-21T06:00:00+00:00")
    await hass.async_block_till_done()

    assert script.calls == []
    task = await get_task(hass)
    assert task["status"] == "skipped"
    assert [event.data["status"] for event in failed] == ["skipped"]
    assert len(notifications) == 1


async def test_expires_always_runs_however_late(
    hass: HomeAssistant, setup_entry: MockConfigEntry, script
) -> None:
    """`expires: always` runs a task even many hours late."""
    await upsert(hass, id="t", due="2026-10-20T06:00:00+00:00", expires="always")
    await hass.async_block_till_done()

    assert len(script.calls) == 1
    assert (await get_task(hass))["status"] == "done"


async def test_task_within_lateness_runs(
    hass: HomeAssistant, setup_entry: MockConfigEntry, script
) -> None:
    """A task a little late (Home Assistant was off) still runs."""
    await upsert(hass, id="t", due="2026-10-21T08:00:00+00:00")
    await hass.async_block_till_done()
    assert (await get_task(hass))["status"] == "done"


async def test_missing_script(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """A script that does not exist is an error (it may appear later)."""
    await upsert(hass, id="t", due="2026-10-21T11:59:00+00:00", action="script.plan_missing")
    await hass.async_block_till_done()

    task = await get_task(hass)
    assert task["status"] == "error"
    assert "script.plan_missing" in task["last_error"]


@pytest.mark.parametrize("answer", [{}, {"reason": "no ok"}])
async def test_answer_without_ok_is_error(
    hass: HomeAssistant, setup_entry: MockConfigEntry, script, answer
) -> None:
    """The script must confirm with ok."""
    script.answers = [answer]
    await upsert(hass, id="t", due="2026-10-21T11:59:00+00:00")
    await hass.async_block_till_done()

    task = await get_task(hass)
    assert task["status"] == "error"
    assert "ok" in task["last_error"]


async def test_cancel_while_running(
    hass: HomeAssistant, setup_entry: MockConfigEntry, script
) -> None:
    """A task cancelled during the attempt stays cancelled."""
    script.gate = asyncio.Event()
    await upsert(hass, id="t", due="2026-10-21T11:59:00+00:00")
    for _ in range(5):
        await asyncio.sleep(0)
    assert (await get_task(hass))["status"] == "running"
    assert len(script.calls) == 1

    await hass.services.async_call(DOMAIN, "remove", {"id": "t"}, blocking=True)
    script.gate.set()
    await hass.async_block_till_done()

    assert (await get_task(hass))["status"] == "cancelled"


async def test_restart_during_attempt_retries(
    hass: HomeAssistant, config_entry: MockConfigEntry, frozen_now, hass_storage, script
) -> None:
    """An attempt interrupted by a restart is made again."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "tasks": [
                {
                    "uid": "u1",
                    "id": "t",
                    "due": "2026-10-21T11:58:00+00:00",
                    "action": "script.plan_switch",
                    "status": "running",
                    "attempts": 1,
                    "next_attempt": "2026-10-21T11:58:00+00:00",
                }
            ]
        },
    }
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    task = await get_task(hass)
    assert task["status"] == "done"
    assert task["attempts"] == 2
    assert len(script.calls) == 1


async def test_task_saved_by_older_version_runs(
    hass: HomeAssistant, config_entry: MockConfigEntry, frozen_now, hass_storage, script
) -> None:
    """A task saved before the executor existed (no next_attempt) still runs."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "tasks": [
                {
                    "uid": "u1",
                    "id": "t",
                    "due": "2026-10-21T11:58:00+00:00",
                    "action": "script.plan_switch",
                    "status": "scheduled",
                }
            ]
        },
    }
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (await get_task(hass))["status"] == "done"
