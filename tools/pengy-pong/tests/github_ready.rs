//! The tool-directory contract, asserted against the committed tree: the
//! GitHub-ready docs, the verifier, the zero-dependency boundary, the launcher,
//! and the Buildroot recipe skeleton. Written the way the
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
        "src/app.rs",
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
        v.contains(r#"cwd = "tools/pengy-pong""#),
        "the cwd is this tool directory"
    );
    assert!(v.contains(r#"expect = "exit0""#), "exit 0 is the pass condition");
}

#[test]
fn the_crate_has_no_dependencies_and_the_library_is_pure() {
    let c = read(tool().join("Cargo.toml"));
    assert!(c.contains("name = \"pengy-pong\""), "the package is pengy-pong");
    assert!(c.contains("edition = \"2021\""), "Rust 2021");
    // Without `cosmic` there are no dependencies: the rules and the replay
    // harness are std only, so `cargo test` needs no network and no display.
    let lines: Vec<&str> = c.lines().collect();
    let at = lines
        .iter()
        .position(|l| l.trim() == "[dependencies]")
        .expect("a [dependencies] table header");
    let after = lines[at + 1..].join("\n");
    let body = match after.find('[') {
        Some(stop) => &after[..stop],
        None => &after[..],
    };
    let body: String = body
        .lines()
        .filter(|l| !l.trim().starts_with('#') && !l.trim().is_empty())
        .collect();
    assert!(body.trim().is_empty(), "[dependencies] stays empty, found: {body}");
    // The window is libcosmic, optional, at the same pin as the other PengyOS
    // apps, so they all build from one cache.
    assert!(c.contains("cosmic = [\"dep:libcosmic\"]"), "the window is the cosmic feature");
    assert!(c.contains("[dependencies.libcosmic]") && c.contains("optional = true"));
    let pin = |toml: &str| {
        toml.lines()
            .find(|l| l.trim_start().starts_with("rev = "))
            .map(|l| l.trim().to_string())
    };
    let usage = read(repo().join("tools/pengyos-usage/Cargo.toml"));
    assert_eq!(pin(&c), pin(&usage), "libcosmic is pinned where pengyos-usage pins it");

    // src/lib.rs is the pure rules core: no files, no clock, no threads, no
    // process, no system entropy.
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
        "cosmic",
    ] {
        assert!(!lib.contains(forbidden), "src/lib.rs must not use {forbidden} — the rules are pure");
    }
    // The terminal front-end is gone; the window is behind the feature.
    let main = read(tool().join("src/cli.rs"));
    assert!(main.contains("#[cfg(feature = \"cosmic\")]\n#[path = \"app.rs\"]\nmod app;"), "the window is src/app.rs, a child of cli");
    for gone in ["stty", "?1049h", "is_terminal"] {
        assert!(!main.contains(gone), "src/cli.rs has no terminal code left: {gone}");
    }
}
#[test]
fn the_launcher_is_a_desktop_game_entry_named_by_the_app_id() {
    let app = read(tool().join("src/app.rs"));
    let id = "io.catbee.pengyos.PenguinPong";
    assert!(app.contains(&format!("pub const APP_ID: &str = \"{id}\";")), "the app id");
    let overlay = repo().join("buildroot-external/board/desktop/rootfs-overlay/usr/share");
    // The file is named by the app id, so COSMIC matches the window to it.
    let d = read(overlay.join(format!("applications/{id}.desktop")));
    assert!(d.contains("[Desktop Entry]") && d.contains("Type=Application"));
    assert!(d.contains("Exec=pengy-pong\n"), "Exec is the bare binary");
    assert!(d.contains("Terminal=false"), "a window, not a terminal game");
    assert!(d.contains("Categories=Game;"), "a game");
    assert!(d.contains(&format!("Icon={id}\n")), "with its own icon");
    assert!(
        overlay.join(format!("icons/hicolor/scalable/apps/{id}.svg")).is_file(),
        "and the icon is in the overlay"
    );
    assert!(!tool().join("launcher").exists(), "the terminal launcher is gone");
}
#[test]
fn the_desktop_build_ships_the_window() {
    // The image carries every game in one binary, tools/pengy-games, so it
    // carries one libcosmic: that crate links this one with its window on,
    // build-cosmic.sh builds it, and the Desktop post-build installs it with
    // /usr/bin/pengy-pong as a symlink to it.
    let games = read(repo().join("tools/pengy-games/Cargo.toml"));
    assert!(games.contains("pengy-pong = { path = \"../pengy-pong\" }"), "pengy-games links pengy-pong");
    assert!(games.contains("\"pengy-pong/cosmic\""), "and turns its window on");
    let build = read(repo().join("scripts/build-cosmic.sh"));
    let list = build
        .lines()
        .find(|l| l.trim_start().starts_with("for app in ") && l.contains("pengyos-install-gui"))
        .expect("the app list in build-cosmic.sh");
    assert!(list.split_whitespace().any(|w| w.trim_end_matches(';') == "pengy-games"), "build-cosmic.sh builds pengy-games");
    let post = read(repo().join("buildroot-external/board/desktop/post-build.sh"));
    assert!(post.contains("install-gui/release/pengy-games"), "post-build takes the host build");
    let games_loop = post
        .lines()
        .find(|l| l.trim_start().starts_with("for game in "))
        .expect("post-build links the games");
    assert!(games_loop.split_whitespace().any(|w| w.trim_end_matches(';') == "pong"), "/usr/bin/pengy-pong is linked");
    assert!(post.contains("ln -sfn pengy-games \"${TARGET_DIR}/usr/bin/pengy-${game}\""));
    // Not a Buildroot recipe, and nowhere but the Desktop.
    assert!(!repo().join("buildroot-external/package/pengy-pong").exists(), "no Buildroot recipe");
    assert!(!read(repo().join("buildroot-external/Config.in")).contains("PENGY_PONG"));
}
#[test]
fn the_fixture_is_a_replayable_winning_session() {
    let f = read(tool().join("fixtures/win.replay"));
    let lines: Vec<&str> = f.lines().collect();
    assert!(lines[0].starts_with('#'), "the fixture explains itself");
    assert!(lines
        .iter()
        .take(3)
        .any(|l| l.contains("--replay") && l.contains("--seed")),
        "the fixture says how to play it back"
    );
    // One line per tick, a whole session, and it is a real match.
    let ticks = lines.iter().filter(|l| !l.starts_with('#')).count();
    assert!(ticks > 2000, "the fixture is a whole session ({ticks} ticks)");
    assert!(lines.iter().all(|l| !l.starts_with('\t')));
    assert!(lines
        .iter()
        .filter(|l| !l.starts_with('#'))
        .all(|l| l.is_empty() || l.chars().all(|c| matches!(c, 'w' | 's'))));
}
