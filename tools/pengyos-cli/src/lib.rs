//! `pengyos-cli` — the library behind the `pengyos` meta-CLI.
//!
//! The `pengyos` binary is one entry point over the PengyOS toolchain:
//!
//! ```text
//! pengyos build <target> | verify <image> | triage | profile | sbom <artifact> | doctor
//! ```
//!
//! Each subcommand shells out to the matching Python tool under
//! `tools/` (see the sibling `pengyos-build`, `pengyos-verifier`, …). This crate
//! is intentionally **standard-library only** so it builds and tests fully
//! offline; any dependency is declared in `Cargo.toml`, added per-subtask.
//!
//! The scaffold (this subtask) establishes the crate, the `pengyos` binary, the
//! MIT license, the GitHub-ready docs, and the contract tests. The subcommand
//! framework and config loading land in the next subtask.

/// The `pengyos` version, driven by Cargo's package version.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// The canonical command name shown in help / usage / error messages.
pub const PROG: &str = "pengyos";

/// The documented subcommand surface (spec.md interface).
///
/// This is the single source of truth for the subcommands the CLI advertises;
/// the framework (next subtask) reads from it so `--help` and dispatch stay in
/// sync. Order here defines the help ordering.
pub const SUBCOMMANDS: &[&str] = &[
    "build",
    "verify",
    "triage",
    "profile",
    "sbom",
    "doctor",
];

/// One-line descriptions, keyed by subcommand, for usage/help output.
///
/// Kept in lockstep with [`SUBCOMMANDS`]; the help printer only shows commands
/// present here.
pub const SUBCOMMAND_HELP: &[(&str, &str)] = &[
    ("build", "build a target image"),
    ("verify", "verify a built image"),
    ("triage", "triage update candidates"),
    ("profile", "profile the host"),
    ("sbom", "emit a software bill of materials"),
    ("doctor", "report host readiness"),
];

/// Look up the help description for a subcommand (empty string if unknown).
pub fn subcommand_help(name: &str) -> &'static str {
    for &(cmd, desc) in SUBCOMMAND_HELP {
        if cmd == name {
            return desc;
        }
    }
    ""
}

// --- Subcommand framework + config (next subtasks) -------------------------

pub mod args;
pub mod config;
pub mod dispatch;
pub mod doctor;
pub mod json;

pub use args::{ArgSpec, ParsedArgs};
pub use config::Config;
pub use dispatch::{
    CommandSpec, DispatchOutcome, Handler, MockRunner, ProcessRunner, Registry, Runner, RunOutcome,
    outcome_to_json,
};
pub use json::Value as JsonValue;
pub use doctor::{
    collect_host, diagnose, doctor_handler, format_diagnosis, is_buildroot_version, Host,
    HostProbe, Status, Severity, CheckResult, Diagnosis, which, REQUIRED_TOOLS, DEFAULT_BUILDROOT,
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_is_semver() {
        // Semver-ish: at least major.minor.patch of digits.
        let parts: Vec<&str> = VERSION.split('.').collect();
        assert!(parts.len() >= 3, "VERSION should be semver-ish, got {VERSION}");
        assert!(
            parts.iter().all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit())),
            "VERSION parts should be numeric, got {VERSION}"
        );
    }

    #[test]
    fn subcommands_match_spec() {
        // The documented interface (spec.md):
        //   pengyos build <target> | verify <image> | triage | profile
        //       | sbom <artifact> | doctor
        let expected = ["build", "verify", "triage", "profile", "sbom", "doctor"];
        assert_eq!(SUBCOMMANDS, expected.as_slice());
    }

    #[test]
    fn every_subcommand_has_a_description() {
        for cmd in SUBCOMMANDS {
            assert!(
                !subcommand_help(cmd).is_empty(),
                "subcommand `{cmd}` is missing a help description"
            );
        }
    }

    #[test]
    fn no_duplicate_subcommands() {
        let mut seen = std::collections::BTreeSet::new();
        for cmd in SUBCOMMANDS {
            assert!(
                seen.insert(*cmd),
                "duplicate subcommand `{cmd}` in SUBCOMMANDS"
            );
        }
    }
}
