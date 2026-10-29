"""Number platform for Dynamic Load Balancer — live-adjustable settings."""
from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoadBalancerConfigEntry
from .const import (
    CONF_FUSE_SIZE,
    CONF_RESTORE_HEADROOM,
    CONF_SPIKE_FILTER_TIME,
    DEFAULT_FUSE_SIZE,
    DEFAULT_RESTORE_HEADROOM,
    DEFAULT_SPIKE_FILTER_TIME,
)
from .coordinator import LoadBalancerCoordinator
from .entity import LoadBalancerEntity

# Ranges match the config flow so both UIs accept the same values
NUMBERS: tuple[tuple[NumberEntityDescription, float], ...] = (
    (
        NumberEntityDescription(
            key=CONF_FUSE_SIZE,
            device_class=NumberDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            native_min_value=10,
            native_max_value=125,
            native_step=1,
            mode=NumberMode.BOX,
            entity_category=EntityCategory.CONFIG,
        ),
        DEFAULT_FUSE_SIZE,
    ),
    (
        NumberEntityDescription(
            key=CONF_SPIKE_FILTER_TIME,
            device_class=NumberDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            native_min_value=5,
            native_max_value=300,
            native_step=5,
            mode=NumberMode.SLIDER,
            entity_category=EntityCategory.CONFIG,
        ),
        DEFAULT_SPIKE_FILTER_TIME,
    ),
    (
        NumberEntityDescription(
            key=CONF_RESTORE_HEADROOM,
            device_class=NumberDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            native_min_value=1,
            native_max_value=15,
            native_step=0.5,
            mode=NumberMode.SLIDER,
            entity_category=EntityCategory.CONFIG,
        ),
        DEFAULT_RESTORE_HEADROOM,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoadBalancerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dynamic Load Balancer number entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        LoadBalancerNumber(coordinator, description, default)
        for description, default in NUMBERS
    )


class LoadBalancerNumber(LoadBalancerEntity, NumberEntity):
    """A setting that can be changed from dashboards and automations.

    Changes are saved to the config entry options (so the options dialog shows
    them too) and take effect on the next control cycle without a reload.
    """

    def __init__(
        self,
        coordinator: LoadBalancerCoordinator,
        description: NumberEntityDescription,
        default: float,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._default = default

    @property
    def native_value(self) -> float:
        """Return the current setting."""
        value = self.coordinator.config.get(self.entity_description.key)
        return float(value) if value is not None else float(self._default)

    async def async_set_native_value(self, value: float) -> None:
        """Save a new value."""
        await self.coordinator.async_set_option(self.entity_description.key, value)
        self.async_write_ha_state()
