"""Button platform for Dynamic Load Balancer."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LoadBalancerConfigEntry
from .coordinator import LoadBalancerCoordinator
from .entity import LoadBalancerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoadBalancerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dynamic Load Balancer buttons."""
    async_add_entities([RestoreLoadButton(entry.runtime_data)])


class RestoreLoadButton(LoadBalancerEntity, ButtonEntity):
    """Immediately restore all reduced load, skipping the cautious ramp-up.

    Balancing stays enabled: if the overload is still there it is reduced
    again once the spike filter confirms it.
    """

    def __init__(self, coordinator: LoadBalancerCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "restore_load")

    async def async_press(self) -> None:
        """Restore everything now."""
        await self.coordinator.async_restore_now()
