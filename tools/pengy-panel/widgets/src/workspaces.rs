//! The [`Workspaces`] widget: a *state-fed* widget showing workspace buttons.
//!
//! Workspace state comes from the **compositor**, not from the
//! [`crate::source::DataSource`] — the source is deliberately scoped to the
//! non-compositor inputs a panel *polls* (see `source.rs`). So this widget does
//! **not** read the source; instead the backend drives it out-of-band by
//! calling [`Workspaces::set_state`], and [`Widget::update`] simply renders the
//! state that was last set. That is the state-fed pattern, and it is exactly
//! what makes this widget headless-testable: a test sets state and reads the
//! render, with no compositor involved.
//!
//! # The backend channel
//!
//! A panel ticks widgets through a `Vec<Box<dyn Widget>>`, but a `dyn` handle
//! cannot call a concrete method like `set_state`. The channel (concrete since
//! the backend task) is: the `Widget` trait provides `as_any`/`as_any_mut`,
//! and the driver exposes `Panel::widget_mut::<Workspaces>("workspaces")` —
//! find the widget by `id()`, downcast through `as_any_mut`, and the backend
//! calls `set_state` on the *same instance* the panel ticks. This module
//! exposes `set_state` on the concrete type — the
//! `tests::state_fed_widget_is_driven_out_of_band_and_ignores_source` test
//! pins that flow, and `seam::state_fed_channel_downcasts_through_the_dyn_list`
//! pins the downcast channel itself.

use crate::source::DataSource;
use crate::widget::{Section, Style, TextRun, Widget, WidgetRender};

/// The state of a single workspace, as reported by the compositor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkspaceState {
    /// No windows on this workspace.
    Empty,
    /// Has windows but is not the focused workspace.
    Visible,
    /// The focused workspace (has windows).
    Focused,
}

impl WorkspaceState {
    /// The glyph the widget renders for this state.
    ///
    /// `1`/`2`/… for empty workspaces, a filled `◉` for the focused one, a
    /// dim `●` for a merely-visible one. All are single UTF-8 chars so the
    /// layout engine's "1 char = 1 cell" approximation stays exact.
    fn glyph(self, number: u32) -> String {
        match self {
            WorkspaceState::Empty => number.to_string(),
            WorkspaceState::Visible => "●".to_string(),
            WorkspaceState::Focused => "◉".to_string(),
        }
    }
}

/// A *state-fed* widget rendering the workspace bar (e.g. `1 2 ◉ 4 5`).
///
/// Construct with a [`Section`] and the number of workspaces. The initial state
/// is all-empty; the backend updates it with [`Workspaces::set_state`].
#[derive(Debug, Clone)]
pub struct Workspaces {
    section: Section,
    /// `states.len()` is the number of workspaces (>= 1).
    states: Vec<WorkspaceState>,
}

impl Workspaces {
    /// Build a workspace widget with `count` workspaces, all empty.
    pub fn new(section: Section, count: usize) -> Self {
        Workspaces {
            section,
            states: vec![WorkspaceState::Empty; count.max(1)],
        }
    }

    /// Move the widget to a different section.
    pub fn with_section(mut self, section: Section) -> Self {
        self.section = section;
        self
    }

    /// The current number of workspaces.
    pub fn count(&self) -> usize {
        self.states.len()
    }

    /// The current state of each workspace (a copy).
    pub fn states(&self) -> Vec<WorkspaceState> {
        self.states.clone()
    }

    /// **Backend channel:** replace the workspace states wholesale.
    ///
    /// This is how the layer-shell backend (T6) pushes compositor state to this
    /// widget. The length defines the workspace count; calling it with a
    /// different length resizes the bar.
    pub fn set_state(&mut self, states: Vec<WorkspaceState>) {
        self.states = states;
    }
}

impl Widget for Workspaces {
    fn id(&self) -> &str {
        "workspaces"
    }

    fn section(&self) -> Section {
        self.section
    }

