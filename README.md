# Zaya Planner

A Home Assistant integration for planning the house's work from the family's calendars.
Automations work as always; Zaya Planner gives them three sets of tools:

1. **Calendar tools**: tags and attributes in event descriptions, finding events, errors in events.
2. **Plan tools**: put a plan (steps at given times) on the schedule, change it, cancel it, freeze it.
3. **Execution**: run the steps when they are due, wait until devices confirm, retry, and run your
   instructions if a plan fails.

What should happen and when is decided by your automations and scripts, with standard tools:
triggers, conditions, templates, script fields. Zaya Planner knows no words and no rules of its own.

> **Status:** calendars, plans and execution work and are tested; the real house is not moved over
> yet. See [Roadmap](#roadmap) and the [design](docs/design.md).

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
| Lateness of the first step | 5 h | A plan whose first step cannot start this late (e.g. Home Assistant was off) fails |
| Allowed step scripts | `script.step_` | Scripts whose entity id starts with this prefix can be steps (besides the built-in `zaya_planner.ensure_state`) |
| Keep finished plans | 3 days | Older finished plans are removed |
| Notifications | empty | Action for notifications about failed plans and errors in events, e.g. `notify.mobile_app_phone` or a script with `title` and `message` fields. Empty: a notification in Home Assistant |
| Calendars | none | Calendars Zaya Planner reads (see [Calendars and tags](#calendars-and-tags)) |
| Tags | none | Your tags, written without `#` |
| Keys | none | Your keys of `Key: value` lines |

## Example: charging the car before school

One calendar is kept by people, the other by automations:

1. In Masha's calendar (a Local Calendar) a lesson says where to take her:

   ```
   Maths                              08:00–08:35, weekly Mon–Fri
   Location: School
   ───────────── description ─────────────
   #leaf #take_there
   Travel: 40:00
   ```

2. An automation puts the departures on the **Plans** calendar: “Departure — School 07:20”.
3. Another automation charges the car for the first departure of the day.

**Departures** (*Add departure* on the Zaya Planner page):

| Departure | Calendars | Tags | Travel time | First of the day |
|---|---|---|---|---|
| “Leaf trips” → `sensor.leaf_trips` | Masha | `#leaf #take_there` | from `Travel` | off |
| “Leaf departure” → `sensor.leaf_departure` | Plans, Dad | `#leaf #take_there` | 0 | on |

**Automations**, made in the visual editor without templates:

```yaml
alias: Leaf departures
triggers:
  - trigger: device              # Zaya Planner: time to plan
    domain: zaya_planner
    device_id: <Zaya Planner device>
    type: time_to_plan
actions:
  - action: zaya_planner.plan_departures
    data:
      departure: sensor.leaf_trips
      summary: Departure
      tags: [leaf, take_there, night]
```

```yaml
alias: Leaf charging
triggers:
  - trigger: device
    domain: zaya_planner
    device_id: <Zaya Planner device>
    type: time_to_plan
actions:
  - action: zaya_planner.plan_window
    data:
      summary: Leaf charging
      devices: [switch.leaf_charger]
      start_entity: sensor.leaf_departure
      start_offset: -120
      end_entity: sensor.leaf_departure
      end_offset: -20
      tags: [night]
```

In the evening the **Plans** calendar shows “Departure — School 07:20” and “Leaf charging
05:20–07:00”; at 22:00 an automation “Freeze for the night” freezes the `#night` plans (❄️), and
no automation can change or cancel them any more. Move the departure to 07:00 on the Plans
calendar and the charging follows it; write a trip of your own into Dad's calendar
(`#leaf #take_there` in its description) and the car is charged for it too. If the charger does
not answer, the plan fails and you are notified.

Each plan belongs to the automation that made it (the hidden owner). For more logic (several
cars, conditions, numbers worked out) put the steps in a **planning module**: a script with
fields that the automation calls, as a form. A module that should own its plan whoever calls it
sets `owner: "{{ this.entity_id }}"`; field defaults of a script apply in the form only, so
templates use `{{ margin | default(30) }}`.

## Calendars and tags

Zaya Planner reads **only the calendars chosen in its settings** (any Home Assistant calendar,
usually a Local Calendar per person) and **never writes** to them. Events are made and edited in
the standard Calendar panel.

The words are yours: the settings hold a **dictionary** of tags and keys, in any language.
In an event's description:

- `#word` anywhere is a **tag**, if it is in the dictionary;
- a line `Key: value` is an **attribute**, if its key is in the dictionary;
- everything else is notes. Words are compared without letter case, “ё” as “е”.

Values are text. Durations are written as Home Assistant writes them (`00:40:00`, `40:00`,
`PT40M`) and read in templates with `as_timedelta`.

**Errors in events** (`sensor.zaya_planner_event_errors`) lists what is not understood in the
next 8 days of the chosen calendars: a tag not in the dictionary, a key with no value, a timed
event longer than a day (almost always a wrong date), a chosen calendar that is not found.
A mistake repeated in a series is one error. A new error is notified.

### `zaya_planner.find_events`: find events by tags

`filter`: one condition per line: `#tag` (the tag is there), `!#tag` (it is not), `Key: value`
(the attribute has this value), `Key` (the attribute is there); empty: all events. `calendars`: some of the chosen
ones (empty: all). `start`, `end`: the period (now and 8 days by default). `first_of_day`: only
the first matching event of each day.

`subtract`: a key whose value is a duration (`Travel: 40:00`): the event's `at` is its start
minus it (none if the event has no such duration). `only_future`: only events whose `at` has
not come yet.

Response: `{events: [{calendar, summary, at, start, end, all_day, location, tags, attributes, description}]}`.
Words not in the dictionary and calendars not chosen are refused.

### Departures: when to leave

*Add departure* on the Zaya Planner page (Settings → Devices & services → Zaya Planner) makes a
timestamp sensor named as you name it (“Leaf departure” → `sensor.leaf_departure`): when to
leave for the next event with the tags. The form:

| Field | Meaning |
|---|---|
| Name | The name of the sensor |
| Events with tags | The event has all these tags (chosen from the dictionary) |
| Without tags | The event has none of these tags (e.g. `#self`: she goes by herself) |
| Calendars | Events of these calendars only (empty: all the chosen ones), e.g. the children's calendars, or *Plans* for the marks of *Plan departures* |
| Leave | For the start of the event (take there) or for its end (pick up) |
| Travel time from key | The key whose value in the event is the travel time (`Travel: 00:40:00`); empty: always the time below |
| If the event has none | The travel time when the event does not say it (30 minutes by default; 0 for marks) |
| Only the first of the day | After the first departure of a day, the next one is on another day |

The departure is the event's start (or end) minus the travel time; all-day events are left out.
With no such event in the next 8 days the sensor is `unknown`. Attributes: `summary`,
`calendar`, `start`, `end`, `location` of the event, `travel` and `default_travel` (the event has no
travel time of its own). An event without a travel time (or with one that is not a duration) is
listed in *Errors in events*. When a departure passes it is time to plan. Change or delete a
departure with ⋮ on its line on the integration page.

## Plans

A **plan** is a sequence of **steps** run as one whole. A step is a time (`at`) and what to do:
`devices` + `state` (`on`/`off`), or `action` (an allowed step script) + `data`.

Every plan has an **owner**. By default it is the automation (or the script run by itself) that
started the run, so the field is hidden in the visual editor (shown in advanced mode). A
planning module that should own its plan whoever calls it, also when you run it by hand, sets
`owner: "{{ this.entity_id }}"`. An owner thinks one sequence ahead: it has at most **one next
plan**, which it may change, plus plans that are already frozen or running.

| Plan status | Meaning |
|---|---|
| `planned` | Its owner may change or cancel it |
| `frozen` ❄️ | Nobody but a person may change or cancel it |
| `running` ⏳ | Its first step is due; the other steps wait for their turn |
| `done` ✅ | Every step done |
| `failed` ❌ | A step was not done: the rest was not run, the failure instructions ran |
| `cancelled` | Cancelled |

### Making a plan

**`zaya_planner.plan_window`** “Turn on for a while” — a whole plan in one action: **Devices**
(one or more), **Start** and **End** (times of entities, e.g. Sun “Next sunset” / “Next sunrise”,
each with an offset in minutes); under *More*: name (default: the devices' names), tags, how it
is shown (by steps by default). If the end is before the start (e.g. at night), nothing is
planned (`no_window`). If an entity shows no time (`unknown`: the event is gone), the owner's
next plan is cancelled; frozen and running plans stay.

```yaml
- action: zaya_planner.plan_window
  data:
    devices: [switch.yard_lights]
    start_entity: sensor.sun_next_setting
    end_entity: sensor.sun_next_rising
    tags: [night]
```

**`zaya_planner.plan_departures`** “Plan departures” — a **mark** on the Plans calendar for each
departure of a departure sensor, from now to the end of a day: **Departure** (the sensor),
**Name** (“Departure”; empty: the event's name), **Add the location** (“Departure — School”),
**Days ahead** (1: today and tomorrow), **Tags**. A mark is a plan of one step that does nothing:
a moment with a name, done when it comes. Other departures and automations read the marks on the
Plans calendar (choose it among the calendars in the settings). The marks are a set: those that
are there already stay, new ones are made, marks of departures that are gone are cancelled.

```yaml
- action: zaya_planner.plan_departures
  data:
    departure: sensor.leaf_trips
    summary: Departure
    tags: [leaf, take_there]
```

For other plans, the building blocks:


- **`zaya_planner.add_step`**: adds a step to the plan being made **in this run**. Its time is one
  of: **Time of** an entity (`at_entity`: Sun “Next sunset”, a timestamp sensor, a date-and-time
  helper), the **time of an event** (`event` filter, `subtract` an attribute: the next fitting event
  of the chosen calendars), or a **date and time** (`at`, also a template); then **Offset** minutes
  (minus: earlier). What: **Devices** + **Turn on / Turn off**, or a step script (`action` +
  `data`). Without a time (no such event, the sensor shows none) the step is left out. Steps may
  be added in conditions and loops, also by scripts the automation calls.
- **`zaya_planner.set_plan`** (`owner`, `summary`, `tags`, `on_failure`,
  `display`, optionally `steps` as a list for YAML, `force`): makes the steps of this run the
  owner's next plan, replacing the one there was. **With no steps it cancels the next plan.**
  `display`: on the calendar as one event from the first step to the last (`span`), or a short
  event per step (`steps`, e.g. lights from sunset to sunrise).
- **`zaya_planner.cancel_plan`** (`owner`, `force`): cancels the owner's next plan.
- **`zaya_planner.get_plans`** (`owner`, `status`): the plans with their steps.

Planning actions answer nothing: what happened is on the Plans calendar and in the logbook.
Planning again the same plan changes nothing and writes nothing (rules may plan as often as they
like). A plan that starts before the owner's frozen or running plan ends is not made (quietly, if
it is that same plan; otherwise the logbook says so once). A plan whose first step is in the past
is not made.

Steps must be in time order.

### Freezing

“What you saw in the evening happens at night.” A frozen plan cannot be changed or cancelled by
automations; attempts are written to the **logbook** once (no notification). A plan is frozen:

- by **`zaya_planner.freeze_plans`** (`tags`, `within_hours`): the planned plans with any of the
  tags (empty: all) that start within the hours given — e.g. an automation at 22:00 freezes the
  plans tagged `#night` that start within 12 hours;
- always, when its first step is due: what has started is carried through.

**`zaya_planner.unfreeze_plans`** (`tags`) unfreezes the frozen plans that have not started.
**Tags** of a plan are given by the action that makes it (`tags`, from the dictionary) and are
shown in its calendar event.

**A person** can still cancel or move a plan: delete or move its event on the **Plans** calendar
(Home Assistant asks to confirm a deletion), or call `cancel_plan` / `set_plan` with `force: true`.
**What a person decided stays:** a moved plan is frozen at once, and a plan a person moved or
deleted is not made again while its owner plans the same. When the owner plans something else
(the lesson moved to another time), that is made.

### Running a plan

- When a step is due, its action runs: the built-in **`zaya_planner.ensure_state`** turns devices
  on or off and waits up to 20 seconds for each to confirm; a step script must answer
  `{ok: true}` or `{ok: false, reason: "..."}` (`stop` with `response_variable`).
- A failed step is retried after 1, 1, 2, 5 minutes, then every 5, until the next step is due
  (the last step: until the lateness limit). An error that lasts 10 minutes is notified once.
- **Any** step not done → the plan **fails**: the remaining steps are not run, the event
  `zaya_planner_plan_failed` is fired and the **failure instructions** run. The first step that
  cannot start within the lateness limit fails the plan too.
- An attempt interrupted by a restart of Home Assistant is made again.

**Failure instructions** (`on_failure`) are a script you write. It gets the fields `plan`
(summary, owner), `step` (the step that failed), `reason` and `done` (the steps that were done):

```yaml
script:
  leaf_charging_failed:
    alias: Leaf charging failed
    fields: { plan: {}, step: {}, reason: {}, done: {} }
    sequence:
      - action: switch.turn_off
        target: { entity_id: switch.leaf_charger }
      - action: notify.mobile_app_phone
        data:
          title: Leaf charging failed
          message: "{{ step.summary }}: {{ reason }}"
```

Without `on_failure` the usual notification is sent (see *Notifications*). If the instructions
themselves fail, that is written to the logbook and notified.

### Time to plan

The device trigger **“Zaya Planner: time to plan”** (Device → Zaya Planner) fires after Home
Assistant starts, when automations or scripts are saved, when events of the chosen calendars change, when a
departure passes, when a plan ends, and every 15 minutes. Add your own triggers too (a snow sensor changed).

## Entities

| Entity | Shows |
|---|---|
| `calendar.zaya_planner_plans` “Plans” | An event per plan, from its first step to its last, with the steps in the description. ❄️ frozen, ⏳ running, ✅ done, ❌ failed. Delete an event to cancel the plan, move it to move the plan (it is frozen then). Can be one of the calendars Zaya Planner reads |
| `sensor.zaya_planner_next_step` “Next step” | When the next step is due; the plan and the step in attributes |
| `sensor.zaya_planner_failed_plans` “Failed plans” | Number of failed plans; the list in the `plans` attribute |
| `sensor.zaya_planner_plans` “Plans” | Number of open plans; **all** plans in the `plans` attribute |
| `sensor.zaya_planner_event_errors` “Errors in events” | Number of errors in the chosen calendars; the lines in the `errors` attribute |
| `sensor.<name>` departures | When to leave next; see *Departures* |

Entity ids do not depend on the language of Home Assistant. Names, statuses and messages are
translated (English and Russian).

Events for automations: `zaya_planner_plan_done` (`uid`, `owner`, `summary`) and
`zaya_planner_plan_failed` (the same plus `step` and `reason`).

## Naming

- `script.plan_*` — planning modules (they make plans);
- `script.step_*` — step scripts (they are steps of plans; see *Allowed step scripts*).

## Roadmap

1. ✅ Calendars: chosen calendars, tag dictionary, errors in events, `find_events`.
2. ✅ Plans: steps, owners, next plans, freezing, failure instructions, the Plans calendar.
3. Planning modules on a test Home Assistant (charging, yard lights).
4. Moving the real house over, one plan at a time.

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
