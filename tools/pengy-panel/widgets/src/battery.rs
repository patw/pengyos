//! The [`Battery`] widget: a *poll-based* widget that reads the power-supply
//! sysfs tree and renders a charge indicator.
//!
//! All hardware access goes through the [`crate::source::DataSource`]
//! (`power_supply("<bat>/capacity")` and `power_supply("<bat>/status")`), so in
//! tests the widget is exercised against committed values in
//! [`crate::testutil::MockSource`] instead of a real laptop. When the battery is
//! absent (a desktop/server) the widget renders a graceful fallback rather than
//! panicking — the core contract that every `None` reading has a sane fallback.

use crate::source::DataSource;
use crate::widget::{Section, Style, TextRun, Widget, WidgetRender};

/// Default percentage at or below which a *discharging* battery is a warning.
pub const DEFAULT_LOW_THRESHOLD: u8 = 25;
/// Default percentage at or below which a *discharging* battery is critical.
pub const DEFAULT_CRITICAL_THRESHOLD: u8 = 10;

/// How the battery is charged, parsed from the sysfs `status` file.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChargeState {
    /// Power is being drawn from the battery (`status` == `Discharging`).
    Discharging,
    /// Power is flowing in (`status` == `Charging`).
    Charging,
    /// Battery is fully charged and connected (`status` == `Full`).
    Full,
    /// Connected to power but neither charging nor full (`status` ==
    /// `Not charging`).
    NotCharging,
    /// The `status` file was absent or unrecognised.
    Unknown,
}

impl ChargeState {
    /// Parse the raw `status` file value (already trimmed by the source).
    pub fn from_status(status: Option<&str>) -> ChargeState {
        match status.map(str::trim) {
            Some("Discharging") => ChargeState::Discharging,
            Some("Charging") => ChargeState::Charging,
            Some("Full") => ChargeState::Full,
            Some("Not charging") => ChargeState::NotCharging,
            _ => ChargeState::Unknown,
        }
    }

    /// True when the battery is connected to an external power source
    /// (charging, full, or idle-on-AC) as opposed to being drained.
    pub fn connected(&self) -> bool {
        matches!(
            self,
            ChargeState::Charging | ChargeState::Full | ChargeState::NotCharging
        )
    }
}

/// A *poll-based* widget that shows battery level and charge state.
///
/// Reads `<supply>/capacity` (an integer 0–100) and `<supply>/status` from the
/// [`DataSource`]. Construct with the section and the sysfs supply name to
/// watch (e.g. `"BAT0"`).
#[derive(Debug, Clone)]
pub struct Battery {
    section: Section,
    supply: String,
    low_threshold: u8,
    critical_threshold: u8,
}

impl Battery {
    /// Build a battery widget watching `supply` (e.g. `"BAT0"`) with the
    /// default low/critical thresholds.
    pub fn new(section: Section, supply: impl Into<String>) -> Self {
        Battery {
            section,
            supply: supply.into(),
            low_threshold: DEFAULT_LOW_THRESHOLD,
            critical_threshold: DEFAULT_CRITICAL_THRESHOLD,
        }
    }

    /// Move the widget to a different section.
    pub fn with_section(mut self, section: Section) -> Self {
        self.section = section;
        self
    }

    /// Watch a different power-supply name.
    pub fn with_supply(mut self, supply: impl Into<String>) -> Self {
        self.supply = supply.into();
        self
    }

    /// Override the low/critical thresholds (both in percent, `critical <= low`).
    pub fn with_thresholds(mut self, low: u8, critical: u8) -> Self {
        self.low_threshold = low;
        self.critical_threshold = critical;
        self
    }

    /// The sysfs supply name this widget reads.
    pub fn supply(&self) -> &str {
        &self.supply
    }

    /// The style tag to apply to the render for a given state + capacity.
    fn style_for(&self, state: ChargeState, percent: u8) -> Style {
        if state.connected() {
            // A connected battery is never in an emergency state.
            return Style::Normal;
        }
        // Discharging or Unknown: escalate as the level falls.
        if percent <= self.critical_threshold {
            Style::Critical
        } else if percent <= self.low_threshold {
            Style::Warning
        } else {
            Style::Normal
        }
    }
}

impl Widget for Battery {
    fn id(&self) -> &str {
        "battery"
    }

    fn section(&self) -> Section {
        self.section
    }

