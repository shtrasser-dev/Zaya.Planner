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

Made with forms only (the README has the full example). Masha's calendar (a Local Calendar):

```
Математика                          08:00–08:35, weekly Mon–Fri
Место: Школа
───────────── description ─────────────
#leaf #отвезти #запланировать
Время в пути: 40:00
```

1. **Calendar sensors** (3.8): “Leaf take there” — the start of `#leaf #отвезти` events minus
   “Время в пути”; “Leaf pick up” — their end minus it; a horizon of two days.
2. **Automation “Leaf departures”**: on “time to plan”, *Plan marks* from both sensors →
   “Leaf departure — Школа 07:20” and “… 09:00” on the “Plans” calendar, tags `#leaf #выезд`.
3. **Calendar sensor “Leaf first departure of the day”**: the “Plans” calendar, `#leaf #выезд`,
   only the first of the day, no offset.
4. **Automation “Leaf charging”**: *Turn on for a while* from that sensor, −90 / +20 minutes:
   a window before the first departure of each day.

Move the departure on the “Plans” calendar and the charging follows it; delete it and the
charging goes (the departure stays as 🚫 and can be brought back). With the tag `#ночь` and an
automation “Freeze for the night” at 22:00, what was seen in the evening happens at night.

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

### 3.7 Departures (2026-10-24; calendar sensors since 0.13, see 3.8)

“Minus attribute” in a step form was hard to read. Instead, a departure is made once, as a form:
*Add departure* on the integration page (a subentry; a helper of its own would need a second
integration, and HACS installs one per repository). Fields: name, events with tags (from the
dictionary, all of them), leave for the start or the end, travel time from a key (optional), the
travel time when an event has none (30 minutes), only the first of the day (on).

It is a timestamp sensor named by its name: the next departure is the event edge minus the travel
time; all-day events are left out; nothing in 8 days: unknown. Events without a travel time, or
with one that is not a duration, go to “Errors in events”. Automations use it as “Time of” in
“Turn on for a while” with offsets (−120 and −30 for the Leaf).

### 3.8 Marks and calendar sensors (2026-10-25)

