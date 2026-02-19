# Dynamic Load Balancer

A Home Assistant custom integration that prevents electrical overloads in residential three-phase power systems by automatically throttling EV chargers and toggling high-power devices when a phase exceeds its fuse capacity.

## What It Does

The integration monitors per-phase current every 5 seconds. When a phase exceeds the configured trigger threshold for a sustained period it:

1. **Reduces EV / charging current** — steps it down incrementally using a `number` entity (works with any EVSE: Tesla Wall Connector, Wallbox, go-e Charger, etc.)
2. **Turns off selected devices** — e.g. water heaters, climate units — if throttling the charger alone is not enough
3. **Restores everything automatically** once all phases drop back below the threshold

## Installation via HACS

1. In Home Assistant, go to **HACS → Integrations → ⋮ → Custom repositories**
2. Add this repository URL and select category **Integration**
3. Install **Dynamic Load Balancer** from the HACS store
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** and search for *Dynamic Load Balancer*

## Manual Installation

Copy the `custom_components/dynamic_load_balancer` folder into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

Setup is done entirely through the UI wizard (4 steps):

| Step | What you configure |
|---|---|
| 1. Panel capacity | Per-phase fuse / breaker size in Amperes |
| 2. Current sensors | Sensor entities that report current (A) per phase |
| 3. Reaction behavior | Aggressiveness level and spike-filter duration |
| 4. Load reduction | EV charger `number` entity and/or devices to toggle |

After setup you can adjust the fuse size, aggressiveness, and spike filter at any time via **Settings → Devices & Services → Dynamic Load Balancer → Configure**.

## Aggressiveness Levels

| Level | Trigger threshold | Example (25 A fuse) |
|---|---|---|
| Low | 95 % of fuse | 23.75 A |
| Medium (default) | 90 % of fuse | 22.5 A |
| High | 85 % of fuse | 21.25 A |
| Very High | 80 % of fuse | 20.0 A |

## Requirements

- Home Assistant 2023.x or newer
- At least one current sensor per phase (e.g. Shelly EM, Eastron SDM, smart meter integration)
- Optional: an EVSE that exposes a charging-current `number` entity
