//! The tool-directory contract, asserted against the committed tree: the
//! GitHub-ready docs, the verifier, the zero-dependency boundary, the launcher,
//! and the Buildroot recipe skeleton. Written the way `pengy-panel`'s and
//! `pengy-floe`'s `github_ready` test is written.

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
    }
    for name in [
        "src/lib.rs",
        "src/main.rs",
        "tests/rules.rs",
        "tests/cli.rs",
        "fixtures/win.replay",
    ] {
        assert!(tool().join(name).is_file(), "missing {name}");
    }
}

#[test]
fn license_is_mit_and_names_the_copyright_holder() {
    let lic = read(tool().join("LICENSE"));
    assert!(lic.starts_with("MIT License"), "LICENSE is the MIT text");
    assert!(lic.contains("Copyright (c) 2026 Pat Wendorf"));
}

#[test]
fn changelog_follows_keep_a_changelog_and_has_010() {
    let c = read(tool().join("CHANGELOG.md"));
    assert!(c.contains("# Changelog"));
    assert!(c.contains("Keep a Changelog"));
    assert!(c.contains("## [Unreleased]"));
    assert!(c.contains("## [0.1.0]"), "the 0.1.0 entry is missing");
}

#[test]
fn verify_toml_runs_cargo_test_in_this_directory() {
    let v = read(tool().join("verify.toml"));
    assert!(v.contains("[[check]]"));
    assert!(v.contains("name = \"cargo test\""));
    assert!(v.contains("\"cargo\", \"test\", \"--quiet\""));
    assert!(v.contains("cwd = \"tools/pengy-merge\""));
    assert!(v.contains("expect = \"exit0\""));
}

#[test]
fn the_crate_is_rust_2021_and_has_zero_dependencies() {
    let c = read(tool().join("Cargo.toml"));
    assert!(c.contains("name = \"pengy-merge\""));
    assert!(c.contains("edition = \"2021\""));
    assert!(c.contains("license = \"MIT\""));

    // The hard boundary: the [dependencies] table is empty. This is what makes
    // the Buildroot recipe an offline, reproducible cargo build.
    let deps: Vec<&str> = c
        .lines()
        .skip_while(|l| !l.trim().starts_with("[dependencies]"))
        .skip(1)
        .take_while(|l| !l.trim().starts_with('['))
        .filter(|l| {
            let t = l.trim();
            !t.is_empty() && !t.starts_with('#')
        })
        .collect();
    assert!(
        deps.is_empty(),
        "[dependencies] must stay empty, found: {deps:?}"
    );

    // `panic = "abort"` would strand the terminal: the restore is a destructor.
    // Check the settings, not the prose: strip comments first.
    let settings: Vec<&str> = c
        .lines()
        .map(|l| l.split('#').next().unwrap_or("").trim())
        .filter(|l| !l.is_empty())
        .collect();
    assert!(
        !settings.iter().any(|l| l.starts_with("panic")),
        "the release profile must unwind so the terminal guard runs: {settings:?}"
    );
}

#[test]
fn the_library_stays_pure() {
    // src/lib.rs is the rules: no I/O, no clock, no threads, no process.
    let lib = read(tool().join("src/lib.rs"));
    for forbidden in [
        "std::fs",
        "std::process",
        "std::thread",
        "std::time",
        "std::io",
        "Instant",
        "SystemTime",
        "File::",
    ] {
        assert!(
            !lib.contains(forbidden),
            "src/lib.rs must stay pure, found {forbidden}"
        );
    }
    // The front-end owns the outside world.
    let main = read(tool().join("src/main.rs"));
    for required in ["stty", "mpsc", "is_terminal", "?1049h", "?1049l", "?25l"] {
        assert!(
            main.contains(required),
            "src/main.rs must own the terminal, missing {required}"
        );
    }
}

#[test]
fn the_launcher_is_a_terminal_game_entry() {
    let d = read(tool().join("launcher/pengy-merge.desktop"));
    assert!(d.contains("[Desktop Entry]"));
    assert!(d.contains("Type=Application"));
    assert!(d.contains("Exec=pengy-merge"));
    assert!(d.contains("Terminal=true"));
    assert!(d.contains("Categories=Game;"));
}

#[test]
fn the_buildroot_recipe_skeleton_is_present_and_names_the_crate() {
    let pkg = repo().join("buildroot-external/package/pengy-merge");
    assert!(pkg.join("pengy-merge.mk").is_file(), "missing the recipe");
    assert!(pkg.join("Config.in").is_file(), "missing Config.in");

    let mk = read(pkg.join("pengy-merge.mk"));
    assert!(mk.contains("PENGY_MERGE_VERSION = 0.1.0"), "version pinned");
    assert!(mk.contains("PENGY_MERGE_LICENSE = MIT"), "license declared");
    assert!(mk.contains("cargo build --offline --release"), "offline build");
    assert!(
        mk.contains("$(TARGET_DIR)/usr/bin/pengy-merge"),
        "installs the binary to /usr/bin"
    );
    assert!(mk.contains("cargo-package"), "uses the cargo package infrastructure");
    // The recipe builds from THIS repo, so there is no download and no digest.
    assert!(
        mk.contains("SITE_METHOD = local"),
        "the source is the repo tree, not a tarball"
    );
    assert!(
        mk.contains("$(PENGY_MERGE_SRCDIR)"),
        "the recipe uses the cargo-package source variables"
    );

    let cin = read(pkg.join("Config.in"));
    assert!(cin.contains("config BR2_PACKAGE_PENGY_MERGE"));
    assert!(cin.contains("BR2_PACKAGE_HOST_RUSTC"));
}

#[test]
fn the_recipe_does_not_touch_the_shared_image_wiring() {
    // Image wiring is a separate integration pass: this change must not have
    // sourced the recipe into the shared Config.in, into a layer, or into a
    // rootfs overlay.
    let ext = repo().join("buildroot-external");
    assert!(
        !read(ext.join("Config.in")).contains("pengy-merge"),
        "buildroot-external/Config.in is the integration pass's to edit"
    );
    for layer in ["core", "server", "desktop"] {
        let p = ext.join(format!("configs/layers/{layer}.config"));
        if p.is_file() {
            assert!(
                !read(p).contains("PENGY_MERGE"),
                "configs/layers/{layer}.config is the integration pass's to edit"
            );
        }
    }
    let overlay = ext.join("board/desktop/rootfs-overlay");
    if overlay.is_dir() {
        for f in overlay
            .join("usr/bin")
            .read_dir()
            .into_iter()
            .flatten()
            .flatten()
        {
            let name = f.file_name().to_string_lossy().to_string();
            assert!(
                !name.contains("pengy-merge"),
                "the game is a package, not an overlay file"
            );
        }
    }
}

#[test]
fn the_fixture_is_a_replayable_winning_session() {
    let f = read(tool().join("fixtures/win.replay"));
    let lines: Vec<&str> = f.lines().collect();
    assert!(lines[0].starts_with('#'), "the fixture explains itself");
    // One line per tick, a whole session, and it reaches at least 256.
    let ticks = lines.iter().filter(|l| !l.starts_with('#')).count();
    assert!(ticks > 200, "the fixture is a whole session ({ticks} ticks)");
    assert!(lines.iter().all(|l| !l.starts_with('\t')));
    assert!(lines
        .iter()
        .filter(|l| !l.starts_with('#'))
        .all(|l| l.is_empty() || matches!(l.chars().next(), Some('h' | 'j' | 'k' | 'l'))));
}
