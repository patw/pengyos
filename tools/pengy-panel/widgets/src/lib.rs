//! # widgets — the headless-testable core of pengy-panel
//!
//! pengy-panel is a pure-Rust Wayland `layer-shell` status bar. It is split into
//! two crates so that the **data + layout** core can be unit-tested with
//! `cargo test` and *no compositor, no display, no network, no hardware*:
//!
//! ```text
//!  ┌──────────────────────┐        ┌───────────────────────────────┐
//!  │  pengy-panel (bin)    │        │  widgets (lib)                 │
//!  │                       │        │                                │
//!  │  • CLI arg parsing    │──────▶ │  • AppInfo (version metadata)  │
//!  │  • config loading     │  uses  │  • Widget trait (task 2)       │
//!  │  • layer-shell draw   │        │  • LayoutEngine (task 3)       │
//!  │  • event loop         │        │  • Clock/Battery/Workspace…    │
//!  │                       │        │   (all pure data, no rendering)│
//!  └──────────────────────┘        └───────────────────────────────┘
//!         Wayland / rendering                    headless-testable
//!
//!  The arrow is the ONLY legal dependency direction. The binary depends on the
//!  core; the core never depends on the binary or on Wayland.
//! ```
//!
//! ## The headless-test boundary
//!
//! Anything in this crate is, by contract, free of side effects that require a
//! running compositor. Real hardware (e.g. reading `/sys/class/power_supply`) is
//! injected behind a trait (`DataSource`) and swapped for a fixture in tests.
//! That is what makes the core
//! deterministic and CI-runnable.

/// The Battery widget (poll-based, sysfs-backed).
pub mod battery;
/// A deterministic, panel-shaped layout workload for the T8 criterion bench.
pub mod bench;
/// Build the panel's widget list from a validated config (task 7 bridge).
pub mod build;
/// The Clock widget (poll-based, wall-clock + timezone).
pub mod clock;
/// The panel configuration model: defaults + semantic validation (task 5).
pub mod config;
/// Calendar-date math (zero-dep) backing the Clock widget.
pub mod civil;
/// The [`LayoutEngine`]: pure geometry over widget renders (task 3).
pub mod layout;
/// The data-source seam (task 2): the only way a widget reaches the outside
/// world.
pub mod source;
/// The production [`DataSource`] (real `/sys` + clock + timezone reads).
pub mod source_impl;
/// The Text and Template widgets (literal / `{{var}}` labels).
pub mod text;
/// The [`Widget`] trait and the renderable description it produces (task 2).
pub mod widget;
/// The Workspaces widget (state-fed, driven by the backend).
pub mod workspaces;

// Re-export the core abstractions at the crate root so consumers write
// `widgets::Widget`, `widgets::DataSource`, … without reaching into modules.
pub use battery::{Battery, ChargeState};
pub use build::build_widget;
pub use build::build_widgets;
pub use clock::Clock;
pub use config::{
    BarConfig, ConfigError, PanelConfig, WidgetConfig,
};
pub use layout::{Layout, LayoutConfig, LayoutEngine, PlacedWidget};
pub use source::DataSource;
pub use source_impl::{parse_numeric_offset, SysfsSource};
pub use text::{Template, Text};
pub use widget::{Section, SectionParseError, Style, TextRun, Widget, WidgetRender};
pub use workspaces::{WorkspaceState, Workspaces};

/// Shared application version, baked in at compile time from the workspace
/// package version. Kept here (rather than in the binary) so both the binary
/// and the integration test can assert on a single source of truth.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Static, dependency-free metadata about the application.
///
/// This is the one piece of genuinely shared core logic that the scaffold
/// establishes. It models the kind of "pure data, no rendering" value that every
/// future widget will be built on: construct it, format it, assert on it — all
/// headlessly.
pub struct AppInfo {
    /// The binary name, e.g. `"pengy-panel"`.
    pub name: &'static str,
    /// The semantic version, e.g. `"0.1.0"`.
    pub version: &'static str,
}

