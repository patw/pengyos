//! The tool-directory contract, asserted against the committed tree: the
//! GitHub-ready docs, the verifier, the zero-dependency boundary, the launcher,
//! and the Buildroot recipe skeleton. Written the way `pengy-panel`'s and the
//! other pack-in games' `github_ready` test is written.

use std::fs;
use std::path::PathBuf;

fn tool() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn repo() -> PathBuf {
    tool().parent().and_then(|p| p.parent()).unwrap().to_path_buf()
}

fn read(path: PathBuf) -> String {
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()))
}

#[test]
fn the_tool_directory_has_its_docs_and_verifier() {
    for name in [
        "README.md",
        "spec.md",
        "LICENSE",
        "CHANGELOG.md",
        "verify.toml",
        "Cargo.toml",
    ] {
        assert!(tool().join(name).is_file(), "missing {name}");
        assert!(!read(tool().join(name)).trim().is_empty(), "{name} is empty");
    }
    for name in [
        "src/lib.rs",
        "src/main.rs",
        "tests/rules.rs",
        "tests/cli.rs",
        "tests/github_ready.rs",
        "fixtures/win.replay",
        "launcher/pengy-invaders.desktop",
    ] {
        assert!(tool().join(name).is_file(), "missing {name}");
    }
}

#[test]
fn license_is_mit_and_names_the_copyright_holder() {
    let lic = read(tool().join("LICENSE"));
    assert!(lic.starts_with("MIT License"), "LICENSE is the MIT text");
    assert!(
        lic.contains("Copyright (c) 2026 Pat Wendorf"),
        "LICENSE names the copyright holder"
    );
}

#[test]
fn changelog_is_keep_a_changelog_with_the_010_entry() {
    let c = read(tool().join("CHANGELOG.md"));
    assert!(c.contains("# Changelog"), "a changelog heading");
    assert!(
        c.to_lowercase().contains("keep a changelog"),
        "declares Keep a Changelog"
    );
    assert!(c.contains("## [0.1.0]"), "the 0.1.0 entry exists");
    assert!(c.contains("Added"), "Keep a Changelog's Added section");
}

#[test]
fn verify_toml_is_the_cargo_test_check_the_gate_runs() {
    let v = read(tool().join("verify.toml"));
    assert!(v.contains("[[check]]"), "a check block");
    assert!(v.contains("cargo"), "runs cargo");
    assert!(v.contains("test"), "runs the tests");
    assert!(
        v.contains("cwd = \"tools/pengy-invaders\""),
        "in this tool's directory"
    );
    assert!(v.contains("expect = \"exit0\""), "expects exit 0");
    assert!(v.contains("timeout"), "with a timeout");
}

#[test]
fn the_crate_is_rust_2021_with_zero_dependencies() {
    let c = read(tool().join("Cargo.toml"));
    assert!(c.contains("edition = \"2021\""), "Rust 2021");
    assert!(c.contains("name = \"pengy-invaders\""), "the package name");
    assert!(c.contains("publish = false"), "not published to crates.io");
    // The hard constraint: nothing after [dependencies] but comments.
    // The table header on its own line, not the comment that talks about it.
    let deps = c
        .lines()
        .skip_while(|l| l.trim() != "[dependencies]")
        .skip(1)
        .collect::<Vec<_>>()
        .join("\n");
    let body = deps.split('[').next().unwrap_or("");
    let lines: Vec<&str> = body
        .lines()
        .map(|l| l.trim())
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .collect();
    assert!(
        lines.is_empty(),
        "[dependencies] must stay empty, found {lines:?}"
    );
    // No dependency on a crate at all, anywhere in the manifest.
    for banned in ["crossterm", "clap", "rand", "libc", "serde", "termion", "ratatui"] {
        assert!(!c.contains(banned), "the manifest mentions {banned}");
    }
    // And the sources agree: no extern crates, no unsafe.
    for src in ["src/lib.rs", "src/main.rs"] {
        let text = read(tool().join(src));
        assert!(!text.contains("extern crate"), "{src} declares no crate");
        assert!(!text.contains("unsafe"), "{src} has no unsafe");
        assert!(!text.contains("std::time::SystemTime"), "{src} reads no clock in the library");
    }
    assert!(
        !read(tool().join("src/lib.rs")).contains("std::process"),
        "the rules file runs no processes"
    );
    assert!(
        !read(tool().join("src/lib.rs")).contains("std::fs"),
        "the rules file touches no files"
    );
}

#[test]
fn the_launcher_is_a_game_entry_that_runs_in_a_terminal() {
    let d = read(tool().join("launcher/pengy-invaders.desktop"));
    assert!(d.contains("[Desktop Entry]"), "a desktop entry");
    assert!(d.contains("Type=Application"), "Type=Application");
    assert!(d.contains("Name="), "a Name");
    assert!(d.contains("Exec=pengy-invaders"), "Exec=pengy-invaders");
    assert!(d.contains("Terminal=true"), "Terminal=true");
    assert!(d.contains("Categories=Game;"), "Categories=Game;");
    assert!(d.contains("NoDisplay=false") || !d.contains("NoDisplay"), "it shows in the menu");
}

