# Zaya Planner: calendars, tags and moments

Design of the next part of Zaya Planner, agreed before writing code.
What exists today (task store, executor, `upsert`, `plan_window`, `ensure_state`) is described
in the [README](../README.md); this document is about what is built on top of it.

## 1. Goal

The family plans its days in calendars; the house follows.

- Events are written in ordinary Home Assistant calendars, one per person or any other way.
- Tags and attributes in an event's description say what matters for the house:
  `#leaf #отвезти`, `В пути: 00:40:00`.
- **Moments** are worked out from the events: “the Leaf leaves for school”, “Dad gets up”.
  Each moment is a timestamp sensor.
- Rules (automations with `plan_window` / `upsert`) plan the house's work from moments:
  the Leaf is charged before it leaves, the floor is warm by the time Dad gets up.
- A change in a calendar (a lesson moved, an appointment added) changes the moments, and the
  rules plan again.

Principles:

- **Nothing foreign to Home Assistant.** Calendars, the event dialog, recurrence and
  automations are the standard ones. Zaya Planner never writes to the chosen calendars.
- **No words in the code.** The integration does not know what a car, a trip or a wake-up is.
  The user's own **dictionary** (tags and keys, in any language) and the user's moments give
  the words their meaning.
- **Settings in one place**: the integration page — its options (calendars, dictionary) and
  its moments.
- Mistakes are shown, never silently ignored.

### 1.1 How we got here (2026-10-22)

1. Calendars of our own with trip fields (iCalendar `X-` properties) worked, but the fields could
   not be seen or edited in the standard event dialog; a card and forms would have been needed.
2. `Key: value` lines with keys fixed in the code (`Car`, `Trip`, `Travel`) made the code decide
   the words and hard-wired people, cars, trips and wake-ups.
3. Now: tags and keys are the user's dictionary; their meaning is set in moments.

## 2. Example: an ordinary weekday

Masha's calendar (a Local Calendar “Masha”), Monday to Friday:

```
School                               08:00–13:45, weekly Mon–Fri
───────────── description ─────────────
#leaf #отвезти
В пути: 00:40:00
Take the sports kit
```

Dad's calendar has “Wake-up” at 8:00 on weekends with `#подъём`.

Moments (on the integration page):

| Moment | Worked out as |
|---|---|
| Leaf: morning departure | the first event of the day with `#leaf #отвезти` in Masha's and Dad's calendars: start − `В пути` |
| Dad: wake-up | the earliest of: Leaf: morning departure − 60 min; an event with `#подъём` in Dad's calendar |

Rules: charge the Leaf for 90 min, ending 30 min before “Leaf: morning departure”;
floor heating −20/+20 min around “Dad: wake-up”.

On Monday: departure 7:20, Dad's wake-up 6:20, charging 5:20–6:50, floor 6:00–6:40.
If school starts at 9:00 this Monday (the event moved with “only this event”), everything moves.

## 3. Concepts

### 3.1 Options: calendars and dictionary

The integration's options (Settings → Devices & services → Zaya Planner → Configure):

| Option | Example | Meaning |
|---|---|---|
| Calendars | Masha, Dad, Mum | The calendars Zaya Planner reads; other calendars are never opened |
| Tags | leaf, chery, отвезти, забрать, подъём, отмена | Tags that may be written in events |
| Keys | В пути | Keys of attributes that may be written in events |

The existing options (lateness, script prefix, retention, notifications) stay on the same form.

Any calendar can be chosen; the usual choice is a Local Calendar per person. A calendar with old
events can be chosen too: only events with tags and keys from the dictionary mean anything.

### 3.2 Event description

- **Tags**: `#word` anywhere in the description, several in a line: `#leaf #отвезти`.
  A tag starts with a letter; letters, digits, `_` and `-` follow.
- **Attributes**: a line `Key: value`, where the key is in the dictionary.
- Everything else is notes: lines without `:`, and lines whose key is not in the dictionary
  (“Address: …”, “Room: 12”).

