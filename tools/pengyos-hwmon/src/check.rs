//! `--check`: one full collection pass, printed as text.
//!
//! The GUI's whole data path without the widgets, so it runs over ssh, in a
//! rescue shell, from a script or an agent, and in a build with no front-end at
//! all. Every sensor is printed, grouped by device, with each device's limit.

use crate::tjmax;
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
    // Every row, grouped by device, with the limit a temperature is judged
    // against: what an agent or an ssh session reads to answer "is it hot?".
    let limits: std::collections::HashMap<&str, tjmax::Limit> = snapshot
        .devices
        .iter()
        .filter_map(|device| tjmax::device_limit(device).map(|limit| (device.id.as_str(), limit)))
        .collect();
    for row in &rows {
        match row.kind {
            RowKind::Device => match limits.get(row.device_id.as_str()) {
                Some(limit) => println!(
                    "{}  (limit {:.0} °C, {})",
                    row.label, limit.celsius, limit.note
                ),
                None => println!("{}", row.label),
            },
            RowKind::Header => println!("  {}", row.label),
            RowKind::Sensor => {
                let status = if tjmax::is_ok_status(&row.status) {
                    String::new()
                } else {
                    format!("  {}", row.status)
                };
                println!("    {:<36} {:>12}{status}", row.label, row.current);
            }
        }
    }
    for warning in &snapshot.warnings {
        println!("  warning: {warning}");
    }
}
