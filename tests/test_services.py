"""Tests of the upsert / remove / list actions and of storage."""

from datetime import datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.zaya_planner.const import (
    DOMAIN,
    SERVICE_LIST,
    SERVICE_REMOVE,
    STORAGE_KEY,
)
from homeassistant.const import EVENT_LOGBOOK_ENTRY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .common import upsert

DUE = "2026-10-22T06:40:00+00:00"


async def list_tasks(hass: HomeAssistant, **data) -> list[dict]:
    """Call zaya_planner.list."""
    response = await hass.services.async_call(
        DOMAIN, SERVICE_LIST, data, blocking=True, return_response=True
    )
    return response["tasks"]


async def remove(hass: HomeAssistant, **data) -> list[str]:
    """Call zaya_planner.remove and return ids of cancelled tasks."""
    response = await hass.services.async_call(
        DOMAIN, SERVICE_REMOVE, data, blocking=True, return_response=True
    )
    return response["cancelled"]


async def test_upsert_creates_task(
    hass: HomeAssistant, setup_entry: MockConfigEntry, hass_storage
) -> None:
    """A new id creates a task, saved to disk right away."""
    response = await upsert(
        hass,
        id="floor_morning",
        title="Floor heating",
        due=DUE,
        params={"entity_id": "switch.floor", "state": "on"},
        rule_id="floor",
    )
    assert response["result"] == "created"
    task = response["task"]
    assert task["id"] == "floor_morning"
    assert task["title"] == "Floor heating"
    assert task["due"] == DUE
    assert task["action"] == "script.plan_switch"
    assert task["params"] == {"entity_id": "switch.floor", "state": "on"}
    assert task["expires"] is None
    assert task["rule_id"] == "floor"
    assert task["status"] == "scheduled"
    assert task["attempts"] == 0

    # No delay: the task is already in .storage
    saved = hass_storage[STORAGE_KEY]["data"]["tasks"]
    assert [item["uid"] for item in saved] == [task["uid"]]


async def test_upsert_updates_task_before_due(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """The same id changes the open task while it is not due."""
    first = await upsert(hass, id="t", title="Old", due=DUE)
    second = await upsert(
        hass, id="t", title="New", due="2026-10-22T07:00:00+00:00", expires="always"
    )
    assert second["result"] == "updated"
    assert second["task"]["uid"] == first["task"]["uid"]
    assert second["task"]["title"] == "New"
    assert second["task"]["due"] == "2026-10-22T07:00:00+00:00"
    assert second["task"]["expires"] == "always"
    assert len(await list_tasks(hass)) == 1


async def test_upsert_leaves_due_task_to_executor(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now
) -> None:
    """An open task whose due time has come is not changed."""
    created = await upsert(hass, id="t", title="Old", due=DUE)
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)

    frozen_now.move_to("2026-10-22T06:41:00+00:00")
    response = await upsert(hass, id="t", title="New", due="2026-10-23T06:40:00+00:00")
    await hass.async_block_till_done()

    assert response["result"] == "locked"
    assert response["task"] == created["task"]
    assert len(logbook) == 1
    assert logbook[0].data["domain"] == DOMAIN
    assert "Old" in logbook[0].data["message"]