#[test]
fn the_buildroot_recipe_builds_the_crate_from_this_repo_into_usr_bin() {
    let pkg = repo().join("buildroot-external/package/pengy-invaders");
    assert!(pkg.is_dir(), "the recipe directory exists");
    let mk = read(pkg.join("pengy-invaders.mk"));
    let cfg = read(pkg.join("Config.in"));
    // Built from this repo, offline, with the cargo package style.
    assert!(
        mk.contains("PENGY_INVADERS_SITE = $(BR2_EXTERNAL_PENGYOS_PATH)/../../tools/pengy-invaders"),
        "the source is tools/pengy-invaders in this repo"
    );
    assert!(mk.contains("SITE_METHOD = local"), "the local site method");
    assert!(mk.contains("cargo build --offline --release"), "an offline release build");
    assert!(
        mk.contains("--bin pengy-invaders"),
        "it builds the game binary"
    );
    assert!(
        mk.contains("$(TARGET_DIR)/usr/bin/pengy-invaders"),
        "the binary installs to /usr/bin"
    );
    assert!(mk.contains("PENGY_INVADERS_LICENSE = MIT"), "the license is declared");
    assert!(mk.contains("cargo-package"), "the cargo-package macro");
    assert!(
        mk.contains("pengy-invaders.desktop"),
        "the launcher installs from the source tree"
    );
    assert!(
        mk.contains("PENGY_INVADERS_VERSION = 0.1.0"),
        "the version matches the crate"
    );
    // Config.in: the symbol, the rustc dependency, the help text.
    assert!(
        cfg.contains("config BR2_PACKAGE_PENGY_INVADERS"),
        "the Config.in symbol"
    );
    assert!(cfg.contains("bool \"pengy-invaders\""), "a bool prompt");
    assert!(
        cfg.contains("BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS"),
        "it depends on a Rust target"
    );
    assert!(cfg.contains("select BR2_PACKAGE_HOST_RUSTC"), "it selects the Rust toolchain");
    assert!(cfg.contains("help"), "a help block");
    assert!(cfg.contains("Terminal=true"), "the help says it runs in a terminal");
    // A skeleton: no hash file, because a local source has nothing to fetch.
    assert!(
        !pkg.join("pengy-invaders.hash").exists(),
        "a local source needs no digest"
    );
}

#[test]
fn the_recipe_does_not_wire_the_image() {
    // The integration job owns image wiring: the Config.in source line, the
    // defconfigs and the rootfs overlay are not ours to touch.
    let git = repo().join(".git");
    if !git.exists() {
        return;
    }
    let out = std::process::Command::new("git")
        .args(["-C", &repo().to_string_lossy(), "log", "--name-only", "-1", "--format=%s"])
        .output();
    let Ok(out) = out else { return };
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    // Only meaningful once this tool's commit is HEAD; a worktree checkout of
    // main has somebody else's commit on top, and that is not ours to police.
    if !text.starts_with("pengy-invaders:") {
        return;
    }
    for line in text.lines().filter(|l| !l.trim().is_empty() && !l.starts_with("pengy-invaders:")) {
        assert!(
            line.starts_with("tools/pengy-invaders/")
                || line.starts_with("buildroot-external/package/pengy-invaders/"),
            "the commit touches only the game: {line}"
        );
    }
}

#[test]
fn the_readme_documents_the_keys_the_run_and_the_tests() {
    let r = read(tool().join("README.md"));
    for want in [
        "space",
        "Pause",
        "Quit",
        "cargo test",
        "--headless",
        "--replay",
        "--seed",
        "--mono",
        "NO_COLOR",
        "XDG_DATA_HOME",
        "80x24",
    ] {
        assert!(r.contains(want), "README documents {want}");
    }
    assert!(r.contains("MIT"), "README states the license");
}

#[test]
fn the_spec_states_the_rules_the_acceptance_criteria_and_the_constraints() {
    let s = read(tool().join("spec.md"));
    for want in [
        "60x24",
        "11x5",
        "Acceptance",
        "Constraints",
        "zero",
        "headless",
        "replay",
        "high score",
        "deterministic",
    ] {
        assert!(s.contains(want), "spec.md covers {want}");
    }
}

#[test]
fn the_replay_fixture_is_a_playable_session() {
    let f = read(tool().join("fixtures/win.replay"));
    let lines: Vec<&str> = f.lines().collect();
    assert!(lines.len() > 200, "the fixture is a whole session");
    assert!(lines[0].starts_with('#'), "the fixture says what it is");
    assert!(
        lines.iter().filter(|l| l.starts_with('#')).count() >= 2,
        "the fixture explains itself in comments"
    );
    assert!(
        lines.iter().any(|l| l.contains('f')),
        "the fixture fires the laser"
    );
    assert!(
        lines.iter().any(|l| l.contains('a') || l.contains('d')),
        "the fixture moves the ship"
    );
    for l in &lines {
        if l.starts_with('#') {
            continue; // comments explain the fixture and are not ticks
        }
        for c in l.chars() {
            assert!(
                c == ' ' || matches!(c, 'a' | 'd' | 'f' | 'p' | 'r' | 'q' | '#' | 'h' | 'l'),
                "the fixture only uses game keys, found {c:?} in {l:?}"
            );
        }
    }
}
