# Zaya Planner

A Home Assistant integration that plans smart home tasks and runs them reliably:
when a task is due it switches the devices (or calls a script), waits until they confirm,
retries on failure and reports what went wrong. Everything planned is shown on the
“Schedule” calendar and in sensors.

> **Status: stage 3 of 6.** Tasks are stored, shown and run when due, with retries and
> notifications. Rules are ordinary automations, with ready-made blueprints.
> See [Roadmap](#roadmap).

## How it works

- **Task**: “at this time turn these devices on” (or “run this script with these parameters”).
  Tasks are created with the `zaya_planner.upsert` action.
- **Rule**: an ordinary Home Assistant **automation** that plans tasks: when and what to do.
  Typical rules are made from the [blueprints](#rules-automations-and-blueprints) in this
  repository with a form, without YAML.
- **Executor**: part of the integration. When a task is due it runs the task's action,
  waits for confirmation, retries with pauses, closes the task and logs the outcome.

Rules decide *what* should happen; the planner makes sure it *does* happen.
Tasks are kept in `.storage/zaya_planner.tasks` and written to disk **immediately** on every change,
so after a restart Home Assistant carries on exactly where it stopped.

## Installation

Install with HACS as a custom repository:

1. HACS → ⋮ → **Custom repositories** → `https://github.com/shtrasser-dev/Zaya.Planner`, type **Integration**.
2. Find **Zaya Planner**, download it and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Zaya Planner.**
4. Import the blueprints you need (see [Rules](#rules-automations-and-blueprints)).

Requires Home Assistant 2026.10 or newer.

### Settings

**Settings → Devices & services → Zaya Planner → Configure:**

| Setting | Default | Meaning |
|---|---|---|
| Lateness limit | 5 h | A task without its own `expires` is not run if it is later than this (for example after Home Assistant was off) |
| Allowed actions | `script.plan_` | Scripts whose entity id starts with this prefix can be tasks (besides the built-in `zaya_planner.ensure_state`) |
| Keep finished tasks | 3 days | Older finished tasks are removed |
| Notifications | empty | Action for notifications about failing tasks, e.g. `notify.mobile_app_phone` or a script with `title` and `message` fields. Empty: a notification in Home Assistant |

## Rules: automations and blueprints

A rule is an automation: it lives in **Settings → Automations & scenes** with all the others,
is edited in the visual editor, can be turned off, and its runs are in its traces.
The automation calls `zaya_planner.upsert` to plan tasks; the planner runs them.

### Blueprints

Import a blueprint: **Settings → Automations & scenes → Blueprints → Import blueprint**, paste its URL.
Then **Create automation** from it and fill in the form.

| Blueprint | URL | What it does |
|---|---|---|
| On for a while | `https://github.com/shtrasser-dev/Zaya.Planner/blob/main/blueprints/automation/zaya_planner/on_for_a_while.yaml` | Turns devices on at a set time for N minutes on the chosen days (floor heating, a pump) |
| Lights at night | `https://github.com/shtrasser-dev/Zaya.Planner/blob/main/blueprints/automation/zaya_planner/night_light.yaml` | Keeps lights on from sunset to sunrise, with offsets |
| Charge by departure | `https://github.com/shtrasser-dev/Zaya.Planner/blob/main/blueprints/automation/zaya_planner/charge_by_departure.yaml` | Charges so that charging ends before a departure (from a helper or a sensor) |

Each blueprint plans two tasks, “on” and “off” (the “off” runs however late), and plans
again when a task of the rule ends, at a fixed time every day, after Home Assistant starts
and whenever any automation is saved. Planning again is safe: tasks are identified by
`id`, so `upsert` changes them instead of adding new ones. If Home Assistant was off when
a window started, the devices are turned on as soon as it is back. A planned “off” is
never cancelled by a rule.

### Your own rules

Any automation or script can plan tasks. Pass the automation's id as `rule_id`
(`{{ this.entity_id }}`): tasks of a rule are never planned in the past (`result: past`),
and `zaya_planner.remove` with that `rule_id` cancels what the rule planned.

```yaml
alias: Floor heating
triggers:
  - trigger: time
    at: "20:00:00"
actions:
  - action: zaya_planner.upsert
    data:
      id: floor_on
      title: Floor heating on
      due: "{{ (today_at('06:40') + timedelta(days=1)).isoformat() }}"
      action: zaya_planner.ensure_state
      params: { entity_id: switch.floor_heating, state: "on" }
      rule_id: "{{ this.entity_id }}"
  - action: zaya_planner.upsert
    data:
      id: floor_off
      title: Floor heating off
      due: "{{ (today_at('07:00') + timedelta(days=1)).isoformat() }}"
      expires: always   # turning off must happen however late
      action: zaya_planner.ensure_state
      params: { entity_id: switch.floor_heating, state: "off" }
      rule_id: "{{ this.entity_id }}"
```

## Family schedule (in progress)

The next part plans the house from the family's days; see [docs/design.md](docs/design.md).
Done so far (step 1 of 6):

- **People**: in **Configure**, choose who takes part (from Settings → People; children can be
  added there without a login). Each person becomes a device “Zaya: Name” with settings:
  *Getting ready* (minutes), *Wake up for trips*, *Car*, *Usually drives*.
- **Cars**: **Add car** on the integration page: name and charger switch. Each car is a device
  “Zaya: Leaf” with *Charged before departure* (minutes).
- **Person calendars**: , ordinary Home Assistant calendars (iCalendar
  files, with repeats and single-occurrence changes). Besides the usual fields an event has
  *Kind* (event, wake-up, cancellation) and a *Trip*: take there / pick up / both, who goes,
  who drives, car, travel time. These fields are kept when the event is edited in the
  Calendar panel.
- Actions on person calendars: ,   (one occurrence or the series),  (events with their trip fields).
  Trip defaults: the calendar's owner and who usually drives them go; the only person with a
  car drives; the driver's car; the travel time of the last trip with the same title.

Next: wake-ups and departures, the house plan, anchors for rules.

## Task

| Field | Meaning |
|---|---|
| `id` | Key of the **current** task. Unique among unfinished tasks; finished tasks with the same id stay in history |
| `title` | Name for people (calendar, plan) |
| `due` | When to run. A time without a time zone is local time. Can be given as the time of an entity plus an offset (see `upsert`) |
| `action` | `zaya_planner.ensure_state`, or a script with the allowed prefix, e.g. `script.plan_switch` |
| `params` | Passed to the action as its data |
| `expires` | Do not run after this time; `always`: run however late (for turning things off); empty: the lateness limit from the settings |
| `rule_id` | Rule (automation) that planned the task |
| `status` | `scheduled` · `running` · `done` · `error` (retrying) · `failed` · `skipped` · `cancelled` |
| `attempts`, `last_error`, `result`, `finished_at` | Filled in by the executor |

## Running tasks

When a task is due, the executor runs its action with `params` as data and waits for the
answer (at most 2 minutes). The answer must be `{ok: true}` or `{ok: false, reason: "..."}`.

**`zaya_planner.ensure_state`** is built in and answers like that: it turns devices
(`entity_id`: switches, lights, input booleans, fans…) `on` or `off`, leaves alone those
already in that state and waits up to 20 seconds for each one to report the new state.
A device that is not connected or does not confirm is named in the reason.

For anything else, write a script with the allowed prefix that checks what it did and answers:

```yaml
script:
  plan_boiler_mode:
    fields:
      mode: {}
    sequence:
      - action: select.select_option
        target: { entity_id: select.boiler_mode }
        data: { option: "{{ mode }}" }
      - wait_template: "{{ is_state('select.boiler_mode', mode) }}"
        timeout: "00:00:30"
        continue_on_timeout: true
      - variables:
          answer:
            ok: "{{ is_state('select.boiler_mode', mode) }}"
            reason: "boiler is {{ states('select.boiler_mode') }}"
      - stop: done
        response_variable: answer
```

- `ok: true`: the task is **done**.
- `ok: false`, no `ok` in the answer, an error or a timeout: the task is in **error** and is
  retried after 1, 1, 2, 5 minutes, then every 5 minutes. Each new reason goes to the logbook.
- An error that lasts 10 minutes is reported once (see *Notifications* in the settings).
- When the task expires (`expires`, or `due` + the lateness limit) attempts stop:
  the task is **failed** (it was tried) or **skipped** (it was too late to try, e.g. Home Assistant was off).
  Both are reported. With `expires: always` the task is retried until it succeeds.
- An attempt interrupted by a restart of Home Assistant is made again.
- A task cancelled while its action runs stays cancelled.

Events for automations:

| Event | When | Data |
|---|---|---|
| `zaya_planner_task_done` | a task is done | `id`, `uid`, `title`, `action`, `rule_id`, `attempts`, `result` |
| `zaya_planner_task_failed` | a task failed or was skipped | the same plus `status` and `reason` |

## Actions

### `zaya_planner.upsert`: plan a task

The form has three parts.

- **When**: a date and time (`due`), *or* the time of an entity (`due_entity`): Sun's
  “Next sunset” / “Next sunrise” (`sensor.sun_next_setting`, `sensor.sun_next_rising`),
  a date-and-time helper, any timestamp sensor. Plus an `offset` in minutes (+ after, − before).
  The entity's value is read once, when the task is planned; a time-only helper means its
  next occurrence. To follow later changes, plan again (rules do).
- **What**: `devices` + `state` (`on`/`off`), run by the built-in `zaya_planner.ensure_state`;
  *or* a script (`action`) with `params`.
- **More**: `expires`, `rule_id`.

Lights 30 minutes after sunset, without templates:

```yaml
action: zaya_planner.upsert
data:
  id: gate_light_on
  title: Gate light on
  due_entity: sensor.sun_next_setting
  offset: 30
  devices: [switch.gate_light]
  state: "on"
```

What happens to a task with the same `id`:

- no unfinished task with this `id`: a new task is created;
- there is one and it is not due yet: it is changed;
- there is one and it is already due: it is **left alone** (it belongs to the executor);
  this is written to the logbook and the response has `result: locked`;
- a task of a rule (`rule_id` given) in the past is not created: `result: past`.

Response (optional): `{result: created|updated|locked|past, task: {...}}`.

### `zaya_planner.remove`: cancel tasks

- `id`: cancel the unfinished task with this id, whatever its state;
- `rule_id`: cancel the rule's tasks that are not due yet.

Cancelled tasks stay in history with status `cancelled`. Response: `{cancelled: [id, …]}`.

### `zaya_planner.list`: list tasks

Filters: `id`, `rule_id`, `status` (one or a list). Returns `{tasks: [...]}` ordered by due time.

### `zaya_planner.ensure_state`: turn on/off and confirm

`entity_id` (one or a list), `state` (`on`/`off`). Response: `{ok, reason}`. See [Running tasks](#running-tasks).

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
2. ✅ Executor: running tasks when due, waiting for confirmation, retries, notifications,
   `zaya_planner_task_done` / `zaya_planner_task_failed` events, logbook.
3. ✅ Rules as automations: blueprints “on for a while”, “lights at night”, “charge by departure”;
   built-in `zaya_planner.ensure_state`.
4. Family schedule: people, cars, person calendars with trips (in progress, see [design](docs/design.md)).
5. House plan from the family schedule, then a custom card.
6. Moving the real house over, one task at a time.

## Development

Tests use `pytest` and [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
and run in GitHub Actions on every push (Home Assistant does not run on Windows).
The blueprints are tested too: each one is set up as a real automation.
Locally, on Linux or macOS with Python 3.14:

```bash
pip install -r requirements_test.txt
pytest
```

## License

[MIT](LICENSE)
