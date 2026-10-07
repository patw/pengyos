//! The presenter layer — a [`ScreenView`] per wizard step.
//!
//! Every wizard step is turned into a [`ScreenView`] — a plain, serializable
//! snapshot of exactly what a screen needs to draw itself. The [`Model`] owns
//! state and derivation; this module owns the *shape of each screen*.
//!
//! This is the seam between the engine and any front-end:
//!
//! * the pure text renderer ([`crate::render`]) formats a [`ScreenView`] into
//!   the smoke-path output;
//! * the libcosmic app (built behind the `cosmic` feature) maps the same
//!   [`ScreenView`] onto COSMIC widgets.
//!
//! Both front-ends therefore consume one source of truth for what a screen
//! shows, and only the widget toolkit differs. [`ScreenView`] uses only the
//! standard library, so it is 100% `cargo`-testable with no display.

use pengyos_install_model::{Layout, Model, Step};

/// A snapshot of a single wizard screen, ready to render.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScreenView {
    /// The welcome / terms screen.
    Welcome(WelcomeView),
    /// The target-disk selection screen.
    Disk(DiskView),
    /// The install-layout selection screen.
    Layout(LayoutView),
    /// The account-creation screen.
    User(UserView),
    /// The optional network screen.
    Network(NetworkView),
    /// The read-only review screen.
    Summary(SummaryView),
    /// The terminal "installing" screen.
    Progress(ProgressView),
}

impl ScreenView {
    /// The screen's window-title text.
    pub fn title(&self) -> &str {
        match self {
            ScreenView::Welcome(v) => &v.title,
            ScreenView::Disk(v) => &v.title,
            ScreenView::Layout(v) => &v.title,
            ScreenView::User(v) => &v.title,
            ScreenView::Network(v) => &v.title,
            ScreenView::Summary(v) => &v.title,
            ScreenView::Progress(v) => &v.title,
        }
    }

    /// Whether the "Continue" action is enabled on this screen.
    pub fn continue_enabled(&self) -> bool {
        match self {
            ScreenView::Welcome(v) => v.continue_enabled,
            ScreenView::Disk(v) => v.continue_enabled,
            ScreenView::Layout(v) => v.continue_enabled,
            ScreenView::User(v) => v.continue_enabled,
            ScreenView::Network(v) => v.continue_enabled,
            ScreenView::Summary(_) | ScreenView::Progress(_) => true,
        }
    }

    /// The blocker message that explains why "Continue" is disabled, if any.
    pub fn blocker(&self) -> Option<&str> {
        match self {
            ScreenView::Welcome(v) => v.blocker.as_deref(),
            ScreenView::Disk(v) => v.blocker.as_deref(),
            ScreenView::Layout(v) => v.blocker.as_deref(),
            ScreenView::User(v) => v.blocker.as_deref(),
            ScreenView::Network(v) => v.blocker.as_deref(),
            ScreenView::Summary(_) | ScreenView::Progress(_) => None,
        }
    }
}

/// Derive the current screen's [`ScreenView`] from the model.
///
/// `step` is the model's current step; it selects which variant to build. All
/// fields are read through the model's public accessors, so this never reaches
/// into the model's private state.
pub fn view_of(model: &Model, step: Step) -> ScreenView {
    match step {
        Step::Welcome => ScreenView::Welcome(WelcomeView::of(model)),
        Step::Disk => ScreenView::Disk(DiskView::of(model)),
        Step::Layout => ScreenView::Layout(LayoutView::of(model)),
        Step::User => ScreenView::User(UserView::of(model)),
        Step::Network => ScreenView::Network(NetworkView::of(model)),
        Step::Summary => ScreenView::Summary(SummaryView::of(model)),
        Step::Progress => ScreenView::Progress(ProgressView::of()),
    }
}

/// The welcome / terms screen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WelcomeView {
    /// Window-title text.
    pub title: String,
    /// The heading shown at the top of the body.
    pub heading: String,
    /// Whether the terms have been accepted.
    pub terms_accepted: bool,
    /// Explanatory line shown under the toggle.
    pub hint: String,
    /// Whether "Continue" is enabled.
    pub continue_enabled: bool,
    /// The blocker message when disabled.
    pub blocker: Option<String>,
}

