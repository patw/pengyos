//! # pengyos-install-model
//!
//! The pure, headless wizard state machine behind the PengyOS Desktop
//! installer.
//!
//! This crate has **no third-party dependencies** and needs **no display**, so
//! it is 100% `cargo`-testable. Both front-ends — the TUI and the libcosmic GUI
//! — drive this same engine so they always agree on step order, validation,
//! back-navigation and the derived install plan.
//!
//! ## Wizard flow
//!
//! ```text
//!  Welcome -> Disk -> Layout -> User -> Network -> Summary -> Progress
//! ```
//!
//! * **Welcome** — the user must accept the terms to leave this screen.
//! * **Disk** — a target disk must be selected.
//! * **Layout** — an install layout must be chosen.
//! * **User** — a hostname, username (>= 3 chars) and password (>= 6 chars)
//!   are required.
//! * **Network** — optional; the wizard may always proceed.
//! * **Summary** — a read-only review of the derived plan.
//! * **Progress** — terminal; reached once the install has started.
//!
//! ## Deriving the plan
//!
//! [`Model::install_toml`] returns the exact `install.toml` text once every
//! required answer has been supplied (i.e. when the wizard is at
//! [`Step::Summary` with a complete answer set`]). [`Model::layout_plan`] returns
//! the human-readable disk layout summary.

use std::fmt;
use std::str::FromStr;

/// A single wizard step.
///
/// The variant order *is* the navigation order; see [`Step::index`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    Welcome,
    Disk,
    Layout,
    User,
    Network,
    Summary,
    Progress,
}

impl Step {
    /// All steps in navigation order.
    pub const ALL: [Step; 7] = [
        Step::Welcome,
        Step::Disk,
        Step::Layout,
        Step::User,
        Step::Network,
        Step::Summary,
        Step::Progress,
    ];

    /// Position in navigation order (Welcome = 0 ... Progress = 6).
    pub const fn index(self) -> usize {
        match self {
            Step::Welcome => 0,
            Step::Disk => 1,
            Step::Layout => 2,
            Step::User => 3,
            Step::Network => 4,
            Step::Summary => 5,
            Step::Progress => 6,
        }
    }

    /// The step that follows this one, if any.
    pub const fn next(self) -> Option<Step> {
        match self {
            Step::Welcome => Some(Step::Disk),
            Step::Disk => Some(Step::Layout),
            Step::Layout => Some(Step::User),
            Step::User => Some(Step::Network),
            Step::Network => Some(Step::Summary),
            Step::Summary => Some(Step::Progress),
            Step::Progress => None,
        }
    }

    /// The step that precedes this one, if any.
    pub const fn prev(self) -> Option<Step> {
        match self {
            Step::Welcome => None,
            Step::Disk => Some(Step::Welcome),
            Step::Layout => Some(Step::Disk),
            Step::User => Some(Step::Layout),
            Step::Network => Some(Step::User),
            Step::Summary => Some(Step::Network),
            Step::Progress => Some(Step::Summary),
        }
    }

    /// Human-readable label for the step.
    pub const fn label(self) -> &'static str {
        match self {
            Step::Welcome => "Welcome",
            Step::Disk => "Select disk",
            Step::Layout => "Layout",
            Step::User => "Your account",
            Step::Network => "Network",
            Step::Summary => "Summary",
            Step::Progress => "Installing",
        }
    }
}

impl fmt::Display for Step {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.label())
    }
}

/// The install layout — what to do with the target disk.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layout {
    /// Keep the existing partitions and only copy the home directory.
    HomeOnly,
    /// Keep existing partitions but reformat the root filesystem.
    Preserve,
    /// Erase the disk and create a fresh layout.
    Wipe,
}

