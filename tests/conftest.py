"""Shared fixtures for Dynamic Load Balancer tests."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

# Import the repository's custom_components package before Home Assistant mounts
# its own testing config dir, so the loader discovers this integration.
import custom_components  # noqa: F401
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_load_balancer.const import (
    CONF_AGGRESSIVENESS,
    CONF_CHARGING_ENTITY,
    CONF_DEVICES_TO_TOGGLE,
    CONF_ENABLED_PHASES,
    CONF_FUSE_SIZE,
    CONF_NOTIFY_ENABLED,
    CONF_PHASE_1_SENSOR,
    CONF_PHASE_2_SENSOR,
    CONF_PHASE_3_SENSOR,
    CONF_RESTORE_HEADROOM,
    CONF_SPIKE_FILTER_TIME,
    DOMAIN,
)
from custom_components.dynamic_load_balancer.coordinator import LoadBalancerCoordinator
from homeassistant.core import HomeAssistant, ServiceCall

CHARGER = "input_number.charger"
HEATER = "switch.heater"
AC = "climate.ac"

BASE_CONFIG = {
    CONF_FUSE_SIZE: 25,  # medium aggressiveness → trigger at 22.5 A
    CONF_PHASE_1_SENSOR: "sensor.l1",
    CONF_PHASE_2_SENSOR: "sensor.l2",
    CONF_PHASE_3_SENSOR: "sensor.l3",
    CONF_ENABLED_PHASES: ["1", "2", "3"],
    CONF_AGGRESSIVENESS: "medium",
    CONF_SPIKE_FILTER_TIME: 30,
    CONF_RESTORE_HEADROOM: 3.0,
    CONF_CHARGING_ENTITY: CHARGER,
    CONF_DEVICES_TO_TOGGLE: [HEATER, AC],
    CONF_NOTIFY_ENABLED: True,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading the integration from custom_components in every test."""
    yield


def set_currents(
    hass: HomeAssistant, l1: float, l2: float = 5.0, l3: float = 5.0, unit: str = "A"
) -> None:
    """Set the three phase current sensors."""
    for phase, value in enumerate((l1, l2, l3), start=1):
        hass.states.async_set(
            f"sensor.l{phase}", str(value), {"unit_of_measurement": unit}
        )


@pytest.fixture
def service_calls(hass: HomeAssistant) -> list[tuple[str, dict[str, Any]]]:
    """Fake device services that record calls and update entity states."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def _update(entity_id: str, state: str) -> None:
        current = hass.states.get(entity_id)
        hass.states.async_set(entity_id, state, current.attributes if current else {})

    def _register(domain: str, service: str, new_state: Callable[[ServiceCall], str]):
        async def handler(call: ServiceCall) -> None:
            calls.append((f"{domain}.{service}", dict(call.data)))
            _update(call.data["entity_id"], new_state(call))

        hass.services.async_register(domain, service, handler)

    _register("input_number", "set_value", lambda call: str(float(call.data["value"])))
    _register("homeassistant", "turn_off", lambda call: "off")
    _register("homeassistant", "turn_on", lambda call: "on")
    _register("climate", "set_hvac_mode", lambda call: call.data["hvac_mode"])
    _register(
        "water_heater", "set_operation_mode", lambda call: call.data["operation_mode"]
    )
    return calls


@pytest.fixture
def devices(hass: HomeAssistant) -> None:
    """Create the charger and toggleable devices in their normal running state."""
    hass.states.async_set(CHARGER, "16.0", {"min": 6, "max": 32, "step": 1})
    hass.states.async_set(HEATER, "on")
    hass.states.async_set(AC, "heat", {"hvac_modes": ["off", "heat", "cool"]})
    set_currents(hass, 10.0)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A config entry as created by the setup wizard."""
    return MockConfigEntry(
        domain=DOMAIN, title="Load Balancer (25A)", data=dict(BASE_CONFIG)
    )


@pytest.fixture
async def coordinator(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    devices: None,
    service_calls: list,
    freezer: FrozenDateTimeFactory,
) -> LoadBalancerCoordinator:
    """Set up the integration and return its coordinator."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry.runtime_data


async def advance(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    seconds: float = 0,
) -> None:
    """Move time forward and run one control cycle."""
    freezer.tick(timedelta(seconds=seconds))
    for entry in hass.config_entries.async_entries(DOMAIN):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
