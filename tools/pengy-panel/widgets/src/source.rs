//! The data-source seam: the only way a widget reaches the outside world.
//!
//! A widget never reads the clock, the filesystem, or a compositor directly. On
//! each tick it asks its [`DataSource`] for whatever it needs, and the *source*
//! decides how to obtain it:
//!
//! - in production the source is backed by real reads (the system clock,
//!   `/sys/class/power_supply`, …);
//! - in tests the source is a committed fixture (see the `#[cfg(test)]`
//!   doubles in this crate), which is what makes every widget a pure,
//!   deterministic `data -> output` function that `cargo test` can run with no
//!   compositor, no display, and no hardware.

/// The single seam between a widget and the outside world.
///
/// # Scope
///
/// This trait is intentionally limited to the **non-compositor** system inputs
/// a status bar *polls*: the wall clock, the timezone, and the power-supply
/// sysfs tree. Compositor-derived data (for example, the workspace state used
/// by the Workspaces widget) does **not** belong here — it is delivered to the
/// core by the backend (the task that implements the Workspaces widget) and
/// consumed by a *state-fed* widget, whose [`crate::widget::Widget::update`]
/// renders its internal state and may ignore the source entirely.
///
/// # Contract
///
/// Every method returns `None` rather than panicking when the underlying input
/// is absent or unreadable. Widgets **must** render a sensible fallback when a
/// reading is `None` (e.g. a desktop with no battery, or a clock that failed),
/// so the panel degrades gracefully instead of crashing.
pub trait DataSource {
    /// Whole seconds since the Unix epoch, or `None` if the host clock is
    /// unreadable.
    fn now_secs(&self) -> Option<i64>;

    /// Signed offset, in seconds **east** of UTC, of the local timezone, or
    /// `None` to fall back to UTC. Stored as `i32` because ±17.5 h covers every
    /// real-world offset with room to spare.
    fn tz_offset_secs(&self) -> Option<i32>;

    /// Read one value from the power-supply sysfs tree, where `entry` is a path
    /// relative to `/sys/class/power_supply` (e.g. `"BAT0/capacity"` or
    /// `"BAT0/status"`).
    ///
    /// Returns the file's text with surrounding whitespace trimmed, or `None`
    /// if the supply or file is absent (e.g. a laptop on AC with no battery, or
    /// a server). The production source is responsible for the trimming; this
    /// method is a pure read behind the seam.
    fn power_supply(&self, entry: &str) -> Option<String>;
}

#[cfg(test)]
mod tests {
    use super::DataSource;
    use crate::testutil::MockSource;

    /// The whole abstraction hinges on the trait being usable behind `dyn`.
    /// If this stops compiling, the trait is no longer object-safe and the
    /// headless-test boundary is broken.
    #[test]
    fn data_source_is_object_safe() {
        let boxed: Box<dyn DataSource> = Box::new(MockSource::default());
        assert_eq!(boxed.now_secs(), None);
        assert_eq!(boxed.tz_offset_secs(), None);
        assert_eq!(boxed.power_supply("BAT0/capacity"), None);
    }

    #[test]
    fn mock_source_returns_canned_values_verbatim() {
        let mut src = MockSource::default();
        src.now = Some(0);
        src.tz = Some(-5 * 3600); // UTC-05:00
        src.sys.insert("BAT0/capacity".to_string(), "87".to_string());

        assert_eq!(src.now_secs(), Some(0));
        assert_eq!(src.tz_offset_secs(), Some(-18_000));
        assert_eq!(src.power_supply("BAT0/capacity"), Some("87".to_string()));
        // A key the fixture never set is simply absent.
        assert_eq!(src.power_supply("BAT0/status"), None);
    }

    #[test]
    fn mock_source_defaults_to_all_missing() {
        let src = MockSource::default();
        assert_eq!(src.now_secs(), None);
        assert_eq!(src.tz_offset_secs(), None);
        assert!(src.sys.is_empty());
    }
}