Tags and keys are compared without letter case, “ё” as “е”, spaces around `:` ignored.
When a key is repeated, the first line counts. Values are text, kept as written.

**Durations** are written in the Home Assistant format, the one the template filter
`as_timedelta` reads: `00:40:00` (40 minutes), `01:00:00` (an hour), `1:10:00`, or ISO 8601
`PT40M`. Zaya Planner reads them with the same Home Assistant function, so a value means the same
in a moment and in a template. No checks of our own: `40` is 40 seconds and `1:00` is one
minute, exactly as in Home Assistant.

### 3.3 Filters

Moments and the action `find_events` pick events with a **filter** written the same way:

| Filter line | Means |
|---|---|
| `#leaf` | the tag is there |
| `В пути: 00:40:00` | the attribute has this value (text, compared as written) |
| `В пути` | the attribute is there, any value |

All lines of a filter must hold. In the moment form tags and keys are chosen from the
dictionary, so a filter cannot have a typo.

### 3.4 Errors in events

The sensor **“Errors in events”** lists what could not be understood in the chosen calendars:

- a tag not in the dictionary: “School (Masha, Mon 26.10): unknown tag #lief”;
- a dictionary key with no value: “School (Masha, Mon 26.10): ‘В пути’ is empty”;
- a timed event longer than 24 hours (almost always a wrong date in the event dialog).

It also lists the problems moments find (3.5): an event that matches a moment's filter but lacks
the attribute the moment takes, or has one that is not a duration (“сорок”, “1 ч”). A typo in a
key (`Впути: 00:40:00`) shows up this way: the event matches, the attribute is missing.

A new error sends a notification (the same notification setting as failed tasks).
Renaming a tag or a key in the dictionary does not change the events: the old word shows up as
errors until the events are fixed.

### 3.5 Moments

A moment is a timestamp sensor `sensor.zaya_<name>` made on the integration page with the button
“Add moment” (a config subentry), and changed or deleted there. There are two kinds.

**From calendars**

| Field | Example |
|---|---|
| Name | Leaf: morning departure |
| Calendars | Masha, Dad (from the chosen ones; none = all) |
| Filter | `#leaf #отвезти` |
| Edge | start / end of the event |
| Offset | − `В пути` (a key from the dictionary, its value a duration, 3.2) and/or ± minutes |
| Which | the first of each day / every one |
| Cancellations | tags of cancelling events, e.g. `#отмена` (optional) |

The sensor shows the next moment (with “the first of each day”: the first moment of the next day
that has one). Its attributes are the event's title, calendar, tags and attributes.

Cancellations are events in the same calendar with any of the cancellation tags (“Frost”,
“Vacation”, “No first two lessons”), all-day or timed, one day or many:

- an event entirely inside a cancellation does not count;
- an edge inside a cancellation moves out of it: a start to the cancellation's end, an end to its
  start (the first lessons are cancelled: the departure moves later);
- deleting the cancellation brings everything back.

**From other moments**

| Field | Example |
|---|---|
| Name | Dad: wake-up |
| Moments | Dad: wake-up for the departure, Dad's own wake-up (any timestamp sensors) |
| Plus minutes | 0 |

The earliest of the moments plus one offset. A source that needs an offset of its own is a
moment of its own: “Dad: wake-up for the departure” is a moment from calendars, the start of the
first `#leaf` event minus `В пути` minus 60 minutes. So “wake-up = departure − getting ready”
and “the earliest of several wake-ups” are made from moments, not hard-wired.

Moments are recalculated when a chosen calendar changes, when the options or a moment change,
every few minutes and after a restart. A change of any moment fires “time to plan”.

### 3.6 Rules

Rules stay ordinary automations, made with the visual editor: the trigger **“Zaya Planner:
time to plan”** (it fires after a start, when automations are saved, when a task ends, when a
moment changes and every 15 minutes), any conditions, and one action:

