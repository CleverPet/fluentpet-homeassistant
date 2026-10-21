"""Tests for setting up and removing FluentPet Connect."""

from datetime import timedelta
import re
import time
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.fluentpet.api import StaticTokenAuth
from custom_components.fluentpet.const import (
    CONF_CLOUDHOOK,
    CONF_PREFER_CLOUDHOOK,
    CONF_SCAN_INTERVAL,
    CONF_WEBHOOK_ENDPOINT_ID,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL,
    DOMAIN,
    OAUTH2_TOKEN,
)
from custom_components.fluentpet.fluentpet_api import (
    DEFAULT_BASE_URL,
    FluentPetAuthError,
    FluentPetConnectionError,
    FluentPetForbiddenError,
    FluentPetNotFoundError,
)
from homeassistant.components.persistent_notification import (
    _async_get_or_create_notifications as async_get_persistent_notifications,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.setup import async_setup_component

from .common import SECRET, load_json_fixture, setup_integration
from .conftest import HOUSEHOLD_ID, TAIL_CURSOR, WEBHOOK_ID, WEBHOOK_URL, mock_token

API = f"{DEFAULT_BASE_URL}/api/v3/households/{HOUSEHOLD_ID}"
NOTIFICATION_ID = "fluentpet_webhook_not_deleted_"

ENDPOINT_ID = "00000000-0000-4000-8000-000000009001"


@pytest.fixture
def registered_entry_data(entry_data: dict[str, Any]) -> dict[str, Any]:
    """Return entry data with a registered FluentPet webhook."""
    return {
        **entry_data,
        CONF_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
        CONF_WEBHOOK_SECRET: SECRET,
        CONF_WEBHOOK_URL: WEBHOOK_URL,
        CONF_CLOUDHOOK: False,
    }


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """The entry sets up, reads the tail cursor once and unloads."""
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.async_get_tail_cursor.assert_awaited_once_with(HOUSEHOLD_ID)
    assert hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"]["data"] == {
        "cursor": TAIL_CURSOR,
        "event_ids": [],
    }

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.async_delete_webhook.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "error", "state"),
    [
        (
            "async_get_bases",
            FluentPetAuthError(401, "unauthorized", None),
            ConfigEntryState.SETUP_ERROR,
        ),
        (
            "async_get_bases",
            FluentPetConnectionError("boom"),
            ConfigEntryState.SETUP_RETRY,
        ),
        (
            "async_get_tail_cursor",
            FluentPetAuthError(401, "unauthorized", None),
            ConfigEntryState.SETUP_ERROR,
        ),
        (
            "async_get_tail_cursor",
            FluentPetConnectionError("boom"),
            ConfigEntryState.SETUP_RETRY,
        ),
    ],
)
async def test_setup_errors(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    method: str,
    error: Exception,
    state: ConfigEntryState,
) -> None:
    """API errors at setup map to reauth or retry."""
    getattr(mock_client, method).side_effect = error
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is state
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert bool(flows) is (state is ConfigEntryState.SETUP_ERROR)
    if flows:
        assert flows[0]["context"]["source"] == SOURCE_REAUTH


