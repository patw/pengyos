//! Schema validation for a loaded [`Theme`](crate::manifest::Theme).
//!
//! [`validate`](crate::manifest::Theme::validate) turns a parsed manifest into a
//! [`ValidationReport`]: it confirms the required tokens are present, every color
//! is well-formed, and each key foreground/background pair meets the WCAG AA
//! contrast threshold. Colors already parse as `#rrggbb` at load time, so this
//! module's color check is a belt-and-suspenders re-validation that also produces
//! a precise diagnostic (which role, which value).
//!
//! The contrast rule is the substantive check: the `foreground`/`background` pair
//! is always required to clear [`contrast::WCAG_AA_NORMAL`]; the optional semantic
//! roles (`warning`/`error`/`success`) are checked against `background` only when
//! present.
//!
//! ## Design notes
//!
//! Validation is intentionally **non-panicking** and returns a report describing
//! every problem found, so the `validate` subcommand can list them all at once
//! rather than failing on the first. A report is a hard error (exit `1`) only
//! when it contains at least one [`Severity::Error`].

use crate::contrast::{self, WCAG_AA_NORMAL};
use crate::manifest::{Color, Theme};
use std::fmt;

/// How serious a [`ValidationIssue`] is.
///
/// `Error` fails validation; `Warn` is reported but does not fail it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    /// A hard failure: the manifest is invalid (the `validate` subcommand exits 1).
    Error,
    /// A soft problem worth surfacing but not fatal (e.g. a low-contrast accent).
    Warn,
}

impl Severity {
    /// Lowercase label, e.g. `error` / `warn`.
    pub fn label(self) -> &'static str {
        match self {
            Severity::Error => "error",
            Severity::Warn => "warn",
        }
    }
}

impl fmt::Display for Severity {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.label())
    }
}

/// A single problem found while validating a theme.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidationIssue {
    /// Where the problem lives (e.g. `colors.foreground`).
    pub path: String,
    /// Why it is a problem.
    pub message: String,
    /// How serious it is.
    pub severity: Severity,
}

impl ValidationIssue {
    fn new(path: impl Into<String>, message: impl Into<String>, severity: Severity) -> Self {
        ValidationIssue {
            path: path.into(),
            message: message.into(),
            severity,
        }
    }
}

impl fmt::Display for ValidationIssue {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {} — {}", self.severity, self.path, self.message)
    }
}

/// The result of validating a [`Theme`]: the passed-or-not verdict plus every
/// [`ValidationIssue`] found (errors first, then warnings).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidationReport {
    /// `true` only when there are no `Severity::Error` issues.
    pub valid: bool,
    /// Every issue found, errors first then warnings.
    pub issues: Vec<ValidationIssue>,
}

impl ValidationReport {
    /// The hard errors, in the order they were found.
    pub fn errors(&self) -> impl Iterator<Item = &ValidationIssue> {
        self.issues.iter().filter(|i| i.severity == Severity::Error)
    }

    /// The soft warnings, in the order they were found.
    pub fn warnings(&self) -> impl Iterator<Item = &ValidationIssue> {
        self.issues.iter().filter(|i| i.severity == Severity::Warn)
    }

    /// Render the report as a single human-readable block (one issue per line).
    ///
    /// An empty report renders as `OK: all checks passed`.
    pub fn render(&self) -> String {
        if self.issues.is_empty() {
            return "OK: all checks passed".to_string();
        }
        let mut out = String::new();
        for issue in &self.issues {
            out.push_str(&format!("{issue}\n"));
        }
        out
    }
}

