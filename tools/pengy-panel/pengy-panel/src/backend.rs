//! The [`Backend`] trait — the seam between the headless core and the
//! compositor.
//!
//! This is the only place a panel is allowed to touch "the outside" beyond
//! [`widgets::DataSource`] (task 6). Everything the panel does — ticking
//! widgets, computing a [`widgets::Layout`], painting — is expressed in terms
//! of this trait, so the entire driver loop runs against any implementation:
//!
//! - [`crate::headless::HeadlessBackend`] — a scripted, recording backend that
//!   needs no compositor at all. The whole panel can be driven, asserted on,
//!   and golden-filed with it (this is what T7's `--print-layout` builds on).
//! - The real Wayland `layer-shell` backend (a follow-up task) — connects to
//!   the compositor, owns the layer surface and pixel buffer, and maps the
//!   cell geometry here to real coordinates. Its tests are gated behind
//!   `PENGYOS_IT=1`; the trait and everything above it are not.
//!
//! # Geometry: cells, not pixels
//!
//! The core speaks in **text cells** (one per UTF-8 char; see
//! [`widgets::WidgetRender`]). A backend maps cells to pixels with its font at
//! paint time; sizes exchanged over this trait (`open`, `Resize`,
//! [`Frame::width`]) are in cells, so the core and every backend test stay
//! font-independent.

use std::time::Duration;

use widgets::{Section, TextRun};

/// One paintable widget in a [`Frame`], at its absolute bar position.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PaintedWidget {
    /// Start cell, from the left edge (0-based).
    pub x: u32,
    /// Cells the widget occupies.
    pub width: u32,
    /// The visible runs, in display order.
    pub runs: Vec<TextRun>,
}

/// One frame of the bar: everything to paint, with absolute positions.
///
/// A `Frame` is the complete output of one panel tick: it is what the
/// [`Panel`](crate::panel::Panel) hands to [`Backend::paint`]. Sections are
/// already resolved to coordinates; `placed` is in display order (ascending
/// `x`) and covers all three sections, so a backend paints it in one pass.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    /// The bar width (text cells) this frame was computed against.
    pub width: u32,
    /// Every placed widget, all sections, in display order.
    pub placed: Vec<PaintedWidget>,
}

impl Frame {
    /// Build an empty frame for a bar of `width` cells.
    pub fn new(width: u32) -> Self {
        Frame {
            width,
            placed: Vec::new(),
        }
    }

    /// True when nothing is placed (an empty panel paints a blank bar).
    pub fn is_empty(&self) -> bool {
        self.placed.is_empty()
    }
}

/// What a [`Backend::wait`] can report.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BackendEvent {
    /// The poll interval elapsed: tick the widgets and paint again.
    Tick,
    /// The bar was resized (e.g. the output changed). `width_cells` is the new
    /// bar width in text cells, computed by the backend from its font metrics.
    Resize { width_cells: u32 },
    /// The compositor destroyed the surface (or otherwise asked the panel to
    /// go away): the run loop exits.
    Quit,
}

/// Errors a [`Backend::open`] can report.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BackendError {
    /// Could not connect to the compositor or create the layer surface.
    /// The payload is a one-line, human-readable reason.
    Connect(String),
}

impl std::fmt::Display for BackendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BackendError::Connect(reason) => write!(f, "cannot attach to a compositor: {reason}"),
        }
    }
}

impl std::error::Error for BackendError {}

/// The rendering/event backend a [`Panel`](crate::panel::Panel) runs on.
///
/// Implementations:
/// * [`crate::headless::HeadlessBackend`] — deterministic, compositor-free
///   (tests, CI, T7).
/// * the Wayland `layer-shell` backend (follow-up task, `PENGYOS_IT`-gated
///   tests).
///
/// The trait is intentionally small: `open` (connect + create the surface),
/// `wait` (event loop), `paint` (draw one frame), `close` (tear down). The
/// backend owns all compositor state; the panel never sees any of it.
pub trait Backend {
    /// Connect, create the layer surface, and return the initial bar width in
    /// text cells (the backend measures the output and divides by its font
    /// cell width).
    fn open(&mut self) -> Result<u32, BackendError>;

