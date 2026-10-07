//! The [`Widget`] trait and the renderable description it produces.
//!
//! A widget is a **pure `data -> output` transform**. It holds any internal
//! state it has and, on each tick, turns its injected [`crate::source::DataSource`]
//! into a [`WidgetRender`]. Because the source is injected, every widget is
//! unit-testable with a canned fixture and no compositor. A widget that is
//! *state-fed* (driven by the backend rather than by polling) simply renders its
//! internal state in [`Widget::update`] and may ignore the source.

use crate::source::DataSource;

/// Which of the three panel sections a widget wants to sit in.
///
/// The `LayoutEngine` arranges widgets within each
/// section; this is the widget's request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Section {
    /// The left-hand section (typically: workspaces).
    Left,
    /// The center section (typically: a clock or the active workspace title).
    Center,
    /// The right-hand section (typically: clock, battery, other indicators).
    Right,
}

/// Error returned by [`str::FromStr for Section`](Section::from_str) when the
/// string is not a recognised section name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SectionParseError(String);

impl std::fmt::Display for SectionParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "unknown section `{}` (expected `left`, `center`, or `right`)",
            self.0
        )
    }
}

impl std::error::Error for SectionParseError {}

/// Section names exactly as they appear in `panel.toml`.
///
/// The config wire layer (task 5) parses the `section = "..."` string through
/// this; keeping it in the core means the name list lives in exactly one
/// place and is headless-testable.
impl std::str::FromStr for Section {
    type Err = SectionParseError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "left" => Ok(Section::Left),
            "center" => Ok(Section::Center),
            "right" => Ok(Section::Right),
            other => Err(SectionParseError(other.to_string())),
        }
    }
}

/// Coarse, backend-agnostic style tags.
///
/// The rendering backend (the binary crate) maps each tag to a concrete font
/// weight and color. The core never picks pixels or fonts — it only *requests*
/// a mood (normal, emphasized, warning, critical) so the panel can, for example,
/// turn the battery red when it is critical.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Style {
    /// Plain text.
    Normal,
    /// Visually emphasized (e.g. bold).
    Emphasized,
    /// Something the user should notice (e.g. a battery that is low).
    Warning,
    /// Something that needs attention (e.g. a battery that is critical).
    Critical,
}

/// One run of visible text plus a coarse [`Style`] tag.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TextRun {
    /// The visible characters.
    pub text: String,
    /// The style tag the backend maps to a concrete font/color.
    pub style: Style,
}

impl TextRun {
    /// Build a run from any `Into<String>` (so both `&str` and `String` work).
    pub fn new(text: impl Into<String>, style: Style) -> Self {
        Self {
            text: text.into(),
            style,
        }
    }
}

/// The renderable description a widget produces on a tick.
///
/// This is the unit the `LayoutEngine` (T3) arranges and the backend (T6) paints.
/// It carries **no fonts, colors, or pixels** — only text, a style tag, and a
/// width in *text cells*. Measuring in cells (not pixels) keeps the core
/// backend-agnostic: the binary later maps cells to pixels using the selected
/// font.
///
/// Width is measured as the number of **UTF-8 chars** across the runs. That is a
/// deliberate, documented approximation of "text cells": it is exact for plain
/// ASCII and for most Latin text, and is a lower bound for wide/emoji glyphs.
/// A backend that needs precise grapheme or cell-width metrics can refine this
/// at render time without the core changing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WidgetRender {
    section: Section,
    runs: Vec<TextRun>,
    width_cells: u32,
}

impl WidgetRender {
    /// Build a render whose width defaults to the width of its content.
    pub fn new(section: Section, runs: Vec<TextRun>) -> Self {
        Self {
            width_cells: Self::content_width(&runs),
            section,
            runs,
        }
    }

    /// Override the width the layout engine should reserve for this widget
    /// (e.g. a fixed-width clock that should not make the bar jump as the
    /// digits change).
    pub fn with_width(mut self, width_cells: u32) -> Self {
        self.width_cells = width_cells;
        self
    }

    /// The width (in text cells) the layout engine should reserve. Defaults to
    /// the content width unless overridden with [`WidgetRender::with_width`].
    pub fn width_cells(&self) -> u32 {
        self.width_cells
    }

    /// Which section this render belongs to.
    pub fn section(&self) -> Section {
        self.section
    }

    /// The visible runs, in display order.
    pub fn runs(&self) -> &[TextRun] {
        &self.runs
    }

    /// Width of the runs measured in text cells (one per UTF-8 char).
    fn content_width(runs: &[TextRun]) -> u32 {
        runs.iter().map(|r| r.text.chars().count() as u32).sum()
    }
}