impl Layout {
    /// All layouts in display order.
    pub const ALL: [Layout; 3] = [Layout::HomeOnly, Layout::Preserve, Layout::Wipe];
    /// Human-readable description of the layout (shown in the layout plan).
    pub const fn description(self) -> &'static str {
        match self {
            Layout::HomeOnly => "keep existing partitions, copy home only",
            Layout::Preserve => "keep partitions, reformat root",
            Layout::Wipe => "erase disk, fresh layout",
        }
    }

    /// Short machine label (used in the derived `install.toml`).
    pub const fn key(self) -> &'static str {
        match self {
            Layout::HomeOnly => "home-only",
            Layout::Preserve => "preserve",
            Layout::Wipe => "wipe",
        }
    }
}

impl fmt::Display for Layout {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.key())
    }
}

/// Escape a value for inclusion in a TOML basic (double-quoted) string.
///
/// The generated `install.toml` is only valid when every interpolated value is
/// escaped — a hostname or SSID containing `"` or `\` would otherwise produce
/// malformed TOML (the closing quote lands early and the rest bleeds outside the
/// string). This implements the TOML basic-string escapes: `\\`, `\"`, `\b`,
/// `\t`, `\n`, `\f`, `\r`, and `\uXXXX` for the remaining C0 control characters
/// (U+0000–U+001F), matching the TOML 1.0 spec.
fn toml_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) <
                0x20 =>
            {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out
}

impl FromStr for Layout {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim() {
            "home-only" | "homeonly" => Ok(Layout::HomeOnly),
            "preserve" => Ok(Layout::Preserve),
            "wipe" => Ok(Layout::Wipe),
            other => Err(format!("unknown layout: {other:?}")),
        }
    }
}

/// Why the wizard refuses to advance.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Blocker {
    /// The current step's validation failed.
    Invalid(Step, String),
    /// There is no step after the current one (e.g. already at Progress).
    NoNext(Step),
}

impl fmt::Display for Blocker {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Blocker::Invalid(step, msg) => write!(f, "{step}: {msg}"),
            Blocker::NoNext(step) => write!(f, "{step}: cannot advance further"),
        }
    }
}

impl std::error::Error for Blocker {}

/// The whole wizard as a view-model.
///
/// Construct a fresh wizard with [`Model::new`], drive it with [`Model::next`]
/// and [`Model::back`], and read the derived plan with [`Model::install_toml`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Model {
    step: Step,
    accepted_terms: bool,
    selected_disk: Option<String>,
    layout: Option<Layout>,
    hostname: Option<String>,
    username: Option<String>,
    password: Option<String>,
    network_ssid: Option<String>,
}

/// A fresh wizard positioned at [`Step::Welcome`].
impl Model {
    /// A fresh wizard at [`Step::Welcome`].
    pub fn new() -> Model {
        Model {
            step: Step::Welcome,
            accepted_terms: false,
            selected_disk: None,
            layout: None,
            hostname: None,
            username: None,
            password: None,
            network_ssid: None,
        }
    }

    /// The current step.
    pub fn step(&self) -> Step {
        self.step
    }

    // --- Per-step setters -------------------------------------------------

    /// Record whether the user accepted the terms (Welcome step).
    pub fn set_accepted_terms(&mut self, accepted: bool) {
        self.accepted_terms = accepted;
    }

    /// Select the target disk (Disk step). An empty string clears the choice.
    pub fn set_selected_disk(&mut self, disk: impl Into<String>) {
        let disk = disk.into();
        self.selected_disk = if disk.trim().is_empty() {
            None
        } else {
            Some(disk)
        };
    }

    /// Choose the install layout (Layout step).
    pub fn set_layout(&mut self, layout: Layout) {
        self.layout = Some(layout);
    }

    /// Set the system hostname (User step).
    pub fn set_hostname(&mut self, hostname: impl Into<String>) {
        let hostname = hostname.into();
        self.hostname = if hostname.trim().is_empty() {
            None
        } else {
            Some(hostname)
        };
    }

    /// Set the account username (User step).
    pub fn set_username(&mut self, username: impl Into<String>) {
        let username = username.into();
        self.username = if username.trim().is_empty() {
            None
        } else {
            Some(username)
        };
    }

