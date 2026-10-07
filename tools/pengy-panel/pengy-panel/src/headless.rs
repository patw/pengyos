//! The [`HeadlessBackend`]: a deterministic [`Backend`] that needs no
//! compositor at all.
//!
//! It is the concrete proof of the task-6 contract that "the backend behind a
//! trait keeps the panel headless-testable":
//!
//! * `open` returns a programmable start width (cells);
//! * `wait` replays a scripted queue of [`BackendEvent`]s (a `Tick` on an
//!   empty queue keeps the loop moving), recording every poll timeout it is
//!   asked with;
//! * `paint` records every [`Frame`] verbatim;
//! * `close` is recorded.
//!
//! [`render_frame`] projects a recorded frame back to a plain text line — one
//! cell per character, gaps as spaces — so a test (and T7's `--print-layout`)
//! can assert on exactly what the user would see, without any font or display.

use std::collections::VecDeque;
use std::time::Duration;

use crate::backend::{Backend, BackendEvent, BackendError, Frame};

/// Renders one [`Frame`] as a single text line of `frame.width` characters.
///
/// Cell `x + i` holds the `i`-th character of the widget placed at `x` (runs
/// laid out in order); unpainted cells are spaces. A run longer than its
/// reserved width is truncated at the bar edge rather than overflowing —
/// defensive, since the [`widgets::LayoutEngine`] never places a widget past
/// the edge.
pub fn render_frame(frame: &Frame) -> String {
    let mut cells: Vec<char> = vec![' '; frame.width as usize];
    for painted in &frame.placed {
        let mut pos = painted.x as usize;
        for run in &painted.runs {
            for ch in run.text.chars() {
                if pos < cells.len() {
                    cells[pos] = ch;
                }
                pos += 1;
            }
        }
    }
    cells.iter().collect()
}

/// A scripted, recording backend for driving a panel without a compositor.
#[derive(Debug, Default)]
pub struct HeadlessBackend {
    /// Bar width (`open` returns this) until the script says otherwise.
    pub start_width: u32,
    /// Events to replay from [`HeadlessBackend::wait`], in order.
    events: VecDeque<BackendEvent>,
    /// Every frame handed to [`Backend::paint`], in order.
    pub frames: Vec<Frame>,
    /// How many times `open` was called.
    pub open_calls: u32,
    /// How many times `close` was called.
    pub close_calls: u32,
    /// The poll timeout passed to each `wait`, in order.
    pub wait_timeouts: Vec<Duration>,
}

impl HeadlessBackend {
    /// A backend whose bar starts `width` cells wide and whose event queue is
    /// empty (`wait` therefore always answers [`BackendEvent::Tick`]).
    pub fn new(width: u32) -> Self {
        HeadlessBackend {
            start_width: width,
            ..Default::default()
        }
    }

    /// A backend with a pre-scripted event queue (consumed FIFO by `wait`;
    /// `Tick` is returned once the queue is exhausted).
    pub fn with_events(width: u32, events: impl IntoIterator<Item = BackendEvent>) -> Self {
        HeadlessBackend {
            start_width: width,
            events: events.into_iter().collect(),
            ..Default::default()
        }
    }

    /// Append one event to the script.
    pub fn push_event(&mut self, event: BackendEvent) -> &mut Self {
        self.events.push_back(event);
        self
    }

    /// The recorded frames (see [`Frame`]).
    pub fn paints(&self) -> &[Frame] {
        &self.frames
    }

    /// The text projection of recorded frame `index` (see [`render_frame`]).
    pub fn frame_text(&self, index: usize) -> String {
        render_frame(&self.frames[index])
    }
}

impl Backend for HeadlessBackend {
    fn open(&mut self) -> Result<u32, BackendError> {
        self.open_calls += 1;
        Ok(self.start_width)
    }

    fn wait(&mut self, timeout: Duration) -> BackendEvent {
        self.wait_timeouts.push(timeout);
        self.events.pop_front().unwrap_or(BackendEvent::Tick)
    }

    fn paint(&mut self, frame: &Frame) {
        self.frames.push(frame.clone());
    }

