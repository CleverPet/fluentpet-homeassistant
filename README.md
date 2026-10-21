# FluentPet Connect for Home Assistant

A Home Assistant custom integration for [FluentPet Connect](https://fluent.pet).
It adds the Bases and Buttons of a FluentPet household to Home Assistant, fires
an event entity when a learner or teacher presses a Button, shows the words of
the latest interaction, and shows when each Base last reported, whether it is
plugged in, and the battery level of each Base and Button.

> **Status: pre-release.** Sign-in depends on FluentPet API endpoints that are
> not available yet. Nothing here is ready to install.

## Contents

- [What it does](#what-it-does)
- [Supported devices](#supported-devices)
- [Devices and entities](#devices-and-entities)
- [Requirements](#requirements)
- [Installation](#installation)
- [Setup](#setup)
- [Options](#options)
- [How data updates](#how-data-updates)
- [Use cases and automation examples](#use-cases-and-automation-examples)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Removal](#removal)
- [Development](#development)
- [License](#license)

## What it does

- Signs in with your FluentPet account through OAuth2 (`auth.fluent.pet`), from
  the Home Assistant integrations page.
- Fires an event entity each time a Button is pressed, with the Button, the
  learner or teacher who pressed it and the interaction the press belongs to.
- Shows the words of the household's latest interaction in a sensor.
- Shows when each FluentPet Connect Base last reported and whether it runs
  plugged in or on battery.
- Shows the battery level of each Base and of each Button linked to a Base.
- Receives events by push through a FluentPet household webhook, and checks
  the FluentPet event feed and the Bases every 5 minutes. When push is
  unavailable (for example Home Assistant has no usable https URL), it polls
  FluentPet at the refresh interval instead.
- Adds Bases and Buttons that join the household later, and removes devices
  that leave it.

## Supported devices

- FluentPet Connect Base
- FluentPet Connect Buttons

Buttons appear as devices whether or not they are linked to a Base. Only
presses that FluentPet records in the household event feed reach Home
Assistant.

## Devices and entities

The integration creates one config entry per household and these devices:

| Device | Model | Linked to |
| --- | --- | --- |
| Household (service device, named after the household) | Household | |
| Base (named after the Base, or `Base <serial>`) | Base model from FluentPet | Household |
| Button (named after the Button) | Connect Button | Its Base, or the household when it has no Base |

Entities:

| Entity | Platform | Device | Example entity id |
| --- | --- | --- | --- |
| Press | `event`, device class `button` | Each Button | `event.outside_press` |
| Last interaction | `sensor` | Household | `sensor.smith_home_last_interaction` |
| Last seen | `sensor`, device class `timestamp` | Each Base | `sensor.kitchen_base_last_seen` |
| Power | `binary_sensor`, device class `plug` | Each Base | `binary_sensor.kitchen_base_power` |
| Battery | `sensor`, device class `battery`, diagnostic | Each Base | `sensor.kitchen_base_battery` |
| Battery | `sensor`, device class `battery`, diagnostic | Each Button linked to a Base | `sensor.outside_battery` |

Entity ids are derived from the device name and the entity name, so they
depend on the names in your household.

### Press event entity

One per Button. It has a single event type, `press`. The state is the time of
the latest press. Attributes of the latest press:

| Attribute | Content |
| --- | --- |
| `event_type` | `press` |
| `button` | `name` and `normalized_word` of the Button |
| `pressed_by` | `name` and `type` (`learner` or `teacher`) of the learner or teacher who pressed, or `null` when FluentPet does not say |
| `press_order` | Position of the press in its interaction |
| `occurred_at` | When the press happened, ISO 8601 |
| `interaction_id` | FluentPet id of the interaction the press belongs to, or `null` |
| `replayed` | `true` for a press read from the event feed during catch-up: after Home Assistant starts or the integration reloads, after failed refreshes, or while the feed was not readable, until the feed is read to its end. `false` for presses the webhook delivers, and for a press the webhook missed that a later refresh reads |

The entity is unavailable only while refreshes from FluentPet fail and the
webhook is not active. Presses read from the feed fire after the refresh that
read them succeeded, so the entity is available then. A press the webhook
delivers while the entity is unavailable fires once it is available again.

Presses fire in the order they reach Home Assistant, which is not always the
order they happened: a press read from the feed (for example one the webhook
missed) can fire after a newer press the webhook delivered, so the entity's
last state then describes the older press. Use the `occurred_at` attribute
for the time of the press.

### Last interaction sensor

One per household. The state is the words of the latest interaction, joined
with spaces and cut to 255 characters. Each word is the Button name, or the
Button's normalized word when it has no name. The sensor keeps its value
across restarts. While an interaction grows, the sensor updates with each new
press. Attributes:

| Attribute | Content |
| --- | --- |
| `sequence` | List of the words in press order. Presses of a Button that was deleted since have no word and are left out, so the list can be shorter than `press_count` |
| `press_count` | Number of presses in the interaction |
| `occurred_at` | When the interaction happened, ISO 8601 |
| `pressed_by` | `name` and `type` (`learner` or `teacher`) of the learner or teacher of the interaction, or `null` |
| `interaction_id` | FluentPet id of the interaction |

The sensor is unavailable only while refreshes from FluentPet fail and the
webhook is not active. An interaction that arrives while it is unavailable is
shown once the sensor is available again.

### Last seen sensor

One per Base. The state is the time of the Base's last report to FluentPet, or
unknown when FluentPet does not say. It is not an online or liveness signal: a
Base reports only on activity (for example a press) or on its own schedule, so
hours without a report are normal. The sensor is unavailable while refreshes
from FluentPet fail or the last read did not return the Base. It has no
attributes of its own; whether the Base runs plugged in is the
[Power sensor](#power-sensor).

### Power sensor

One per Base, a binary sensor named **Power** with device class `plug`. The
state is `on` (shown as Plugged in) while the Base runs plugged in (FluentPet
reports a charging Base as plugged in) and `off` (shown as Unplugged) while it
runs on battery, so its history shows when a Base ran on battery. It is
unknown while FluentPet has not reported a power mode for the Base, or reports
one the integration does not know. The sensor is unavailable while refreshes
from FluentPet fail or the last read did not return the Base. Buttons have no
power sensor.

When a Base is plugged in or unplugged, FluentPet sends a `base.updated`
event, which updates the sensor like a battery level change; see
[Battery sensors](#battery-sensors).

### Battery sensors

One per Base and one per Button linked to a Base, named **Battery**, in the
diagnostic category. The state is the battery charge in percent. The
precision is coarse: FluentPet reports only the values 100, 75, 50, 25 and 0,
the same steps the FluentPet app shows, so the sensor changes in steps of 25.

A battery sensor is unavailable while refreshes from FluentPet fail or the
last read did not return its Base or Button.

A Base battery sensor shows the level FluentPet reports, whatever the power
mode. It is unknown, and stays available, whenever FluentPet reports no level
for the Base: while it runs plugged in (FluentPet reports no level then) and
before it has reported a level. It shows the level again once FluentPet
reports one, for example after the Base is unplugged.

A Button battery sensor is also unavailable while FluentPet reports no
battery level for the Button. A Button reports its level through its Base
when it is pressed, so a Button's battery sensor is unavailable until its
first press after FluentPet started reporting Button levels. FluentPet also
reports no level for a Button whose battery is empty, so a Button battery
sensor that becomes unavailable after showing a level can mean the battery
needs replacing. A Button that is not linked to a Base has no battery sensor;
the sensor is removed when the Button is unlinked and comes back when it is
linked again.

The battery sensors have no attributes of their own. Whether a Base runs
plugged in or on battery is its [Power sensor](#power-sensor).

When the battery level of a Base or Button moves to another step, FluentPet
sends a `base.updated` or `button.updated` event. Delivered by the webhook, it
makes the integration read the Bases and Buttons within seconds, so the sensor
follows without waiting for the safety poll. A delivery the webhook missed is
caught by the next safety poll, and while polling the change shows up at the
next refresh; see [Bases and Buttons](#bases-and-buttons).

## Requirements

- Home Assistant 2026.10.0 or newer.
- [HACS](https://hacs.xyz).
- A FluentPet account that is the admin of the household. The FluentPet API
  serves the event feed and webhooks to the household admin only, so setup
  refuses accounts that are not the admin.
- For push updates: either Home Assistant Cloud with an active subscription, or
  an external URL for Home Assistant that starts with `https://` and is
  reachable from the internet. Without either, the integration polls FluentPet
  at the refresh interval.

## Installation

1. In HACS, open the menu in the top right and choose **Custom repositories**.
2. Add `https://github.com/CleverPet/fluentpet-homeassistant` with type
   **Integration**.
3. Search for **FluentPet Connect** in HACS, download it, and restart Home
   Assistant.

## Setup

### Application credential

No step is needed. The integration ships the public client id of the FluentPet
OAuth client for Home Assistant and adds it as an application credential named
**FluentPet** when you start adding the integration and no FluentPet
application credential exists yet, and on each start of Home Assistant once
the integration is set up. An existing credential is kept. The client is a
public client that signs in with PKCE and has no secret.

### Add the integration

1. Go to **Settings > Devices & services > Add integration** and choose
   **FluentPet Connect**.
2. Select **Submit** on the first form. Leave **Advanced options** as they
   are.
3. If you have more than one application credential for FluentPet Connect,
   choose one.
4. Sign in with the FluentPet account of the household admin on
   `auth.fluent.pet` and allow access.
5. The integration sets up the household of the signed-in account, which must
   be its admin.

Setup stops with a message when the account belongs to no household, when its
household is already configured, or when the account is not the admin of its
household.

When the sign-in expires or is revoked, Home Assistant asks you to sign in
again. Use an account with access to the same household.

## Options

Open **Settings > Devices & services > FluentPet Connect > Configure**.
Changing an option reloads the integration.

| Option | Default | Range | Description |
| --- | --- | --- | --- |
| Refresh interval | 60 s | 30 to 3600 s | Fallback when push is unavailable (no usable https URL from Home Assistant Cloud or an external https URL, a webhook FluentPet refused, disabled or fails to deliver to, or a failed webhook check): seconds between refreshes of the Bases, Buttons, learners and the event feed. While the webhook delivers, events are pushed and the event feed and the Bases are checked every 5 minutes. |
| Prefer Home Assistant Cloud | On | | With an active Home Assistant Cloud subscription, receive events through a Home Assistant Cloud webhook (cloudhook) instead of your external URL. |
| API base URL (advanced) | The FluentPet API | An https URL | Under **Advanced options**. For developers only; keep the default. |

A refresh interval below 30 s, set in an earlier version, is raised to 30 s
when the integration loads.

Developers can point the integration at another API host from the advanced
options. Changing it registers a new webhook endpoint at the new host; the
endpoint at the previous host is not deleted.

## How data updates

### Push through the webhook

At setup the integration registers a webhook in Home Assistant and a matching
webhook endpoint for the household at FluentPet. The delivery URL is chosen as
follows:

1. With **Prefer Home Assistant Cloud** on and an active Home Assistant Cloud
   subscription, it uses a cloudhook.
2. Otherwise it uses the external URL of Home Assistant, if it starts with
   `https://`.
3. Without a usable https URL, it polls at the refresh interval instead, and
   tries again 5 minutes later, then at doubling intervals up to once an hour,
   and whenever Home Assistant Cloud connects.

A registered cloudhook is kept while **Prefer Home Assistant Cloud** is on and
Home Assistant Cloud is signed in, also while the cloud is disconnected or its
subscription briefly reads as inactive. When the subscription is inactive and
an external https URL exists, the endpoint moves to that URL (and the cloudhook
is deleted) only if the subscription is still inactive when the integration
checks again at least 5 minutes later. Turning **Prefer Home Assistant Cloud**
off moves the endpoint to the external URL and deletes the cloudhook at once.
When the cloudhook URL changes, the endpoint is updated to the new URL.

While the webhook is active, the integration reads the FluentPet endpoint
again every 5 minutes, and at every refresh while FluentPet reports failed
deliveries. An endpoint FluentPet disabled raises the repair issue below and
polling takes over; one that was deleted is created again; one disabled by
hand or pointing at another URL is enabled again and updated.

If FluentPet creates the endpoint but its answer cannot be read, the
integration deletes the endpoints with its URL before creating one again, so
no untracked endpoint is left behind.

When no usable URL is found, a previously registered FluentPet endpoint is
kept at first, so a short outage (a cloud reconnect, a network change) does not
remove it. If there is still no usable URL when the integration checks again at
least 5 minutes later, it deletes the endpoint, and the cloudhook when one was
used, and keeps checking at the slow interval to register again.

Every delivery is checked before it is processed:

- The request must carry a valid HMAC signature made with the endpoint's
  signing secret. Requests without one, or with a wrong one, are refused.
- The signed timestamp must be within 5 minutes of the Home Assistant clock.
- Repeated deliveries and repeated event ids are ignored, so a press fires
  once even when FluentPet retries a delivery or the same event also arrives
  from the feed.
- A delivery of an event FluentPet stored more than 10 minutes earlier (a late
  retry) is answered but not processed: the feed, read at every refresh, has
  processed that event already, or reads it from its stored position.

### Push first, polling as fallback

The integration works in one of two modes:

- **Push.** Events arrive within seconds through the webhook. A safety poll
  every 5 minutes reads the event feed, to catch up events the webhook missed
  and keep the stored feed position current, and then the Bases, for their
  last seen time, power mode and battery level: two requests, plus the
  webhook check every 5 minutes.
  Buttons and learners are read after their events (see
  [Bases and Buttons](#bases-and-buttons)).
- **Polling.** Every refresh interval reads the event feed, then the Bases,
  Buttons and learners.

Push is used while the webhook is healthy:

- it is registered and enabled, FluentPet accepted it, and the last check of
  it succeeded; and
- FluentPet reports no failed deliveries, or Home Assistant received a
  signed delivery (a ping counts too) in the last 10 minutes. FluentPet
  counts failed deliveries in a row and resets the count only after a
  delivered event, so a recent delivery shows that the webhook works while
  the count is still above 0.

Otherwise the integration polls: without a usable https URL (Home Assistant
Cloud or an external https URL), when FluentPet refused or disabled the
webhook, when it reports failed deliveries and nothing arrived for 10 minutes,
or when the last webhook check failed. The health is evaluated again at every
refresh and on every delivery, and the integration switches modes as it
changes. So a 10 minute window without deliveries that runs out is noticed
at the next refresh (up to 5 minutes later, or 10 during backoff). The first
refresh after the webhook becomes healthy still reads everything once, and so
does the refresh after a failed one. The mode only sets how fast changes
arrive: the safety poll's feed read catches up any event the webhook missed.

### Event feed

The household event feed is read at every refresh, in both modes. With push,
presses arrive within seconds and the feed read keeps the stored feed
position current; while polling, the feed read is how presses arrive.

Events read from the feed are applied only when the whole refresh succeeds,
and the stored position moves only then. A refresh that fails part way reads
the same events again next time; events already processed do not fire twice.

- **Feed position.** The integration stores its position in the feed and the
  ids of the last 512 processed events. After a restart it continues from the
  stored position, so presses and interactions from while Home Assistant was
  offline are processed, and events already processed do not fire again.
- **First setup.** On the first setup it starts at the end of the feed, so
  older history is not imported.
- **Rejected position.** FluentPet refuses a feed position it did not issue for
  the household, for example after the stored file was damaged. The
  integration then logs a warning and continues from the end of the feed, so
  events since the last refresh can be missed. A stored file of the wrong
  shape is handled the same way. In push mode the same refresh then also
  reads the Buttons and learners, since changes to them may be among the
  missed events.
- **Malformed events.** An event in the feed that cannot be read is skipped
  with a warning; the feed position still moves past it.

### Bases and Buttons

Every refresh reads the Bases, Buttons and learners after the event feed, so
they are at least as new as the events of that refresh. While polling, they
are all read every refresh interval. In push mode the safety poll reads the
Bases every 5 minutes; the Buttons and learners are read when FluentPet
reports that a Base, Button, learner or teacher was added, changed or removed
(for example a `base.updated` event when the battery level or power mode of a
Base changes). Such an event delivered by the webhook reads them within
seconds. One read from the feed costs no extra refresh: a polling refresh read
them already, and a safety poll that finds such an event the webhook missed
reads them once, in the same poll. A press or interaction delivered by the
webhook moves the last seen time of its Base forward to when FluentPet stored
the event, without a request. A later read with an older or unknown time does
not move it back until Home Assistant restarts or the integration reloads.

A press on a Button that Home Assistant does not know yet fires once the
Button's event entity exists. When the webhook delivers it, it triggers a
refresh. When the feed delivers it, the refresh that read it also reads the
Buttons, in both modes, so no second refresh follows. A press of a Button that
is still unknown after a Buttons read that started after the press arrived is
dropped.

A Base, Button, learner or teacher that FluentPet returns in a shape the
integration cannot read is skipped with a warning. While any item is skipped,
no devices are removed, so a malformed item does not remove its device.

### Errors and backoff

- On a rate limit (HTTP 429) or a FluentPet server error, the interval
  doubles after each failure, up to 10 minutes, in both modes. It returns to
  the interval of the current mode (5 minutes in push mode, the refresh
  interval while polling) after the next successful refresh. When a rate
  limit answer says how long to wait (`Retry-After`), the next refresh waits
  that long instead.
- When FluentPet refuses the access token, or the sign-in server refuses to
  renew it, Home Assistant asks you to sign in again and stops refreshing until
  you do. When the sign-in server fails for the moment, the refresh is retried.
- When registering or checking the webhook fails for a temporary reason, the
  integration polls and tries again at the next refresh.
- When FluentPet refuses the webhook (for example an invalid URL, or the
  household already has the most webhooks FluentPet allows), the integration
  polls and tries again 5 minutes later, then at doubling intervals up
  to once an hour. When the household is at its webhook limit, a repair issue,
  **FluentPet webhook limit reached**, explains how to free a slot.
- When FluentPet answers that the account is not allowed (it is no longer the
  household admin), a repair issue, **FluentPet household admin access
  needed**, is raised. The feed and the webhook are tried again at the same
  slow intervals, and the issue goes away once admin access returns.

### Disabled webhook

FluentPet disables a webhook endpoint after 20 failed deliveries in a row. The
integration polls at the refresh interval and raises a repair issue,
**FluentPet webhook disabled**. Check that Home Assistant is reachable from
the internet, then enable the webhook again on
[my.fluent.pet](https://my.fluent.pet). The repair issue goes away at the next
refresh after the webhook is enabled, and push takes over again.

## Use cases and automation examples

- Turn on the porch light when a learner or teacher presses **Outside**.
- Play a sound or send a notification for a specific word.
- React to a combination of words, such as **outside** followed by **play**.
- Get notified when the battery of a Base on battery power, or of a Button,
  runs low.

### Trigger on a Button press

An event entity changes state on every press. Exclude changes from
`unavailable` and `unknown`, which happen at startup and reload.

```yaml
automation:
  - alias: "Porch light on Outside press"
    triggers:
      - trigger: state
        entity_id: event.outside_press
        not_from:
          - unavailable
          - unknown
    actions:
      - action: light.turn_on
        target:
          entity_id: light.porch
```

### Trigger on a press by one learner

```yaml
automation:
  - alias: "Notify when Bunny presses Outside"
    triggers:
      - trigger: state
        entity_id: event.outside_press
        not_from:
          - unavailable
          - unknown
    conditions:
      - condition: template
        value_template: >
          {{ (trigger.to_state.attributes.pressed_by or {}).get('name') == 'Bunny' }}
    actions:
      - action: notify.notify
        data:
          message: "Bunny pressed Outside"
```

### Ignore presses replayed after a restart

Presses that happened while Home Assistant was offline, while refreshes
failed, or while the feed was not readable, are read from the feed when it
catches up and fire with their original `occurred_at`, with `replayed` set to
`true`. A press the webhook missed and a later refresh reads is not marked
replayed. This condition skips the replayed ones:

```yaml
conditions:
  - condition: template
    value_template: "{{ not trigger.to_state.attributes.replayed }}"
```

### Condition on the words of the latest interaction

The `sequence` attribute lists the words in press order, as named in the
FluentPet app.

```yaml
automation:
  - alias: "Outside then play"
    triggers:
      - trigger: state
        entity_id: sensor.smith_home_last_interaction
        not_from:
          - unavailable
          - unknown
    conditions:
      - condition: template
        value_template: >
          {% set words = state_attr('sensor.smith_home_last_interaction', 'sequence') or [] %}
          {{ words | map('lower') | list == ['outside', 'play'] }}
    actions:
      - action: notify.notify
        data:
          message: "Outside, then play"
```

### Base battery low

Notifies once when the battery of the Base named **Kitchen Base** is at 25% or
lower while it is not plugged in, and not again until the level rises above
25% or the Base is plugged in. A Toggle helper remembers that the notification
was sent; create it under **Settings > Devices & services > Helpers**, or in
YAML as below. The plugged in check reads the Power sensor; the battery
sensor is unknown while the Base is plugged in, because FluentPet reports no
level then. A Power sensor that is unknown (the Base has never reported a
power mode) counts as not plugged in, so the example still notifies on a low
level. The levels are 100, 75, 50, 25 and 0, so `below: 26` means 25 or
lower.

```yaml
input_boolean:
  kitchen_base_battery_low_notified:
    name: Kitchen Base battery low notified

automation:
  - alias: "Kitchen Base battery low"
    mode: queued
    triggers:
      - trigger: homeassistant
        event: start
      - trigger: numeric_state
        entity_id: sensor.kitchen_base_battery
        below: 26
      - trigger: numeric_state
        entity_id: sensor.kitchen_base_battery
        above: 25
      - trigger: state
        entity_id: binary_sensor.kitchen_base_power
    actions:
      - choose:
          - conditions:
              - condition: or
                conditions:
                  - condition: numeric_state
                    entity_id: sensor.kitchen_base_battery
                    above: 25
                  - condition: state
                    entity_id: binary_sensor.kitchen_base_power
                    state: "on"
            sequence:
              - action: input_boolean.turn_off
                target:
                  entity_id: input_boolean.kitchen_base_battery_low_notified
          - conditions:
              - condition: numeric_state
                entity_id: sensor.kitchen_base_battery
                below: 26
              - condition: state
                entity_id: input_boolean.kitchen_base_battery_low_notified
                state: "off"
            sequence:
              - action: input_boolean.turn_on
                target:
                  entity_id: input_boolean.kitchen_base_battery_low_notified
              - action: notify.notify
                data:
                  message: >
                    The kitchen Base battery is at
                    {{ states('sensor.kitchen_base_battery') }}%
```

The first option resets the helper when the level is above 25% or the Base is
plugged in; only when neither holds does the second option notify, so a Base
that is unplugged while its level is already low notifies at that moment. The
start trigger checks a Base that is already low when Home Assistant starts;
the helper keeps its state across restarts, so a notification already sent
is not sent again, and a battery sensor still unknown or unavailable at start
matches neither option and is handled by the numeric state trigger once it
has a level.

### Button battery low

The same for the Button named **Outside**, without the power mode: it
notifies once when the battery is at 25% or lower, and not again until the
level rises above 25%, for example after the battery is replaced.

```yaml
input_boolean:
  outside_button_battery_low_notified:
    name: Outside Button battery low notified

automation:
  - alias: "Outside Button battery low"
    mode: queued
    triggers:
      - trigger: homeassistant
        event: start
      - trigger: numeric_state
        entity_id: sensor.outside_battery
        below: 26
      - trigger: numeric_state
        entity_id: sensor.outside_battery
        above: 25
    actions:
      - choose:
          - conditions:
              - condition: numeric_state
                entity_id: sensor.outside_battery
                above: 25
            sequence:
              - action: input_boolean.turn_off
                target:
                  entity_id: input_boolean.outside_button_battery_low_notified
          - conditions:
              - condition: numeric_state
                entity_id: sensor.outside_battery
                below: 26
              - condition: state
                entity_id: input_boolean.outside_button_battery_low_notified
                state: "off"
            sequence:
              - action: input_boolean.turn_on
                target:
                  entity_id: input_boolean.outside_button_battery_low_notified
              - action: notify.notify
                data:
                  message: >
                    The Outside Button battery is at
                    {{ states('sensor.outside_battery') }}%
```

The automation does not fire when a Button goes straight from above 25% to no
level: a Button reports only when pressed, so a rarely pressed Button can skip
steps until its battery is empty, and FluentPet reports an empty Button
battery as no level. It does not trigger on unavailable, because the sensor
is also unavailable while refreshes from FluentPet fail, which would send
false notifications.

## Known limitations

- In push mode, battery level and power mode changes arrive as pushed
  `base.updated` and `button.updated` events, so they are current while
  deliveries work. The last seen time of a Base, and a battery or power mode
  change whose webhook delivery was missed, can be up to 5 minutes behind (up
  to 10 minutes during backoff); the safety poll catches them. A pushed press
  or interaction moves the last seen time forward sooner. While polling, a
  change shows up after up to one refresh interval, or up to 10 minutes during
  backoff. How often a Base checks in, and so how fresh its last seen time is,
  is up to the Base.
- Presses that happened while Home Assistant was offline arrive at the next
  start, with their original `occurred_at` and `replayed` set to `true`. At
  most 5,000 events are read per refresh; a longer backlog continues at the
  next refresh.
- While refreshes from FluentPet fail, the last seen, power and battery
  sensors are unavailable. The press entities and the last interaction sensor
  stay available while the webhook is active; without the webhook they are
  unavailable too, and the presses and interactions read once refreshes work
  again fire after the entities are back. A press delivered by the webhook
  while its entity is unavailable is held (up to 100 per entity) and fires
  once the entity is back; a press that cannot be held, or is still held when
  the integration unloads or Home Assistant restarts, is left for the feed to
  deliver again, and fires once.
- History from before the first setup is not imported.
- Only the household admin can set up the integration. Other members are
  refused, and households the account is not the admin of are not offered.
- One household per config entry. Add the integration again for each
  household of the account.
- Push needs Home Assistant Cloud or an external https URL. Over plain http,
  or with only an internal URL, the integration polls the event feed and
  presses arrive with a delay of up to one refresh interval.
- The integration provides no actions; it does not change anything in the
  household.

## Troubleshooting

**Setup says you are not the admin of the household.** Sign in with the
account of the household admin. Only the admin receives button presses and
manages webhooks.

**Setup says the integration is not configured, or asks for an application
credential.** Start adding the integration again; it adds the FluentPet
application credential, as described in
[Application credential](#application-credential). Once the integration is set
up, a restart of Home Assistant adds it too.

**Presses arrive late.** The webhook is not active, so presses arrive from the
event feed at the refresh interval. Check that:

- Home Assistant Cloud is signed in with an active subscription and
  **Prefer Home Assistant Cloud** is on, or
- **Settings > System > Network** has an external URL starting with
  `https://` that is reachable from the internet.

Then reload the integration. The diagnostics show whether the webhook is
active (`webhook.active`).

**A repair issue says the FluentPet webhook is disabled.** See
[Disabled webhook](#disabled-webhook).

**A repair issue says the FluentPet webhook limit is reached.** FluentPet
allows a limited number of webhooks per household. Delete a webhook you no
longer use on [my.fluent.pet](https://my.fluent.pet), for example one left by
an earlier Home Assistant installation. The integration tries again on its
own; the issue goes away once the webhook is registered.

**A repair issue says household admin access is needed.** The account Home
Assistant signed in with is no longer the household admin, so FluentPet stops
sending button presses. Make the account the household admin again in the
FluentPet app, or ask the household admin to set up the integration with
their account. The issue goes away once admin access returns.

**A Base's last seen or Power sensor is unavailable.** The last refresh from
FluentPet failed, or the last read did not return the Base (for example
FluentPet sent it in a form the integration could not read, which is logged as
a warning). Check the Home Assistant logs for errors from
`custom_components.fluentpet`. A last seen of unknown means FluentPet has not
reported when it last heard from the Base. A last seen hours ago is normal: a
Base reports only on activity or on its own schedule.

**A battery sensor is unavailable.** For a Button, FluentPet reports no
battery level for it yet. A Button reports its level when it is pressed, so
press it once. A Button battery sensor that becomes unavailable after showing
a level can mean the battery is empty and needs replacing: FluentPet reports
an empty Button battery as no level. Otherwise the last refresh from
FluentPet failed or the last read did not return the Button (for a Base
battery sensor, the Base); check the logs as above.

**A Base battery sensor is unknown.** FluentPet reports no level for the
Base. It reports none while the Base runs plugged in (its Power sensor is
`on`), and a Base can also have no level for other reasons, for example one
that has not reported a level yet or has never reported a power mode (its
Power sensor is unknown). The sensor stays available and shows the level once
FluentPet reports one.

**A new Button has no entity.** New Buttons are added at the next refresh, or
within seconds when the webhook delivers the change. Reload the integration to
force a refresh.

### Debug logging

Add to `configuration.yaml` and restart:

```yaml
logger:
  logs:
    custom_components.fluentpet: debug
```

Or use **Enable debug logging** on the integration page.

### Diagnostics

Download diagnostics from **Settings > Devices & services > FluentPet Connect**,
then the menu of the entry, **Download diagnostics**. The file contains the
config entry, whether a custom API base URL is set (`custom_api_base_url`), the
household's Bases, Buttons, learners and teachers, the webhook state
(`active`, `auto_disabled`, `uses_cloudhook`, `last_delivery`) and the feed
state (`has_cursor`, `update_interval`, `pending_presses`).

These values are redacted: OAuth access, refresh and ID tokens; the Home
Assistant webhook id, the webhook signing secret, the webhook URL and the
cloudhook URL; the API base URL; all URLs; the entry title; all names; the normalized words and
audio ids of Buttons; serial numbers; and email addresses.

## Removal

1. Go to **Settings > Devices & services > FluentPet Connect**, open the menu
   of the household entry and choose **Delete**. This deletes the FluentPet
   webhook endpoint of the household, the Home Assistant Cloud cloudhook, the
   repair issues and the stored feed position.
2. In HACS, open **FluentPet Connect**, choose **Remove**, and restart Home
   Assistant.
3. Optional: remove the FluentPet Connect application credential under
   **Settings > Devices & services > Application credentials**.

If Home Assistant cannot reach FluentPet during removal, or the sign-in is no
longer valid, the webhook endpoint stays at FluentPet and a notification says
so. Delete it on [my.fluent.pet](https://my.fluent.pet).

## Development

Requirements: Python 3.14 (Home Assistant requires 3.14.2 or newer).

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
```

Without uv: `python3.14 -m venv .venv` and
`.venv/bin/pip install -r requirements_test.txt`.

Checks (the same as CI):

```sh
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest
```

`hassfest` and HACS validation run in GitHub Actions
(`.github/workflows/validate.yml`) on every push to `main` and on pull
requests. To run hassfest locally:

```sh
docker run --rm -v "$PWD":/github/workspace ghcr.io/home-assistant/hassfest
```

### API client library

The FluentPet API client is vendored at `custom_components/fluentpet/fluentpet_api`
and imported as `from .fluentpet_api import ...`. It depends only on `aiohttp`
and never imports Home Assistant. `libs/fluentpet-api` holds the packaging
metadata for the future PyPI package `fluentpet-api`; nothing is published from
there yet. Its tests are in `tests/fluentpet_api`. See
[libs/fluentpet-api/README.md](libs/fluentpet-api/README.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).

FluentPet is a trademark of CleverPet, Inc. The license does not grant rights to the FluentPet name or logos.
