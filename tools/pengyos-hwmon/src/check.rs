//! `--check`: one full collection pass, printed as text.
//!
//! The GUI's whole data path without the widgets, so it runs over ssh, in a
//! rescue shell, and on a Server image with no graphical session. A future TUI
//! builds on the same snapshot.

use hwall_app::{build_sensor_rows, AppSettings, RowKind, RowOptions};
use hwall_core::{CollectOptions, CollectionProfile, MonitorCollector, SnapshotStatistics};
use std::collections::BTreeMap;
use std::time::Duration;

/// Headless smoke check: one full collection, one row build, printed to stdout.
/// The GUI's whole data path minus the widgets, so it can run over ssh.
pub fn run() {
    let mut collector = MonitorCollector::new(
        CollectOptions {
            profile: CollectionProfile::Full,
            allow_helper_commands: true,
            include_sensitive: false,
            include_storage_health: false,
        },
        Duration::from_secs(90),
        Duration::from_secs(1800),
    );
    let snapshot = collector.snapshot(false);
    let mut statistics = SnapshotStatistics::new();
    statistics.observe(&snapshot);
    let settings = AppSettings::default();
    let rows = build_sensor_rows(
        &snapshot,
        &statistics,
        RowOptions {
            visibility: &settings.visibility,
            device_aliases: &settings.device_aliases,
            sensor_aliases: &settings.sensor_aliases,
            device_order: &settings.device_order,
            show_sensor_groups: settings.show_sensor_groups,
            query: "",
            favorites_only: false,
            alert_rules: &settings.sensor_alerts,
            alert_states: &BTreeMap::new(),
        },
    );
    let sensors: usize = snapshot.devices.iter().map(|d| d.sensors.len()).sum();
    println!(
        "pengyos-hwmon --check: {} devices, {} sensors, {} rows, {} warnings",
        snapshot.devices.len(),
        sensors,
        rows.len(),
        snapshot.warnings.len(),
    );
    for row in rows
        .iter()
        .filter(|row| row.kind == RowKind::Sensor)
        .take(8)
    {
        println!(
            "  {:<28} {:<30} {:>12}  {}",
            row.device_id, row.label, row.current, row.status
        );
    }
    for warning in &snapshot.warnings {
        println!("  warning: {warning}");
    }
}
