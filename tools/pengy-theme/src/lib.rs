//! `pengy_theme` — theme & desktop-defaults packager/validator for PengyOS.
//!
//! This crate provides a small, dependency-light library plus a `pengy-theme`
//! CLI that validates a theme manifest, checks contrast/consistency, and emits
//! installable artifacts + a hash.
//!
//! ## Modules
//!
//! - [`meta`] — crate identity (name, version, banner, usage text).
//! - [`cli`] — argument parsing and the command dispatcher (`validate`, `pack`,
//!   `contrast`), with a documented exit-code contract.
//!
//! The theme surface is layered in by follow-up subtasks — see
//! [`implementation`] for the ordered task list:
//!
//! - [`manifest`] — the theme schema (colors, fonts, wallpapers, keybindings,
//!   defaults) and manifest loading/validation.
//! - [`contrast`] — WCAG relative-contrast-ratio math for foreground/background
//!   pairs (used by [`validate`]).
//! - [`validate`] — schema validation: required tokens, valid colors, and WCAG
//!   contrast ratios for key pairs.
//! - [`pack`] — deterministic archive + manifest hash emission.
//!
//! [`manifest`]: manifest
//! [`contrast`]: contrast
//! [`validate`]: validate
//! [`pack`]: pack

/// The `manifest` module: the theme schema (colors, fonts, wallpapers,
/// keybindings, defaults) and the TOML manifest loader.
pub mod manifest;

/// The `contrast` module: WCAG relative-contrast-ratio math.
pub mod contrast;

/// The `validate` module: schema validation (required tokens, valid colors,
/// WCAG contrast for key pairs) for a loaded [`Theme`](manifest::Theme).
pub mod validate;

/// The `pack` module: deterministic archive + manifest hash emission.
pub mod pack;

/// The `meta` module: crate identity (name, version, banner, usage).
pub mod meta;

/// The `cli` module: argument parsing and the command dispatcher.
pub mod cli;

/// Canonical package name (the binary name).
pub const NAME: &str = "pengy-theme";

/// Crate version, pinned from `Cargo.toml` at build time.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// One-line human-readable banner, e.g. `pengy-theme 0.1.0`.
pub fn banner() -> String {
    format!("{NAME} {VERSION}")
}

/// Usage text shown by `pengy-theme --help`.
///
/// Kept as a single const so the CLI and the README can share the exact
/// wording (docs stay in sync with code).
pub const USAGE: &str = "\
pengy-theme — theme & desktop-defaults packager/validator for PengyOS

USAGE:
    pengy-theme [COMMAND] [OPTIONS]

COMMANDS:
    validate  Validate a theme manifest (required tokens, valid colors,
              WCAG contrast ratios for key pairs)
    pack      Produce a deterministic archive + manifest hash from a manifest
    contrast  Compute the WCAG relative contrast ratio of two colors

GLOBAL OPTIONS:
    -h, --help      Print help
    -V, --version   Print version

Subcommands and their flags are documented in the README.
";

/// Exit-code contract, shared by the CLI and its tests.
pub mod exit {
    /// Success (including `--help` / `--version`).
    pub const OK: i32 = 0;
    /// Generic runtime error (I/O, bad manifest, contrast mismatch, ...).
    pub const ERROR: i32 = 1;
    /// Usage error (bad or missing arguments).
    pub const USAGE: i32 = 2;
    /// Subcommand not yet implemented.
    pub const UNIMPLEMENTED: i32 = 3;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn banner_contains_name_and_version() {
        let b = banner();
        assert_eq!(b, format!("{NAME} {VERSION}"), "unexpected banner: {b}");
        assert!(b.starts_with("pengy-theme"), "banner should name the crate");
    }

    #[test]
    fn version_matches_manifest() {
        // VERSION is baked from CARGO_PKG_VERSION; sanity-check it parses as a
        // semver-ish string and is non-empty.
        assert_eq!(VERSION, env!("CARGO_PKG_VERSION"));
        assert!(!VERSION.is_empty());
        assert!(VERSION.contains('.'), "VERSION should be semver-like: {VERSION}");
    }

    #[test]
    fn usage_documents_all_subcommands() {
        // The three documented subcommands must appear in the usage text.
        for cmd in ["validate", "pack", "contrast"] {
            assert!(
                USAGE.contains(cmd),
                "USAGE text must mention the `{cmd}` subcommand"
            );
        }
    }

    #[test]
    fn exit_codes_are_distinct() {
        // The four exit codes must be unique so tests can pin them.
        let codes = [exit::OK, exit::ERROR, exit::USAGE, exit::UNIMPLEMENTED];
        for (i, a) in codes.iter().enumerate() {
            for b in &codes[i + 1..] {
                assert_ne!(a, b, "exit codes must be distinct");
            }
        }
    }
}
