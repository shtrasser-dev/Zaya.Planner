# Zaya Planner: calendars, plans and planning modules

Design agreed before writing code (2026-10-23). What exists today is described in the
[README](../README.md); this document says where it is going.

## 1. Goal

Home Assistant automations work as always. Zaya Planner gives them three sets of tools:

1. **Calendar tools**: tags and attributes in event descriptions, finding events, errors in events.
2. **Plan tools**: put a plan (steps at given times) on the schedule, change it, cancel it.
3. **Execution**: run the steps of a plan when they are due, reliably, and report what happened.

What should happen and when is decided by automations and scripts, with standard tools:
triggers, conditions, templates, script fields. Zaya Planner knows no words and no rules of its own.

Principles:

- **Nothing foreign to Home Assistant.** Calendars, the event dialog, automations, scripts and
  templates are the standard ones. Zaya Planner never writes to the chosen calendars.
- **Nothing is created by itself.** Calendars, plans and modules are made by the user (or by the
  user's automations). The integration's own entities are fixed: one calendar and a few sensors.
- **What you saw in the evening happens at night.** A plan can be frozen: after that no automation
  can change or cancel it.
- **Mistakes are shown**, never silently ignored.

### 1.1 How we got here

1. Calendars of our own with trip fields: the fields could not be edited in the standard dialog.
2. Fixed keys in descriptions (`Car`, `Trip`): the code decided the words.
3. Moments (sensors worked out from events by a form): the form was too complex and still could
   not do simple things (“5 minutes earlier if it snowed at night”).
4. Now: automations and scripts compute times with templates; Zaya Planner finds events and
   keeps and runs plans.

## 2. Example: charging the Leaf

Masha's calendar (a Local Calendar), weekdays:

```
Математика                          07:00–07:35, weekly Mon–Fri
───────────── description ─────────────
#leaf #отвезти
Время в пути: 40:00
```

**Planning module** (a script with fields, made in the visual script editor):

```yaml
script:
  plan_leaf_charging:
    alias: Планирование зарядки Leaf
    fields:
      departure: { selector: { datetime: {} } }
      charge_minutes: { default: 90, selector: { number: { min: 0, max: 600 } } }
    sequence:
      - variables:
          off_at: "{{ (departure | as_datetime) - timedelta(minutes=30) }}"
          on_at: "{{ off_at - timedelta(minutes=charge_minutes) }}"
      - action: zaya_planner.add_step
        data: { owner: "{{ this.entity_id }}", at: "{{ on_at }}",
                devices: [switch.leaf_charger], state: "on" }
      - action: zaya_planner.add_step
        data: { owner: "{{ this.entity_id }}", at: "{{ off_at }}",
                devices: [switch.leaf_charger], state: "off" }
      - action: zaya_planner.set_plan
        data: { owner: "{{ this.entity_id }}", summary: Зарядка Leaf, tags: [ночь] }
```

**Automation** (visual editor): trigger “Zaya Planner: time to plan”; find the first `#leaf` event;
if there is one, call the module with the departure; if not, cancel the module's plan.

```yaml
actions:
  - action: zaya_planner.find_events
    data: { filter: "#leaf", first_of_day: true }
    response_variable: found
  - if: "{{ found.events | count > 0 }}"
    then:
      - action: script.plan_leaf_charging
        data:
          departure: >
            {{ (found.events[0].start | as_datetime)
               - (found.events[0].attributes['Время в пути'] | as_timedelta) }}
    else:
      - action: zaya_planner.cancel_plan
        data: { owner: script.plan_leaf_charging }
```

The plan “Зарядка Leaf: 04:50 on → 06:20 off” is on the “Plans” calendar in the evening; at 23:00
it is frozen (❄️). If at 02:00 a mistake makes the automation cancel it, nothing changes, and the
logbook says so. In the morning the car is charged.

## 3. Calendars

### 3.1 Options

The integration's options hold the **calendars** Zaya Planner reads (any Home Assistant
calendars; others are never opened) and the user's **dictionary**: **tags** and **keys**, in any
language.

### 3.2 Event description

