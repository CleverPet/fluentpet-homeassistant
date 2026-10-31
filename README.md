# FluentPet Connect for Home Assistant

A Home Assistant custom integration for [FluentPet Connect](https://fluent.pet).
It adds the Bases and Buttons of a FluentPet household to Home Assistant, fires
an event entity when a learner or teacher presses a Button, shows the words of
the latest interaction, and shows when each Base last reported, whether it is
plugged in, and the battery level of each Base and Button.

> **Status: pre-release.** Sign-in depends on FluentPet API endpoints that are
> not available yet. Nothing here is ready to install.

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
  FluentPet at the refresh interval instead. See
  [How data updates](docs/how-data-updates.md).
- Adds Bases and Buttons that join the household later, and removes devices
  that leave it.

Supported devices: the FluentPet Connect Base and FluentPet Connect Buttons.

## Requirements

- Home Assistant 2026.10.0 or newer.
- [HACS](https://hacs.xyz), to install through HACS.
- A FluentPet account that is the admin of the household. The FluentPet API
  serves the event feed and webhooks to the household admin only, so setup
  refuses accounts that are not the admin.
- For push updates: either Home Assistant Cloud with an active subscription, or
  an external URL for Home Assistant that starts with `https://` and is
  reachable from the internet. Without either, the integration polls FluentPet
  at the refresh interval.

## Installation

### HACS

1. In HACS, open the menu in the top right and choose **Custom repositories**.
2. Add `https://github.com/CleverPet/fluentpet-homeassistant` with type
   **Integration**.
3. Search for **FluentPet Connect** in HACS, download it, and restart Home
   Assistant.

### Manual

1. Download the source archive of the latest
   [release](https://github.com/CleverPet/fluentpet-homeassistant/releases),
   or, before the first release, of `main` (**Code > Download ZIP**), and
   extract it.
2. Copy its `custom_components/fluentpet` folder into the `custom_components`
   folder (create it if missing) of your Home Assistant configuration folder.
3. Restart Home Assistant.

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
household. When the sign-in expires or is revoked, Home Assistant asks you to
sign in again. Use an account with access to the same household.

### Options

Open **Settings > Devices & services > FluentPet Connect > Configure**.
Changing an option reloads the integration.

| Option | Default | Range | Description |
| --- | --- | --- | --- |
| Refresh interval | 60 s | 30 to 3600 s | Fallback when push is unavailable (no usable https URL from Home Assistant Cloud or an external https URL, a webhook FluentPet refused, disabled or fails to deliver to, or a failed webhook check): seconds between refreshes of the Bases, Buttons, learners and the event feed. While the webhook delivers, events are pushed and the event feed and the Bases are checked every 5 minutes. |
| Prefer Home Assistant Cloud | On | | With an active Home Assistant Cloud subscription, receive events through a Home Assistant Cloud webhook (cloudhook) instead of your external URL. |
| API base URL (advanced) | The FluentPet API | An https URL | Under **Advanced options**. For developers only; keep the default. |

A refresh interval below 30 s, set in an earlier version, is raised to 30 s
when the integration loads. Developers can point the integration at another API
host from the advanced options. Changing it registers a new webhook endpoint at
the new host; the endpoint at the previous host is not deleted.

## Entities and events

One config entry per household, with a household device, a device per Base
and a device per Button. Entity ids depend on the names in your household.

| Entity | Platform | Device | Example entity id |
| --- | --- | --- | --- |
| Press | `event`, device class `button`, event type `press` | Each Button | `event.outside_press` |
| Last interaction | `sensor` | Household | `sensor.smith_home_last_interaction` |
| Last seen | `sensor`, device class `timestamp` | Each Base | `sensor.kitchen_base_last_seen` |
| Power | `binary_sensor`, device class `plug` | Each Base | `binary_sensor.kitchen_base_power` |
| Battery | `sensor`, device class `battery`, diagnostic | Each Base, each Button linked to a Base | `sensor.kitchen_base_battery` |

See [Entities and devices](docs/entities.md) for states and attributes, and
[Automations](docs/automations.md) for examples.

## Troubleshooting

To enable debug logging, use **Enable debug logging** on the integration page,
or add `custom_components.fluentpet: debug` under `logger: logs:` in
`configuration.yaml` and restart. To download diagnostics, open
**Settings > Devices & services > FluentPet Connect**, then the menu of the
entry, **Download diagnostics**. Tokens, secrets, URLs, names, serial numbers
and email addresses are among the redacted values. For setup errors, late
presses, repair issues and unavailable sensors, see
[Troubleshooting](docs/troubleshooting.md).

## Removal

1. Go to **Settings > Devices & services > FluentPet Connect**, open the menu
   of each household entry and choose **Delete**.
2. In HACS, open **FluentPet Connect**, choose **Remove**, and restart Home
   Assistant. For a manual installation, delete the
   `custom_components/fluentpet` folder and restart Home Assistant.
3. Optional: under **Settings > Devices & services > Application
   credentials**, remove the credential named **FluentPet** (integration
   FluentPet Connect).

What deleting the entry removes, and what to do when FluentPet cannot be
reached, is in [Removal](docs/troubleshooting.md#removal).

## Documentation

The full reference is in [docs](docs/README.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).

FluentPet is a trademark of CleverPet, Inc.
The license does not grant rights to the FluentPet name or logos.
