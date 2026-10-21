"""Config flow of the FluentPet Connect integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

import probatio

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN, UnitOfTime
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import StaticTokenAuth, api_base_url, normalize_api_base_url
from .const import (
    AUDIENCE,
    CONF_API_BASE_URL,
    CONF_HOUSEHOLD_ID,
    CONF_PREFER_CLOUDHOOK,
    CONF_SCAN_INTERVAL,
    DEFAULT_PREFER_CLOUDHOOK,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SCOPES,
)
from .coordinator import FluentPetConfigEntry
from .fluentpet_api import (
    DEFAULT_BASE_URL,
    FluentPetAuthError,
    FluentPetClient,
    FluentPetError,
    Household,
)


class FluentPetOAuth2FlowHandler(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Link a FluentPet household with OAuth2."""

    DOMAIN = DOMAIN
    VERSION = 1
    # 1.2 raised the minimum refresh interval to MIN_SCAN_INTERVAL.
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._api_base_url = DEFAULT_BASE_URL
        self._oauth_data: dict[str, Any] = {}
        self._households: dict[str, Household] = {}

    @property
    @override
    def logger(self) -> logging.Logger:
        """Return the logger of the OAuth2 flow."""
        return LOGGER

    @property
    @override
    def extra_authorize_data(self) -> dict[str, Any]:
        """Add the FluentPet API audience and scopes to the authorize URL."""
        return {"audience": AUDIENCE, "scope": " ".join(SCOPES)}

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: FluentPetConfigEntry,
    ) -> FluentPetOptionsFlow:
        """Return the options flow."""
        return FluentPetOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start the flow with a form whose collapsed section sets the API host.

        The household list is fetched from that host, so it is asked before the
        sign-in. Home Assistant no longer has an advanced mode to hide it behind
        (show_advanced_options is deprecated and always true), so it is a
        collapsed section, as Home Assistant recommends. Reauthentication keeps
        the host of the entry and skips the form.
        """
        if self.source == SOURCE_REAUTH:
            return await super().async_step_user(user_input)
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_api_base_url(
                user_input[SECTION_ADVANCED][CONF_API_BASE_URL]
            )
            if url is not None:
                self._api_base_url = url
                return await super().async_step_user()
            errors["base"] = "invalid_api_base_url"
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA,
                user_input
                or {SECTION_ADVANCED: {CONF_API_BASE_URL: self._api_base_url}},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the token was refused."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={"name": self._get_reauth_entry().title},
            )
        return await self.async_step_user()

    @override
    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """List the households of the account and pick one."""
        if self.source == SOURCE_REAUTH:
            self._api_base_url = api_base_url(self._get_reauth_entry().options)
        client = FluentPetClient(
            StaticTokenAuth(
                async_get_clientsession(self.hass),
                data[CONF_TOKEN][CONF_ACCESS_TOKEN],
                self._api_base_url,
            )
        )
        try:
            households = await client.async_get_households()
        except FluentPetAuthError:
            return self.async_abort(reason="oauth_unauthorized")
        except FluentPetError as err:
            LOGGER.debug("Could not list FluentPet households: %s", err)
            return self.async_abort(reason="cannot_connect")
        self._oauth_data = data
        self._households = {household.id: household for household in households}

        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if entry.unique_id not in self._households:
                return self.async_abort(reason="unique_id_mismatch")
            if not (household := self._households[entry.unique_id]).admin:
                return self.async_abort(
                    reason="not_admin",
                    description_placeholders={"name": household.name},
                )
            await self.async_set_unique_id(entry.unique_id)
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(entry, data_updates=data)

        if not households:
            return self.async_abort(reason="no_households")
        configured = {entry.unique_id for entry in self._async_current_entries()}
        candidates = [
            household for household in households if household.id not in configured
        ]
        if not candidates:
            return self.async_abort(reason="already_configured")
        admin_of = [household for household in candidates if household.admin]
        if not admin_of:
            return self.async_abort(
                reason="not_admin",
                description_placeholders={
                    "name": ", ".join(household.name for household in candidates)
                },
            )
        # Only households this account administers can be set up.
        self._households = {household.id: household for household in admin_of}
        if len(admin_of) == 1:
            return await self._async_create(admin_of[0])
        return await self.async_step_household()

    async def async_step_household(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a household."""
        if user_input is not None:
            return await self._async_create(
                self._households[user_input[CONF_HOUSEHOLD_ID]]
            )
        return self.async_show_form(
            step_id="household",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_HOUSEHOLD_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=household.id, label=household.name
                                )
                                for household in self._households.values()
                            ]
                        )
                    )
                }
            ),
        )

    async def _async_create(self, household: Household) -> ConfigFlowResult:
        """Create the entry of a household the account administers."""
        await self.async_set_unique_id(household.id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=household.name,
            data={**self._oauth_data, CONF_HOUSEHOLD_ID: household.id},
            options={}
            if self._api_base_url == DEFAULT_BASE_URL
            else {CONF_API_BASE_URL: self._api_base_url},
        )


SECTION_ADVANCED = "advanced"

ADVANCED_SECTION = section(
    probatio.Schema(
        {
            probatio.Required(CONF_API_BASE_URL): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            )
        }
    ),
    {"collapsed": True},
)

USER_SCHEMA = probatio.Schema({probatio.Required(SECTION_ADVANCED): ADVANCED_SECTION})

OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL,
                max=MAX_SCAN_INTERVAL,
                step=1,
                unit_of_measurement=UnitOfTime.SECONDS,
                mode=NumberSelectorMode.BOX,
            )
        ),
        probatio.Required(CONF_PREFER_CLOUDHOOK): BooleanSelector(),
        probatio.Required(SECTION_ADVANCED): ADVANCED_SECTION,
    }
)


class FluentPetOptionsFlow(OptionsFlowWithReload):
    """Change the fallback refresh interval and the webhook URL preference."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the options; the API base URL is in a collapsed section."""
        options = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            data: dict[str, Any] = {
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                CONF_PREFER_CLOUDHOOK: user_input[CONF_PREFER_CLOUDHOOK],
            }
            url = normalize_api_base_url(
                user_input[SECTION_ADVANCED][CONF_API_BASE_URL]
            )
            if url is None:
                errors["base"] = "invalid_api_base_url"
            else:
                if url != DEFAULT_BASE_URL:
                    data[CONF_API_BASE_URL] = url
                return self.async_create_entry(data=data)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA,
                user_input
                or {
                    CONF_SCAN_INTERVAL: options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                    CONF_PREFER_CLOUDHOOK: options.get(
                        CONF_PREFER_CLOUDHOOK, DEFAULT_PREFER_CLOUDHOOK
                    ),
                    SECTION_ADVANCED: {CONF_API_BASE_URL: api_base_url(options)},
                },
            ),
            errors=errors,
        )
