"""Webhook of the FluentPet Connect integration."""

import asyncio
from datetime import datetime
from http import HTTPStatus
import re
from typing import Any

from aiohttp import web
from aiohttp.hdrs import METH_POST

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    OAuth2TokenRequestBaseError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.util import dt as dt_util
from homeassistant.util.aiohttp import MockRequest

from . import cloudhook
from .const import (
    CONF_CLOUDHOOK,
    CONF_PREFER_CLOUDHOOK,
    CONF_WEBHOOK_ENDPOINT_ID,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL,
    DEFAULT_PREFER_CLOUDHOOK,
    DOMAIN,
    ISSUE_WEBHOOK_DISABLED,
    ISSUE_WEBHOOK_LIMIT,
    LOGGER,
    MY_FLUENTPET_URL,
    SLOW_RETRY_MIN,
    VERIFY_SLACK,
    WEBHOOK_DELIVERY_WINDOW,
    WEBHOOK_STALE_EVENT_AGE,
)
from .coordinator import FluentPetConfigEntry
from .events import FluentPetEventProcessor
from .fluentpet_api import (
    HEADER_DELIVERY,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    FluentPetAuthError,
    FluentPetClient,
    FluentPetError,
    FluentPetForbiddenError,
    FluentPetNotFoundError,
    FluentPetParseError,
    FluentPetRequestError,
    WebhookVerificationError,
    WebhookWithSecret,
    parse_event_body,
    verify_signature,
)
from .retry import SlowRetry

# The 422 message of a create beyond the household limit, for example "Validation
# failed: A household can have at most 5 webhooks".
_WEBHOOK_LIMIT_MESSAGE = re.compile(r"at most \d+ webhooks", re.IGNORECASE)


def webhook_issue_id(entry_id: str) -> str:
    """Return the repair issue id of an auto-disabled webhook."""
    return f"{ISSUE_WEBHOOK_DISABLED}_{entry_id}"


def webhook_limit_issue_id(entry_id: str) -> str:
    """Return the repair issue id of a household at its webhook limit."""
    return f"{ISSUE_WEBHOOK_LIMIT}_{entry_id}"


def webhook_healthy(
    *,
    active: bool,
    check_failed: bool,
    failure_count: int,
    last_delivery: datetime | None,
    now: datetime,
) -> bool:
    """Return whether push can replace polling; the one place of this rule.

    FluentPet counts consecutive failed delivery attempts in failure_count. It
    resets the count only on a successful delivery of an event that is not a
    ping, or when the webhook is enabled again, and disables the webhook (enabled
    false) after 20 failures in a row. A count above 0 can therefore stay while
    deliveries work, for example when only pings arrived since. A delivery that
    passed the signature check here within WEBHOOK_DELIVERY_WINDOW proves that
    FluentPet reaches Home Assistant, so it outweighs the count.

    Healthy: the webhook is active (registered, enabled, not refused) and its
    last check succeeded, and FluentPet reports no failure or a verified
    delivery arrived within the window. Health only governs latency; the feed
    read of the safety poll covers missed events.
    """
    if not active or check_failed:
        return False
    return failure_count == 0 or (
        last_delivery is not None and now - last_delivery <= WEBHOOK_DELIVERY_WINDOW
    )


def _is_webhook_limit(err: FluentPetRequestError) -> bool:
    """Return whether FluentPet refused a new webhook because the household has the most."""
    return (
        err.status == HTTPStatus.UNPROCESSABLE_ENTITY
        and err.error_message is not None
        and _WEBHOOK_LIMIT_MESSAGE.search(err.error_message) is not None
    )


