# Changelog

All notable changes to this project are documented in this file.

## Unreleased

- Repository scaffold.
- FluentPet Connect integration (`fluentpet`), installable as a HACS custom
  integration. Requires Home Assistant 2026.10.0 or newer.
- OAuth2 sign-in with PKCE through `auth.fluent.pet`, with the public FluentPet
  client id imported as an application credential. Sets up the household of
  the signed-in account, which must be its admin; other accounts are refused.
  Reauthentication flow.
- Options: refresh interval (30 to 3600 s, default 60) and prefer Home
  Assistant Cloud (default on).
- Devices: household hub, Bases, and Buttons (model Connect Button) linked to
  their Base. Devices are added and removed as the household changes.
- Event platform: a press event entity per Button, fired when a learner or
  teacher presses it. The `pressed_by` attribute names who pressed it and
  whether that is a learner or a teacher. The `press_order` attribute is the
  position of the press in its interaction at the time of the press, counted
  from 0; a later press, or a reorder, merge or split in the FluentPet app,
  can change it.
- Sensor platform: last interaction sensor per household, restored across
  restarts, with the same `pressed_by` attribute for the learner or teacher of
  the interaction.
- Sensor platform: last seen sensor per Base (timestamp, the time of the
  Base's last report), without attributes.
- Binary sensor platform: power sensor per Base (device class plug), on
  (Plugged in) while the Base runs plugged in, off (Unplugged) on battery,
  unknown while FluentPet has not reported a power mode or reports one the
  integration does not know. A `base.updated` event (reason
  `battery_reported` or `power_mode_reported`) delivered by the webhook
  updates it within seconds; while polling, the change shows up at the next
  refresh.
- Sensor platform: battery sensor per Base and per Button linked to a Base
  (device class battery, diagnostic), in the steps the FluentPet app shows
  (100, 75, 50, 25, 0). A Base battery sensor is unknown, and stays
  available, while FluentPet reports no level, as it does while the Base runs
  plugged in. A Button battery sensor is unavailable while FluentPet reports
  no level; a Button reports its level from its first press after FluentPet
  started reporting Button levels. A Button's sensor is removed when it is
  unlinked from its Base and comes back when it is linked again. An empty
  Button battery reads as no level. A level change pushed as a `base.updated`
  or `button.updated` event updates the sensor within seconds.
- Push through a FluentPet household webhook, delivered to a Home Assistant
  Cloud cloudhook or the external https URL, with HMAC signature verification,
  a 5 minute timestamp window and deduplication. Deliveries relayed by Home
  Assistant Cloud are read and processed like direct requests. A delivery
  whose body cannot be read is answered with 400 (413 over the body size
  limit) and logged as a warning with its delivery id, so FluentPet counts it
  as failed.
- Event feed read at every refresh, with the feed position and the
  last processed event ids stored across restarts, so presses are neither
  missed nor fired twice. Backoff on rate limits and server errors, up to 10
  minutes.
- Repair issue when FluentPet disables the webhook after failed deliveries.
- Diagnostics with tokens, secrets, URLs, names, serial numbers and email
  addresses redacted.
- Removing the entry deletes the FluentPet webhook endpoint and the cloudhook.
- `fluentpet_api` client library for the FluentPet api/v3 household API,
  webhooks and event feed, vendored in the integration, with packaging metadata
  in `libs/fluentpet-api` for the future `fluentpet-api` PyPI package.
- Brand folder from the official FluentPet symbol and the 2022 joined logo.
- Push first: while the webhook is healthy, a safety poll every 5 minutes
  reads the event feed, then the Bases (for their last seen time); Buttons and
  learners are read after a Base, Button, learner or teacher was added,
  changed or removed, after a feed press of an unknown Button, once when the
  webhook becomes healthy, after a failed refresh, when the feed position was
  reset, and on request. Healthy means registered, enabled, accepted and
  checked, with no failed deliveries reported by FluentPet or a signed
  delivery received in the last 10 minutes. Otherwise the integration polls
  everything at the refresh interval, now the fallback cadence. Backoff
  applies in both modes. A refresh interval below 30 s is raised to 30 s by a
  config entry migration (version 1.2).
- A press or interaction delivered by the webhook moves the last seen time of
  its Base forward, without a request; a refresh in flight or a Bases read
  with an older or unknown time does not move it back until Home Assistant
  restarts or the integration reloads.
- A refresh that reads a press of an unknown Button reads the Buttons and
  learners in the same refresh, and drops the press there when the Button is
  still unknown, without a second round.
- Every refresh reads the event feed before the Bases, Buttons and learners,
  so they are at least as new as its events.
- The webhook check every 5 minutes also runs when the refresh timer fires a
  little early.
- Diagnostics show whether push is active and the webhook's health and
  failure count.
- A lifecycle event read from the feed no longer requests another refresh: a
  polling refresh read the resources already, and a safety poll reads them
  once in the same poll.
- Push recovery: without a usable URL or after FluentPet refused the webhook,
  registration is tried again after 5 minutes, then at doubling intervals up
  to an hour, and when Home Assistant Cloud connects. A registered endpoint or
  cloudhook is no longer deleted on the first check without a URL.
- Repair issues when the household is at its webhook limit and when the
  account lost household admin access; a notification when removal could not
  delete the FluentPet webhook.
- A refused or damaged feed position continues from the end of the feed
  instead of failing every refresh. Malformed events and resources are
  skipped with a warning.
- A failed token renewal starts reauthentication instead of being retried as
  a network error. The `Retry-After` of a rate limit is honored.
- Late webhook retries of events older than 10 minutes are acknowledged but
  not fired again.
- Press entities get a `replayed` attribute: `true` for a press read from the
  feed during catch-up (after startup, a reload or failed refreshes, or while
  the feed was not readable, until the feed is read to its end), `false` for pushed presses and for a missed press
  a later refresh reads, whatever its age. Press and last interaction
  entities are unavailable while refreshes fail and the webhook is not active;
  events that arrive meanwhile fire once they are available again.
- The household list in setup offers only households the account administers
  and that are not configured yet.
- Diagnostics also redact the normalized words and audio ids of Buttons.
- Diagnostics include the battery level and battery reading time of Bases
  and Buttons.
- An active webhook is checked again every 5 minutes, so one FluentPet
  disabled or deleted while Home Assistant runs is noticed and handled.
- An inactive Home Assistant Cloud subscription moves the webhook off the
  cloudhook only when it is still inactive 5 minutes later.
- Feed events are applied, and the feed position stored, only when the whole
  refresh succeeds. Held presses that cannot be applied are left for the feed.
- Removing an entry with an expired sign-in no longer starts reauthentication.
- Advanced option for developers: the API base URL, in a collapsed section of
  the first setup form and of the options. Changing it registers a new webhook
  at the new host; the one at the previous host is not deleted. Diagnostics
  report only whether a custom API base URL is set.
- A Button or Base skipped by one refresh is no longer added twice, and a
  disabled press or last seen entity is no longer rebuilt on every update.
- Presses held while their entity is unavailable are not stored as processed,
  so the feed fires them once after a restart.
- Adding the integration on a fresh install no longer stops with a missing
  application credential: adding the integration and reauthentication add
  the FluentPet application credential when no application credential exists,
  without a restart.
- Reauthentication signs in again with the application credential of the
  entry instead of asking to pick one, and the credential picker shows each
  credential by its name.
- Base devices show the FluentPet firmware release (such as `1.1.0`) as their
  firmware version; Button devices show the firmware release of their Button
  image.
  The firmware version is left empty while FluentPet reports none, including
  when the installed build is not a published release. It replaces the raw
  firmware component string Bases showed before. A `firmware_reported`
  `base.updated` or `button.updated` event delivered by the webhook updates
  the device within seconds. FluentPet can publish a build as a release
  without an event: a safety poll that finds a Base with another firmware
  version also reads the Buttons, and the safety poll an hour after the last
  read of the Buttons and learners reads them again, so Button firmware
  versions follow at that poll, about an hour later. Entity ids and unique
  ids are unchanged.