    fn update(&mut self, source: &dyn DataSource) -> WidgetRender {
        // No capacity reading at all → the host has no (readable) battery.
        let Some(cap_raw) = source.power_supply(&format!("{}/capacity", self.supply)) else {
            return WidgetRender::new(
                self.section,
                vec![TextRun::new("no battery", Style::Warning)],
            );
        };

        let percent: u8 = match cap_raw.trim().parse() {
            Ok(p) if p <= 100 => p,
            // Out-of-range or non-numeric: the file exists but is not a sane
            // reading — degrade to the fallback rather than show garbage.
            _ => {
                return WidgetRender::new(
                    self.section,
                    vec![TextRun::new("no battery", Style::Warning)],
                );
            }
        };

        let status = source.power_supply(&format!("{}/status", self.supply));
        let state = ChargeState::from_status(status.as_deref());

        let arrow = match state {
            ChargeState::Discharging => "↓",
            ChargeState::Charging | ChargeState::Full | ChargeState::NotCharging => "↑",
            ChargeState::Unknown => "",
        };

        let style = self.style_for(state, percent);
        WidgetRender::new(
            self.section,
            vec![TextRun::new(format!("{arrow}{percent}%"), style)],
        )
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }

    fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::MockSource;

    fn source_with(capacity: Option<&str>, status: Option<&str>) -> MockSource {
        let mut s = MockSource::default();
        if let Some(c) = capacity {
            s.sys.insert("BAT0/capacity".to_string(), c.to_string());
        }
        if let Some(st) = status {
            s.sys.insert("BAT0/status".to_string(), st.to_string());
        }
        s
    }

    fn run(b: &Battery, capacity: Option<&str>, status: Option<&str>) -> WidgetRender {
        // `update` needs `&mut self`; clone so call sites can keep a shared ref.
        let mut w = b.clone();
        w.update(&source_with(capacity, status))
    }

    #[test]
    fn battery_reports_id_and_section() {
        let b = Battery::new(Section::Right, "BAT0");
        assert_eq!(b.id(), "battery");
        assert_eq!(b.section(), Section::Right);
        assert_eq!(b.supply(), "BAT0");
        assert_eq!(b.with_section(Section::Left).section(), Section::Left);
    }

    #[test]
    fn discharging_normal() {
        let b = Battery::new(Section::Right, "BAT0");
        let r = run(&b, Some("63"), Some("Discharging"));
        assert_eq!(r.runs()[0].text, "↓63%");
        assert_eq!(r.runs()[0].style, Style::Normal);
    }

    #[test]
    fn charging_shows_up_arrow_and_normal() {
        let b = Battery::new(Section::Right, "BAT0");
        let r = run(&b, Some("40"), Some("Charging"));
        assert_eq!(r.runs()[0].text, "↑40%");
        assert_eq!(r.runs()[0].style, Style::Normal);
    }

    #[test]
    fn full_battery() {
        let b = Battery::new(Section::Right, "BAT0");
        let r = run(&b, Some("100"), Some("Full"));
        assert_eq!(r.runs()[0].text, "↑100%");
        assert_eq!(r.runs()[0].style, Style::Normal);
    }

    #[test]
    fn low_battery_is_a_warning() {
        // Default low threshold is 25; 20% discharging → Warning.
        let b = Battery::new(Section::Right, "BAT0");
        let r = run(&b, Some("20"), Some("Discharging"));
        assert_eq!(r.runs()[0].text, "↓20%");
        assert_eq!(r.runs()[0].style, Style::Warning);
    }

    #[test]
    fn critical_battery() {
        // Default critical threshold is 10; 8% discharging → Critical.
        let b = Battery::new(Section::Right, "BAT0");
        let r = run(&b, Some("8"), Some("Discharging"));
        assert_eq!(r.runs()[0].text, "↓8%");
        assert_eq!(r.runs()[0].style, Style::Critical);
    }

    #[test]
    fn thresholds_are_configurable() {
        let b = Battery::new(Section::Right, "BAT0").with_thresholds(50, 30);
        // 30% with a critical threshold of 30 → Critical.
        assert_eq!(run(&b, Some("30"), Some("Discharging")).runs()[0].style, Style::Critical);
        // 40% → Warning (<= 50, > 30).
        assert_eq!(run(&b, Some("40"), Some("Discharging")).runs()[0].style, Style::Warning);
        // 50% → Warning (== low).
        assert_eq!(run(&b, Some("50"), Some("Discharging")).runs()[0].style, Style::Warning);
        // 51% → Normal.
        assert_eq!(run(&b, Some("51"), Some("Discharging")).runs()[0].style, Style::Normal);
    }

