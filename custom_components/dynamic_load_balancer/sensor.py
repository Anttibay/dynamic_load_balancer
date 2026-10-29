"""Sensor platform for Dynamic Load Balancer."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import LoadBalancerConfigEntry
from .const import STATUSES
from .coordinator import LoadBalancerCoordinator
from .entity import LoadBalancerEntity


@dataclass(frozen=True, kw_only=True)
class LoadBalancerSensorDescription(SensorEntityDescription):
    """Describes a load balancer sensor."""

    value_fn: Callable[[LoadBalancerCoordinator], StateType | datetime]
    attrs_fn: Callable[[LoadBalancerCoordinator], dict[str, Any]] | None = None


def _data(coordinator: LoadBalancerCoordinator) -> dict[str, Any]:
    return coordinator.data or {}


SENSORS: tuple[LoadBalancerSensorDescription, ...] = (
    LoadBalancerSensorDescription(
        key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUSES,
        value_fn=lambda c: _data(c).get("status"),
        attrs_fn=lambda c: {
            "overloaded_phases": _data(c).get("overloaded_phases", []),
            "charging_original_value": c.charging_original_value,
            "disabled_devices": list(c.disabled_devices),
        },
    ),
    LoadBalancerSensorDescription(
        key="headroom",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: _data(c).get("min_headroom"),
        attrs_fn=lambda c: {
            f"phase_{phase}_headroom": round(_data(c)["trigger_current"] - current, 2)
            for phase, current in _data(c).get("phase_currents", {}).items()
            if current is not None and phase in c.monitored_phases
        },
    ),
    LoadBalancerSensorDescription(
        key="trigger_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda c: _data(c).get("trigger_current"),
    ),
    LoadBalancerSensorDescription(
        key="overload_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda c: c.overload_count,
    ),
    # The state is a timezone-aware datetime when an overload was last detected,
    # or None (shown as 'Unknown' in HA) if no overload has been recorded yet.
    LoadBalancerSensorDescription(
        key="last_triggered",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.last_triggered_time,
        attrs_fn=lambda c: {
            "last_overloaded_phases": c.last_triggered_phases,
            "last_peak_current": c.last_triggered_peak,
            "trigger_current_at_event": c.last_triggered_threshold,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoadBalancerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dynamic Load Balancer sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        LoadBalancerSensor(coordinator, description) for description in SENSORS
    )


class LoadBalancerSensor(LoadBalancerEntity, SensorEntity):
    """Sensor exposing one aspect of the load balancer's state."""

    entity_description: LoadBalancerSensorDescription

    def __init__(
        self,
        coordinator: LoadBalancerCoordinator,
        description: LoadBalancerSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra detail for this sensor."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator)
