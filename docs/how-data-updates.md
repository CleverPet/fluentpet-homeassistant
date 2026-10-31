# How data updates

How the integration receives presses, interactions and device changes from
FluentPet: push through a webhook, polling as a fallback, the event feed, and
what happens on errors. For the entities these update, see
[Entities and devices](entities.md). For the options mentioned here, see
[Options](../README.md#options).

## Push through the webhook

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

## Push first, polling as fallback

The integration works in one of two modes:

- **Push.** Events arrive within seconds through the webhook. A safety poll
  every 5 minutes reads the event feed, to catch up events the webhook missed
  and keep the stored feed position current, and then the Bases, for their
  last seen time, power mode and battery level: two requests, plus the
  webhook check every 5 minutes.
  Buttons and learners (two more requests) are read after their events (see
  [Bases and Buttons](#bases-and-buttons)), when a Base reports another
  firmware version, and at the safety poll an hour after their last read,
  because FluentPet can change a Button's firmware version without an event.
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

## Event feed

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

## Bases and Buttons

Every refresh reads the Bases, Buttons and learners after the event feed, so
they are at least as new as the events of that refresh. While polling, they
are all read every refresh interval. In push mode the safety poll reads the
Bases every 5 minutes; the Buttons and learners are read when FluentPet
reports that a Base, Button, learner or teacher was added, changed or removed
(for example a `base.updated` event when the battery level or power mode of a
Base changes). Such an event delivered by the webhook reads them within
seconds. One read from the feed costs no extra refresh: a polling refresh read
them already, and a safety poll that finds such an event the webhook missed
reads them once, in the same poll. A safety poll also reads the Buttons and
learners when a Base reports another firmware version, and at the safety
poll an hour after their last read: FluentPet can rename a build as a published
release without an event. A press or interaction delivered by the
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

## Errors and backoff

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

## Disabled webhook

FluentPet disables a webhook endpoint after 20 failed deliveries in a row. The
integration polls at the refresh interval and raises a repair issue,
**FluentPet webhook disabled**. Check that Home Assistant is reachable from
the internet, then enable the webhook again on
[my.fluent.pet](https://my.fluent.pet). The repair issue goes away at the next
refresh after the webhook is enabled, and push takes over again.