async def test_upsert_after_finish_creates_new_task(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """A finished task stays in history; the same id makes a new task."""
    first = await upsert(hass, id="t", due=DUE)
    assert await remove(hass, id="t") == ["t"]
    second = await upsert(hass, id="t", due=DUE)

    assert second["result"] == "created"
    assert second["task"]["uid"] != first["task"]["uid"]
    statuses = sorted(task["status"] for task in await list_tasks(hass, id="t"))
    assert statuses == ["cancelled", "scheduled"]


async def test_upsert_naive_time_is_local(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Time without a time zone means local time."""
    response = await upsert(hass, id="t", due="2026-10-22 06:40:00")
    local = dt_util.get_default_time_zone()
    expected = datetime(2026, 10, 22, 6, 40, tzinfo=local)
    assert dt_util.parse_datetime(response["task"]["due"]) == expected


async def test_upsert_rejects_other_actions(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Only scripts with the allowed prefix can be tasks."""
    with pytest.raises(ServiceValidationError) as err:
        await upsert(hass, id="t", due=DUE, action="script.turn_everything_off")
    assert err.value.translation_key == "action_not_allowed"


async def test_upsert_rejects_expires_before_due(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """A task that expires before it is due could never run."""
    with pytest.raises(ServiceValidationError) as err:
        await upsert(hass, id="t", due=DUE, expires="2026-10-22T06:00:00+00:00")
    assert err.value.translation_key == "expires_before_due"


async def test_remove_by_rule_cancels_only_future(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now
) -> None:
    """A rule cancels its tasks that are not due; other rules are untouched."""
    await upsert(hass, id="a", due="2026-10-21T12:30:00+00:00", rule_id="r")
    await upsert(hass, id="b", due="2026-10-21T14:00:00+00:00", rule_id="r")
    await upsert(hass, id="c", due="2026-10-21T14:00:00+00:00", rule_id="other")

    frozen_now.move_to("2026-10-21T13:00:00+00:00")
    assert await remove(hass, rule_id="r") == ["b"]

    cancelled = [task["id"] for task in await list_tasks(hass, status="cancelled")]
    assert cancelled == ["b"]


async def test_remove_by_id_cancels_due_task(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now
) -> None:
    """Cancelling by id works even when the task is already due."""
    await upsert(hass, id="a", due="2026-10-21T12:30:00+00:00")
    frozen_now.move_to("2026-10-21T13:00:00+00:00")
    assert await remove(hass, id="a") == ["a"]
    assert await remove(hass, id="a") == []

    tasks = await list_tasks(hass)
    assert tasks[0]["status"] == "cancelled"
    assert tasks[0]["finished_at"] == "2026-10-21T13:00:00+00:00"


async def test_remove_needs_id_or_rule(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """remove without id and rule_id is an error."""
    with pytest.raises(Exception):  # noqa: B017 - schema error type is HA's
        await remove(hass)


async def test_list_filters(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """list filters by id, rule and status and is ordered by due."""
    await upsert(hass, id="late", due="2026-10-23T06:00:00+00:00", rule_id="r")
    await upsert(hass, id="early", due="2026-10-22T06:00:00+00:00")
    await remove(hass, id="early")

    assert [task["id"] for task in await list_tasks(hass)] == ["early", "late"]
    assert [task["id"] for task in await list_tasks(hass, rule_id="r")] == ["late"]
    assert [
        task["id"] for task in await list_tasks(hass, status=["cancelled"])
    ] == ["early"]


async def test_tasks_survive_restart(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """After reloading, tasks are exactly as they were."""
    created = await upsert(hass, id="t", title="Floor", due=DUE, expires="always")

    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()

    assert await list_tasks(hass) == [created["task"]]


async def test_cleanup_removes_old_finished_tasks(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now
) -> None:
    """Finished tasks older than 3 days are removed; open ones stay."""
    await upsert(hass, id="old", due="2026-10-21T12:30:00+00:00")
    await upsert(hass, id="open", due="2026-10-30T12:00:00+00:00")
    await remove(hass, id="old")

    frozen_now.move_to(dt_util.utcnow() + timedelta(days=3, hours=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert [task["id"] for task in await list_tasks(hass)] == ["open"]


async def test_rule_task_in_past_is_not_created(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """A rule plans only the future: its task in the past is skipped."""
    response = await upsert(
        hass, id="t", due="2026-10-21T11:00:00+00:00", rule_id="automation.floor"
    )
    assert response == {"result": "past", "task": None}
    assert await list_tasks(hass) == []


async def test_builtin_action_is_allowed(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """zaya_planner.ensure_state can be a task's action without a script."""
    response = await upsert(
        hass,
        id="t",
        due=DUE,
        action="zaya_planner.ensure_state",
        params={"entity_id": ["switch.floor"], "state": "on"},
    )
    assert response["result"] == "created"
