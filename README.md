# Zaya Planner

A Home Assistant integration that plans smart home tasks and runs them reliably:
it calls a script when a task is due, waits for the script to confirm, retries on failure
and reports what went wrong. Everything planned is shown on the “Schedule” calendar and in sensors.

> **Status: stage 3 of 6.** Tasks are stored, shown and run when due, with retries and
> notifications; rules plan tasks on a schedule. Next: trying it on a test instance, see [Roadmap](#roadmap).

## How it works

- **Task**: “at this time run this script with these parameters”.
  Tasks are created with the `zaya_planner.upsert` action from a rule, an automation or a script.
- **Rule**: a declarative description of when and what to plan, set up in the UI. Every day
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
| `zaya_planner_rule_run` | a rule ran its planner | `rule_id`, `title`, `ok`, `reason`, `next_run` |

## Rules

**Settings → Devices & services → Zaya Planner → Add rule.** A rule has:

| Field | Meaning |
|---|---|
| Rule ID | Latin letters, digits and `_`; cannot be changed later. Tasks of the rule carry it in `rule_id` |
| Title | Name for people |
| Planner script | A script with the allowed prefix (e.g. `script.plan_rule_floor`) that answers `{ok, reason}` |
| Parameters | Passed to the planner together with `rule_id` |
| Repeat | Every day, on weekdays, at weekends or every hour |
| Time | Local time of the run; for “every hour” only the minutes count |
| Enabled | A disabled rule does not run; tasks it already planned stay |

A rule is changed or removed from the same page (⋮ next to the rule).

- At its time the rule calls the planner; then the next run moves on by the repeat.
- Runs missed while Home Assistant was off are caught up **once**, as soon as it starts.
  The lateness limit does not apply to rules.
- A rule plans **only the future**: a task with `rule_id` whose `due` has passed is not created
  (`result: past`).
- A planner that fails (`ok: false`, error, timeout) is logged and reported; the rule runs again next time.
- Planning again is safe: tasks are identified by `id`, so `upsert` changes them instead of adding new ones.
- To drop what a rule planned: `zaya_planner.remove` with its `rule_id`.

The next run of every enabled rule is shown on the calendar (🗓️).

A planner for floor heating: every evening it plans “on” and “off” for the next morning.
Rule: ID `floor_heating`, planner `script.plan_rule_floor`, every day at 20:00, parameters
`{entity_id: switch.floor_heating, start: "06:40", minutes: 20}`.

```yaml
script:
  plan_rule_floor:
    alias: Plan rule · floor heating
    fields:
      rule_id: {}
      entity_id: {}
      start: {}
      minutes: {}
    sequence:
      - variables:
          # Today if the start is still ahead (a run caught up after midnight), else tomorrow
          on_at: >-
            {% set t = today_at(start) %}
            {{ (t if t > now() else t + timedelta(days=1)).isoformat() }}
          off_at: "{{ (as_datetime(on_at) + timedelta(minutes=minutes | int)).isoformat() }}"
      - action: zaya_planner.upsert
        data:
          id: floor_on
          title: Floor heating on
          due: "{{ on_at }}"
          action: script.plan_switch
          params: { entity_id: "{{ entity_id }}", state: "on" }
          rule_id: "{{ rule_id }}"
      - action: zaya_planner.upsert
        data:
          id: floor_off
          title: Floor heating off
          due: "{{ off_at }}"
          expires: always   # turning off must happen however late
          action: script.plan_switch
          params: { entity_id: "{{ entity_id }}", state: "off" }
          rule_id: "{{ rule_id }}"
      - variables:
          answer: { ok: true }
      - stop: planned
        response_variable: answer
```

## Actions

### `zaya_planner.upsert`: plan a task

- no unfinished task with this `id`: a new task is created;
- there is one and it is not due yet: it is changed;
- there is one and it is already due: it is **left alone** (it belongs to the executor);
  this is written to the logbook and the response has `result: locked`;
- a task of a rule (`rule_id` given) in the past is not created: `result: past`.

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

### `zaya_planner.run_rule`: run a rule now

`rule_id`: the rule to run. Its schedule stays as it is. Response: `{ok, reason}`.

## Entities

| Entity | Shows |
|---|---|
| `calendar.zaya_planner_schedule` “Schedule” | An event per task: planned and recently finished (cancelled tasks are not shown). The outcome is shown before the title: ⏳ running, ✅ done, ⚠️ error, ❌ failed, ⏭️ skipped. Plus the next run of each rule (🗓️) |
| `sensor.zaya_planner_next_task` “Next task” | Due time of the next planned task; the task itself in attributes |
| `sensor.zaya_planner_errors` “Errors” | Number of tasks in error, failed or skipped; the list in the `tasks` attribute |
| `sensor.zaya_planner_tasks` “Plan” | Number of unfinished tasks; **all** tasks in the `tasks` attribute |
| `sensor.zaya_planner_rules` “Rules” | Number of enabled rules; all rules with `next_run`, `last_run`, `last_result`, `last_error` in the `rules` attribute |

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
3. ✅ Rules: set up in the UI, `repeat` (daily / weekdays / weekends / hourly), catching up missed runs,
   `zaya_planner_rule_run` event, `zaya_planner.run_rule`.
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
