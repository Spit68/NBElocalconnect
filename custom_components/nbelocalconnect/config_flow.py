import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)


class NbeConnectConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for NBELocalConnect custom integration."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step when setting up the integration."""
        errors = {}

        if user_input is not None:
            # Validate the user input
            if not user_input.get("password"):
                errors["base"] = "missing_password"

            if not errors:
                # Configuration is valid, create the entry
                # Use IP address or serial for unique_id
                unique_id = user_input.get("ip_address") or user_input.get("serial") or "nbe_boiler"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                
                # Ensure serial is None if empty string
                if not user_input.get("serial"):
                    user_input["serial"] = None
                    
                return self.async_create_entry(title="NBE Local Connect", data=user_input)
        
        # Define the schema for the user input form
        STEP_USER_DATA_SCHEMA = vol.Schema(
            {
                vol.Required(
                    "serial", 
                    default="",
                    description={
                        "label": "Boiler Serial Number", 
                        "hint": "Enter serial number if you have multiple boilers. Leave empty for auto-discovery."
                    }
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="serial")
                ),
                vol.Required(
                    "password", 
                    description={
                        "label": "Boiler Password", 
                        "hint": "Enter the password found on your NBE boiler label (10 digits)."
                    }
                ): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD, autocomplete="current-password"
                    )
                ),
                vol.Optional(
                    "ip_address", 
                    default="",
                    description={
                        "label": "Boiler IP Address (Optional)", 
                        "hint": "Enter the fixed IP address of your boiler. Leave empty for auto-discovery."
                    }
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="ip_address")
                )
            }
        )

        # Show the configuration form to the user
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return NbeConnectOptionsFlowHandler()


class NbeConnectOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle NBELocalConnect options flow."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}

        if user_input is not None:
            # Validate the user input for reconfigure
            if not user_input.get("password"):
                errors["base"] = "missing_password"

            if not errors:
                # Ensure serial is None if empty string
                if not user_input.get("serial"):
                    user_input["serial"] = None
                    
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=user_input
                )
                return self.async_create_entry(title="", data=user_input)

        # Get current data to pre-fill the form
        current_data = self.config_entry.data

        # Define the schema for the reconfigure form, pre-filling with current values
        OPTIONS_SCHEMA = vol.Schema(
            {
                vol.Required(
                    "serial",
                    default=current_data.get("serial", ""),
                    description={
                        "label": "Boiler Serial Number", 
                        "hint": "Enter serial number if you have multiple boilers. Leave empty for auto-discovery."
                    }
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="serial")
                ),
                vol.Required(
                    "password",
                    default=current_data.get("password", ""),
                    description={
                        "label": "Boiler Password", 
                        "hint": "Enter the password found on your NBE boiler label (10 digits)."
                    }
                ): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD, autocomplete="current-password"
                    )
                ),
                vol.Optional(
                    "ip_address",
                    default=current_data.get("ip_address", ""),
                    description={
                        "label": "Boiler IP Address (Optional)", 
                        "hint": "Enter the fixed IP address of your boiler. Leave empty for auto-discovery."
                    }
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="")
                )
            }
        )

        # Show the reconfigure form
        return self.async_show_form(
            step_id="init",
            data_schema=OPTIONS_SCHEMA,
            errors=errors
        )