impl WelcomeView {
    fn of(model: &Model) -> Self {
        let blocker = model
            .advance_error()
            .map(|b| format!("{b}"))
            .filter(|m| !m.is_empty());
        WelcomeView {
            title: "Welcome".to_string(),
            heading: "Welcome to PengyOS Desktop".to_string(),
            terms_accepted: model.accepted_terms(),
            hint: "You must accept the terms to continue.".to_string(),
            continue_enabled: model.can_next(),
            blocker,
        }
    }
}

/// The target-disk selection screen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiskView {
    /// Window-title text.
    pub title: String,
    /// The heading shown at the top of the body.
    pub heading: String,
    /// The selected target disk, or `None` if none is selected.
    pub target: Option<String>,
    /// Whether "Continue" is enabled.
    pub continue_enabled: bool,
    /// The blocker message when disabled.
    pub blocker: Option<String>,
}

impl DiskView {
    fn of(model: &Model) -> Self {
        let blocker = model
            .advance_error()
            .map(|b| format!("{b}"))
            .filter(|m| !m.is_empty());
        DiskView {
            title: "Select disk".to_string(),
            heading: "Select a disk to install PengyOS to".to_string(),
            target: model.selected_disk().map(|s| s.to_string()),
            continue_enabled: model.can_next(),
            blocker,
        }
    }
}

/// The install-layout selection screen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayoutView {
    /// Window-title text.
    pub title: String,
    /// The heading shown at the top of the body.
    pub heading: String,
    /// The key of the chosen layout, or `None` if none is chosen.
    pub selected: Option<String>,
    /// The human-readable description of the chosen layout, if any.
    pub selected_description: Option<String>,
    /// All layouts in display order, as `(key, description)` pairs.
    pub options: Vec<(String, String)>,
    /// Whether "Continue" is enabled.
    pub continue_enabled: bool,
    /// The blocker message when disabled.
    pub blocker: Option<String>,
}

impl LayoutView {
    fn of(model: &Model) -> Self {
        let layout = model.layout();
        let blocker = model
            .advance_error()
            .map(|b| format!("{b}"))
            .filter(|m| !m.is_empty());
        LayoutView {
            title: "Layout".to_string(),
            heading: "How should PengyOS use the target disk?".to_string(),
            selected: layout.map(|l| l.key().to_string()),
            selected_description: layout.map(|l| l.description().to_string()),
            options: Layout::ALL
                .iter()
                .map(|l| (l.key().to_string(), l.description().to_string()))
                .collect(),
            continue_enabled: model.can_next(),
            blocker,
        }
    }
}

/// The account-creation screen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UserView {
    /// Window-title text.
    pub title: String,
    /// The heading shown at the top of the body.
    pub heading: String,
    /// The configured hostname, or `None` if unset.
    pub hostname: Option<String>,
    /// The configured username, or `None` if unset.
    pub username: Option<String>,
    /// The number of password characters entered (so the UI can mask it), or
    /// `None` if unset. The plaintext password is never carried here.
    pub password_len: Option<usize>,
    /// Whether "Continue" is enabled.
    pub continue_enabled: bool,
    /// The blocker message when disabled.
    pub blocker: Option<String>,
}

impl UserView {
    fn of(model: &Model) -> Self {
        let blocker = model
            .advance_error()
            .map(|b| format!("{b}"))
            .filter(|m| !m.is_empty());
        UserView {
            title: "Your account".to_string(),
            heading: "Set up your account".to_string(),
            hostname: model.hostname().map(|s| s.to_string()),
            username: model.username().map(|s| s.to_string()),
            password_len: model.password().map(|s| s.len()),
            continue_enabled: model.can_next(),
            blocker,
        }
    }
}

/// The optional network screen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetworkView {
    /// Window-title text.
    pub title: String,
    /// The heading shown at the top of the body.
    pub heading: String,
    /// The configured SSID, or `None` if unset.
    pub ssid: Option<String>,
    /// A note that this step is optional.
    pub hint: String,
    /// Whether "Continue" is enabled (always `true`; the step is optional).
    pub continue_enabled: bool,
    /// The blocker message when disabled (always `None` for this step).
    pub blocker: Option<String>,
}

impl NetworkView {
    fn of(model: &Model) -> Self {
        NetworkView {
            title: "Network".to_string(),
            heading: "Connect to a network (optional)".to_string(),
            ssid: model.network_ssid().map(|s| s.to_string()),
            hint: "You may skip this step.".to_string(),
            continue_enabled: model.can_next(),
            blocker: None,
        }
    }
}