/// A status-bar widget: a pure `data -> output` transform.
///
/// Implementations are the built-in widgets (Clock, Battery, Workspaces,
/// Text/Template — a later task) and any user widget. The trait is object-safe,
/// so a panel can hold a heterogeneous `Vec<Box<dyn Widget>>` and tick them
/// uniformly.
pub trait Widget {
    /// A stable identifier used in config and diagnostics, e.g. `"clock"`.
    fn id(&self) -> &str;

    /// Which section this widget wants to sit in.
    fn section(&self) -> Section;

    /// Pull the latest data from `source` and produce the current render.
    ///
    /// A *state-fed* widget (e.g. Workspaces) may ignore `source` and instead
    /// render internal state that the backend updates out-of-band.
    fn update(&mut self, source: &dyn DataSource) -> WidgetRender;

    /// View this widget as a `dyn Any`.
    ///
    /// Downcast support: a panel holds widgets as `Vec<Box<dyn Widget>>` and
    /// can tick them uniformly, but a `dyn` handle cannot call a concrete
    /// setter like `Workspaces::set_state` or `Template::set_vars`. The
    /// backend drives a *state-fed* widget by finding it in the list (by
    /// [`Widget::id`]) and downcasting through this method, reaching the
    /// **same instance** the panel ticks. See
    /// `pengy-panel`'s `Panel::widget_mut` for the concrete channel.
    fn as_any(&self) -> &dyn std::any::Any;

    /// The `&mut` counterpart of [`Widget::as_any`] (needed to recover a
    /// `&mut T` handle and actually mutate the widget's state).
    fn as_any_mut(&mut self) -> &mut dyn std::any::Any;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    #[test]
    fn section_is_copy_eq_and_hashable() {
        let s = Section::Center;
        let s2 = s; // Copy, not move.
        assert_eq!(s, s2);
        assert_eq!(s, Section::Center);
        assert_ne!(Section::Left, Section::Right);

        let mut h1 = DefaultHasher::new();
        s.hash(&mut h1);
        let mut h2 = DefaultHasher::new();
        Section::Center.hash(&mut h2);
        assert_eq!(h1.finish(), h2.finish());
    }

    #[test]
    fn style_variants_are_distinct() {
        assert_ne!(Style::Normal, Style::Emphasized);
        assert_ne!(Style::Warning, Style::Critical);
        assert_eq!(Style::Normal, Style::Normal);
    }

    #[test]
    fn text_run_new_accepts_str_and_string() {
        let from_str = TextRun::new("hi", Style::Warning);
        assert_eq!(from_str.text, "hi");
        assert_eq!(from_str.style, Style::Warning);

        let from_string = TextRun::new(String::from("x"), Style::Normal);
        assert_eq!(from_string.text, "x");
    }

    #[test]
    fn render_new_defaults_width_to_content() {
        let render = WidgetRender::new(
            Section::Right,
            vec![
                TextRun::new("12", Style::Normal),
                TextRun::new(":34", Style::Emphasized),
            ],
        );
        assert_eq!(render.section(), Section::Right);
        assert_eq!(render.width_cells(), 5); // "12" + ":34"
        assert_eq!(render.runs().len(), 2);
        assert_eq!(render.runs()[1].style, Style::Emphasized);
    }

    #[test]
    fn render_empty_runs_has_zero_width() {
        let render = WidgetRender::new(Section::Center, vec![]);
        assert_eq!(render.width_cells(), 0);
        assert!(render.runs().is_empty());
    }

    #[test]
    fn render_width_counts_chars_not_bytes() {
        // "héllo" is 6 UTF-8 bytes but 5 chars (cells).
        let render =
            WidgetRender::new(Section::Left, vec![TextRun::new("héllo", Style::Normal)]);
        assert_eq!(render.width_cells(), 5);
    }

    #[test]
    fn with_width_overrides_content_width() {
        let render = WidgetRender::new(Section::Left, vec![TextRun::new("ab", Style::Normal)])
            .with_width(8);
        assert_eq!(render.width_cells(), 8);
    }

    #[test]
    fn render_is_clone_and_eq() {
        let a = WidgetRender::new(Section::Left, vec![TextRun::new("x", Style::Normal)]);
        let b = a.clone();
        assert_eq!(a, b);
    }

    #[test]
    fn section_parses_from_its_config_names() {
        assert_eq!("left".parse::<Section>().unwrap(), Section::Left);
        assert_eq!("center".parse::<Section>().unwrap(), Section::Center);
        assert_eq!("right".parse::<Section>().unwrap(), Section::Right);
    }

    #[test]
    fn section_rejects_unknown_names_with_a_useful_error() {
        let err = "middle".parse::<Section>().unwrap_err();
        assert_eq!(err, SectionParseError("middle".to_string()));
        let msg = err.to_string();
        assert!(msg.contains("`middle`"));
        assert!(msg.contains("`left`, `center`, or `right`"));
        // Case is significant: `Left` is not a config spelling.
        assert!("Left".parse::<Section>().is_err());
        assert!("".parse::<Section>().is_err());
    }
}
