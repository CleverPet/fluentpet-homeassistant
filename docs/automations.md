# Automations

Use cases and automation examples for the FluentPet Connect integration. The
entity ids in the examples depend on the names in your household; see
[Entities and devices](entities.md).

## Use cases

- Turn on the porch light when a learner or teacher presses **Outside**.
- Play a sound or send a notification for a specific word.
- React to a combination of words, such as **outside** followed by **play**.
- Get notified when the battery of a Base on battery power, or of a Button,
  runs low.

## Trigger on a Button press

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

## Trigger on a press by one learner

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

## Ignore presses replayed after a restart

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

## Condition on the words of the latest interaction

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

## Base battery low

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

## Button battery low

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
