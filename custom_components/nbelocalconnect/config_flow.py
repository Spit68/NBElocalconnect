import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    BooleanSelector,
)


class NbeConnectConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for NBELocalConnect custom integration."""

    VERSION = 2

    async def async_step_user(self, user_input=None):
        """Handle the initial step when setting up the integration."""
        errors = {}

        if user_input is not None:
            if not user_input.get("password"):
                errors["base"] = "missing_password"

            if user_input.get("stokercloud_enabled") and not user_input.get("stokercloud_username", "").strip():
                errors["stokercloud_username"] = "missing_stokercloud_username"

            if not errors:
                unique_id = user_input.get("serial") or user_input.get("ip_address") or "nbe_boiler"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                if not user_input.get("serial"):
                    user_input["serial"] = None
                return self.async_create_entry(title="NBE Local Connect", data=user_input)

        ui = user_input or {}
        STEP_USER_DATA_SCHEMA = vol.Schema(
            {
                vol.Required("serial", default=ui.get("serial", "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="serial")
                ),
                vol.Required("password", default=ui.get("password", "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                ),
                vol.Optional("ip_address", default=ui.get("ip_address", "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="ip_address")
                ),
                vol.Optional("stokercloud_enabled", default=ui.get("stokercloud_enabled", False)): BooleanSelector(),
                vol.Optional("stokercloud_username", default=ui.get("stokercloud_username", "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NbeConnectOptionsFlowHandler()


class NbeConnectOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle NBELocalConnect options flow."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}

        if user_input is not None:
            if not user_input.get("password"):
                errors["base"] = "missing_password"

            if user_input.get("stokercloud_enabled") and not user_input.get("stokercloud_username", "").strip():
                errors["stokercloud_username"] = "missing_stokercloud_username"

            if not errors:
                if not user_input.get("serial"):
                    user_input["serial"] = None
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=user_input
                )
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data={})

        current_data = self.config_entry.data

        OPTIONS_SCHEMA = vol.Schema(
            {
                vol.Required("serial", default=current_data.get("serial")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="serial")
                ),
                vol.Required("password", default=current_data.get("password", "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                ),
                vol.Optional("ip_address", default=current_data.get("ip_address", "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="")
                ),
                vol.Optional("stokercloud_enabled", default=current_data.get("stokercloud_enabled", False)): BooleanSelector(),
                vol.Optional("stokercloud_username", default=current_data.get("stokercloud_username", "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=OPTIONS_SCHEMA,
            errors=errors
        )