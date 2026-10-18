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
        "launcher/pengy-blocks.desktop",
    ] {
        assert!(tool().join(name).is_file(), "missing {name}");
    }
}

#[test]
fn license_is_mit_and_names_the_copyright_holder() {
    let lic = read(tool().join("LICENSE"));
    assert!(lic.starts_with("MIT License"), "LICENSE is the MIT text");
    assert!(lic.contains("Copyright (c) 2026 Pat Wendorf"));
    assert!(lic.contains("AS IS"), "the warranty disclaimer is there");
}

#[test]
fn changelog_follows_keep_a_changelog_and_has_010() {
    let c = read(tool().join("CHANGELOG.md"));
    assert!(c.contains("# Changelog"), "the heading");
    assert!(c.contains("Keep a Changelog"), "the format is named");
    assert!(c.contains("## [0.1.0]"), "the 0.1.0 entry exists");
    assert!(c.contains("### Added"), "and it says what was added");
}

#[test]
fn the_verifier_runs_cargo_test_in_this_directory() {
    let v = read(tool().join("verify.toml"));
    assert!(v.contains("[[check]]"), "verify.toml is the gate's check list");
    assert!(v.contains("cargo test"), "the check is the test suite");
    assert!(
        v.contains(r#"run = ["cargo", "test", "--quiet"]"#),
        "the exact command the gate runs"
    );
    assert!(
        v.contains(r#"cwd = "tools/pengy-blocks""#),
        "the cwd is this tool directory"
    );
    assert!(v.contains(r#"expect = "exit0""#), "exit 0 is the pass condition");
}

#[test]
fn the_crate_has_no_dependencies_and_the_library_is_pure() {
    let c = read(tool().join("Cargo.toml"));
    assert!(c.contains("name = \"pengy-blocks\""), "the package is pengy-blocks");
    assert!(c.contains("edition = \"2021\""), "Rust 2021");
    assert!(
        c.contains("[dependencies]"),
        "the dependencies table exists (and stays empty)"
    );
    // The [dependencies] table is the text between its header and the next one.
    let lines: Vec<&str> = c.lines().collect();
    let at = lines
        .iter()
        .position(|l| l.trim() == "[dependencies]")
        .expect("a [dependencies] table header");
    let after = lines[at + 1..].join("\n");
    let body = match after.find("[") {
        Some(stop) => &after[..stop],
        None => &after[..],
    };
    let body: String = body
        .lines()
        .filter(|l| !l.trim().starts_with('#') && !l.trim().is_empty())
        .collect();
    assert!(
        body.trim().is_empty(),
        "[dependencies] must stay empty, found: {body}"
    );

    // src/lib.rs is the pure rules core: no I/O, no clock, no threads, no
    // process, no randomness from the system.
    let lib = read(tool().join("src/lib.rs"));
    for forbidden in [
        "std::fs",
        "std::process",
        "std::thread",
        "std::time",
        "std::io",
        "std::env",
        "Instant",
        "SystemTime",
        "Command::new",
    ] {
        assert!(
            !lib.contains(forbidden),
            "src/lib.rs must not use {forbidden} — the rules are pure"
        );
    }
    // The front-end is the only thing that touches the outside world.
    let main = read(tool().join("src/main.rs"));
    assert!(main.contains("stty"), "the keyboard goes raw through stty");
    assert!(
        main.contains("stty sane") || main.contains("\"sane\""),
        "and back to sane"
    );
    assert!(main.contains("mpsc"), "keys arrive over a channel");
    assert!(main.contains("?1049h") && main.contains("?1049l"), "the alternate screen");
    assert!(main.contains("\\x1b[H"), "one home + repaint per frame");
    assert!(main.contains("NO_COLOR"), "NO_COLOR is honoured");
    assert!(main.contains("is_terminal"), "and a missing TTY is a path, not a crash");
}

#[test]
fn the_launcher_is_a_game_entry_that_runs_the_binary_in_a_terminal() {
    let d = read(tool().join("launcher/pengy-blocks.desktop"));
    assert!(d.contains("[Desktop Entry]"), "the desktop entry header");
    assert!(d.contains("Exec=pengy-blocks"), "Exec is the bare binary");
    assert!(d.contains("Terminal=true"), "it is a terminal game");
    assert!(d.contains("Categories=Game;"), "and a game");
    assert!(d.contains("Type=Application"), "Type is Application");
    assert!(d.contains("Name="), "it has a name");
}

#[test]
fn the_buildroot_recipe_builds_this_crate_into_usr_bin() {
    let pkg = repo().join("buildroot-external").join("package").join("pengy-blocks");
    for name in ["Config.in", "pengy-blocks.mk"] {
        assert!(pkg.join(name).is_file(), "missing {name}");
    }
    let mk = read(pkg.join("pengy-blocks.mk"));
    assert!(
        mk.contains("Glacier Blocks") || mk.contains("pengy-blocks"),
        "names the game"
    );
    assert!(mk.contains("cargo build"), "builds with cargo");
    assert!(mk.contains("--offline"), "offline: no network, no vendored crates");
    assert!(mk.contains("--release"), "release build");
    assert!(
        mk.contains("$(TARGET_DIR)/usr/bin/pengy-blocks"),
        "installs the binary to /usr/bin"
    );
    assert!(mk.contains("cargo-package"), "uses the cargo-package infrastructure");
    assert!(
        mk.contains("launcher/pengy-blocks.desktop"),
        "installs the launcher from this source tree"
    );
    let kconfig = read(pkg.join("Config.in"));
    assert!(
        kconfig.contains("config BR2_PACKAGE_PENGY_BLOCKS"),
        "the kconfig symbol"
    );
    assert!(kconfig.contains("bool"), "it is a boolean option");
    assert!(kconfig.contains("help"), "with help text");
    assert!(
        kconfig.contains("depends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS")
            && kconfig.contains("select BR2_PACKAGE_HOST_RUSTC"),
        "the only build dependency is the Rust toolchain"
    );
    assert!(
        !kconfig.contains("depends on BR2_PACKAGE_SYSTEMD")
            && !kconfig.contains("select BR2_PACKAGE_SYSTEMD"),
        "the game needs no libraries at run time"
    );
    assert!(kconfig.contains("pengy-blocks"), "the help text names the game");
}

#[test]
fn the_image_wiring_is_left_to_the_integration_pass() {
    // Ten games touching three shared files is a conflict generator. This
    // change adds the recipe only.
    let ext = repo().join("buildroot-external");
    let cfg = ext.join("Config.in");
    if cfg.is_file() {
        assert!(
            !read(cfg).contains("PENGY_BLOCKS"),
            "buildroot-external/Config.in is the integration pass's to edit"
        );
    }
    for layer in ["core", "server", "desktop"] {
        let p = ext.join(format!("configs/layers/{layer}.config"));
        if p.is_file() {
            assert!(
                !read(p).contains("PENGY_BLOCKS"),
                "configs/layers/{layer}.config is the integration pass's to edit"
            );
        }
    }
    let overlay = ext.join("board/desktop/rootfs-overlay");
    if overlay.is_dir() {
        for f in overlay.join("usr/bin").read_dir().into_iter().flatten().flatten() {
            let name = f.file_name().to_string_lossy().to_string();
            assert!(!name.contains("pengy-blocks"), "the game is a package, not an overlay file");
        }
    }
}

#[test]
fn the_fixture_is_a_replayable_winning_session() {
    let f = read(tool().join("fixtures/win.replay"));
    let lines: Vec<&str> = f.lines().collect();
    assert!(lines[0].starts_with('#'), "the fixture explains itself");
    assert!(
        lines.iter().take(5).any(|l| l.contains("--replay") && l.contains("--seed")),
        "the fixture says how to play it back"
    );
    // One line per tick, a whole session, and only keys a player can press.
    let ticks = lines.iter().filter(|l| !l.starts_with('#')).count();
    assert!(ticks > 1000, "the fixture is a whole session ({ticks} ticks)");
    assert!(lines.iter().all(|l| !l.starts_with('\t')));
    assert!(lines
        .iter()
        .filter(|l| !l.starts_with('#'))
        .all(|l| l.chars().all(|c| matches!(c, 'h' | 'l' | 'j' | 'k' | 'x' | 'z' | 'c' | '_' ))));
}
