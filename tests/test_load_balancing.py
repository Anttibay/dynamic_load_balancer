"""Tests for detection, reduction and restoration."""
from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.dynamic_load_balancer.const import (
    CONF_DEVICES_TO_TOGGLE,
    CONF_NOTIFY_ON_RESTORE,
    CONF_NOTIFY_TARGET,
    CONF_RESTORE_PRIORITY,
    DOMAIN,
)
from custom_components.dynamic_load_balancer.coordinator import LoadBalancerCoordinator
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.helpers import device_registry as dr

from .conftest import AC, CHARGER, HEATER, advance, set_currents

ENTITY_IDS = [
    "switch.dynamic_load_balancer",
    "switch.dynamic_load_balancer_notifications",
    "sensor.dynamic_load_balancer_status",
    "sensor.dynamic_load_balancer_available_headroom",
    "sensor.dynamic_load_balancer_trigger_threshold",
    "sensor.dynamic_load_balancer_overload_events",
    "sensor.dynamic_load_balancer_last_overload",
    "binary_sensor.dynamic_load_balancer_overload",
    "binary_sensor.dynamic_load_balancer_load_reduced",
    "number.dynamic_load_balancer_fuse_size",
    "number.dynamic_load_balancer_spike_filter",
    "number.dynamic_load_balancer_restore_headroom",
    "select.dynamic_load_balancer_aggressiveness",
    "button.dynamic_load_balancer_restore_load_now",
]


def _status(hass: HomeAssistant) -> str:
    return hass.states.get("sensor.dynamic_load_balancer_status").state


def _notifications(hass: HomeAssistant) -> dict:
    return persistent_notification._async_get_or_create_notifications(hass)


async def _overload_until_reduced(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, l1: float
) -> None:
    """Overload L1 long enough to pass the 30 s spike filter."""
    set_currents(hass, l1)
    await advance(hass, freezer)
    await advance(hass, freezer, 30)


async def test_entities_created(hass: HomeAssistant, coordinator) -> None:
    """All entities exist with sensible initial states."""
    for entity_id in ENTITY_IDS:
        assert hass.states.get(entity_id) is not None, entity_id
    assert hass.states.get("switch.dynamic_load_balancer").state == "on"
    assert _status(hass) == "monitoring"
    assert hass.states.get("sensor.dynamic_load_balancer_available_headroom").state == "12.5"
    assert hass.states.get("select.dynamic_load_balancer_aggressiveness").state == "medium"
    assert hass.states.get("number.dynamic_load_balancer_fuse_size").state == "25.0"


