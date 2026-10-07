//! The panel configuration **model** (task 5): `panel.toml` semantics,
//! defaults, and validation.
//!
//! The TOML *syntax* lives in the binary crate (it is the only crate allowed
//! to depend on `serde`/`toml`); this module owns what the configuration
//! **means**: [`PanelConfig`] / [`BarConfig`] / [`WidgetConfig`], every default
//! value, and [`PanelConfig::validate`], which enforces all the semantic rules
//! headlessly. The binary parses text into the wire structs, maps them onto
//! this model (applying defaults), and then calls `validate()`.
//!
//! Rules (kept in one place so spec.md, README, and the tests all agree):
//!
//! - `[bar]` (all keys optional): `gap_cells` (`0..=16`, default [`DEFAULT_GAP_CELLS`]),
//!   `poll_interval_ms` (`>= 250`, default [`DEFAULT_POLL_INTERVAL_MS`]).
//! - `[[widgets]]`: each entry requires `type` (one of `clock`, `battery`,
//!   `workspaces`, `text`, `template`) and `section` (`left`/`center`/`right`).
//!   Unknown keys and unknown types are rejected by the wire layer.
//!   - `clock`: `format` (default [`DEFAULT_CLOCK_FORMAT`]); strict
//!     strftime validation — an unknown `%x` token or a lone trailing `%` is
//!     a load-time error, not literal garbage on the bar.
//!   - `battery`: `supply` (default [`DEFAULT_BATTERY_SUPPLY`]), `low` /
//!     `critical` (defaults 25 / 10, matching the widget); must satisfy
//!     `1 <= critical < low <= 100`.
//!   - `workspaces`: `count` (`1..=32`, default [`DEFAULT_WORKSPACES_COUNT`]).
//!   - `text` / `template`: `text` (required; must not be empty or
//!     whitespace).
//! - At most [`MAX_WIDGETS`] widgets per panel.
//!
//! [`validate`] reports the **first** violation it finds (enough to fix a
//! config file, and the deterministic error a test can assert on).

use crate::battery::{DEFAULT_CRITICAL_THRESHOLD, DEFAULT_LOW_THRESHOLD};
use crate::clock::{validate_format, FormatError};
use crate::widget::Section;

/// Default gap between adjacent widgets within a section, in text cells.
pub const DEFAULT_GAP_CELLS: u32 = 1;
/// Hard sanity cap on `gap_cells` (a typo like `100` is almost never intended).
pub const MAX_GAP_CELLS: u32 = 16;
/// Default poll interval for poll-based widgets, in milliseconds.
pub const DEFAULT_POLL_INTERVAL_MS: u64 = 1000;
/// Floor on `poll_interval_ms`: faster than this is meaningless (the clock has
/// a 1-second resolution) and hammers `/sys`.
pub const MIN_POLL_INTERVAL_MS: u64 = 250;
/// Default clock format.
pub const DEFAULT_CLOCK_FORMAT: &str = "%H:%M";
/// Default power-supply name a battery widget watches when none is given.
/// (The backend may still auto-discover at runtime — see T6/T7.)
pub const DEFAULT_BATTERY_SUPPLY: &str = "BAT0";
/// Default number of workspaces in the workspace widget.
pub const DEFAULT_WORKSPACES_COUNT: usize = 3;
/// Fewest workspaces the widget can render.
pub const MIN_WORKSPACES: usize = 1;
/// Most workspaces the widget will render.
pub const MAX_WORKSPACES: usize = 32;
/// Most widgets a single panel may hold.
pub const MAX_WIDGETS: usize = 64;

/// Bar-level settings (the `[bar]` table).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BarConfig {
    /// Gap between adjacent widgets *within* a section, in text cells
    /// (there is no gap between sections; see the `LayoutEngine`).
    pub gap_cells: u32,
    /// How often poll-based widgets ask the data source for fresh readings,
    /// in milliseconds.
    pub poll_interval_ms: u64,
}

