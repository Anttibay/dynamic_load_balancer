"""Dynamic Load Balancer Integration for Home Assistant by Anttibay."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import LoadBalancerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type LoadBalancerConfigEntry = ConfigEntry[LoadBalancerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LoadBalancerConfigEntry) -> bool:
    """Set up Dynamic Load Balancer from a config entry."""
    coordinator = LoadBalancerCoordinator(hass, entry)
    await coordinator.async_load_state()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Apply option changes (options flow or config entities) live — no reload,
    # so an ongoing reduction / restoration is never interrupted.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: LoadBalancerConfigEntry
) -> None:
    """Handle options update by re-reading the configuration."""
    await entry.runtime_data.async_apply_config()


async def async_unload_entry(hass: HomeAssistant, entry: LoadBalancerConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_save_state()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: LoadBalancerConfigEntry) -> None:
    """Hand back any load still reduced when the integration is deleted."""
    coordinator = LoadBalancerCoordinator(hass, entry)
    await coordinator.async_load_state()
    if coordinator.has_reduced_load:
        _LOGGER.info("Integration removed while load was reduced — restoring it")
        await coordinator.async_force_restore()
    await coordinator.async_remove_state()
