# Troubleshooting

Troubleshooting, known limitations, debug logging, diagnostics and removal of
the FluentPet Connect integration.

## Common problems

**Setup says you are not the admin of the household.** Sign in with the
account of the household admin. Only the admin receives button presses and
manages webhooks.

**Setup says the integration is not configured, or asks for an application
credential.** Start adding the integration again; it adds the FluentPet
application credential, as described in
[Application credential](../README.md#application-credential). Once the
integration is set up, a restart of Home Assistant adds it too.

**Presses arrive late.** The webhook is not active, so presses arrive from the
event feed at the refresh interval. Check that:

- Home Assistant Cloud is signed in with an active subscription and
  **Prefer Home Assistant Cloud** is on, or
- **Settings > System > Network** has an external URL starting with
  `https://` that is reachable from the internet.

Then reload the integration. The diagnostics show whether the webhook is
active (`webhook.active`).

**A repair issue says the FluentPet webhook is disabled.** See
[Disabled webhook](how-data-updates.md#disabled-webhook).

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

## Debug logging

Add to `configuration.yaml` and restart:

```yaml
logger:
  logs:
    custom_components.fluentpet: debug
```

Or use **Enable debug logging** on the integration page.

## Diagnostics

Download diagnostics from **Settings > Devices & services > FluentPet Connect**,
then the menu of the entry, **Download diagnostics**. The file contains the
config entry, whether a custom API base URL is set (`custom_api_base_url`), the
household's Bases, Buttons, learners and teachers, the webhook state
(`active`, `auto_disabled`, `uses_cloudhook`, `last_delivery`) and the feed
state (`has_cursor`, `update_interval`, `pending_presses`).

These values are redacted: OAuth access, refresh and ID tokens; the Home
Assistant webhook id, the webhook signing secret, the webhook URL and the
cloudhook URL; the API base URL; all URLs; the entry title; all names; the
normalized words and audio ids of Buttons; serial numbers; and email addresses.

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

## Removal

1. Go to **Settings > Devices & services > FluentPet Connect**, open the menu
   of each household entry and choose **Delete**. This deletes the FluentPet
   webhook endpoint of the household, the Home Assistant Cloud cloudhook, the
   repair issues and the stored feed position.
2. In HACS, open **FluentPet Connect**, choose **Remove**, and restart Home
   Assistant. For a manual installation, delete the
   `custom_components/fluentpet` folder and restart Home Assistant.
3. Optional: under **Settings > Devices & services > Application
   credentials**, remove the credential named **FluentPet**, and any other
   credential you added for FluentPet Connect.

If Home Assistant cannot reach FluentPet during removal, or the sign-in is no
longer valid, the webhook endpoint stays at FluentPet and a notification says
so. Delete it on [my.fluent.pet](https://my.fluent.pet).