One calendar is kept by people, another by automations. An automation puts **marks** on the
“Plans” calendar (“Departure — School 07:20”, the location from the event's *Location*); other
calendar sensors and automations read them there, as from any calendar. So a trip is planned
once, can be seen, moved and frozen, and everything that depends on it (charging, warming the
car, waking up) follows it. A trip of one's own can be written into one's own calendar with the
same tags. Zaya Planner still writes only to its own calendar: writing to people's calendars
would need marks of “our” events and would mix people's edits with automations'.

0.12 had an action of its own for departures (`plan_departures`). That was a mistake: no action
is made for one task (departures, watering, heating). Instead (0.13):

- **Calendar sensor** (*Add calendar sensor*, it replaces *Add departure*): events with tags,
  without tags, of some calendars; their start or end; earlier or later by an offset from a key
  or a default one; a **horizon**; only the first of the day. The state is the next time (so the
  sensor works in every standard form); with a horizon the attribute `times` lists every time
  within it, with its event. Departures of 0.12 are moved over: the travel time is an offset to
  subtract. The subentry key stays `departure`, so sensors made then stay.
- **Plan marks** (`plan_marks`): a mark at the time of any entities plus minutes, or at each time
  they list; the times of all of them make one set (0.14: “take there” and “pick up” sensors are
  both departures). A mark is a step with no action: a name and a moment, done when it comes.
- **Turn on for a while** (`plan_window`): when the start and the end are the same entity with a
  list, a window for each time; windows that overlap become one.
- The windows and marks of an owner are a **set**: those that are there stay, new ones are made,
  those whose times are gone are cancelled; one that would overlap a frozen or running plan is
  not made. `set_plan` keeps “one next plan per owner”.
- An entity that is `unavailable` (a calendar sensor before the calendars are read) changes
  nothing; `unknown` (no time) cancels the owner's planned windows or marks.
- `find_events` gets `!#tag`; the “Plans” calendar may be one of the chosen calendars; its
  changes are “time to plan”.

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
- **What a person decided stays** (2026-10-25): a moved plan is frozen at once. A plan keeps
  its steps as its owner made them; a plan a person moved or deleted is not made again while
  its owner plans those same steps. When the owner plans something else (the lesson moved),
  that is made.
- **A decision can be undone** (0.15, 2026-10-27): a plan a person deleted stays on the calendar
  as 🚫 (description unchanged); saving it brings it back (frozen), deleting it again forgets the
  decision. Saving a plan without a new time changes nothing; a changed name or description is
  refused. Searches of Zaya Planner skip 🚫 plans; they are kept until their own time is past the
  retention period. Statuses of Home Assistant calendars do not exist, so the two buttons of the
  standard event dialog are all there is; tags were considered (they only work for marks read by
  sensors, not for plans that switch devices).

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
| Actions | `plan_window`, `plan_marks`, `add_step`, `set_plan`, `cancel_plan`, `freeze_plans`, `unfreeze_plans`, `get_plans`, `find_events`, `ensure_state` | Включить на время, Поставить отметки, Добавить шаг, Поставить план, Отменить план, Заморозить планы, Разморозить планы, Получить планы, Найти события, Включить или выключить и проверить |
| Plan fields | `owner`, `summary`, `steps`, `tags`, `on_failure`, `display`, `force` | Владелец, Название, Шаги, Теги, При сбое, В календаре, Вручную |
| Step fields | `at`, `at_entity`, `event`, `subtract`, `offset`, `devices`, `state`, `action`, `data` | Дата и время, Время из, Событие, Минус атрибут, Сдвиг, Устройства, Действие, Скрипт, Данные |
| Calendar sensor | subentry `departure` (the key of its first version): `tags`, `exclude_tags`, `calendars`, `edge`, `offset_key`, `sign`, `default_offset`, `horizon`, `first_of_day` | Сенсор календаря: События с тегами, Без тегов, Календари, Время события, Смещение из ключа, Смещение, Смещение по умолчанию, Горизонт, Только первое за день |
| Calendar | `calendar.zaya_planner_plans` | Планы |
| Sensors | `sensor.zaya_planner_next_step`, `sensor.zaya_planner_failed_plans`, `sensor.zaya_planner_plans`, `sensor.zaya_planner_event_errors`, `sensor.<name>` (calendar sensors) | Следующий шаг, Сломанные планы, Планы, Ошибки в событиях |
| Plan statuses | `planned`, `frozen` ❄️, `running` ⏳, `done` ✅, `failed` ❌, `cancelled` (🚫 when by a person) | Запланирован, Заморожен, Идёт, Выполнен, Сломан, Отменён |
| Events | `zaya_planner_plan_done`, `zaya_planner_plan_failed` | — |
| Trigger | `time_to_plan` | Пора планировать |
| Options | lateness, retention, notifications, allowed actions, calendars, tags, keys | Допустимое опоздание первого шага, Хранить завершённые планы, Уведомления, Разрешённые скрипты шагов, Календари, Теги, Ключи |

There are plans and steps, no tasks. Statuses `skipped` and `done_with_errors` do not exist: a
plan is done or failed. The statuses, their names and the labels of plan descriptions are words of
the planner itself (like `on`/`off` of a switch); the words of the house (tags, keys, names)
are the user's.

## 8. History of the code

- 0.10: tasks and moments gave way to plans and steps; `add_step`, `set_plan`, freezing, the
  “Plans” calendar. Planning actions answer nothing since 0.11.
- 0.11: `plan_window` (“Turn on for a while”); departures (“Add departure”).
- 0.12: marks (steps with no action); `!#tag`; what a person decided stays. The action
  `plan_departures` of this version was made for one task and was replaced in 0.13.
- 0.13: calendar sensors instead of departures (offset from a key, sign, horizon, first of the
  day); `plan_marks`; windows for each listed time; windows and marks of an owner are a set.
- 0.14: `plan_marks` from several entities. 0.14.1: the first of the day stays first once over;
  a plan whose time has come is not cancelled by a set.
- 0.15: a plan a person cancelled stays as 🚫 and can be brought back or forgotten.

## 9. Steps

1. ✅ Calendars: chosen calendars, dictionary, errors in events, `find_events`.
2. ✅ Plans: model, store, executor, freezing, actions, “Plans” calendar, sensors.
3. ✅ Calendar sensors, marks, windows for each time; a person's decisions.
4. ✅ Leaf departures and charging, yard lights on the test Home Assistant, made with forms.
5. **Checks on the test Home Assistant**: freezing at night, a restart in the middle of a plan,
   a device that is unavailable (retries, failure, notification), Home Assistant off longer
   than the lateness limit.
6. **Moving the real house over**: floor heating → lights → Leaf, the old YAML planner running in
   parallel until the new one has proved itself.

## 10. Open questions

- “Time of an event” and “minus attribute” in `add_step`: calendar sensors do it better; remove?
- Silent “nothing planned” (an unavailable or renamed entity, an end before the start, a
  person's decision): a way to see why an automation planned nothing (a sensor of planning
  errors, or one logbook line).
- One owner rule: `set_plan` keeps one next plan, windows and marks are sets. Worth unifying
  only when a planning module needs several plans in one run.
- Labels of plan descriptions (“Status:”) could clash with a key of the dictionary. (The texts
  of the logbook, notifications and plan descriptions are in the translation files, under
  `common`, since 0.15.2.)

## 11. Later

- “Finish now” for a running plan (run its remaining steps at once).
- `on_cancel`: instructions for a plan cancelled by a person while it runs.
- Android alarm for a plan or a time.
- Travel time from maps with traffic (Waze free / 2GIS paid; Yandex too expensive).
- One-way export of plans to Google Calendar.
- iPhone: reading the next alarm through Shortcuts.
