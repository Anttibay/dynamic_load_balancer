"""Coordinator for Dynamic Load Balancer."""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from enum import Enum
import logging
import math
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfElectricCurrent,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util, slugify

from .const import (
    AGGRESSIVENESS_LEVELS,
    CONF_AGGRESSIVENESS,
    CONF_CHARGER_MARGIN,
    CONF_CHARGING_ENTITY,
    CONF_DEVICE_LOAD,
    CONF_DEVICES_TO_TOGGLE,
    CONF_ENABLED_PHASES,
    CONF_FUSE_SIZE,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_ON_RESTORE,
    CONF_NOTIFY_TARGET,
    CONF_PHASE_1_SENSOR,
    CONF_PHASE_2_SENSOR,
    CONF_PHASE_3_SENSOR,
    CONF_REDUCTION_INTERVAL,
    CONF_RESTORE_HEADROOM,
    CONF_RESTORE_PRIORITY,
    CONF_RESTORE_SETTLE_TIME,
    CONF_RESTORE_STEP_TIME,
    CONF_SPIKE_FILTER_TIME,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_CHARGER_MARGIN,
    DEFAULT_DEVICE_LOAD,
    DEFAULT_ENABLED_PHASES,
    DEFAULT_FUSE_SIZE,
    DEFAULT_NOTIFY_ENABLED,
    DEFAULT_NOTIFY_ON_RESTORE,
    DEFAULT_REDUCTION_INTERVAL,
    DEFAULT_RESTORE_HEADROOM,
    DEFAULT_RESTORE_SETTLE_TIME,
    DEFAULT_RESTORE_STEP_TIME,
    DEFAULT_SPIKE_FILTER_TIME,
    DOMAIN,
    OVERLOAD_EVENT_GAP_SECS,
    RESTORE_PRIORITY_DEVICES,
    STATUS_DISABLED,
    STATUS_MONITORING,
    STATUS_OVERLOAD_PENDING,
    STATUS_REDUCING,
    STATUS_RESTORING,
    STATUS_SENSOR_UNAVAILABLE,
    STATUS_SETTLING,
    STATUS_WAITING_HEADROOM,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=5)

# Delay before persisting changed restoration state (batches rapid changes)
STORE_SAVE_DELAY = 2  # seconds

# Maps phase numbers to their sensor config keys
_PHASE_SENSOR_KEY = {
    1: CONF_PHASE_1_SENSOR,
    2: CONF_PHASE_2_SENSOR,
    3: CONF_PHASE_3_SENSOR,
}

# States in which a toggled device is not drawing (or cannot be controlled)
_INACTIVE_STATES = (STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN)
_UNUSABLE_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN)


class _StepResult(Enum):
    """Outcome of one restoration attempt."""

    IDLE = "idle"  # Nothing of this kind left to restore
    STEPPED = "stepped"  # A step was taken (or attempted) — wait before the next
    BLOCKED = "blocked"  # Something to restore, but not enough headroom yet


def _attr_float(state: State, name: str, default: float) -> float:
    """Read a numeric attribute, falling back to a default on bad data."""
    try:
        return float(state.attributes.get(name, default))
    except (TypeError, ValueError):
        return default


class LoadBalancerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage load balancing."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        # Merge base data with any user-saved options (options take precedence)
        self.config: dict[str, Any] = {**entry.data, **entry.options}

        # Serializes the control loop with switch / button / option actions so
        # they never interleave halfway through a reduction or restoration.
        self._lock = asyncio.Lock()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._saved_state: dict[str, Any] | None = None

        # Overload tracking per phase
        self.overload_start: dict[int, datetime | None] = {1: None, 2: None, 3: None}
        self._sensors_unavailable = False

        # Reduction rate limiting
        self.last_action_time: datetime | None = None

        # Restoration state (persisted — survives restarts and reloads)
        self.charging_entity: str | None = None  # Charger the original value belongs to
        self.charging_original_value: float | None = None
        # Devices turned off by us, in shedding order: entity_id → state before
        self.disabled_devices: dict[str, str] = {}
        self.restore_headroom_since: datetime | None = None  # When sufficient headroom was first seen
        self.last_restore_step_time: datetime | None = None  # When the last restoration step was taken

        # Last overload event — stored for the sensors and for deduplicating notifications
        self.last_triggered_time: datetime | None = None
        self.last_triggered_phases: list[int] = []
        self.last_triggered_peak: float | None = None
        self.last_triggered_threshold: float | None = None
        self.overload_count = 0
        self._last_sustained_at: datetime | None = None

        # Overall state
        self.is_managing_load = False
        self.enabled = True  # Controlled by switch entity

    # ── Configuration ─────────────────────────────────────────────────────────

    def _get_float(self, key: str, default: float) -> float:
        """Return a numeric config value, falling back to the default."""
        value = self.config.get(key)
        if value is None:
            return float(default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @property
    def fuse_size(self) -> float:
        """Per-phase fuse size in Amperes."""
        return self._get_float(CONF_FUSE_SIZE, DEFAULT_FUSE_SIZE)

    @property
    def trigger_current(self) -> float:
        """Current above which a phase is considered overloaded."""
        aggressiveness = self.config.get(CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS)
        ratio = AGGRESSIVENESS_LEVELS.get(aggressiveness)
        if ratio is None:
            _LOGGER.warning(
                "Unknown aggressiveness value '%s' — falling back to 'medium'", aggressiveness
            )
            ratio = AGGRESSIVENESS_LEVELS[DEFAULT_AGGRESSIVENESS]
        return self.fuse_size * ratio

    @property
    def monitored_phases(self) -> list[int]:
        """Phases that are enabled and have a sensor assigned."""
        enabled = self.config.get(CONF_ENABLED_PHASES) or DEFAULT_ENABLED_PHASES
        return [
            int(phase)
            for phase in enabled
            if self.config.get(_PHASE_SENSOR_KEY.get(int(phase), ""))
        ]

    @property
    def has_reduced_load(self) -> bool:
        """Return True while any load is held below its original level."""
        return self.charging_original_value is not None or bool(self.disabled_devices)

    async def async_apply_config(self) -> None:
        """Apply changed config entry options without reloading the integration.

        Everything is read from self.config on each cycle, so a reload (which
        would interrupt an ongoing restoration) is never needed.
        """
        new_config = {**self.entry.data, **self.entry.options}
        async with self._lock:
            if (
                self.charging_entity is not None
                and self.charging_entity != new_config.get(CONF_CHARGING_ENTITY)
            ):
                # The charger we reduced is no longer managed — hand it back
                await self._async_release_charger()
            self.config = new_config
        await self.async_refresh()

    async def async_set_option(self, key: str, value: Any) -> None:
        """Persist a single setting changed from an entity (number, select, switch)."""
        self.config[key] = value
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, key: value}
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def state_as_dict(self) -> dict[str, Any]:
        """Serialize the state that must survive a restart."""
        last_event = None
        if self.last_triggered_time is not None:
            last_event = {
                "time": self.last_triggered_time.isoformat(),
                "phases": self.last_triggered_phases,
                "peak": self.last_triggered_peak,
                "threshold": self.last_triggered_threshold,
            }
        return {
            "charging_entity": self.charging_entity,
            "charging_original_value": self.charging_original_value,
            "disabled_devices": [
                {"entity_id": entity_id, "state": state}
                for entity_id, state in self.disabled_devices.items()
            ],
            "is_managing_load": self.is_managing_load,
            "last_event": last_event,
            "overload_count": self.overload_count,
        }

    async def async_load_state(self) -> None:
        """Restore persisted state so reduced loads are still restored after a restart."""
        data = await self._store.async_load()
        if data:
            try:
                original = data.get("charging_original_value")
                if original is not None and data.get("charging_entity"):
                    self.charging_entity = data["charging_entity"]
                    self.charging_original_value = float(original)
                self.disabled_devices = {
                    item["entity_id"]: item.get("state")
                    for item in data.get("disabled_devices", [])
                }
                self.is_managing_load = bool(data.get("is_managing_load"))
                if event := data.get("last_event"):
                    self.last_triggered_time = dt_util.parse_datetime(event["time"])
                    self.last_triggered_phases = list(event.get("phases") or [])
                    self.last_triggered_peak = event.get("peak")
                    self.last_triggered_threshold = event.get("threshold")
                self.overload_count = int(data.get("overload_count", 0))
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Ignoring invalid stored load balancer state: %s", err)

        self.is_managing_load = self.is_managing_load or self.has_reduced_load
        self._saved_state = self.state_as_dict()
        if self.has_reduced_load:
            _LOGGER.info(
                "Resuming management of reduced load (charger original: %s, devices off: %s)",
                self.charging_original_value,
                list(self.disabled_devices),
            )

    @callback
    def _async_save_if_changed(self) -> None:
        """Schedule a store write when the persisted state has changed."""
        state = self.state_as_dict()
        if state != self._saved_state:
            self._saved_state = state
            self._store.async_delay_save(self.state_as_dict, STORE_SAVE_DELAY)

    async def async_save_state(self) -> None:
        """Write the persisted state immediately (used on unload)."""
        await self._store.async_save(self.state_as_dict())

    async def async_remove_state(self) -> None:
        """Delete the persisted state (used when the entry is removed)."""
        await self._store.async_remove()

    # ── Control loop ──────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from sensors and manage load."""
        async with self._lock:
            data = await self._async_run_cycle()
        self._async_save_if_changed()
        return data

    def _snapshot(
        self,
        phase_currents: dict[int, float | None],
        overloaded_phases: list[int],
        sustained_overloads: list[int],
        status: str,
        min_headroom: float | None,
    ) -> dict[str, Any]:
        """Build the data dict consumed by the entities."""
        return {
            "phase_currents": phase_currents,
            "overloaded_phases": overloaded_phases,
            "sustained_overloads": sustained_overloads,
            "status": status,
            "min_headroom": min_headroom,
            "is_managing": self.is_managing_load,
            "fuse_size": self.fuse_size,
            "trigger_current": self.trigger_current,
            "charging_original_value": self.charging_original_value,
            "disabled_devices": list(self.disabled_devices),
            "restore_headroom_since": self.restore_headroom_since,
            "last_restore_step_time": self.last_restore_step_time,
            # Last overload event — consumed by the sensors
            "last_overloaded_phases": self.last_triggered_phases,
            "last_peak_current": self.last_triggered_peak,
            "trigger_current_at_event": self.last_triggered_threshold,
        }

    async def _async_run_cycle(self) -> dict[str, Any]:
        """Run one detection / reduction / restoration cycle."""
        phase_currents = self._get_phase_currents()
        trigger_current = self.trigger_current
        monitored_phases = self.monitored_phases
        min_headroom = self._calculate_min_headroom(
            phase_currents, trigger_current, monitored_phases
        )

        # ── Sensor availability guard ─────────────────────────────────────────
        # If any monitored phase is returning None (unavailable / unknown / bad
        # value), skip all load management for this cycle. Acting on incomplete
        # data risks incorrect load reduction or premature restoration. Also
        # reset stale overload timers for those phases.
        unavailable_phases = [
            phase for phase in monitored_phases if phase_currents.get(phase) is None
        ]
        if unavailable_phases:
            for phase in unavailable_phases:
                if self.overload_start.get(phase) is not None:
                    _LOGGER.info(
                        "Phase %d sensor became unavailable — resetting overload timer", phase
                    )
                    self.overload_start[phase] = None
            if not self._sensors_unavailable:
                _LOGGER.warning(
                    "Phase %s sensor(s) unavailable or invalid — "
                    "pausing load management until they recover",
                    unavailable_phases,
                )
            self._sensors_unavailable = True
            return self._snapshot(
                phase_currents, [], [], STATUS_SENSOR_UNAVAILABLE, min_headroom
            )
        if self._sensors_unavailable:
            _LOGGER.info("All phase sensors available again — resuming load management")
            self._sensors_unavailable = False

        # ── Phase overload detection ──────────────────────────────────────────
        now = dt_util.utcnow()
        overloaded_phases = []
        for phase in monitored_phases:
            current = phase_currents[phase]
            if current is not None and current > trigger_current:
                overloaded_phases.append(phase)
                if self.overload_start[phase] is None:
                    self.overload_start[phase] = now
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
        spike_filter_seconds = self._get_float(
            CONF_SPIKE_FILTER_TIME, DEFAULT_SPIKE_FILTER_TIME
        )
        for phase in overloaded_phases:
            started = self.overload_start[phase]
            if started is not None:
                duration = (now - started).total_seconds()
                _LOGGER.debug(
                    "Phase %d overload duration: %.1fs / %.0fs",
                    phase, duration, spike_filter_seconds,
                )
                if duration >= spike_filter_seconds:
                    sustained_overloads.append(phase)

        # ── Load management decision ──────────────────────────────────────────
        if not self.enabled:
            if self.has_reduced_load:
                # Safety net: never leave load reduced while balancing is off
                await self._async_force_restore()
            status = STATUS_DISABLED

        elif sustained_overloads:
            if self._is_new_event(now):
                await self._async_start_event(
                    sustained_overloads, phase_currents, trigger_current
                )
            self._last_sustained_at = now

            # Active overload: reduce load and reset restoration tracking
            await self._reduce_load(sustained_overloads, phase_currents, trigger_current)
            self.is_managing_load = True
            self.restore_headroom_since = None
            status = STATUS_REDUCING

        elif self.is_managing_load or self.has_reduced_load:
            if overloaded_phases:
                # Even a transient spike blocks restoration — reset the settle timer
                _LOGGER.debug(
                    "Transient overload on phase(s) %s — pausing restoration",
                    overloaded_phases,
                )
                self.restore_headroom_since = None
                status = STATUS_WAITING_HEADROOM
            else:
                # No overload at all: check whether headroom is sufficient to restore
                status = await self._maybe_restore_load(min_headroom)

        elif overloaded_phases:
            status = STATUS_OVERLOAD_PENDING
        else:
            status = STATUS_MONITORING

        return self._snapshot(
            phase_currents, overloaded_phases, sustained_overloads, status, min_headroom
        )

    def _is_new_event(self, now: datetime) -> bool:
        """Decide whether a sustained overload starts a new overload event.

        Flickering within one event must not spam notifications, but a fresh
        overload after a quiet period must be reported even while a previous
        event's load is still being restored.
        """
        if not self.is_managing_load or self._last_sustained_at is None:
            return True
        quiet = (now - self._last_sustained_at).total_seconds()
        return quiet > OVERLOAD_EVENT_GAP_SECS

    async def _async_start_event(
        self,
        sustained_overloads: list[int],
        phase_currents: dict[int, float | None],
        trigger_current: float,
    ) -> None:
        """Record a new overload event and notify about it."""
        peak_current = max(
            (
                current
                for phase in sustained_overloads
                if (current := phase_currents.get(phase)) is not None
            ),
            default=0.0,
        )
        self.last_triggered_time = dt_util.utcnow()
        self.last_triggered_phases = list(sustained_overloads)
        self.last_triggered_peak = peak_current
        self.last_triggered_threshold = trigger_current
        self.overload_count += 1
        _LOGGER.warning(
            "Sustained overload on phase(s) %s: peak %.1fA > trigger %.1fA",
            sustained_overloads, peak_current, trigger_current,
        )
        await self._send_overload_notification(
            sustained_overloads, phase_currents, trigger_current, peak_current
        )

    # ── Sensor reading ────────────────────────────────────────────────────────

    def _get_phase_currents(self) -> dict[int, float | None]:
        """Get current readings (in Amperes) from all configured phase sensors."""
        currents: dict[int, float | None] = {}
        for phase_num, conf_key in _PHASE_SENSOR_KEY.items():
            sensor_id = self.config.get(conf_key)
            if not sensor_id:
                continue
            state = self.hass.states.get(sensor_id)
            if state is None or state.state in _UNUSABLE_STATES:
                currents[phase_num] = None
                continue
            try:
                value = float(state.state)
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid current value for phase %d: %s", phase_num, state.state
                )
                currents[phase_num] = None
                continue
            if (
                state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                == UnitOfElectricCurrent.MILLIAMPERE
            ):
                value /= 1000
            currents[phase_num] = value
        return currents

    # ── Headroom helper ───────────────────────────────────────────────────────

    def _calculate_min_headroom(
        self,
        phase_currents: dict[int, float | None],
        trigger_current: float,
        phases: list[int],
    ) -> float | None:
        """Return the smallest headroom (trigger − current) across the given phases.

        A positive value means all phases are below the trigger. The smaller the
        number, the less room there is before the next overload. Returns None
        when no phase has a reading.
        """
        headrooms = [
            trigger_current - current
            for phase in phases
            if (current := phase_currents.get(phase)) is not None
        ]
        return min(headrooms) if headrooms else None

    # ── Notifications ─────────────────────────────────────────────────────────

    @property
    def _notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_overload"

    async def _async_notify(self, title: str, message: str) -> None:
        """Post to the HA notification centre and optionally a mobile device."""
        # 1. Always create a persistent HA notification (visible as a bell icon in HA)
        persistent_notification.async_create(
            self.hass, message, title=title, notification_id=self._notification_id
        )

        # 2. Optionally send to a configured mobile device (stored as device_id)
        device_id = self.config.get(CONF_NOTIFY_TARGET)
        if not device_id:
            return
        service = self._resolve_mobile_notify_service(device_id)
        if service is None:
            _LOGGER.warning(
                "Could not resolve notify service for device_id '%s'", device_id
            )
            return
        if not self.hass.services.has_service("notify", service):
            _LOGGER.warning(
                "Notify service 'notify.%s' not registered — "
                "open the HA companion app on the device once to activate it",
                service,
            )
            return
        try:
            await self.hass.services.async_call(
                "notify",
                service,
                {
                    "title": title,
                    "message": message,
                    # Same tag → the restore message replaces the overload alert
                    "data": {"tag": self._notification_id},
                },
                blocking=False,
            )
            _LOGGER.info("Notification sent to notify.%s", service)
        except Exception as exc:  # noqa: BLE001 — a notification must never break the loop
            _LOGGER.error("Failed to send notification to notify.%s: %s", service, exc)

    async def _send_overload_notification(
        self,
        overloaded_phases: list[int],
        phase_currents: dict[int, float | None],
        trigger_current: float,
        peak_current: float,
    ) -> None:
        """Send an overload notification unless notifications are disabled."""
        if not self.config.get(CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED):
            _LOGGER.debug("Notifications disabled — skipping overload alert")
            return

        # Build a readable phase summary, e.g. "L1: 24.3 A, L2: 23.1 A"
        phase_parts = [
            f"L{phase}: {current:.1f} A"
            for phase in overloaded_phases
            if (current := phase_currents.get(phase)) is not None
        ]
        phase_summary = ", ".join(phase_parts) if phase_parts else f"phase(s) {overloaded_phases}"

        await self._async_notify(
            "⚡ Electrical Overload Detected",
            f"Overload on {phase_summary}. "
            f"Peak: {peak_current:.1f} A — trigger threshold: {trigger_current:.1f} A "
            f"({self.fuse_size:g} A fuse). "
            f"Dynamic Load Balancer is reducing load.",
        )

    async def _send_restored_notification(self) -> None:
        """Tell the user the overload is over, if they asked for it."""
        if not self.config.get(
            CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED
        ) or not self.config.get(CONF_NOTIFY_ON_RESTORE, DEFAULT_NOTIFY_ON_RESTORE):
            return
        duration = ""
        if self.last_triggered_time is not None:
            minutes = (dt_util.utcnow() - self.last_triggered_time).total_seconds() / 60
            duration = f" Load management lasted {minutes:.0f} min."
        await self._async_notify(
            "✅ Load Restored",
            f"All phases are back below the {self.trigger_current:.1f} A trigger "
            f"threshold and all reduced load has been restored.{duration}",
        )

    @callback
    def _resolve_mobile_notify_service(self, device_id: str) -> str | None:
        """Map a mobile_app device_id to its notify service name.

        The mobile_app integration names its notify service after the
        device_name stored in its config entry, so look that up first. Falls
        back to slugifying the device registry name.
        """
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return None
        for entry_id in device.config_entries:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if (
                entry is not None
                and entry.domain == "mobile_app"
                and (name := entry.data.get("device_name"))
            ):
                return f"mobile_app_{slugify(name)}"
        if device.name:
            return f"mobile_app_{slugify(device.name)}"
        return None

    # ── Device control helpers ────────────────────────────────────────────────

    async def _async_call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        await self.hass.services.async_call(domain, service, data, blocking=True)

    async def _async_turn_off_device(self, entity_id: str, state: State) -> None:
        """Turn a device off using the most reliable service for its domain."""
        domain = entity_id.split(".", 1)[0]
        if domain == "climate" and STATE_OFF in (state.attributes.get("hvac_modes") or []):
            await self._async_call(
                "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": STATE_OFF}
            )
        elif domain == "water_heater" and STATE_OFF in (
            state.attributes.get("operation_list") or []
        ):
            await self._async_call(
                "water_heater",
                "set_operation_mode",
                {"entity_id": entity_id, "operation_mode": STATE_OFF},
            )
        else:
            await self._async_call("homeassistant", "turn_off", {"entity_id": entity_id})

    async def _async_turn_on_device(self, entity_id: str, previous: str | None) -> None:
        """Turn a device back on, restoring its previous mode where possible."""
        domain = entity_id.split(".", 1)[0]
        state = self.hass.states.get(entity_id)
        attributes = state.attributes if state else {}
        if domain == "climate" and previous in (attributes.get("hvac_modes") or []):
            await self._async_call(
                "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": previous}
            )
        elif domain == "water_heater" and previous in (
            attributes.get("operation_list") or []
        ):
            await self._async_call(
                "water_heater",
                "set_operation_mode",
                {"entity_id": entity_id, "operation_mode": previous},
            )
        else:
            await self._async_call("homeassistant", "turn_on", {"entity_id": entity_id})

    async def _async_shed_device(self, entity_id: str) -> bool:
        """Turn off one device if it is currently on. Returns True if it was turned off."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _INACTIVE_STATES:
            _LOGGER.debug("Device %s is off or unavailable — skipping", entity_id)
            return False
        try:
            await self._async_turn_off_device(entity_id, state)
        except Exception as exc:  # noqa: BLE001 — one failing device must not stop the others
            _LOGGER.error("Failed to turn off %s: %s", entity_id, exc)
            return False
        self.disabled_devices[entity_id] = state.state
        _LOGGER.info("Turned off device: %s (was %s)", entity_id, state.state)
        return True

    async def _async_set_charger(self, entity_id: str, value: float) -> None:
        """Set the charging current on a number / input_number entity."""
        await self._async_call(
            entity_id.split(".", 1)[0], "set_value", {"entity_id": entity_id, "value": value}
        )

    async def _async_release_charger(self) -> None:
        """Put the tracked charger straight back to its original value and stop tracking it."""
        entity_id, original = self.charging_entity, self.charging_original_value
        self.charging_entity = None
        self.charging_original_value = None
        if entity_id is None or original is None:
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _UNUSABLE_STATES:
            _LOGGER.warning(
                "Charger %s is unavailable — could not restore it to %.1fA",
                entity_id, original,
            )
            return
        try:
            await self._async_set_charger(entity_id, original)
            _LOGGER.info("Charging restored to %.1fA", original)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to restore charging current: %s", exc)

    # ── Load reduction ────────────────────────────────────────────────────────

    async def _reduce_load(
        self,
        overloaded_phases: list[int],
        phase_currents: dict[int, float | None],
        trigger_current: float,
    ) -> None:
        """Reduce electrical load by adjusting charging current and toggling devices."""
        # Rate-limit reduction actions so each one can take effect first
        if self.last_action_time:
            elapsed = (dt_util.utcnow() - self.last_action_time).total_seconds()
            if elapsed < self._get_float(CONF_REDUCTION_INTERVAL, DEFAULT_REDUCTION_INTERVAL):
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

        # Step 2: Toggle off devices (in configured order) if still overloaded
        if total_overload > 0:
            devices = self.config.get(CONF_DEVICES_TO_TOGGLE) or []
            device_load = self._get_float(CONF_DEVICE_LOAD, DEFAULT_DEVICE_LOAD)
            _LOGGER.info(
                "Still overloaded by %.1fA — checking %d device(s)",
                total_overload, len(devices),
            )
            for device in devices:
                if device in self.disabled_devices:
                    continue
                if await self._async_shed_device(device):
                    total_overload -= device_load
                    if total_overload <= 0:
                        break

        self.last_action_time = dt_util.utcnow()

    async def _reduce_charging_current(self, entity_id: str, overload_amps: float) -> float:
        """Reduce charging current by the overload amount plus a safety margin.

        Reads min/max/step from the entity's attributes — works with any EVSE
        (Tesla, Wallbox, go-e, etc.) that exposes a number entity for charge current.
        Returns the number of Amperes actually removed.
        """
        if self.charging_entity is not None and self.charging_entity != entity_id:
            await self._async_release_charger()

        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.error("Charging entity %s not found", entity_id)
            return 0.0
        if state.state in _UNUSABLE_STATES:
            _LOGGER.warning("Charging entity %s is %s", entity_id, state.state)
            return 0.0
        try:
            current_value = float(state.state)
        except (ValueError, TypeError) as exc:
            _LOGGER.error("Cannot parse charging value '%s': %s", state.state, exc)
            return 0.0

        min_value = _attr_float(state, "min", 5.0)
        max_value = _attr_float(state, "max", 32.0)
        step = _attr_float(state, "step", 1.0)

        if max_value <= min_value:
            _LOGGER.error(
                "Charger %s has invalid range: min=%.1f >= max=%.1f — skipping",
                entity_id, min_value, max_value,
            )
            return 0.0

        _LOGGER.debug(
            "Charger %s: current=%.1fA  min=%.1f  max=%.1f  step=%.1f",
            entity_id, current_value, min_value, max_value, step,
        )

        # Store original value on first reduction so we know where to return to
        if self.charging_original_value is None:
            self.charging_entity = entity_id
            self.charging_original_value = current_value
            _LOGGER.info(
                "Stored original charging value: %.1fA (range %.1f–%.1fA)",
                current_value, min_value, max_value,
            )

        # Reduce by the overload plus a safety margin, but not below min
        margin = self._get_float(CONF_CHARGER_MARGIN, DEFAULT_CHARGER_MARGIN)
        target_reduction = min(overload_amps + margin, current_value - min_value)
        new_value = current_value - target_reduction

        # Snap down to the step grid (rounding up could remove too little)
        if step > 0:
            new_value = math.floor(new_value / step + 1e-9) * step
        new_value = max(min_value, min(max_value, new_value))

        if new_value >= current_value:
            _LOGGER.debug("Charging already at minimum (%.1fA)", current_value)
            return 0.0

        try:
            await self._async_set_charger(entity_id, new_value)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to set charging current: %s", exc)
            return 0.0
        _LOGGER.info("Charging reduced: %.1fA → %.1fA", current_value, new_value)
        return current_value - new_value

    # ── Cautious restoration ──────────────────────────────────────────────────

    async def _maybe_restore_load(self, min_headroom: float | None) -> str:
        """Cautiously restore reduced load when there is sufficient stable headroom.

        The restoration is gated by three conditions that must all be true:
        1. Headroom across every monitored phase >= restore_headroom
        2. That headroom has been continuously present for the settle time
        3. The restore step interval has passed since the previous step

        When all three conditions are met, a single small step is taken (one charger
        increment OR one device re-enabled) and the cycle restarts so the system can
        observe the effect before the next step. Returns the resulting status.
        """
        restore_headroom = self._get_float(CONF_RESTORE_HEADROOM, DEFAULT_RESTORE_HEADROOM)
        settle_secs = self._get_float(CONF_RESTORE_SETTLE_TIME, DEFAULT_RESTORE_SETTLE_TIME)
        step_secs = self._get_float(CONF_RESTORE_STEP_TIME, DEFAULT_RESTORE_STEP_TIME)
        now = dt_util.utcnow()

        # ── Gate 1: Is there enough headroom at all? ──────────────────────────
        if min_headroom is None or min_headroom < restore_headroom:
            if self.restore_headroom_since is not None:
                _LOGGER.debug(
                    "Headroom %s A < %.1fA minimum — resetting settle timer",
                    min_headroom, restore_headroom,
                )
                self.restore_headroom_since = None
            return STATUS_WAITING_HEADROOM

        # ── Gate 2: Has headroom been stable long enough? ─────────────────────
        if self.restore_headroom_since is None:
            self.restore_headroom_since = now
            _LOGGER.info(
                "Headroom %.1fA detected (need %.1fA) — waiting %.0fs before restoring",
                min_headroom, restore_headroom, settle_secs,
            )
            return STATUS_SETTLING

        settle_elapsed = (now - self.restore_headroom_since).total_seconds()
        if settle_elapsed < settle_secs:
            _LOGGER.debug(
                "Settle timer: %.0fs / %.0fs (headroom %.1fA)",
                settle_elapsed, settle_secs, min_headroom,
            )
            return STATUS_SETTLING

        # ── Gate 3: Has enough time passed since the last restore step? ───────
        if self.last_restore_step_time is not None:
            step_elapsed = (now - self.last_restore_step_time).total_seconds()
            if step_elapsed < step_secs:
                _LOGGER.debug(
                    "Waiting %.0fs more before next restore step (headroom %.1fA)",
                    step_secs - step_elapsed, min_headroom,
                )
                return STATUS_RESTORING

        # ── All gates passed: take one restoration step ───────────────────────
        return await self._restore_one_step(min_headroom, restore_headroom)

    async def _restore_one_step(self, available_headroom: float, margin: float) -> str:
        """Perform a single restoration step in the configured priority order.

        Lower-priority load waits until higher-priority load is fully restored,
        so the charger and devices never compete for the same headroom.
        A step is only taken if the headroom still exceeds the safety margin
        after adding what that step brings back.
        """
        if self.config.get(CONF_RESTORE_PRIORITY) == RESTORE_PRIORITY_DEVICES:
            attempts = (self._try_restore_device, self._try_restore_charger)
        else:
            attempts = (self._try_restore_charger, self._try_restore_device)

        for attempt in attempts:
            result = await attempt(available_headroom, margin)
            if result is _StepResult.STEPPED:
                self.last_restore_step_time = dt_util.utcnow()
                return STATUS_RESTORING
            if result is _StepResult.BLOCKED:
                return STATUS_WAITING_HEADROOM

        # ── Everything is restored ────────────────────────────────────────────
        self.is_managing_load = False
        self.restore_headroom_since = None
        self.last_restore_step_time = None
        _LOGGER.info("All load restored — returning to monitoring mode")
        await self._send_restored_notification()
        return STATUS_MONITORING

    async def _try_restore_charger(self, headroom: float, margin: float) -> _StepResult:
        """Increase the charger by one step towards its original value."""
        entity_id = self.charging_entity
        original = self.charging_original_value
        if entity_id is None or original is None:
            return _StepResult.IDLE

        state = self.hass.states.get(entity_id)
        current_value: float | None = None
        if state is not None and state.state not in _UNUSABLE_STATES:
            with contextlib.suppress(ValueError, TypeError):
                current_value = float(state.state)
        if state is None or current_value is None:
            # Charger is offline (car disconnected, EVSE unreachable, etc.). It is no
            # longer drawing current, so release the tracking and continue.
            _LOGGER.warning(
                "Charger %s is unavailable during restoration — releasing charger tracking",
                entity_id,
            )
            self.charging_entity = None
            self.charging_original_value = None
            return _StepResult.IDLE

        step = _attr_float(state, "step", 1.0) or 1.0
        target = min(current_value + step, original)
        if target <= current_value:
            # Already at or above original (e.g. raised manually) — clear tracking
            self.charging_entity = None
            self.charging_original_value = None
            return _StepResult.IDLE

        increment = target - current_value
        if headroom < increment + margin:
            # Keep the original value: restoration resumes once headroom allows
            _LOGGER.debug(
                "Headroom %.1fA not enough to add %.1fA charger step (need %.1fA)",
                headroom, increment, increment + margin,
            )
            return _StepResult.BLOCKED

        try:
            await self._async_set_charger(entity_id, target)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to restore charging current: %s", exc)
            return _StepResult.STEPPED  # Retry after the step interval
        _LOGGER.info(
            "Restore: charging %.1fA → %.1fA (headroom was %.1fA)",
            current_value, target, headroom,
        )
        if target >= original:
            self.charging_entity = None
            self.charging_original_value = None
            _LOGGER.info("Charging fully restored to original value")
        return _StepResult.STEPPED

    async def _try_restore_device(self, headroom: float, margin: float) -> _StepResult:
        """Turn the most recently disabled device back on (last off, first on)."""
        device_load = self._get_float(CONF_DEVICE_LOAD, DEFAULT_DEVICE_LOAD)
        for entity_id, previous in reversed(list(self.disabled_devices.items())):
            state = self.hass.states.get(entity_id)
            if state is None:
                _LOGGER.warning("Device %s no longer exists — forgetting it", entity_id)
                del self.disabled_devices[entity_id]
                continue
            if state.state not in _INACTIVE_STATES:
                _LOGGER.info("Device %s was turned back on externally", entity_id)
                del self.disabled_devices[entity_id]
                continue
            if state.state in _UNUSABLE_STATES:
                continue  # Try again once it is reachable

            # The device's real draw is unknown: require room for the estimate
            if headroom < device_load + margin:
                _LOGGER.debug(
                    "Headroom %.1fA too low to re-enable %s (need %.1fA)",
                    headroom, entity_id, device_load + margin,
                )
                return _StepResult.BLOCKED
            try:
                await self._async_turn_on_device(entity_id, previous)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error("Failed to restore device %s: %s", entity_id, exc)
                return _StepResult.STEPPED  # Retry after the step interval
            del self.disabled_devices[entity_id]
            _LOGGER.info(
                "Restore: re-enabled device %s (headroom was %.1fA)", entity_id, headroom
            )
            return _StepResult.STEPPED

        return _StepResult.BLOCKED if self.disabled_devices else _StepResult.IDLE

    # ── Immediate (forced) restore — switch turned off / "restore now" button ──

    async def async_restore_now(self) -> None:
        """Immediately restore all reduced load and refresh the entities."""
        await self.async_force_restore()
        await self.async_refresh()

    async def async_force_restore(self) -> None:
        """Immediately restore all reduced load (serialized with the control loop)."""
        async with self._lock:
            await self._async_force_restore()

    async def _async_force_restore(self) -> None:
        """Immediately restore all load without waiting for headroom checks."""
        _LOGGER.info("Forcing immediate restore of all reduced load")
        await self._async_release_charger()

        for device, previous in reversed(list(self.disabled_devices.items())):
            try:
                await self._async_turn_on_device(device, previous)
                _LOGGER.info("Restored device: %s", device)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error("Failed to restore device %s: %s", device, exc)

        # Clear all state; restart the spike filter so a still-present overload
        # is only acted on again once it is confirmed.
        self.disabled_devices.clear()
        self.is_managing_load = False
        self.restore_headroom_since = None
        self.last_restore_step_time = None
        self.last_action_time = None
        self.overload_start = {1: None, 2: None, 3: None}
        self._async_save_if_changed()
