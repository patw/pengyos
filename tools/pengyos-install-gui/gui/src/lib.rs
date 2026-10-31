//! # pengyos-install-gui
//!
//! The libcosmic front-end for the PengyOS Desktop installer.
//!
//! This crate renders the pure [`pengyos_install_model::Model`] to a screen. The
//! rendering here is **pure text** and needs **no display**, so it is fully
//! `cargo`-testable. When built with the `cosmic` feature, a libcosmic
//! [`cosmic_app::App`] wraps these same renderers to draw each screen in the
//! desktop.
//!
//! The split matters: the model owns all state and derivation (shared with the
//! TUI), while this crate owns presentation. Both front-ends therefore agree on
//! behaviour, and only the presentation differs.
//!
//! ## Presentation layers
//!
//! * [`screen`] — the *presenter*: each step is turned into a [`screen::ScreenView`]
//!   snapshot (title, body, field values, whether "Continue" is enabled, the
//!   blocker message). This is the single source of truth for what a screen
//!   shows, and it uses only the standard library, so it is 100% `cargo`-testable.
//! * this module — the *renderer*: [`render`] formats a [`screen::ScreenView`]
//!   into plain text for the headless smoke path. The libcosmic app (built
//!   behind the `cosmic` feature) maps the same [`screen::ScreenView`] onto
//!   COSMIC widgets, so both front-ends render one and the same screen state.

pub mod restart;
pub mod router;
pub mod screen;

// The libcosmic app shell is build-gated behind the `cosmic` feature (off by
// default) so the workspace's default `cargo test` stays offline with the
// standard library only. It is compiled where libcosmic is available.
#[cfg(feature = "cosmic")]
pub mod cosmic_app;

pub use router::{Message, MessageRouter, route};
pub use screen::{
    DiskView, LayoutView, NetworkView, PengyView, ProgressView, ScreenView, SummaryView,
    UserView, WelcomeView, view_of,
};

use pengyos_install_model::{Model, Step};

/// A wizard screen — one per model step.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Screen {
    Welcome,
    Disk,
    Layout,
    User,
    Network,
    Pengy,
    Summary,
    Progress,
}

impl From<Step> for Screen {
    fn from(step: Step) -> Self {
        match step {
            Step::Welcome => Screen::Welcome,
            Step::Disk => Screen::Disk,
            Step::Layout => Screen::Layout,
            Step::User => Screen::User,
            Step::Network => Screen::Network,
            Step::Pengy => Screen::Pengy,
            Step::Summary => Screen::Summary,
            Step::Progress => Screen::Progress,
        }
    }
}

impl Screen {
    /// The label shown in the window title bar.
    pub fn title(self) -> &'static str {
        match self {
            Screen::Welcome => "Welcome",
            Screen::Disk => "Select disk",
            Screen::Layout => "Layout",
            Screen::User => "Your account",
            Screen::Network => "Network",
            Screen::Pengy => "Pengy",
            Screen::Summary => "Summary",
            Screen::Progress => "Installing",
        }
    }
}

/// Render the current screen from the model as plain text.
///
/// The output is deterministic and display-free, which is what makes the gui
/// crate testable without a running desktop. The [`ScreenView`] is the single
/// source of truth for what the screen shows; this renderer just formats it.
/// The libcosmic app (built behind the `cosmic` feature) consumes the same
/// [`ScreenView`], so both front-ends render one and the same screen state.
pub fn render(model: &Model) -> String {
    let view = view_of(model, model.step());
    match &view {
        ScreenView::Welcome(v) => render_welcome(v),
        ScreenView::Disk(v) => render_disk(v),
        ScreenView::Layout(v) => render_layout(v),
        ScreenView::User(v) => render_user(v),
        ScreenView::Network(v) => render_network(v),
        ScreenView::Pengy(v) => render_pengy(v),
        ScreenView::Summary(v) => render_summary(v),
        ScreenView::Progress(_) => render_progress(),
    }
}