    /// Set the account password (User step).
    pub fn set_password(&mut self, password: impl Into<String>) {
        let password = password.into();
        self.password = if password.is_empty() {
            None
        } else {
            Some(password)
        };
    }

    /// Set the network SSID (Network step).
    pub fn set_network_ssid(&mut self, ssid: impl Into<String>) {
        let ssid = ssid.into();
        self.network_ssid = if ssid.trim().is_empty() {
            None
        } else {
            Some(ssid)
        };
    }

    // --- Validation -------------------------------------------------------

    /// True when the current step's answers are complete enough to advance.
    pub fn can_advance(&self) -> bool {
        self.advance_error().is_none()
    }

    /// The reason the wizard cannot advance from the current step, if any.
    pub fn advance_error(&self) -> Option<Blocker> {
        if self.step == Step::Progress {
            return Some(Blocker::NoNext(self.step));
        }
        let ok = match self.step {
            Step::Welcome => self.accepted_terms,
            Step::Disk => self.selected_disk.as_deref().map(str::trim).is_some_and(|s| !s.is_empty()),
            Step::Layout => self.layout.is_some(),
            Step::User => {
                let host = self.hostname.as_deref().map(str::trim).is_some_and(|s| !s.is_empty());
                let user = self
                    .username
                    .as_deref()
                    .map(|s| s.trim().len() >= 3)
                    .unwrap_or(false);
                let pass = self
                    .password
                    .as_deref()
                    .map(|s| s.len() >= 6)
                    .unwrap_or(false);
                host && user && pass
            }
            // Network is optional; the wizard may always proceed.
            // Progress is handled by the early return above; the arm keeps the
            // match exhaustive.
            Step::Network | Step::Summary | Step::Progress => true,
        };
        if ok {
            None
        } else {
            Some(Blocker::Invalid(self.step, self.reason_for(self.step)))
        }
    }

    fn reason_for(&self, step: Step) -> String {
        match step {
            Step::Welcome => "accept the terms to continue".into(),
            Step::Disk => "select a target disk".into(),
            Step::Layout => "choose an install layout".into(),
            Step::User => {
                let mut missing = Vec::new();
                if self.hostname.as_deref().map(|s| s.trim()).filter(|s| !s.is_empty()).is_none() {
                    missing.push("hostname");
                }
                match self.username.as_deref().map(|s| s.trim()) {
                    Some(u) if u.len() >= 3 => {}
                    _ => missing.push("username (at least 3 characters)"),
                }
                match self.password.as_deref() {
                    Some(p) if p.len() >= 6 => {}
                    _ => missing.push("password (at least 6 characters)"),
                }
                format!("please provide {}", missing.join(", "))
            }
            Step::Network => "network is optional; continue".into(),
            Step::Summary | Step::Progress => "already complete".into(),
        }
    }

    // --- Navigation -------------------------------------------------------

    /// Advance one step. Returns [`Err`] with the [`Blocker`] if the current
    /// step is not yet valid. On success the wizard moves forward and returns
    /// the step it landed on.
    pub fn next(&mut self) -> Result<Step, Blocker> {
        match self.advance_error() {
            None => {
                let next = self
                    .step
                    .next()
                    .expect("advance allowed implies a next step exists");
                self.step = next;
                Ok(next)
            }
            Some(Blocker::NoNext(step)) => Err(Blocker::NoNext(step)),
            Some(Blocker::Invalid(step, _)) => Err(Blocker::Invalid(step, self.reason_for(step))),
        }
    }

    /// The step you would land on if you advance successfully.
    pub fn next_step(&self) -> Option<Step> {
        self.step.next()
    }

    /// Whether advancing is possible right now.
    pub fn can_next(&self) -> bool {
        self.can_advance()
    }

