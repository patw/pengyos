//! The `--headless --print-layout` one-shot mode (task 7).
//!
//! Builds the panel's widget list from a validated config
//! ([`widgets::build_widgets`]), runs exactly one poll → layout → paint cycle
//! on the [`crate::headless::HeadlessBackend`], and projects the resulting
//! [`crate::backend::Frame`] to a single text line
//! ([`crate::headless::render_frame`]). That line is the complete visual
//! state of the bar at one instant — one character per text cell — which is
//! what a CI assertion needs: a deterministic, human-readable,
//! diff-able projection of the whole pipeline (config → widgets → source →
//! layout → frame) with no compositor, display, or network.
//!
//! # Determinism
//!
//! The wall clock is the one reading that would otherwise change between
//! runs. `--epoch <SECONDS>` pins it through [`PinnedSource`]; everything
//! else (sysfs battery reads, the local timezone offset) still comes from the
//! real [`widgets::SysfsSource`], so state-fed widgets and text widgets give
//! host-independent output while poll-based widgets reflect the real machine
//! — except for the pinned clock. [`layout_line_with`] exists for tests,
//! which inject a fully canned source and get goldens independent of *any*
//! host state.

use widgets::{build_widgets, DataSource, PanelConfig, SysfsSource};

use crate::headless::{render_frame, HeadlessBackend};
use crate::panel::Panel;

/// A [`DataSource`] that pins the wall clock to a fixed epoch, with every
/// other reading (timezone, power supply) delegated to the real
/// [`SysfsSource`].
///
/// `epoch == None` falls through to the host clock, so the same source
/// works pinned (CI) or live.
#[derive(Debug)]
pub struct PinnedSource {
    inner: SysfsSource,
    epoch: Option<i64>,
}

impl PinnedSource {
    /// Pin the clock to `epoch` (whole seconds since the Unix epoch), or
    /// `None` to read the real clock.
    pub fn new(epoch: Option<i64>) -> Self {
        PinnedSource {
            inner: SysfsSource::new(),
            epoch,
        }
    }
}

impl DataSource for PinnedSource {
    fn now_secs(&self) -> Option<i64> {
        self.epoch.or_else(|| self.inner.now_secs())
    }

    fn tz_offset_secs(&self) -> Option<i32> {
        self.inner.tz_offset_secs()
    }

    fn power_supply(&self, entry: &str) -> Option<String> {
        self.inner.power_supply(entry)
    }
}

/// The one-shot layout line: build widgets from `config`, tick the panel once
/// at `width` cells using the real [`SysfsSource`] (with the clock pinned to
/// `epoch` when `Some`), and project the frame to text.
pub fn layout_line(config: &PanelConfig, width: u32, epoch: Option<i64>) -> String {
    layout_line_with(config, width, Box::new(PinnedSource::new(epoch)))
}

