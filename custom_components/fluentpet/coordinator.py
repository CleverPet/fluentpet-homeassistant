"""Coordinator of the FluentPet Connect integration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
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
    STORAGE_VERSION,
    STORE_SAVE_DELAY,
)
from .events import FluentPetEventProcessor
from .fluentpet_api import (
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
    Learner,
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
    """Refreshes household resources and reads the event feed every interval.

    The feed is read on every refresh, also while the webhook is active, so the
    stored cursor stays current and events missed by the webhook (for example while
    Home Assistant was down) are caught up. The webhook gives low latency; the ids of
    recently processed events are stored with the cursor, so an event delivered by
    both, or read again after a restart, fires once.
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
        # Events and the position read by the running refresh, applied when it
        # succeeds (see _async_refresh_finished).
        self._feed_batch: list[Event] = []
        self._feed_cursor: str | None = None
        # The resources of the running refresh and whether the lists were
        # complete; committed to the known ids and the device registry only when
        # the refresh succeeds.
        self._resources: tuple[FluentPetHouseholdData, bool] | None = None

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

    @override
    async def _async_update_data(self) -> FluentPetHouseholdData:
        """Fetch the household resources and new feed events."""
        self.refresh_count += 1
        self._feed_batch = []
        self._feed_cursor = self.cursor
        self._resources = None
        skipped: list[str] = []

        def _skip(kind: str) -> Callable[[FluentPetParseError], None]:
            def _on_invalid(err: FluentPetParseError) -> None:
                LOGGER.warning("Skipped a malformed FluentPet %s: %s", kind, err)
                skipped.append(kind)

            return _on_invalid

        household_id = self.household_id
        try:
            data = FluentPetHouseholdData(
                bases={
                    base.id: base
                    for base in await self.client.async_get_bases(
                        household_id, on_invalid=_skip("Base")
                    )
                },
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
            # Applied in _async_refresh_finished once the whole refresh succeeded:
            # a refresh that fails later (the feed, the webhook check) keeps the
            # previous data, so its known ids and devices must stay as well.
            self._resources = (data, not skipped)
            if self.webhook is not None:
                await self.webhook.async_check()
            await self._async_poll_feed()
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
        self.update_interval = self._base_interval
        return data

    @callback
    def _async_back_off(self) -> None:
        """Double the refresh interval, up to MAX_BACKOFF."""
        current = self.update_interval or self._base_interval
        self.update_interval = max(self._base_interval, min(current * 2, MAX_BACKOFF))

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

    async def _async_poll_feed(self) -> None:
        """Read the feed from the cursor, page by page.

        The events and the new position are kept in _feed_batch and _feed_cursor
        and applied only when the whole refresh succeeds, so entities are
        available when they fire, and a refresh that fails part way does not move
        the position past events it did not apply.
        """
        if self._feed_forbidden and not self._feed_retry.due(dt_util.utcnow()):
            return
        if not self._store_loaded:
            # The tail of the feed was not readable at setup.
            try:
                await self._async_start_at_tail()
            except FluentPetForbiddenError:
                self._async_feed_forbidden()
                return
        recovered = False
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
                return
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
                recovered = True
                cursor = self._feed_cursor = await self.client.async_get_tail_cursor(
                    self.household_id
                )
                continue
            self._async_feed_readable()
            self._feed_batch.extend(page.events)
            if page.next_cursor is not None:
                cursor = self._feed_cursor = page.next_cursor
            if len(page.events) < FEED_PAGE_LIMIT:
                return

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
        if self.last_update_success:
            if resources is not None:
                self._async_commit_resources(*resources)
            if batch:
                # Write the available state first, so a state trigger that
                # excludes changes from unavailable sees the presses.
                self.async_update_listeners()
            for event in batch:
                self.processor.async_process(event)
            if self._feed_cursor != self.cursor:
                self.cursor = self._feed_cursor
                self.config_entry.async_create_task(
                    self.hass,
                    self.store.async_save(self._store_data()),
                    "fluentpet store feed position",
                )
        self.processor.async_drop_stale_pending()

    @callback
    def _async_commit_resources(
        self, data: FluentPetHouseholdData, complete: bool
    ) -> None:
        """Record the ids the household has and update the devices.

        With a skipped malformed item the lists are incomplete: the known ids are
        kept and no device is removed because of one malformed item.
        """
        self.known_button_ids = set(data.buttons) | (
            set() if complete else self.known_button_ids
        )
        self.known_base_ids = set(data.bases) | (
            set() if complete else self.known_base_ids
        )
        self._async_update_devices(data, remove_stale=complete)

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
                sw_version=base.fw_version,
                via_device_id=hub.id,
            ).id
        for button in data.buttons.values():
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, button.id)},
                name=button.name,
                manufacturer=MANUFACTURER,
                model="Button",
                serial_number=button.serial,
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
