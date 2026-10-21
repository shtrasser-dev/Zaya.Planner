"""Tests of the calendar and sensors."""

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .common import upsert

CALENDAR = "calendar.zaya_planner_schedule"
NEXT_TASK = "sensor.zaya_planner_next_task"
ERRORS = "sensor.zaya_planner_errors"
TASKS = "sensor.zaya_planner_tasks"


async def test_entities_without_tasks(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """With no tasks everything is empty."""
    assert hass.states.get(CALENDAR).state == STATE_OFF
    assert hass.states.get(NEXT_TASK).state == STATE_UNKNOWN
    assert hass.states.get(ERRORS).state == "0"
    assert hass.states.get(TASKS).state == "0"
    assert hass.states.get(TASKS).attributes["tasks"] == []


async def test_sensors_follow_tasks(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Sensors show the next task and all tasks."""
    await upsert(hass, id="b", title="Second", due="2026-10-22T08:00:00+00:00")
    await upsert(hass, id="a", title="First", due="2026-10-22T06:40:00+00:00")
    await hass.async_block_till_done()

    next_task = hass.states.get(NEXT_TASK)
    assert next_task.state == "2026-10-22T06:40:00+00:00"
    assert next_task.attributes["id"] == "a"
    assert next_task.attributes["title"] == "First"
    assert next_task.attributes["status_text"] == "scheduled"

    tasks = hass.states.get(TASKS)
    assert tasks.state == "2"
    assert [task["id"] for task in tasks.attributes["tasks"]] == ["a", "b"]

    await hass.services.async_call(
        "zaya_planner", "remove", {"id": "a"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(NEXT_TASK).attributes["id"] == "b"
    assert hass.states.get(TASKS).state == "1"


async def test_calendar_events(
    hass: HomeAssistant, setup_entry: MockConfigEntry, frozen_now
) -> None:
    """Every task but cancelled ones is an event; the calendar is on during it."""
    await upsert(
        hass,
        id="a",
        title="Floor heating",
        due="2026-10-21T12:10:00+00:00",
        params={"state": "on"},
    )
    await upsert(hass, id="b", due="2026-10-22T06:40:00+00:00")
    await hass.services.async_call("zaya_planner", "remove", {"id": "b"}, blocking=True)
    await hass.async_block_till_done()

    state = hass.states.get(CALENDAR)
    assert state.state == STATE_OFF
    assert state.attributes["message"] == "Floor heating"

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": CALENDAR,
            "start_date_time": "2026-10-21T00:00:00+00:00",
            "end_date_time": "2026-10-23T00:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    events = response[CALENDAR]["events"]
    assert [event["summary"] for event in events] == ["Floor heating"]
    assert "script.plan_switch" in events[0]["description"]
    assert '{"state": "on"}' in events[0]["description"]

    frozen_now.move_to("2026-10-21T12:10:30+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(CALENDAR).state == STATE_ON