/// Test form of [`layout_line`]: the data source is injected verbatim, so a
/// golden is independent of any host state (clock, timezone, sysfs).
pub fn layout_line_with(
    config: &PanelConfig,
    width: u32,
    source: Box<dyn DataSource>,
) -> String {
    let mut backend = HeadlessBackend::new(width);
    let mut panel = Panel::new(build_widgets(config), source, config.bar.clone())
        .with_width(width);
    let frame = panel.tick(&mut backend);
    render_frame(&frame)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use widgets::{Section, WidgetConfig};

    /// A fully canned source: fixed clock, fixed timezone, fixed sysfs map.
    /// (The binary crate keeps its own doubles on purpose.)
    #[derive(Default)]
    struct Canned {
        now: Option<i64>,
        tz: Option<i32>,
        sys: BTreeMap<String, String>,
    }

    impl DataSource for Canned {
        fn now_secs(&self) -> Option<i64> {
            self.now
        }
        fn tz_offset_secs(&self) -> Option<i32> {
            self.tz
        }
        fn power_supply(&self, entry: &str) -> Option<String> {
            self.sys.get(entry).cloned()
        }
    }

    fn config_with(widgets: Vec<WidgetConfig>) -> PanelConfig {
        let mut config = PanelConfig::default();
        config.widgets = widgets;
        config
    }

    /// Epoch 1_000_000_000 in UTC is 2001-09-09 01:46:40 (31.7 years after
    /// 1970 — *not* 1970, which would be ~10^6), so `%H:%M` at tz 0 renders
    /// `01:46`. Hand-verified against `date -u -d @1000000000`.
    const FIXED_EPOCH: i64 = 1_000_000_000;

    /// Hand-computed, width 24, gap 1: Left `PengyOS`(7) at x=0 (left_end =
    /// 7); Right `12`(2) at x=22 (right_start = 22); Center `01:46`(5)
    /// centered in the free space [7,22) (length 15) → x = 7 + (15-5)/2 = 12.
    /// Line: `PengyOS     01:46     12` (24 cells, verified char-by-char).
    #[test]
    fn golden_line_three_sections_at_fixed_epoch() {
        let config = config_with(vec![
            WidgetConfig::text(Section::Left, "PengyOS".to_string()),
            WidgetConfig::clock(Section::Center, Some("%H:%M".to_string())),
            WidgetConfig::workspaces(Section::Right, Some(2)),
        ]);
        config.validate().unwrap();
        let source = Box::new(Canned {
            now: Some(FIXED_EPOCH),
            tz: Some(0),
            ..Default::default()
        });
        assert_eq!(
            layout_line_with(&config, 24, source),
            "PengyOS     01:46     12"
        );
    }

    /// Hand-computed overflow, width 10, gap 1: total 7+5+2 = 14 > 10.
    /// Outside-in drop: left and right each hold one widget (tie → left), so
    /// `PengyOS` is dropped first; 5+2 = 7 ≤ 10 fits. Right `12` at x=8;
    /// Center `01:46` centered in [0,8) → x = (8-5)/2 = 1.
    /// Line: ` 01:46  12`.
    #[test]
    fn golden_line_overflow_drops_whole_widgets_outside_in() {
        let config = config_with(vec![
            WidgetConfig::text(Section::Left, "PengyOS".to_string()),
            WidgetConfig::clock(Section::Center, Some("%H:%M".to_string())),
            WidgetConfig::workspaces(Section::Right, Some(2)),
        ]);
        config.validate().unwrap();
        let source = Box::new(Canned {
            now: Some(FIXED_EPOCH),
            tz: Some(0),
            ..Default::default()
        });
        assert_eq!(layout_line_with(&config, 10, source), " 01:46  12");
    }

    /// Battery at 17% discharging (low 25 / critical 10) renders `↓17%`
    /// (Warning style). Right-justified on a 6-cell bar: x=2 → `  ↓17%`.
    /// The battery readings come from the canned sysfs map, not the host.
    #[test]
    fn golden_line_battery_from_canned_sysfs() {
        let mut sys = BTreeMap::new();
        sys.insert("BAT0/capacity".to_string(), "17".to_string());
        sys.insert("BAT0/status".to_string(), "Discharging".to_string());
        let config = config_with(vec![WidgetConfig::battery(
            Section::Right,
            Some("BAT0".to_string()),
            Some(25),
            Some(10),
        )]);
        config.validate().unwrap();
        let source = Box::new(Canned {
            now: Some(FIXED_EPOCH),
            tz: Some(0),
            sys,
        });
        assert_eq!(layout_line_with(&config, 6, source), "  ↓17%");
    }

    /// No battery in the sysfs map → the graceful `no battery` fallback
    /// (10 cells), centered on a 10-cell bar: exactly `no battery`.
    #[test]
    fn golden_line_battery_fallback_when_absent() {
        let config = config_with(vec![WidgetConfig::battery(
            Section::Center,
            None,
            None,
            None,
        )]);
        config.validate().unwrap();
        let source = Box::new(Canned {
            now: Some(FIXED_EPOCH),
            tz: Some(0),
            ..Default::default()
        });
        assert_eq!(layout_line_with(&config, 10, source), "no battery");
    }

    /// A template with no variables set substitutes `{{missing}}` to the
    /// empty string → zero-width render → excluded by the layout engine.
    /// Only `PengyOS` remains: `PengyOS` + 11 padding spaces = 18 cells.
    #[test]
    fn golden_line_zero_width_template_is_excluded() {
        let config = config_with(vec![
            WidgetConfig::text(Section::Left, "PengyOS".to_string()),
            WidgetConfig::template(Section::Right, "{{missing}}".to_string()),
        ]);
        config.validate().unwrap();
        let source = Box::new(Canned::default());
        assert_eq!(layout_line_with(&config, 18, source), "PengyOS           ");
    }

    /// An empty config (the no-`-c` default) renders all spaces, full width.
    #[test]
    fn golden_line_empty_config_is_all_spaces() {
        assert_eq!(
            layout_line_with(&PanelConfig::default(), 5, Box::new(Canned::default())),
            "     "
        );
        assert_eq!(
            layout_line_with(&PanelConfig::default(), 0, Box::new(Canned::default())),
            ""
        );
    }

    /// `gap_cells` is honored: two left texts at gap 2 leave two spaces
    /// between them. `ab  cd        ` (width 14: 2+2+2+8).
    #[test]
    fn golden_line_honors_configured_gap() {
        let mut config = config_with(vec![
            WidgetConfig::text(Section::Left, "ab".to_string()),
            WidgetConfig::text(Section::Left, "cd".to_string()),
        ]);
        config.bar.gap_cells = 2;
        config.validate().unwrap();
        let source = Box::new(Canned::default());
        assert_eq!(layout_line_with(&config, 14, source), "ab  cd        ");
    }

    /// The production entry point, without host state: text-only widgets and
    /// no clock reads, so the golden is identical on every machine.
    #[test]
    fn layout_line_production_path_is_host_independent_for_state_fed_widgets() {
        let config = config_with(vec![
            WidgetConfig::text(Section::Left, "hello".to_string()),
            WidgetConfig::workspaces(Section::Right, Some(1)),
        ]);
        config.validate().unwrap();
        // `--epoch` irrelevant here (nothing poll-based), but pass one: the
        // output must not depend on the real clock, tz, or sysfs.
        assert_eq!(
            layout_line(&config, 12, Some(FIXED_EPOCH)),
            "hello      1"
        );
    }

    /// `PinnedSource` pins the clock when `Some` and falls through to the
    /// host clock when `None` (never `None` on a real host, but never panic).
    #[test]
    fn pinned_source_pins_the_clock_or_passes_through() {
        let pinned = PinnedSource::new(Some(42));
        assert_eq!(pinned.now_secs(), Some(42));
        let live = PinnedSource::new(None);
        assert!(live.now_secs().is_some());
    }
}
