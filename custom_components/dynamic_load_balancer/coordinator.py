"""Coordinator for Dynamic Load Balancer."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    AGGRESSIVENESS_LEVELS,
    CONF_AGGRESSIVENESS,
    CONF_CHARGING_ENTITY,
    CONF_DEVICES_TO_TOGGLE,
    CONF_ENABLED_PHASES,
    CONF_FUSE_SIZE,
    CONF_PHASE_1_SENSOR,
    CONF_PHASE_2_SENSOR,
    CONF_PHASE_3_SENSOR,
    CONF_SPIKE_FILTER_TIME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class LoadBalancerCoordinator(DataUpdateCoordinator):
    """Class to manage load balancing."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=5),
        )
        self.entry = entry
        # Merge base data with any user-saved options (options take precedence)
        self.config = {**entry.data, **entry.options}
        
        # Overload tracking per phase
        self.overload_start: dict[int, Any] = {1: None, 2: None, 3: None}
        self.last_action_time: Any = None
        self.charging_original_value: float | None = None
        self.disabled_devices: set[str] = set()
        
        # State tracking
        self.is_managing_load = False
        self.enabled = True  # Controlled by switch entity

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from sensors and manage load."""
        phase_currents = await self._get_phase_currents()
        
        fuse_size = self.config[CONF_FUSE_SIZE]
        aggressiveness = self.config.get(CONF_AGGRESSIVENESS, "medium")
        threshold = AGGRESSIVENESS_LEVELS[aggressiveness]
        trigger_current = fuse_size * threshold
        
        enabled_phases = [int(p) for p in self.config.get(CONF_ENABLED_PHASES, ["1", "2", "3"])]
        
        # Check for overloads on enabled phases
        overloaded_phases = []
        for phase in enabled_phases:
            if phase in phase_currents:
                current = phase_currents[phase]
                if current is not None and current > trigger_current:
                    overloaded_phases.append(phase)
                    
                    # Track overload duration
                    if self.overload_start[phase] is None:
                        self.overload_start[phase] = dt_util.utcnow()
                        _LOGGER.info(f"Phase {phase} overload started: {current:.1f}A > {trigger_current:.1f}A")
                else:
                    # Reset overload tracking if below threshold
                    if self.overload_start[phase] is not None:
                        _LOGGER.info(f"Phase {phase} overload cleared: {current:.1f}A <= {trigger_current:.1f}A")
                    self.overload_start[phase] = None
        
        # Apply spike filter - only act on sustained overloads
        sustained_overloads = []
        spike_filter_seconds = self.config.get(CONF_SPIKE_FILTER_TIME, 30)
        
        for phase in overloaded_phases:
            if self.overload_start[phase] is not None:
                duration = (dt_util.utcnow() - self.overload_start[phase]).total_seconds()
                _LOGGER.debug(f"Phase {phase} overload duration: {duration:.1f}s / {spike_filter_seconds}s")
                if duration >= spike_filter_seconds:
                    sustained_overloads.append(phase)
                    _LOGGER.warning(f"Phase {phase} sustained overload detected after {duration:.1f}s")
        
        # Check if load balancing is enabled
        is_enabled = self.enabled
        
        # Manage load if sustained overload detected AND switch is enabled
        if sustained_overloads and is_enabled:
            await self._reduce_load(sustained_overloads, phase_currents, trigger_current)
            self.is_managing_load = True
        elif not overloaded_phases and self.is_managing_load:
            # Restore load when no overloads
            await self._restore_load()
            self.is_managing_load = False
        
        return {
            "phase_currents": phase_currents,
            "overloaded_phases": overloaded_phases,
            "sustained_overloads": sustained_overloads,
            "is_managing": self.is_managing_load,
            "fuse_size": fuse_size,
            "trigger_current": trigger_current,
            "charging_original_value": self.charging_original_value,
            "disabled_devices": list(self.disabled_devices),
        }

    async def _get_phase_currents(self) -> dict[int, float | None]:
        """Get current readings from all phase sensors."""
        currents = {}
        
        for phase_num, conf_key in [
            (1, CONF_PHASE_1_SENSOR),
            (2, CONF_PHASE_2_SENSOR),
            (3, CONF_PHASE_3_SENSOR),
        ]:
            sensor_id = self.config.get(conf_key)
            if sensor_id:
                state = self.hass.states.get(sensor_id)
                if state and state.state not in ("unknown", "unavailable"):
                    try:
                        currents[phase_num] = float(state.state)
                    except (ValueError, TypeError):
                        _LOGGER.warning(f"Invalid current value for phase {phase_num}: {state.state}")
                        currents[phase_num] = None
                else:
                    currents[phase_num] = None
        
        return currents

    async def _reduce_load(
        self,
        overloaded_phases: list[int],
        phase_currents: dict[int, float | None],
        trigger_current: float,
    ) -> None:
        """Reduce electrical load by adjusting charging current and toggling devices."""
        # Prevent too frequent actions
        if self.last_action_time:
            time_since_last = (dt_util.utcnow() - self.last_action_time).total_seconds()
            if time_since_last < 10:  # Minimum 10 seconds between actions
                return

        # Calculate how much each phase exceeds the trigger threshold
        total_overload = 0.0
        for phase in overloaded_phases:
            if phase in phase_currents and phase_currents[phase] is not None:
                overload = phase_currents[phase] - trigger_current
                total_overload = max(total_overload, overload)

        _LOGGER.info(
            f"Overload detected on phases {overloaded_phases}. "
            f"Maximum overload above trigger: {total_overload:.1f}A. Taking action..."
        )

        # Step 1: Reduce charging current
        charging_entity = self.config.get(CONF_CHARGING_ENTITY)
        if charging_entity and total_overload > 0:
            reduction = await self._reduce_charging_current(charging_entity, total_overload)
            total_overload -= reduction
            _LOGGER.info(f"Reduced charging current by {reduction:.1f}A")

        # Step 2: Toggle off devices if still overloaded
        if total_overload > 0:
            devices = self.config.get(CONF_DEVICES_TO_TOGGLE, [])
            _LOGGER.info(f"Still overloaded by {total_overload:.1f}A, checking {len(devices)} devices")
            for device in devices:
                if device not in self.disabled_devices:
                    state = self.hass.states.get(device)
                    if state and state.state == "on":
                        try:
                            await self.hass.services.async_call(
                                "homeassistant",
                                "turn_off",
                                {"entity_id": device},
                                blocking=True,
                            )
                            self.disabled_devices.add(device)
                            _LOGGER.info(f"Turned off device: {device}")
                            # Rough estimate: assume 5A load reduction per device
                            total_overload -= 5
                            if total_overload <= 0:
                                break
                        except Exception as e:
                            _LOGGER.error(f"Failed to turn off {device}: {e}")
                    else:
                        _LOGGER.debug(f"Device {device} is already off or unavailable, skipping")

        self.last_action_time = dt_util.utcnow()

    async def _reduce_charging_current(self, entity_id: str, overload_amps: float) -> float:
        """Reduce charging current and return amount reduced.
        
        Reads min/max values from the entity attributes to determine valid range.
        Works with any number entity (Tesla, Wallbox, etc.).
        """
        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.error(f"Charging entity {entity_id} not found")
            return 0
            
        if state.state in ("unknown", "unavailable"):
            _LOGGER.warning(f"Charging entity {entity_id} is {state.state}")
            return 0
        
        try:
            current_value = float(state.state)
        except (ValueError, TypeError) as e:
            _LOGGER.error(f"Cannot parse charging current value '{state.state}': {e}")
            return 0
        
        # Read min/max from entity attributes
        min_value = state.attributes.get("min", 5)  # Default to 5 if not specified
        max_value = state.attributes.get("max", 32)  # Default to 32 if not specified
        step = state.attributes.get("step", 1)  # Default step of 1
        
        _LOGGER.debug(f"Charging entity {entity_id}: current={current_value}, min={min_value}, max={max_value}, step={step}")
        
        # Store original value if this is first reduction
        if self.charging_original_value is None:
            self.charging_original_value = current_value
            _LOGGER.info(f"Storing original charging value: {current_value}A (range: {min_value}-{max_value}A)")
        
        # Calculate new value with margin
        target_reduction = min(overload_amps + 2, current_value - min_value)  # +2A margin
        new_value = max(min_value, current_value - target_reduction)
        
        # Round to step if specified
        if step > 0:
            new_value = round(new_value / step) * step
            new_value = max(min_value, min(max_value, new_value))
        
        if new_value < current_value:
            _LOGGER.info(f"Attempting to reduce charging from {current_value}A to {new_value}A")
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": new_value},
                    blocking=True,
                )
                _LOGGER.info(f"✅ Successfully reduced charging to {new_value}A")
                return current_value - new_value
            except Exception as e:
                _LOGGER.error(f"Failed to set charging current: {e}")
                return 0
        else:
            _LOGGER.debug(f"Charging already at minimum or target: {current_value}A")
        
        return 0

    async def _restore_load(self) -> None:
        """Restore charging current and devices after overload clears."""
        _LOGGER.info("Overload cleared. Restoring load...")
        
        # Restore charging current gradually
        charging_entity = self.config.get(CONF_CHARGING_ENTITY)
        if charging_entity and self.charging_original_value is not None:
            state = self.hass.states.get(charging_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current_value = float(state.state)
                    step = state.attributes.get("step", 1)
                    
                    # Increase by 2A or 1 step at a time to avoid immediate re-overload
                    increment = max(2, step)
                    new_value = min(current_value + increment, self.charging_original_value)
                    
                    if new_value > current_value:
                        await self.hass.services.async_call(
                            "number",
                            "set_value",
                            {"entity_id": charging_entity, "value": new_value},
                            blocking=True,
                        )
                        _LOGGER.info(f"Increased charging current to {new_value}A")
                    
                    # Reset tracking if fully restored
                    if new_value >= self.charging_original_value:
                        self.charging_original_value = None
                        
                except (ValueError, TypeError):
                    pass
        
        # Re-enable disabled devices
        for device in list(self.disabled_devices):
            try:
                await self.hass.services.async_call(
                    "homeassistant",
                    "turn_on",
                    {"entity_id": device},
                    blocking=True,
                )
                _LOGGER.info(f"Restored device: {device}")
            except Exception as e:
                _LOGGER.error(f"Failed to restore device {device}: {e}")
        
        self.disabled_devices.clear()
        self.last_action_time = dt_util.utcnow()
