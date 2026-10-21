# Zaya Planner

A Home Assistant integration that plans smart home tasks and runs them reliably:
it calls a script when a task is due, waits for the script to confirm, retries on failure
and reports what went wrong. Everything planned is shown on the “Schedule” calendar and in sensors.

> **Status: stage 1 of 6.** The task store, the `upsert` / `remove` / `list` actions,
> the calendar and the sensors are done. The executor (running tasks when due) and rules
> come next, see [Roadmap](#roadmap).

## How it works

- **Task**: “at this time run this script with these parameters”.
  Tasks are created with the `zaya_planner.upsert` action from an automation, a script or (later) a rule.
- **Rule** (stage 3): a declarative description of when and what to plan. Once a day
  (or on weekdays, at weekends, every hour) it calls a planner script that works out the tasks.
- **Executor** (stage 2): part of the integration. When a task is due it calls the task's script,
  waits for its `{ok, reason}` response, retries with pauses, closes the task and logs the outcome.

Tasks are kept in `.storage/zaya_planner.tasks` and written to disk **immediately** on every change,
so after a restart Home Assistant carries on exactly where it stopped.

## Installation

Install with HACS as a custom repository:

1. HACS → ⋮ → **Custom repositories** → `https://github.com/shtrasser-dev/Zaya.Planner`, type **Integration**.
2. Find **Zaya Planner**, download it and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Zaya Planner.**

Requires Home Assistant 2026.10 or newer.

### Settings

**Settings → Devices & services → Zaya Planner → Configure:**

| Setting | Default | Meaning |
|---|---|---|
| Lateness limit | 5 h | A task without its own `expires` is not run if it is later than this (for example after Home Assistant was off) |
| Allowed actions | `script.plan_` | Only scripts whose entity id starts with this prefix can be tasks |
| Keep finished tasks | 3 days | Older finished tasks are removed |

## Task

| Field | Meaning |
|---|---|
| `id` | Key of the **current** task. Unique among unfinished tasks; finished tasks with the same id stay in history |
| `title` | Name for people (calendar, plan) |
| `due` | When to run. A time without a time zone is local time |
| `action` | Script to run, e.g. `script.plan_switch` (only with the allowed prefix) |
| `params` | Passed to the script as its `data` |
| `expires` | Do not run after this time; `always`: run however late (for turning things off); empty: the lateness limit from the settings |
| `rule_id` | Rule that created the task |
| `status` | `scheduled` · `running` · `done` · `error` (retrying) · `failed` · `skipped` · `cancelled` |
| `attempts`, `last_error`, `result`, `finished_at` | Filled in by the executor |

## Actions

### `zaya_planner.upsert`: plan a task

- no unfinished task with this `id`: a new task is created;
- there is one and it is not due yet: it is changed;
- there is one and it is already due: it is **left alone** (it belongs to the executor);
  this is written to the logbook and the response has `result: locked`.

```yaml
action: zaya_planner.upsert
data:
  id: floor_heating_on
  title: Floor heating on
  due: "2026-10-22 06:40:00"
  action: script.plan_switch
  params:
    entity_id: switch.floor_heating
    state: "on"
response_variable: r   # optional: {result: created|updated|locked, task: {...}}
```

### `zaya_planner.remove`: cancel tasks

- `id`: cancel the unfinished task with this id, whatever its state;
- `rule_id`: cancel the rule's tasks that are not due yet.

Cancelled tasks stay in history with status `cancelled`. Response: `{cancelled: [id, …]}`.

### `zaya_planner.list`: list tasks

Filters: `id`, `rule_id`, `status` (one or a list). Returns `{tasks: [...]}` ordered by due time.

## Entities

| Entity | Shows |
|---|---|
| `calendar.zaya_planner_schedule` “Schedule” | An event per task: planned and recently finished (cancelled tasks are not shown) |
| `sensor.zaya_planner_next_task` “Next task” | Due time of the next planned task; the task itself in attributes |
| `sensor.zaya_planner_errors` “Errors” | Number of tasks in error or failed; the list in the `tasks` attribute |
| `sensor.zaya_planner_tasks` “Plan” | Number of unfinished tasks; **all** tasks in the `tasks` attribute |

Entity ids do not depend on the language of Home Assistant. Names, statuses and messages
are translated (English and Russian).

A “Plan” markdown card:

```yaml
type: markdown
title: Plan
content: >-
  {% for t in state_attr('sensor.zaya_planner_tasks', 'tasks') or [] %}
  - **{{ as_timestamp(t.due) | timestamp_custom('%d.%m %H:%M') }}** {{ t.title or t.id }}: {{ t.status_text }}
  {% endfor %}
```

## Roadmap

1. ✅ Skeleton, task store, `upsert` / `remove` / `list`, calendar, sensors.
2. Executor: running tasks when due, waiting for the script's response, retries, notifications,
   `zaya_planner_task_done` / `zaya_planner_task_failed` events, logbook.
3. Rules: `repeat` (daily / weekdays / weekends / hourly), catching up missed runs, `zaya_planner_rule_run` event.
4. A test Home Assistant instance, installation through HACS.
5. Moving real tasks over, one at a time.
6. Optionally, a custom Lovelace card.

## Development

Tests use `pytest` and [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
and run in GitHub Actions on every push (Home Assistant does not run on Windows).
Locally, on Linux or macOS with Python 3.14:

```bash
pip install -r requirements_test.txt
pytest
```

## License

[MIT](LICENSE)