class FluentPetWebhookManager:
    """Keeps a FluentPet webhook pointed at this Home Assistant and receives it.

    While the webhook is healthy (see webhook_healthy), the coordinator reads the
    event feed and the Bases every SAFETY_POLL_INTERVAL and the other resources
    after their events. Otherwise it refreshes everything with the refresh
    interval.

    What the next refresh would not fix (no usable URL, a refused webhook) is tried
    again on a slow schedule, from 5 minutes up to 1 hour, and when Home Assistant
    Cloud connects. Transient errors are tried again at the next refresh.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FluentPetConfigEntry,
        client: FluentPetClient,
        household_id: str,
        processor: FluentPetEventProcessor,
    ) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self.client = client
        self.household_id = household_id
        self.processor = processor
        self._active = False
        self.auto_disabled = False
        self.forbidden = False
        # The consecutive failed deliveries FluentPet reported at the last check.
        self._failure_count = 0
        # The health last evaluated, which the coordinator was told about.
        self._healthy = False
        self.last_delivery: datetime | None = None
        self._needs_check = False
        self._refused = False
        self._no_url_since: datetime | None = None
        self._cloudhook_unusable_since: datetime | None = None
        self._verify_at: datetime | None = None
        self._retry = SlowRetry()
        self._shutdown = False
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        """Return whether the webhook is registered and FluentPet delivers to it."""
        return self._active

    @active.setter
    def active(self, active: bool) -> None:
        """Set whether the webhook delivers; entities follow it for availability."""
        self._async_set_state(active=active)

    @property
    def failure_count(self) -> int:
        """Return the consecutive failed deliveries FluentPet reported last."""
        return self._failure_count

    @property
    def failing(self) -> bool:
        """Return whether FluentPet reported failed deliveries at the last check."""
        return self._failure_count > 0

    @callback
    def _async_set_state(
        self,
        *,
        active: bool | None = None,
        needs_check: bool | None = None,
        failure_count: int | None = None,
    ) -> None:
        """Change the state that makes the webhook healthy, in one step.

        Entities follow active for availability; then the health is evaluated.
        """
        if needs_check is not None:
            self._needs_check = needs_check
        if failure_count is not None:
            self._failure_count = failure_count
        if active is not None and active != self._active:
            self._active = active
            self.processor.coordinator.async_update_listeners()
        self.async_update_health()

    @callback
    def async_update_health(self) -> None:
        """Evaluate the health, and tell the coordinator once when it changed.

        Also called at every refresh, since a delivery window can expire.
        """
        healthy = webhook_healthy(
            active=self._active,
            check_failed=self._needs_check,
            failure_count=self._failure_count,
            last_delivery=self.last_delivery,
            now=dt_util.utcnow(),
        )
        if healthy != self._healthy:
            self._healthy = healthy
            self.processor.coordinator.async_webhook_health_changed()

    @property
    def healthy(self) -> bool:
        """Return whether push can replace polling, as last evaluated.

        See webhook_healthy.
        """
        return self._healthy

    @property
    def webhook_id(self) -> str:
        """Return the Home Assistant webhook id."""
        return str(self.entry.data[CONF_WEBHOOK_ID])

    @property
    def uses_cloudhook(self) -> bool:
        """Return whether the registered URL is a Home Assistant Cloud webhook."""
        return bool(self.entry.data.get(CONF_CLOUDHOOK, False))

    @property
    def _prefer_cloudhook(self) -> bool:
        """Return whether the user prefers a Home Assistant Cloud webhook."""
        return bool(
            self.entry.options.get(CONF_PREFER_CLOUDHOOK, DEFAULT_PREFER_CLOUDHOOK)
        )

    async def async_setup(self) -> None:
        """Register the local webhook and reconcile the FluentPet webhook."""
        if CONF_WEBHOOK_ID not in self.entry.data:
            self._async_update_data({CONF_WEBHOOK_ID: webhook.async_generate_id()})
        webhook.async_register(
            self.hass,
            DOMAIN,
            self.entry.title,
            self.webhook_id,
            self.async_handle_webhook,
            allowed_methods=[METH_POST],
        )
        self.entry.async_on_unload(
            cloudhook.async_listen_cloudhook_change(
                self.hass, self.webhook_id, self._async_cloudhook_changed
            )
        )
        self.entry.async_on_unload(
            cloudhook.async_listen_connection_change(
                self.hass, self._async_cloud_connection_changed
            )
        )
        await self.async_reconcile()

    @callback
    def async_unload(self) -> None:
        """Unregister the local webhook and stop changing the FluentPet webhook.

        The FluentPet webhook stays; removing the entry deletes it.
        """
        self._shutdown = True
        webhook.async_unregister(self.hass, self.webhook_id)

    async def async_check(self) -> None:
        """Reconcile after a failure, when a slow retry is due, or to verify.

        An active webhook is read again every SLOW_RETRY_MIN (at the next refresh
        while FluentPet reports failed deliveries), so one FluentPet disabled,
        deleted or changed while Home Assistant runs is noticed. A refresh up to
        VERIFY_SLACK early still verifies, so timer rounding does not skip a
        check and stretch it to twice the interval.
        """
        now = dt_util.utcnow()
        if (
            self._needs_check
            or self._retry.due(now)
            or (
                self.active
                and self._verify_at is not None
                and now >= self._verify_at - VERIFY_SLACK
            )
        ):
            await self.async_reconcile()

    async def async_reconcile(self) -> None:
        """Make the FluentPet webhook deliver to the current URL.

        Raises:
            FluentPetAuthError: The access token was refused.
            OAuth2TokenRequestReauthError: The token could not be refreshed.
        """
        async with self._lock:
            if self._shutdown:
                return
            try:
                await self._async_reconcile()
            except FluentPetAuthError, OAuth2TokenRequestReauthError:
                raise
            except FluentPetForbiddenError as err:
                self._async_refused(err)
                self._async_set_forbidden(True)
            except FluentPetRequestError as err:
                self._async_refused(err)
                if _is_webhook_limit(err):
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        webhook_limit_issue_id(self.entry.entry_id),
                        is_fixable=False,
                        severity=ir.IssueSeverity.WARNING,
                        translation_key=ISSUE_WEBHOOK_LIMIT,
                        translation_placeholders={
                            "household": self.entry.title,
                            "url": MY_FLUENTPET_URL,
                        },
                    )
            except (FluentPetError, OAuth2TokenRequestBaseError) as err:
                LOGGER.debug("Could not update the FluentPet webhook: %s", err)
                self._async_set_state(needs_check=True)

    @callback
    def _async_refused(self, err: FluentPetError) -> None:
        """Fall back to the feed after FluentPet refused the webhook; retry slowly."""
        if not self._refused:
            LOGGER.warning(
                "FluentPet refused the webhook (%s); events arrive with the"
                " refresh interval",
                err,
            )
        self._refused = True
        self._async_set_state(active=False, needs_check=False)
        self._retry.schedule(dt_util.utcnow())

    @callback
    def _async_set_forbidden(self, forbidden: bool) -> None:
        """Record whether FluentPet answered 403, and update the not-admin issue."""
        self.forbidden = forbidden
        self.processor.coordinator.async_update_not_admin_issue()

    async def _async_reconcile(self) -> None:
        """Create, update or check the FluentPet webhook."""
        had_cloudhook = self.uses_cloudhook
        url, is_cloudhook = await self._async_get_url()
        if url is None:
            await self._async_no_url(had_cloudhook)
            return
        self._no_url_since = None

        # After the API base URL option changed, the stored endpoint belongs to the
        # previous host: the new host does not know its id and answers 404, so a
        # new endpoint is created there and its id and secret replace the stored
        # ones. The endpoint on the previous host is not deleted; its deliveries
        # fail the signature check with the new secret until that host disables
        # it. The stored feed position is refused by the new host in the same way
        # and restarts at the end of its feed.
        endpoint_id = self.entry.data.get(CONF_WEBHOOK_ENDPOINT_ID)
        remote = None
        if endpoint_id is not None:
            if CONF_WEBHOOK_SECRET in self.entry.data:
                remote = await self.client.async_get_webhook(
                    self.household_id, endpoint_id
                )
            else:
                # Deliveries cannot be verified without the secret, which FluentPet
                # shows only at creation: replace the endpoint.
                await self._async_remove_endpoint()

        if remote is None:
            if self._shutdown:
                return
            created = await self._async_create(url)
            self._async_update_data(
                {
                    CONF_WEBHOOK_ENDPOINT_ID: created.id,
                    CONF_WEBHOOK_SECRET: created.secret,
                    CONF_WEBHOOK_URL: url,
                    CONF_CLOUDHOOK: is_cloudhook,
                }
            )
            LOGGER.debug("Created FluentPet webhook %s", created.id)
        elif remote.auto_disabled:
            if not self.auto_disabled:
                LOGGER.warning(
                    "FluentPet disabled the webhook after failed deliveries; events"
                    " arrive with the refresh interval until it is enabled again"
                )
            self.auto_disabled = True
            self._async_set_state(active=False, needs_check=True)
            self._async_accepted()
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                webhook_issue_id(self.entry.entry_id),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_WEBHOOK_DISABLED,
                translation_placeholders={
                    "household": self.entry.title,
                    "url": MY_FLUENTPET_URL,
                },
            )
            return
        else:
            if remote.url != url or not remote.enabled:
                if self._shutdown:
                    return
                await self.client.async_update_webhook(
                    self.household_id, remote.id, url=url, enabled=True
                )
            self._async_update_data(
                {CONF_WEBHOOK_URL: url, CONF_CLOUDHOOK: is_cloudhook}
            )

        if had_cloudhook and not is_cloudhook:
            await cloudhook.async_delete_cloudhook(self.hass, self.webhook_id)
        if self.auto_disabled:
            LOGGER.info("The FluentPet webhook is enabled again")
        self.auto_disabled = False
        failure_count = 0 if remote is None else remote.failure_count
        self._async_set_state(
            active=True, needs_check=False, failure_count=failure_count
        )
        now = dt_util.utcnow()
        self._verify_at = now if self.failing else now + SLOW_RETRY_MIN
        self._async_accepted()
        ir.async_delete_issue(self.hass, DOMAIN, webhook_issue_id(self.entry.entry_id))

    async def _async_create(self, url: str) -> WebhookWithSecret:
        """Create the FluentPet webhook.

        When FluentPet answered a create with a body that cannot be read, the
        webhook may exist without its id or secret being known. Webhooks with this
        URL are then deleted before creating once more, so none is left behind.
        """
        try:
            return await self.client.async_create_webhook(self.household_id, url)
        except FluentPetParseError as err:
            LOGGER.warning(
                "Could not read the FluentPet webhook FluentPet created (%s);"
                " replacing it",
                err,
            )
        for remote in await self.client.async_list_webhooks(self.household_id):
            if remote.url != url:
                continue
            try:
                await self.client.async_delete_webhook(self.household_id, remote.id)
            except FluentPetNotFoundError:
                LOGGER.debug("The FluentPet webhook %s was already deleted", remote.id)
        return await self.client.async_create_webhook(self.household_id, url)

    @callback
    def _async_accepted(self) -> None:
        """Clear the refused state after FluentPet answered a webhook request."""
        if self._refused:
            LOGGER.info("FluentPet accepted the webhook again")
        self._refused = False
        self._retry.reset()
        ir.async_delete_issue(
            self.hass, DOMAIN, webhook_limit_issue_id(self.entry.entry_id)
        )
        if self.forbidden:
            self._async_set_forbidden(False)

    async def _async_no_url(self, had_cloudhook: bool) -> None:
        """Fall back to the feed without a usable URL, and retry slowly.

        Not destructive on a transient state: the stored endpoint (and cloudhook) is
        kept the first time no URL is found, and deleted only when there is still no
        usable URL on a re-check at least SLOW_RETRY_MIN later. A stored cloudhook
        never gets here while it is preferred and Home Assistant Cloud is signed in
        (see _async_get_url).
        """
        now = dt_util.utcnow()
        self._async_set_state(active=False, needs_check=False)
        if self._no_url_since is None:
            LOGGER.info(
                "No external https URL for the FluentPet webhook; events arrive"
                " with the refresh interval"
            )
            self._no_url_since = now
            self._retry.schedule(now)
            return
        if now - self._no_url_since < SLOW_RETRY_MIN:
            # An early re-check (a cloud connection change) keeps the schedule.
            return
        self._retry.schedule(now)
        await self._async_remove_endpoint()
        if had_cloudhook:
            await cloudhook.async_delete_cloudhook(self.hass, self.webhook_id)

    async def _async_remove_endpoint(self) -> None:
        """Delete the stored FluentPet webhook and forget it."""
        endpoint_id = self.entry.data.get(CONF_WEBHOOK_ENDPOINT_ID)
        if endpoint_id is None:
            return
        try:
            await self.client.async_delete_webhook(self.household_id, endpoint_id)
        except FluentPetNotFoundError:
            LOGGER.debug("The FluentPet webhook %s was already deleted", endpoint_id)
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                key: value
                for key, value in self.entry.data.items()
                if key
                not in (
                    CONF_WEBHOOK_ENDPOINT_ID,
                    CONF_WEBHOOK_SECRET,
                    CONF_WEBHOOK_URL,
                    CONF_CLOUDHOOK,
                )
            },
        )
        LOGGER.debug("Deleted FluentPet webhook %s", endpoint_id)

    async def _async_get_url(self) -> tuple[str | None, bool]:
        """Return the URL to deliver to and whether it is a cloudhook.

        A stored cloudhook stays the URL while it is preferred and Home Assistant
        Cloud is signed in, also while the cloud is disconnected or its subscription
        reads as inactive for a moment. With an inactive subscription and an
        external https URL, the webhook moves to the external URL only when the
        subscription is still inactive on a check at least SLOW_RETRY_MIN after it
        was first seen inactive. The cloudhook is given up at once when
        prefer_cloudhook is turned off; when the cloud is signed out without an
        external URL, the no-URL rule of _async_no_url applies.
        """
        prefer = self._prefer_cloudhook
        stored = self.entry.data.get(CONF_WEBHOOK_URL)
        keep_cloudhook = prefer and self.uses_cloudhook and stored is not None
        if prefer and cloudhook.async_active_subscription(self.hass):
            self._cloudhook_unusable_since = None
            url = await cloudhook.async_get_cloudhook_url(self.hass, self.webhook_id)
            if url is not None:
                return url, True
            if keep_cloudhook:
                return str(stored), True
        try:
            url = webhook.async_generate_url(
                self.hass, self.webhook_id, allow_internal=False, prefer_external=True
            )
        except NoURLAvailableError:
            url = None
        if url is not None and url.startswith("https://"):
            if keep_cloudhook and self._cloudhook_grace():
                return str(stored), True
            self._cloudhook_unusable_since = None
            return url, False
        if keep_cloudhook and cloudhook.async_is_logged_in(self.hass):
            return str(stored), True
        return None, False

    def _cloudhook_grace(self) -> bool:
        """Return whether a stored cloudhook is kept while the cloud is unusable.

        True until the cloud has been unusable for SLOW_RETRY_MIN.
        """
        now = dt_util.utcnow()
        if self._cloudhook_unusable_since is None:
            self._cloudhook_unusable_since = now
        return now - self._cloudhook_unusable_since < SLOW_RETRY_MIN

    @callback
    def _async_update_data(self, updates: dict[str, Any]) -> None:
        """Merge values into the entry data."""
        if any(self.entry.data.get(key) != value for key, value in updates.items()):
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, **updates}
            )

    @callback
    def _async_cloudhook_changed(self, change: dict[str, Any] | None) -> None:
        """Point the FluentPet webhook at a changed cloudhook URL."""
        if not self.uses_cloudhook:
            return
        url = change.get("cloudhook_url") if change else None
        if url == self.entry.data.get(CONF_WEBHOOK_URL):
            return
        self._async_reconcile_in_background()

    @callback
    def _async_cloud_connection_changed(self, connected: bool) -> None:
        """Reconcile when Home Assistant Cloud connects, to use or keep a cloudhook."""
        if connected and self._prefer_cloudhook:
            self._async_reconcile_in_background()

    @callback
    def _async_reconcile_in_background(self) -> None:
        """Reconcile outside the coordinator."""
        if self._shutdown:
            return
        self.entry.async_create_background_task(
            self.hass, self._async_reconcile_or_reauth(), "fluentpet webhook update"
        )

    async def _async_reconcile_or_reauth(self) -> None:
        """Reconcile outside the coordinator; start reauth on an auth error."""
        try:
            await self.async_reconcile()
        except FluentPetAuthError, OAuth2TokenRequestReauthError:
            self.entry.async_start_reauth(self.hass)

    async def async_handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Verify, deduplicate and process one webhook delivery."""
        delivery = request.headers.get(HEADER_DELIVERY)
        try:
            body = await _async_read_body(request)
        except web.HTTPRequestEntityTooLarge as err:
            LOGGER.warning(
                "Rejected FluentPet webhook delivery %s: %s", delivery, err.text
            )
            return web.Response(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except (web.RequestPayloadError, OSError) as err:
            LOGGER.warning(
                "Could not read the body of FluentPet webhook delivery %s: %s",
                delivery,
                # aiohttp's payload errors span lines; keep the warning on one.
                " ".join(str(err).split()),
            )
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        secret = self.entry.data.get(CONF_WEBHOOK_SECRET)
        timestamp = request.headers.get(HEADER_TIMESTAMP)
        signature = request.headers.get(HEADER_SIGNATURE)
        if secret is None or timestamp is None or signature is None:
            LOGGER.warning("Rejected a FluentPet webhook request without a signature")
            return web.Response(status=HTTPStatus.UNAUTHORIZED)
        try:
            verify_signature(secret, timestamp, body, signature)
        except WebhookVerificationError as err:
            LOGGER.warning("Rejected a FluentPet webhook request: %s", err)
            return web.Response(status=HTTPStatus.UNAUTHORIZED)
        # A signed request, a ping too, proves FluentPet reaches Home Assistant.
        self.last_delivery = dt_util.utcnow()
        self.async_update_health()

        try:
            event = parse_event_body(body)
        except FluentPetParseError as err:
            LOGGER.warning("Rejected a FluentPet webhook body: %s", err)
            return web.Response(status=HTTPStatus.BAD_REQUEST)

        if delivery is not None and not self.processor.seen_deliveries.add(delivery):
            LOGGER.debug("Ignoring repeated delivery %s", delivery)
            return web.Response(status=HTTPStatus.OK)
        if dt_util.utcnow() - event.created_at > WEBHOOK_STALE_EVENT_AGE:
            # A late retry. The feed, read every refresh, processed the event already
            # (or will, from its stored position), so firing it here could fire it
            # twice once its id left the dedupe set.
            LOGGER.debug("Ignoring late delivery of event %s", event.id)
            return web.Response(status=HTTPStatus.OK)
        self.processor.coordinator.async_base_seen(event)
        self.processor.async_process(event)
        return web.Response(status=HTTPStatus.OK)


async def _async_read_body(request: web.Request | MockRequest) -> bytes:
    """Return the raw body of a direct or a Home Assistant Cloud request.

    Home Assistant Cloud hands a cloudhook delivery over as a MockRequest, which
    has no read(). Its content stream returns the whole body from memory. A
    direct request is read with read(), which enforces the server's body size
    limit and raises RequestPayloadError for a body it cannot decode.
    """
    if isinstance(request, MockRequest):
        return await request.content.read()
    return await request.read()