impl Default for BarConfig {
    fn default() -> Self {
        BarConfig {
            gap_cells: DEFAULT_GAP_CELLS,
            poll_interval_ms: DEFAULT_POLL_INTERVAL_MS,
        }
    }
}

/// The whole panel configuration: bar settings plus the ordered widget list.
///
/// A default [`PanelConfig`] is a *valid* but empty panel (default bar, no
/// widgets) — an empty `panel.toml` is therefore legal and means "all
/// defaults".
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PanelConfig {
    /// The `[bar]` table settings.
    pub bar: BarConfig,
    /// The `[[widgets]]` entries, in file order.
    pub widgets: Vec<WidgetConfig>,
}

impl Default for PanelConfig {
    fn default() -> Self {
        PanelConfig {
            bar: BarConfig::default(),
            widgets: Vec::new(),
        }
    }
}

impl PanelConfig {
    /// Check every semantic rule for this configuration.
    ///
    /// Reports the first violation found, in the order: bar settings, widget
    /// count, then each widget in file order.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if !(0..=MAX_GAP_CELLS).contains(&self.bar.gap_cells) {
            return Err(ConfigError::GapCellsOutOfRange {
                value: self.bar.gap_cells,
                min: 0,
                max: MAX_GAP_CELLS,
            });
        }
        if self.bar.poll_interval_ms < MIN_POLL_INTERVAL_MS {
            return Err(ConfigError::PollIntervalTooSmall {
                value: self.bar.poll_interval_ms,
                min: MIN_POLL_INTERVAL_MS,
            });
        }
        if self.widgets.len() > MAX_WIDGETS {
            return Err(ConfigError::TooManyWidgets {
                count: self.widgets.len(),
                max: MAX_WIDGETS,
            });
        }
        for (index, widget) in self.widgets.iter().enumerate() {
            widget.validate_at(index)?;
        }
        Ok(())
    }
}

/// One `[[widgets]]` entry, with all defaults already applied.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WidgetConfig {
    /// The Clock widget (poll-based).
    Clock {
        /// Which section it renders in.
        section: Section,
        /// The strftime-flavoured format string (strictly validated).
        format: String,
    },
    /// The Battery widget (poll-based, sysfs).
    Battery {
        /// Which section it renders in.
        section: Section,
        /// The `/sys/class/power_supply` entry name to watch.
        supply: String,
        /// Low-battery warning threshold, in percent (discharging only).
        low: u8,
        /// Critical threshold, in percent (discharging only).
        critical: u8,
    },
    /// The Workspaces widget (state-fed, driven by the backend).
    Workspaces {
        /// Which section it renders in.
        section: Section,
        /// Number of workspaces.
        count: usize,
    },
    /// A literal text label (state-fed).
    Text {
        /// Which section it renders in.
        section: Section,
        /// The visible text.
        text: String,
    },
    /// A `{{var}}`-substituting label (state-fed).
    Template {
        /// Which section it renders in.
        section: Section,
        /// The template text.
        text: String,
    },
}

impl WidgetConfig {
    /// Build a clock entry, applying the default format when none is given.
    pub fn clock(section: Section, format: Option<String>) -> Self {
        WidgetConfig::Clock {
            section,
            format: format.unwrap_or_else(|| DEFAULT_CLOCK_FORMAT.to_string()),
        }
    }

    /// Build a battery entry, applying default supply and thresholds.
    pub fn battery(
        section: Section,
        supply: Option<String>,
        low: Option<u8>,
        critical: Option<u8>,
    ) -> Self {
        WidgetConfig::Battery {
            section,
            supply: supply.unwrap_or_else(|| DEFAULT_BATTERY_SUPPLY.to_string()),
            low: low.unwrap_or(DEFAULT_LOW_THRESHOLD),
            critical: critical.unwrap_or(DEFAULT_CRITICAL_THRESHOLD),
        }
    }

    /// Build a workspaces entry, applying the default count.
    pub fn workspaces(section: Section, count: Option<usize>) -> Self {
        WidgetConfig::Workspaces {
            section,
            count: count.unwrap_or(DEFAULT_WORKSPACES_COUNT),
        }
    }

