//! GitHub-ready contract test (spec.md: "assert with a `test_github_ready`").
//!
//! The repo must be publishable at all times: README, an MIT LICENSE with the
//! correct copyright line, a changelog, and a .gitignore at the workspace
//! root. This test fails loudly if any of them goes missing or drifts.

use std::path::PathBuf;

/// The workspace root, resolved from this crate's manifest so the test works
/// no matter what directory `cargo` was invoked from.
fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("pengy-panel/ must live inside the workspace root")
        .to_path_buf()
}

fn read(root: &PathBuf, file: &str) -> String {
    let path = root.join(file);
    let contents = std::fs::read_to_string(&path).unwrap_or_else(|err| {
        panic!("GitHub-ready contract violated: cannot read {path:?}: {err}")
    });
    contents
}

#[test]
fn github_ready() {
    let root = workspace_root();
    for file in ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore"] {
        assert!(
            root.join(file).is_file(),
            "GitHub-ready contract violated: missing {file} at workspace root"
        );
    }

    let license = read(&root, "LICENSE");
    assert!(
        license.contains("Copyright (c) 2026 Pat Wendorf"),
        "LICENSE must carry 'Copyright (c) 2026 Pat Wendorf'; got:\n{license}"
    );
    assert!(
        license.to_ascii_lowercase().contains("mit license"),
        "LICENSE must be the MIT license"
    );

    let changelog = read(&root, "CHANGELOG.md");
    assert!(
        changelog.contains("# Changelog"),
        "CHANGELOG.md must start with a changelog heading"
    );

    let readme = read(&root, "README.md");
    assert!(
        readme.contains("pengy-panel"),
        "README.md must be about pengy-panel"
    );
    assert!(
        readme.contains("cargo test"),
        "README.md must document how to run the test suite"
    );
}

#[test]
fn cargo_lock_is_committed_for_reproducibility() {
    // PengyOS is a from-source reproducible distribution: the dependency set
    // of a binary distribution must be pinned, so Cargo.lock is committed
    // (hence NOT in .gitignore).
    let root = workspace_root();
    assert!(
        root.join("Cargo.lock").is_file(),
        "Cargo.lock must be committed for reproducible builds"
    );
    let gitignore = read(&root, ".gitignore");
    assert!(
        !gitignore.lines().any(|line| line.trim() == "Cargo.lock"),
        ".gitignore must not exclude Cargo.lock"
    );
}