- **`plan_window`**: devices on from a start to an end. Each end is a time of day (with days),
  the time of an entity + offset (moments, the sun; the earliest of several), or minutes counted
  from the other end. Examples: floor heating −20/+20 min around “Dad: wake-up”; the Leaf charged
  until 30 min before “Leaf: morning departure”; lights from sunset + 30 to sunrise − 30.
- **`upsert`** for a single moment.

Rules plan tasks ahead, so the “Schedule” calendar shows them and the executor runs them reliably.
Planning again with nothing changed changes nothing (`unchanged`), so rules may run often.

### 3.7 Action `find_events`

`zaya_planner.find_events`: calendars (chosen ones; none = all), a filter, a period, and
optionally “the first of each day”. Answers the events with their tags and attributes as text;
templates convert them with standard tools:

```yaml
- action: zaya_planner.find_events
  data:
    filter: "#leaf"
    first_of_day: true
  response_variable: found
- variables:
    travel_minutes: >
      {{ (found.events[0].attributes['В пути'] | as_timedelta).total_seconds() / 60 }}
```

Moment sensors carry the same tags and attributes (3.5). No functions of our own are added to
templates: the more standard tools, the better.

## 4. What changes in the code

Stays: task store, executor, `upsert`, `ensure_state`, `plan_window`, the “time to plan”
trigger, the “Schedule” calendar with descriptions built as `Key: value` lines, the generic part
of `description.py` (`attributes`, `build`, `matches`, `find`, `first_of_day`).
Our own reading of durations (`travel_minutes` with “мин”, “ч”) goes: durations are read by
Home Assistant's `parse_duration`, the same as `as_timedelta`.

Goes:

- people and cars: options “People”, car entries, person and car devices with their settings
  entities, `.storage/zaya_planner.settings`;
- our own person calendars (`.ics` files, `X-ZAYA-*`), the actions `add_event`,
  `set_event_details`, `get_events`, the `ical` requirement and the `person` dependency;
- the hard-wired house plan: departures, wake-ups, the “House plan” calendar, the anchor sensors
  `sensor.zaya_<person>_*`, `sensor.zaya_<car>_first_departure`, `sensor.zaya_planner_first_wake_up`;
- the Android alarm (may come back later);
- fixed keys (`KEY_NAMES`, `parse_details`, `build_details`, `details.py`).

The cancellation rules of `plan.py` move into moments.
On upgrade the car entries and the saved person settings are removed; nothing is carried over
(only the test Home Assistant has them).

## 5. Steps

1. ✅ Task store, executor, rules with `plan_window` (see README).
2. ✅ First version of people, cars, own calendars and house plan (replaced by steps 4–6).
3. ✅ `Key: value` lines: parse / build / find.
4. ✅ **Dictionary and calendars**: options “Calendars”, “Tags”, “Keys”; tags and attributes read
   from the chosen calendars; “Errors in events”; `find_events`.
5. ✅ **Moments**: subentries “from calendars” (with cancellations) and “from other moments”;
   sensors; “time to plan” on a change; moment problems in “Errors in events”.
6. ✅ **Clean-up** (done before step 5): people, cars, own calendars, house plan, alarm and fixed keys removed;
   README updated.
7. **Moving the real house over**: floor heating → lights → Leaf, the old YAML planner
   running in parallel until the new one has proved itself.

Each step ends tested (automated tests + the test Home Assistant).

## 6. Open questions

None at the moment.

## 7. Later

- Android alarm for a moment (set in the evening, a warning if the moment moves).
- A calendar of moments (what the house worked out and why), if the sensors are not enough.
- Travel time from maps with traffic (Waze free / 2GIS paid; Yandex too expensive).
- One-way export to Google Calendar (via the Google Calendar API).
- iPhone: reading the next alarm through Shortcuts; HA as an alarm with critical notifications.
