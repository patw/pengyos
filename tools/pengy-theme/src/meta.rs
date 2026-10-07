//! Crate identity for `pengy-theme`.
//!
//! A single, self-describing module so the name, version, banner, and usage
//! text live in exactly one place and are shared by the CLI and the docs.

use crate::{banner, USAGE};

/// Human-readable package name.
pub const NAME: &str = crate::NAME;

/// Package version, pinned from `Cargo.toml` at build time.
pub const VERSION: &str = crate::VERSION;

/// One-line banner, e.g. `pengy-theme 0.1.0`.
pub fn banner_str() -> String {
    banner()
}

/// Full usage text (see [`crate::USAGE`]).
pub fn usage() -> &'static str {
    USAGE
}

/// The three documented subcommands, in display order.
///
/// Used by the dispatcher to render a command list and by the README/tests to
/// keep the documented surface in sync with the code.
pub const SUBCOMMANDS: &[&str] = &["validate", "pack", "contrast"];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn name_matches_crate() {
        assert_eq!(NAME, "pengy-theme");
    }

    #[test]
    fn banner_matches_crate() {
        assert_eq!(banner_str(), crate::banner());
    }

    #[test]
    fn subcommands_documented() {
        assert_eq!(SUBCOMMANDS, &["validate", "pack", "contrast"]);
    }
}