    /// Go back one step. Cannot move before [`Step::Welcome`].
    pub fn back(&mut self) -> Option<Step> {
        let next = self.step.prev()?;
        self.step = next;
        Some(next)
    }

    /// Whether there is a previous step to return to.
    pub fn can_back(&self) -> bool {
        self.step != Step::Welcome
    }

    /// Whether the wizard has reached the terminal [`Step::Progress`].
    pub fn is_complete(&self) -> bool {
        self.step == Step::Progress
    }

    // --- Accessors --------------------------------------------------------

    /// Whether the terms have been accepted.
    pub fn accepted_terms(&self) -> bool {
        self.accepted_terms
    }

    /// The selected target disk, if any.
    pub fn selected_disk(&self) -> Option<&str> {
        self.selected_disk.as_deref()
    }

    /// The chosen install layout, if any.
    pub fn layout(&self) -> Option<Layout> {
        self.layout
    }

    /// The configured hostname, if any.
    pub fn hostname(&self) -> Option<&str> {
        self.hostname.as_deref()
    }

    /// The configured username, if any.
    pub fn username(&self) -> Option<&str> {
        self.username.as_deref()
    }

    /// The configured password, if any.
    pub fn password(&self) -> Option<&str> {
        self.password.as_deref()
    }

    /// The configured network SSID, if any.
    pub fn network_ssid(&self) -> Option<&str> {
        self.network_ssid.as_deref()
    }

    // --- Derived plan -----------------------------------------------------

    /// True when every required answer has been supplied.
    pub fn is_complete_plan(&self) -> bool {
        matches!(self.step, Step::Summary | Step::Progress)
            && self.accepted_terms
            && self.selected_disk.as_deref().map(|s| s.trim()).is_some_and(|s| !s.is_empty())
            && self.layout.is_some()
            && self
                .hostname
                .as_deref()
                .map(|s| s.trim())
                .is_some_and(|s| !s.is_empty())
            && self
                .username
                .as_deref()
                .map(|s| s.trim().len() >= 3)
                .unwrap_or(false)
    }

    /// The exact `install.toml` text for the current answers.
    ///
    /// Returns `None` until every required answer has been supplied (the wizard
    /// is at [`Step::Summary`] with a complete plan).
    pub fn install_toml(&self) -> Option<String> {
        if !self.is_complete_plan() {
            return None;
        }
        let disk = self.selected_disk.as_deref().unwrap();
        let layout = self.layout.unwrap().key();
        let hostname = self.hostname.as_deref().unwrap();
        let user = self.username.as_deref().unwrap();
        let mut out = String::new();
        out.push_str("# PengyOS Desktop install plan\n");
        out.push_str("# Generated by pengyos-install-model\n\n");
        out.push_str("[install]\n");
        out.push_str(&format!("target = \"{}\"\n", toml_escape(disk)));
        out.push_str(&format!("layout = \"{}\"\n", toml_escape(layout)));
        out.push_str(&format!("hostname = \"{}\"\n", toml_escape(hostname)));
        out.push_str(&format!("user = \"{}\"\n", toml_escape(user)));
        if let Some(ssid) = self.network_ssid.as_deref() {
            out.push_str(&format!("network_ssid = \"{}\"\n", toml_escape(ssid)));
        }
        Some(out)
    }

    /// The human-readable disk layout plan.
    ///
    /// Returns `None` until every required answer has been supplied (the wizard
    /// is at [`Step::Summary`] with a complete plan). The plan names the target
    /// disk, the chosen [`Layout`] and its human-readable description.
    pub fn layout_plan(&self) -> Option<String> {
        if !self.is_complete_plan() {
            return None;
        }
        let disk = self.selected_disk.as_deref().unwrap();
        let layout = self.layout.unwrap();
        let mut out = String::new();
        out.push_str("Layout plan\n");
        out.push_str(&format!("  target = {}\n", disk));
        out.push_str(&format!("  layout = {} ({})\n", layout.key(), layout.description()));
        Some(out)
    }

