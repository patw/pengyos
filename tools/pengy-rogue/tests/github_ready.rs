//! The tool-directory contract, asserted against the committed tree: the
//! GitHub-ready docs, the verifier, the zero-dependency boundary, the pure
//! library, the launcher, the fixtures and the Buildroot recipe skeleton.
//! Written the way `pengy-panel`'s and the other pack-in games' `github_ready`
//! test is written.

use std::fs;
use std::path::PathBuf;

use pengy_rogue::*;

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
        "fixtures/boss.state",
        "fixtures/win.replay",
        "fixtures/demo.replay",
        "launcher/pengy-rogue.desktop",
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
        v.contains(r#"cwd = "tools/pengy-rogue""#),
        "the cwd is this tool directory"
    );
    assert!(v.contains(r#"expect = "exit0""#), "exit 0 is the pass condition");
}

#[test]
fn the_crate_has_no_dependencies_and_the_library_is_pure() {
    let c = read(tool().join("Cargo.toml"));
    assert!(
        c.contains("name = \"pengy-rogue\""),
        "the package is pengy-rogue"
    );
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
    assert!(
        main.contains("?1049h") && main.contains("?1049l"),
        "the alternate screen"
    );
    assert!(main.contains("\\x1b[H"), "one home + repaint per frame");
    assert!(main.contains("NO_COLOR"), "NO_COLOR is honoured");
    assert!(
        main.contains("is_terminal"),
        "and a missing TTY is a path, not a crash"
    );
    assert!(
        main.contains("Duration::from_millis") && main.contains("recv_timeout"),
        "a fixed timestep that sleeps instead of spinning"
    );
}

#[test]
fn the_launcher_is_a_game_entry_that_runs_the_binary_in_a_terminal() {
    let d = read(tool().join("launcher/pengy-rogue.desktop"));
    assert!(d.contains("[Desktop Entry]"), "the desktop entry header");
    assert!(d.contains("Exec=pengy-rogue"), "Exec is the bare binary");
    assert!(d.contains("Terminal=true"), "it is a terminal game");
    assert!(d.contains("Categories=Game;"), "and a game");
    assert!(d.contains("Type=Application"), "Type is Application");
    assert!(d.contains("Name="), "it has a name");
}

#[test]
fn the_buildroot_recipe_builds_this_crate_into_usr_bin() {
    let pkg = repo()
        .join("buildroot-external")
        .join("package")
        .join("pengy-rogue");
    for name in ["Config.in", "pengy-rogue.mk"] {
        assert!(pkg.join(name).is_file(), "missing {name}");
    }
    let mk = read(pkg.join("pengy-rogue.mk"));
    assert!(
        mk.contains("Ice Rogue") || mk.contains("pengy-rogue"),
        "names the game"
    );
    assert!(mk.contains("cargo build"), "builds with cargo");
    assert!(mk.contains("--offline"), "offline: no network, no vendored crates");
    assert!(mk.contains("--release"), "release build");
    assert!(
        mk.contains("$(TARGET_DIR)/usr/bin/pengy-rogue"),
        "installs the binary to /usr/bin"
    );
    assert!(
        mk.contains("cargo-package"),
        "uses the cargo-package infrastructure"
    );
    assert!(
        mk.contains("launcher/pengy-rogue.desktop"),
        "installs the launcher from this source tree"
    );
    let kconfig = read(pkg.join("Config.in"));
    assert!(
        kconfig.contains("config BR2_PACKAGE_PENGY_ROGUE"),
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
    assert!(
        kconfig.contains("pengy-rogue"),
        "the help text names the game"
    );
}

#[test]
fn the_image_wiring_is_left_to_the_integration_pass() {
    // Ten games touching three shared files is a conflict generator. This
    // change adds the recipe only.
    let ext = repo().join("buildroot-external");
    let cfg = ext.join("Config.in");
    if cfg.is_file() {
        assert!(
            !read(cfg).contains("PENGY_ROGUE"),
            "buildroot-external/Config.in is the integration pass's to edit"
        );
    }
    for layer in ["core", "server", "desktop"] {
        let p = ext.join(format!("configs/layers/{layer}.config"));
        if p.is_file() {
            assert!(
                !read(p).contains("PENGY_ROGUE"),
                "configs/layers/{layer}.config is the integration pass's to edit"
            );
        }
    }
    let overlay = ext.join("board/desktop/rootfs-overlay");
    if overlay.is_dir() {
        for f in overlay.join("usr/bin").read_dir().into_iter().flatten().flatten() {
            let name = f.file_name().to_string_lossy().to_string();
            assert!(
                !name.contains("pengy-rogue"),
                "the game is a package, not an overlay file"
            );
        }
    }
}

#[test]
fn the_fixtures_are_a_legal_boss_floor_a_script_that_wins_it_and_a_whole_session()
{
    // fixtures/boss.state: floor 5, The Thaw wounded to 1 hp, right below the
    // penguin. A legal position, read by the same parser the binary uses.
    let state = read(tool().join("fixtures/boss.state"));
    let g = parse_state(&state).expect("the fixture position is legal");
    assert_eq!(g.floor, FLOORS, "it is the boss floor");
    assert_eq!(g.state, State::Play);
    let boss = g
        .monsters
        .iter()
        .find(|m| m.kind == MonsterKind::Thaw)
        .expect("The Thaw is there");
    assert_eq!(boss.hp, 1, "and it is one swing from breaking");
    assert_eq!(
        (boss.x as i32 - g.player.x as i32).abs() + (boss.y as i32 - g.player.y as i32).abs(),
        1,
        "and it is adjacent"
    );
    assert!(g.hp > 0, "the penguin is alive");

    // fixtures/win.replay: a whole session of keys a player can press, that
    // wins it.
    let replay = read(tool().join("fixtures/win.replay"));
    let lines: Vec<&str> = replay.lines().collect();
    assert!(lines[0].starts_with('#'), "the fixture explains itself");
    assert!(
        lines.iter().take(8).any(|l| l.contains("--replay") && l.contains("--seed")),
        "the fixture says how to play it back"
    );
    let keys: Vec<char> = replay
        .lines()
        .filter(|l| !l.starts_with('#'))
        .flat_map(|l| l.chars().filter(|c| *c != ' '))
        .collect();
    assert!(keys
        .iter()
        .all(|c| matches!(c, 'h' | 'j' | 'k' | 'l' | 'y' | 'u' | 'b' | 'n' | '.' | 'g' | 'i' | '>')),
        "the script is only keys the game accepts");

    let mut g = parse_state(&state).unwrap();
    for c in keys {
        g.key(c);
        g.tick();
    }
    assert_eq!(g.state, State::Win, "the committed replay beats The Thaw");
    assert_eq!(g.score(), (5 * 100 + g.gold + g.kills) as i64);

    // fixtures/demo.replay: a whole autopilot session, deterministic.
    let demo = read(tool().join("fixtures/demo.replay"));
    let play = |text: &str| {
        let mut g = Game::new(12);
        for line in text.lines() {
            if line.starts_with('#') {
                continue;
            }
            if g.state != State::Play {
                break;
            }
            for c in line.chars().filter(|c| *c != ' ') {
                g.key(c);
            }
            g.tick();
        }
        g
    };
    let a = play(&demo);
    let b = play(&demo);
    assert_eq!(a.state_hash(), b.state_hash(), "the demo session is reproducible");
    assert!(a.turns >= 100, "and it is a whole session: {} turns", a.turns);
    assert!(a.deepest >= 2, "the demo penguin got off floor 1");
    assert!(a.kills > 0, "and it fought");
}

#[test]
fn the_game_is_the_documented_dungeon_with_the_documented_tuning() {
    // The numbers the README and spec.md quote are the numbers the code uses.
    assert_eq!(FLOORS, 5);
    assert_eq!(MAP_W, 54);
    assert_eq!(MAP_H, 15);
    assert_eq!(FOV_RADIUS, 7);
    assert_eq!(START_HP, 12);
    assert_eq!(START_DEFENCE, 0);
    assert_eq!(FISH_HEAL, 3);
    assert_eq!(PARKA_DEFENCE, 1);
    assert_eq!(SCORE_PER_FLOOR, 100);
    assert_eq!(ROOMS_MIN, 4);
    assert_eq!(ROOMS_MAX, 9);
    assert_eq!(ROOM_W_MIN, 4);
    assert_eq!(ROOM_W_MAX, 10);
    assert_eq!(ROOM_H_MIN, 4);
    assert_eq!(ROOM_H_MAX, 6);
    assert_eq!(MonsterKind::Thaw.hp(), 20);
    assert_eq!(MonsterKind::Thaw.damage(), 4);
    assert_eq!(REF_WIDTH, 80);
    assert_eq!(REF_HEIGHT, 24);
    let g = Game::new(1);
    assert_eq!(g.floor, 1);
    assert_eq!(g.hp, START_HP);
    assert_eq!(g.score(), 100, "floor 1, no gold, no kills");
    assert_eq!(g.map.len(), MAP_W * MAP_H);
    assert!(g
        .map
        .iter()
        .all(|t| matches!(t, b'#' | b'.' | b'>')),
        "the terrain alphabet is # . >");
}
