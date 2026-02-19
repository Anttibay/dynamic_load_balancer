"""Sensor platform for Dynamic Load Balancer."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    """Set up Dynamic Load Balancer sensors."""
    coordinator: LoadBalancerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LastTriggeredSensor(coordinator, entry)])


class LastTriggeredSensor(CoordinatorEntity, SensorEntity):
    """Sensor that records the timestamp of the last electrical overload event.

    The state is a timezone-aware datetime when an overload was last detected,
    or None (shown as 'Unknown' in HA) if no overload has been seen since the
    integration was last loaded.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-alert-outline"

    def __init__(
        self,
        coordinator: LoadBalancerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "Load Balancer Last Overload"
        self._attr_unique_id = f"{entry.entry_id}_last_triggered"

    @property
    def native_value(self):
        """Return the timestamp of the last overload, or None if never triggered."""
        return self.coordinator.last_triggered_time

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra detail about the last overload event."""
        data = self.coordinator.data or {}
        return {
            "last_overloaded_phases": data.get("last_overloaded_phases", []),
            "last_peak_current": data.get("last_peak_current"),
            "trigger_current_at_event": data.get("trigger_current_at_event"),
        }