    /// Block until the next [`BackendEvent`], returning `Tick` once `timeout`
    /// (the configured poll interval) has passed without anything else.
    fn wait(&mut self, timeout: Duration) -> BackendEvent;

    /// Paint one computed frame.
    fn paint(&mut self, frame: &Frame);

    /// Release resources. Called exactly once by
    /// [`Panel::run`](crate::panel::Panel::run), after `Quit`.
    fn close(&mut self);
}

/// The three sections in display order (left, then center, then right).
///
/// Used by the panel driver to collect a [`widgets::Layout`] into a
/// section-ordered paint list without magic-coding the enum.
pub const SECTIONS: &[Section] = &[Section::Left, Section::Center, Section::Right];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_new_is_empty_and_carrys_width() {
        let frame = Frame::new(37);
        assert_eq!(frame.width, 37);
        assert!(frame.is_empty());
        assert!(frame.placed.is_empty());
    }

    #[test]
    fn frame_reports_non_empty_when_anything_is_placed() {
        let mut frame = Frame::new(10);
        frame.placed.push(PaintedWidget {
            x: 0,
            width: 2,
            runs: vec![TextRun::new("ab", widgets::Style::Normal)],
        });
        assert!(!frame.is_empty());
        assert_eq!(frame.placed.len(), 1);
    }

    #[test]
    fn frame_is_clone_and_eq() {
        let a = Frame::new(10);
        let b = a.clone();
        assert_eq!(a, b);
    }

    #[test]
    fn painted_widget_holds_absolute_geometry_and_runs() {
        let pw = PaintedWidget {
            x: 5,
            width: 3,
            runs: vec![
                TextRun::new("12", widgets::Style::Normal),
                TextRun::new("%", widgets::Style::Warning),
            ],
        };
        assert_eq!(pw.x, 5);
        assert_eq!(pw.width, 3);
        assert_eq!(pw.runs.len(), 2);
        assert_eq!(pw.runs[1].style, widgets::Style::Warning);
    }

    #[test]
    fn backend_event_variants_are_distinct_and_eq() {
        assert_eq!(BackendEvent::Tick, BackendEvent::Tick);
        assert_eq!(
            BackendEvent::Resize { width_cells: 80 },
            BackendEvent::Resize { width_cells: 80 }
        );
        assert_ne!(BackendEvent::Tick, BackendEvent::Quit);
        assert_ne!(BackendEvent::Quit, BackendEvent::Resize { width_cells: 0 });
    }

    #[test]
    fn backend_error_renders_one_line() {
        let err = BackendError::Connect("WAYLAND_DISPLAY not set".to_string());
        let line = err.to_string();
        assert!(line.contains("WAYLAND_DISPLAY not set"));
        assert!(line.contains("cannot attach to a compositor"));
        assert!(!line.contains('\n'));
    }

    #[test]
    fn sections_constant_is_the_display_order_of_all_sections() {
        assert_eq!(SECTIONS, &[Section::Left, Section::Center, Section::Right]);
        assert_eq!(SECTIONS.len(), 3);
    }

    /// A throwaway backend proving the trait is object-safe and the four
    /// methods compose the way `Panel::run` uses them (open → tick/paint →
    /// wait → close).
    #[test]
    fn backend_trait_is_object_safe() {
        struct Stub;
        impl Backend for Stub {
            fn open(&mut self) -> Result<u32, BackendError> {
                Ok(42)
            }
            fn wait(&mut self, _timeout: Duration) -> BackendEvent {
                BackendEvent::Quit
            }
            fn paint(&mut self, _frame: &Frame) {}
            fn close(&mut self) {}
        }
        let mut b: Box<dyn Backend> = Box::new(Stub);
        assert_eq!(b.open().unwrap(), 42);
        assert_eq!(b.wait(Duration::from_millis(1000)), BackendEvent::Quit);
        b.paint(&Frame::new(42));
        b.close();
    }
}