    /// Apply a single scripted answer key/value pair to the relevant field.
    ///
    /// Unknown keys are ignored. This is the primitive behind
    /// [`run_answers`] and the display-free `--answers` smoke path.
    pub fn apply_answer(&mut self, key: &str, value: &str) {
        match key {
            "accepted_terms" => self.set_accepted_terms(value.trim() == "true"),
            "selected_disk" => self.set_selected_disk(value),
            "layout" => {
                if let Ok(layout) = value.parse::<Layout>() {
                    self.set_layout(layout);
                }
            }
            "hostname" => self.set_hostname(value),
            "username" => self.set_username(value),
            "password" => self.set_password(value),
            "network_ssid" => self.set_network_ssid(value),
            _ => {}
        }
    }

    /// Drive the whole wizard from a scripted answer set, with no display.
    ///
    /// Every answer is applied, then the wizard is walked forward step by step.
    /// If a required step cannot be satisfied the returned [`Blocker`] explains
    /// which field is missing. On success the returned model sits at
    /// [`Step::Progress`] (i.e. [`Model::is_complete`]) with a complete plan.
    ///
    /// The answer set is a slice of `(key, value)` pairs whose members
    /// dereference to `str` — so both `&[(&str, &str)]` and
    /// `&[(String, String)]` work.
    pub fn run_answers<K, V>(answers: &[(K, V)]) -> Result<Model, Blocker>
    where
        K: AsRef<str>,
        V: AsRef<str>,
    {
        let mut model = Model::new();
        for (key, value) in answers.iter() {
            model.apply_answer(key.as_ref(), value.as_ref());
        }
        loop {
            if model.step() == Step::Progress {
                break;
            }
            if !model.can_advance() {
                return Err(model
                    .advance_error()
                    .unwrap_or_else(|| Blocker::Invalid(model.step(), "incomplete answer set".into())));
            }
            model.next()?;
        }
        Ok(model)
    }
}

impl Default for Model {
    fn default() -> Self {
        Model::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn starts_at_welcome() {
        let m = Model::new();
        assert_eq!(m.step(), Step::Welcome);
        assert!(!m.can_back());
        assert!(!m.is_complete());
    }

    #[test]
    fn step_order_is_stable() {
        assert_eq!(Step::Welcome.index(), 0);
        assert_eq!(Step::Progress.index(), 6);
        assert_eq!(Step::Disk.next(), Some(Step::Layout));
        assert_eq!(Step::Layout.prev(), Some(Step::Disk));
        assert_eq!(Step::Welcome.prev(), None);
        assert_eq!(Step::Progress.next(), None);
    }

    #[test]
    fn cannot_advance_welcome_without_accepting() {
        let mut m = Model::new();
        assert!(!m.can_advance());
        assert!(m.next().is_err());
    }

    #[test]
    fn welcome_to_disk_then_blocked_on_disk() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        assert!(m.can_advance());
        assert_eq!(m.next().unwrap(), Step::Disk);
        assert_eq!(m.step(), Step::Disk);
        // No disk selected yet.
        assert!(!m.can_advance());
        assert!(matches!(m.advance_error(), Some(Blocker::Invalid(Step::Disk, _))));
    }

    #[test]
    fn full_forward_walk_reaches_progress() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("pengyos-box");
        m.set_username("alice");
        m.set_password("hunter2");
        m.set_network_ssid("home");

