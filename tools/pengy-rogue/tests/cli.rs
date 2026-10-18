//! The front-end, driven as the gate drives it: the real binary, no pty, no
//! network, no root. Every scripted path must print plain text and exit 0.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

fn tool() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn bin() -> String {
    env!("CARGO_BIN_EXE_pengy-rogue").to_string()
}

/// A scratch directory under the crate's target dir (the machine is tight on
/// /tmp, and the repo says to use /data/tmp for the big stuff).
fn scratch(name: &str) -> PathBuf {
    let dir = tool().join("target").join("test-scratch").join(name);
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("scratch dir");
    dir
}

/// Run the binary with no tty on stdin: exactly the shape of the repo gate and
/// pengyos-qemu-verify.
fn run(args: &[&str]) -> Output {
    Command::new(bin())
        .args(args)
        .current_dir(tool())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .expect("the binary runs")
}

fn stdout_of(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).to_string()
}

fn kv(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

/// The frame part of a `--print-frame` output: everything from the first line
/// that is not a `key=value` line.
fn frame_of(text: &str) -> String {
    let start = text
        .lines()
        .position(|l| !l.contains('='))
        .unwrap_or(0);
    text.lines().skip(start).collect::<Vec<_>>().join("\n")
}

fn get(text: &str, key: &str) -> String {
    kv(text)
        .into_iter()
        .find(|(k, _)| k == key)
        .map(|(_, v)| v)
        .unwrap_or_else(|| panic!("no {key}= in the output:\n{text}"))
}

#[test]
fn help_lists_every_flag_and_every_key() {
    let o = run(&["--help"]);
    assert!(o.status.success(), "--help exits 0");
    let t = stdout_of(&o);
    for flag in [
        "--seed",
        "--headless",
        "--replay",
        "--state",
        "--save",
        "--record",
        "--ticks",
        "--autopilot",
        "--mono",
        "--no-color",
        "--print-frame",
        "--width",
        "--height",
        "--data-dir",
        "--no-save",
        "--help",
        "--version",
    ] {
        assert!(t.contains(flag), "--help does not list {flag}");
    }
    for key in [
        "h j k l", "y u b n", ". ", "g ", "i ", "> ", "q ",
    ] {
        assert!(t.contains(key), "--help does not list the {key:?} key");
    }
    assert!(t.contains("floor * 100 + gold + kills"), "and the score formula");
    assert!(t.contains("1d4+1"), "and the attack roll");
    assert!(t.contains("The Thaw"), "and the boss");
    assert!(t.contains("radius 7") || t.contains("Radius 7"), "and the FOV");
}

#[test]
fn version_names_the_game() {
    let o = run(&["--version"]);
    assert!(o.status.success());
    let t = stdout_of(&o);
    assert!(t.contains("pengy-rogue"), "{t}");
    assert!(t.contains("Ice Rogue"), "{t}");
    assert!(t.contains("1980"), "{t}");
}

#[test]
fn headless_needs_no_tty_and_prints_key_value_lines() {
    let o = run(&["--headless", "--seed", "1", "--ticks", "50", "--no-save"]);
    assert!(o.status.success(), "the headless path exits 0 with no pty");
    let t = stdout_of(&o);
    assert!(!t.contains('\x1b'), "and prints no escape codes");
    for key in [
        "state", "seed", "floor", "turns", "hp", "gold", "kills", "score", "hash",
    ] {
        assert!(t.lines().any(|l| l.starts_with(&format!("{key}="))), "no {key}=");
    }
    assert_eq!(get(&t, "seed"), "1");
    assert_eq!(get(&t, "turns"), "50", "every tick was played");
    assert!(get(&t, "floor").parse::<i64>().unwrap() >= 1);
}

#[test]
fn a_run_with_no_tty_on_stdin_takes_the_headless_path() {
    // No --headless at all: stdin is /dev/null, so the game must not try to
    // take over a terminal. This is the shape of the repo gate.
    let o = run(&["--seed", "5", "--ticks", "30", "--no-save"]);
    assert!(o.status.success());
    let t = stdout_of(&o);
    assert!(!t.contains("\x1b[?1049h"), "it never entered the alternate screen");
    assert!(t.starts_with("state="), "it printed the plain state: {t}");
}

#[test]
fn the_same_seed_and_the_same_script_give_the_same_hash() {
    let a = stdout_of(&run(&[
        "--replay",
        "fixtures/demo.replay",
        "--seed",
        "12",
        "--no-save",
    ]));
    let b = stdout_of(&run(&[
        "--replay",
        "fixtures/demo.replay",
        "--seed",
        "12",
        "--no-save",
    ]));
    assert_eq!(get(&a, "hash"), get(&b, "hash"), "a replay diverged");
    assert_eq!(get(&a, "turns"), get(&b, "turns"));
    assert_eq!(get(&a, "score"), get(&b, "score"));
    // A different seed is a different dungeon, so a different hash.
    let c = stdout_of(&run(&[
        "--replay",
        "fixtures/demo.replay",
        "--seed",
        "13",
        "--no-save",
    ]));
    assert_ne!(get(&a, "hash"), get(&c, "hash"), "the seed is in the hash");
}

#[test]
fn the_committed_replay_beats_the_boss() {
    let o = run(&[
        "--state",
        "fixtures/boss.state",
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "1",
        "--no-save",
    ]);
    assert!(o.status.success(), "the acceptance harness exits 0");
    let t = stdout_of(&o);
    assert_eq!(get(&t, "state"), "win", "the committed replay wins");
    assert_eq!(get(&t, "floor"), "5");
    assert_eq!(get(&t, "kills"), "8");
    assert_eq!(get(&t, "score"), "517", "floor*100 + gold + kills");
    assert!(get(&t, "message").contains("win"));
}

#[test]
fn the_committed_demo_replay_is_a_whole_session() {
    let o = run(&[
        "--replay",
        "fixtures/demo.replay",
        "--seed",
        "12",
        "--no-save",
        "--print-frame",
    ]);
    assert!(o.status.success());
    let t = stdout_of(&o);
    assert_eq!(get(&t, "turns"), "400");
    assert_eq!(get(&t, "deepest"), "4", "the demo penguin got to floor 4");
    assert!(get(&t, "kills").parse::<i64>().unwrap() >= 5);
    assert!(get(&t, "score").parse::<i64>().unwrap() >= 400);
    // --print-frame drew the reference window.
    assert!(frame_of(&t).contains('@'), "the frame shows the penguin");
}

#[test]
fn a_saved_position_round_trips_through_the_binary() {
    let dir = scratch("save");
    let pos = dir.join("pos.state");
    let o = run(&[
        "--headless",
        "--seed",
        "7",
        "--ticks",
        "40",
        "--save",
        pos.to_str().unwrap(),
        "--no-save",
    ]);
    assert!(o.status.success());
    assert!(pos.is_file(), "--save wrote the position");
    let text = fs::read_to_string(&pos).unwrap();
    assert!(text.contains("floor="), "the position is the documented format");
    assert!(text.contains("map="));

    // Playing on from the saved position continues the same run.
    let o = run(&[
        "--state",
        pos.to_str().unwrap(),
        "--headless",
        "--seed",
        "7",
        "--ticks",
        "10",
        "--no-save",
    ]);
    assert!(o.status.success());
    let t = stdout_of(&o);
    assert_eq!(get(&t, "turns"), "50", "the turn counter carried over");
}

#[test]
fn record_produces_a_replay_the_replayer_accepts() {
    let dir = scratch("record");
    let script = dir.join("session.replay");
    let o = run(&[
        "--headless",
        "--seed",
        "31",
        "--ticks",
        "120",
        "--record",
        script.to_str().unwrap(),
        "--no-save",
    ]);
    assert!(o.status.success());
    let text = fs::read_to_string(&script).unwrap();
    assert!(text.starts_with('#'), "the fixture explains itself");
    assert!(text
        .lines()
        .take(4)
        .any(|l| l.contains("--replay") && l.contains("--seed")));
    let lines: Vec<&str> = text.lines().filter(|l| !l.starts_with('#')).collect();
    assert_eq!(lines.len(), 120, "one line per tick");
    assert!(lines
        .iter()
        .all(|l| l
            .chars()
            .all(|c| matches!(c, 'h' | 'j' | 'k' | 'l' | 'y' | 'u' | 'b' | 'n' | '.' | 'g' | '>'))),
        "the script is only keys the game accepts");
    let o = run(&[
        "--replay",
        script.to_str().unwrap(),
        "--seed",
        "31",
        "--no-save",
    ]);
    assert!(o.status.success());
    assert_eq!(get(&stdout_of(&o), "turns"), "120");
}

#[test]
fn mono_and_no_color_emit_no_escape_codes_and_no_box_drawing() {
    let o = run(&[
        "--headless",
        "--seed",
        "3",
        "--ticks",
        "20",
        "--no-save",
        "--print-frame",
        "--mono",
    ]);
    assert!(o.status.success());
    let t = stdout_of(&o);
    assert!(!t.contains('\x1b'), "--mono emits no escape codes");
    assert!(!t.contains('│') && !t.contains('┌'), "--mono emits no box drawing");
    assert!(t.contains('.'), "--mono draws the explored floor as '.'");

    // NO_COLOR in the environment is honoured without a flag.
    let o = Command::new(bin())
        .args([
            "--headless",
            "--seed",
            "3",
            "--ticks",
            "20",
            "--no-save",
            "--print-frame",
        ])
        .env("NO_COLOR", "1")
        .current_dir(tool())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .expect("run");
    assert!(o.status.success());
    assert!(
        !stdout_of(&o).contains('\x1b'),
        "NO_COLOR suppressed the colour"
    );
    // And colour is on when the caller asks for a frame and NO_COLOR is unset.
    let o = Command::new(bin())
        .args([
            "--headless",
            "--seed",
            "3",
            "--ticks",
            "20",
            "--no-save",
            "--print-frame",
        ])
        .env_remove("NO_COLOR")
        .current_dir(tool())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .expect("run");
    assert!(o.status.success());
    // The headless frame is plain text: there is no terminal to colour.
    assert!(!stdout_of(&o).contains('\x1b'));
}

#[test]
fn the_frame_is_the_reference_window_and_degrades_in_a_small_one() {
    let o = run(&[
        "--headless",
        "--seed",
        "9",
        "--ticks",
        "20",
        "--no-save",
        "--print-frame",
    ]);
    assert!(o.status.success());
    let t = stdout_of(&o);
    let frame = frame_of(&t);
    let lines: Vec<&str> = frame.lines().collect();
    assert!(
        lines.len() >= 23 && lines.len() <= 24,
        "the reference frame is the 24-row window, got {}",
        lines.len()
    );
    assert!(frame.contains("score"), "80x24 carries the score panel");
    assert!(frame.contains('@'));

    let o = run(&[
        "--headless",
        "--seed",
        "9",
        "--ticks",
        "20",
        "--no-save",
        "--print-frame",
        "--width",
        "40",
        "--height",
        "12",
    ]);
    assert!(o.status.success(), "a 40x12 window is playable, not fatal");
    let t = stdout_of(&o);
    let frame = frame_of(&t);
    assert_eq!(frame.lines().count(), 12);
    assert!(!frame.contains("score"), "the panel was dropped");
    assert!(frame.contains('@'), "the map was not");
}

#[test]
fn bad_flags_and_bad_files_are_clean_exits() {
    let o = run(&["--no-such-flag"]);
    assert_eq!(o.status.code(), Some(2), "an unknown flag is exit 2");
    let e = String::from_utf8_lossy(&o.stderr);
    assert!(e.contains("unknown flag"), "{e}");
    assert!(e.contains("--help"), "and it points at --help");

    let o = run(&["--seed"]);
    assert_eq!(o.status.code(), Some(2), "a flag with no value is exit 2");

    let o = run(&["--seed", "not-a-number"]);
    assert_eq!(o.status.code(), Some(2));

    let o = run(&["--ticks", "0"]);
    assert_eq!(o.status.code(), Some(2), "zero ticks is not a session");

    let o = run(&["--width", "0"]);
    assert_eq!(o.status.code(), Some(2));

    let o = run(&["--replay", "tools/does-not-exist.replay", "--no-save"]);
    assert_eq!(o.status.code(), Some(66), "a missing replay file is exit 66");

    let dir = scratch("bad");
    let bad = dir.join("bad.state");
    fs::write(&bad, "floor=99\nmap=\n").unwrap();
    let o = run(&["--state", bad.to_str().unwrap(), "--headless", "--no-save"]);
    assert_eq!(o.status.code(), Some(66), "a broken position is exit 66");
    assert!(!String::from_utf8_lossy(&o.stderr).contains("panicked"));
}

#[test]
fn the_record_lives_only_in_the_data_dir() {
    let dir = scratch("record-dir");
    let o = run(&[
        "--headless",
        "--seed",
        "4",
        "--ticks",
        "30",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert!(o.status.success());
    let best = dir.join("best.txt");
    assert!(best.is_file(), "the record was written under the data dir");
    let text = fs::read_to_string(&best).unwrap();
    assert!(text.contains("games=1"), "{text}");
    assert!(text.contains("score="));
    assert!(text.contains("pengy-rogue"));

    // A second run adds to it, and the frame reports it.
    let o = run(&[
        "--headless",
        "--seed",
        "4",
        "--ticks",
        "30",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert!(o.status.success());
    let text = fs::read_to_string(&best).unwrap();
    assert!(text.contains("games=2"), "the record accumulates: {text}");
    assert!(stdout_of(&o).contains("best_games=2"));

    // --no-save touches nothing.
    let dir = scratch("no-save");
    let o = run(&[
        "--headless",
        "--seed",
        "4",
        "--ticks",
        "30",
        "--no-save",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert!(o.status.success());
    assert!(!dir.join("best.txt").exists(), "--no-save writes nothing");
}

#[test]
fn the_default_data_dir_is_the_pengyos_games_path() {
    // With XDG_DATA_HOME pointed at a scratch dir, the game must create
    // pengyos/games/pengy-rogue under it — Tier-1 mutable state, under $HOME.
    let dir = scratch("xdg");
    let o = Command::new(bin())
        .args(["--headless", "--seed", "6", "--ticks", "20"])
        .env("XDG_DATA_HOME", &dir)
        .env("HOME", &dir)
        .current_dir(tool())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .expect("run");
    assert!(o.status.success());
    let expected = Path::new(&dir)
        .join("pengyos")
        .join("games")
        .join("pengy-rogue")
        .join("best.txt");
    assert!(expected.is_file(), "the record is at {expected:?}");
}

#[test]
fn a_quit_key_ends_the_session_cleanly() {
    // The scripted quit path: a replay that presses q stops there, prints the
    // state, and exits 0.
    let dir = scratch("quit");
    let script = dir.join("quit.replay");
    fs::write(
        &script,
        "# quit\nj\nj\nq\nj\nj\n",
    )
    .unwrap();
    let o = run(&[
        "--replay",
        script.to_str().unwrap(),
        "--seed",
        "1",
        "--no-save",
    ]);
    assert!(o.status.success());
    let t = stdout_of(&o);
    assert_eq!(get(&t, "quit"), "1", "the quit key was seen");
    assert_eq!(get(&t, "stopped_at"), "4", "and the session stopped on that line");
    assert_eq!(get(&t, "turns"), "2", "the keys after q were never played");
}