async def test_spike_is_ignored(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """An overload shorter than the spike filter causes no action."""
    set_currents(hass, 25.0)
    await advance(hass, freezer)
    await advance(hass, freezer, 20)
    assert _status(hass) == "overload_pending"
    assert hass.states.get("binary_sensor.dynamic_load_balancer_overload").state == "on"

    set_currents(hass, 10.0)
    await advance(hass, freezer, 5)
    assert _status(hass) == "monitoring"
    assert service_calls == []


async def test_sustained_overload_reduces_charger(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """The charger is reduced by overload + margin, snapped down to its step."""
    await _overload_until_reduced(hass, freezer, 25.0)  # 2.5 A over 22.5 A trigger

    # 16 − (2.5 + 2) = 11.5 → snapped down to 11 (rounding would give 12)
    assert service_calls == [("input_number.set_value", {"entity_id": CHARGER, "value": 11.0})]
    assert _status(hass) == "reducing"
    assert coordinator.charging_original_value == 16.0
    assert hass.states.get("binary_sensor.dynamic_load_balancer_load_reduced").state == "on"
    assert hass.states.get("sensor.dynamic_load_balancer_overload_events").state == "1"
    assert hass.states.get("sensor.dynamic_load_balancer_last_overload").state != "unknown"
    assert "Overload on L1: 25.0 A" in _notifications(hass)[
        f"{DOMAIN}_{coordinator.entry.entry_id}_overload"
    ]["message"]


async def test_devices_shed_in_order_and_climate_mode_kept(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """When the charger is not enough, devices go off in order — climate via HVAC mode."""
    await _overload_until_reduced(hass, freezer, 40.0)  # 17.5 A over

    assert service_calls == [
        ("input_number.set_value", {"entity_id": CHARGER, "value": 6.0}),
        ("homeassistant.turn_off", {"entity_id": HEATER}),
        ("climate.set_hvac_mode", {"entity_id": AC, "hvac_mode": "off"}),
    ]
    assert coordinator.disabled_devices == {HEATER: "on", AC: "heat"}


async def test_single_device_when_enough(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """Only as many devices as the estimated load requires are turned off."""
    hass.states.async_set(CHARGER, "6.0", {"min": 6, "max": 32, "step": 1})
    await _overload_until_reduced(hass, freezer, 25.0)
    assert service_calls == [("homeassistant.turn_off", {"entity_id": HEATER})]


async def test_gradual_restoration_charger_first(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """After settling, the charger ramps up step by step, then devices return LIFO."""
    await _overload_until_reduced(hass, freezer, 40.0)
    service_calls.clear()

    set_currents(hass, 10.0)  # 12.5 A headroom
    await advance(hass, freezer, 5)
    assert _status(hass) == "settling"
    await advance(hass, freezer, 30)
    assert service_calls == []

    for expected in range(7, 17):
        await advance(hass, freezer, 60)
        assert service_calls[-1] == (
            "input_number.set_value", {"entity_id": CHARGER, "value": float(expected)}
        )
        assert _status(hass) == "restoring"
    assert coordinator.charging_original_value is None

    await advance(hass, freezer, 60)
    assert service_calls[-1] == ("climate.set_hvac_mode", {"entity_id": AC, "hvac_mode": "heat"})
    await advance(hass, freezer, 60)
    assert service_calls[-1] == ("homeassistant.turn_on", {"entity_id": HEATER})
    await advance(hass, freezer, 60)
    assert _status(hass) == "monitoring"
    assert hass.states.get("binary_sensor.dynamic_load_balancer_load_reduced").state == "off"


async def test_restoration_devices_first(
    hass: HomeAssistant,
    coordinator: LoadBalancerCoordinator,
    freezer: FrozenDateTimeFactory,
    service_calls,
) -> None:
    """With devices-first priority, devices come back before the charger ramps."""
    await coordinator.async_set_option(CONF_RESTORE_PRIORITY, "devices_first")
    await hass.async_block_till_done()
    await _overload_until_reduced(hass, freezer, 40.0)
    service_calls.clear()

    set_currents(hass, 10.0)
    await advance(hass, freezer, 5)
    await advance(hass, freezer, 60)
    assert service_calls[-1] == ("climate.set_hvac_mode", {"entity_id": AC, "hvac_mode": "heat"})
    await advance(hass, freezer, 60)
    assert service_calls[-1] == ("homeassistant.turn_on", {"entity_id": HEATER})
    await advance(hass, freezer, 60)
    assert service_calls[-1] == ("input_number.set_value", {"entity_id": CHARGER, "value": 7.0})


async def test_charger_not_abandoned_when_headroom_is_tight(
    hass: HomeAssistant,
    coordinator: LoadBalancerCoordinator,
    freezer: FrozenDateTimeFactory,
    service_calls,
) -> None:
    """Too little room for a step keeps the original value instead of dropping it."""
    await _overload_until_reduced(hass, freezer, 25.0)  # charger 16 → 11
    service_calls.clear()

    set_currents(hass, 19.0)  # 3.5 A headroom: enough to settle, not for +1 A + 3 A
    await advance(hass, freezer, 5)
    await advance(hass, freezer, 60)
    await advance(hass, freezer, 60)
    assert service_calls == []
    assert _status(hass) == "waiting_headroom"
    assert coordinator.charging_original_value == 16.0

    # The big load went away. Headroom never dropped below 3 A, so the settle
    # timer is still satisfied and ramping resumes on the next cycle.
    set_currents(hass, 10.0)
    await advance(hass, freezer, 5)
    assert service_calls == [("input_number.set_value", {"entity_id": CHARGER, "value": 12.0})]


async def test_device_needs_room_for_its_estimated_load(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """A device is only re-enabled with headroom for device load + restore headroom."""
    hass.states.async_set(CHARGER, "6.0", {"min": 6, "max": 32, "step": 1})
    await _overload_until_reduced(hass, freezer, 25.0)  # heater off
    service_calls.clear()

    set_currents(hass, 16.0)  # 6.5 A headroom < 5 + 3
    await advance(hass, freezer, 5)
    await advance(hass, freezer, 60)
    assert service_calls == []
    assert _status(hass) == "waiting_headroom"

    set_currents(hass, 14.0)  # 8.5 A headroom
    await advance(hass, freezer, 5)
    await advance(hass, freezer, 60)
    assert service_calls == [("homeassistant.turn_on", {"entity_id": HEATER})]


async def test_transient_overload_pauses_restoration(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """A short spike during restoration resets the settle timer."""
    await _overload_until_reduced(hass, freezer, 25.0)
    service_calls.clear()
    set_currents(hass, 10.0)
    await advance(hass, freezer, 5)
    await advance(hass, freezer, 50)
    set_currents(hass, 23.0)
    await advance(hass, freezer, 5)
    assert _status(hass) == "waiting_headroom"
    set_currents(hass, 10.0)
    await advance(hass, freezer, 5)
    await advance(hass, freezer, 50)
    assert service_calls == []  # Settle timer restarted
    await advance(hass, freezer, 15)
    assert len(service_calls) == 1


async def test_state_survives_reload(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """Reduced load is still restored after a reload / restart."""
    await _overload_until_reduced(hass, freezer, 40.0)
    entry = coordinator.entry

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new_coordinator = entry.runtime_data
    assert new_coordinator is not coordinator
    assert new_coordinator.charging_original_value == 16.0
    assert new_coordinator.disabled_devices == {HEATER: "on", AC: "heat"}
    assert new_coordinator.overload_count == 1
    assert hass.states.get("sensor.dynamic_load_balancer_last_overload").state != "unknown"

    service_calls.clear()
    set_currents(hass, 10.0)
    await advance(hass, freezer, 5)
    await advance(hass, freezer, 60)
    assert service_calls == [("input_number.set_value", {"entity_id": CHARGER, "value": 7.0})]


async def test_switch_off_restores_everything(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """Disabling balancing hands back all load immediately."""
    await _overload_until_reduced(hass, freezer, 40.0)
    service_calls.clear()

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.dynamic_load_balancer"}, blocking=True
    )
    assert service_calls == [
        ("input_number.set_value", {"entity_id": CHARGER, "value": 16.0}),
        ("climate.set_hvac_mode", {"entity_id": AC, "hvac_mode": "heat"}),
        ("homeassistant.turn_on", {"entity_id": HEATER}),
    ]
    assert _status(hass) == "disabled"

    # Still overloaded, but disabled → no action
    service_calls.clear()
    await advance(hass, freezer, 60)
    assert service_calls == []


async def test_restore_button(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """The button restores now; a persisting overload is re-confirmed first."""
    await _overload_until_reduced(hass, freezer, 25.0)
    service_calls.clear()

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.dynamic_load_balancer_restore_load_now"},
        blocking=True,
    )
    assert service_calls == [("input_number.set_value", {"entity_id": CHARGER, "value": 16.0})]
    assert _status(hass) == "overload_pending"  # Spike filter restarted


async def test_config_entities_change_settings_live(
    hass: HomeAssistant, coordinator: LoadBalancerCoordinator
) -> None:
    """Number/select/switch entities update the options without a reload."""
    entry = coordinator.entry
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.dynamic_load_balancer_fuse_size", "value": 32},
        blocking=True,
    )
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.dynamic_load_balancer_aggressiveness", "option": "very_high"},
        blocking=True,
    )
    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": "switch.dynamic_load_balancer_notifications"}, blocking=True,
    )
    await hass.async_block_till_done()

    assert entry.runtime_data is coordinator
    assert entry.options["fuse_size"] == 32
    assert entry.options["aggressiveness"] == "very_high"
    assert entry.options["notify_enabled"] is False
    assert coordinator.trigger_current == 32 * 0.8
    assert hass.states.get("number.dynamic_load_balancer_fuse_size").state == "32.0"
    assert hass.states.get("sensor.dynamic_load_balancer_trigger_threshold").state == "25.6"


async def test_sensor_unavailable_pauses(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """No action is taken on incomplete data."""
    set_currents(hass, 30.0)
    hass.states.async_set("sensor.l2", "unavailable")
    await advance(hass, freezer)
    await advance(hass, freezer, 60)
    assert _status(hass) == "sensor_unavailable"
    assert service_calls == []


async def test_milliampere_sensor(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory
) -> None:
    """Sensors reporting mA are converted to A."""
    set_currents(hass, 10000.0, 5000.0, 5000.0, unit="mA")
    await advance(hass, freezer)
    assert hass.states.get("sensor.dynamic_load_balancer_available_headroom").state == "12.5"


async def test_new_overload_notifies_while_restoring(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory
) -> None:
    """A fresh overload long after the first is a new event, even mid-restoration."""
    await _overload_until_reduced(hass, freezer, 25.0)
    set_currents(hass, 19.0)  # Tight headroom: the charger stays partly reduced
    await advance(hass, freezer, 5)
    await advance(hass, freezer, 30)
    assert coordinator.overload_count == 1  # Same event while flickering

    await advance(hass, freezer, 400)
    await _overload_until_reduced(hass, freezer, 25.0)
    assert coordinator.overload_count == 2


async def test_restore_notification(
    hass: HomeAssistant,
    coordinator: LoadBalancerCoordinator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Optionally notify once everything is back."""
    await coordinator.async_set_option(CONF_NOTIFY_ON_RESTORE, True)
    await coordinator.async_set_option(CONF_DEVICES_TO_TOGGLE, [])
    await hass.async_block_till_done()
    hass.states.async_set(CHARGER, "15.0", {"min": 6, "max": 32, "step": 1})
    await _overload_until_reduced(hass, freezer, 23.0)  # 15 → 12

    set_currents(hass, 5.0)
    for _ in range(6):
        await advance(hass, freezer, 60)
    notification = _notifications(hass)[f"{DOMAIN}_{coordinator.entry.entry_id}_overload"]
    assert notification["title"] == "✅ Load Restored"


async def test_mobile_notification(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    devices,
    service_calls,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The notify service is resolved from the mobile_app config entry."""
    mobile_entry = MockConfigEntry(domain="mobile_app", data={"device_name": "Antti's Pixel"})
    mobile_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mobile_entry.entry_id,
        identifiers={("mobile_app", "abc")},
        name="Renamed phone",
    )
    pushes: list[ServiceCall] = []

    async def _notify(call: ServiceCall) -> None:
        pushes.append(call)

    hass.services.async_register("notify", "mobile_app_antti_s_pixel", _notify)
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_NOTIFY_TARGET: device.id}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await _overload_until_reduced(hass, freezer, 25.0)
    await hass.async_block_till_done()
    assert len(pushes) == 1
    assert pushes[0].data["title"] == "⚡ Electrical Overload Detected"


async def test_remove_entry_restores_load(
    hass: HomeAssistant, coordinator, freezer: FrozenDateTimeFactory, service_calls
) -> None:
    """Deleting the integration mid-overload does not leave devices off."""
    await _overload_until_reduced(hass, freezer, 40.0)
    service_calls.clear()

    assert await hass.config_entries.async_remove(coordinator.entry.entry_id)
    await hass.async_block_till_done()
    assert ("homeassistant.turn_on", {"entity_id": HEATER}) in service_calls
    assert ("input_number.set_value", {"entity_id": CHARGER, "value": 16.0}) in service_calls


async def test_diagnostics(hass: HomeAssistant, coordinator) -> None:
    """Diagnostics redact the notification device."""
    from custom_components.dynamic_load_balancer.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await coordinator.async_set_option(CONF_NOTIFY_TARGET, "device-123")
    diag = await async_get_config_entry_diagnostics(hass, coordinator.entry)
    assert diag["config"][CONF_NOTIFY_TARGET] == "**REDACTED**"
    assert diag["data"]["status"] == "monitoring"


async def test_switch_state_restored_after_restart(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    devices,
    service_calls,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A balancer switched off before a restart stays off."""
    mock_restore_cache(hass, [State("switch.dynamic_load_balancer", "off")])
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("switch.dynamic_load_balancer").state == "off"
    await _overload_until_reduced(hass, freezer, 40.0)
    assert _status(hass) == "disabled"
    assert service_calls == []


async def test_water_heater_operation_mode_restored(
    hass: HomeAssistant,
    coordinator: LoadBalancerCoordinator,
    freezer: FrozenDateTimeFactory,
    service_calls,
) -> None:
    """Water heaters are switched off via operation mode and get their mode back."""
    hass.states.async_set(
        "water_heater.boiler", "eco", {"operation_list": ["off", "eco", "performance"]}
    )
    hass.states.async_set(CHARGER, "6.0", {"min": 6, "max": 32, "step": 1})
    await coordinator.async_set_option(CONF_DEVICES_TO_TOGGLE, ["water_heater.boiler"])
    await hass.async_block_till_done()

    await _overload_until_reduced(hass, freezer, 25.0)
    assert service_calls[-1] == (
        "water_heater.set_operation_mode",
        {"entity_id": "water_heater.boiler", "operation_mode": "off"},
    )

    set_currents(hass, 5.0)
    await advance(hass, freezer, 5)
    await advance(hass, freezer, 60)
    assert service_calls[-1] == (
        "water_heater.set_operation_mode",
        {"entity_id": "water_heater.boiler", "operation_mode": "eco"},
    )