    /// Build a literal text label.
    pub fn text(section: Section, text: String) -> Self {
        WidgetConfig::Text { section, text }
    }

    /// Build a template label.
    pub fn template(section: Section, text: String) -> Self {
        WidgetConfig::Template { section, text }
    }

    /// Which section this widget renders in.
    pub fn section(&self) -> Section {
        match self {
            WidgetConfig::Clock { section, .. }
            | WidgetConfig::Battery { section, .. }
            | WidgetConfig::Workspaces { section, .. }
            | WidgetConfig::Text { section, .. }
            | WidgetConfig::Template { section, .. } => *section,
        }
    }

    /// The widget type name exactly as it appears in `panel.toml`.
    pub fn kind(&self) -> &'static str {
        match self {
            WidgetConfig::Clock { .. } => "clock",
            WidgetConfig::Battery { .. } => "battery",
            WidgetConfig::Workspaces { .. } => "workspaces",
            WidgetConfig::Text { .. } => "text",
            WidgetConfig::Template { .. } => "template",
        }
    }

    /// Semantic validation for one widget, with its file index for messages.
    pub(crate) fn validate_at(&self, index: usize) -> Result<(), ConfigError> {
        match self {
            WidgetConfig::Clock { format, .. } => match validate_format(format) {
                Ok(()) => Ok(()),
                Err(FormatError::UnknownToken {
                    percent_index,
                    token,
                }) => Err(ConfigError::UnknownClockToken {
                    index,
                    percent_index,
                    token,
                }),
                Err(FormatError::TrailingPercent { percent_index }) => {
                    Err(ConfigError::TrailingClockPercent {
                        index,
                        percent_index,
                    })
                }
            },
            WidgetConfig::Battery { low, critical, .. } => {
                if !(1..=100).contains(low) || !(1..=100).contains(critical) || critical >= low {
                    Err(ConfigError::BatteryThresholds {
                        index,
                        low: *low,
                        critical: *critical,
                    })
                } else {
                    Ok(())
                }
            }
            WidgetConfig::Workspaces { count, .. } => {
                if !(MIN_WORKSPACES..=MAX_WORKSPACES).contains(count) {
                    Err(ConfigError::WorkspaceCountOutOfRange {
                        index,
                        value: *count,
                        min: MIN_WORKSPACES,
                        max: MAX_WORKSPACES,
                    })
                } else {
                    Ok(())
                }
            }
            WidgetConfig::Text { text, .. }
            | WidgetConfig::Template { text, .. } => {
                if text.trim().is_empty() {
                    Err(ConfigError::EmptyText {
                        index,
                        kind: self.kind(),
                    })
                } else {
                    Ok(())
                }
            }
        }
    }
}

