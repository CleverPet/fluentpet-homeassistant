# Entities and devices

The devices, entities and attributes of the FluentPet Connect integration. For
automation examples, see [Automations](automations.md). For how and when the
values update, see [How data updates](how-data-updates.md).

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

A Base device shows its firmware version (the FluentPet firmware release, such
as `1.1.0`). A Button device shows the firmware version of the release its
Button image belongs to. The firmware version is left empty while FluentPet
has no report yet, for an unlinked Button, and when the installed build is
not a published FluentPet release. A firmware change pushed as a
`base.updated` or `button.updated` event (reason `firmware_reported`) updates
the device within seconds; while polling, it shows up at the next refresh.
FluentPet can also rename a build as a published release without an event. A Base shows that at the next
refresh or safety poll, which then also reads the Buttons. A Button shows it
at the safety poll an hour after the Buttons were last read.

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

## Press event entity

One per Button. It has a single event type, `press`. The state is the time of
the latest press. Attributes of the latest press:

| Attribute | Content |
| --- | --- |
| `event_type` | `press` |
| `button` | `name` and `normalized_word` of the Button |
| `pressed_by` | `name` and `type` (`learner` or `teacher`) of the learner or teacher who pressed, or `null` when FluentPet does not say |
| `press_order` | Position of the press in its interaction at the time of the press, counted from 0, or `null`. A later press, or a reorder, merge or split in the FluentPet app, can change the order; the last interaction sensor shows the current order of the household's latest interaction |
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

## Last interaction sensor

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

## Last seen sensor

One per Base. The state is the time of the Base's last report to FluentPet, or
unknown when FluentPet does not say. It is not an online or liveness signal: a
Base reports only on activity (for example a press) or on its own schedule, so
hours without a report are normal. The sensor is unavailable while refreshes
from FluentPet fail or the last read did not return the Base. It has no
attributes of its own; whether the Base runs plugged in is the
[Power sensor](#power-sensor).

## Power sensor

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

## Battery sensors

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
next refresh; see [Bases and Buttons](how-data-updates.md#bases-and-buttons).
