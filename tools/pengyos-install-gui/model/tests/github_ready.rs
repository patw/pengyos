//! Workspace-level GitHub-ready contract.
//!
//! The spec's GitHub-ready contract asserts the presence of `README.md`,
//! `LICENSE` (MIT, "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`,
//! `.gitignore`, and a green test suite. This integration test verifies the
//! file-level contract from the workspace root — located relative to this
//! crate's manifest dir (one level up from `model/`).
//!
//! It is std-only and needs no network, root, or display, so it runs in the
//! default `cargo test` for the model crate and is never gated behind the
//! `cosmic` feature.

use std::path::Path;

/// The workspace root: the parent of this crate's manifest dir.
///
/// Cargo sets `CARGO_MANIFEST_DIR` to the crate being built (`.../model/`), so
/// its parent is the workspace root that holds the top-level `Cargo.toml` and
/// the GitHub-ready metadata.
fn workspace_root() -> std::path::PathBuf {
    let manifest = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .expect("crate manifest dir has a parent")
        .to_path_buf()
}

/// Read a file at the workspace root, panicking with a clear message if it is
/// missing or unreadable.
fn read(root: &Path, file: &str) -> String {
    let path = root.join(file);
    std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("{file} missing or unreadable ({e} at {})", path.display()))
}

/// Assert the GitHub-ready contract: the metadata files that make the repo
/// publishable on GitHub.
#[test]
fn github_ready_contract() {
    let root = workspace_root();

    // README.md exists and is non-empty.
    let readme = read(&root, "README.md");
    assert!(
        readme.trim().len() > 0,
        "README.md must exist and be non-empty"
    );

    // LICENSE exists, is MIT, and carries the copyright line.
    let license = read(&root, "LICENSE");
    assert!(
        license.contains("MIT"),
        "LICENSE must be an MIT license (found: {:?})",
        license.lines().next()
    );
    assert!(
        license.contains("Copyright (c) 2026 Pat Wendorf"),
        "LICENSE must carry 'Copyright (c) 2026 Pat Wendorf'"
    );

    // CHANGELOG.md exists and is non-empty.
    let changelog = read(&root, "CHANGELOG.md");
    assert!(
        changelog.trim().len() > 0,
        "CHANGELOG.md must exist and be non-empty"
    );

    // .gitignore exists and is non-empty.
    let gitignore = read(&root, ".gitignore");
    assert!(
        gitignore.trim().len() > 0,
        ".gitignore must exist and be non-empty"
    );

    // The workspace manifest exists and declares the two members.
    let workspace_cargo = read(&root, "Cargo.toml");
    assert!(
        workspace_cargo.contains("[workspace]"),
        "the workspace Cargo.toml must declare [workspace]"
    );
    assert!(
        workspace_cargo.contains("\"model\""),
        "the workspace Cargo.toml must list the model crate as a member"
    );
    assert!(
        workspace_cargo.contains("\"gui\""),
        "the workspace Cargo.toml must list the gui crate as a member"
    );

    // The model crate manifest exists and is dependency-free (stdlib only), so
    // the workspace's default `cargo test` needs no third-party crates.
    let model_cargo = read(&root.join("model"), "Cargo.toml");
    assert!(
        model_cargo.contains("pengyos-install-model"),
        "the model crate must be named pengyos-install-model"
    );
    // The only dependency section is the (empty) `[dependencies]` — no
    // third-party crates, so the offline suite needs no network.
    let deps = model_cargo
        .split("[dependencies]")
        .nth(1)
        .unwrap_or("");
    let deps_body: String = deps
        .lines()
        .filter(|l| !l.trim().is_empty() && !l.trim().starts_with('#'))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(
        deps_body.trim().is_empty(),
        "the model crate must be dependency-free (stdlib only); found: {deps_body}"
    );
}

/// The README must point at the same two crates it ships, so the docs and the
/// workspace agree.
#[test]
fn readme_names_both_crates() {
    let root = workspace_root();
    let readme = read(&root, "README.md");
    assert!(
        readme.contains("pengyos-install-model"),
        "README should reference the model crate"
    );
    assert!(
        readme.contains("pengyos-install-gui"),
        "README should reference the gui crate"
    );
}
