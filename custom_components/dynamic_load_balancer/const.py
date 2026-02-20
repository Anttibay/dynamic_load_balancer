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

# Default values
DEFAULT_FUSE_SIZE = 25
DEFAULT_AGGRESSIVENESS = "medium"
DEFAULT_SPIKE_FILTER_TIME = 30  # seconds
DEFAULT_ENABLED_PHASES = ["1", "2", "3"]
DEFAULT_NOTIFY_ENABLED = True

# Aggressiveness levels (percentage of fuse capacity to trigger at)
AGGRESSIVENESS_LEVELS = {
    "very_low": 1.00, # Trigger at 100% capacity (at the fuse limit itself)
    "low": 0.95,      # Trigger at 95% capacity
    "medium": 0.90,   # Trigger at 90% capacity
    "high": 0.85,     # Trigger at 85% capacity
    "very_high": 0.80 # Trigger at 80% capacity
}