/// A semantic configuration error (as opposed to a TOML syntax error, which
/// the wire layer reports separately).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigError {
    /// `bar.gap_cells` was outside `0..=MAX_GAP_CELLS`.
    GapCellsOutOfRange {
        /// The offending value.
        value: u32,
        /// The minimum allowed value.
        min: u32,
        /// The maximum allowed value.
        max: u32,
    },
    /// `bar.poll_interval_ms` was below `MIN_POLL_INTERVAL_MS`.
    PollIntervalTooSmall {
        /// The offending value, in milliseconds.
        value: u64,
        /// The minimum allowed value, in milliseconds.
        min: u64,
    },
    /// The panel has more than `MAX_WIDGETS` widgets.
    TooManyWidgets {
        /// How many widgets the config declares.
        count: usize,
        /// The maximum allowed.
        max: usize,
    },
    /// A clock `format` contains an unrecognised `%x` token.
    UnknownClockToken {
        /// Zero-based index of the widget in the config file.
        index: usize,
        /// Zero-based character index of the offending `%` in the format.
        percent_index: usize,
        /// The unrecognised token character (the one after `%`).
        token: char,
    },
    /// A clock `format` ends with a lone `%` (a typo for `%%`).
    TrailingClockPercent {
        /// Zero-based index of the widget in the config file.
        index: usize,
        /// Zero-based character index of the offending `%` in the format.
        percent_index: usize,
    },
    /// Battery thresholds do not satisfy `1 <= critical < low <= 100`.
    BatteryThresholds {
        /// Zero-based index of the widget in the config file.
        index: usize,
        /// The offending low threshold.
        low: u8,
        /// The offending critical threshold.
        critical: u8,
    },
    /// The workspaces `count` was outside `MIN_WORKSPACES..=MAX_WORKSPACES`.
    WorkspaceCountOutOfRange {
        /// Zero-based index of the widget in the config file.
        index: usize,
        /// The offending value.
        value: usize,
        /// The minimum allowed value.
        min: usize,
        /// The maximum allowed value.
        max: usize,
    },
    /// A `text`/`template` widget's text is empty or whitespace.
    EmptyText {
        /// Zero-based index of the widget in the config file.
        index: usize,
        /// The widget kind (`text` or `template`).
        kind: &'static str,
    },
    /// A widget's `section` was not `left`, `center`, or `right`.
    UnknownSection {
        /// Zero-based index of the widget in the config file.
        index: usize,
        /// The offending section string.
        section: String,
    },
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::GapCellsOutOfRange { value, min, max } => write!(
                f,
                "bar.gap_cells must be between {min} and {max} (got {value})"
            ),
            ConfigError::PollIntervalTooSmall { value, min } => write!(
                f,
                "bar.poll_interval_ms must be at least {min} ms (got {value})"
            ),
            ConfigError::TooManyWidgets { count, max } => write!(
                f,
                "a panel may have at most {max} widgets (got {count})"
            ),
            ConfigError::UnknownClockToken {
                index,
                percent_index,
                token,
            } => write!(
                f,
                "widget[{index}] (clock): unknown format token `%{token}` at position {percent_index}"
            ),
            ConfigError::TrailingClockPercent { index, percent_index } => write!(
                f,
                "widget[{index}] (clock): format ends with a lone `%` at position {percent_index} (use `%%` for a literal percent)"
            ),
            ConfigError::BatteryThresholds {
                index,
                low,
                critical,
            } => write!(
                f,
                "widget[{index}] (battery): thresholds must satisfy 1 <= critical < low <= 100 (got low={low}, critical={critical})"
            ),
            ConfigError::WorkspaceCountOutOfRange {
                index,
                value,
                min,
                max,
            } => write!(
                f,
                "widget[{index}] (workspaces): count must be between {min} and {max} (got {value})"
            ),
            ConfigError::EmptyText { index, kind } => write!(
                f,
                "widget[{index}] ({kind}): text must not be empty or whitespace"
            ),
            ConfigError::UnknownSection { index, section } => write!(
                f,
                "widget[{index}]: unknown section `{section}` (expected `left`, `center`, or `right`)"
            ),
        }
    }
}

impl std::error::Error for ConfigError {}

#[cfg(test)]
mod tests {
    use super::*;

    /// A config with one clock in the center (the default clock), used as a
    /// valid baseline most tests mutate.
    fn baseline() -> PanelConfig {
        PanelConfig {
            bar: BarConfig::default(),
            widgets: vec![WidgetConfig::clock(Section::Center, None)],
        }
    }

    #[test]
    fn defaults_are_valid_and_empty() {
        let config = PanelConfig::default();
        assert!(config.validate().is_ok());
        assert!(config.widgets.is_empty());
        assert_eq!(config.bar.gap_cells, 1);
        assert_eq!(config.bar.poll_interval_ms, 1000);
    }

    #[test]
    fn widget_constructors_apply_defaults() {
        let c = WidgetConfig::clock(Section::Right, None);
        assert!(matches!(
            c,
            WidgetConfig::Clock {
                ref format, ..
            } if format == DEFAULT_CLOCK_FORMAT
        ));

        let b = WidgetConfig::battery(Section::Right, None, None, None);
        assert!(matches!(
            b,
            WidgetConfig::Battery {
                ref supply,
                low,
                critical,
                ..
            } if supply == DEFAULT_BATTERY_SUPPLY
                && low == DEFAULT_LOW_THRESHOLD
                && critical == DEFAULT_CRITICAL_THRESHOLD
        ));

        let w = WidgetConfig::workspaces(Section::Left, None);
        assert!(matches!(
            w,
            WidgetConfig::Workspaces { count, .. } if count == DEFAULT_WORKSPACES_COUNT
        ));

        let w2 = WidgetConfig::workspaces(Section::Left, Some(5));
        assert!(matches!(
            w2,
            WidgetConfig::Workspaces { count, .. } if count == 5
        ));
    }