impl AppInfo {
    /// Build the default application info for pengy-panel.
    pub fn new() -> Self {
        AppInfo {
            name: "pengy-panel",
            version: VERSION,
        }
    }

    /// Render a human-readable `"name version"` line.
    pub fn format(&self) -> String {
        format!("{} {}", self.name, self.version)
    }
}

impl Default for AppInfo {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_matches_workspace_package_version() {
        // If the workspace version changes, this test fails loudly instead of
        // the binary and the test disagreeing silently.
        assert_eq!(VERSION, "0.1.0");
    }

    #[test]
    fn app_info_reports_name_and_version() {
        let info = AppInfo::new();
        assert_eq!(info.name, "pengy-panel");
        assert_eq!(info.version, "0.1.0");
        assert_eq!(info.format(), "pengy-panel 0.1.0");
    }

    #[test]
    fn app_info_is_default() {
        assert_eq!(AppInfo::default().format(), "pengy-panel 0.1.0");
    }
}

/// Test-only doubles. Kept `#[cfg(test)]` so they never ship in the published
/// crate, yet stay usable from every test module in this crate.
#[cfg(test)]
pub mod testutil {
    use crate::source::DataSource;
    use std::collections::BTreeMap;

    /// A canned [`DataSource`] for tests: every read returns a pre-set value, or
    /// `None` if unset. This is the concrete proof of the task-2 contract that
    /// "widgets are unit-testable without a compositor" — swap the real source
    /// for this fixture and a widget becomes a pure, deterministic function.
    ///
    /// `power_supply` returns the stored value verbatim; store already-trimmed
    /// values to mirror the production source's trimming contract.
    #[derive(Default)]
    pub struct MockSource {
        pub now: Option<i64>,
        pub tz: Option<i32>,
        pub sys: BTreeMap<String, String>,
    }

    impl DataSource for MockSource {
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
}

/// End-to-end proof of the task-2 seam: a throwaway widget pulls real data out
/// of a `DataSource` double and turns it into a [`WidgetRender`], exercised the
/// way a real panel would (behind `dyn`, in a heterogeneous list).
#[cfg(test)]
mod seam {
    use crate::source::DataSource;
    use crate::testutil::MockSource;
    use crate::{Section, Style, TextRun, Widget};

    /// A tiny widget that reads the battery capacity from the source and renders
    /// it, falling back gracefully when the reading is absent. (Not shipped —
    /// the real Battery widget is a later task; this one only proves the seam.)
    struct Probe {
        section: Section,
    }

    impl Widget for Probe {
        fn id(&self) -> &str {
            "probe"
        }
        fn section(&self) -> Section {
            self.section
        }
        fn update(&mut self, source: &dyn DataSource) -> crate::WidgetRender {
            match source.power_supply("BAT0/capacity") {
                Some(cap) => crate::WidgetRender::new(
                    self.section(),
                    vec![TextRun::new(format!("{cap}%"), Style::Normal)],
                ),
                None => crate::WidgetRender::new(
                    self.section(),
                    vec![TextRun::new("no battery", Style::Warning)],
                ),
            }
        }
        fn as_any(&self) -> &dyn std::any::Any {
            self
        }
        fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
            self
        }
    }

    /// The panel ticks widgets through `dyn`, so the trait must be object-safe
    /// and usable in a heterogeneous list.
    #[test]
    fn widget_trait_is_object_safe_and_listable() {
        let mut widgets: Vec<Box<dyn Widget>> =
            vec![Box::new(Probe { section: Section::Right })];
        let src = MockSource::default();
        let render = widgets[0].update(&src);
        assert_eq!(render.section(), Section::Right);
        assert_eq!(widgets[0].id(), "probe");
    }

