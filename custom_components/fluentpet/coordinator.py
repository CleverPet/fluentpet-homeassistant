"""Coordinator of the FluentPet Connect integration."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    OAuth2TokenRequestBaseError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HOUSEHOLD_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FEED_MAX_PAGES,
    FEED_PAGE_LIMIT,
    ISSUE_NOT_ADMIN,
    LOGGER,
    MANUFACTURER,
    MAX_BACKOFF,
    MODEL_BUTTON,
    RESOURCE_POLL_INTERVAL,
    SAFETY_POLL_INTERVAL,
    STORAGE_VERSION,
    STORE_SAVE_DELAY,
)
from .events import FluentPetEventProcessor
from .fluentpet_api import (
    LIFECYCLE_EVENT_TYPES,
    Base,
    Button,
    Event,
    FluentPetAuthError,
    FluentPetClient,
    FluentPetError,
    FluentPetForbiddenError,
    FluentPetParseError,
    FluentPetRateLimitError,
    FluentPetRequestError,
    FluentPetServerError,
    InteractionUpdatedData,
    Learner,
    PressCreatedData,
)
from .retry import SlowRetry

if TYPE_CHECKING:
    from .webhook import FluentPetWebhookManager


@dataclass(frozen=True, slots=True)
class FluentPetHouseholdData:
    """The resources of a household."""

    bases: dict[str, Base]
    buttons: dict[str, Button]
    learners: dict[str, Learner]


@dataclass(frozen=True, slots=True)
class _ResourceRead:
    """The resources read by a refresh, committed when the refresh succeeds.

    Attributes:
        bases_complete: No Base was skipped as malformed.
        buttons_complete: No button was skipped, in this read or, when the
            buttons were not read, in the read they come from.
        buttons_read: The buttons were read by this refresh.
    """

    data: FluentPetHouseholdData
    bases_complete: bool
    buttons_complete: bool
    buttons_read: bool


@dataclass(slots=True)
class FluentPetData:
    """Runtime data of a config entry."""

    client: FluentPetClient
    coordinator: FluentPetCoordinator
    webhook: FluentPetWebhookManager


type FluentPetConfigEntry = ConfigEntry[FluentPetData]


def not_admin_issue_id(entry_id: str) -> str:
    """Return the repair issue id of an entry whose account lost admin access."""
    return f"{ISSUE_NOT_ADMIN}_{entry_id}"


def base_device_name(base: Base) -> str:
    """Return the device name of a Base."""
    return base.name or f"Base {base.serial}"


class FluentPetCoordinator(DataUpdateCoordinator[FluentPetHouseholdData]):
    """Refreshes household resources and reads the event feed.

    Without a healthy webhook (see webhook_healthy), every refresh reads the feed,
    then the Bases, buttons and learners, with the refresh interval. While the
    webhook is healthy, a safety poll every SAFETY_POLL_INTERVAL reads the feed
    and the Bases (for their last seen time); the buttons and learners are read
    too after a lifecycle event (base, button or pusher), after a feed press of
    an unknown button, when a Base reports another firmware version (in the
    same poll), at a safety poll RESOURCE_POLL_INTERVAL after their last read
    (a button's firmware version can change without an event), once when the
    webhook becomes healthy, after a failed
    refresh, when the feed had to restart at its newest event, and on request.
    Pushed presses and interactions move the last seen time of their Base
    forward in between.

    The feed is read on every refresh, so the stored cursor stays current and
    events missed by the webhook (for example while Home Assistant was down) are
    caught up. The ids of recently processed events are stored with the cursor,
    so an event delivered by both, or read again after a restart, fires once.

    catching_up is True from setup (or reload) until a refresh first reads the
    feed to its end (a page shorter than FEED_PAGE_LIMIT), and again after a
    failed refresh or while the feed is not readable (403), until the next
    refresh that reads it to its end. Presses read from the feed
    while catching up are marked replayed; presses the webhook delivers, and
    feed presses read at other times (a safety poll or polling refresh picking
    up a missed delivery), are live.
    """

    config_entry: FluentPetConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: FluentPetConfigEntry, client: FluentPetClient
    ) -> None:
        """Initialize the coordinator."""
        self._base_interval = timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self._base_interval,
            always_update=False,
        )
        self.client = client
        self.household_id: str = entry.data[CONF_HOUSEHOLD_ID]
        self.processor = FluentPetEventProcessor(hass, entry.entry_id, self)
        self.webhook: FluentPetWebhookManager | None = None
        self.store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.cursor: str | None = None
        # Ids the household has, kept through refreshes that skipped an item.
        self.known_button_ids: set[str] = set()
        self.known_base_ids: set[str] = set()
        self.refresh_count = 0
        self._store_loaded = False
        self._feed_forbidden = False
        self._feed_retry = SlowRetry()
        # Whether the next refresh reads the resources also while push is active,
        # and whether the running refresh reads them.
        self._full_refresh = True
        self._refresh_is_full = False
        self._backing_off = False
        # Whether the buttons last committed were complete.
        self._buttons_complete = True
        # When the buttons and learners were last read.
        self._resources_read_at: datetime | None = None
        # Last seen times pushed events set, per Base id, until a Bases read
        # catches up (see _async_apply_pushed_seen).
        self._pushed_seen: dict[str, datetime] = {}
        # See catching_up; whether the running refresh read the feed to its end.
        self.catching_up = True
        self._feed_at_tail = False
        # Events and the position read by the running refresh, applied when it
        # succeeds (see _async_refresh_finished).
        self._feed_batch: list[Event] = []
        self._feed_cursor: str | None = None
        # The resources of the running refresh and whether the lists were
        # complete; committed to the known ids and the device registry only when
        # the refresh succeeds.
        self._resources: _ResourceRead | None = None

    @override
    async def _async_setup(self) -> None:
        """Load the feed cursor, or start at the tail of the feed."""
        usable, repaired = self._async_load_store(await self.store.async_load())
        if usable:
            self._store_loaded = True
            if repaired:
                await self.store.async_save(self._store_data())
            return
        try:
            await self._async_start_at_tail()
        except (FluentPetAuthError, OAuth2TokenRequestReauthError) as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="authentication_failed"
            ) from err
        except FluentPetForbiddenError:
            self._async_feed_forbidden()
        except (FluentPetError, OAuth2TokenRequestBaseError) as err:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="update_error",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _async_start_at_tail(self) -> None:
        """Start reading the feed after its newest event, and store that position."""
        self.cursor = await self.client.async_get_tail_cursor(self.household_id)
        self._store_loaded = True
        await self.store.async_save(self._store_data())

    @callback
    def _async_load_store(self, stored: object) -> tuple[bool, bool]:
        """Load the stored feed position and event ids.

        Returns whether the position is usable and whether invalid parts were dropped.

        Parts of the wrong shape (a corrupted or hand-edited file) are discarded with
        a warning; without a usable position the feed starts at its newest event.
        """
        if stored is None:
            return False, False
        if not isinstance(stored, dict):
            LOGGER.warning(
                "Ignoring the stored FluentPet feed state of %s, which is not valid;"
                " reading on from the newest event",
                self.config_entry.title,
            )
            return False, True
        repaired = False
        event_ids = stored.get("event_ids", [])
        if not isinstance(event_ids, list) or not all(
            isinstance(event_id, str) for event_id in event_ids
        ):
            LOGGER.warning(
                "Ignoring invalid stored FluentPet event ids of %s",
                self.config_entry.title,
            )
            event_ids = (
                [event_id for event_id in event_ids if isinstance(event_id, str)]
                if isinstance(event_ids, list)
                else []
            )
            repaired = True
        self.processor.seen_events.load(event_ids)
        cursor = stored.get("cursor", ...)
        if cursor is not None and not isinstance(cursor, str):
            LOGGER.warning(
                "Ignoring the stored FluentPet feed position of %s, which is not"
                " valid; reading on from the newest event",
                self.config_entry.title,
            )
            return False, True
        self.cursor = cursor
        return True, repaired

    @callback
    def _store_data(self) -> dict[str, Any]:
        """Return the cursor and the recently processed event ids to store.

        Ids of updates an entity still holds are left out: they were not applied,
        so after a restart the feed delivers them again.
        """
        held = self.processor.held_events
        return {
            "cursor": self.cursor,
            "event_ids": [
                event_id
                for event_id in self.processor.seen_events
                if event_id not in held
            ],
        }

    @callback
    def async_schedule_save(self) -> None:
        """Store the cursor and processed event ids soon."""
        if self._store_loaded:
            self.store.async_delay_save(self._store_data, STORE_SAVE_DELAY)

    @override
    async def async_shutdown(self) -> None:
        """Store the processed event ids before stopping."""
        await super().async_shutdown()
        if self._store_loaded:
            await self.store.async_save(self._store_data())

    @property
    def push_active(self) -> bool:
        """Return whether push is active, because the webhook is healthy."""
        return self.webhook is not None and self.webhook.healthy

    @property
    def _mode_interval(self) -> timedelta:
        """Return the refresh interval of the current mode, without backoff."""
        return SAFETY_POLL_INTERVAL if self.push_active else self._base_interval

    @override
    async def async_request_refresh(self) -> None:
        """Request a refresh that also reads the buttons and learners.

        Requested after a pushed lifecycle event or a press of an unknown button,
        or by the user (homeassistant.update_entity), so everything is read also
        while push is active. A request during the debounce cooldown keeps the
        flag until the refresh runs.
        """
        self._full_refresh = True
        await super().async_request_refresh()

    @callback
    def async_webhook_health_changed(self) -> None:
        """Switch between the safety poll and the refresh interval.

        Called by the webhook manager. When the webhook becomes healthy, the next
        refresh still reads the resources once. A running backoff is kept.
        """
        if self.push_active:
            LOGGER.debug("Webhook healthy; switching to the safety poll")
            # A running full refresh covers it: its feed read follows the check.
            if not self._refresh_is_full:
                self._full_refresh = True
        else:
            LOGGER.debug("Webhook unavailable; refreshing with the refresh interval")
        self._async_apply_interval()
        # DataUpdateCoordinator clears _unsub_refresh while a refresh runs and
        # schedules the next one when it finishes; _schedule_refresh cancels the
        # pending timer. So only a waiting coordinator is rescheduled here.
        if self._unsub_refresh is not None:
            self._schedule_refresh()

    @callback
    def _async_apply_interval(self) -> None:
        """Set the interval of the current mode, or keep a longer backoff."""
        interval = self._mode_interval
        if self._backing_off and self.update_interval is not None:
            interval = max(interval, self.update_interval)
        self.update_interval = interval

    @override
    async def _async_update_data(self) -> FluentPetHouseholdData:
        """Fetch new feed events, then the household resources."""
        self._feed_batch = []
        self._feed_cursor = self.cursor
        self._feed_at_tail = False
        self._resources = None
        if self.webhook is not None:
            # A delivery window may have expired since the last evaluation.
            self.webhook.async_update_health()
        self._refresh_is_full = self._full_refresh or not self.push_active
        self._full_refresh = False
        try:
            # The check and the feed come first, so the resources read after
            # them are at least as new as every event of the batch.
            if self.webhook is not None:
                await self.webhook.async_check()
            reset = await self._async_poll_feed()
            if (
                self._refresh_is_full
                or reset
                or self._feed_needs_resources()
                or self._resources_due()
            ):
                # Also in a safety poll whose feed restarted at its newest event
                # (which may skip lifecycle events), or has a lifecycle event the
                # webhook did not deliver or a press of an unknown button: once,
                # however many events.
                resources = await self._async_fetch_resources()
            else:
                # The safety poll: the Bases, for their last seen time. Buttons
                # and learners follow their events.
                resources = await self._async_fetch_bases()
                if self._firmware_changed(resources.data.bases):
                    # A release table update at FluentPet renames firmware
                    # without an event; the buttons take theirs from the same
                    # table, so they are read too.
                    resources = await self._async_fetch_resources(resources)
        except (FluentPetAuthError, OAuth2TokenRequestReauthError) as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="authentication_failed"
            ) from err
        except (FluentPetRateLimitError, FluentPetServerError) as err:
            self._async_back_off()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_error",
                translation_placeholders={"error": str(err)},
                retry_after=err.retry_after
                if isinstance(err, FluentPetRateLimitError)
                else None,
            ) from err
        except (FluentPetError, OAuth2TokenRequestBaseError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_error",
                translation_placeholders={"error": str(err)},
            ) from err
        self._backing_off = False
        self._async_apply_interval()
        # No await from here: pushed last seen times that arrived during the
        # reads are applied to the data this refresh returns and commits.
        # Committed in _async_refresh_finished once the refresh succeeded; a
        # failed refresh keeps the previous data, known ids and devices.
        self._resources = self._async_apply_pushed_seen(resources)
        return self._resources.data

    @callback
    def _feed_needs_resources(self) -> bool:
        """Return whether the feed read needs the buttons and learners read.

        True for a lifecycle event not processed yet, and for a new press of a
        button Home Assistant does not know.
        """
        processor = self.processor
        for event in self._feed_batch:
            if event.id in processor.seen_events or event.id in processor.held_events:
                continue
            if event.type in LIFECYCLE_EVENT_TYPES:
                return True
            data = event.data
            if (
                isinstance(data, PressCreatedData)
                and data.button is not None
                and data.button.id not in self.known_button_ids
            ):
                return True
        return False

    @callback
    def _resources_due(self) -> bool:
        """Return whether the buttons and learners were last read too long ago.

        Half a safety poll early counts: the poll that falls due an hour after
        the last read may start a moment before the hour is complete.
        """
        return (
            self._resources_read_at is None
            or dt_util.utcnow() - self._resources_read_at
            >= RESOURCE_POLL_INTERVAL - SAFETY_POLL_INTERVAL / 2
        )

    @callback
    def _firmware_changed(self, bases: dict[str, Base]) -> bool:
        """Return whether a known Base reports another firmware version."""
        known = self.data.bases
        return any(
            base.id in known
            and base.firmware_version != known[base.id].firmware_version
            for base in bases.values()
        )

    @callback
    def _async_apply_pushed_seen(self, resources: _ResourceRead) -> _ResourceRead:
        """Apply the last seen times pushed events set to the Bases read.

        A pushed time is kept while it is newer than the time FluentPet returns,
        also when FluentPet returns none, and forgotten once FluentPet catches up
        or the Base is gone.
        """
        pushed = self._pushed_seen
        if not pushed:
            return resources
        bases = dict(resources.data.bases)
        for base_id, seen in list(pushed.items()):
            if (base := bases.get(base_id)) is None:
                if resources.bases_complete:
                    del pushed[base_id]
                continue
            if base.last_online_at is not None and base.last_online_at >= seen:
                del pushed[base_id]
                continue
            bases[base_id] = replace(base, last_online_at=seen)
        return replace(resources, data=replace(resources.data, bases=bases))

    async def _async_fetch_resources(
        self, bases_read: _ResourceRead | None = None
    ) -> _ResourceRead:
        """Fetch the Bases, buttons and learners of the household.

        With bases_read, the Bases of that read are kept and only the buttons and
        learners are fetched.
        """
        # Counts button reads, so presses of unknown buttons wait for one that
        # started after they arrived (see async_drop_stale_pending).
        self.refresh_count += 1
        self._resources_read_at = dt_util.utcnow()
        skipped: list[str] = []

        def _skip(kind: str) -> Callable[[FluentPetParseError], None]:
            def _on_invalid(err: FluentPetParseError) -> None:
                LOGGER.warning("Skipped a malformed FluentPet %s: %s", kind, err)
                skipped.append(kind)

            return _on_invalid

        household_id = self.household_id
        if bases_read is None:
            bases = {
                base.id: base
                for base in await self.client.async_get_bases(
                    household_id, on_invalid=_skip("Base")
                )
            }
            bases_complete = True
        else:
            bases = bases_read.data.bases
            bases_complete = bases_read.bases_complete
        data = FluentPetHouseholdData(
            bases=bases,
            buttons={
                button.id: button
                for button in await self.client.async_get_buttons(
                    household_id, on_invalid=_skip("button")
                )
            },
            learners={
                learner.id: learner
                for learner in await self.client.async_get_learners(
                    household_id, on_invalid=_skip("learner")
                )
            },
        )
        complete = bases_complete and not skipped
        return _ResourceRead(
            data,
            bases_complete=complete,
            buttons_complete=complete,
            buttons_read=True,
        )

    async def _async_fetch_bases(self) -> _ResourceRead:
        """Fetch the Bases; keep the buttons and learners of the last refresh."""
        skipped = False

        def _on_invalid(err: FluentPetParseError) -> None:
            nonlocal skipped
            LOGGER.warning("Skipped a malformed FluentPet Base: %s", err)
            skipped = True

        bases = {
            base.id: base
            for base in await self.client.async_get_bases(
                self.household_id, on_invalid=_on_invalid
            )
        }
        # The buttons and learners as committed now, after the await.
        return _ResourceRead(
            replace(self.data, bases=bases),
            bases_complete=not skipped,
            buttons_complete=self._buttons_complete,
            buttons_read=False,
        )

    @callback
    def async_base_seen(self, event: Event) -> None:
        """Move the last seen time of the Base of a pushed event forward.

        A press or interaction delivered by the webhook shows that its Base was
        online when FluentPet stored the event. No request is made; the time
        only moves forward.
        """
        data = event.data
        if (
            not isinstance(data, PressCreatedData | InteractionUpdatedData)
            or data.base is None
            or (base := self.data.bases.get(data.base.id)) is None
            or (
                base.last_online_at is not None
                and base.last_online_at >= event.created_at
            )
            or (
                (pushed := self._pushed_seen.get(base.id)) is not None
                and pushed >= event.created_at
            )
        ):
            return
        # Kept for the refresh that may be reading the Bases right now.
        self._pushed_seen[base.id] = event.created_at
        bases = {
            **self.data.bases,
            base.id: replace(base, last_online_at=event.created_at),
        }
        # Not async_set_updated_data, which would also move the next safety poll.
        self.data = replace(self.data, bases=bases)
        self.async_update_listeners()

    @callback
    def _async_back_off(self) -> None:
        """Double the refresh interval, up to MAX_BACKOFF."""
        mode_interval = self._mode_interval
        current = self.update_interval or mode_interval
        self._backing_off = True
        self.update_interval = max(mode_interval, min(current * 2, MAX_BACKOFF))

    @callback
    def _async_feed_forbidden(self) -> None:
        """Pause reading the feed, which only the household admin may read.

        Tried again on a slow schedule, in case admin access returns.
        """
        if not self._feed_forbidden:
            LOGGER.warning(
                "The FluentPet event feed of household %s is not readable; only the"
                " household admin receives button presses",
                self.config_entry.title,
            )
        self._feed_forbidden = True
        # The only feed failure a refresh survives; the backlog read once the
        # feed is readable again is a catch-up.
        self.catching_up = True
        self._feed_retry.schedule(dt_util.utcnow())
        self.async_update_not_admin_issue()

    @callback
    def _async_feed_readable(self) -> None:
        """Resume normal reading after the feed was forbidden."""
        if not self._feed_forbidden:
            return
        LOGGER.info(
            "The FluentPet event feed of household %s is readable again",
            self.config_entry.title,
        )
        self._feed_forbidden = False
        self._feed_retry.reset()
        self.async_update_not_admin_issue()

    @callback
    def async_update_not_admin_issue(self) -> None:
        """Raise or clear the repair issue for an account without admin access."""
        issue_id = not_admin_issue_id(self.config_entry.entry_id)
        if self._feed_forbidden or (
            self.webhook is not None and self.webhook.forbidden
        ):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_NOT_ADMIN,
                translation_placeholders={"household": self.config_entry.title},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @callback
    def _async_invalid_event(self, err: FluentPetParseError) -> None:
        """Log an event of the feed that is skipped because it is malformed."""
        LOGGER.warning("Skipped a malformed FluentPet event: %s", err)

    async def _async_poll_feed(self) -> bool:
        """Read the feed from the cursor, page by page.

        Returns whether the position was reset to the newest event, so events
        since the last read may be missed.

        The events and the new position are kept in _feed_batch and _feed_cursor
        and applied only when the whole refresh succeeds, so entities are
        available when they fire, and a refresh that fails part way does not move
        the position past events it did not apply.
        """
        if self._feed_forbidden and not self._feed_retry.due(dt_util.utcnow()):
            return False
        reset = recovered = False
        if not self._store_loaded:
            # The tail of the feed was not readable at setup.
            try:
                await self._async_start_at_tail()
            except FluentPetForbiddenError:
                self._async_feed_forbidden()
                return False
            reset = True
        cursor = self._feed_cursor = self.cursor
        for _ in range(FEED_MAX_PAGES):
            try:
                page = await self.client.async_get_events(
                    self.household_id,
                    since=cursor,
                    limit=FEED_PAGE_LIMIT,
                    on_invalid=self._async_invalid_event,
                )
            except FluentPetForbiddenError:
                self._async_feed_forbidden()
                return reset
            except FluentPetRequestError:
                # FluentPet answers 400 for a position it did not issue for this
                # household (a corrupted store, another environment). Start over at
                # the newest event once; a second refusal fails the refresh.
                if cursor is None or recovered:
                    raise
                LOGGER.warning(
                    "FluentPet refused the stored event feed position of %s; reading"
                    " on from the newest event, so events since the last refresh"
                    " may be missed",
                    self.config_entry.title,
                )
                reset = recovered = True
                cursor = self._feed_cursor = await self.client.async_get_tail_cursor(
                    self.household_id
                )
                continue
            self._async_feed_readable()
            self._feed_batch.extend(page.events)
            if page.next_cursor is not None:
                cursor = self._feed_cursor = page.next_cursor
            if len(page.events) < FEED_PAGE_LIMIT:
                self._feed_at_tail = True
                return reset
        return reset

    @override
    @callback
    def _async_refresh_finished(self) -> None:
        """Apply a successful refresh, then drop stale presses.

        Runs before the listeners are updated. The known ids and devices are
        committed first, so the platforms add new items and drop departed ones in
        the same update; then the feed events are processed.
        """
        batch, self._feed_batch = self._feed_batch, []
        resources, self._resources = self._resources, None
        self._refresh_is_full = False
        if not self.last_update_success:
            # Read everything on the next refresh, also what a failed safety
            # poll would have read after a feed restart.
            self._full_refresh = True
            self.catching_up = True
            return
        if resources is not None:
            self._async_commit_resources(resources)
        if batch:
            # Write the available state first, so a state trigger that
            # excludes changes from unavailable sees the presses.
            self.async_update_listeners()
        for event in batch:
            # This refresh read the resources after the feed, and all of them
            # after a lifecycle event (see _async_update_data), so the event
            # requests no other refresh.
            self.processor.async_process(
                event, refresh=False, replayed=self.catching_up
            )
        if self._feed_at_tail:
            self.catching_up = False
        if self._feed_cursor != self.cursor:
            self.cursor = self._feed_cursor
            self.config_entry.async_create_task(
                self.hass,
                self.store.async_save(self._store_data()),
                "fluentpet store feed position",
            )
        if resources is not None and resources.buttons_read:
            self.processor.async_drop_stale_pending()

    @callback
    def _async_commit_resources(self, resources: _ResourceRead) -> None:
        """Record the ids the household has and update the devices.

        With a skipped malformed item the lists are incomplete: the known ids are
        kept and no device is removed because of one malformed item.
        """
        data = resources.data
        self.known_button_ids = set(data.buttons) | (
            set() if resources.buttons_complete else self.known_button_ids
        )
        self.known_base_ids = set(data.bases) | (
            set() if resources.bases_complete else self.known_base_ids
        )
        self._buttons_complete = resources.buttons_complete
        self._async_update_devices(
            data,
            remove_stale=resources.bases_complete and resources.buttons_complete,
        )

    @callback
    def _async_update_devices(
        self, data: FluentPetHouseholdData, *, remove_stale: bool = True
    ) -> None:
        """Register the hub, Base and button devices and remove stale ones."""
        entry = self.config_entry
        device_registry = dr.async_get(self.hass)
        hub = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, self.household_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model="Household",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        base_device_ids: dict[str, str] = {}
        for base in data.bases.values():
            base_device_ids[base.id] = device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, base.id)},
                name=base_device_name(base),
                manufacturer=MANUFACTURER,
                model=base.model,
                serial_number=base.serial,
                sw_version=base.firmware_version,
                via_device_id=hub.id,
            ).id
        for button in data.buttons.values():
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, button.id)},
                name=button.name,
                manufacturer=MANUFACTURER,
                model=MODEL_BUTTON,
                serial_number=button.serial,
                sw_version=button.firmware_version,
                via_device_id=base_device_ids.get(button.base_id or "", hub.id),
            )

        if not remove_stale:
            return
        current = self.current_ids(data)
        for device in dr.async_entries_for_config_entry(
            device_registry, entry.entry_id
        ):
            if not any(
                domain == DOMAIN and identifier in current
                for domain, identifier in device.identifiers
            ):
                LOGGER.debug("Removing stale device %s", device.name)
                device_registry.async_remove_device(device.id)

    def current_ids(self, data: FluentPetHouseholdData | None = None) -> set[str]:
        """Return the device identifiers of the household, its Bases and buttons."""
        data = data or self.data
        return {self.household_id, *data.bases, *data.buttons}
