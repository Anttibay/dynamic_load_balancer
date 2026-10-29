"""Binary sensor platform for Dynamic Load Balancer."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoadBalancerConfigEntry
from .coordinator import LoadBalancerCoordinator
from .entity import LoadBalancerEntity


@dataclass(frozen=True, kw_only=True)
class LoadBalancerBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a load balancer binary sensor."""

    is_on_fn: Callable[[LoadBalancerCoordinator], bool]
    attrs_fn: Callable[[LoadBalancerCoordinator], dict[str, Any]]


BINARY_SENSORS: tuple[LoadBalancerBinarySensorDescription, ...] = (
    # On while any monitored phase is above the trigger threshold
    LoadBalancerBinarySensorDescription(
        key="overload",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda c: bool((c.data or {}).get("overloaded_phases")),
        attrs_fn=lambda c: {
            "overloaded_phases": (c.data or {}).get("overloaded_phases", []),
            "sustained_overloads": (c.data or {}).get("sustained_overloads", []),
        },
    ),
    # On while the charger is throttled or devices are held off
    LoadBalancerBinarySensorDescription(
        key="load_reduced",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda c: c.is_managing_load or c.has_reduced_load,
        attrs_fn=lambda c: {
            "charging_entity": c.charging_entity,
            "charging_original_value": c.charging_original_value,
            "disabled_devices": list(c.disabled_devices),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoadBalancerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dynamic Load Balancer binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        LoadBalancerBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class LoadBalancerBinarySensor(LoadBalancerEntity, BinarySensorEntity):
    """Binary sensor exposing one condition of the load balancer."""

    entity_description: LoadBalancerBinarySensorDescription

    def __init__(
        self,
        coordinator: LoadBalancerCoordinator,
        description: LoadBalancerBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return true if the condition is active."""
        return self.entity_description.is_on_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra detail."""
        return self.entity_description.attrs_fn(self.coordinator)
