# Zaya Planner: family schedule and house plan

Design of the next part of Zaya Planner, agreed before writing code.
What exists today (task store, executor, `plan_window`, `ensure_state`) is described in the
[README](../README.md); this document is about what is built on top of it.

## 1. Goal

The family plans its days; the house follows.

- Each person has a calendar: school and clubs for children, work and appointments for adults.
- Some events are trips: someone has to drive someone somewhere by a given time.
- From trips the planner works out **departures** and **wake-ups**; from those the house plans
  its work: the car is charged before the first departure, the floor is warm by the wake-up.
- A change in a calendar (a lesson moved, an appointment added) changes the plan right away.
- Everything the house is going to do, and why, is visible in one place: the **house plan**.

Principles:

- **Nothing foreign to Home Assistant.** Calendars, the event dialog, recurrence and
  automations are the standard ones. Zaya Planner only reads the chosen calendars and
  understands `Key: value` lines in event descriptions.
- **Settings in one place**: the integration's options (people) and its car entries.
- Known keys are checked; a mistake is shown, never silently ignored.
- No logic is hard-wired per person or per device: what heats which floor, who is woken,
  which car is charged is set up with automations.
- What can be computed is computed; the person only writes what they know (“school at 8:00,
  take there, 40 minutes on the way”).

### 1.1 Why not our own calendars (decided 2026-10-22)

Steps 1–3 were first built with calendars of our own (`calendar.zaya_<person>`, trip fields
as iCalendar `X-` properties, actions to edit them). It worked, but the trip fields could not
be seen or edited in the standard event dialog, so a card, forms and weekly timetables were
needed, and settings were spread across options, devices and actions. Lines in the event
description give the same data with the standard tools only.

## 2. Example: an ordinary weekday

Masha's calendar (an ordinary Local Calendar “Masha”), Monday to Friday:

```
School                               08:00–13:45, weekly Mon–Fri
───────────── description ─────────────
Trip: take there
Travel: 40
Take the sports kit
```

Dad usually drives Masha and drives the Leaf, so riders, driver and car need not be written.

The house plan for Monday, worked out the evening before:

```
05:20–06:50  Leaf charging          ← departure 7:20 (first Leaf departure of the day)
06:00–06:40  Floor heating          ← wake-up 6:20 (Dad)
06:20        Wake-up (Dad)          ← departure 7:20 − getting ready 60 min
07:20        Departure on the Leaf  ← School 8:00 (Masha), Dad drives, 40 min
08:30        Wake-up (Mum)          ← her weekday wake-up
```

In the evening Dad's Android phone gets an alarm for 6:20.
If school starts at 9:00 this Monday (the event moved with “only this event”), the plan moves.

## 3. Concepts

### 3.1 Calendars

Any Home Assistant calendar can be used; the usual choice is a **Local Calendar** per person,
created in Settings → Integrations. Zaya Planner:

- reads **only the calendars chosen in its settings** (one per person, see 3.3);
  other calendars are never opened;
- **never writes** to these calendars: descriptions stay exactly as the person wrote them;
- knows whose an event is from the calendar it is in, so no “who” line is needed.

An existing calendar with old events can be chosen: only events with known keys mean
anything to the planner.

### 3.2 Event description

A line `Key: value` in the description sets a field. Keys and values are recognised in
Russian and English, in any letter case; spaces around `:` do not matter.

| Key (ru / en) | Values | Meaning |
|---|---|---|
| `Тип` / `Kind` | `подъём` / `wake`, `отмена` / `cancel` | a wake-up or a cancellation (3.5, 3.6); without it, an ordinary event |
| `Поездка` / `Trip` | `отвезти` / `take there`, `забрать` / `pick up`, `отвезти и забрать` / `both` | the event is a trip |
| `В пути` / `Travel` | minutes: `40`, `40 мин`, `1:10` | travel time, including parking |
| `Едут` / `Riders` | names, comma-separated | people going |
| `За рулём` / `Driver` | a name | a rider who drives |
| `Машина` / `Car` | a car's name | the car |

