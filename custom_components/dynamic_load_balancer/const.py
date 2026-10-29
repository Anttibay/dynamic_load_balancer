"""Constants for the Dynamic Load Balancer integration."""

DOMAIN = "dynamic_load_balancer"

# Configuration keys
CONF_FUSE_SIZE = "fuse_size"
CONF_PHASE_1_SENSOR = "phase_1_sensor"
CONF_PHASE_2_SENSOR = "phase_2_sensor"
CONF_PHASE_3_SENSOR = "phase_3_sensor"
CONF_ENABLED_PHASES = "enabled_phases"
CONF_AGGRESSIVENESS = "aggressiveness"
CONF_SPIKE_FILTER_TIME = "spike_filter_time"
CONF_CHARGING_ENTITY = "charging_entity"
CONF_DEVICES_TO_TOGGLE = "devices_to_toggle"
CONF_NOTIFY_ENABLED = "notify_enabled"
CONF_NOTIFY_TARGET = "notify_target"
CONF_NOTIFY_ON_RESTORE = "notify_on_restore"
CONF_RESTORE_HEADROOM = "restore_headroom"
CONF_RESTORE_PRIORITY = "restore_priority"
CONF_RESTORE_SETTLE_TIME = "restore_settle_time"
CONF_RESTORE_STEP_TIME = "restore_step_time"
CONF_REDUCTION_INTERVAL = "reduction_interval"
CONF_CHARGER_MARGIN = "charger_margin"
CONF_DEVICE_LOAD = "device_load"

# Default values
DEFAULT_FUSE_SIZE = 25
DEFAULT_AGGRESSIVENESS = "medium"
DEFAULT_SPIKE_FILTER_TIME = 30  # seconds
DEFAULT_ENABLED_PHASES = ["1", "2", "3"]
DEFAULT_NOTIFY_ENABLED = True
DEFAULT_NOTIFY_ON_RESTORE = False
DEFAULT_RESTORE_HEADROOM = 3.0  # Amperes of free capacity required before restoration
DEFAULT_RESTORE_SETTLE_TIME = 60  # seconds headroom must be stable before restoring
DEFAULT_RESTORE_STEP_TIME = 60  # seconds between consecutive restoration steps
DEFAULT_REDUCTION_INTERVAL = 10  # minimum seconds between load-reduction actions
DEFAULT_CHARGER_MARGIN = 2.0  # extra Amperes removed from the charger on reduction
DEFAULT_DEVICE_LOAD = 5.0  # assumed Amperes freed / added per toggled device

# Restoration priority: which kind of load is brought back first
RESTORE_PRIORITY_CHARGER = "charger_first"
RESTORE_PRIORITY_DEVICES = "devices_first"
RESTORE_PRIORITIES = [RESTORE_PRIORITY_CHARGER, RESTORE_PRIORITY_DEVICES]
DEFAULT_RESTORE_PRIORITY = RESTORE_PRIORITY_CHARGER

# Entity domains that can be turned off during an overload
TOGGLE_DOMAINS = ["switch", "climate", "water_heater", "fan", "input_boolean"]

# Aggressiveness levels (percentage of fuse capacity to trigger at)
AGGRESSIVENESS_LEVELS = {
    "very_low": 1.00, # Trigger at 100% capacity (at the fuse limit itself)
    "low": 0.95,      # Trigger at 95% capacity
    "medium": 0.90,   # Trigger at 90% capacity
    "high": 0.85,     # Trigger at 85% capacity
    "very_high": 0.80 # Trigger at 80% capacity
}

# A sustained overload that starts this long after the previous one counts as a
# new event (new notification, new "last overload" timestamp, counter + 1).
OVERLOAD_EVENT_GAP_SECS = 300

# Coordinator status values (also the options of the status sensor)
STATUS_MONITORING = "monitoring"
STATUS_OVERLOAD_PENDING = "overload_pending"
STATUS_REDUCING = "reducing"
STATUS_WAITING_HEADROOM = "waiting_headroom"
STATUS_SETTLING = "settling"
STATUS_RESTORING = "restoring"
STATUS_DISABLED = "disabled"
STATUS_SENSOR_UNAVAILABLE = "sensor_unavailable"
STATUSES = [
    STATUS_MONITORING,
    STATUS_OVERLOAD_PENDING,
    STATUS_REDUCING,
    STATUS_WAITING_HEADROOM,
    STATUS_SETTLING,
    STATUS_RESTORING,
    STATUS_DISABLED,
    STATUS_SENSOR_UNAVAILABLE,
]

STORAGE_VERSION = 1
