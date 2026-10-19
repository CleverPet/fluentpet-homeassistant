"""Tests for the FluentPet Connect helper modules."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from custom_components.fluentpet import cloudhook
from custom_components.fluentpet.api import AsyncConfigEntryAuth, StaticTokenAuth
from custom_components.fluentpet.application_credentials import (
    async_get_auth_implementation,
    async_get_description_placeholders,
)
from custom_components.fluentpet.events import LruSet
from custom_components.fluentpet.fluentpet_api import DEFAULT_BASE_URL
from custom_components.fluentpet.retry import SlowRetry
from homeassistant.components.application_credentials import ClientCredential
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


def test_lru_set() -> None:
    """The set reports repeats and forgets the oldest keys."""
    keys = LruSet(size=2)
    assert keys.add("a")
    assert keys.add("b")
    assert not keys.add("a")
    assert keys.add("c")
    assert "b" not in keys
    assert "a" in keys
    assert len(keys) == 2


async def test_cloudhook_without_cloud(hass: HomeAssistant) -> None:
    """Without the cloud integration nothing is called."""
    assert not cloudhook.async_active_subscription(hass)
    assert not cloudhook.async_is_logged_in(hass)
    cloudhook.async_listen_connection_change(hass, lambda connected: None)()
    assert await cloudhook.async_get_cloudhook_url(hass, "test-webhook-id") is None
    await cloudhook.async_delete_cloudhook(hass, "test-webhook-id")
    unsubscribe = cloudhook.async_listen_cloudhook_change(
        hass, "test-webhook-id", lambda change: None
    )
    unsubscribe()


async def test_config_entry_auth(hass: HomeAssistant) -> None:
    """The entry auth refreshes the token before returning it."""
    oauth_session = MagicMock()
    oauth_session.async_ensure_token_valid = AsyncMock()
    oauth_session.token = {"access_token": "mock-access-token"}
    auth = AsyncConfigEntryAuth(MagicMock(), oauth_session)
    assert auth.host == DEFAULT_BASE_URL
    assert await auth.async_get_access_token() == "mock-access-token"
    oauth_session.async_ensure_token_valid.assert_awaited_once()


async def test_static_token_auth() -> None:
    """The flow auth returns its token."""
    auth = StaticTokenAuth(MagicMock(), "mock-access-token")
    assert await auth.async_get_access_token() == "mock-access-token"


async def test_description_placeholders(hass: HomeAssistant) -> None:
    """The credentials dialog links to the documentation."""
    assert await async_get_description_placeholders(hass) == {
        "more_info_url": "https://github.com/CleverPet/fluentpet-homeassistant"
    }


async def test_auth_implementation_never_sends_secret(hass: HomeAssistant) -> None:
    """A secret typed into the credentials form is not used with the public client."""
    implementation = await async_get_auth_implementation(
        hass, "fluentpet", ClientCredential("mock-client-id", "mock-dummy-secret")
    )
    assert implementation.client_id == "mock-client-id"
    assert implementation.client_secret == ""


def test_slow_retry() -> None:
    """The slow retry doubles from 5 minutes to an hour and resets."""
    now = dt_util.utcnow()
    retry = SlowRetry()
    assert not retry.due(now)
    retry.schedule(now)
    assert not retry.due(now + timedelta(minutes=4))
    assert retry.due(now + timedelta(minutes=5))
    for _ in range(6):
        retry.schedule(now)
    assert retry.next_at == now + timedelta(hours=1)
    retry.reset()
    assert retry.next_at is None
    retry.schedule(now)
    assert retry.next_at == now + timedelta(minutes=5)
