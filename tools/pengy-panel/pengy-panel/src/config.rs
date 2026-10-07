//! `panel.toml` loading (task 5): TOML syntax → validated
//! [`widgets::PanelConfig`].
//!
//! This module owns the *wire* side of the config: `serde` structs that
//! mirror the file layout (with `deny_unknown_fields`, so a typo'd key is a
//! load-time error) and the mapping from wire structs onto the core's
//! [`widgets::config::PanelConfig`] model. Default application and every
//! semantic rule live in the core (`widgets::config`) so they stay
//! headless-testable without `serde`/`toml` in the dependency-free crate.
//!
//! Error policy: one `Result`, one-line diagnostics.
//! [`ConfigError::Syntax`] wraps the `toml` crate's error (which carries the
//! line/column of the offending text); [`ConfigError::Core`] wraps the core's
//! semantic [`widgets::ConfigError`].

use widgets::config::{
    BarConfig, DEFAULT_BATTERY_SUPPLY, DEFAULT_CLOCK_FORMAT, DEFAULT_GAP_CELLS,
    DEFAULT_POLL_INTERVAL_MS, PanelConfig, WidgetConfig,
};
use widgets::Section;

/// A configuration error: either TOML syntax/type problems or a semantic
/// validation failure from the core model.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigError {
    /// The text was not valid TOML (or did not match the expected shape).
    /// The message includes the toml crate's line/column diagnostics.
    Syntax(String),
    /// The text parsed, but the configuration violates a semantic rule.
    Core(widgets::ConfigError),
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::Syntax(msg) => write!(f, "{msg}"),
            ConfigError::Core(err) => write!(f, "{err}"),
        }
    }
}

impl std::error::Error for ConfigError {}

/// The `[bar]` table, mirrored 1:1. Unknown keys are rejected.
#[derive(serde::Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct Bar {
    /// Gap between adjacent widgets within a section (cells).
    #[serde(default)]
    gap_cells: Option<u32>,
    /// Poll interval for poll-based widgets (milliseconds).
    #[serde(default)]
    poll_interval_ms: Option<u64>,
}

/// The whole document. Both tables default to empty, so a blank `panel.toml`
/// is legal and means "all defaults".
#[derive(serde::Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct PanelWire {
    /// The `[bar]` table.
    #[serde(default)]
    bar: Bar,
    /// The `[[widgets]]` array.
    #[serde(default)]
    widgets: Vec<WidgetWire>,
}

/// One `[[widgets]]` entry. Externally tagged on `type`; each variant rejects
/// unknown keys, so `format = ...` on a battery widget is a load-time error
/// that names the table and the field.
#[derive(serde::Deserialize)]
#[serde(tag = "type")]
enum WidgetWire {
    #[serde(rename = "clock")]
    Clock(ClockWire),
    #[serde(rename = "battery")]
    Battery(BatteryWire),
    #[serde(rename = "workspaces")]
    Workspaces(WorkspacesWire),
    #[serde(rename = "text")]
    Text(TextWire),
    #[serde(rename = "template")]
    Template(TemplateWire),
}

#[derive(serde::Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct ClockWire {
    section: String,
    #[serde(default)]
    format: Option<String>,
}

#[derive(serde::Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct BatteryWire {
    section: String,
    #[serde(default)]
    supply: Option<String>,
    #[serde(default)]
    low: Option<u8>,
    #[serde(default)]
    critical: Option<u8>,
}

#[derive(serde::Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct WorkspacesWire {
    section: String,
    #[serde(default)]
    count: Option<usize>,
}

#[derive(serde::Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct TextWire {
    section: String,
    text: String,
}

#[derive(serde::Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct TemplateWire {
    section: String,
    text: String,
}

/// Parse `panel.toml` text into a fully default-applied, validated
/// [`PanelConfig`].
///
/// Order of operations: TOML syntax/type (serde), per-widget mapping
/// (section parsing + default application), then `PanelConfig::validate`.
pub fn parse(text: &str) -> Result<PanelConfig, ConfigError> {
    let wire: PanelWire = toml::from_str(text)
        .map_err(|e| ConfigError::Syntax(e.to_string()))?;

    let bar = BarConfig {
        gap_cells: wire.bar.gap_cells.unwrap_or(DEFAULT_GAP_CELLS),
        poll_interval_ms: wire
            .bar
            .poll_interval_ms
            .unwrap_or(DEFAULT_POLL_INTERVAL_MS),
    };

    let mut widgets = Vec::with_capacity(wire.widgets.len());
    for (index, widget) in wire.widgets.into_iter().enumerate() {
        widgets.push(match widget {
            WidgetWire::Clock(w) => WidgetConfig::clock(
                section_at(index, &w.section)?,
                w.format.or_else(|| Some(DEFAULT_CLOCK_FORMAT.to_string())),
            ),
            WidgetWire::Battery(w) => WidgetConfig::battery(
                section_at(index, &w.section)?,
                w.supply.or_else(|| Some(DEFAULT_BATTERY_SUPPLY.to_string())),
                w.low,
                w.critical,
            ),
            WidgetWire::Workspaces(w) => {
                WidgetConfig::workspaces(section_at(index, &w.section)?, w.count)
            }
            WidgetWire::Text(w) => WidgetConfig::text(section_at(index, &w.section)?, w.text),
            WidgetWire::Template(w) => {
                WidgetConfig::template(section_at(index, &w.section)?, w.text)
            }
        });
    }

    let config = PanelConfig { bar, widgets };
    config.validate().map_err(ConfigError::Core)?;
    Ok(config)
}