/// Render every screen in order — the full wizard transcript.
pub fn render_all(model: &Model) -> String {
    let mut out = String::new();
    for step in Step::ALL {
        let view = view_of(model, step);
        out.push_str(&format!("=== {} ===\n", view.title()));
        match &view {
            ScreenView::Welcome(v) => out.push_str(&render_welcome(v)),
            ScreenView::Disk(v) => out.push_str(&render_disk(v)),
            ScreenView::Layout(v) => out.push_str(&render_layout(v)),
            ScreenView::User(v) => out.push_str(&render_user(v)),
            ScreenView::Network(v) => out.push_str(&render_network(v)),
            ScreenView::Pengy(v) => out.push_str(&render_pengy(v)),
            ScreenView::Summary(v) => out.push_str(&render_summary(v)),
            ScreenView::Progress(_) => out.push_str(&render_progress()),
        }
        out.push('\n');
    }
    out
}

fn rule() -> String {
    "-".repeat(48)
}

fn render_welcome(v: &WelcomeView) -> String {
    let mut out = String::new();
    out.push_str(&v.heading);
    out.push('\n');
    out.push_str(&rule());
    out.push('\n');
    out.push_str(&format!("Terms accepted: {}\n", flag(v.terms_accepted)));
    out.push_str(&v.hint);
    out.push('\n');
    out.push_str(&button(v.continue_enabled));
    out
}

fn render_disk(v: &DiskView) -> String {
    let mut out = String::new();
    out.push_str(&v.heading);
    out.push('\n');
    out.push_str(&rule());
    out.push('\n');
    out.push_str(&format!("Target: {}\n", disk_or_none(v.target.as_deref())));
    out.push_str(&button(v.continue_enabled));
    out
}

fn render_layout(v: &LayoutView) -> String {
    let mut out = String::new();
    out.push_str(&v.heading);
    out.push('\n');
    out.push_str(&rule());
    out.push('\n');
    match &v.selected {
        Some(key) => out.push_str(&format!("Layout: {key}\n")),
        None => out.push_str("Layout: (none selected)\n"),
    }
    out.push_str("Options: home-only, preserve, wipe.\n");
    out.push_str(&button(v.continue_enabled));
    out
}

fn render_user(v: &UserView) -> String {
    let mut out = String::new();
    out.push_str(&v.heading);
    out.push('\n');
    out.push_str(&rule());
    out.push('\n');
    out.push_str(&format!("Hostname: {}\n", opt(v.hostname.as_deref())));
    out.push_str(&format!("Username: {}\n", opt(v.username.as_deref())));
    out.push_str(&format!("Password: {}\n", masked(v.password_len)));
    out.push_str(&button(v.continue_enabled));
    out
}

fn render_network(v: &NetworkView) -> String {
    let mut out = String::new();
    out.push_str(&v.heading);
    out.push('\n');
    out.push_str(&rule());
    out.push('\n');
    out.push_str(&format!("SSID: {}\n", opt(v.ssid.as_deref())));
    out.push_str(&v.hint);
    out.push('\n');
    out.push_str(&button(v.continue_enabled));
    out
}

fn render_pengy(v: &PengyView) -> String {
    let mut out = String::new();
    out.push_str(&v.heading);
    out.push('\n');
    out.push_str(&rule());
    out.push('\n');
    out.push_str(&format!("Base URL: {}\n", opt(v.base_url.as_deref())));
    out.push_str(&format!("API key: {}\n", masked(v.api_key_len)));
    out.push_str(&format!("Model: {}\n", opt(v.model.as_deref())));
    out.push_str(&v.hint);
    out.push('\n');
    out.push_str(&button(v.continue_enabled));
    out
}

fn render_summary(v: &SummaryView) -> String {
    let mut out = String::new();
    out.push_str(&v.heading);
    out.push('\n');
    out.push_str(&rule());
    out.push('\n');
    match &v.install_toml {
        Some(toml) => out.push_str(toml),
        None => out.push_str("(plan not yet complete)\n"),
    }
    out
}

fn render_progress() -> String {
    let mut out = String::new();
    out.push_str("Installing PengyOS Desktop\n");
    out.push_str(&rule());
    out.push('\n');
    out.push_str("Installation in progress. Do not turn off the computer.\n");
    out
}

// --- small helpers --------------------------------------------------------

fn flag(b: bool) -> &'static str {
    if b {
        "yes"
    } else {
        "no"
    }
}

fn button(continue_enabled: bool) -> String {
    if continue_enabled {
        "[ Continue ]".to_string()
    } else {
        "[ Continue ] (disabled)".to_string()
    }
}

