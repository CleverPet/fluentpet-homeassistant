# Changelog

All notable changes to this project are documented in this file.

## Unreleased

- Repository scaffold.
- FluentPet Connect integration (`fluentpet`), installable as a HACS custom
  integration. Requires Home Assistant 2026.10.0 or newer.
- OAuth2 sign-in with PKCE through `auth.fluent.pet`, with the public FluentPet
  client id imported as an application credential. Household chosen automatically
  or from a list; accounts that are not the household admin are refused.
  Reauthentication flow.
- Options: refresh interval (10 to 3600 s, default 30) and prefer Home
  Assistant Cloud (default on).
- Devices: household hub, Bases, and Buttons linked to their Base. Devices are
  added and removed as the household changes.
- Event platform: a press event entity per Button, fired when a learner or
  teacher presses it. The `pressed_by` attribute names who pressed it and
  whether that is a learner or a teacher.
- Sensor platform: last interaction sensor per household, restored across
  restarts, with the same `pressed_by` attribute for the learner or teacher of
  the interaction.
- Sensor platform: last seen sensor per Base (timestamp), with the Base's
  power mode (`plugged_in` or `battery`) and battery level as attributes.
- Push through a FluentPet household webhook, delivered to a Home Assistant
  Cloud cloudhook or the external https URL, with HMAC signature verification,
  a 5 minute timestamp window and deduplication.
- Event feed read every refresh interval, with the feed position and the
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
- Press entities get a `replayed` attribute. Press and last interaction
  entities are unavailable while refreshes fail and the webhook is not active;
  events that arrive meanwhile fire once they are available again.
- The household list in setup offers only households the account administers
  and that are not configured yet.
- Diagnostics also redact the normalized words and audio ids of Buttons.
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
