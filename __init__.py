"""Dynamic Load Balancer Integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry # type: ignore
from homeassistant.core import HomeAssistant # type: ignore
from homeassistant.helpers.event import async_track_time_interval # type: ignore

from .const import DOMAIN
from .coordinator import LoadBalancerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dynamic Load Balancer from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    coordinator = LoadBalancerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Start the load balancing loop
    async def update_interval(now):
        await coordinator.async_refresh()
    
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            update_interval,
            timedelta(seconds=5)
        )
    )
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok
