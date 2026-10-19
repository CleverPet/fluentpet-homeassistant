"""Fixtures for the FluentPet API client library tests."""

import asyncio
from collections.abc import AsyncGenerator

from aiohttp import ClientSession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fluentpet.fluentpet_api import AbstractAuth, FluentPetClient

ACCESS_TOKEN = "fake-access-token"
HOST = "https://example.com"


class FakeAuth(AbstractAuth):
    """Auth that returns a fixed fake token."""

    async def async_get_access_token(self) -> str:
        """Return the fake token."""
        return ACCESS_TOKEN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations() -> None:
    """Library tests need no Home Assistant instance; override the package fixture."""


@pytest.fixture(autouse=True)
def setup_credentials() -> None:
    """Library tests need no application credentials; override the package fixture."""


@pytest.fixture
def mocker_aiohttp() -> AiohttpClientMocker:
    """Return a fresh aiohttp request mocker."""
    return AiohttpClientMocker()


@pytest.fixture
async def session(
    mocker_aiohttp: AiohttpClientMocker,
) -> AsyncGenerator[ClientSession]:
    """Return a ClientSession whose requests go to the mocker."""
    websession = mocker_aiohttp.create_session(asyncio.get_running_loop())
    yield websession
    await websession.close()


@pytest.fixture
def auth(session: ClientSession) -> FakeAuth:
    """Return a FakeAuth on the mocked session."""
    return FakeAuth(session, HOST)


@pytest.fixture
def client(auth: FakeAuth) -> FluentPetClient:
    """Return a client on the fake auth."""
    return FluentPetClient(auth)