@pytest.mark.usefixtures("external_url")
async def test_setup_webhook_auth_error(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """An auth error while registering the webhook starts reauth."""
    mock_client.async_create_webhook.side_effect = FluentPetAuthError(
        401, "unauthorized", None
    )
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_tail_cursor_forbidden(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A user who may not read the feed still gets the Bases and buttons."""
    mock_client.async_get_tail_cursor.side_effect = FluentPetForbiddenError(
        403, "forbidden", None
    )
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.async_get_events.assert_not_awaited()
    assert "only the household admin receives button presses" in caplog.text


async def test_setup_imports_client_credential(hass: HomeAssistant) -> None:
    """A defined client id is imported as the application credential."""
    with (
        patch(
            "custom_components.fluentpet.application_credentials.CLIENT_ID",
            "mock-public-client-id",
        ),
        patch(
            "custom_components.fluentpet.application_credentials.async_import_client_credential"
        ) as mock_import,
    ):
        assert await async_setup_component(hass, DOMAIN, {})
    credential = mock_import.call_args.args[2]
    assert credential.client_id == "mock-public-client-id"
    assert credential.client_secret == ""


async def test_setup_without_client_id(hass: HomeAssistant) -> None:
    """Without a client id nothing is imported."""
    with patch(
        "custom_components.fluentpet.application_credentials.async_import_client_credential"
    ) as mock_import:
        assert await async_setup_component(hass, DOMAIN, {})
    mock_import.assert_not_called()


@pytest.mark.usefixtures("external_url")
async def test_remove_entry_deletes_webhook(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry_data: dict[str, Any],
    hass_storage: dict[str, Any],
) -> None:
    """Removing the entry deletes the webhook, the cloudhook and the cursor."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data=registered_entry_data,
    )
    await setup_integration(hass, entry)
    assert f"{DOMAIN}.{entry.entry_id}" in hass_storage

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_delete_webhook.assert_awaited_once_with(HOUSEHOLD_ID, ENDPOINT_ID)
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage


@pytest.mark.usefixtures("external_url")
@pytest.mark.parametrize(
    "error",
    [
        FluentPetNotFoundError(404, "not found", None),
        FluentPetConnectionError("boom"),
    ],
)
async def test_remove_entry_tolerates_errors(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry_data: dict[str, Any],
    error: Exception,
) -> None:
    """A missing webhook or an unreachable API does not block removal."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=HOUSEHOLD_ID, data=registered_entry_data
    )
    await setup_integration(hass, entry)
    mock_client.async_delete_webhook.side_effect = error
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_delete_webhook.assert_awaited_once()
    notifications = async_get_persistent_notifications(hass)
    notification_id = f"{NOTIFICATION_ID}{entry.entry_id}"
    if isinstance(error, FluentPetNotFoundError):
        assert notification_id not in notifications
    else:
        notification = notifications[notification_id]
        assert notification["title"] == "FluentPet Connect"
        assert "https://my.fluent.pet" in notification["message"]
        assert "{" not in notification["message"]


async def test_remove_entry_deletes_cloudhook(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    registered_entry_data: dict[str, Any],
) -> None:
    """A cloudhook of the entry is deleted with it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOUSEHOLD_ID,
        data={**registered_entry_data, CONF_CLOUDHOOK: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    mock_cloud.async_delete_cloudhook.assert_awaited_once_with(hass, WEBHOOK_ID)
    mock_client.async_delete_webhook.assert_awaited_once()


async def test_remove_entry_cloudhook_unavailable(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    registered_entry_data: dict[str, Any],
) -> None:
    """An unavailable cloud does not block removal."""
    mock_cloud.async_delete_cloudhook.side_effect = mock_cloud.CloudNotAvailable
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOUSEHOLD_ID,
        data={**registered_entry_data, CONF_CLOUDHOOK: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    mock_cloud.async_delete_cloudhook.assert_awaited_once()


async def test_remove_entry_without_credentials(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    registered_entry_data: dict[str, Any],
) -> None:
    """Removal works when the OAuth implementation is gone, and says what is left."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOUSEHOLD_ID,
        data={**registered_entry_data, "auth_implementation": "missing"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    mock_client.async_delete_webhook.assert_not_awaited()
    assert f"{NOTIFICATION_ID}{entry.entry_id}" in async_get_persistent_notifications(
        hass
    )


async def test_remove_device(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Only devices the household no longer has can be removed."""
    assert await async_setup_component(hass, "config", {})
    await setup_integration(hass, mock_config_entry)
    client = await hass_ws_client(hass)

    current = device_registry.async_get_device_by_identifier(
        (DOMAIN, "btn_testbuttonoutsideaaaaaaaaa"), mock_config_entry.entry_id
    )
    assert current is not None
    response = await client.remove_device(current.id)
    assert not response["success"]

    gone = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "btn_testbuttongoneaaaaaaaaaaaa")},
    )
    response = await client.remove_device(gone.id)
    assert response["success"]


async def test_remove_entry_deletes_leftover_cloudhook(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_cloud: MagicMock,
    registered_entry_data: dict[str, Any],
) -> None:
    """A cloudhook is deleted even when the webhook no longer used it."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=HOUSEHOLD_ID, data=registered_entry_data
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    mock_cloud.async_delete_cloudhook.assert_awaited_once_with(hass, WEBHOOK_ID)


async def test_remove_entry_deletes_issues(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Removing the entry deletes its repair issues."""
    mock_client.async_get_tail_cursor.side_effect = FluentPetForbiddenError(
        403, "not allowed", None
    )
    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(
        DOMAIN, f"not_admin_{mock_config_entry.entry_id}"
    )
    assert await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert not issue_registry.issues


