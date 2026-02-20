"""Config flow for Dynamic Load Balancer integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_AGGRESSIVENESS,
    CONF_CHARGING_ENTITY,
    CONF_DEVICES_TO_TOGGLE,
    CONF_ENABLED_PHASES,
    CONF_FUSE_SIZE,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_TARGET,
    CONF_PHASE_1_SENSOR,
    CONF_PHASE_2_SENSOR,
    CONF_PHASE_3_SENSOR,
    CONF_SPIKE_FILTER_TIME,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_ENABLED_PHASES,
    DEFAULT_FUSE_SIZE,
    DEFAULT_NOTIFY_ENABLED,
    DEFAULT_SPIKE_FILTER_TIME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class DynamicLoadBalancerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dynamic Load Balancer."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial step - fuse size configuration."""
        errors = {}

        if user_input is not None:
            self._fuse_size = user_input[CONF_FUSE_SIZE]
            return await self.async_step_phases()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_FUSE_SIZE, default=DEFAULT_FUSE_SIZE): NumberSelector(
                    NumberSelectorConfig(
                        min=10,
                        max=125,
                        step=1,
                        unit_of_measurement="A",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_phases(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle phase sensor configuration."""
        errors = {}

        if user_input is not None:
            self._phase_config = user_input
            return await self.async_step_behavior()

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_PHASE_1_SENSOR): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_PHASE_2_SENSOR): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_PHASE_3_SENSOR): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(
                    CONF_ENABLED_PHASES, default=DEFAULT_ENABLED_PHASES
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=["1", "2", "3"],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="phases",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_behavior(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle behavior configuration."""
        errors = {}

        if user_input is not None:
            self._behavior_config = user_input
            return await self.async_step_actions()

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_AGGRESSIVENESS, default=DEFAULT_AGGRESSIVENESS
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": "low", "label": "Low (95% capacity)"},
                            {"value": "medium", "label": "Medium (90% capacity)"},
                            {"value": "high", "label": "High (85% capacity)"},
                            {"value": "very_high", "label": "Very High (80% capacity)"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SPIKE_FILTER_TIME, default=DEFAULT_SPIKE_FILTER_TIME
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5,
                        max=300,
                        step=5,
                        unit_of_measurement="seconds",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        fuse = self._fuse_size
        return self.async_show_form(
            step_id="behavior",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "fuse_size": str(fuse),
                "low_trigger": str(round(fuse * 0.95, 1)),
                "medium_trigger": str(round(fuse * 0.90, 1)),
                "high_trigger": str(round(fuse * 0.85, 1)),
                "very_high_trigger": str(round(fuse * 0.80, 1)),
            },
        )

    async def async_step_actions(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle actions and notifications configuration."""
        errors = {}

        if user_input is not None:
            config_data = {
                CONF_FUSE_SIZE: self._fuse_size,
                **self._phase_config,
                **self._behavior_config,
                **user_input,
            }
            return self.async_create_entry(
                title=f"Load Balancer ({self._fuse_size}A)",
                data=config_data,
            )

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_CHARGING_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain=["number"])
                ),
                vol.Optional(CONF_DEVICES_TO_TOGGLE): EntitySelector(
                    EntitySelectorConfig(
                        domain=["switch", "climate", "water_heater"],
                        multiple=True,
                    )
                ),
                vol.Required(
                    CONF_NOTIFY_ENABLED, default=DEFAULT_NOTIFY_ENABLED
                ): BooleanSelector(),
                vol.Optional(CONF_NOTIFY_TARGET): DeviceSelector(DeviceSelectorConfig(integration="mobile_app")),
            }
        )

        return self.async_show_form(
            step_id="actions",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for the integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_FUSE_SIZE,
                    default=current.get(CONF_FUSE_SIZE, DEFAULT_FUSE_SIZE),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10,
                        max=125,
                        step=1,
                        unit_of_measurement="A",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_AGGRESSIVENESS,
                    default=current.get(CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": "low", "label": "Low (95% capacity)"},
                            {"value": "medium", "label": "Medium (90% capacity)"},
                            {"value": "high", "label": "High (85% capacity)"},
                            {"value": "very_high", "label": "Very High (80% capacity)"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SPIKE_FILTER_TIME,
                    default=current.get(CONF_SPIKE_FILTER_TIME, DEFAULT_SPIKE_FILTER_TIME),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5,
                        max=300,
                        step=5,
                        unit_of_measurement="seconds",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_NOTIFY_ENABLED,
                    default=current.get(CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_NOTIFY_TARGET,
                    default=current.get(CONF_NOTIFY_TARGET),
                ): DeviceSelector(DeviceSelectorConfig(integration="mobile_app")),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)