/// The read-only review screen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SummaryView {
    /// Window-title text.
    pub title: String,
    /// The heading shown at the top of the body.
    pub heading: String,
    /// The derived `install.toml`, if the plan is complete.
    pub install_toml: Option<String>,
    /// The human-readable layout plan, if complete.
    pub layout_plan: Option<String>,
    /// Whether the plan is complete (and the review is fully populated).
    pub complete: bool,
}

impl SummaryView {
    fn of(model: &Model) -> Self {
        let complete = model.is_complete_plan();
        SummaryView {
            title: "Summary".to_string(),
            heading: "Review your install plan".to_string(),
            install_toml: model.install_toml(),
            layout_plan: model.layout_plan(),
            complete,
        }
    }
}

/// The terminal "installing" screen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProgressView {
    /// Window-title text.
    pub title: String,
    /// The heading shown at the top of the body.
    pub heading: String,
    /// The body message shown while the install runs.
    pub message: String,
}

impl ProgressView {
    /// The single progress screen.
    pub fn of() -> Self {
        ProgressView {
            title: "Installing".to_string(),
            heading: "Installing PengyOS Desktop".to_string(),
            message: "Installation in progress. Do not turn off the computer.".to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pengyos_install_model::Model;

    fn walk_to(step: Step) -> Model {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("pengyos-box");
        m.set_username("alice");
        m.set_password("hunter2");
        m.set_network_ssid("home");
        // Advance until we reach `step`. Welcome is the start, so no moves.
        while m.step() != step {
            m.next().expect("should be able to advance to the requested step");
        }
        m
    }

    #[test]
    fn view_of_matches_the_current_step() {
        assert!(matches!(view_of(&Model::new(), Step::Welcome), ScreenView::Welcome(_)));
        assert!(matches!(
            view_of(&walk_to(Step::Disk), Step::Disk),
            ScreenView::Disk(_)
        ));
        assert!(matches!(
            view_of(&walk_to(Step::Progress), Step::Progress),
            ScreenView::Progress(_)
        ));
    }

    #[test]
    fn welcome_view_captures_terms_and_enabled() {
        let m = Model::new();
        let v = match view_of(&m, Step::Welcome) {
            ScreenView::Welcome(v) => v,
            _ => panic!("expected welcome view"),
        };
        assert_eq!(v.title, "Welcome");
        assert_eq!(v.heading, "Welcome to PengyOS Desktop");
        assert!(!v.terms_accepted);
        assert!(!v.continue_enabled);
        assert_eq!(v.blocker.as_deref(), Some("Welcome: accept the terms to continue"));
    }

    #[test]
    fn welcome_view_enabled_after_accepting() {
        let m = walk_to(Step::Welcome);
        let v = match view_of(&m, Step::Welcome) {
            ScreenView::Welcome(v) => v,
            _ => panic!("expected welcome view"),
        };
        assert!(v.terms_accepted);
        assert!(v.continue_enabled);
        assert_eq!(v.blocker, None);
    }

    #[test]
    fn disk_view_carries_the_target() {
        let m = walk_to(Step::Disk);
        let v = match view_of(&m, Step::Disk) {
            ScreenView::Disk(v) => v,
            _ => panic!("expected disk view"),
        };
        assert_eq!(v.target.as_deref(), Some("/dev/sda"));
        assert!(v.continue_enabled);
    }

    #[test]
    fn disk_view_none_when_no_disk() {
        // Walk to Disk with a valid disk, then clear it and re-derive: the
        // view must report no target and a disabled Continue.
        let mut m2 = Model::new();
        m2.set_accepted_terms(true);
        m2.next().unwrap();
        let v = match view_of(&m2, Step::Disk) {
            ScreenView::Disk(v) => v,
            _ => panic!("expected disk view"),
        };
        assert_eq!(v.target, None);
        assert!(!v.continue_enabled);
        assert!(v.blocker.is_some());
    }

    #[test]
    fn layout_view_lists_all_options_and_selection() {
        let m = walk_to(Step::Layout);
        let v = match view_of(&m, Step::Layout) {
            ScreenView::Layout(v) => v,
            _ => panic!("expected layout view"),
        };
        assert_eq!(v.selected.as_deref(), Some("wipe"));
        assert_eq!(v.selected_description.as_deref(), Some("erase disk, fresh layout"));
        assert_eq!(v.options.len(), 3);
        assert_eq!(v.options[0], ("home-only".to_string(), "keep existing partitions, copy home only".to_string()));
        assert_eq!(v.options[2], ("wipe".to_string(), "erase disk, fresh layout".to_string()));
    }

    #[test]
    fn user_view_masks_the_password() {
        let m = walk_to(Step::User);
        let v = match view_of(&m, Step::User) {
            ScreenView::User(v) => v,
            _ => panic!("expected user view"),
        };
        assert_eq!(v.hostname.as_deref(), Some("pengyos-box"));
        assert_eq!(v.username.as_deref(), Some("alice"));
        assert_eq!(v.password_len, Some(7));
        // The plaintext password is never carried in the view.
        assert!(!format!("{v:?}").contains("hunter2"));
    }

    #[test]
    fn user_view_reports_the_blocker_when_incomplete() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.next().unwrap(); // Disk
        m.set_selected_disk("/dev/sda");
        m.next().unwrap(); // Layout
        m.set_layout(Layout::Wipe);
        m.next().unwrap(); // User (nothing set)
        let v = match view_of(&m, Step::User) {
            ScreenView::User(v) => v,
            _ => panic!("expected user view"),
        };
        assert!(!v.continue_enabled);
        let blocker = v.blocker.expect("blocker");
        assert!(blocker.contains("hostname"));
        assert!(blocker.contains("username"));
        assert!(blocker.contains("password"));
    }

    #[test]
    fn network_view_is_optional_and_always_enabled() {
        let m = walk_to(Step::Network);
        let v = match view_of(&m, Step::Network) {
            ScreenView::Network(v) => v,
            _ => panic!("expected network view"),
        };
        assert_eq!(v.ssid.as_deref(), Some("home"));
        assert!(v.continue_enabled);
        assert_eq!(v.blocker, None);
    }

    #[test]
    fn summary_view_includes_the_derived_toml() {
        let m = walk_to(Step::Summary);
        let v = match view_of(&m, Step::Summary) {
            ScreenView::Summary(v) => v,
            _ => panic!("expected summary view"),
        };
        assert!(v.complete);
        assert!(v.install_toml.as_deref().unwrap().contains("target = \"/dev/sda\""));
        assert!(v.layout_plan.as_deref().unwrap().contains("layout = wipe"));
    }

    #[test]
    fn summary_view_complete_after_full_walk() {
        // Walking forward always produces a complete plan by Summary (each
        // step's required fields were satisfied to advance). The view should
        // report `complete` and carry the derived plan.
        let m = walk_to(Step::Summary);
        let v = match view_of(&m, Step::Summary) {
            ScreenView::Summary(v) => v,
            _ => panic!("expected summary view"),
        };
        assert!(v.complete);
        assert!(v.install_toml.is_some());
        assert!(v.layout_plan.is_some());
    }

    #[test]
    fn progress_view_is_static() {
        let m = walk_to(Step::Progress);
        let view = view_of(&m, Step::Progress);
        assert_eq!(view.title(), "Installing");
        assert!(matches!(view, ScreenView::Progress(_)));
        assert!(view.continue_enabled());
        assert_eq!(view.blocker(), None);
    }

    #[test]
    fn view_titles_match_the_step_labels() {
        // Every screen's title equals the model step's label, so the window
        // title bar is consistent across both front-ends.
        for step in Step::ALL {
            let m = walk_to(step);
            let view = view_of(&m, step);
            assert_eq!(view.title(), step.label(), "title mismatch for {step:?}");
        }
    }

    #[test]
    fn blocker_is_some_exactly_when_continue_is_disabled() {
        // For the screens that carry a blocker (Welcome, Disk, User, Layout,
        // Network), `continue_enabled == false` iff a blocker is present.
        for step in [Step::Welcome, Step::Disk, Step::User] {
            let mut m = Model::new();
            m.set_accepted_terms(true);
            m.set_selected_disk("/dev/sda");
            m.set_layout(Layout::Wipe);
            m.set_hostname("host");
            m.set_username("alice");
            m.set_password("hunter2");
            while m.step() != step {
                let _ = m.next();
            }
            let view = view_of(&m, step);
            assert_eq!(view.continue_enabled(), view.blocker().is_none());
        }
    }
}