        assert_eq!(m.next().unwrap(), Step::Disk);
        assert_eq!(m.next().unwrap(), Step::Layout);
        assert_eq!(m.next().unwrap(), Step::User);
        assert_eq!(m.next().unwrap(), Step::Network);
        assert_eq!(m.next().unwrap(), Step::Summary);
        assert!(m.is_complete_plan());
        assert_eq!(m.next().unwrap(), Step::Progress);
        assert!(m.is_complete());
        assert!(matches!(m.next(), Err(Blocker::NoNext(Step::Progress))));
    }

    #[test]
    fn user_requires_valid_fields() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.next().unwrap(); // Disk
        m.set_selected_disk("/dev/sda");
        m.next().unwrap(); // Layout
        m.set_layout(Layout::Preserve);
        m.next().unwrap(); // User
        m.set_hostname("host");
        m.set_username("ab"); // too short
        m.set_password("short"); // too short
        assert!(!m.can_advance());
        assert!(format!("{}", m.advance_error().unwrap()).contains("username"));
    }

    #[test]
    fn back_navigation() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        assert_eq!(m.next().unwrap(), Step::Disk);
        assert!(m.can_back());
        assert_eq!(m.back(), Some(Step::Welcome));
        assert!(!m.can_back());
        assert_eq!(m.back(), None);
    }

    #[test]
    fn empty_strings_clear_fields() {
        let mut m = Model::new();
        m.set_selected_disk("/dev/sda");
        m.set_selected_disk("   ");
        assert_eq!(m.selected_disk, None);
        m.set_username("bob");
        m.set_username("");
        assert_eq!(m.username, None);
    }

    #[test]
    fn layout_parsing() {
        assert_eq!("wipe".parse::<Layout>().unwrap(), Layout::Wipe);
        assert_eq!("home-only".parse::<Layout>().unwrap(), Layout::HomeOnly);
        assert!("nope".parse::<Layout>().is_err());
    }

    #[test]
    fn install_toml_none_before_complete() {
        let m = Model::new();
        assert!(m.install_toml().is_none());
    }

    #[test]
    fn layout_plan_rendered() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("host");
        m.set_username("alice");
        m.set_password("hunter2");
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        let plan = m.layout_plan().expect("plan");
        assert!(plan.contains("target = /dev/sda"));
        assert!(plan.contains("layout = wipe"));
    }

    // --- golden derivation of install.toml + layout plan ----------------------

    #[test]
    fn install_toml_golden_for_scripted_answers() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("pengyos-box");
        m.set_username("alice");
        m.set_password("hunter2");
        m.set_network_ssid("home");
        // Walk Welcome -> Disk -> Layout -> User -> Network -> Summary.
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        assert_eq!(m.step(), Step::Summary);

        let toml = m.install_toml().expect("plan should be complete");
        let expected = "\
# PengyOS Desktop install plan
# Generated by pengyos-install-model