    #[test]
    fn kind_and_section_reflect_the_variant() {
        let t = WidgetConfig::text(Section::Left, "PengyOS".into());
        assert_eq!(t.kind(), "text");
        assert_eq!(t.section(), Section::Left);
        let p = WidgetConfig::template(Section::Right, "{{name}}".into());
        assert_eq!(p.kind(), "template");
        assert_eq!(p.section(), Section::Right);
        let c = WidgetConfig::clock(Section::Center, Some("%d".into()));
        assert_eq!(c.kind(), "clock");
        assert_eq!(c.section(), Section::Center);
    }

    #[test]
    fn gap_cells_out_of_range_is_rejected() {
        let mut config = baseline();
        config.bar.gap_cells = MAX_GAP_CELLS + 1;
        let err = config.validate().unwrap_err();
        assert_eq!(
            err,
            ConfigError::GapCellsOutOfRange {
                value: MAX_GAP_CELLS + 1,
                min: 0,
                max: MAX_GAP_CELLS
            }
        );
        // The boundary values are accepted.
        config.bar.gap_cells = 0;
        assert!(config.validate().is_ok());
        config.bar.gap_cells = MAX_GAP_CELLS;
        assert!(config.validate().is_ok());
    }

    #[test]
    fn poll_interval_below_the_floor_is_rejected() {
        let mut config = baseline();
        config.bar.poll_interval_ms = MIN_POLL_INTERVAL_MS - 1;
        assert_eq!(
            config.validate().unwrap_err(),
            ConfigError::PollIntervalTooSmall {
                value: MIN_POLL_INTERVAL_MS - 1,
                min: MIN_POLL_INTERVAL_MS
            }
        );
        config.bar.poll_interval_ms = MIN_POLL_INTERVAL_MS;
        assert!(config.validate().is_ok());
    }

    #[test]
    fn more_than_max_widgets_is_rejected() {
        let mut config = PanelConfig::default();
        for _ in 0..MAX_WIDGETS {
            config
                .widgets
                .push(WidgetConfig::text(Section::Left, "x".into()));
        }
        assert!(config.validate().is_ok()); // exactly MAX_WIDGETS is fine
        config
            .widgets
            .push(WidgetConfig::text(Section::Left, "x".into()));
        assert_eq!(
            config.validate().unwrap_err(),
            ConfigError::TooManyWidgets {
                count: MAX_WIDGETS + 1,
                max: MAX_WIDGETS
            }
        );
    }

    #[test]
    fn clock_format_validation_runs_on_the_model() {
        let mut config = baseline();
        config
            .widgets
            .push(WidgetConfig::clock(Section::Left, Some("%H %Q".into())));
        let err = config.validate().unwrap_err();
        assert_eq!(
            err,
            ConfigError::UnknownClockToken {
                index: 1,
                percent_index: 3,
                token: 'Q'
            }
        );

        let mut config = baseline();
        config
            .widgets
            .push(WidgetConfig::clock(Section::Left, Some("%H%".into())));
        assert_eq!(
            config.validate().unwrap_err(),
            ConfigError::TrailingClockPercent {
                index: 1,
                percent_index: 2
            }
        );
    }

