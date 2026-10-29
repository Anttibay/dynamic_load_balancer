"""Config flow for Dynamic Load Balancer integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
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
    AGGRESSIVENESS_LEVELS,
    CONF_AGGRESSIVENESS,
    CONF_CHARGER_MARGIN,
    CONF_CHARGING_ENTITY,
    CONF_DEVICE_LOAD,
    CONF_DEVICES_TO_TOGGLE,
    CONF_ENABLED_PHASES,
    CONF_FUSE_SIZE,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_ON_RESTORE,
    CONF_NOTIFY_TARGET,
    CONF_PHASE_1_SENSOR,
    CONF_PHASE_2_SENSOR,
    CONF_PHASE_3_SENSOR,
    CONF_REDUCTION_INTERVAL,
    CONF_RESTORE_HEADROOM,
    CONF_RESTORE_PRIORITY,
    CONF_RESTORE_SETTLE_TIME,
    CONF_RESTORE_STEP_TIME,
    CONF_SPIKE_FILTER_TIME,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_CHARGER_MARGIN,
    DEFAULT_DEVICE_LOAD,
    DEFAULT_ENABLED_PHASES,
    DEFAULT_FUSE_SIZE,
    DEFAULT_NOTIFY_ENABLED,
    DEFAULT_NOTIFY_ON_RESTORE,
    DEFAULT_REDUCTION_INTERVAL,
    DEFAULT_RESTORE_HEADROOM,
    DEFAULT_RESTORE_PRIORITY,
    DEFAULT_RESTORE_SETTLE_TIME,
    DEFAULT_RESTORE_STEP_TIME,
    DEFAULT_SPIKE_FILTER_TIME,
    DOMAIN,
    RESTORE_PRIORITIES,
    TOGGLE_DOMAINS,
)

_LOGGER = logging.getLogger(__name__)

PHASE_SENSOR_KEYS = {
    1: CONF_PHASE_1_SENSOR,
    2: CONF_PHASE_2_SENSOR,
    3: CONF_PHASE_3_SENSOR,
}

# ── Selectors shared by the config flow and the options flow ─────────────────

FUSE_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=10, max=125, step=1, unit_of_measurement="A", mode=NumberSelectorMode.BOX
    )
)
AGGRESSIVENESS_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=list(AGGRESSIVENESS_LEVELS),
        mode=SelectSelectorMode.DROPDOWN,
        translation_key="aggressiveness",
    )
)
SPIKE_FILTER_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=5, max=300, step=5, unit_of_measurement="s", mode=NumberSelectorMode.BOX
    )
)
RESTORE_HEADROOM_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=1.0, max=15.0, step=0.5, unit_of_measurement="A", mode=NumberSelectorMode.BOX
    )
)
PHASE_SENSOR_SELECTOR = EntitySelector(EntitySelectorConfig(domain="sensor"))
ENABLED_PHASES_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=["1", "2", "3"],
        multiple=True,
        mode=SelectSelectorMode.LIST,
        translation_key="phases",
    )
)
CHARGER_SELECTOR = EntitySelector(EntitySelectorConfig(domain=["number", "input_number"]))
DEVICES_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain=TOGGLE_DOMAINS, multiple=True)
)
DEVICE_LOAD_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0.5, max=32.0, step=0.5, unit_of_measurement="A", mode=NumberSelectorMode.BOX
    )
)
RESTORE_PRIORITY_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=RESTORE_PRIORITIES,
        mode=SelectSelectorMode.LIST,
        translation_key="restore_priority",
    )
)
NOTIFY_TARGET_SELECTOR = DeviceSelector(DeviceSelectorConfig(integration="mobile_app"))
SECONDS_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=5, max=900, step=5, unit_of_measurement="s", mode=NumberSelectorMode.BOX
    )
)
CHARGER_MARGIN_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0.0, max=10.0, step=0.5, unit_of_measurement="A", mode=NumberSelectorMode.BOX
    )
)


# ── Field builders — each returns the schema fields for one group of settings ─


def _suggested(config: dict[str, Any], key: str) -> dict[str, Any]:
    """Pre-fill an optional field without making it impossible to clear."""
    return {"suggested_value": config.get(key)}


def _fuse_fields(config: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(
            CONF_FUSE_SIZE, default=config.get(CONF_FUSE_SIZE, DEFAULT_FUSE_SIZE)
        ): FUSE_SELECTOR,
    }


def _behavior_fields(config: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(
            CONF_AGGRESSIVENESS,
            default=config.get(CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS),
        ): AGGRESSIVENESS_SELECTOR,
        vol.Required(
            CONF_SPIKE_FILTER_TIME,
            default=config.get(CONF_SPIKE_FILTER_TIME, DEFAULT_SPIKE_FILTER_TIME),
        ): SPIKE_FILTER_SELECTOR,
        vol.Required(
            CONF_RESTORE_HEADROOM,
            default=config.get(CONF_RESTORE_HEADROOM, DEFAULT_RESTORE_HEADROOM),
        ): RESTORE_HEADROOM_SELECTOR,
    }


def _sensor_fields(config: dict[str, Any]) -> dict[Any, Any]:
    fields: dict[Any, Any] = {
        vol.Optional(key, description=_suggested(config, key)): PHASE_SENSOR_SELECTOR
        for key in PHASE_SENSOR_KEYS.values()
    }
    fields[
        vol.Required(
            CONF_ENABLED_PHASES,
            default=config.get(CONF_ENABLED_PHASES, DEFAULT_ENABLED_PHASES),
        )
    ] = ENABLED_PHASES_SELECTOR
    return fields


def _charger_device_fields(config: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Optional(
            CONF_CHARGING_ENTITY, description=_suggested(config, CONF_CHARGING_ENTITY)
        ): CHARGER_SELECTOR,
        vol.Optional(
            CONF_DEVICES_TO_TOGGLE,
            description=_suggested(config, CONF_DEVICES_TO_TOGGLE),
        ): DEVICES_SELECTOR,
    }


def _load_strategy_fields(config: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(
            CONF_DEVICE_LOAD, default=config.get(CONF_DEVICE_LOAD, DEFAULT_DEVICE_LOAD)
        ): DEVICE_LOAD_SELECTOR,
        vol.Required(
            CONF_RESTORE_PRIORITY,
            default=config.get(CONF_RESTORE_PRIORITY, DEFAULT_RESTORE_PRIORITY),
        ): RESTORE_PRIORITY_SELECTOR,
    }


def _notification_fields(
    config: dict[str, Any], include_restore: bool = True
) -> dict[Any, Any]:
    fields: dict[Any, Any] = {
        vol.Required(
            CONF_NOTIFY_ENABLED,
            default=config.get(CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED),
        ): BooleanSelector(),
        vol.Optional(
            CONF_NOTIFY_TARGET, description=_suggested(config, CONF_NOTIFY_TARGET)
        ): NOTIFY_TARGET_SELECTOR,
    }
    if include_restore:
        fields[
            vol.Required(
                CONF_NOTIFY_ON_RESTORE,
                default=config.get(CONF_NOTIFY_ON_RESTORE, DEFAULT_NOTIFY_ON_RESTORE),
            )
        ] = BooleanSelector()
    return fields


def _advanced_fields(config: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(
            CONF_RESTORE_SETTLE_TIME,
            default=config.get(CONF_RESTORE_SETTLE_TIME, DEFAULT_RESTORE_SETTLE_TIME),
        ): SECONDS_SELECTOR,
        vol.Required(
            CONF_RESTORE_STEP_TIME,
            default=config.get(CONF_RESTORE_STEP_TIME, DEFAULT_RESTORE_STEP_TIME),
        ): SECONDS_SELECTOR,
        vol.Required(
            CONF_REDUCTION_INTERVAL,
            default=config.get(CONF_REDUCTION_INTERVAL, DEFAULT_REDUCTION_INTERVAL),
        ): SECONDS_SELECTOR,
        vol.Required(
            CONF_CHARGER_MARGIN,
            default=config.get(CONF_CHARGER_MARGIN, DEFAULT_CHARGER_MARGIN),
        ): CHARGER_MARGIN_SELECTOR,
    }


def _trigger_placeholders(fuse: float) -> dict[str, str]:
    """Trigger currents per aggressiveness level, shown in step descriptions."""
    placeholders = {"fuse_size": f"{fuse:g}"}
    for level, ratio in AGGRESSIVENESS_LEVELS.items():
        placeholders[f"{level}_trigger"] = f"{fuse * ratio:.1f}"
    return placeholders


def _validate_sensors(user_input: dict[str, Any]) -> dict[str, str]:
    """Require at least one monitored phase and no sensor assigned twice."""
    assigned = [user_input.get(key) for key in PHASE_SENSOR_KEYS.values()]
    assigned = [sensor for sensor in assigned if sensor]
    if len(assigned) != len(set(assigned)):
        return {"base": "duplicate_sensor"}

    enabled = {int(phase) for phase in user_input.get(CONF_ENABLED_PHASES, [])}
    if not any(user_input.get(PHASE_SENSOR_KEYS[phase]) for phase in enabled):
        return {"base": "no_monitored_phase"}
    return {}


class DynamicLoadBalancerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dynamic Load Balancer."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - fuse size configuration."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_phases()

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(_fuse_fields(self._data))
        )

    async def async_step_phases(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle phase sensor configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_sensors(user_input)
            if not errors:
                self._data.update(user_input)
                return await self.async_step_behavior()

        return self.async_show_form(
            step_id="phases",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_sensor_fields(self._data)), user_input or {}
            ),
            errors=errors,
        )

    async def async_step_behavior(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle behavior configuration."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_actions()

        return self.async_show_form(
            step_id="behavior",
            data_schema=vol.Schema(_behavior_fields(self._data)),
            description_placeholders=_trigger_placeholders(
                float(self._data[CONF_FUSE_SIZE])
            ),
        )

    async def async_step_actions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle actions and notifications configuration."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(
                title=f"Load Balancer ({float(self._data[CONF_FUSE_SIZE]):g}A)",
                data=self._data,
            )

        return self.async_show_form(
            step_id="actions",
            data_schema=vol.Schema(
                {
                    **_charger_device_fields(self._data),
                    **_notification_fields(self._data, include_restore=False),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlow):
    """Menu-driven options flow: every setting can be changed after setup."""

    @property
    def _current(self) -> dict[str, Any]:
        """Effective configuration — options override the initial setup data."""
        return {**self.config_entry.data, **self.config_entry.options}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "thresholds",
                "sensors",
                "load_control",
                "notifications",
                "advanced",
            ],
        )

    @callback
    def _save(
        self, fields: dict[Any, Any], user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Store the submitted fields on top of the existing options.

        Optional fields left empty are stored as None so they override any value
        from the initial setup (otherwise a cleared field would silently fall
        back to the original config entry data).
        """
        options = dict(self.config_entry.options)
        for marker in fields:
            options[marker.schema] = user_input.get(marker.schema)
        return self.async_create_entry(data=options)

    async def async_step_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fuse size, aggressiveness, spike filter and restore headroom."""
        current = self._current
        fields = {**_fuse_fields(current), **_behavior_fields(current)}
        if user_input is not None:
            return self._save(fields, user_input)

        return self.async_show_form(
            step_id="thresholds",
            data_schema=vol.Schema(fields),
            description_placeholders=_trigger_placeholders(
                float(current.get(CONF_FUSE_SIZE, DEFAULT_FUSE_SIZE))
            ),
        )

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Phase current sensors and which phases are monitored."""
        fields = _sensor_fields(self._current)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_sensors(user_input)
            if not errors:
                return self._save(fields, user_input)

        schema = vol.Schema(fields)
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="sensors", data_schema=schema, errors=errors
        )

    async def async_step_load_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """EV charger, devices to toggle and restoration strategy."""
        current = self._current
        fields = {**_charger_device_fields(current), **_load_strategy_fields(current)}
        if user_input is not None:
            return self._save(fields, user_input)

        return self.async_show_form(step_id="load_control", data_schema=vol.Schema(fields))

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Notification settings."""
        fields = _notification_fields(self._current)
        if user_input is not None:
            return self._save(fields, user_input)

        return self.async_show_form(step_id="notifications", data_schema=vol.Schema(fields))

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Timing and estimation parameters of the control loop."""
        fields = _advanced_fields(self._current)
        if user_input is not None:
            return self._save(fields, user_input)

        return self.async_show_form(step_id="advanced", data_schema=vol.Schema(fields))
