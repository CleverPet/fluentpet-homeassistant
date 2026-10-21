"""Fixtures for FluentPet Connect tests."""

from collections.abc import Callable, Generator
import enum
import sys
import time
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.fluentpet.const import CONF_HOUSEHOLD_ID, CONF_WEBHOOK_ID, DOMAIN
from custom_components.fluentpet.fluentpet_api import (
    Base,
    Button,
    EventPage,
    Household,
    Learner,
    Webhook,
    WebhookWithSecret,
)
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .common import load_json_fixture

CLIENT_ID = "mock-client-id"
HOUSEHOLD_ID = "hh_testhouseholdaaaaaaaaaaaaa"
OTHER_HOUSEHOLD_ID = "hh_testhouseholdotheraaaaaaaa"
WEBHOOK_ID = "test-webhook-id"
TAIL_CURSOR = "AAAAAAAAAAtestcursor00000013"
EXTERNAL_URL = "https://hooks.example.com"
WEBHOOK_URL = f"{EXTERNAL_URL}/api/webhook/{WEBHOOK_ID}"


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Store snapshots in tests/snapshots with the Home Assistant serializer.

    syrupy and pytest-homeassistant-custom-component both register a plugin
    fixture named ``snapshot``; the one registered last wins. That order follows
    the filesystem order of site-packages, so on some machines syrupy's plain
    Amber extension wins and looks in tests/__snapshots__ instead. A conftest
    fixture always overrides plugin fixtures.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in every test."""


@pytest.fixture(autouse=True)
def no_shipped_client_id() -> Generator[None]:
    """Keep the shipped public client id out of tests; they use a mock one."""
    with patch("custom_components.fluentpet.application_credentials.CLIENT_ID", ""):
        yield


@pytest.fixture(autouse=True)
async def setup_credentials(
    hass: HomeAssistant, request: pytest.FixtureRequest
) -> None:
    """Import the application credential, unless the test is a fresh install."""
    if request.node.get_closest_marker("fresh_install"):
        return
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential(CLIENT_ID, ""), DOMAIN
    )


def mock_token(**overrides: Any) -> dict[str, Any]:
    """Return a fake OAuth token."""
    return {
        "access_token": "mock-access-token",
        "refresh_token": "mock-refresh-token",
        "id_token": "mock-id-token",
        "token_type": "Bearer",
        "expires_in": 86400,
        "expires_at": time.time() + 86400,
        **overrides,
    }


@pytest.fixture
def entry_data() -> dict[str, Any]:
    """Return the data of the config entry."""
    return {
        "auth_implementation": DOMAIN,
        "token": mock_token(),
        CONF_HOUSEHOLD_ID: HOUSEHOLD_ID,
        CONF_WEBHOOK_ID: WEBHOOK_ID,
    }


@pytest.fixture
def mock_config_entry(entry_data: dict[str, Any]) -> MockConfigEntry:
    """Return a FluentPet config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data=entry_data,
        entry_id="01J0FLUENTPETENTRY000000000",
    )


def households() -> list[Household]:
    """Return the households of the fixture."""
    return [
        Household.from_dict(item)
        for item in load_json_fixture("households.json")["data"]
    ]


def bases() -> list[Base]:
    """Return the Bases of the fixture."""
    return [Base.from_dict(item) for item in load_json_fixture("bases.json")["data"]]


def buttons() -> list[Button]:
    """Return the buttons of the fixture."""
    return [
        Button.from_dict(item) for item in load_json_fixture("buttons.json")["data"]
    ]


def learners() -> list[Learner]:
    """Return the learners of the fixture."""
    return [
        Learner.from_dict(item) for item in load_json_fixture("learners.json")["data"]
    ]


def webhook(**overrides: Any) -> Webhook:
    """Return the webhook of the fixture."""
    return Webhook.from_dict({**load_json_fixture("webhook.json"), **overrides})