[install]
target = \"/dev/sda\"
layout = \"wipe\"
hostname = \"pengyos-box\"
user = \"alice\"
network_ssid = \"home\"
";
        assert_eq!(toml, expected);
    }

    #[test]
    fn install_toml_omits_optional_network() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/nvme0n1");
        m.set_layout(Layout::HomeOnly);
        m.set_hostname("laptop");
        m.set_username("carol");
        m.set_password("password1");
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        let toml = m.install_toml().unwrap();
        assert!(!toml.contains("network_ssid"));
        assert!(toml.contains("target = \"/dev/nvme0n1\""));
        assert!(toml.contains("layout = \"home-only\""));
    }

    #[test]
    fn install_toml_escapes_special_characters() {
        // A hostname and SSID containing a quote, backslash and newline would
        // otherwise produce malformed TOML. The derived plan must escape them.
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("a\"b\\c");
        m.set_username("alice");
        m.set_password("hunter2");
        m.set_network_ssid("ho\"me\nx");
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        assert_eq!(m.step(), Step::Summary);

        let toml = m.install_toml().expect("plan should be complete");
        // Raw-string golden: the escapes appear literally (backslash + char).
        let expected = "\
# PengyOS Desktop install plan
# Generated by pengyos-install-model

[install]
target = \"/dev/sda\"
layout = \"wipe\"
hostname = \"a\\\"b\\\\c\"
user = \"alice\"
network_ssid = \"ho\\\"me\\nx\"
";
        assert_eq!(toml, expected);
    }

    #[test]
    fn install_toml_escaping_is_lossless() {
        // The escaping must be well-formed AND lossless: un-escaping the
        // derived values must recover the originals. This is the real
        // "valid TOML" property — a value that could not round-trip would be
        // ambiguous when parsed back.
        use super::toml_escape;
        for v in [
            "",
            "plain",
            "/dev/sda",
            "a\"b\\c",
            "ho\"me\nx\ty\rz\u{01}\u{1f}",
            "line1\nline2",
            "back\\slash",
            "quote\"inside",
            "tab\there",
        ] {
            let escaped = toml_escape(v);
            assert_eq!(unescape(&escaped), v, "round-trip failed for {v:?}");
        }
    }

    fn unescape(s: &str) -> String {
        // The inverse of [`toml_escape`] — enough to prove round-trip.
        let mut out = String::new();
        let mut chars = s.chars();
        while let Some(c) = chars.next() {
            if c != '\\' {
                out.push(c);
                continue;
            }
            match chars.next() {
                Some('"') => out.push('"'),
                Some('\\') => out.push('\\'),
                Some('n') => out.push('\n'),
                Some('r') => out.push('\r'),
                Some('t') => out.push('\t'),
                Some('b') => out.push('\u{08}'),
                Some('f') => out.push('\u{0c}'),
                Some('u') => {
                    let mut hex = String::new();
                    for _ in 0..4 {
                        hex.push(chars.next().unwrap_or('0'));
                    }
                    if let Ok(cp) = u32::from_str_radix(&hex, 16) {
                        if let Some(ch) = char::from_u32(cp) {
                            out.push(ch);
                        }
                    }
                }
                _ => {}
            }
        }
        out
    }

    #[test]
    fn layout_plan_golden() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("host");
        m.set_username("alice");
        m.set_password("hunter2");
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        let plan = m.layout_plan().expect("plan");
        let expected = "\
Layout plan
  target = /dev/sda
  layout = wipe (erase disk, fresh layout)