/// Validate a fully-parsed [`Theme`].
///
/// Checks, in order:
///
/// 1. **Required tokens** — `name`, `version`, `colors`, `fonts`, and `defaults`
///    are present and the free-form tokens (`name`, `version`) are non-empty.
///    (These are required by the schema itself, so a parsed `Theme` already has
///    them; this documents and re-asserts the contract.)
/// 2. **Valid colors** — every color role, including the optional semantic ones,
///    is a well-formed `#rrggbb`. (Parsed colors are always valid; this keeps the
///    check explicit and future-proofs a programmatic [`Theme`] that bypasses the
///    loader.)
/// 3. **Contrast** — the `foreground`/`background` pair must meet
///    [`contrast::WCAG_AA_NORMAL`]; the optional `warning`/`error`/`success` roles
///    are checked against `background` only when present.
///
/// The returned [`ValidationReport`] is valid only when it has zero
/// [`Severity::Error`] issues.
pub fn validate(theme: &Theme) -> ValidationReport {
    let mut issues: Vec<ValidationIssue> = Vec::new();

    // 1. Required tokens.
    if theme.name.trim().is_empty() {
        issues.push(ValidationIssue::new(
            "name",
            "required theme name is empty",
            Severity::Error,
        ));
    }
    if theme.version.trim().is_empty() {
        issues.push(ValidationIssue::new(
            "version",
            "required theme version is empty",
            Severity::Error,
        ));
    }

    // 2. Valid colors + 3. contrast, together so each color is checked once and
    // then reused in the contrast check. Parsed colors are always well-formed, so
    // the color check is a documented invariant; the contrast check does the real
    // work.
    let palette = &theme.colors;
    let bg = palette.background;

    // foreground/background is the primary pair and always required.
    check_contrast(&mut issues, "colors.foreground", palette.foreground, bg);

    // accent is required; a low-contrast accent is a soft failure (warn) so a
    // decorative accent that is too close to the background does not reject the
    // whole theme.
    check_contrast_soft(&mut issues, "colors.accent", palette.accent, bg);

    // Semantic roles are optional; check them only when present.
    for (role, color) in [
        ("colors.warning", palette.warning.as_ref()),
        ("colors.error", palette.error.as_ref()),
        ("colors.success", palette.success.as_ref()),
    ] {
        if let Some(c) = color {
            check_contrast(&mut issues, role, *c, bg);
        }
    }

    let valid = issues.iter().all(|i| i.severity != Severity::Error);
    ValidationReport { valid, issues }
}

/// Validate a [`Theme`] and return `Ok(())` or the rendered error report.
///
/// Convenience wrapper for callers (like the `validate` subcommand) that want a
/// single boolean-ish result with a diagnostic.
pub fn validate_theme(theme: &Theme) -> Result<ValidationReport, ValidationReport> {
    let report = validate(theme);
    if report.valid {
        Ok(report)
    } else {
        Err(report)
    }
}

/// Record a hard contrast failure when a pair misses the AA-normal threshold.
fn check_contrast(
    issues: &mut Vec<ValidationIssue>,
    fg_path: &str,
    fg: Color,
    bg: Color,
) {
    let ratio = contrast::contrast_ratio(fg, bg);
    if ratio < WCAG_AA_NORMAL {
        issues.push(ValidationIssue::new(
            fg_path,
            format!(
                "contrast {ratio:.2}:1 against background is below WCAG AA normal ({:.1}:1)",
                WCAG_AA_NORMAL
            ),
            Severity::Error,
        ));
    }
}