    #[test]
    fn a_connected_battery_never_warns_even_when_low() {
        // Charging at a low level is not an emergency.
        let b = Battery::new(Section::Right, "BAT0");
        let r = run(&b, Some("5"), Some("Charging"));
        assert_eq!(r.runs()[0].style, Style::Normal);
        // Idle on AC at a low level is also fine.
        let r = run(&b, Some("5"), Some("Not charging"));
        assert_eq!(r.runs()[0].text, "↑5%");
        assert_eq!(r.runs()[0].style, Style::Normal);
    }

    #[test]
    fn no_status_file_treats_battery_as_unknown() {
        // Capacity present, no status → no arrow, and low levels still escalate.
        let b = Battery::new(Section::Right, "BAT0");
        let r = run(&b, Some("63"), None);
        assert_eq!(r.runs()[0].text, "63%");
        assert_eq!(r.runs()[0].style, Style::Normal);
        let r = run(&b, Some("8"), None);
        assert_eq!(r.runs()[0].text, "8%");
        assert_eq!(r.runs()[0].style, Style::Critical);
    }

    #[test]
    fn no_battery_renders_a_graceful_fallback() {
        let b = Battery::new(Section::Right, "BAT0");
        let r = run(&b, None, None);
        assert_eq!(r.runs().len(), 1);
        assert_eq!(r.runs()[0].text, "no battery");
        assert_eq!(r.runs()[0].style, Style::Warning);
    }

    #[test]
    fn non_numeric_capacity_degrades_to_fallback() {
        let b = Battery::new(Section::Right, "BAT0");
        assert_eq!(run(&b, Some("???"), Some("Discharging")).runs()[0].text, "no battery");
        // Out-of-range (> 100) is not a sane reading.
        assert_eq!(run(&b, Some("140"), Some("Discharging")).runs()[0].text, "no battery");
    }

    #[test]
    fn reads_from_the_configured_supply_name() {
        // A widget watching BAT1 must NOT be satisfied by BAT0's files.
        let mut s = MockSource::default();
        s.sys.insert("BAT0/capacity".to_string(), "63".to_string());
        s.sys.insert("BAT0/status".to_string(), "Discharging".to_string());

        let mut b = Battery::new(Section::Right, "BAT1");
        let r = b.update(&s);
        assert_eq!(r.runs()[0].text, "no battery");

        // Now with a BAT1 present, it reads BAT1.
        s.sys.insert("BAT1/capacity".to_string(), "17".to_string());
        s.sys.insert("BAT1/status".to_string(), "Discharging".to_string());
        let r = b.update(&s);
        assert_eq!(r.runs()[0].text, "↓17%");
    }

    #[test]
    fn charge_state_from_status_mapping() {
        assert_eq!(ChargeState::from_status(Some("Discharging")), ChargeState::Discharging);
        assert_eq!(ChargeState::from_status(Some("Charging")), ChargeState::Charging);
        assert_eq!(ChargeState::from_status(Some("Full")), ChargeState::Full);
        assert_eq!(ChargeState::from_status(Some("Not charging")), ChargeState::NotCharging);
        assert_eq!(ChargeState::from_status(Some("garbage")), ChargeState::Unknown);
        assert_eq!(ChargeState::from_status(None), ChargeState::Unknown);
        assert_eq!(ChargeState::from_status(Some("  Discharging  ")), ChargeState::Discharging);

        assert!(ChargeState::Charging.connected());
        assert!(ChargeState::Full.connected());
        assert!(ChargeState::NotCharging.connected());
        assert!(!ChargeState::Discharging.connected());
        assert!(!ChargeState::Unknown.connected());
    }

    #[test]
    fn width_counts_the_arrow_and_percent() {
        let b = Battery::new(Section::Right, "BAT0");
        // "↓63%" is 4 chars (the arrow counts as one UTF-8 char).
        assert_eq!(run(&b, Some("63"), Some("Discharging")).width_cells(), 4);
        // "100%" without a status is 4 chars.
        assert_eq!(run(&b, Some("100"), None).width_cells(), 4);
    }
}
