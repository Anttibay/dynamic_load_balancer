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
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_TARGET,
    CONF_PHASE_1_SENSOR,
    CONF_PHASE_2_SENSOR,
    CONF_PHASE_3_SENSOR,
    CONF_SPIKE_FILTER_TIME,
    DEFAULT_NOTIFY_ENABLED,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# How much headroom below the trigger threshold must exist before restoration begins.
# e.g. if trigger is 22.5 A and this is 3.0, current must be <= 19.5 A on all phases.
RESTORE_MIN_HEADROOM = 3.0   # Amperes

# Headroom must be continuously observed for this many seconds before the first
# restoration step is taken. Prevents restoring into a situation that just settled.
RESTORE_SETTLE_SECS = 60     # seconds

# Minimum wait between consecutive restoration steps (charger increment or device
# re-enable). Gives the system time to observe the effect of each step.
RESTORE_STEP_SECS = 60       # seconds


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

        # Reduction rate limiting
        self.last_action_time: Any = None

        # Restoration state
        self.charging_original_value: float | None = None
        self.disabled_devices: set[str] = set()
        self.restore_headroom_since: Any = None  # When sufficient headroom was first seen
        self.last_restore_step_time: Any = None  # When the last restoration step was taken

        # Last overload event — stored for the sensor and for deduplicating notifications
        self.last_triggered_time: Any = None
        self.last_triggered_phases: list[int] = []
        self.last_triggered_peak: float | None = None
        self.last_triggered_threshold: float | None = None

        # Overall state
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

        # ── Phase overload detection ──────────────────────────────────────────
        overloaded_phases = []
        for phase in enabled_phases:
            if phase in phase_currents:
                current = phase_currents[phase]
                if current is not None and current > trigger_current:
                    overloaded_phases.append(phase)
                    if self.overload_start[phase] is None:
                        self.overload_start[phase] = dt_util.utcnow()
                        _LOGGER.info(
                            "Phase %d overload started: %.1fA > %.1fA",
                            phase, current, trigger_current,
                        )
                else:
                    if self.overload_start[phase] is not None:
                        _LOGGER.info(
                            "Phase %d overload cleared: %.1fA <= %.1fA",
                            phase, current, trigger_current,
                        )
                    self.overload_start[phase] = None

        # Apply spike filter — only act on sustained overloads
        sustained_overloads = []
        spike_filter_seconds = self.config.get(CONF_SPIKE_FILTER_TIME, 30)
        for phase in overloaded_phases:
            if self.overload_start[phase] is not None:
                duration = (dt_util.utcnow() - self.overload_start[phase]).total_seconds()
                _LOGGER.debug(
                    "Phase %d overload duration: %.1fs / %ss",
                    phase, duration, spike_filter_seconds,
                )
                if duration >= spike_filter_seconds:
                    sustained_overloads.append(phase)
                    _LOGGER.warning(
                        "Phase %d sustained overload after %.1fs", phase, duration
                    )

        # ── Load management decision ──────────────────────────────────────────
        is_enabled = self.enabled

        if sustained_overloads and is_enabled:
            # Detect the moment an overload event begins (transition into managing state)
            new_event = not self.is_managing_load
            if new_event:
                peak_current = max(
                    (phase_currents[p] for p in sustained_overloads if phase_currents.get(p) is not None),
                    default=0.0,
                )
                self.last_triggered_time = dt_util.utcnow()
                self.last_triggered_phases = list(sustained_overloads)
                self.last_triggered_peak = peak_current
                self.last_triggered_threshold = trigger_current
                await self._send_overload_notification(
                    sustained_overloads, phase_currents, trigger_current, peak_current
                )

            # Active overload: reduce load and reset restoration tracking
            await self._reduce_load(sustained_overloads, phase_currents, trigger_current)
            self.is_managing_load = True
            self.restore_headroom_since = None

        elif is_enabled and (
            self.is_managing_load
            or self.disabled_devices
            or self.charging_original_value is not None
        ):
            if overloaded_phases:
                # Even a transient spike blocks restoration — reset the settle timer
                _LOGGER.debug(
                    "Transient overload on phase(s) %s — pausing restoration",
                    overloaded_phases,
                )
                self.restore_headroom_since = None
            else:
                # No overload at all: check whether headroom is sufficient to restore
                await self._maybe_restore_load(phase_currents, trigger_current, enabled_phases)

        return {
            "phase_currents": phase_currents,
            "overloaded_phases": overloaded_phases,
            "sustained_overloads": sustained_overloads,
            "is_managing": self.is_managing_load,
            "fuse_size": fuse_size,
            "trigger_current": trigger_current,
            "charging_original_value": self.charging_original_value,
            "disabled_devices": list(self.disabled_devices),
            "restore_headroom_since": self.restore_headroom_since,
            "last_restore_step_time": self.last_restore_step_time,
            # Last overload event — consumed by the sensor
            "last_overloaded_phases": self.last_triggered_phases,
            "last_peak_current": self.last_triggered_peak,
            "trigger_current_at_event": self.last_triggered_threshold,
        }

    # ── Sensor reading ────────────────────────────────────────────────────────

    async def _get_phase_currents(self) -> dict[int, float | None]:
        """Get current readings from all phase sensors."""
        currents: dict[int, float | None] = {}
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
                        _LOGGER.warning(
                            "Invalid current value for phase %d: %s",
                            phase_num, state.state,
                        )
                        currents[phase_num] = None
                else:
                    currents[phase_num] = None
        return currents

    # ── Headroom helper ───────────────────────────────────────────────────────

    def _calculate_min_headroom(
        self,
        phase_currents: dict[int, float | None],
        trigger_current: float,
        enabled_phases: list[int],
    ) -> float:
        """Return the smallest headroom (trigger − current) across all enabled phases.

        A positive value means all phases are below the trigger. The smaller the
        number, the less room there is before the next overload.
        """
        min_headroom = float("inf")
        for phase in enabled_phases:
            current = phase_currents.get(phase)
            if current is not None:
                min_headroom = min(min_headroom, trigger_current - current)
        return min_headroom if min_headroom != float("inf") else 0.0

    # ── Notifications ─────────────────────────────────────────────────────────

    async def _send_overload_notification(
        self,
        overloaded_phases: list[int],
        phase_currents: dict[int, float | None],
        trigger_current: float,
        peak_current: float,
    ) -> None:
        """Send an overload notification via persistent_notification and optionally a mobile device.

        Both notification channels are skipped when notify_enabled is False.
        """
        if not self.config.get(CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED):
            _LOGGER.debug("Notifications disabled — skipping overload alert")
            return

        fuse_size = self.config[CONF_FUSE_SIZE]

        # Build a readable phase summary, e.g. "L1: 24.3 A, L2: 23.1 A"
        phase_parts = []
        for phase in overloaded_phases:
            current = phase_currents.get(phase)
            if current is not None:
                phase_parts.append(f"L{phase}: {current:.1f} A")
        phase_summary = ", ".join(phase_parts) if phase_parts else f"phase(s) {overloaded_phases}"

        title = "⚡ Electrical Overload Detected"
        message = (
            f"Overload on {phase_summary}. "
            f"Peak: {peak_current:.1f} A — trigger threshold: {trigger_current:.1f} A "
            f"({fuse_size} A fuse). "
            f"Dynamic Load Balancer is reducing load."
        )

        # 1. Always create a persistent HA notification (visible as a bell icon in HA)
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": title,
                    "message": message,
                    "notification_id": f"{DOMAIN}_overload",
                },
                blocking=False,
            )
        except Exception as exc:
            _LOGGER.error("Failed to create persistent notification: %s", exc)

        # 2. Optionally send to a configured mobile device (stored as device_id)
        device_id = self.config.get(CONF_NOTIFY_TARGET) or ""
        if device_id:
            service = await self._resolve_mobile_notify_service(device_id)
            if service:
                if not self.hass.services.has_service("notify", service):
                    _LOGGER.warning(
                        "Notify service 'notify.%s' not registered — "
                        "open the HA companion app on the device once to activate it",
                        service,
                    )
                else:
                    try:
                        await self.hass.services.async_call(
                            "notify",
                            service,
                            {"title": title, "message": message},
                            blocking=False,
                        )
                        _LOGGER.info("Overload notification sent to notify.%s", service)
                    except Exception as exc:
                        _LOGGER.error(
                            "Failed to send notification to notify.%s: %s", service, exc
                        )
            else:
                _LOGGER.warning(
                    "Could not resolve notify service for device_id '%s'", device_id
                )

    async def _resolve_mobile_notify_service(self, device_id: str) -> str | None:
        """Map a mobile_app device_id to its notify service name.

        First tries the mobile_app push registration data (most accurate).
        Falls back to slugifying the device registry name.
        """
        from homeassistant.util import slugify  # local import — always available

        # Primary: look up via mobile_app push registrations
        try:
            push_regs = (
                self.hass.data
                .get("mobile_app", {})
                .get("push_registrations", {})
            )
            for _, reg in push_regs.items():
                if reg.get("device_id") == device_id:
                    name = reg.get("device_name", "")
                    if name:
                        return f"mobile_app_{slugify(name)}"
        except Exception:
            pass

        # Fallback: slugify the HA device registry entry name
        try:
            from homeassistant.helpers import device_registry as dr
            device = dr.async_get(self.hass).async_get(device_id)
            if device and device.name:
                return f"mobile_app_{slugify(device.name)}"
        except Exception:
            pass

        return None

    # ── Load reduction ────────────────────────────────────────────────────────

    async def _reduce_load(
        self,
        overloaded_phases: list[int],
        phase_currents: dict[int, float | None],
        trigger_current: float,
    ) -> None:
        """Reduce electrical load by adjusting charging current and toggling devices."""
        # Rate-limit: minimum 10 s between reduction actions
        if self.last_action_time:
            elapsed = (dt_util.utcnow() - self.last_action_time).total_seconds()
            if elapsed < 10:
                return

        # How much above the trigger threshold is the worst phase?
        total_overload = 0.0
        for phase in overloaded_phases:
            current = phase_currents.get(phase)
            if current is not None:
                total_overload = max(total_overload, current - trigger_current)

        _LOGGER.info(
            "Overload on phase(s) %s — %.1fA above trigger. Taking action.",
            overloaded_phases, total_overload,
        )

        # Step 1: Reduce EV charging current first (fine-grained control)
        charging_entity = self.config.get(CONF_CHARGING_ENTITY)
        if charging_entity and total_overload > 0:
            reduction = await self._reduce_charging_current(charging_entity, total_overload)
            total_overload -= reduction
            _LOGGER.info("Reduced charging current by %.1fA", reduction)

        # Step 2: Toggle off devices if still overloaded
        if total_overload > 0:
            devices = self.config.get(CONF_DEVICES_TO_TOGGLE, [])
            _LOGGER.info(
                "Still overloaded by %.1fA — checking %d device(s)",
                total_overload, len(devices),
            )
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
                            _LOGGER.info("Turned off device: %s", device)
                            total_overload -= 5  # rough estimate per device
                            if total_overload <= 0:
                                break
                        except Exception as e:
                            _LOGGER.error("Failed to turn off %s: %s", device, e)
                    else:
                        _LOGGER.debug("Device %s already off — skipping", device)

        self.last_action_time = dt_util.utcnow()

    async def _reduce_charging_current(self, entity_id: str, overload_amps: float) -> float:
        """Reduce charging current by the overload amount plus a 2 A safety margin.

        Reads min/max/step from the entity's attributes — works with any EVSE
        (Tesla, Wallbox, go-e, etc.) that exposes a number entity for charge current.
        Returns the number of Amperes actually removed.
        """
        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.error("Charging entity %s not found", entity_id)
            return 0.0
        if state.state in ("unknown", "unavailable"):
            _LOGGER.warning("Charging entity %s is %s", entity_id, state.state)
            return 0.0
        try:
            current_value = float(state.state)
        except (ValueError, TypeError) as exc:
            _LOGGER.error("Cannot parse charging value '%s': %s", state.state, exc)
            return 0.0

        min_value = float(state.attributes.get("min", 5))
        max_value = float(state.attributes.get("max", 32))
        step = float(state.attributes.get("step", 1))

        _LOGGER.debug(
            "Charger %s: current=%.1fA  min=%.1f  max=%.1f  step=%.1f",
            entity_id, current_value, min_value, max_value, step,
        )

        # Store original value on first reduction so we know where to return to
        if self.charging_original_value is None:
            self.charging_original_value = current_value
            _LOGGER.info(
                "Stored original charging value: %.1fA (range %.1f–%.1fA)",
                current_value, min_value, max_value,
            )

        # Reduce by the overload plus a 2 A safety margin, but not below min
        target_reduction = min(overload_amps + 2.0, current_value - min_value)
        new_value = current_value - target_reduction

        # Snap to step grid
        if step > 0:
            new_value = round(new_value / step) * step
        new_value = max(min_value, min(max_value, new_value))

        if new_value < current_value:
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": new_value},
                    blocking=True,
                )
                _LOGGER.info(
                    "Charging reduced: %.1fA → %.1fA", current_value, new_value
                )
                return current_value - new_value
            except Exception as exc:
                _LOGGER.error("Failed to set charging current: %s", exc)
                return 0.0
        else:
            _LOGGER.debug("Charging already at minimum (%.1fA)", current_value)

        return 0.0

    # ── Cautious restoration ──────────────────────────────────────────────────

    async def _maybe_restore_load(
        self,
        phase_currents: dict[int, float | None],
        trigger_current: float,
        enabled_phases: list[int],
    ) -> None:
        """Cautiously restore reduced load when there is sufficient stable headroom.

        The restoration is gated by three conditions that must all be true:
        1. Headroom across every enabled phase >= RESTORE_MIN_HEADROOM
        2. That headroom has been continuously present for RESTORE_SETTLE_SECS
        3. At least RESTORE_STEP_SECS have passed since the previous restoration step

        When all three conditions are met, a single small step is taken (one charger
        increment OR one device re-enabled) and the cycle restarts so the system can
        observe the effect before the next step.
        """
        min_headroom = self._calculate_min_headroom(
            phase_currents, trigger_current, enabled_phases
        )

        # ── Gate 1: Is there enough headroom at all? ──────────────────────────
        if min_headroom < RESTORE_MIN_HEADROOM:
            if self.restore_headroom_since is not None:
                _LOGGER.debug(
                    "Headroom %.1fA < %.1fA minimum — resetting settle timer",
                    min_headroom, RESTORE_MIN_HEADROOM,
                )
                self.restore_headroom_since = None
            return

        # ── Gate 2: Has headroom been stable long enough? ─────────────────────
        if self.restore_headroom_since is None:
            self.restore_headroom_since = dt_util.utcnow()
            _LOGGER.info(
                "Headroom %.1fA detected — waiting %ds before restoring",
                min_headroom, RESTORE_SETTLE_SECS,
            )
            return

        settle_elapsed = (dt_util.utcnow() - self.restore_headroom_since).total_seconds()
        if settle_elapsed < RESTORE_SETTLE_SECS:
            _LOGGER.debug(
                "Settle timer: %.0fs / %ds (headroom %.1fA)",
                settle_elapsed, RESTORE_SETTLE_SECS, min_headroom,
            )
            return

        # ── Gate 3: Has enough time passed since the last restore step? ───────
        if self.last_restore_step_time is not None:
            step_elapsed = (dt_util.utcnow() - self.last_restore_step_time).total_seconds()
            if step_elapsed < RESTORE_STEP_SECS:
                _LOGGER.debug(
                    "Waiting %.0fs more before next restore step (headroom %.1fA)",
                    RESTORE_STEP_SECS - step_elapsed, min_headroom,
                )
                return

        # ── All gates passed: take one restoration step ───────────────────────
        await self._restore_one_step(phase_currents, trigger_current, min_headroom)

    async def _restore_one_step(
        self,
        phase_currents: dict[int, float | None],
        trigger_current: float,
        available_headroom: float,
    ) -> None:
        """Perform a single restoration step and update the step timer.

        Priority order: charger first (precise, incremental), then devices.
        A step is only taken if the headroom comfortably exceeds the amount that
        step would add back to the load.
        """
        # ── 1. Try to increase charger by one step ────────────────────────────
        charging_entity = self.config.get(CONF_CHARGING_ENTITY)
        if charging_entity and self.charging_original_value is not None:
            state = self.hass.states.get(charging_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current_value = float(state.state)
                    step = float(state.attributes.get("step", 1))

                    # Need headroom > step + safety margin to safely add one step
                    needed = step + RESTORE_MIN_HEADROOM
                    if available_headroom >= needed:
                        target = min(current_value + step, self.charging_original_value)
                        if target > current_value:
                            await self.hass.services.async_call(
                                "number",
                                "set_value",
                                {"entity_id": charging_entity, "value": target},
                                blocking=True,
                            )
                            _LOGGER.info(
                                "Restore: charging %.1fA → %.1fA (headroom was %.1fA)",
                                current_value, target, available_headroom,
                            )
                            self.last_restore_step_time = dt_util.utcnow()

                            if target >= self.charging_original_value:
                                self.charging_original_value = None
                                _LOGGER.info("Charging fully restored to original value")
                            return
                        else:
                            # Already at or above original — clear tracking
                            self.charging_original_value = None
                    else:
                        _LOGGER.info(
                            "Headroom %.1fA is not enough to safely add %.1fA charger step "
                            "(need %.1fA) — waiting",
                            available_headroom, step, needed,
                        )
                        return
                except (ValueError, TypeError) as exc:
                    _LOGGER.error("Error reading charger state during restore: %s", exc)

        # ── 2. Try to re-enable one disabled device ───────────────────────────
        if self.disabled_devices:
            # We don't know exactly how much each device draws, so require at
            # least RESTORE_MIN_HEADROOM as a conservative guard.
            if available_headroom >= RESTORE_MIN_HEADROOM:
                device = next(iter(self.disabled_devices))
                try:
                    await self.hass.services.async_call(
                        "homeassistant",
                        "turn_on",
                        {"entity_id": device},
                        blocking=True,
                    )
                    self.disabled_devices.discard(device)
                    _LOGGER.info(
                        "Restore: re-enabled device %s (headroom was %.1fA)",
                        device, available_headroom,
                    )
                    self.last_restore_step_time = dt_util.utcnow()
                    return
                except Exception as exc:
                    _LOGGER.error("Failed to restore device %s: %s", device, exc)
            else:
                _LOGGER.info(
                    "Headroom %.1fA too low to safely re-enable a device — waiting",
                    available_headroom,
                )
                return

        # ── 3. Everything is restored ─────────────────────────────────────────
        self.is_managing_load = False
        self.restore_headroom_since = None
        self.last_restore_step_time = None
        _LOGGER.info("All load restored — returning to monitoring mode")

    # ── Immediate (forced) restore — called when the switch is turned off ─────

    async def _force_restore_load(self) -> None:
        """Immediately restore all load without waiting for headroom checks.

        Used when the user disables the integration via the switch entity.
        """
        _LOGGER.info("Load balancing disabled — forcing immediate restore")

        charging_entity = self.config.get(CONF_CHARGING_ENTITY)
        if charging_entity and self.charging_original_value is not None:
            state = self.hass.states.get(charging_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    await self.hass.services.async_call(
                        "number",
                        "set_value",
                        {"entity_id": charging_entity, "value": self.charging_original_value},
                        blocking=True,
                    )
                    _LOGGER.info(
                        "Charging restored to %.1fA", self.charging_original_value
                    )
                except Exception as exc:
                    _LOGGER.error("Failed to restore charging current: %s", exc)

        for device in list(self.disabled_devices):
            try:
                await self.hass.services.async_call(
                    "homeassistant",
                    "turn_on",
                    {"entity_id": device},
                    blocking=True,
                )
                _LOGGER.info("Restored device: %s", device)
            except Exception as exc:
                _LOGGER.error("Failed to restore device %s: %s", device, exc)

        # Clear all state
        self.charging_original_value = None
        self.disabled_devices.clear()
        self.is_managing_load = False
        self.restore_headroom_since = None
        self.last_restore_step_time = None
        self.last_action_time = None
