//! The tool-directory contract, and the image wiring: every game the table
//! knows has a crate linked in, a launcher, an icon, and a symlink.

use std::fs;
use std::path::PathBuf;

fn tool() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn repo() -> PathBuf {
    tool().join("../..")
}

fn read(p: PathBuf) -> String {
    fs::read_to_string(&p).unwrap_or_else(|e| panic!("{}: {e}", p.display()))
}

#[test]
fn the_tool_directory_has_its_docs_and_verifier() {
    for f in ["README.md", "spec.md", "LICENSE", "CHANGELOG.md", "verify.toml", "src/lib.rs", "src/main.rs"] {
        assert!(tool().join(f).is_file(), "missing {f}");
    }
    assert!(read(tool().join("LICENSE")).contains("Copyright (c) 2026 Pat Wendorf"));
    assert!(read(tool().join("CHANGELOG.md")).contains("## [0.1.0]"));
    let v = read(tool().join("verify.toml"));
    assert!(v.contains("cwd = \"tools/pengy-games\"") && v.contains("\"cargo\", \"test\""));
}

#[test]
fn every_game_is_linked_with_its_window_and_wired_into_the_desktop() {
    let cargo = read(tool().join("Cargo.toml"));
    let post = read(repo().join("buildroot-external/board/desktop/post-build.sh"));
    let games_loop = post
        .lines()
        .find(|l| l.trim_start().starts_with("for game in "))
        .expect("post-build links the games");
    let apps = repo().join("buildroot-external/board/desktop/rootfs-overlay/usr/share/applications");
    for g in pengy_games::GAMES.iter() {
        assert!(cargo.contains(&format!("{} = {{ path = \"../{}\" }}", g.binary, g.binary)), "{} is linked", g.binary);
        assert!(cargo.contains(&format!("\"{}/cosmic\"", g.binary)), "{} gets its window", g.binary);
        assert!(games_loop.split_whitespace().any(|w| w.trim_end_matches(';') == g.name), "{} is symlinked", g.binary);
        // The launcher runs the symlink, so the window's app id picks the entry.
        let entry = fs::read_dir(&apps)
            .unwrap()
            .flatten()
            .map(|e| read(e.path()))
            .find(|d| d.contains(&format!("Exec={}\n", g.binary)));
        assert!(entry.is_some(), "a launcher runs {}", g.binary);
    }
    let build = read(repo().join("scripts/build-cosmic.sh"));
    assert!(build.contains(" pengy-games;"), "build-cosmic.sh builds pengy-games");
    assert!(post.contains("install -Dm0755 \"${GAMES_GUI}\" \"${TARGET_DIR}/usr/bin/pengy-games\""));
    // Only the shared binary ships: no game is built or installed on its own.
    for g in pengy_games::GAMES.iter() {
        assert!(!build.contains(&format!(" {};", g.binary)) && !build.contains(&format!(" {} ", g.binary)), "{} is not built alone", g.binary);
        assert!(!post.contains(&format!("release/{}\"", g.binary)), "{} is not installed alone", g.binary);
    }
}

#[test]
fn without_cosmic_the_crate_depends_on_the_games_only() {
    let cargo = read(tool().join("Cargo.toml"));
    let deps = cargo.split("[dependencies]").nth(1).expect("[dependencies]");
    for line in deps.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')) {
        if line.starts_with('[') {
            break;
        }
        assert!(line.starts_with("pengy-") && line.contains("path = \"../pengy-"), "only the game crates: {line}");
    }
}