    #[test]
    fn battery_thresholds_must_strictly_decrease() {
        let cases: &[(u8, u8, bool)] = &[
            (25, 10, true),  // the documented default
            (100, 99, true), // top of the range
            (2, 1, true), // bottom of the range
            (10, 10, false), // critical == low is ambiguous
            (10, 20, false), // critical above low
            (0, 10, false), // low below range
            (100, 101, false), // critical above range (101)
        ];
        for (low, critical, ok) in cases {
            let mut config = PanelConfig::default();
            config.widgets = vec![WidgetConfig::Battery {
                section: Section::Right,
                supply: "BAT0".into(),
                low: *low,
                critical: *critical,
            }];
            if *ok {
                assert!(
                    config.validate().is_ok(),
                    "low={low} critical={critical} should pass"
                );
            } else {
                assert_eq!(
                    config.validate().unwrap_err(),
                    ConfigError::BatteryThresholds {
                        index: 0,
                        low: *low,
                        critical: *critical
                    },
                    "low={low} critical={critical} should fail"
                );
            }
        }
    }

    #[test]
    fn workspace_count_boundaries() {
        // validate reports the FIRST violation, so each out-of-range case gets
        // its own single-widget config.
        for count in [0usize, MAX_WORKSPACES + 1] {
            let config = PanelConfig {
                bar: BarConfig::default(),
                widgets: vec![WidgetConfig::workspaces(Section::Left, Some(count))],
            };
            assert_eq!(
                config.validate().unwrap_err(),
                ConfigError::WorkspaceCountOutOfRange {
                    index: 0,
                    value: count,
                    min: MIN_WORKSPACES,
                    max: MAX_WORKSPACES
                }
            );
        }
        // The in-range boundaries are accepted.
        for count in [MIN_WORKSPACES, MAX_WORKSPACES] {
            let config = PanelConfig {
                bar: BarConfig::default(),
                widgets: vec![WidgetConfig::workspaces(Section::Left, Some(count))],
            };
            assert!(config.validate().is_ok(), "count {count} should pass");
        }
    }

    #[test]
    fn empty_text_is_rejected_for_text_and_template() {
        for (kind, widget) in [
            ("text", WidgetConfig::text(Section::Left, "   ".into())),
            ("template", WidgetConfig::template(Section::Left, "".into())),
        ] {
            let mut config = PanelConfig::default();
            config.widgets = vec![widget];
            assert_eq!(
                config.validate().unwrap_err(),
                ConfigError::EmptyText { index: 0, kind },
                "kind {kind} should be rejected"
            );
        }
        // Whitespace-around-content is fine.
        let mut config = PanelConfig::default();
        config.widgets = vec![WidgetConfig::text(Section::Left, " a ".into())];
        assert!(config.validate().is_ok());
    }

    #[test]
    fn unknown_section_error_carries_index_and_string() {
        let err = ConfigError::UnknownSection {
            index: 3,
            section: "middle".into(),
        };
        let msg = err.to_string();
        assert!(msg.contains("widget[3]"));
        assert!(msg.contains("`middle`"));
        assert!(msg.contains("`left`, `center`, or `right`"));
    }

    #[test]
    fn error_messages_are_one_line_and_stable() {
        let config = PanelConfig {
            bar: BarConfig {
                gap_cells: 0,
                poll_interval_ms: 0,
            },
            widgets: Vec::new(),
        };
        // The bar check fires first (poll is below the floor).
        let msg = config.validate().unwrap_err().to_string();
        assert_eq!(
            msg,
            format!(
                "bar.poll_interval_ms must be at least {MIN_POLL_INTERVAL_MS} ms (got 0)"
            )
        );
        assert!(!msg.contains('\n'));
    }

    #[test]
    fn a_full_config_validates() {
        let config = PanelConfig {
            bar: BarConfig {
                gap_cells: 2,
                poll_interval_ms: 250,
            },
            widgets: vec![
                WidgetConfig::workspaces(Section::Left, Some(3)),
                WidgetConfig::text(Section::Left, "PengyOS".into()),
                WidgetConfig::clock(Section::Center, Some("%a %b %d  %H:%M".into())),
                WidgetConfig::template(Section::Right, "{{hostname}}".into()),
                WidgetConfig::battery(Section::Right, Some("BAT0".into()), Some(30), Some(15)),
            ],
        };
        assert!(config.validate().is_ok());
    }
}