- `#word` anywhere is a **tag** (if it is in the dictionary);
- a line `Key: value` is an **attribute** (if its key is in the dictionary);
- everything else is notes. Words are compared without letter case, “ё” as “е”.
- Values are text. Durations are written as Home Assistant writes them (`00:40:00`, `40:00`,
  `PT40M`) and read in templates with `as_timedelta`. No checks of our own.

### 3.3 Filters

`#tag` (the tag is there), `Key: value` (the attribute has this value), `Key` (the attribute is
there); all lines must hold.

### 3.4 Errors in events

The sensor “Errors in events” lists, for the next 8 days of the chosen calendars: tags not in the
dictionary, dictionary keys with no value, timed events longer than a day, chosen calendars that
are not found. A mistake repeated in a series is one error; a new error is notified.

### 3.5 Finding events

`zaya_planner.find_events`: filter, calendars (some of the chosen; empty: all), period (now and
8 days by default), “first of each day”. Answers the events with their tags and attributes as text.

### 3.6 Time to plan

The device trigger “Zaya Planner: time to plan” fires after Home Assistant starts, when automations
are saved, **when an event of a chosen calendar changes**, when a plan ends, and every 15 minutes.
Automations may add triggers of their own (a snow sensor changed). Also when a departure passes
(3.7), and once the calendars are first read if there are departures.

### 3.7 Departures (2026-10-24)

“Minus attribute” in a step form was hard to read. Instead, a departure is made once, as a form:
*Add departure* on the integration page (a subentry; a helper of its own would need a second
integration, and HACS installs one per repository). Fields: name, events with tags (from the
dictionary, all of them), leave for the start or the end, travel time from a key (optional), the
travel time when an event has none (30 minutes), only the first of the day (on).

It is a timestamp sensor named by its name: the next departure is the event edge minus the travel
time; all-day events are left out; nothing in 8 days: unknown. Events without a travel time, or
with one that is not a duration, go to “Errors in events”. Automations use it as “Time of” in
“Turn on for a while” with offsets (−120 and −30 for the Leaf).

## 4. Plans

### 4.1 What a plan is

A **plan** is a sequence of **steps** run as one whole: “on at 04:50, off at 06:20”. A step is a
time (`at`) and what to do: `devices` + `state` (`on`/`off`, run by the built-in
`zaya_planner.ensure_state`), or `action` (a script allowed in the options) + `data`.

Every plan has an **owner**: the automation or script that made it (`owner: "{{ this.entity_id }}"`).
An owner thinks one sequence ahead: it has at most **one next plan**, which it may change, plus the
plans that are already frozen or running.

### 4.2 Statuses

```
planned ──(freeze_plans)──▶ frozen ──(first step due)──▶ running ──▶ done
   │                                                       │
   └──(cancel_plan, set_plan without steps)──▶ cancelled   └──(any step not done)──▶ failed
```

| Plan | Meaning |
|---|---|
| `planned` | The owner may change or cancel it |
| `frozen` | Nobody but a person may change or cancel it |
| `running` | Its first step is due; the other steps wait for their turn |
| `done` | Every step done |
| `failed` | A step was not done: the rest is not run, the failure instructions run (5.1) |
| `cancelled` | Cancelled (by its owner before it froze, or by a person) |

| Step | Meaning |
|---|---|
| `planned` | Not run yet (the plan has not started) |
| `waiting` | The plan is running; the step waits for its turn |
| `running` | Being run |
| `retrying` | Failed, will be tried again |
| `done` | Done |
| `failed` | Not done (retries ran out, or the next step became due) |
| `cancelled` | The plan was cancelled |

### 4.3 Making a plan

- **`zaya_planner.add_step`** (owner, `at`, `devices` + `state` or `action` + `data`): adds a step
  to the owner's **draft**. Each step has its own form in the visual editor; steps may be added in
  conditions and loops.
- **`zaya_planner.set_plan`** (owner, `summary`, freezing, `on_failure` (5.1), optionally `steps`
  as a list for YAML):
  turns the draft into the owner's next plan, replacing the one there was. With no steps it cancels
  the next plan.