fn disk_or_none(disk: Option<&str>) -> String {
    match disk {
        Some(d) if !d.trim().is_empty() => d.to_string(),
        _ => "(none selected)".to_string(),
    }
}

fn opt(v: Option<&str>) -> String {
    match v {
        Some(s) if !s.is_empty() => s.to_string(),
        _ => "(empty)".to_string(),
    }
}

fn masked(password_len: Option<usize>) -> String {
    match password_len {
        Some(n) if n > 0 => "*".repeat(n).to_string(),
        _ => "(empty)".to_string(),
    }
}

/// Parse a flat JSON object of string values into an ordered list of
/// `(key, value)` pairs — the shape the model's [`apply_answer`] expects.
///
/// This is a deliberately tiny, dependency-free reader for the `--answers`
/// smoke path; it accepts only a top-level object whose values are JSON
/// strings. Whitespace between tokens is ignored.
pub fn answers_from_json(input: &str) -> Result<Vec<(String, String)>, String> {
    let chars: Vec<char> = input.chars().collect();
    let mut p = JsonParser { chars: &chars, pos: 0 };
    p.skip_ws();
    let pairs = p.parse_object()?;
    p.skip_ws();
    if p.pos != p.chars.len() {
        return Err(format!("trailing characters near position {}", p.pos));
    }
    Ok(pairs)
}

struct JsonParser<'a> {
    chars: &'a [char],
    pos: usize,
}

impl<'a> JsonParser<'a> {
    fn peek(&self) -> Option<char> {
        self.chars.get(self.pos).copied()
    }

    fn next(&mut self) -> Option<char> {
        let c = self.chars.get(self.pos).copied();
        if c.is_some() {
            self.pos += 1;
        }
        c
    }

    fn skip_ws(&mut self) {
        while let Some(c) = self.peek() {
            if c.is_whitespace() {
                self.pos += 1;
            } else {
                break;
            }
        }
    }

    fn parse_object(&mut self) -> Result<Vec<(String, String)>, String> {
        if self.next() != Some('{') {
            return Err("expected a JSON object".into());
        }
        let mut pairs = Vec::new();
        self.skip_ws();
        if self.peek() == Some('}') {
            self.next();
            return Ok(pairs);
        }
        loop {
            self.skip_ws();
            let key = self.parse_string()?;
            self.skip_ws();
            if self.next() != Some(':') {
                return Err(format!("expected ':' after key {key:?}"));
            }
            self.skip_ws();
            let value = self.parse_string()?;
            pairs.push((key, value));
            self.skip_ws();
            match self.next() {
                Some(',') => continue,
                Some('}') => break,
                other => return Err(format!("expected ',' or '}}', got {other:?}")),
            }
        }
        Ok(pairs)
    }

    fn parse_string(&mut self) -> Result<String, String> {
        if self.next() != Some('"') {
            return Err("expected a string literal".into());
        }
        let mut out = String::new();
        loop {
            match self.next() {
                Some('"') => break,
                Some('\\') => match self.next() {
                    Some('"') => out.push('"'),
                    Some('\\') => out.push('\\'),
                    Some('/') => out.push('/'),
                    Some('n') => out.push('\n'),
                    Some('t') => out.push('\t'),
                    other => return Err(format!("unsupported escape \\{other:?}")),
                },
                Some(c) => out.push(c),
                None => return Err("unterminated string".into()),
            }
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pengyos_install_model::Model;

    fn complete() -> Model {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(pengyos_install_model::Layout::Wipe);
        m.set_hostname("pengyos-box");
        m.set_username("alice");
        m.set_password("hunter2");
        m.set_network_ssid("home");
        // Walk Welcome -> Disk -> Layout -> User -> Network -> Pengy -> Summary.
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m.next().unwrap();
        m
    }

    #[test]
    fn welcome_shows_terms_and_button() {
        let m = Model::new();
        let text = render(&m);
        assert!(text.contains("Welcome to PengyOS Desktop"));
        assert!(text.contains("Terms accepted: no"));
        assert!(text.contains("(disabled)"));
    }

    #[test]
    fn welcome_enabled_after_accepting() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        assert!(render(&m).contains("[ Continue ]"));
        assert!(!render(&m).contains("(disabled)"));
    }

    #[test]
    fn disk_screen_reports_target() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.next().unwrap();
        m.set_selected_disk("/dev/sda");
        let text = render(&m);
        assert!(text.contains("Target: /dev/sda"));
    }