    #[test]
    fn probe_reads_data_from_the_source() {
        let mut w = Probe {
            section: Section::Left,
        };
        let mut src = MockSource::default();
        src.sys.insert("BAT0/capacity".to_string(), "63".to_string());

        let r = w.update(&src);
        assert_eq!(r.section(), Section::Left);
        assert_eq!(r.runs().len(), 1);
        assert_eq!(r.runs()[0].text, "63%");
        assert_eq!(r.width_cells(), 3);
    }

    #[test]
    fn probe_is_deterministic_for_the_same_source() {
        let mut a = Probe {
            section: Section::Center,
        };
        let mut b = Probe {
            section: Section::Center,
        };
        let mut src = MockSource::default();
        src.sys.insert("BAT0/capacity".to_string(), "10".to_string());
        assert_eq!(a.update(&src), b.update(&src));
    }

    #[test]
    fn probe_degrades_gracefully_when_data_is_missing() {
        let mut w = Probe {
            section: Section::Left,
        };
        let src = MockSource::default(); // no battery present
        let r = w.update(&src);
        assert_eq!(r.runs().len(), 1);
        assert_eq!(r.runs()[0].text, "no battery");
        assert_eq!(r.runs()[0].style, Style::Warning);
    }

    /// A state-fed widget (the pattern Workspaces will use) ignores the source
    /// and renders internal state — proving the trait accommodates both poll and
    /// state-fed widgets without any change to the abstraction.
    #[test]
    fn state_fed_widget_ignores_the_source() {
        struct Stateful {
            label: String,
        }
        impl Widget for Stateful {
            fn id(&self) -> &str {
                "stateful"
            }
            fn section(&self) -> Section {
                Section::Left
            }
            fn update(&mut self, _source: &dyn DataSource) -> crate::WidgetRender {
                crate::WidgetRender::new(self.section(), vec![TextRun::new(&self.label, Style::Normal)])
            }
            fn as_any(&self) -> &dyn std::any::Any {
                self
            }
            fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
                self
            }
        }

        let mut w = Stateful {
            label: "ws-1".to_string(),
        };
        let r = w.update(&MockSource::default());
        assert_eq!(r.runs()[0].text, "ws-1");

        // The backend "drives" the widget by mutating state, not via the source.
        w.label = "ws-2".to_string();
        let r2 = w.update(&MockSource::default());
        assert_eq!(r2.runs()[0].text, "ws-2");
    }

    /// The `as_any`/`as_any_mut` channel is how a backend reaches a state-fed
    /// widget *through the `dyn` list*: find it by id, downcast to the
    /// concrete type, mutate — and the *same instance* the panel ticks now
    /// renders the new state. A wrong-type downcast is `None`, not a panic.
    #[test]
    fn state_fed_channel_downcasts_through_the_dyn_list() {
        struct Driven {
            value: u32,
        }
        impl Widget for Driven {
            fn id(&self) -> &str {
                "driven"
            }
            fn section(&self) -> Section {
                Section::Center
            }
            fn update(&mut self, _source: &dyn DataSource) -> crate::WidgetRender {
                crate::WidgetRender::new(self.section(), vec![TextRun::new(self.value.to_string(), Style::Normal)])
            }
            fn as_any(&self) -> &dyn std::any::Any {
                self
            }
            fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
                self
            }
        }

        let mut widgets: Vec<Box<dyn Widget>> = vec![Box::new(Driven { value: 7 })];
        // Right type: the downcast reaches the instance.
        {
            let w = widgets.iter_mut().find(|w| w.id() == "driven").unwrap();
            let driven: &mut Driven = w.as_any_mut().downcast_mut().unwrap();
            driven.value = 42;
        }
        let r = widgets[0].update(&MockSource::default());
        assert_eq!(r.runs()[0].text, "42");

        // Wrong type: `None`, the instance is untouched.
        let wrong: Option<&mut Probe> = widgets
            .iter_mut()
            .find(|w| w.id() == "driven")
            .unwrap()
            .as_any_mut()
            .downcast_mut();
        assert!(wrong.is_none());
        assert_eq!(widgets[0].update(&MockSource::default()).runs()[0].text, "42");
    }
}