- A draft belongs to **one run** (the Home Assistant context of the automation run, shared by the
  scripts it calls). A run that stopped half way leaves nothing behind; the next run starts afresh.
- **`zaya_planner.cancel_plan`** (owner): cancels the owner's next plan. Frozen and running plans
  stay, unless `force: true` (a person's choice, see 4.6).
- **`zaya_planner.get_plans`** (owner, statuses): the plans with their steps.

Planning actions answer nothing: what happened is on the Plans calendar and in the logbook.
Planning again the same plan changes nothing and writes nothing (rules may plan as often as they
like). A plan that starts before the owner's frozen or running plan ends is not made (quietly, if
it is that same plan; otherwise the logbook says so once). A plan whose first step is in the past
is not made.

Rules: steps must be in time order; the next plan must start after the owner's last frozen or
running plan ends. So “lights from sunset to sunrise” needs no checks in the automation: while
tonight's plan runs, planning tonight again is answered `overlaps` quietly.

### 4.4 Freezing (changed 2026-10-24)

Freezing is an action, not a setting of each plan:

- **`freeze_plans`** (`tags`, `within_hours`): freezes the planned plans with any of the tags
  (none: all) that start within the hours given. Usually an automation at 22:00: “freeze the
  `#night` plans that start within 12 hours”. What appears later (while you sleep) you have not
  seen, so it is not frozen.
- **`unfreeze_plans`** (`tags`): the frozen plans that have not started become planned again.
- A plan is always frozen when its first step is due: what has started is carried through.

Plans have **tags** (from the dictionary), given by the action that makes them and shown in the
calendar event. The per-plan `freeze_at` / `freeze_before` and the option “Time to fix” are gone.

### 4.4.1 Convenience actions (2026-10-24)

One universal “add step” form was too heavy. Plans are made by actions with few, plain fields;
they are added one by one, when needed. The first: **`plan_window`** “Turn on for a while” —
devices, start and end as times of entities plus minutes, name, tags; a whole plan in one action;
no window (an end before its start) is not an error. `add_step` / `set_plan` stay for complex
plans. Next candidates: “turn on by a deadline” (charging), “plan a trip” (departure from the
first event of a day, travel time from an attribute), “time of a plan” as a time source.

### 4.5 Conflicts

An attempt of an automation to change or cancel a frozen plan changes nothing, is written to the
**logbook** (“Зарядка Leaf: frozen at 23:00; script.plan_leaf_charging wanted to cancel it at 02:00”)
and marked in the plan's attributes. No notification.

### 4.6 A person's changes

