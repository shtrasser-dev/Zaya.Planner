# Zaya Planner

A Home Assistant integration that plans smart home tasks and runs them reliably:
when a task is due it switches the devices (or calls a script), waits until they confirm,
retries on failure and reports what went wrong. Everything planned is shown on the
“Schedule” calendar and in sensors.

> **Status:** tasks are stored, shown and run when due, with retries and notifications;
> rules are ordinary automations with one action, `plan_window`; the family schedule
> (people, calendars, trips) gives the house plan. See [Roadmap](#roadmap).

## How it works

- **Task**: “at this time turn these devices on” (or “run this script with these parameters”).
  Tasks are created with the `zaya_planner.upsert` action.
- **Rule**: an ordinary Home Assistant **automation** that plans tasks: when and what to do.
  It is made in the visual editor: trigger “Zaya Planner: time to plan”, your conditions,
  and the action `zaya_planner.plan_window` (see [Rules](#rules-automations-with-plan_window)).
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

Requires Home Assistant 2026.10 or newer.

### Settings

**Settings → Devices & services → Zaya Planner → Configure:**

| Setting | Default | Meaning |
|---|---|---|
| Lateness limit | 5 h | A task without its own `expires` is not run if it is later than this (for example after Home Assistant was off) |
| Allowed actions | `script.plan_` | Scripts whose entity id starts with this prefix can be tasks (besides the built-in `zaya_planner.ensure_state`) |
| Keep finished tasks | 3 days | Older finished tasks are removed |
| Notifications | empty | Action for notifications about failing tasks, e.g. `notify.mobile_app_phone` or a script with `title` and `message` fields. Empty: a notification in Home Assistant |

## Rules: automations with `plan_window`

A rule is an ordinary automation, made in the visual editor:

- **When**: the trigger **“Zaya Planner: time to plan”** (Device → Zaya Planner). It fires when
  Home Assistant has started, when automations are saved, when a task ends, when the house plan
  changes, and every 15 minutes as a safety net.
- **And if**: any conditions of your own (“only if the bathroom is below 22 °C”).
- **Then**: the action **`zaya_planner.plan_window`**: keep devices on from a start to an end.

Planning again with nothing changed changes nothing, so the rule may run as often as it likes.

### `zaya_planner.plan_window`: plan a window

The window that is going on now, or the next one. Its **start** is one of:

- a **time** of day (with **days**): 06:40 on weekdays;
- **the time of an entity** + offset: a wake-up − 20 min, sunset + 30 min (with several
  entities the earliest counts);
- **minutes before the end** (a number, or a sensor with the number as a fallback): a charging time.

Its **end** is one of:

- a **time** of day (the first one after the start);
- **the time of an entity** + offset: sunrise − 30 min, a departure − 30 min;
- **minutes after the start** (a number or a sensor): the length.

What the action takes care of:

- an “on” task at the start and an “off” task at the end; the “off” runs however late;
- each window has its own “off”: when an anchor jumps to the next day (a wake-up is over),
  the window that is open still ends as planned;
- if there is no window any more (the anchor has no time), the planned “on” is cancelled and
  a planned “off” stays;
- planned inside the window (e.g. after a restart): devices that are not on are turned on right away.

Examples:

```yaml
# Floor heating 20 minutes around Dad's or Mum's wake-up, whichever is earlier
alias: Floor heating
triggers:
  - trigger: device
    domain: zaya_planner
    device_id: <Zaya Planner device>
    type: time_to_plan
conditions:
  - condition: numeric_state
    entity_id: sensor.bathroom_temperature
    below: 22
actions:
  - action: zaya_planner.plan_window
    data:
      name: Floor heating
      devices: [switch.floor_heating]
      start_entity: [sensor.zaya_dad_next_wake_up, sensor.zaya_mum_next_wake_up]
      start_offset: -20
      duration: 40
```

```yaml
# Other windows, same trigger
- action: zaya_planner.plan_window      # 06:40–07:00 on weekdays
  data: { name: Pump, devices: [switch.pump], start_time: "06:40", duration: 20,
          days: [mon, tue, wed, thu, fri] }
- action: zaya_planner.plan_window      # lights from sunset + 30 to sunrise − 30
  data: { name: Gate light, devices: [switch.gate_light],
          start_entity: [sensor.sun_next_setting], start_offset: 30,
          end_entity: [sensor.sun_next_rising], end_offset: -30 }
- action: zaya_planner.plan_window      # charged 30 min before the first departure
  data: { name: Leaf charging, devices: [switch.leaf_charger],
          end_entity: [sensor.zaya_leaf_first_departure], end_offset: -30,
          start_before_end_entity: sensor.leaf_charge_minutes, start_before_end: 120 }
```

Tasks of a window are `window.<name>:on` and `window.<name>:off:<date>`; `rule_id` is
`window.<name>`, so `zaya_planner.remove` with it cancels what the window planned.

### Single moments

For one action at one moment (“turn the boiler off at 23:00”, “notify 30 minutes before the
departure”) use `zaya_planner.upsert` with a time or the time of an entity + offset.

## Family schedule (in progress)

The next part plans the house from the family's days; see [docs/design.md](docs/design.md).
Done so far (steps 1–3 of 6):

- **People**: in **Configure**, choose who takes part (from Settings → People; children can be
  added there without a login). Each person becomes a device “Zaya: Name” with settings:
  *Getting ready* (minutes), *Wake up for trips*, *Car*, *Usually drives*.
- **Cars**: **Add car** on the integration page: name and charger switch. Each car is a device
  “Zaya: Leaf” with *Charged before departure* (minutes).
- **Person calendars**: `calendar.zaya_<person>`, ordinary Home Assistant calendars (iCalendar
  files, with repeats and single-occurrence changes). Besides the usual fields an event has
  *Kind* (event, wake-up, cancellation) and a *Trip*: take there / pick up / both, who goes,
  who drives, car, travel time. These fields are kept when the event is edited in the
  Calendar panel.
- Actions on person calendars: `zaya_planner.add_event`, `zaya_planner.set_event_details`
  (one occurrence or the series), `zaya_planner.get_events` (events with their trip fields).
  Trip defaults: the calendar's owner and who usually drives them go; the only person with a
  car drives; the driver's car; the travel time of the last trip with the same title.

**House plan** (step 2): worked out from the person calendars for the next 8 days and kept
up to date on every change.

- **Departures**: a trip “take there” leaves *start − travel time*, “pick up” leaves
  *end − travel time*, “both” makes two departures.
- **Wake-ups**: wake-up events in a person's calendar, and for every person going on a trip
  (with *Wake up for trips* on) *departure − getting ready*. A person's wake-up of the day is
  the earliest one.
- **Cancellations** (events of kind *cancellation*) switch off what happens in their time in
  the same calendar: events inside them do not happen, events starting inside start when the
  cancellation ends, events ending inside end when it starts, wake-ups inside are dropped.
- `calendar.zaya_planner_house_plan` “House plan” shows departures (🚗) and wake-ups (⏰) with
  their reasons.
- Anchors for rules (timestamp sensors): `sensor.zaya_<person>_next_wake_up`,
  `sensor.zaya_<person>_next_departure`, `sensor.zaya_planner_first_wake_up` (anyone in the house),
  `sensor.zaya_<car>_first_departure` (the car's next first departure of a day).
- **Android alarm**: set *Alarm on phone* and *Set the alarm at* (21:00) on a person's device;
  at that time the phone gets an alarm for tomorrow's wake-up. If the wake-up moves later, the
  phone gets a notification to fix the alarm.

**Rules from the plan** (step 3): anchors are used by `plan_window` (see
[Rules](#rules-automations-with-plan_window)); the car's *First departure of the day* sensor also
carries its charger and margin as attributes.

Next: weekly timetables, then the dashboard card.

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
3. ✅ Rules as automations: trigger “time to plan”, `zaya_planner.plan_window`,
   built-in `zaya_planner.ensure_state`.
4. Family schedule (see [design](docs/design.md)): ✅ people, cars, person calendars with trips;
   ✅ house plan; next: weekly timetables.
5. A custom dashboard card.
6. Moving the real house over, one task at a time.

## Development

Tests use `pytest` and [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
and run in GitHub Actions on every push (Home Assistant does not run on Windows).
Windows are tested with real automations triggered by “time to plan”.
Locally, on Linux or macOS with Python 3.14:

```bash
pip install -r requirements_test.txt
pytest
```

## License

[MIT](LICENSE)
