"""Tests for the FluentPet Connect config flow."""

from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from yarl import URL

from custom_components.fluentpet.api import AsyncConfigEntryAuth, StaticTokenAuth
from custom_components.fluentpet.const import (
    AUDIENCE,
    CLIENT_NAME,
    CONF_API_BASE_URL,
    CONF_HOUSEHOLD_ID,
    CONF_PREFER_CLOUDHOOK,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    OAUTH2_AUTHORIZE,
    OAUTH2_TOKEN,
)
from custom_components.fluentpet.fluentpet_api import (
    DEFAULT_BASE_URL,
    FluentPetAuthError,
    FluentPetConnectionError,
    Household,
)
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.setup import async_setup_component

from .common import load_json_fixture, setup_integration
from .conftest import CLIENT_ID, HOUSEHOLD_ID, OTHER_HOUSEHOLD_ID, households

REDIRECT_URI = "https://example.com/auth/external/callback"

pytestmark = pytest.mark.usefixtures("current_request_with_host")


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Do not set the entry up."""
    with patch(
        "custom_components.fluentpet.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


async def _start(
    hass: HomeAssistant, api_base_url: str = DEFAULT_BASE_URL
) -> dict[str, Any]:
    """Start a user flow and submit the first form with an API base URL."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"advanced": {CONF_API_BASE_URL: api_base_url}}
    )


async def _authorize(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    result: dict[str, Any],
    *,
    check_url: bool = False,
    api_mocks: Callable[[AiohttpClientMocker], None] | None = None,
) -> dict[str, Any]:
    """Complete the external OAuth step and return the next flow result."""
    state = config_entry_oauth2_flow._encode_jwt(  # noqa: SLF001
        hass, {"flow_id": result["flow_id"], "redirect_uri": REDIRECT_URI}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    if check_url:
        url = URL(result["url"])
        assert str(url.with_query(None)) == OAUTH2_AUTHORIZE
        assert url.query["response_type"] == "code"
        assert url.query["client_id"] == CLIENT_ID
        assert url.query["redirect_uri"] == REDIRECT_URI
        assert url.query["state"] == state
        assert url.query["audience"] == AUDIENCE
        assert url.query["scope"] == "openid email offline_access"
        assert url.query["code_challenge_method"] == "S256"
        assert url.query["code_challenge"]

    client = await hass_client_no_auth()
    response = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert response.status == 200
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        OAUTH2_TOKEN,
        json={
            "access_token": "mock-new-access-token",
            "refresh_token": "mock-new-refresh-token",
            "token_type": "Bearer",
            "expires_in": 86400,
        },
    )
    if api_mocks is not None:
        api_mocks(aioclient_mock)
    return await hass.config_entries.flow.async_configure(result["flow_id"])


@pytest.mark.usefixtures("mock_setup_entry")
async def test_full_flow_one_household(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
) -> None:
    """One household creates the entry without a form."""
    mock_client.async_get_households.return_value = [
        Household(id=HOUSEHOLD_ID, name="Test Household", admin=True)
    ]
    result = await _start(hass)
    result = await _authorize(
        hass, hass_client_no_auth, aioclient_mock, result, check_url=True
    )

    token_request = aioclient_mock.mock_calls[-1][2]
    assert token_request["code"] == "abcd"
    assert token_request["code_verifier"]
    assert "client_secret" not in token_request

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Household"
    assert result["result"].unique_id == HOUSEHOLD_ID
    assert result["data"][CONF_HOUSEHOLD_ID] == HOUSEHOLD_ID
    assert result["data"]["auth_implementation"] == DOMAIN
    assert result["data"]["token"]["access_token"] == "mock-new-access-token"


TWO_ADMIN_HOUSEHOLDS = [
    Household(id=HOUSEHOLD_ID, name="Test Household", admin=True),
    Household(id=OTHER_HOUSEHOLD_ID, name="Test Household Two", admin=True),
]