- In the **“Plans” calendar** (the integration's calendar): deleting a plan's event cancels the plan
  (Home Assistant asks to confirm); moving it moves all its steps. This works for frozen plans too.
- In scripts and actions: `cancel_plan` / `set_plan` with `force: true`.

## 5. Execution

- Steps answer `{ok, reason}` as tasks do today (`ensure_state` or a step script); retries after
  1, 1, 2, 5 minutes, then every 5.
- A step is **not done** when its retries have not succeeded by the time the next step is due
  (the last step: by the lateness limit after its time), or when the first step could not start
  within the **lateness limit** (an option; e.g. Home Assistant was off).
- Once a plan runs, its later steps are run however late, as long as the plan does not fail.
- **Any** step not done → the plan **failed**: its remaining steps are `cancelled`, the failure
  instructions run (5.1), the event `zaya_planner_plan_failed` is fired. The first, a middle and the
  last step are treated alike; nothing is guessed.

### 5.1 Failure instructions

`set_plan` may name a script, `on_failure`, that Zaya Planner runs when the plan fails, with the
fields `plan` (summary, owner), `step` (the step that failed: time, what), `reason`, `done` (the
steps that were done). The script decides: undo (turn the charger off), notify, plan again, or
nothing.

```yaml
- action: zaya_planner.set_plan
  data: { owner: "{{ this.entity_id }}", summary: Зарядка Leaf, tags: [ночь],
          on_failure: script.leaf_charging_failed }
```

- No `on_failure`: the usual failure notification (the “Notifications” option) is sent.
- The instructions themselves fail: a logbook entry and the usual notification; they are not
  retried.
- A plan cancelled by a person is not a failure: `on_failure` does not run.
- Events `zaya_planner_plan_done` and `zaya_planner_plan_failed`; a plan that ends fires
  “time to plan”.
- Plans are written to disk at once; after a restart running plans carry on.

## 6. Planning modules

A **planning module** is an ordinary script with fields: “plan the Leaf charging for this
departure”. It works out the steps (with templates) and makes the plan with `add_step` and
`set_plan`. Automations call it from the visual editor, where its fields are a form; it can also
be run by hand from the script page, and may return an answer.

- The module is the plan's owner (`this.entity_id` inside the script): one module, one plan ahead,
  whoever calls it. A module shared by two cars uses `owner: "{{ this.entity_id }}:{{ car }}"`.
- Naming (README): `script.plan_*` for planning modules, `script.step_*` for scripts used as steps.
  The option “Allowed actions” defaults to `script.step_`.

## 7. Names

| What | Code | Russian |
|---|---|---|
| Actions | `add_step`, `set_plan`, `cancel_plan`, `get_plans`, `find_events`, `ensure_state` | Добавить шаг, Поставить план, Отменить план, Получить планы, Найти события, Включить или выключить и проверить |
| Plan fields | `owner`, `summary`, `steps`, `tags`, `on_failure`, `force` | Владелец, Название, Шаги, Теги, При сбое, Вручную |
| Step fields | `at`, `devices`, `state`, `action`, `data` | Когда, Устройства, Состояние, Действие, Данные |
| Calendar | `calendar.zaya_planner_plans` | Планы |
| Sensors | `sensor.zaya_planner_next_step`, `sensor.zaya_planner_failed_plans`, `sensor.zaya_planner_plans`, `sensor.zaya_planner_event_errors` | Следующий шаг, Сбойные планы, Планы, Ошибки в событиях |
| Events | `zaya_planner_plan_done`, `zaya_planner_plan_failed` | — |
| Trigger | `time_to_plan` | Пора планировать |
| Options | lateness, retention, notifications, allowed actions, calendars, tags, keys | Допустимое опоздание первого шага, Хранить завершённые планы, Уведомления, Разрешённые действия, Календари, Теги, Ключи |

The word “task” goes; there are plans and steps. Statuses `skipped` and `done_with_errors` do not exist: a plan is done or failed.

## 8. What changes in the code

- **Goes**: moments (subentries, sensors, `moments.py`); `upsert`, `remove`, `list`, `plan_window`
  (and `window.py`); the task model.
- **Stays**: calendars, dictionary, `find_events`, errors in events, `ensure_state`, the executor's
  retries and notifications, “time to plan”.
- **New**: plans and steps in the store; `add_step`, `set_plan`, `cancel_plan`, `get_plans`;
  freezing, owners, next plans; the “Plans” calendar with delete and move; “time to plan” on
  calendar changes.
- On upgrade, tasks and moments are removed (only the test Home Assistant has them).

## 9. Steps

1. ✅ Task store, executor, `plan_window` (replaced by step 4).
2. ✅ Calendars: chosen calendars, dictionary, errors in events, `find_events`.
3. ✅ People, cars, own calendars and house plan removed.
4. ✅ **Plans**: model, store, executor, freezing, actions, “Plans” calendar, sensors; moments
   removed; “time to plan” on calendar changes.
5. **Modules on the test Home Assistant**: Leaf charging and yard lights, as in section 2; README
   with these examples.
6. **Moving the real house over**: floor heating → lights → Leaf, the old YAML planner running in
   parallel until the new one has proved itself.

## 10. Open questions

None at the moment.

## 11. Later

- “Finish now” for a running plan (run its remaining steps at once).
- `on_cancel`: instructions for a plan cancelled by a person while it runs.
- Android alarm for a plan or a time.
- Travel time from maps with traffic (Waze free / 2GIS paid; Yandex too expensive).
- One-way export of plans to Google Calendar.
- iPhone: reading the next alarm through Shortcuts.