- Names are the names of people and cars in Zaya Planner's settings (case does not matter).
- Lines without `:`, and lines with keys not in the table (“Address: …”, “Room: 12”),
  are notes and are ignored.
- **A known key with a value that cannot be understood is an error**: an unknown car, a driver
  who is not a rider, “Travel: forty”, a trip without a travel time. The event is left out of
  the plan and the error is shown (3.8).
- A timed event longer than 24 hours in a chosen calendar is also shown as an error: it is
  almost always a wrong date in the event dialog.

Three helpers are built on this format and used everywhere:

1. **parse**: description → fields, plus errors;
2. **build**: fields → description lines (the integration writes descriptions of its own
   events this way, e.g. tasks in the “Schedule” calendar:
   `Device: Lamp`, `Status: not run`, `Attempts: 0`);
3. **find**: the events of the chosen calendars that match given fields, e.g. the first event
   of the day with `Car: Leaf` and a trip. Available as the action `zaya_planner.find_events`.

### 3.3 People

People come from Home Assistant **People** (Settings → People); children are added there
without a login. All person settings are in one place: **Zaya Planner → Configure → People**,
a page per person:

| Setting | Example | Meaning |
|---|---|---|
| Calendar | “Masha” | The person's calendar (none: the person has no events) |
| Getting ready | 60 min | Wake-up generated by a trip is this long before the person's departure |
| Wake up for trips | on / off | Whether trips create wake-ups for this person (off for small children) |
| Car | Leaf | The car this person drives by default |
| Usually drives | Dad | For children: who is added to their trips by default |
| Alarm phone | Pixel / none | Android phone that gets the alarm for the wake-up (3.10) |
| Set alarm at | 21:00 | When the alarm for tomorrow is set |

Whether a person can drive follows from the settings: a person with a car is a driver.

Each person still has a device “Zaya: Name”, but only with sensors (next wake-up, next
departure) for dashboards and automations; it has no settings.

### 3.4 Cars

Cars are added with the button “Add car” on the integration page (an entry per car):
name, charger switch (none for a car without one), how long before the departure charging
must end. One car may be driven by several people.

### 3.5 Events

- **Ordinary events** (work, doctor, “the plumber comes”) are markers: by themselves they
  create nothing. Automations can use them with the standard calendar trigger.
- **Trips** are events with a `Trip` line:
  - *Take there*: departure = start − travel time.
  - *Pick up*: departure = end − travel time.
  - *Take there and pick up*: both; the driver comes home in between.
  - Defaults: riders = the calendar's owner + the person who usually drives them;
    driver = the only driver among riders (an error when there are two and none is written);
    car = the driver's car.
- **Wake-ups** are events with `Kind: wake` (3.6).
- **Cancellations** are events with `Kind: cancel` (“Frost”, “Vacation”, “No first two lessons”):
  they switch off what happens in their time *in the same calendar*, without deleting anything.
  All-day or with times, one day or many. Within a cancellation:
  - an event entirely inside it does not happen: no trips, no wake-ups from it;
  - an event that starts inside it starts when it ends (the “take there” departure moves later);
  - an event that ends inside it ends when it starts (the “pick up” departure moves earlier);
  - a wake-up inside it is cancelled.

  Deleting the cancellation brings everything back.

School and clubs are ordinary recurring events (one per weekday pattern); a change on one
day is made with “only this event”. There are no separate weekly timetables.

### 3.6 Wake-ups

A person's wake-ups come from two sources:

1. **Set by the person**: an event with `Kind: wake` in their calendar, usually recurring
   (“Wake-up 8:30 on weekdays”).
2. **Created by trips**: for every rider with “Wake up for trips” on,
   wake-up = their departure − their getting-ready time.

The person's wake-up of the day is the **earliest** of them. “Don't wake me today” = delete
that day's wake-up event; “wake me at 6:00 tomorrow” = move it. Generated wake-ups follow
their trips and are not edited by hand.

### 3.7 House plan and anchors