    #[test]
    fn summary_includes_derived_toml() {
        let m = complete();
        let text = render(&m);
        assert!(text.contains("[install]"));
        assert!(text.contains("target = \"/dev/sda\""));
    }

    #[test]
    fn password_is_never_rendered_in_clear() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.next().unwrap(); // Disk
        m.set_selected_disk("/dev/sda");
        m.next().unwrap(); // Layout
        m.set_layout(pengyos_install_model::Layout::Wipe);
        m.next().unwrap(); // User
        m.set_hostname("pengyos-box");
        m.set_username("alice");
        m.set_password("hunter2");
        let text = render(&m);
        assert!(!text.contains("hunter2"));
        assert!(text.contains("*******"));
    }

    #[test]
    fn render_all_covers_all_screens() {
        let m = complete();
        let text = render_all(&m);
        for title in [
            "Welcome",
            "Select disk",
            "Layout",
            "Your account",
            "Network",
            "Pengy",
            "Summary",
            "Installing",
        ] {
            assert!(text.contains(&format!("=== {title} ===")), "missing {title}");
        }
    }

    #[test]
    fn pengy_screen_masks_the_api_key() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.next().unwrap(); // Disk
        m.set_selected_disk("/dev/sda");
        m.next().unwrap(); // Layout
        m.set_layout(pengyos_install_model::Layout::Wipe);
        m.next().unwrap(); // User
        m.set_hostname("pengyos-box");
        m.set_username("alice");
        m.set_password("hunter2");
        m.next().unwrap(); // Network
        m.next().unwrap(); // Pengy
        m.set_pengy_base_url("http://127.0.0.1:8080/v1");
        m.set_pengy_api_key("sk-topsecret");
        m.set_pengy_model("qwen3:8b");
        let text = render(&m);
        assert!(text.contains("Base URL: http://127.0.0.1:8080/v1"));
        assert!(text.contains("Model: qwen3:8b"));
        assert!(!text.contains("sk-topsecret"));
        assert!(text.contains("API key: ***********"));
    }

    #[test]
    fn pengy_screen_is_skippable() {
        // Nothing typed on the Pengy screen: Continue is enabled and the plan
        // carries no [pengy] section.
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.next().unwrap();
        m.set_selected_disk("/dev/sda");
        m.next().unwrap();
        m.set_layout(pengyos_install_model::Layout::Wipe);
        m.next().unwrap();
        m.set_hostname("pengyos-box");
        m.set_username("alice");
        m.set_password("hunter2");
        m.next().unwrap();
        m.next().unwrap(); // -> Pengy
        assert_eq!(m.step(), pengyos_install_model::Step::Pengy);
        let text = render(&m);
        assert!(text.contains("[ Continue ]"));
        assert!(!text.contains("(disabled)"));
    }

    #[test]
    fn screen_from_step_matches() {
        assert_eq!(Screen::from(Step::Disk), Screen::Disk);
        assert_eq!(Screen::from(Step::Progress), Screen::Progress);
    }

    #[test]
    fn answers_from_json_parses_flat_object() {
        let json = r#"{ "accepted_terms": "true", "selected_disk": "/dev/sda", "layout": "wipe" }"#;
        let pairs = answers_from_json(json).expect("valid json");
        assert_eq!(pairs.len(), 3);
        assert_eq!(pairs[0], ("accepted_terms".to_string(), "true".to_string()));
        assert_eq!(pairs[1], ("selected_disk".to_string(), "/dev/sda".to_string()));
        assert_eq!(pairs[2], ("layout".to_string(), "wipe".to_string()));
    }

    #[test]
    fn answers_from_json_handles_whitespace_and_escapes() {
        let json = "{\n  \"network_ssid\" : \"a\\\"b\"\n}";
        let pairs = answers_from_json(json).expect("valid json");
        assert_eq!(pairs.len(), 1);
        assert_eq!(pairs[0], ("network_ssid".to_string(), "a\"b".to_string()));
    }

    #[test]
    fn answers_from_json_rejects_bad_input() {
        assert!(answers_from_json("{}").is_ok()); // empty object is fine
        assert!(answers_from_json("{").is_err());
        assert!(answers_from_json("not json").is_err());
        assert!(answers_from_json(r#"{"a":1}"#).is_err()); // numbers not supported
    }
}