def empty_page(
    household_id: str,
    since: str | None = None,
    types: Any = None,
    limit: int | None = None,
    **kwargs: Any,
) -> EventPage:
    """Return an empty feed page."""
    return EventPage(events=(), next_cursor=since)


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch the API client of the integration and the config flow."""
    with (
        patch(
            "custom_components.fluentpet.FluentPetClient", autospec=True
        ) as client_class,
        patch(
            "custom_components.fluentpet.config_flow.FluentPetClient",
            new=client_class,
        ),
    ):
        client = client_class.return_value
        client.async_get_households.return_value = households()
        client.async_get_bases.return_value = bases()
        client.async_get_buttons.return_value = buttons()
        client.async_get_learners.return_value = learners()
        client.async_get_tail_cursor.return_value = TAIL_CURSOR
        client.async_get_events.side_effect = empty_page
        client.async_get_webhook.return_value = webhook()
        client.async_create_webhook.return_value = WebhookWithSecret.from_dict(
            load_json_fixture("webhook_created.json")
        )
        client.async_update_webhook.return_value = webhook()
        client.async_delete_webhook.return_value = None
        yield client


@pytest.fixture
async def external_url(hass: HomeAssistant) -> str:
    """Give Home Assistant an external https URL."""
    await hass.config.async_update(external_url=EXTERNAL_URL)
    return EXTERNAL_URL


CLOUDHOOK_URL = "https://hooks.example.com/cloudhook/mock-cloudhook-id"


class CloudNotAvailable(Exception):
    """Fake cloud.CloudNotAvailable."""


class CloudNotConnected(CloudNotAvailable):
    """Fake cloud.CloudNotConnected."""


class CloudConnectionState(enum.Enum):
    """Fake cloud.CloudConnectionState."""

    CLOUD_CONNECTED = "cloud_connected"
    CLOUD_DISCONNECTED = "cloud_disconnected"


@pytest.fixture
def mock_cloud(hass: HomeAssistant) -> Generator[MagicMock]:
    """Provide a fake cloud integration with an active subscription."""
    module = types.ModuleType("homeassistant.components.cloud")
    listeners: list[Callable[[dict[str, Any] | None], None]] = []

    def _listen(
        hass: HomeAssistant,
        webhook_id: str,
        on_change: Callable[[dict[str, Any] | None], None],
    ) -> Callable[[], None]:
        listeners.append(on_change)
        return lambda: listeners.remove(on_change)

    connection_listeners: list[Callable[[CloudConnectionState], None]] = []

    def _listen_connection(
        hass: HomeAssistant, target: Callable[[CloudConnectionState], None]
    ) -> Callable[[], None]:
        connection_listeners.append(target)
        return lambda: connection_listeners.remove(target)

    fake = MagicMock()
    fake.CloudNotAvailable = CloudNotAvailable
    fake.CloudNotConnected = CloudNotConnected
    fake.CloudConnectionState = CloudConnectionState
    fake.async_active_subscription = MagicMock(return_value=True)
    fake.async_is_logged_in = MagicMock(return_value=True)
    fake.async_listen_connection_change = MagicMock(side_effect=_listen_connection)
    fake.connection_listeners = connection_listeners
    fake.async_get_or_create_cloudhook = AsyncMock(return_value=CLOUDHOOK_URL)
    fake.async_delete_cloudhook = AsyncMock(return_value=None)
    fake.async_listen_cloudhook_change = MagicMock(side_effect=_listen)
    fake.listeners = listeners
    for name in (
        "CloudNotAvailable",
        "CloudNotConnected",
        "CloudConnectionState",
        "async_active_subscription",
        "async_is_logged_in",
        "async_listen_connection_change",
        "async_get_or_create_cloudhook",
        "async_delete_cloudhook",
        "async_listen_cloudhook_change",
    ):
        setattr(module, name, getattr(fake, name))
    hass.config.components.add("cloud")
    with patch.dict(sys.modules, {"homeassistant.components.cloud": module}):
        yield fake