@pytest.mark.usefixtures("mock_setup_entry")
async def test_flow_pick_household(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
) -> None:
    """Several households the account administers show a selection form."""
    mock_client.async_get_households.return_value = TWO_ADMIN_HOUSEHOLDS
    result = await _start(hass)
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "household"
    options = result["data_schema"].schema[CONF_HOUSEHOLD_ID].config["options"]
    assert [option["value"] for option in options] == [
        HOUSEHOLD_ID,
        OTHER_HOUSEHOLD_ID,
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOUSEHOLD_ID: HOUSEHOLD_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Household"
    assert result["result"].unique_id == HOUSEHOLD_ID


@pytest.mark.usefixtures("mock_setup_entry")
async def test_flow_skips_households_not_administered(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
) -> None:
    """Households the account does not administer are not offered."""
    result = await _start(hass)
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == HOUSEHOLD_ID


async def test_flow_not_admin(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
) -> None:
    """An account that administers none of its households aborts."""
    mock_client.async_get_households.return_value = [
        Household(id=HOUSEHOLD_ID, name="Test Household", admin=False),
        Household(id=OTHER_HOUSEHOLD_ID, name="Test Household Two", admin=False),
    ]
    result = await _start(hass)
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_admin"
    assert result["description_placeholders"] == {
        "name": "Test Household, Test Household Two"
    }


@pytest.mark.usefixtures("mock_setup_entry")
async def test_flow_skips_configured_households(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A configured household is not offered again."""
    mock_config_entry.add_to_hass(hass)
    mock_client.async_get_households.return_value = TWO_ADMIN_HOUSEHOLDS
    result = await _start(hass)
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == OTHER_HOUSEHOLD_ID


async def test_flow_duplicate(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An account whose households are all configured aborts."""
    mock_config_entry.add_to_hass(hass)
    result = await _start(hass)
    mock_client.async_get_households.return_value = households()[:1]
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_flow_household_configured_meanwhile(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A household configured while the form was open aborts."""
    mock_client.async_get_households.return_value = TWO_ADMIN_HOUSEHOLDS
    result = await _start(hass)
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOUSEHOLD_ID: HOUSEHOLD_ID}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_flow_no_households(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
) -> None:
    """An account without households aborts."""
    mock_client.async_get_households.return_value = []
    result = await _start(hass)
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_households"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (FluentPetConnectionError("boom"), "cannot_connect"),
        (FluentPetAuthError(401, "unauthorized", None), "oauth_unauthorized"),
    ],
)
async def test_flow_errors(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    error: Exception,
    reason: str,
) -> None:
    """Listing the households fails."""
    mock_client.async_get_households.side_effect = error
    result = await _start(hass)
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    # Shared OAuth reasons are translated by Home Assistant itself.
    expected_domain = "homeassistant" if reason == "oauth_unauthorized" else None
    assert result.get("translation_domain") == expected_domain


async def test_reauth(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauthentication updates the token and keeps the other data."""
    await setup_integration(hass, mock_config_entry)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data["token"]["access_token"] == "mock-new-access-token"
    assert mock_config_entry.data[CONF_HOUSEHOLD_ID] == HOUSEHOLD_ID


async def test_reauth_other_account(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauthentication with an account without the household aborts."""
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_households.return_value = [
        Household(id=OTHER_HOUSEHOLD_ID, name="Test Household Two", admin=True)
    ]
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert mock_config_entry.data["token"]["access_token"] == "mock-access-token"


async def test_options_flow(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The options flow saves the options and reloads the entry."""
    await setup_integration(hass, mock_config_entry)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 120.0,
            CONF_PREFER_CLOUDHOOK: False,
            "advanced": {CONF_API_BASE_URL: DEFAULT_BASE_URL},
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        CONF_SCAN_INTERVAL: 120,
        CONF_PREFER_CLOUDHOOK: False,
    }
    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.update_interval.total_seconds() == 120


@pytest.mark.parametrize("interval", [29, 3601])
async def test_options_flow_interval_bounds(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    interval: int,
) -> None:
    """The refresh interval is refused below 30 or above 3600 seconds."""
    await setup_integration(hass, mock_config_entry)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SCAN_INTERVAL: interval,
                CONF_PREFER_CLOUDHOOK: True,
                "advanced": {CONF_API_BASE_URL: DEFAULT_BASE_URL},
            },
        )
    assert mock_config_entry.options == {}


async def test_reauth_not_admin(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauthentication with an account that is no longer admin aborts."""
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_households.return_value = [
        Household(id=HOUSEHOLD_ID, name="Test Household", admin=False)
    ]
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_admin"
    assert mock_config_entry.data["token"]["access_token"] == "mock-access-token"


@pytest.fixture
def public_client_id() -> Generator[None]:
    """Ship the mock client id as the public client id of the integration."""
    with patch(
        "custom_components.fluentpet.application_credentials.CLIENT_ID", CLIENT_ID
    ):
        yield


async def _assert_credential_imported(hass: HomeAssistant) -> None:
    """Assert that the flow imported the public credential."""
    implementations = await config_entry_oauth2_flow.async_get_implementations(
        hass, DOMAIN
    )
    assert list(implementations) == [DOMAIN]
    implementation = implementations[DOMAIN]
    assert isinstance(
        implementation, config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce
    )
    assert implementation.client_id == CLIENT_ID
    assert implementation.name == CLIENT_NAME


USER_CLIENT_ID = "mock-user-client-id"
USER_AUTH_DOMAIN = f"{DOMAIN}_user"


async def _import_user_credential(hass: HomeAssistant) -> None:
    """Add a credential the user entered, with another client id."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass,
        DOMAIN,
        ClientCredential(USER_CLIENT_ID, "", name="Mine"),
        USER_AUTH_DOMAIN,
    )


@pytest.mark.fresh_install
@pytest.mark.usefixtures("mock_setup_entry", "public_client_id")
async def test_fresh_install_imports_credential(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
) -> None:
    """A first install signs in without the integration being set up before."""
    assert "application_credentials" not in hass.config.components
    mock_client.async_get_households.return_value = [
        Household(id=HOUSEHOLD_ID, name="Test Household", admin=True)
    ]
    result = await _start(hass)

    assert DOMAIN not in hass.config.components
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    await _assert_credential_imported(hass)
    result = await _authorize(
        hass, hass_client_no_auth, aioclient_mock, result, check_url=True
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["auth_implementation"] == DOMAIN


@pytest.mark.fresh_install
@pytest.mark.usefixtures("mock_setup_entry", "public_client_id")
async def test_reauth_imports_credential(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauthentication imports the public credential when it is missing.

    This guards the reauth entry point; users do not normally reach a
    reauthentication with the integration not set up.
    """
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    await _assert_credential_imported(hass)
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data["token"]["access_token"] == "mock-new-access-token"


@pytest.mark.fresh_install
@pytest.mark.usefixtures("mock_setup_entry", "public_client_id")
async def test_unavailable_provider_imports_credential(hass: HomeAssistant) -> None:
    """A provider that is down does not keep the public credential out."""

    async def _unavailable(
        hass: HomeAssistant, domain: str
    ) -> list[config_entry_oauth2_flow.AbstractOAuth2Implementation]:
        raise config_entry_oauth2_flow.ImplementationUnavailableError("cloud down")

    config_entry_oauth2_flow.async_add_implementation_provider(
        hass, "cloud", _unavailable
    )
    result = await _start(hass)

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    await _assert_credential_imported(hass)


@pytest.mark.fresh_install
@pytest.mark.usefixtures("mock_setup_entry", "public_client_id")
async def test_user_credential_skips_import(hass: HomeAssistant) -> None:
    """A credential of the user is used, and the public one is not added."""
    await _import_user_credential(hass)
    result = await _start(hass)

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert URL(result["url"]).query["client_id"] == USER_CLIENT_ID
    implementations = await config_entry_oauth2_flow.async_get_implementations(
        hass, DOMAIN
    )
    assert list(implementations) == [USER_AUTH_DOMAIN]


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reauth_keeps_implementation(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    entry_data: dict[str, Any],
) -> None:
    """Reauthentication signs in with the entry's credential, without a pick form."""
    await _import_user_credential(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data={**entry_data, "auth_implementation": USER_AUTH_DOMAIN},
    )
    entry.add_to_hass(hass)
    assert (
        len(await config_entry_oauth2_flow.async_get_implementations(hass, DOMAIN)) == 2
    )
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert URL(result["url"]).query["client_id"] == USER_CLIENT_ID
    result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data["auth_implementation"] == USER_AUTH_DOMAIN
    assert entry.data["token"]["access_token"] == "mock-new-access-token"


DEV_URL = "https://example.com/dev-api"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_api_base_url_default(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
) -> None:
    """The first form offers the production API in a collapsed section."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    advanced = result["data_schema"].schema["advanced"]
    assert advanced.options["collapsed"] is True
    (key,) = advanced.schema.schema
    assert key.description["suggested_value"] == DEFAULT_BASE_URL

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"advanced": {CONF_API_BASE_URL: DEFAULT_BASE_URL}}
    )
    with patch(
        "custom_components.fluentpet.config_flow.StaticTokenAuth",
        wraps=StaticTokenAuth,
    ) as auth:
        result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert auth.call_args.args[2] == DEFAULT_BASE_URL
    assert result["result"].options == {}


@pytest.mark.usefixtures("mock_setup_entry")
async def test_api_base_url_override(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Another API host lists the households from there and is stored."""
    result = await _start(hass, f" {DEV_URL}/ ")

    def _households(mocker: AiohttpClientMocker) -> None:
        mocker.get(
            f"{DEV_URL}/api/v3/households", json=load_json_fixture("households.json")
        )

    result = await _authorize(
        hass, hass_client_no_auth, aioclient_mock, result, api_mocks=_households
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {CONF_API_BASE_URL: DEV_URL}
    assert str(aioclient_mock.mock_calls[-1][1]) == f"{DEV_URL}/api/v3/households"


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "https://",
        "garbage",
        "https://user@example.com",
        "https://example.com/?debug=1",
        "https://example.com/#top",
        "https://[invalid",
    ],
)
async def test_api_base_url_invalid(hass: HomeAssistant, value: str) -> None:
    """An API base URL that is not https with a host is refused."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"advanced": {CONF_API_BASE_URL: value}}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_api_base_url"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"advanced": {CONF_API_BASE_URL: DEV_URL}}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP


async def test_reauth_keeps_api_base_url(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_client: AsyncMock,
    entry_data: dict[str, Any],
) -> None:
    """Reauthentication lists the households from the entry's API host."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        unique_id=HOUSEHOLD_ID,
        data=entry_data,
        options={CONF_API_BASE_URL: DEV_URL},
    )
    await setup_integration(hass, entry)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    with patch(
        "custom_components.fluentpet.config_flow.StaticTokenAuth",
        wraps=StaticTokenAuth,
    ) as auth:
        result = await _authorize(hass, hass_client_no_auth, aioclient_mock, result)
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert auth.call_args.args[2] == DEV_URL


async def test_options_api_base_url(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Another API host in the options reloads the entry against it."""
    await setup_integration(hass, mock_config_entry)

    async def _configure(url: str) -> dict[str, Any]:
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SCAN_INTERVAL: 30,
                CONF_PREFER_CLOUDHOOK: True,
                "advanced": {CONF_API_BASE_URL: url},
            },
        )
        await hass.async_block_till_done()
        return result

    with patch(
        "custom_components.fluentpet.AsyncConfigEntryAuth", wraps=AsyncConfigEntryAuth
    ) as auth:
        result = await _configure(f"{DEV_URL}/")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_API_BASE_URL] == DEV_URL
    assert auth.call_args.args[2] == DEV_URL

    result = await _configure("ftp://example.com")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_api_base_url"}

    await _configure(DEFAULT_BASE_URL)
    assert CONF_API_BASE_URL not in mock_config_entry.options
