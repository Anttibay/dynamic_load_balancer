"""Switch platform for Dynamic Load Balancer."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LoadBalancerConfigEntry
from .const import (
    CONF_NOTIFY_ENABLED,
    DEFAULT_NOTIFY_ENABLED,
    STATUS_DISABLED,
    STATUS_MONITORING,
    STATUS_OVERLOAD_PENDING,
    STATUS_REDUCING,
    STATUS_RESTORING,
    STATUS_SENSOR_UNAVAILABLE,
    STATUS_SETTLING,
    STATUS_WAITING_HEADROOM,
)
from .coordinator import LoadBalancerCoordinator
from .entity import LoadBalancerEntity

_LOGGER = logging.getLogger(__name__)

# Human-readable status for the switch's "status" attribute (kept for automations
# written against earlier versions; the Status sensor exposes the raw value).
_STATUS_TEXT = {
    STATUS_MONITORING: "Monitoring",
    STATUS_OVERLOAD_PENDING: "Overload detected — filtering spikes",
    STATUS_REDUCING: "Overload — reducing load",
    STATUS_WAITING_HEADROOM: "Waiting for headroom",
    STATUS_SETTLING: "Settling — waiting to restore",
    STATUS_RESTORING: "Restoring — waiting between steps",
    STATUS_DISABLED: "Disabled",
    STATUS_SENSOR_UNAVAILABLE: "Sensor unavailable",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoadBalancerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Dynamic Load Balancer switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        [LoadBalancerSwitch(coordinator), NotificationsSwitch(coordinator)]
    )


class LoadBalancerSwitch(LoadBalancerEntity, SwitchEntity, RestoreEntity):
    """Master switch to enable/disable load balancing."""

    _attr_name = None  # Main feature of the device — takes the device name

    def __init__(self, coordinator: LoadBalancerCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "load_balancer_switch")

    async def async_added_to_hass(self) -> None:
        """Restore the last known state after a restart."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self.coordinator.enabled = last_state.state != STATE_OFF
            _LOGGER.info(
                "Restored load balancer switch: %s",
                "enabled" if self.coordinator.enabled else "disabled",
            )
            if not self.coordinator.enabled:
                await self.coordinator.async_request_refresh()

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self.coordinator.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data or {}

        phase_info = {
            f"phase_{phase}_current": round(current, 2)
            for phase, current in data.get("phase_currents", {}).items()
            if current is not None
        }
        charging_original = data.get("charging_original_value")
        disabled_devices = data.get("disabled_devices", [])

        return {
            **phase_info,
            "fuse_size": data.get("fuse_size"),
            "trigger_current": round(data.get("trigger_current", 0), 2),
            "is_managing_load": data.get("is_managing", False),
            "overloaded_phases": data.get("overloaded_phases", []),
            "sustained_overloads": data.get("sustained_overloads", []),
            "charging_original_value": charging_original,
            "disabled_devices": disabled_devices,
            "restoring": charging_original is not None or bool(disabled_devices),
            "status": _STATUS_TEXT.get(data.get("status"), data.get("status")),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on load balancing."""
        self.coordinator.enabled = True
        _LOGGER.info("Load balancing enabled")
        await self.coordinator.async_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off load balancing and immediately hand back all reduced load."""
        self.coordinator.enabled = False
        _LOGGER.info("Load balancing disabled")
        await self.coordinator.async_restore_now()


class NotificationsSwitch(LoadBalancerEntity, SwitchEntity):
    """Configuration switch: send overload notifications or not."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: LoadBalancerCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "notifications")

    @property
    def is_on(self) -> bool:
        """Return true if notifications are enabled."""
        return bool(self.coordinator.config.get(CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable notifications."""
        await self.coordinator.async_set_option(CONF_NOTIFY_ENABLED, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable notifications."""
        await self.coordinator.async_set_option(CONF_NOTIFY_ENABLED, False)
        self.async_write_ha_state()