def _expired_entry(entry_data: dict[str, Any]) -> MockConfigEntry:
    """Return an entry whose access token has expired."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data={**entry_data, "token": mock_token(expires_at=time.time() - 3600)},
    )


def _mock_api(aioclient_mock: AiohttpClientMocker) -> None:
    """Answer the household API calls of a refresh."""
    for name in ("bases", "buttons", "learners"):
        aioclient_mock.get(f"{API}/{name}", json=load_json_fixture(f"{name}.json"))
    aioclient_mock.get(f"{API}/events/cursor", json={"next_cursor": TAIL_CURSOR})
    aioclient_mock.get(
        re.compile(re.escape(f"{API}/events") + r"\?"),
        json={"data": [], "next_cursor": TAIL_CURSOR},
    )


async def test_refused_token_refresh_at_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry_data: dict[str, Any],
) -> None:
    """A refresh token the server refuses at setup starts reauth, not a retry."""
    entry = _expired_entry(entry_data)
    aioclient_mock.post(OAUTH2_TOKEN, status=400, json={"error": "invalid_grant"})
    await setup_integration(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [OAUTH2_TOKEN]


async def test_transient_token_refresh_error_at_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry_data: dict[str, Any],
) -> None:
    """A token endpoint that fails for the moment retries setup."""
    entry = _expired_entry(entry_data)
    aioclient_mock.post(OAUTH2_TOKEN, status=503)
    await setup_integration(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


@pytest.mark.parametrize(
    ("status", "reauth"), [(400, True), (503, False)], ids=["refused", "transient"]
)
async def test_token_refresh_error_at_runtime(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry_data: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    status: int,
    reauth: bool,
) -> None:
    """A refused refresh token stops refreshes and starts reauth; a transient one retries."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test Household", unique_id=HOUSEHOLD_ID, data=entry_data
    )
    _mock_api(aioclient_mock)
    await setup_integration(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data.coordinator

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "token": mock_token(expires_at=time.time() - 60)}
    )
    aioclient_mock.post(OAUTH2_TOKEN, status=status, json={"error": "invalid_grant"})
    calls = aioclient_mock.call_count
    for _ in range(2):
        freezer.tick(timedelta(seconds=61))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert not coordinator.last_update_success
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert bool(flows) is reauth
    # Each refresh asks only for a token; a refused one stops the refreshes.
    assert aioclient_mock.call_count - calls == (1 if reauth else 2)
    assert all(
        str(call[1]) == OAUTH2_TOKEN for call in aioclient_mock.mock_calls[calls:]
    )


@pytest.mark.parametrize(
    ("status", "deleted"), [(400, False), (200, True)], ids=["refused", "renewed"]
)
async def test_remove_entry_with_expired_token(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
    registered_entry_data: dict[str, Any],
    status: int,
    deleted: bool,
) -> None:
    """Removal renews an expired token without starting reauth for the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data={
            **registered_entry_data,
            "token": mock_token(expires_at=time.time() - 3600),
        },
    )
    entry.add_to_hass(hass)
    if status == 200:
        aioclient_mock.post(OAUTH2_TOKEN, json=mock_token(access_token="mock-new"))
    else:
        aioclient_mock.post(OAUTH2_TOKEN, status=400, json={"error": "invalid_grant"})
    with patch(
        "custom_components.fluentpet.StaticTokenAuth", wraps=StaticTokenAuth
    ) as auth:
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert (mock_client.async_delete_webhook.await_count == 1) is deleted
    if deleted:
        auth.assert_called_once_with(ANY, "mock-new", DEFAULT_BASE_URL)
    else:
        auth.assert_not_called()
    notifications = async_get_persistent_notifications(hass)
    assert (f"{NOTIFICATION_ID}{entry.entry_id}" in notifications) is not deleted


@pytest.mark.parametrize(
    ("options", "migrated"),
    [
        ({CONF_SCAN_INTERVAL: 10, CONF_PREFER_CLOUDHOOK: False}, 30),
        ({CONF_SCAN_INTERVAL: 29}, 30),
        ({CONF_SCAN_INTERVAL: 30}, 30),
        ({CONF_SCAN_INTERVAL: 45}, 45),
        ({}, None),
    ],
)
async def test_migrate_raises_refresh_interval(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    entry_data: dict[str, Any],
    options: dict[str, Any],
    migrated: int | None,
) -> None:
    """Entries of version 1.1 get a refresh interval of at least 30 seconds."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data=entry_data,
        options=options,
        version=1,
        minor_version=1,
    )
    await setup_integration(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 1
    assert entry.minor_version == 2
    assert entry.options.get(CONF_SCAN_INTERVAL) == migrated
    assert {
        key: value for key, value in entry.options.items() if key != CONF_SCAN_INTERVAL
    } == {key: value for key, value in options.items() if key != CONF_SCAN_INTERVAL}
    coordinator = entry.runtime_data.coordinator
    assert coordinator.update_interval == timedelta(seconds=migrated or 60)
