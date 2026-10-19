# FluentPet Connect for Home Assistant

A Home Assistant custom integration for [FluentPet Connect](https://fluent.pet).
It adds the Bases and Buttons of a FluentPet household to Home Assistant, fires
an event entity when a learner or teacher presses a Button, shows the words of
the latest interaction, and shows when each Base was last seen, with its power
mode and battery level.

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
- Shows when each FluentPet Connect Base was last seen, whether it runs plugged
  in or on battery, and its battery level.
- Receives events by push through a FluentPet household webhook. When Home
  Assistant has no usable https URL, it reads the FluentPet event feed at a
  fixed interval instead.
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
| Button (named after the Button) | Button | Its Base, or the household when it has no Base |

Entities:

| Entity | Platform | Device | Example entity id |
| --- | --- | --- | --- |
| Press | `event`, device class `button` | Each Button | `event.outside_press` |
| Last interaction | `sensor` | Household | `sensor.smith_home_last_interaction` |
| Last seen | `sensor`, device class `timestamp` | Each Base | `sensor.kitchen_base_last_seen` |

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
| `replayed` | `true` when the press fired more than 2 minutes after it happened, for example a press read from the feed after Home Assistant was offline |

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

One per Base. The state is when FluentPet last heard from the Base, or
unknown when FluentPet does not say. The sensor is unavailable while refreshes
from FluentPet fail. Attributes:

| Attribute | Content |
| --- | --- |
| `power_mode` | `plugged_in` or `battery`, or `null` when FluentPet does not say |
| `battery_level` | Battery charge in percent, 0 to 100, or `null` when FluentPet does not say |

The state and both attributes are kept in the recorder history, so the
history shows when a Base ran on battery and how its charge went.

## Requirements

- Home Assistant 2026.10.0 or newer.
- [HACS](https://hacs.xyz).
- A FluentPet account that is the admin of the household. The FluentPet API
  serves the event feed and webhooks to the household admin only, so setup
  refuses accounts that are not the admin.
- For push updates: either Home Assistant Cloud with an active subscription, or
  an external URL for Home Assistant that starts with `https://` and is
  reachable from the internet. Without either, presses arrive from the event
  feed at the refresh interval.

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
**FluentPet** when Home Assistant starts. The client is a public client that
signs in with PKCE and has no secret.

### Add the integration

1. Go to **Settings > Devices & services > Add integration** and choose
   **FluentPet Connect**.
2. Select **Submit** on the first form. Leave **Advanced options** as they
   are.
3. If you have more than one application credential for FluentPet Connect,
   choose one.
4. Sign in with the FluentPet account of the household admin on
   `auth.fluent.pet` and allow access.
5. The integration lists the households of the account that it can set up:
   households the account is the admin of and that are not configured yet.
   - With one such household, it is chosen automatically.
   - With several, choose one from the list. To add another household, add the
     integration again.

Setup stops with a message when the account belongs to no household, when
every household of the account is already configured, or when the account is
not the admin of any household that is not configured yet.

When the sign-in expires or is revoked, Home Assistant asks you to sign in
again. Use an account with access to the same household.

## Options

Open **Settings > Devices & services > FluentPet Connect > Configure**.
Changing an option reloads the integration.

| Option | Default | Range | Description |
| --- | --- | --- | --- |
| Refresh interval | 30 s | 10 to 3600 s | Seconds between refreshes of the Bases, Buttons and the event feed. |
| Prefer Home Assistant Cloud | On | | With an active Home Assistant Cloud subscription, receive events through a Home Assistant Cloud webhook (cloudhook) instead of your external URL. |
| API base URL (advanced) | The FluentPet API | An https URL | Under **Advanced options**. For developers only; keep the default. |

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
3. Without a usable https URL, it reads the event feed instead, and tries
   again 5 minutes later, then at doubling intervals up to once an hour, and
   whenever Home Assistant Cloud connects.

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
the feed takes over; one that was deleted is created again; one disabled by
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
  retry) is answered but not processed: the feed, read every refresh interval,
  has processed that event already, or reads it from its stored position.

### Event feed

The household event feed is read every refresh interval, also while the
webhook is active. With a webhook, presses arrive within seconds and the feed
read keeps the stored feed position current; without one (no usable https
URL, FluentPet refused the webhook, or FluentPet disabled it), the feed read is
how presses arrive.

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
  shape is handled the same way.
- **Malformed events.** An event in the feed that cannot be read is skipped
  with a warning; the feed position still moves past it.

### Bases and Buttons

The lists of Bases, Buttons, learners and teachers are refreshed every refresh
interval, also when the webhook is active. The last seen time, power mode and
battery level of each Base come from this refresh, so they lag by up to one
interval. When FluentPet reports that a Base,
Button, learner or teacher was added, changed or removed, the integration
refreshes right away.

A press on a Button that Home Assistant does not know yet triggers a refresh;
the press fires once the Button's event entity exists. Presses of a Button
that is still unknown after the next refresh are dropped.

A Base, Button, learner or teacher that FluentPet returns in a shape the
integration cannot read is skipped with a warning. While any item is skipped,
no devices are removed, so a malformed item does not remove its device.

### Errors and backoff

- On a rate limit (HTTP 429) or a FluentPet server error, the refresh interval
  doubles after each failure, up to 10 minutes. It returns to the configured
  interval after the next successful refresh. When a rate limit answer says
  how long to wait (`Retry-After`), the next refresh waits that long instead.
- When FluentPet refuses the access token, or the sign-in server refuses to
  renew it, Home Assistant asks you to sign in again and stops refreshing until
  you do. When the sign-in server fails for the moment, the refresh is retried.
- When registering the webhook fails for a temporary reason, the integration
  tries again at the next refresh and reads the feed meanwhile.
- When FluentPet refuses the webhook (for example an invalid URL, or the
  household already has the most webhooks FluentPet allows), the integration
  reads the feed and tries again 5 minutes later, then at doubling intervals up
  to once an hour. When the household is at its webhook limit, a repair issue,
  **FluentPet webhook limit reached**, explains how to free a slot.
- When FluentPet answers that the account is not allowed (it is no longer the
  household admin), a repair issue, **FluentPet household admin access
  needed**, is raised. The feed and the webhook are tried again at the same
  slow intervals, and the issue goes away once admin access returns.

### Disabled webhook

FluentPet disables a webhook endpoint after 20 failed deliveries in a row. The
integration keeps reading the event feed every refresh interval and raises a
repair issue, **FluentPet webhook disabled**. Check that Home Assistant is
reachable from the internet, then enable the webhook again on
[my.fluent.pet](https://my.fluent.pet). The repair issue goes away at the next
refresh after the webhook is enabled.

## Use cases and automation examples

- Turn on the porch light when a learner or teacher presses **Outside**.
- Play a sound or send a notification for a specific word.
- React to a combination of words, such as **outside** followed by **play**.
- Get notified when a Base has not been seen for a while, when it switches to
  battery, or when its battery runs low.

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

Presses that happened while Home Assistant was offline are read from the
feed at startup and fire with their original `occurred_at`, with `replayed`
set to `true`. This condition skips them:

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

### Base not seen for 2 hours

The Last seen sensor of a Base named **Kitchen Base** is
`sensor.kitchen_base_last_seen`. Its state is the time FluentPet last heard
from the Base. This template trigger fires once when that time becomes more
than 2 hours old, and again only after the Base was seen and went quiet again.
An unknown last seen does not trigger it.

```yaml
automation:
  - alias: "Kitchen Base not seen"
    triggers:
      - trigger: template
        value_template: >
          {% set last_seen = states('sensor.kitchen_base_last_seen') | as_datetime(none) %}
          {{ last_seen is not none and now() - last_seen > timedelta(hours=2) }}
    actions:
      - action: notify.notify
        data:
          message: "The kitchen Base has not been seen for 2 hours"
```

### Base on battery or low battery

The `power_mode` and `battery_level` attributes of the Last seen sensor tell
when a Base runs on battery and how full it is.

```yaml
automation:
  - alias: "Kitchen Base power"
    triggers:
      - trigger: state
        entity_id: sensor.kitchen_base_last_seen
        attribute: power_mode
        to: battery
        id: on_battery
      - trigger: numeric_state
        entity_id: sensor.kitchen_base_last_seen
        attribute: battery_level
        below: 20
        id: low_battery
    actions:
      - action: notify.notify
        data:
          message: >
            {% if trigger.id == 'on_battery' %}
              The kitchen Base runs on battery
            {% else %}
              The kitchen Base battery is below 20%
            {% endif %}
```

## Known limitations

- The last seen time, power mode and battery level of a Base are polled, so a
  change shows up after up to one refresh interval, or up to 10 minutes during
  backoff. How often a Base checks in, and so how fresh its last seen time is,
  is up to the Base.
- Presses that happened while Home Assistant was offline arrive at the next
  start, with their original `occurred_at` and `replayed` set to `true`. At
  most 5,000 events are read per refresh; a longer backlog continues at the
  next refresh.
- While refreshes from FluentPet fail, the last seen sensors are
  unavailable. The press entities and the last interaction sensor stay
  available while the webhook is active; without the webhook they are
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
credential.** Restart Home Assistant so the integration adds the FluentPet
application credential again, as described in
[Application credential](#application-credential).

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

**A Base's last seen sensor is unavailable.** The last refresh from FluentPet
failed. Check the Home Assistant logs for errors from
`custom_components.fluentpet`. A last seen of unknown means FluentPet has not
reported when it last heard from the Base.

**A new Button has no entity.** New Buttons are added at the next refresh, or
right away when FluentPet reports the change. Reload the integration to force
a refresh.

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
