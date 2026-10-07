//! GitHub-ready contract for the `pengyos-cli` crate.
//!
//! Mirrors the Python `test_github_ready` pattern used by the other PengyOS
//! tools: the repository must, at all times, ship an authored README, an MIT
//! LICENSE with the required copyright line, a Keep-a-Changelog CHANGELOG, a
//! non-empty `.gitignore`, and a Cargo.toml that declares the MIT license.
//!
//! These checks are fast (file presence + content). They do not re-run the test
//! suite from inside itself.

use std::path::PathBuf;

/// Directory containing Cargo.toml / README / LICENSE — the crate root.
fn repo_root() -> PathBuf {
    std::env::var("CARGO_MANIFEST_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

fn read(path: &std::path::Path) -> String {
    assert!(path.is_file(), "missing required GitHub-ready file: {}", path.display());
    std::fs::read_to_string(path).expect("failed to read a required GitHub-ready file")
}

/// Extract `## [X.Y.Z] - YYYY-MM-DD` version headings without a regex dep.
fn semver_headings(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("## [") {
            if let Some((version, after)) = rest.split_once("] - ") {
                let digits = after.chars().take(10).collect::<String>();
                if digits.len() == 10
                    && digits.chars().all(|c| c.is_ascii_digit() || c == '-')
                    && after.len() >= 10
                {
                    out.push(version.to_string());
                }
            }
        }
    }
    out
}

#[test]
fn readme_exists_and_is_authored() {
    let text = read(&repo_root().join("README.md"));
    // Authored, not a stub: substantial prose with a real section structure.
    assert!(text.len() >= 2000, "README.md looks like a stub ({}) chars", text.len());
    assert!(
        text.trim_start().starts_with("# pengyos"),
        "README.md should be titled for this tool"
    );
    // At least a handful of top-level sections.
    assert!(
        text.matches("\n## ").count() >= 5,
        "README.md should have install/usage/exit-codes/etc. sections"
    );
    // The documented surface appears in the usage docs.
    for cmd in ["build", "verify", "triage", "profile", "sbom", "doctor"] {
        assert!(text.contains(cmd), "README usage docs do not mention `{cmd}`");
    }
}

#[test]
fn readme_documents_the_license() {
    let text = read(&repo_root().join("README.md"));
    assert!(text.contains("MIT"), "README should document the license");
    assert!(
        text.contains("Copyright (c) 2026 Pat Wendorf"),
        "README should carry the required copyright line"
    );
}

#[test]
fn license_is_mit_with_required_copyright() {
    let text = read(&repo_root().join("LICENSE"));
    assert!(
        text.trim_start().starts_with("MIT License"),
        "LICENSE should start with 'MIT License'"
    );
    assert!(text.contains("Copyright (c) 2026 Pat Wendorf"), "missing required copyright");
    // Core MIT clauses (wording checked so a silent re-license is caught).
    assert!(text.contains("Permission is hereby granted, free of charge"));
    assert!(text.contains("THE SOFTWARE IS PROVIDED \"AS IS\""));
}

#[test]
fn changelog_is_keep_a_changelog() {
    let text = read(&repo_root().join("CHANGELOG.md"));
    assert!(text.contains("Keep a Changelog"), "not Keep a Changelog format");
    assert!(text.contains("Semantic Versioning"), "not adhering to Semantic Versioning");
    let versions = semver_headings(&text);
    assert!(!versions.is_empty(), "no '## [version] - date' headings found");
}

#[test]
fn changelog_current_version_matches_package() {
    let text = read(&repo_root().join("CHANGELOG.md"));
    let versions = semver_headings(&text);
    let top = versions.first().expect("at least one version heading");
    assert_eq!(
        top,
        pengyos_cli::VERSION,
        "CHANGELOG.md top entry [{top}] must match package version {}",
        pengyos_cli::VERSION
    );
}

#[test]
fn gitignore_is_non_empty_and_covers_rust_noise() {
    let text = read(&repo_root().join(".gitignore"));
    assert!(!text.trim().is_empty(), ".gitignore must not be empty");
    assert!(text.contains("target"), ".gitignore should ignore Rust build output");
}

#[test]
fn cargo_toml_declares_mit_license() {
    let text = read(&repo_root().join("Cargo.toml"));
    assert!(text.to_lowercase().contains("license"), "Cargo.toml should declare a license");
    assert!(text.contains("MIT"), "Cargo.toml should declare the MIT license");
}
