"""Select platform for Dynamic Load Balancer — live-adjustable aggressiveness."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoadBalancerConfigEntry
from .const import AGGRESSIVENESS_LEVELS, CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS
from .coordinator import LoadBalancerCoordinator
from .entity import LoadBalancerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoadBalancerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dynamic Load Balancer select entities."""
    async_add_entities([AggressivenessSelect(entry.runtime_data)])


class AggressivenessSelect(LoadBalancerEntity, SelectEntity):
    """Trigger threshold as a share of the fuse size.

    Handy for automations, e.g. a more protective level during peak tariff hours.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(AGGRESSIVENESS_LEVELS)

    def __init__(self, coordinator: LoadBalancerCoordinator) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, CONF_AGGRESSIVENESS)

    @property
    def current_option(self) -> str:
        """Return the current aggressiveness level."""
        return self.coordinator.config.get(CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS)

    async def async_select_option(self, option: str) -> None:
        """Change the aggressiveness level."""
        await self.coordinator.async_set_option(CONF_AGGRESSIVENESS, option)
        self.async_write_ha_state()