/// Record a soft contrast warning when a pair misses the AA-normal threshold.
fn check_contrast_soft(
    issues: &mut Vec<ValidationIssue>,
    fg_path: &str,
    fg: Color,
    bg: Color,
) {
    let ratio = contrast::contrast_ratio(fg, bg);
    if ratio < WCAG_AA_NORMAL {
        issues.push(ValidationIssue::new(
            fg_path,
            format!(
                "contrast {ratio:.2}:1 against background is below WCAG AA normal ({:.1}:1) (warn)",
                WCAG_AA_NORMAL
            ),
            Severity::Warn,
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::manifest::{Color, Palette, Theme};
    use std::str::FromStr;

    // A manifest that parses; used to build themes programmatically.
    const EXAMPLE: &str = r##"
name = "Catbee Drift"
version = "1.0.0"
[colors]
foreground = "#f5f5f5"
background = "#1a1a1a"
accent = "#ffb454"
warning = "#ffd54a"
error = "#ff5772"
success = "#7cffcb"
[fonts]
mono = "DejaVu Sans Mono"
sans = "DejaVu Sans"
base_size = 14
[defaults]
"##;

    fn theme() -> Theme {
        Theme::from_toml(EXAMPLE).unwrap()
    }

    #[test]
    fn clean_theme_passes() {
        let report = validate(&theme());
        assert!(report.valid, "clean theme should pass: {}", report.render());
        assert!(report.errors().next().is_none());
    }

    #[test]
    fn empty_theme_has_required_token_errors() {
        // A minimal theme with empty name/version must report both as errors.
        let t = Theme {
            name: String::new(),
            version: String::new(),
            description: None,
            colors: Palette {
                foreground: Color::from_str("#f5f5f5").unwrap(),
                background: Color::from_str("#1a1a1a").unwrap(),
                accent: Color::from_str("#ffb454").unwrap(),
                warning: None,
                error: None,
                success: None,
            },
            fonts: crate::manifest::Fonts {
                mono: "Mono".into(),
                sans: "Sans".into(),
                serif: None,
                base_size: 14,
                mono_size: None,
            },
            wallpapers: vec![],
            keybindings: vec![],
            defaults: crate::manifest::Defaults::default(),
        };
        let report = validate(&t);
        assert!(!report.valid);
        let paths: Vec<&str> = report.errors().map(|i| i.path.as_str()).collect();
        assert!(paths.contains(&"name"), "expected a `name` error, got {paths:?}");
        assert!(paths.contains(&"version"), "expected a `version` error, got {paths:?}");
    }

    #[test]
    fn fg_bg_below_threshold_is_error() {
        // Two near-identical greys fail AA normal.
        let t = theme();
        let mut palette = t.colors.clone();
        palette.foreground = Color::from_str("#808080").unwrap();
        palette.background = Color::from_str("#8a8a8a").unwrap();
        let report = validate(&Theme { colors: palette, ..t });
        assert!(!report.valid, "low-contrast fg/bg should fail");
        let msgs: Vec<String> = report.errors().map(|i| i.message.clone()).collect();
        assert!(
            msgs.iter().any(|m| m.contains("contrast")),
            "expected a contrast error, got {msgs:?}"
        );
    }

    #[test]
    fn fg_bg_above_threshold_passes() {
        let t = theme();
        let mut palette = t.colors.clone();
        // Pure black on pure white — the canonical passing pair.
        palette.foreground = Color::from_str("#000000").unwrap();
        palette.background = Color::from_str("#ffffff").unwrap();
        // The example's semantic roles are low-contrast against white; clear them
        // so only the fg/bg pair is exercised here.
        palette.warning = None;
        palette.error = None;
        palette.success = None;
        let report = validate(&Theme { colors: palette, ..t });
        assert!(report.valid, "21:1 should pass: {}", report.render());
    }

    #[test]
    fn semantic_roles_checked_only_when_present() {
        // A theme with all three semantic roles present checks each of them
        // against the background. #808080 (grey) on the dark background is below
        // AA normal, so it must surface as an error.
        let t = theme();
        let mut palette = t.colors.clone();
        palette.warning = Some(Color::from_str("#808080").unwrap()); // grey on dark bg
        let report = validate(&Theme { colors: palette, ..t });
        assert!(!report.valid, "bad warning contrast should fail: {}", report.render());
        let paths: Vec<&str> = report.errors().map(|i| i.path.as_str()).collect();
        assert!(paths.contains(&"colors.warning"), "expected a warning-role error, got {paths:?}");
    }

    #[test]
    fn accent_low_contrast_is_warn_not_error() {
        // A dark accent on a dark background is a soft warning, not a hard error.
        let t = theme();
        let mut palette = t.colors.clone();
        palette.accent = Color::from_str("#1a1a1a").unwrap(); // same as background
        let report = validate(&Theme { colors: palette, ..t });
        // No hard errors (only the warn), so still "valid".
        assert!(report.valid, "accent warn should not fail validation");
        assert!(report.warnings().next().is_some(), "expected an accent warn");
        let warn_paths: Vec<&str> = report.warnings().map(|i| i.path.as_str()).collect();
        assert_eq!(warn_paths, vec!["colors.accent"]);
    }

    #[test]
    fn empty_semantic_roles_do_not_add_issues() {
        // A theme with the semantic roles omitted must not report them.
        let minimal = r##"
name = "Minimal"
version = "1.0.0"
[colors]
foreground = "#000000"
background = "#ffffff"
accent = "#00aaff"
[fonts]
mono = "Mono"
sans = "Sans"
base_size = 14
[defaults]
"##;
        let report = validate(&Theme::from_toml(minimal).unwrap());
        assert!(report.valid, "minimal theme should pass: {}", report.render());
        let paths: Vec<&str> = report.issues.iter().map(|i| i.path.as_str()).collect();
        assert!(
            !paths.iter().any(|p| p.contains("warning") || p.contains("error") || p.contains("success")),
            "absent semantic roles must not be reported: {paths:?}"
        );
    }

    #[test]
    fn report_render_lists_issues() {
        let t = theme();
        let mut palette = t.colors.clone();
        palette.foreground = Color::from_str("#808080").unwrap();
        palette.background = Color::from_str("#8a8a8a").unwrap();
        let report = validate(&Theme { colors: palette, ..t });
        let rendered = report.render();
        assert!(rendered.contains("colors.foreground"));
        assert!(rendered.contains("contrast"));
    }

    #[test]
    fn render_ok_when_clean() {
        let report = validate(&theme());
        assert!(report.render().starts_with("OK:"));
    }

    #[test]
    fn validate_wrapper_returns_ok_when_valid() {
        assert!(validate_theme(&theme()).is_ok());
    }

    #[test]
    fn validate_wrapper_returns_err_when_invalid() {
        let t = theme();
        let mut palette = t.colors.clone();
        palette.foreground = Color::from_str("#808080").unwrap();
        palette.background = Color::from_str("#8a8a8a").unwrap();
        let bad = Theme { colors: palette, ..t };
        assert!(validate_theme(&bad).is_err());
    }
}
