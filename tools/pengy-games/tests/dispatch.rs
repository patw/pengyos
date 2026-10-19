//! The dispatcher, through the real binary: by name, by first argument, and the
//! errors. Built without `cosmic`, so a game run with no harness flag reports
//! the missing window — the harness paths are what prove a game was reached.

use std::path::PathBuf;
use std::process::{Command, Output};

fn bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_pengy-games"))
}

fn run(exe: &PathBuf, args: &[&str]) -> Output {
    Command::new(exe).args(args).output().expect("the binary runs")
}

fn text(b: &[u8]) -> String {
    String::from_utf8_lossy(b).into_owned()
}

#[test]
fn the_first_argument_names_the_game_and_the_rest_are_its_own() {
    let o = run(&bin(), &["pong", "--version"]);
    assert_eq!(o.status.code(), Some(0));
    assert!(text(&o.stdout).starts_with("pengy-pong "), "{}", text(&o.stdout));
    // The binary name works as well as the short name.
    let o = run(&bin(), &["pengy-merge", "--version"]);
    assert!(text(&o.stdout).starts_with("pengy-merge "));
}

#[test]
fn every_game_runs_its_headless_harness() {
    for g in pengy_games::GAMES.iter() {
        let o = run(&bin(), &[g.name, "--headless", "--ticks", "40", "--no-save"]);
        assert_eq!(o.status.code(), Some(0), "{}: {}", g.name, text(&o.stderr));
        assert!(text(&o.stdout).contains("seed="), "{} printed its state", g.name);
    }
}

#[test]
fn a_symlink_named_for_a_game_runs_that_game() {
    let dir = std::env::temp_dir().join(format!("pengy-games-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let link = dir.join("pengy-rogue");
    let _ = std::fs::remove_file(&link);
    std::os::unix::fs::symlink(bin(), &link).unwrap();
    let o = run(&link, &["--version"]);
    assert!(text(&o.stdout).starts_with("pengy-rogue "), "{}", text(&o.stdout));
    // Through the symlink every argument is the game's, even one that names
    // another game.
    let o = run(&link, &["pong"]);
    assert_eq!(o.status.code(), Some(2), "rogue refuses a stray argument");
    assert!(text(&o.stderr).contains("pengy-rogue"));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn no_game_lists_the_games_and_exits_2() {
    let o = run(&bin(), &[]);
    assert_eq!(o.status.code(), Some(2));
    let usage = text(&o.stderr);
    for g in pengy_games::GAMES.iter() {
        assert!(usage.contains(g.binary) && usage.contains(g.title), "usage lists {}", g.name);
    }
    let o = run(&bin(), &["chess"]);
    assert_eq!(o.status.code(), Some(2));
    assert!(text(&o.stderr).contains("no game called \"chess\""));
}

#[test]
fn list_help_and_version_answer_on_stdout() {
    let o = run(&bin(), &["--list"]);
    assert_eq!(o.status.code(), Some(0));
    assert_eq!(text(&o.stdout).lines().count(), 8);
    let o = run(&bin(), &["--help"]);
    assert_eq!(o.status.code(), Some(0));
    assert!(text(&o.stdout).contains("pengy-games GAME"));
    let o = run(&bin(), &["--version"]);
    assert!(text(&o.stdout).starts_with("pengy-games "));
}

#[test]
fn a_game_without_the_window_built_in_says_so() {
    let o = run(&bin(), &["solitaire", "--no-save"]);
    assert_eq!(o.status.code(), Some(2));
    assert!(text(&o.stderr).contains("--features cosmic"));
}

#[test]
fn the_table_is_every_game_once() {
    let mut names: Vec<&str> = pengy_games::GAMES.iter().map(|g| g.name).collect();
    names.sort();
    names.dedup();
    assert_eq!(names.len(), 8);
    for g in pengy_games::GAMES.iter() {
        assert_eq!(g.binary, format!("pengy-{}", g.name));
        assert!(pengy_games::find(g.name).is_some() && pengy_games::find(g.binary).is_some());
    }
    assert!(pengy_games::dispatch("/usr/bin/pengy-games", &[]).is_none());
    let (g, rest) = pengy_games::dispatch("/usr/bin/pengy-floe", &["--headless".into()]).unwrap();
    assert_eq!((g.name, rest), ("floe", vec!["--headless".to_string()]));
}