";
        assert_eq!(plan, expected);
    }

    #[test]
    fn layout_plan_none_before_complete() {
        let m = Model::new();
        assert!(m.layout_plan().is_none());
    }

    // --- state-machine navigation invariants --------------------------------

    #[test]
    fn all_steps_match_navigation_order() {
        assert_eq!(Step::ALL, Step::ALL);
        let mut expected_index = 0usize;
        for step in Step::ALL {
            assert_eq!(step.index(), expected_index);
            expected_index += 1;
        }
        assert_eq!(expected_index, Step::ALL.len());
    }

    #[test]
    fn every_step_has_a_nonempty_label() {
        for step in Step::ALL {
            let label = step.label();
            assert!(!label.is_empty(), "{step:?} has an empty label");
            assert_eq!(label, step.to_string());
        }
    }

    #[test]
    fn next_and_prev_are_inverse_along_the_chain() {
        for step in Step::ALL {
            match step.next() {
                Some(n) => assert_eq!(n.prev(), Some(step)),
                None => assert_eq!(step, Step::Progress),
            }
        }
    }

    #[test]
    fn cannot_back_past_welcome() {
        let mut m = Model::new();
        assert!(!m.can_back());
        assert_eq!(m.back(), None);
        // Still at Welcome — back-nav never runs off the front.
        assert_eq!(m.step(), Step::Welcome);
    }

    #[test]
    fn back_walks_the_whole_chain() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("host");
        m.set_username("alice");
        m.set_password("hunter2");

        // Walk all the way to Summary (Progress is terminal).
        assert_eq!(m.next().unwrap(), Step::Disk);
        assert_eq!(m.next().unwrap(), Step::Layout);
        assert_eq!(m.next().unwrap(), Step::User);
        assert_eq!(m.next().unwrap(), Step::Network);
        assert_eq!(m.next().unwrap(), Step::Summary);

        // Walk back one at a time and confirm each landing step.
        assert_eq!(m.back(), Some(Step::Network));
        assert_eq!(m.back(), Some(Step::User));
        assert_eq!(m.back(), Some(Step::Layout));
        assert_eq!(m.back(), Some(Step::Disk));
        assert_eq!(m.back(), Some(Step::Welcome));
        assert_eq!(m.back(), None);
    }

    #[test]
    fn back_nav_retains_prior_answers() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Preserve);
        m.set_hostname("retained");
        m.set_username("alice");
        m.set_password("hunter2");

        m.next().unwrap(); // Disk
        m.next().unwrap(); // Layout
        m.next().unwrap(); // User
        // Leave the User step; the answers must survive a return.
        assert_eq!(m.back(), Some(Step::Layout));
        assert_eq!(m.back(), Some(Step::Disk));
        assert_eq!(m.hostname(), Some("retained"));
        assert_eq!(m.username(), Some("alice"));
        assert_eq!(m.layout(), Some(Layout::Preserve));
    }

    #[test]
    fn re_answering_replaces_the_value() {
        let mut m = Model::new();
        m.set_selected_disk("/dev/sda");
        assert_eq!(m.selected_disk(), Some("/dev/sda"));
        m.set_selected_disk("/dev/sdb");
        assert_eq!(m.selected_disk(), Some("/dev/sdb"));
        // Clearing with whitespace drops it entirely.
        m.set_selected_disk("   ");
        assert_eq!(m.selected_disk(), None);
    }

    #[test]
    fn can_next_and_advance_error_are_consistent() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("host");
        m.set_username("alice");
        m.set_password("hunter2");
        for step in Step::ALL {
            m.step = step;
            assert_eq!(m.can_next(), m.advance_error().is_none());
            if let Some(n) = step.next() {
                assert_eq!(m.next_step(), Some(n));
            } else {
                assert_eq!(m.next_step(), None);
            }
        }
    }

    #[test]
    fn advance_error_names_the_current_step() {
        let mut m = Model::new();
        // At Welcome with no acceptance -> the blocker names Welcome.
        assert!(matches!(
            m.advance_error(),
            Some(Blocker::Invalid(Step::Welcome, _))
        ));
        m.set_accepted_terms(true);
        m.next().unwrap(); // Disk
        assert!(matches!(
            m.advance_error(),
            Some(Blocker::Invalid(Step::Disk, _))
        ));
    }

    #[test]
    fn progress_is_terminal_for_next_but_backable() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("host");
        m.set_username("alice");
        m.set_password("hunter2");
        for _ in 0..6 {
            m.next().unwrap();
        }
        assert_eq!(m.step(), Step::Progress);
        assert!(m.is_complete());
        assert!(matches!(m.next(), Err(Blocker::NoNext(Step::Progress))));
        // Back-nav still works from Progress.
        assert_eq!(m.back(), Some(Step::Summary));
    }

    #[test]
    fn reason_for_lists_every_missing_user_field() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.next().unwrap(); // Disk
        m.set_selected_disk("/dev/sda");
        m.next().unwrap(); // Layout
        m.set_layout(Layout::Wipe);
        m.next().unwrap(); // User (nothing set yet)
        let msg = format!("{}", m.advance_error().unwrap());
        assert!(msg.contains("hostname"));
        assert!(msg.contains("username"));
        assert!(msg.contains("password"));
    }

    #[test]
    fn run_answers_walks_headlessly_to_progress() {
        let answers: &[(&str, &str)] = &[
            ("accepted_terms", "true"),
            ("selected_disk", "/dev/sda"),
            ("layout", "wipe"),
            ("hostname", "host"),
            ("username", "alice"),
            ("password", "hunter2"),
        ];
        let m = Model::run_answers(answers).expect("should complete");
        assert!(m.is_complete());
        assert_eq!(m.step(), Step::Progress);
        // No network ssid supplied -> install.toml omits it.
        assert!(!m.install_toml().unwrap().contains("network_ssid"));
    }
}
