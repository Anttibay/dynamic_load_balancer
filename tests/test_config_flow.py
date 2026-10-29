"""Tests for the config flow and the options flow."""
from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_load_balancer.const import (
    CONF_AGGRESSIVENESS,
    CONF_CHARGER_MARGIN,
    CONF_CHARGING_ENTITY,
    CONF_DEVICE_LOAD,
    CONF_DEVICES_TO_TOGGLE,
    CONF_ENABLED_PHASES,
    CONF_FUSE_SIZE,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_ON_RESTORE,
    CONF_PHASE_1_SENSOR,
    CONF_PHASE_2_SENSOR,
    CONF_PHASE_3_SENSOR,
    CONF_REDUCTION_INTERVAL,
    CONF_RESTORE_HEADROOM,
    CONF_RESTORE_PRIORITY,
    CONF_RESTORE_SETTLE_TIME,
    CONF_RESTORE_STEP_TIME,
    CONF_SPIKE_FILTER_TIME,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import CHARGER, HEATER


async def test_full_user_flow(hass: HomeAssistant) -> None:
    """The four-step wizard creates an entry with all values."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FUSE_SIZE: 32}
    )
    assert result["step_id"] == "phases"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PHASE_1_SENSOR: "sensor.l1", CONF_ENABLED_PHASES: ["1"]},
    )
    assert result["step_id"] == "behavior"
    # Trigger currents for the chosen fuse are shown in the description
    assert result["description_placeholders"]["medium_trigger"] == "28.8"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_AGGRESSIVENESS: "high",
            CONF_SPIKE_FILTER_TIME: 20,
            CONF_RESTORE_HEADROOM: 4.0,
        },
    )
    assert result["step_id"] == "actions"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CHARGING_ENTITY: CHARGER,
            CONF_DEVICES_TO_TOGGLE: [HEATER],
            CONF_NOTIFY_ENABLED: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Load Balancer (32A)"
    assert result["data"] == {
        CONF_FUSE_SIZE: 32,
        CONF_PHASE_1_SENSOR: "sensor.l1",
        CONF_ENABLED_PHASES: ["1"],
        CONF_AGGRESSIVENESS: "high",
        CONF_SPIKE_FILTER_TIME: 20,
        CONF_RESTORE_HEADROOM: 4.0,
        CONF_CHARGING_ENTITY: CHARGER,
        CONF_DEVICES_TO_TOGGLE: [HEATER],
        CONF_NOTIFY_ENABLED: True,
    }


async def _phases_step(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FUSE_SIZE: 25}
    )
    return result["flow_id"]


async def test_phases_requires_a_monitored_sensor(hass: HomeAssistant) -> None:
    """A phase must be both enabled and have a sensor."""
    flow_id = await _phases_step(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_ENABLED_PHASES: ["1", "2", "3"]}
    )
    assert result["errors"] == {"base": "no_monitored_phase"}

    # Sensor on L2, but only L1 enabled → still nothing monitored
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_PHASE_2_SENSOR: "sensor.l2", CONF_ENABLED_PHASES: ["1"]}
    )
    assert result["errors"] == {"base": "no_monitored_phase"}

    # Single-phase home with the default "all phases" selection is accepted
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_PHASE_1_SENSOR: "sensor.l1", CONF_ENABLED_PHASES: ["1", "2", "3"]}
    )
    assert result["step_id"] == "behavior"


async def test_phases_rejects_duplicate_sensor(hass: HomeAssistant) -> None:
    """The same sensor cannot be assigned to two phases."""
    flow_id = await _phases_step(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {
            CONF_PHASE_1_SENSOR: "sensor.l1",
            CONF_PHASE_2_SENSOR: "sensor.l1",
            CONF_ENABLED_PHASES: ["1", "2"],
        },
    )
    assert result["errors"] == {"base": "duplicate_sensor"}


async def test_options_menu_opens(hass: HomeAssistant, coordinator) -> None:
    """Regression: the options dialog used to crash on current Home Assistant."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "thresholds",
        "sensors",
        "load_control",
        "notifications",
        "advanced",
    ]


