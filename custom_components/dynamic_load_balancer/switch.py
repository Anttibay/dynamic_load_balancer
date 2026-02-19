"""Switch platform for Dynamic Load Balancer."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LoadBalancerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Dynamic Load Balancer switch."""
    coordinator: LoadBalancerCoordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([LoadBalancerSwitch(coordinator, entry)])


class LoadBalancerSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable load balancing."""

    def __init__(
        self,
        coordinator: LoadBalancerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_name = "Dynamic Load Balancer"
        self._attr_unique_id = f"{entry.entry_id}_load_balancer_switch"
        self._attr_icon = "mdi:transmission-tower"
        self._enabled = True

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data or {}
        
        phase_currents = data.get("phase_currents", {})
        phase_info = {}
        for phase, current in phase_currents.items():
            if current is not None:
                phase_info[f"phase_{phase}_current"] = round(current, 2)
        
        overloaded = data.get("sustained_overloads", [])
        
        return {
            **phase_info,
            "fuse_size": data.get("fuse_size"),
            "trigger_current": round(data.get("trigger_current", 0), 2),
            "is_managing_load": data.get("is_managing", False),
            "overloaded_phases": data.get("overloaded_phases", []),
            "sustained_overloads": overloaded,
            "charging_original_value": data.get("charging_original_value"),
            "disabled_devices": data.get("disabled_devices", []),
            "status": "Managing load" if overloaded else "Monitoring",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on load balancing."""
        self._enabled = True
        self.coordinator.enabled = True
        self.async_write_ha_state()
        _LOGGER.info("Load balancing enabled")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off load balancing."""
        self._enabled = False
        self.coordinator.enabled = False
        # Restore all devices when disabled
        await self.coordinator._restore_load()
        self.async_write_ha_state()
        _LOGGER.info("Load balancing disabled")
