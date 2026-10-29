# Changelog

Release notes for each version. The Release workflow publishes the section
matching the version in `manifest.json`.

## 0.3.0

### Highlights
- **Every setting is now editable after setup**: Configure opens a menu (thresholds, current sensors, EV charger & devices, notifications, advanced timing). No more removing and re-adding the integration.
- **Fixes the Configure dialog crashing** on recent Home Assistant versions.
- **Live controls on the device page**: Aggressiveness, Fuse size, Spike filter, Restore headroom and Notifications can be changed from dashboards and automations.
- **New entities**: Status, Available headroom, Trigger threshold, Overload events, Overload / Load reduced binary sensors, and a "Restore load now" button.
- Diagnostics download for bug reports.

### Fixes
- Climate units and water heaters are now actually turned off during an overload, and get their previous mode back afterwards.
- A restart or settings change during an overload no longer leaves devices off or the charger throttled.
- The charger's original current is kept and restored later, even when headroom is briefly too tight.
- Charger reduction rounds down, so it never cuts too little.
- A new overload is notified again after a quiet period, even while earlier load is still being restored.

### Behaviour changes
- Devices come back in reverse order and need room for their estimated load (default 5 A) plus the restore headroom. Adjustable under Configure → EV charger & devices.
- Requires Home Assistant 2025.1 or newer.

## 0.2.3 and earlier

See the [GitHub releases](https://github.com/Anttibay/dynamic_load_balancer/releases).