The integration keeps the plan for the next days up to date and shows it as a read-only
calendar **“House plan”**: departures and wake-ups with their reasons. It is recalculated
when a chosen calendar changes, when a setting changes, every few minutes and after a restart.

Anchors are sensors (timestamps) that rules use:

- `sensor.zaya_<person>_next_wake_up`, `sensor.zaya_<person>_next_departure`;
- `sensor.zaya_planner_first_wake_up` (earliest wake-up in the house);
- `sensor.zaya_<car>_first_departure` (the car's first departure of the day:
  the car is charged once a day for it).

### 3.8 Errors in events

The sensor **“Errors in events”** counts the events of the chosen calendars that could not be
understood and lists them: “School (Masha, Mon 26.10): unknown car ‘Lief’”. A new error also
sends a notification (the same notification setting as failed tasks). An event with an error
is left out of the plan until it is fixed.

### 3.9 Rules

Rules stay ordinary automations, made with the visual editor: the trigger **“Zaya Planner:
time to plan”** (it fires after a start, when automations are saved, when a task ends, when the
house plan changes and every 15 minutes), any conditions, and one action:

- **`plan_window`**: devices on from a start to an end. Each end is a time of day (with days),
  the time of an entity + offset (wake-ups, departures, the sun; the earliest of several), or
  minutes counted from the other end (a charging time, a length). Examples: floor heating
  −20/+20 min around Dad's and Mum's wake-up; the Leaf charged 30 min before its first
  departure; lights from sunset + 30 to sunrise − 30.
- **`upsert`** for a single moment (a time, or the time of an entity + offset).

Rules plan tasks ahead, so the “Schedule” calendar shows them and the executor runs them
reliably. Planning again with nothing changed changes nothing (`unchanged`), so rules may
run often.

### 3.10 Alarms

- No daily summary: the house plan on a dashboard is checked by eye.
- **Android**: in the evening (a time in the person's settings) the system alarm is set for the
  person's wake-up of tomorrow, once a day. If the wake-up moves after that, a notification
  asks to fix the alarm (Android does not let apps change or delete an alarm).
- iPhone: no alarm for now (iOS does not let apps set alarms).

## 4. What changes in the code

Stays: task store, executor, `upsert`, `ensure_state`, `plan_window`, the “time to plan”
trigger, the plan calculation (`plan.py`), the house plan calendar, anchor sensors, alarms.

Goes:

- our own person calendars (`calendar.zaya_<person>`, `.ics` files, `X-ZAYA-*` properties);
- the actions `add_event`, `set_event_details`, `get_events`;
- the settings entities on person and car devices (number, select, switch, time):
  their values move to the options and car entries;
- the planned weekly timetables and dashboard card.

The settings saved today (`.storage/zaya_planner.settings`) are carried over to the options
once, when the new version starts.

## 5. Steps

1. ✅ **Data** (first version, replaced by steps 4–6).
2. ✅ **House plan**: wake-ups and departures, cancellations, the “House plan” calendar,
   anchor sensors, Android alarm.
3. ✅ **Rules**: the device trigger “time to plan”, the action `plan_window`, `upsert` → `unchanged`.
4. **Descriptions**: parse / build / find with tests; task descriptions in “Schedule” built
   with them.
5. **Chosen calendars**: person settings in one options form (with the calendar), cars with
   all their settings in car entries; the house plan reads the chosen Home Assistant
   calendars; “Errors in events”; `find_events`; settings carried over.
6. **Clean-up**: our own calendars, their actions and the settings entities removed;
   README updated.
7. **Moving the real house over**: floor heating → lights → Leaf, the old YAML planner
   running in parallel until the new one has proved itself.

Each step ends tested (automated tests + the test Home Assistant).

## 6. Open questions

None at the moment.

## 7. Later

- Travel time from maps with traffic (Waze free / 2GIS paid; Yandex too expensive).
- One-way export of the house plan to Google Calendar (via the Google Calendar API).
- iPhone: reading the next alarm through Shortcuts; HA as an alarm with critical notifications.
- A daily summary notification, if the dashboard is not enough.
- A shared family calendar with a `Who:` line, if one calendar per person is not enough.
