"""Base entity for Dynamic Load Balancer."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LoadBalancerCoordinator


class LoadBalancerEntity(CoordinatorEntity[LoadBalancerCoordinator]):
    """Common device info and naming for all entities of one load balancer."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LoadBalancerCoordinator, key: str) -> None:
        """Initialize the entity; key doubles as unique-id suffix and translation key."""
        super().__init__(coordinator)
        entry_id = coordinator.entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Dynamic Load Balancer",
            manufacturer="Custom Integration",
            model="Electrical Load Balancer",
        )