/// Parse one widget's `section` string, attaching the widget index to the
/// error so the diagnostic points at the right `[[widgets]]` entry.
fn section_at(index: usize, section: &str) -> Result<Section, ConfigError> {
    section.parse::<Section>().map_err(|_| {
        ConfigError::Core(widgets::ConfigError::UnknownSection {
            index,
            section: section.to_string(),
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use widgets::ConfigError as CoreError;
    use widgets::Section;

    /// A realistic full config, matching `examples/panel.toml`'s semantics.
    const FULL: &str = r#"
[bar]
gap_cells = 2
poll_interval_ms = 500

[[widgets]]
type = "workspaces"
section = "left"
count = 5

[[widgets]]
type = "text"
section = "left"
text = "PengyOS"

[[widgets]]
type = "clock"
section = "center"
format = "%a %b %d  %H:%M"

[[widgets]]
type = "template"
section = "right"
text = "{{hostname}}"

[[widgets]]
type = "battery"
section = "right"
supply = "BAT1"
low = 30
critical = 15
"#;

    #[test]
    fn empty_text_is_all_defaults() {
        let config = parse("").unwrap();
        assert_eq!(config, PanelConfig::default());
    }

    #[test]
    fn blank_document_is_a_valid_empty_panel() {
        let config = parse("# just a comment\n").unwrap();
        assert!(config.widgets.is_empty());
        assert_eq!(config.bar.gap_cells, 1);
        assert_eq!(config.bar.poll_interval_ms, 1000);
    }

    #[test]
    fn full_config_parses_to_the_exact_model() {
        let config = parse(FULL).unwrap();
        assert_eq!(config.bar.gap_cells, 2);
        assert_eq!(config.bar.poll_interval_ms, 500);
        assert_eq!(
            config.widgets,
            vec![
                WidgetConfig::workspaces(Section::Left, Some(5)),
                WidgetConfig::text(Section::Left, "PengyOS".into()),
                WidgetConfig::clock(
                    Section::Center,
                    Some("%a %b %d  %H:%M".into())
                ),
                WidgetConfig::template(Section::Right, "{{hostname}}".into()),
                WidgetConfig::battery(
                    Section::Right,
                    Some("BAT1".into()),
                    Some(30),
                    Some(15)
                ),
            ]
        );
    }

    #[test]
    fn omitted_keys_take_the_documented_defaults() {
        let config = parse(
            r#"
[[widgets]]
type = "clock"
section = "right"

[[widgets]]
type = "battery"
section = "right"

[[widgets]]
type = "workspaces"
section = "left"
"#,
        )
        .unwrap();
        assert_eq!(
            config.widgets,
            vec![
                WidgetConfig::clock(Section::Right, None),
                WidgetConfig::battery(Section::Right, None, None, None),
                WidgetConfig::workspaces(Section::Left, None),
            ]
        );
        // Spot-check the applied defaults.
        assert!(matches!(
            &config.widgets[0],
            WidgetConfig::Clock { format, .. } if format == DEFAULT_CLOCK_FORMAT
        ));
        assert!(matches!(
            &config.widgets[1],
            WidgetConfig::Battery { supply, .. } if supply == DEFAULT_BATTERY_SUPPLY
        ));
    }

    #[test]
    fn unknown_widget_type_is_a_syntax_error_naming_the_type() {
        let err = parse(
            r#"
[[widgets]]
type = "cpu"
section = "left"
"#,
        )
        .unwrap_err();
        match err {
            ConfigError::Syntax(msg) => {
                assert!(msg.contains("unknown variant"), "{msg}");
                assert!(msg.contains("`cpu`"), "{msg}");
                // serde lists the expected variants.
                assert!(msg.contains("clock"), "{msg}");
                assert!(msg.contains("battery"), "{msg}");
            }
            other => panic!("expected Syntax, got {other:?}"),
        }
    }

    #[test]
    fn unknown_key_in_bar_table_is_rejected() {
        // `bar.gap = 1` is *valid TOML* (dotted key) but `gap` is not a
        // recognised bar key — deny_unknown_fields must catch it.
        let err = parse("bar.gap = 1").unwrap_err();
        match err {
            ConfigError::Syntax(msg) => {
                assert!(msg.contains("`gap`"), "{msg}");
                assert!(msg.contains("gap_cells"), "{msg}");
            }
            other => panic!("expected Syntax, got {other:?}"),
        }
    }

    #[test]
    fn unknown_key_in_a_widget_is_rejected_and_names_it() {
        let err = parse(
            r#"
[[widgets]]
type = "battery"
section = "right"
low = 25
unknown_key = true
"#,
        )
        .unwrap_err();
        match err {
            ConfigError::Syntax(msg) => {
                assert!(msg.contains("unknown_key"), "{msg}");
                // serde lists the fields the battery table actually accepts.
                assert!(msg.contains("supply"), "{msg}");
            }
            other => panic!("expected Syntax, got {other:?}"),
        }
    }

    #[test]
    fn missing_required_keys_are_rejected() {
        // `type` missing.
        let err = parse(r#"[[widgets]]
section = "left""#).unwrap_err();
        assert!(
            matches!(err, ConfigError::Syntax(ref m) if m.contains("type")),
            "{err:?}"
        );
        // `section` missing.
        let err = parse(r#"[[widgets]]
type = "clock""#).unwrap_err();
        assert!(
            matches!(err, ConfigError::Syntax(ref m) if m.contains("section")),
            "{err:?}"
        );
        // `text` missing on a text widget.
        let err = parse(r#"[[widgets]]
type = "text"
section = "left""#).unwrap_err();
        assert!(
            matches!(err, ConfigError::Syntax(ref m) if m.contains("text")),
            "{err:?}"
        );
    }

    #[test]
    fn wrong_types_are_syntax_errors() {
        let err = parse(
            r#"
[[widgets]]
type = "battery"
section = "right"
low = "25"
"#,
        )
        .unwrap_err();
        // The toml error names the offending value and type, and points at the
        // `[[widgets]]` table (line 2) — it does not repeat the field name.
        assert!(
            matches!(
                err,
                ConfigError::Syntax(ref m)
                    if m.contains("expected u8") && m.contains("line 2")
            ),
            "{err:?}"
        );
        // A non-table document.
        let err = parse("42").unwrap_err();
        assert!(matches!(err, ConfigError::Syntax(_)), "{err:?}");
        // Malformed TOML.
        let err = parse("[bar").unwrap_err();
        assert!(matches!(err, ConfigError::Syntax(_)), "{err:?}");
    }

    #[test]
    fn unknown_section_is_a_core_error_with_the_widget_index() {
        let err = parse(
            r#"
[[widgets]]
type = "text"
section = "left"
text = "ok"

[[widgets]]
type = "text"
section = "middle"
text = "bad"
"#,
        )
        .unwrap_err();
        assert_eq!(
            err,
            ConfigError::Core(CoreError::UnknownSection {
                index: 1,
                section: "middle".into()
            })
        );
    }

    #[test]
    fn semantic_rules_fire_through_the_wire_layer() {
        // Bad clock format.
        let err = parse(
            r#"
[[widgets]]
type = "clock"
section = "center"
format = "%H %Q"
"#,
        )
        .unwrap_err();
        assert_eq!(
            err,
            ConfigError::Core(CoreError::UnknownClockToken {
                index: 0,
                percent_index: 3,
                token: 'Q'
            })
        );

        // Inverted battery thresholds.
        let err = parse(
            r#"
[[widgets]]
type = "battery"
section = "right"
low = 10
critical = 20
"#,
        )
        .unwrap_err();
        assert_eq!(
            err,
            ConfigError::Core(CoreError::BatteryThresholds {
                index: 0,
                low: 10,
                critical: 20
            })
        );

        // Poll interval below the floor.
        let err = parse("bar.poll_interval_ms = 100").unwrap_err();
        assert_eq!(
            err,
            ConfigError::Core(CoreError::PollIntervalTooSmall {
                value: 100,
                min: widgets::config::MIN_POLL_INTERVAL_MS
            })
        );

        // Whitespace-only text.
        let err = parse(
            r#"
[[widgets]]
type = "text"
section = "left"
text = "   "
"#,
        )
        .unwrap_err();
        assert_eq!(
            err,
            ConfigError::Core(CoreError::EmptyText {
                index: 0,
                kind: "text"
            })
        );
    }

    #[test]
    fn the_committed_example_config_parses() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("examples")
            .join("panel.toml");
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("{} must exist: {e}", path.display()));
        let config = parse(&text).expect("examples/panel.toml must be valid");
        assert!(!config.widgets.is_empty());
    }
}