    fn close(&mut self) {
        self.close_calls += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::PaintedWidget;
    use widgets::{Style, TextRun};

    #[test]
    fn open_returns_the_programmable_width_and_counts() {
        let mut b = HeadlessBackend::new(77);
        assert_eq!(b.open().unwrap(), 77);
        assert_eq!(b.open_calls, 1);
        // Open is repeatable (idempotent width) but each call is counted.
        assert_eq!(b.open().unwrap(), 77);
        assert_eq!(b.open_calls, 2);
    }

    #[test]
    fn wait_replays_the_script_in_fifo_order_then_ticks() {
        let mut b = HeadlessBackend::with_events(10, vec![
            BackendEvent::Resize { width_cells: 20 },
            BackendEvent::Quit,
        ]);
        assert_eq!(b.wait(Duration::from_millis(250)), BackendEvent::Resize { width_cells: 20 });
        assert_eq!(b.wait(Duration::from_millis(250)), BackendEvent::Quit);
        // Queue exhausted: the loop keeps moving.
        assert_eq!(b.wait(Duration::from_millis(250)), BackendEvent::Tick);
    }

    #[test]
    fn wait_records_every_poll_timeout() {
        let mut b = HeadlessBackend::new(10);
        b.wait(Duration::from_millis(250));
        b.wait(Duration::from_millis(1000));
        assert_eq!(b.wait_timeouts, vec![Duration::from_millis(250), Duration::from_millis(1000)]);
    }

    #[test]
    fn push_event_appends_to_the_script() {
        let mut b = HeadlessBackend::new(10);
        b.push_event(BackendEvent::Quit);
        assert_eq!(b.wait(Duration::ZERO), BackendEvent::Quit);
    }

    #[test]
    fn paint_records_frames_verbatim_and_in_order() {
        let mut b = HeadlessBackend::new(4);
        let f1 = Frame::new(4);
        let mut f2 = Frame::new(6);
        f2.placed.push(PaintedWidget {
            x: 0,
            width: 2,
            runs: vec![TextRun::new("hi", Style::Normal)],
        });
        b.paint(&f1);
        b.paint(&f2);
        assert_eq!(b.paints(), &[f1, f2]);
        assert_eq!(b.frames.len(), 2);
    }

    #[test]
    fn close_is_recorded() {
        let mut b = HeadlessBackend::new(10);
        assert_eq!(b.close_calls, 0);
        b.close();
        assert_eq!(b.close_calls, 1);
    }

    /// Hand-computed: width 8; widget "ab" at x=0, "cd" at x=5.
    /// Cells 0,1 = a,b; 5,6 = c,d; cells 2-4 and 7 are spaces.
    #[test]
    fn render_frame_places_runs_at_absolute_cells() {
        let mut frame = Frame::new(8);
        frame.placed.push(PaintedWidget {
            x: 0,
            width: 2,
            runs: vec![TextRun::new("ab", Style::Normal)],
        });
        frame.placed.push(PaintedWidget {
            x: 5,
            width: 2,
            runs: vec![TextRun::new("cd", Style::Normal)],
        });
        assert_eq!(render_frame(&frame), "ab   cd ");
    }

    /// Hand-computed on width 10: two runs share the widget's cell span
    /// ("12" + "%") at x=3, so cells 3,4,5 are painted and 4 cells trail.
    #[test]
    fn render_frame_lays_out_runs_in_order_within_a_widget() {
        let mut frame = Frame::new(10);
        frame.placed.push(PaintedWidget {
            x: 3,
            width: 3,
            runs: vec![TextRun::new("12", Style::Normal), TextRun::new("%", Style::Warning)],
        });
        assert_eq!(render_frame(&frame), "   12%    ");
    }

    /// Non-ASCII is one char = one cell (the documented approximation), so a
    /// single-cell widget holds exactly one of the workspace glyphs.
    #[test]
    fn render_frame_counts_unicode_chars_as_single_cells() {
        let mut frame = Frame::new(5);
        frame.placed.push(PaintedWidget {
            x: 1,
            width: 1,
            runs: vec![TextRun::new("◉", Style::Emphasized)],
        });
        assert_eq!(render_frame(&frame), " ◉   ");
    }

    /// A run longer than its reserved width (possible only via an explicit
    /// `with_width` narrower than the content) is truncated, never a panic.
    #[test]
    fn render_frame_truncates_overlong_runs_at_the_bar_edge() {
        let mut frame = Frame::new(4);
        frame.placed.push(PaintedWidget {
            x: 2,
            width: 1,
            runs: vec![TextRun::new("abcd", Style::Normal)],
        });
        assert_eq!(render_frame(&frame), "  ab");
    }

    /// A widget placed with content extending past the bar (defensive: the
    /// layout engine never does this) is clipped, not panicked on.
    #[test]
    fn render_frame_clips_runs_at_the_bar_edge() {
        let mut frame = Frame::new(3);
        frame.placed.push(PaintedWidget {
            x: 2,
            width: 1,
            runs: vec![TextRun::new("zz", Style::Normal)],
        });
        assert_eq!(render_frame(&frame), "  z");
    }

    #[test]
    fn empty_frame_renders_as_all_spaces_of_the_bar_width() {
        assert_eq!(render_frame(&Frame::new(5)), "     ");
        assert_eq!(render_frame(&Frame::new(0)), "");
    }

    /// End-to-end double: drive the backend the way `Panel::run` does and
    /// observe the whole recorded session.
    #[test]
    fn headless_backend_records_a_full_session() {
        let mut b = HeadlessBackend::with_events(12, vec![BackendEvent::Tick, BackendEvent::Quit]);
        assert_eq!(b.open().unwrap(), 12);
        b.paint(&Frame::new(12));
        assert_eq!(b.wait(Duration::from_millis(1000)), BackendEvent::Tick);
        b.paint(&Frame::new(12));
        assert_eq!(b.wait(Duration::from_millis(1000)), BackendEvent::Quit);
        b.close();

        assert_eq!(b.open_calls, 1);
        assert_eq!(b.close_calls, 1);
        assert_eq!(b.frames.len(), 2);
        assert_eq!(b.wait_timeouts, vec![Duration::from_millis(1000); 2]);
        assert_eq!(b.frame_text(0), "            ");
    }
}