async def _open_options_step(hass: HomeAssistant, step: str) -> dict:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_options_thresholds_apply_live(hass: HomeAssistant, coordinator) -> None:
    """Saved options take effect immediately without reloading the entry."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await _open_options_step(hass, "thresholds")
    assert result["step_id"] == "thresholds"
    assert result["description_placeholders"]["very_high_trigger"] == "20.0"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_FUSE_SIZE: 35,
            CONF_AGGRESSIVENESS: "low",
            CONF_SPIKE_FILTER_TIME: 15,
            CONF_RESTORE_HEADROOM: 5.0,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_FUSE_SIZE] == 35
    # Same coordinator object → no reload happened
    assert entry.runtime_data is coordinator
    assert coordinator.trigger_current == 35 * 0.95
    assert hass.states.get("sensor.dynamic_load_balancer_trigger_threshold").state == "33.25"


async def test_options_sensors_can_be_changed_and_cleared(
    hass: HomeAssistant, coordinator
) -> None:
    """Sensors were locked after setup before; now they can be edited."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await _open_options_step(hass, "sensors")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENABLED_PHASES: ["1", "2", "3"]},
    )
    assert result["errors"] == {"base": "no_monitored_phase"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_PHASE_1_SENSOR: "sensor.new_l1", CONF_ENABLED_PHASES: ["1"]},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Cleared fields are stored as None so they override the setup data
    assert entry.options[CONF_PHASE_2_SENSOR] is None
    assert coordinator.config[CONF_PHASE_1_SENSOR] == "sensor.new_l1"
    assert coordinator.config[CONF_PHASE_3_SENSOR] is None
    assert coordinator.monitored_phases == [1]


async def test_options_load_control(hass: HomeAssistant, coordinator) -> None:
    """Charger, devices and restore strategy are editable; charger can be removed."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await _open_options_step(hass, "load_control")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DEVICES_TO_TOGGLE: [HEATER],
            CONF_DEVICE_LOAD: 10.0,
            CONF_RESTORE_PRIORITY: "devices_first",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CHARGING_ENTITY] is None
    assert coordinator.config[CONF_CHARGING_ENTITY] is None
    assert coordinator.config[CONF_DEVICES_TO_TOGGLE] == [HEATER]
    assert coordinator.config[CONF_RESTORE_PRIORITY] == "devices_first"


async def test_options_notifications_and_advanced(
    hass: HomeAssistant, coordinator
) -> None:
    """Notification and advanced timing steps store their values."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await _open_options_step(hass, "notifications")
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_ENABLED: False, CONF_NOTIFY_ON_RESTORE: True}
    )
    result = await _open_options_step(hass, "advanced")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_RESTORE_SETTLE_TIME: 120,
            CONF_RESTORE_STEP_TIME: 30,
            CONF_REDUCTION_INTERVAL: 20,
            CONF_CHARGER_MARGIN: 1.0,
        },
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_NOTIFY_ENABLED] is False
    assert entry.options[CONF_NOTIFY_ON_RESTORE] is True
    assert entry.options[CONF_RESTORE_SETTLE_TIME] == 120
    assert entry.options[CONF_CHARGER_MARGIN] == 1.0
    # Earlier options are kept when another step is saved
    assert hass.states.get("switch.dynamic_load_balancer_notifications").state == "off"


async def test_entry_without_options_flow_data(hass: HomeAssistant) -> None:
    """An entry created by an older version (data only) still loads."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FUSE_SIZE: 25,
            CONF_PHASE_1_SENSOR: "sensor.l1",
            CONF_ENABLED_PHASES: ["1", "2", "3"],
            CONF_AGGRESSIVENESS: "medium",
            CONF_SPIKE_FILTER_TIME: 30,
            CONF_NOTIFY_ENABLED: True,
        },
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.l1", "5")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dynamic_load_balancer_status").state == "monitoring"
