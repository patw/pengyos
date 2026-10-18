//! End-to-end tests: the real binary, driven the way the gate and
//! `pengyos-qemu-verify` drive it — no pty, no interaction, exit codes and
//! stdout asserted.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};

const BIN: &str = env!("CARGO_BIN_EXE_pengy-floe");

fn out(args: &[&str]) -> Output {
    Command::new(BIN)
        .args(args)
        // No pty, ever: stdin is /dev/null, so the binary must take its
        // headless path. This is the shape of the repo gate's environment.
        .stdin(Stdio::null())
        .output()
        .expect("run pengy-floe")
}

fn stdout_of(args: &[&str]) -> String {
    let o = out(args);
    assert!(
        o.status.success(),
        "exit {:?} for {args:?}: {}",
        o.status.code(),
        String::from_utf8_lossy(&o.stderr)
    );
    String::from_utf8_lossy(&o.stdout).to_string()
}

fn tmpdir(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "pengy-floe-test-{}-{}-{}",
        tag,
        std::process::id(),
        std::time::UNIX_EPOCH
            .elapsed()
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("temp dir");
    dir
}

fn kv(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

fn get(text: &str, key: &str) -> String {
    kv(text)
        .into_iter()
        .find(|(k, _)| k == key)
        .map(|(_, v)| v)
        .unwrap_or_else(|| panic!("no {key}= in output: {text}"))
}

// ---------------------------------------------------------------------------
// --help / --version
// ---------------------------------------------------------------------------

#[test]
fn help_lists_every_flag_and_key_and_exits_zero() {
    let text = stdout_of(&["--help"]);
    for flag in [
        "--seed",
        "--headless",
        "--replay",
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
        assert!(text.contains(flag), "--help must list {flag}");
    }
    for key in ["space", "up", "w", "p", "r", "q"] {
        assert!(text.contains(key), "--help must list the {key} key");
    }
    assert!(text.contains("Ctrl-C"));
}

#[test]
fn version_prints_the_crate_version() {
    let text = stdout_of(&["--version"]);
    assert_eq!(text.trim(), format!("pengy-floe {}", env!("CARGO_PKG_VERSION")));
}

// ---------------------------------------------------------------------------
// Headless: the no-pty path
// ---------------------------------------------------------------------------

#[test]
fn headless_runs_without_a_tty_and_prints_plain_state() {
    let text = stdout_of(&["--headless", "--seed", "2", "--ticks", "200", "--no-save"]);
    assert!(!text.contains('\x1b'), "headless emits no ANSI");
    for key in ["state", "seed", "ticks", "score", "best", "hash"] {
        assert!(text.contains(&format!("{key}=")), "missing {key}=");
    }
    assert_eq!(get(&text, "seed"), "2");
    assert_eq!(get(&text, "state"), "playing", "200 ticks is mid-run");
    assert_eq!(get(&text, "ticks"), "200");
    assert_eq!(get(&text, "score"), "7", "the same seed, the same score");
    assert_eq!(get(&text, "hash"), "7a4dc29c40eaf2b7");
}

#[test]
fn a_piped_stdin_takes_the_headless_path() {
    // No --headless flag at all: stdin is not a tty, so the game must not try
    // to grab the terminal. Exit 0 is the whole point.
    let o = Command::new(BIN)
        .args(["--seed", "3", "--no-save"])
        .stdin(Stdio::null())
        .output()
        .expect("run");
    assert_eq!(o.status.code(), Some(0));
    assert!(String::from_utf8_lossy(&o.stdout).contains("score="));
    assert!(!String::from_utf8_lossy(&o.stdout).contains('\x1b'));
}

#[test]
fn headless_is_bit_for_bit_reproducible() {
    let a = stdout_of(&["--headless", "--seed", "2", "--ticks", "1039", "--no-save"]);
    let b = stdout_of(&["--headless", "--seed", "2", "--ticks", "1039", "--no-save"]);
    assert_eq!(a, b);
    let c = stdout_of(&["--headless", "--seed", "5", "--ticks", "1039", "--no-save"]);
    assert_ne!(get(&a, "hash"), get(&c, "hash"));
}

// ---------------------------------------------------------------------------
// --replay: the acceptance harness
// ---------------------------------------------------------------------------

#[test]
fn the_committed_fixture_wins_a_medal() {
    let text = stdout_of(&["--replay", "fixtures/win.replay", "--seed", "2", "--no-save"]);
    assert_eq!(get(&text, "state"), "dead");
    assert_eq!(get(&text, "score"), "70");
    assert_eq!(get(&text, "medal"), "silver");
    assert_eq!(get(&text, "cause"), "pillar");
    assert_eq!(get(&text, "quit"), "0");
    assert_eq!(get(&text, "hash"), "5270b19a8f034ede");
}

#[test]
fn replay_is_deterministic_and_matches_the_headless_run() {
    let r1 = stdout_of(&["--replay", "fixtures/win.replay", "--seed", "2", "--no-save"]);
    let r2 = stdout_of(&["--replay", "fixtures/win.replay", "--seed", "2", "--no-save"]);
    assert_eq!(r1, r2);
    let h = stdout_of(&["--headless", "--seed", "2", "--ticks", "4000", "--no-save"]);
    // The same seed and the same inputs land on the same state, whichever
    // path produced them.
    assert_eq!(get(&r1, "hash"), get(&h, "hash"));
    assert_eq!(get(&r1, "score"), get(&h, "score"));
}

#[test]
fn replay_of_a_missing_file_is_a_data_error() {
    let o = out(&["--replay", "fixtures/nope.replay", "--no-save"]);
    assert_eq!(o.status.code(), Some(66), "EX_NOINPUT");
    assert!(String::from_utf8_lossy(&o.stderr).contains("cannot read"));
}

#[test]
fn record_then_replay_round_trips() {
    let dir = tmpdir("record");
    let fixture = dir.join("round.replay");
    let rec = stdout_of(&[
        "--headless",
        "--seed",
        "13",
        "--ticks",
        "4000",
        "--no-save",
        "--record",
        fixture.to_str().unwrap(),
    ]);
    assert!(rec.contains("recorded="));
    let played = stdout_of(&[
        "--replay",
        fixture.to_str().unwrap(),
        "--seed",
        "13",
        "--no-save",
    ]);
    assert_eq!(get(&played, "score"), "62");
    assert_eq!(get(&played, "medal"), "silver");
    let _ = fs::remove_dir_all(&dir);
}

// ---------------------------------------------------------------------------
// Presentation flags
// ---------------------------------------------------------------------------

#[test]
fn no_color_is_respected() {
    let o = Command::new(BIN)
        .args(["--headless", "--seed", "2", "--ticks", "40", "--no-save", "--print-frame"])
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .output()
        .expect("run");
    let text = String::from_utf8_lossy(&o.stdout).to_string();
    assert!(!text.contains('\x1b'), "NO_COLOR must suppress every escape");
    assert!(text.contains("Flappy Pengy"));
}

#[test]
fn mono_is_ascii_only_and_color_is_on_by_default() {
    let mono = stdout_of(&[
        "--headless",
        "--seed",
        "2",
        "--ticks",
        "40",
        "--no-save",
        "--mono",
        "--print-frame",
    ]);
    assert!(mono.is_ascii(), "--mono is ASCII-only");
    assert!(mono.contains('#'));

    let colour = stdout_of(&[
        "--headless",
        "--seed",
        "2",
        "--ticks",
        "40",
        "--no-save",
        "--print-frame",
    ]);
    assert!(colour.contains("\x1b["), "colour is on by default");
}

#[test]
fn the_frame_fits_the_reference_window_and_a_smaller_one() {
    let full = stdout_of(&[
        "--headless",
        "--seed",
        "2",
        "--ticks",
        "40",
        "--no-save",
        "--mono",
        "--print-frame",
        "--width",
        "80",
        "--height",
        "24",
    ]);
    let rows: Vec<&str> = full.lines().filter(|l| !l.starts_with("state=") && l.contains('|')).collect();
    assert!(rows.iter().all(|l| l.chars().count() <= 80));

    let small = stdout_of(&[
        "--headless",
        "--seed",
        "2",
        "--ticks",
        "40",
        "--no-save",
        "--mono",
        "--print-frame",
        "--width",
        "30",
        "--height",
        "12",
    ]);
    let frame_rows: Vec<&str> = small
        .lines()
        .skip_while(|l| !l.starts_with('-') && !l.starts_with('+'))
        .collect();
    assert!(frame_rows.iter().all(|l| l.chars().count() <= 30));
    assert!(frame_rows.len() <= 12);
}

// ---------------------------------------------------------------------------
// Usage errors
// ---------------------------------------------------------------------------

#[test]
fn bad_flags_are_exit_2_with_one_diagnostic() {
    for args in [
        vec!["--nope"],
        vec!["--seed", "x"],
        vec!["--ticks"],
        vec!["--replay", "a.replay", "--record", "b.replay", "--seed", "1"],
        vec!["--record", "b.replay", "--headless"],
        vec!["--print-frame"],
        vec!["--width", "0", "--headless"],
    ] {
        let o = out(&args);
        assert_eq!(o.status.code(), Some(2), "usage error for {args:?}");
        let err = String::from_utf8_lossy(&o.stderr).to_string();
        assert!(err.starts_with("pengy-floe:"), "diagnostic for {args:?}: {err}");
        assert_eq!(err.lines().count(), 2, "one line + the --help hint");
    }
}

// ---------------------------------------------------------------------------
// High score persistence
// ---------------------------------------------------------------------------

#[test]
fn the_high_score_persists_in_the_data_dir_and_only_there() {
    let dir = tmpdir("best");
    let first = stdout_of(&[
        "--headless",
        "--seed",
        "2",
        "--ticks",
        "4000",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert_eq!(get(&first, "best"), "70");
    let file = dir.join("best.txt");
    let body = fs::read_to_string(&file).expect("best.txt written under the data dir");
    assert!(body.contains("score=70"), "body: {body}");
    assert!(body.contains("ticks=1039"));

    // A worse run keeps the record; the next run starts from it.
    let second = stdout_of(&[
        "--headless",
        "--seed",
        "7",
        "--ticks",
        "100",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert_eq!(get(&second, "best"), "70", "the persisted best survives");
    assert!(fs::read_to_string(&file)
        .unwrap()
        .contains("score=70"));

    // Nothing outside the data dir is touched.
    assert_eq!(fs::read_dir(&dir).unwrap().count(), 1);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn no_save_touches_no_files() {
    let dir = tmpdir("nosave");
    stdout_of(&[
        "--headless",
        "--seed",
        "2",
        "--ticks",
        "400",
        "--no-save",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert_eq!(fs::read_dir(&dir).unwrap().count(), 0);
    let _ = fs::remove_dir_all(&dir);
}
