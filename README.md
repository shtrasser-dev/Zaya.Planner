# Zaya Planner

A Home Assistant integration that plans smart home tasks and runs them reliably:
it calls a script when a task is due, waits for the script to confirm, retries on failure
and reports what went wrong. Everything planned is shown on the “Schedule” calendar and in sensors.

> **Status: stage 2 of 6.** Tasks are stored, shown and run when due, with retries and
> notifications. Rules (planning tasks declaratively) come next, see [Roadmap](#roadmap).

## How it works

- **Task**: “at this time run this script with these parameters”.
  Tasks are created with the `zaya_planner.upsert` action from an automation, a script or (later) a rule.
- **Rule** (stage 3): a declarative description of when and what to plan. Once a day
  (or on weekdays, at weekends, every hour) it calls a planner script that works out the tasks.
- **Executor**: part of the integration. When a task is due it calls the task's script,
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
| Notifications | empty | Action for notifications about failing tasks, e.g. `notify.mobile_app_phone` or a script with `title` and `message` fields. Empty: a notification in Home Assistant |

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

## Running tasks

When a task is due, the executor calls its script with `params` as data and waits
for the answer (at most 2 minutes). The script must check that it did what it should and answer:

```yaml
script:
  plan_switch:
    alias: Plan · switch
    fields:
      entity_id: {}
      state: {}
    sequence:
      - action: "switch.turn_{{ state }}"
        target: { entity_id: "{{ entity_id }}" }
        continue_on_error: true
      - wait_template: "{{ is_state(entity_id, state) }}"
        timeout: "00:00:20"
        continue_on_timeout: true
      - variables:
          answer:
            ok: "{{ is_state(entity_id, state) }}"
            reason: "{{ '' if is_state(entity_id, state) else entity_id ~ ' is ' ~ states(entity_id) }}"
      - stop: done
        response_variable: answer
```

- `ok: true`: the task is **done**.
- `ok: false` (with a `reason`), no `ok` in the answer, an error or a timeout: the task is in
  **error** and is retried after 1, 1, 2, 5 minutes, then every 5 minutes. Each new reason goes to the logbook.
- An error that lasts 10 minutes is reported once (see *Notifications* in the settings).
- When the task expires (`expires`, or `due` + the lateness limit) attempts stop:
  the task is **failed** (it was tried) or **skipped** (it was too late to try, e.g. Home Assistant was off).
  Both are reported. With `expires: always` the task is retried until it succeeds.
- An attempt interrupted by a restart of Home Assistant is made again.
- A task cancelled while its script runs stays cancelled.

Events for automations:

| Event | When | Data |
|---|---|---|
| `zaya_planner_task_done` | a task is done | `id`, `uid`, `title`, `action`, `rule_id`, `attempts`, `result` |
| `zaya_planner_task_failed` | a task failed or was skipped | the same plus `status` and `reason` |

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
| `calendar.zaya_planner_schedule` “Schedule” | An event per task: planned and recently finished (cancelled tasks are not shown). The outcome is shown before the title: ⏳ running, ✅ done, ⚠️ error, ❌ failed, ⏭️ skipped |
| `sensor.zaya_planner_next_task` “Next task” | Due time of the next planned task; the task itself in attributes |
| `sensor.zaya_planner_errors` “Errors” | Number of tasks in error, failed or skipped; the list in the `tasks` attribute |
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
2. ✅ Executor: running tasks when due, waiting for the script's response, retries, notifications,
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
