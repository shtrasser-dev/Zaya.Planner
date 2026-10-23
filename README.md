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
| Time to fix | 10 min | A plan freezes no sooner than this after it last changed: time to undo a mistake |
| Allowed step scripts | `script.step_` | Scripts whose entity id starts with this prefix can be steps (besides the built-in `zaya_planner.ensure_state`) |
| Keep finished plans | 3 days | Older finished plans are removed |
| Notifications | empty | Action for notifications about failed plans and errors in events, e.g. `notify.mobile_app_phone` or a script with `title` and `message` fields. Empty: a notification in Home Assistant |
| Calendars | none | Calendars Zaya Planner reads (see [Calendars and tags](#calendars-and-tags)) |
| Tags | none | Your tags, written without `#` |
| Keys | none | Your keys of `Key: value` lines |

## Example: charging the car before school

Masha's calendar (a Local Calendar), weekdays:

```
Maths                              07:00–07:35, weekly Mon–Fri
───────────── description ─────────────
#leaf #take_there
Travel: 40:00
```

A **planning module**: an ordinary script with fields (Settings → Automations & scenes → Scripts),
made in the visual editor. It works out the steps and makes the plan:

```yaml
script:
  plan_leaf_charging:
    alias: Plan Leaf charging
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
        data: { owner: "{{ this.entity_id }}", summary: Leaf charging,
                freeze_at: "23:00", on_failure: script.leaf_charging_failed }
```

An **automation** finds the departure and calls the module (in the visual editor the module's
fields are a form):

```yaml
alias: Leaf charging
triggers:
  - trigger: device              # Zaya Planner: time to plan
    domain: zaya_planner
    device_id: <Zaya Planner device>
    type: time_to_plan
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
               - (found.events[0].attributes['Travel'] | as_timedelta) }}
    else:
      - action: zaya_planner.cancel_plan
        data: { owner: script.plan_leaf_charging }
```

In the evening the plan “Leaf charging 04:50–06:20” is on the **Plans** calendar; at 23:00 it is
frozen (❄️) and no automation can change or cancel it any more. If the charger does not answer,
the plan fails and `script.leaf_charging_failed` runs: turn the charger off, notify, plan again —
whatever you write there.

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

`filter`: one condition per line: `#tag` (the tag is there), `Key: value` (the attribute has
this value), `Key` (the attribute is there); empty: all events. `calendars`: some of the chosen
ones (empty: all). `start`, `end`: the period (now and 8 days by default). `first_of_day`: only
the first matching event of each day.

Response: `{events: [{calendar, summary, start, end, all_day, tags, attributes, description}]}`.
Words not in the dictionary and calendars not chosen are refused.

## Plans

A **plan** is a sequence of **steps** run as one whole. A step is a time (`at`) and what to do:
`devices` + `state` (`on`/`off`), or `action` (an allowed step script) + `data`.

Every plan has an **owner**: the automation or script that made it, usually
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

- **`zaya_planner.add_step`** (`owner`, `at`, `devices` + `state` or `action` + `data`): adds a
  step to the plan being made **in this run**. Each step has its own form in the visual editor;
  steps may be added in conditions and loops, also by scripts the automation calls.
- **`zaya_planner.set_plan`** (`owner`, `summary`, `freeze_at` or `freeze_before`, `on_failure`,
  optionally `steps` as a list for YAML, `force`): makes the steps of this run the owner's next
  plan, replacing the one there was. **With no steps it cancels the next plan.**
- **`zaya_planner.cancel_plan`** (`owner`, `force`): cancels the owner's next plan.
- **`zaya_planner.get_plans`** (`owner`, `status`): the plans with their steps.

`set_plan` answers `result`:

| Result | When |
|---|---|
| `created`, `updated`, `cancelled` | as said |
| `unchanged` | the same plan is there already (rules may plan as often as they like) |
| `overlaps` | the plan starts before the owner's frozen or running plan ends; quiet if it is that same plan |
| `frozen` | the plan would change a frozen plan: nothing changes, the logbook says so |

Steps must be in time order and the first one in the future.

### Freezing

“What you saw in the evening happens at night.” `set_plan` sets when the plan freezes:

- `freeze_at`: a time of day — the last such time before the first step (`"23:00"`);
- `freeze_before`: minutes before the first step;
- neither: when the first step is due.

A plan never freezes sooner than **Time to fix** after it last changed: a mistake at 01:00 can
still be undone by 01:10. Attempts of automations to change or cancel a frozen plan change
nothing and are written to the **logbook** once (no notification).

**A person** can still cancel or move a plan: delete or move its event on the **Plans** calendar
(Home Assistant asks to confirm a deletion), or call `cancel_plan` / `set_plan` with `force: true`.

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
Assistant starts, when automations are saved, when events of the chosen calendars change, when a
plan ends, and every 15 minutes. Add your own triggers too (a snow sensor changed).

## Entities

| Entity | Shows |
|---|---|
| `calendar.zaya_planner_plans` “Plans” | An event per plan, from its first step to its last, with the steps in the description. ❄️ frozen, ⏳ running, ✅ done, ❌ failed. Delete an event to cancel the plan, move it to move the plan |
| `sensor.zaya_planner_next_step` “Next step” | When the next step is due; the plan and the step in attributes |
| `sensor.zaya_planner_failed_plans` “Failed plans” | Number of failed plans; the list in the `plans` attribute |
| `sensor.zaya_planner_plans` “Plans” | Number of open plans; **all** plans in the `plans` attribute |
| `sensor.zaya_planner_event_errors` “Errors in events” | Number of errors in the chosen calendars; the lines in the `errors` attribute |

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