    fn update(&mut self, _source: &dyn DataSource) -> WidgetRender {
        // State-fed: the source is ignored; render the internal state.
        let runs: Vec<TextRun> = self
            .states
            .iter()
            .enumerate()
            .map(|(i, state)| {
                let glyph = state.glyph((i + 1) as u32);
                let style = match state {
                    WorkspaceState::Focused => Style::Emphasized,
                    _ => Style::Normal,
                };
                TextRun::new(glyph, style)
            })
            .collect();
        WidgetRender::new(self.section, runs)
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
    use crate::widget::Section;

    fn src() -> MockSource {
        // A state-fed widget must ignore whatever the source says.
        let mut s = MockSource::default();
        s.now = Some(12_345);
        s
    }

    #[test]
    fn workspaces_reports_id_and_section() {
        let w = Workspaces::new(Section::Left, 5);
        assert_eq!(w.id(), "workspaces");
        assert_eq!(w.section(), Section::Left);
        assert_eq!(w.count(), 5);
    }

    #[test]
    fn all_empty_renders_numbers() {
        let mut w = Workspaces::new(Section::Left, 5);
        let r = w.update(&src());
        let texts: Vec<&str> = r.runs().iter().map(|run| run.text.as_str()).collect();
        assert_eq!(texts, vec!["1", "2", "3", "4", "5"]);
        // Empty workspaces are all Normal style.
        assert!(r.runs().iter().all(|run| run.style == Style::Normal));
    }

    #[test]
    fn focused_workspace_is_emphasized_and_glyped() {
        let mut w = Workspaces::new(Section::Left, 4);
        w.set_state(vec![
            WorkspaceState::Empty,
            WorkspaceState::Focused,
            WorkspaceState::Empty,
            WorkspaceState::Empty,
        ]);
        let r = w.update(&src());
        let texts: Vec<&str> = r.runs().iter().map(|run| run.text.as_str()).collect();
        assert_eq!(texts, vec!["1", "◉", "3", "4"]);
        assert_eq!(r.runs()[1].style, Style::Emphasized);
        assert_eq!(r.runs()[0].style, Style::Normal);
    }

    #[test]
    fn visible_workspace_renders_a_dim_glyph() {
        let mut w = Workspaces::new(Section::Left, 3);
        w.set_state(vec![
            WorkspaceState::Visible,
            WorkspaceState::Focused,
            WorkspaceState::Empty,
        ]);
        let r = w.update(&src());
        let texts: Vec<&str> = r.runs().iter().map(|run| run.text.as_str()).collect();
        assert_eq!(texts, vec!["●", "◉", "3"]);
    }

    #[test]
    fn set_state_resizes_the_bar() {
        let mut w = Workspaces::new(Section::Left, 3);
        assert_eq!(w.count(), 3);
        w.set_state(vec![
            WorkspaceState::Empty,
            WorkspaceState::Empty,
            WorkspaceState::Empty,
            WorkspaceState::Empty,
            WorkspaceState::Empty,
            WorkspaceState::Empty,
            WorkspaceState::Empty,
            WorkspaceState::Empty,
            WorkspaceState::Empty,
        ]);
        assert_eq!(w.count(), 9);
        let r = w.update(&src());
        assert_eq!(r.runs().len(), 9);
        assert_eq!(r.runs()[8].text, "9");
    }

    #[test]
    fn new_with_zero_clamps_to_one_workspace() {
        let mut w = Workspaces::new(Section::Left, 0);
        assert_eq!(w.count(), 1);
        assert_eq!(w.update(&src()).runs()[0].text, "1");
    }

    #[test]
    fn state_fed_widget_is_driven_out_of_band_and_ignores_source() {
        // The whole point: two different sources, same state → identical render.
        let mut w = Workspaces::new(Section::Left, 2);
        w.set_state(vec![WorkspaceState::Focused, WorkspaceState::Empty]);

        let empty = MockSource::default();
        let mut noisy = MockSource::default();
        noisy.now = Some(999_999);
        noisy.tz = Some(3_600);

        assert_eq!(w.update(&empty), w.update(&noisy));

        // And the render reflects *state*, which the backend just set.
        assert_eq!(w.update(&src()).runs()[0].text, "◉");
    }

    #[test]
    fn states_getter_returns_a_copy_of_current_state() {
        let mut w = Workspaces::new(Section::Left, 3);
        w.set_state(vec![
            WorkspaceState::Empty,
            WorkspaceState::Visible,
            WorkspaceState::Focused,
        ]);
        assert_eq!(
            w.states(),
            vec![
                WorkspaceState::Empty,
                WorkspaceState::Visible,
                WorkspaceState::Focused
            ]
        );
    }

    #[test]
    fn width_is_one_cell_per_workspace() {
        let mut w = Workspaces::new(Section::Left, 4);
        w.set_state(vec![
            WorkspaceState::Empty,
            WorkspaceState::Focused,
            WorkspaceState::Empty,
            WorkspaceState::Empty,
        ]);
        // "1", "◉", "3", "4" → 4 chars total.
        assert_eq!(w.update(&src()).width_cells(), 4);
    }
}
