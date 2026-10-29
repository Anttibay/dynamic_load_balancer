"""Diagnostics support for Dynamic Load Balancer."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import LoadBalancerConfigEntry
from .const import CONF_NOTIFY_TARGET

TO_REDACT = {CONF_NOTIFY_TARGET}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LoadBalancerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (Download diagnostics in the UI)."""
    coordinator = entry.runtime_data
    return {
        "config": async_redact_data(coordinator.config, TO_REDACT),
        "enabled": coordinator.enabled,
        "persisted_state": coordinator.state_as_dict(),
        "data": coordinator.data,
    }
