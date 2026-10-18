//! End-to-end tests: the real binary, driven the way the gate and
//! `pengyos-qemu-verify` drive it — no pty, no interaction, exit codes and
//! stdout asserted.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};

const BIN: &str = env!("CARGO_BIN_EXE_pengy-lander");

fn out(args: &[&str]) -> Output {
    Command::new(BIN)
        .args(args)
        // No pty, ever: stdin is /dev/null, so the binary must take its
        // headless path. This is the shape of the repo gate's environment.
        .stdin(Stdio::null())
        .output()
        .expect("run pengy-lander")
}

fn out_with(args: &[&str], stdin: &[u8]) -> Output {
    use std::io::Write;
    let mut child = Command::new(BIN)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn pengy-lander");
    child
        .stdin
        .as_mut()
        .expect("stdin")
        .write_all(stdin)
        .expect("write stdin");
    child.wait_with_output().expect("wait")
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

fn code(args: &[&str]) -> Option<i32> {
    out(args).status.code()
}

fn tmpdir(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "pengy-lander-test-{}-{}-{}",
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

/// The last `n` lines of a text, in order, with the carriage returns the
/// frame uses stripped.
fn take_last(lines: std::str::Split<'_, char>, n: usize) -> Vec<String> {
    let v: Vec<String> = lines
        .map(|l| l.trim_end_matches('\r').to_string())
        .collect();
    let start = v.len().saturating_sub(n);
    v[start..].to_vec()
}

fn get(text: &str, key: &str) -> String {
    text.lines()
        .filter_map(|l| l.split_once('='))
        .find(|(k, _)| *k == key)
        .map(|(_, v)| v.trim().to_string())
        .unwrap_or_else(|| panic!("no {key}= in output:\n{text}"))
}

// ---------------------------------------------------------------------------
// --help / --version: the CLI contract
// ---------------------------------------------------------------------------

#[test]
fn help_lists_every_flag_and_every_key() {
    let h = stdout_of(&["--help"]);
    for flag in [
        "--seed",
        "--body",
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
        assert!(h.contains(flag), "--help does not list {flag}");
    }
    for key in [
        "space",
        "a / d",
        "n ",
        "r ",
        "q ",
        "Escape",
        "thrust",
        "rotate",
        "new terrain",
        "restart",
        "quit",
    ] {
        assert!(h.contains(key), "--help does not document {key:?}");
    }
    assert_eq!(code(&["--help"]), Some(0));
    assert_eq!(code(&["-h"]), Some(0));
}

#[test]
fn version_is_the_crate_version() {
    let v = stdout_of(&["--version"]);
    assert!(v.starts_with("pengy-lander 0.1.0"), "got {v}");
}

#[test]
fn a_bad_flag_is_a_usage_error_and_exits_2() {
    assert_eq!(code(&["--nope"]), Some(2));
    assert_eq!(code(&["--seed"]), Some(2));
    assert_eq!(code(&["--seed", "x"]), Some(2));
    assert_eq!(code(&["--body", "sun"]), Some(2));
    assert_eq!(code(&["--width", "0"]), Some(2));
    assert_eq!(code(&["--record", "/tmp/x", "--seed", "1"]), Some(2)); // no --headless
    assert_eq!(code(&["--record", "/tmp/x", "--headless"]), Some(2)); // no --seed
    assert_eq!(
        code(&["--replay", "f", "--record", "g", "--headless", "--seed", "1"]),
        Some(2)
    );
}

#[test]
fn an_unreadable_replay_exits_66() {
    assert_eq!(
        code(&["--replay", "/nonexistent/pengy-lander.replay", "--no-save"]),
        Some(66)
    );
}

// ---------------------------------------------------------------------------
// Headless: no pty, plain text, exit 0
// ---------------------------------------------------------------------------

#[test]
fn headless_needs_no_terminal_and_exits_0() {
    let o = out(&["--headless", "--seed", "20", "--ticks", "1200", "--no-save"]);
    assert_eq!(o.status.code(), Some(0));
    let s = String::from_utf8_lossy(&o.stdout).to_string();
    assert!(s.contains("state="), "no state line:\n{s}");
    assert!(s.contains("hash="));
    assert!(s.contains("field:"));
    assert!(
        !s.contains('\x1b'),
        "headless output must be plain text, escapes and all"
    );
    assert!(s.is_ascii());
}

#[test]
fn piped_stdin_takes_the_headless_path() {
    // The gate has no pty: a piped stdin must never touch the terminal.
    let o = out_with(&["--seed", "3", "--ticks", "200", "--no-save"], b"   \n  d\n");
    assert_eq!(o.status.code(), Some(0));
    let s = String::from_utf8_lossy(&o.stdout).to_string();
    assert!(s.contains("state="));
    assert!(!s.contains("\x1b[?1049h"), "it grabbed the alternate screen");
    assert!(s.is_ascii());
}

#[test]
fn headless_is_bit_for_bit_reproducible() {
    let a = stdout_of(&["--headless", "--seed", "20", "--ticks", "1500", "--no-save"]);
    let b = stdout_of(&["--headless", "--seed", "20", "--ticks", "1500", "--no-save"]);
    assert_eq!(a, b);
    let c = stdout_of(&["--headless", "--seed", "21", "--ticks", "1500", "--no-save"]);
    assert_ne!(a, c);
}

#[test]
fn the_body_flag_changes_the_run() {
    let moon = stdout_of(&["--headless", "--seed", "5", "--ticks", "600", "--no-save"]);
    let ast = stdout_of(&[
        "--headless",
        "--seed",
        "5",
        "--ticks",
        "600",
        "--body",
        "asteroid",
        "--no-save",
    ]);
    assert!(moon.contains("body=moon"));
    assert!(ast.contains("body=asteroid"));
    assert_ne!(get(&moon, "hash"), get(&ast, "hash"));
}

#[test]
fn no_color_and_mono_are_honoured() {
    let plain = stdout_of(&[
        "--headless",
        "--seed",
        "4",
        "--ticks",
        "300",
        "--print-frame",
        "--no-color",
        "--no-save",
    ]);
    assert!(!plain.contains('\x1b'), "--no-color emitted escapes");
    let mono = stdout_of(&[
        "--headless",
        "--seed",
        "4",
        "--ticks",
        "300",
        "--print-frame",
        "--mono",
        "--no-save",
    ]);
    assert!(
        mono.is_ascii(),
        "--mono must draw in ASCII only:\n{}",
        mono.chars()
            .filter(|c| !c.is_ascii())
            .take(6)
            .collect::<String>()
    );
    // NO_COLOR in the environment is honoured without a flag.
    let o = Command::new(BIN)
        .args([
            "--headless",
            "--seed",
            "4",
            "--ticks",
            "300",
            "--print-frame",
            "--no-save",
        ])
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .output()
        .expect("run");
    let s = String::from_utf8_lossy(&o.stdout).to_string();
    assert!(!s.contains('\x1b'), "NO_COLOR was ignored");
}

#[test]
fn the_frame_is_the_window_that_was_asked_for() {
    for (w, h) in [(80, 24), (60, 16), (40, 10)] {
        let s = stdout_of(&[
            "--headless",
            "--seed",
            "4",
            "--ticks",
            "300",
            "--print-frame",
            "--mono",
            "--width",
            &w.to_string(),
            "--height",
            &h.to_string(),
            "--no-save",
        ]);
        // --print-frame prints the numbers and then the screen: the frame is
        // the last `h` lines of the output.
        let rows: Vec<String> = take_last(s.split('\n'), h);
        assert_eq!(rows.len(), h, "{w}x{h} drew {} rows", rows.len());
        for row in &rows {
            assert_eq!(row.chars().count(), w, "{w}x{h}: row {:?} is the wrong width", row);
        }
    }
}

// ---------------------------------------------------------------------------
// --replay: the acceptance harness
// ---------------------------------------------------------------------------

#[test]
fn the_committed_fixture_replays_to_a_landed_run() {
    let s = stdout_of(&[
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "20",
        "--no-save",
    ]);
    assert_eq!(get(&s, "state"), "landed", "the fixture must end on a landing");
    assert_eq!(get(&s, "seed"), "20");
    assert_eq!(get(&s, "quit"), "0");
    assert!(get(&s, "score").parse::<u64>().unwrap() >= 1000);
    assert!(get(&s, "waves_landed").parse::<u64>().unwrap() >= 3);
    assert_eq!(get(&s, "hash"), "22392a0ed4250697", "the fixture's pinned hash");
    assert_eq!(get(&s, "stopped_at"), "eof");
}

#[test]
fn a_replay_is_deterministic_and_a_script_edit_changes_it() {
    let a = stdout_of(&["--replay", "fixtures/win.replay", "--seed", "20", "--no-save"]);
    let b = stdout_of(&["--replay", "fixtures/win.replay", "--seed", "20", "--no-save"]);
    assert_eq!(get(&a, "hash"), get(&b, "hash"));
    // A different seed is a different run: the seed is a harness argument.
    let c = stdout_of(&["--replay", "fixtures/win.replay", "--seed", "21", "--no-save"]);
    assert_ne!(get(&a, "hash"), get(&c, "hash"));
    assert_eq!(get(&c, "seed"), "21");
}

#[test]
fn record_then_replay_round_trips() {
    let dir = tmpdir("roundtrip");
    let f = dir.join("s.replay");
    let s = stdout_of(&[
        "--headless",
        "--seed",
        "12",
        "--ticks",
        "900",
        "--record",
        f.to_str().unwrap(),
        "--no-save",
    ]);
    assert!(s.contains("recorded="));
    let text = fs::read_to_string(&f).expect("fixture");
    let lines: Vec<&str> = text.lines().collect();
    assert!(lines[0].starts_with('#'));
    // Exactly one line per tick, no blank separator after the header.
    assert_eq!(lines.iter().filter(|l| !l.starts_with('#')).count(), 900);
    let r = stdout_of(&["--replay", f.to_str().unwrap(), "--seed", "12", "--no-save"]);
    let h = stdout_of(&["--headless", "--seed", "12", "--ticks", "900", "--no-save"]);
    assert_eq!(
        get(&r, "hash"),
        get(&h, "hash"),
        "record and replay disagree"
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn a_replay_line_is_one_tick_and_a_space_is_thrust() {
    let dir = tmpdir("lines");
    let f = dir.join("p.replay");
    // 40 ticks of thrust from the spawn: the probe must climb and burn fuel.
    let script = "# comment\n".to_string() + &" \n".repeat(40);
    fs::write(&f, script).expect("write");
    let s = stdout_of(&["--replay", f.to_str().unwrap(), "--seed", "8", "--no-save"]);
    assert_eq!(get(&s, "ticks"), "40", "a comment is not a tick");
    assert_eq!(get(&s, "fuel"), "98", "40 ticks of thrust is 40/30 units");
    assert_eq!(get(&s, "y"), "23", "and the probe climbed to the ceiling");
    // A blank line is no input.
    fs::write(&f, "\n\n\n").expect("write");
    let s = stdout_of(&["--replay", f.to_str().unwrap(), "--seed", "8", "--no-save"]);
    assert_eq!(get(&s, "ticks"), "3");
    assert_eq!(get(&s, "fuel"), "100");
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn a_replay_stops_at_the_crash_tick() {
    let dir = tmpdir("stop");
    let f = dir.join("c.replay");
    // Free fall from the spawn onto the ridge: thousands of lines, one crash.
    fs::write(&f, "\n".repeat(4000)).expect("write");
    let s = stdout_of(&["--replay", f.to_str().unwrap(), "--seed", "2", "--no-save"]);
    assert_eq!(get(&s, "state"), "crashed");
    let at: usize = get(&s, "stopped_at").parse().expect("stopped_at");
    let ticks: u64 = get(&s, "ticks").parse().unwrap();
    assert_eq!(at as u64, ticks + 1, "the harness ran past the crash tick");
    let _ = fs::remove_dir_all(&dir);
}

// ---------------------------------------------------------------------------
// The high score: Tier-1 mutable state, under $HOME only
// ---------------------------------------------------------------------------

#[test]
fn the_high_score_lives_in_the_data_dir_and_nowhere_else() {
    let dir = tmpdir("data");
    stdout_of(&[
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "20",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    let best = dir.join("best.txt");
    let text = fs::read_to_string(&best).expect("best.txt was not written");
    assert!(text.contains("score=8390"), "the score is not banked:\n{text}");
    assert!(text.contains("waves=6"));
    assert!(text.contains("seed=20"));
    assert!(text.contains("runs=1"));
    // Nothing is written outside the data directory.
    let entries: Vec<String> = fs::read_dir(&dir)
        .expect("read dir")
        .flatten()
        .map(|e| e.file_name().to_string_lossy().to_string())
        .collect();
    assert_eq!(entries, vec!["best.txt".to_string()]);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn the_high_score_is_read_back_and_kept_across_runs() {
    let dir = tmpdir("keep");
    let d = dir.to_str().unwrap();
    stdout_of(&["--replay", "fixtures/win.replay", "--seed", "20", "--data-dir", d]);
    let s = stdout_of(&[
        "--headless",
        "--seed",
        "1",
        "--ticks",
        "100",
        "--data-dir",
        d,
    ]);
    assert_eq!(get(&s, "best_score"), "8390", "the record was not read back");
    // A worse run must not lower it.
    let _s = stdout_of(&[
        "--headless",
        "--seed",
        "1",
        "--ticks",
        "100",
        "--data-dir",
        d,
    ]);
    let text = fs::read_to_string(dir.join("best.txt")).expect("best.txt");
    assert!(text.contains("score=8390"));
    assert!(text.contains("runs=3"));
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn no_save_touches_nothing() {
    let dir = tmpdir("nosave");
    stdout_of(&[
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "20",
        "--data-dir",
        dir.to_str().unwrap(),
        "--no-save",
    ]);
    assert!(
        fs::read_dir(&dir).map(|d| d.count()).unwrap_or(0) == 0,
        "--no-save wrote into the data dir"
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn the_default_data_dir_is_under_the_home_xdg_tree() {
    // HOME and XDG_DATA_HOME both point at a temp dir: the game must write
    // under it, and nowhere else.
    let dir = tmpdir("xdg");
    let o = Command::new(BIN)
        .args([
            "--headless",
            "--seed",
            "1",
            "--ticks",
            "60",
            "--data-dir",
            dir.join("explicit").to_str().unwrap(),
        ])
        .stdin(Stdio::null())
        .output()
        .expect("run");
    assert!(o.status.success());
    let _ = fs::remove_dir_all(&dir);

    let home = tmpdir("home");
    let o = Command::new(BIN)
        .args(["--headless", "--seed", "1", "--ticks", "60"])
        .env("HOME", &home)
        .env_remove("XDG_DATA_HOME")
        .stdin(Stdio::null())
        .output()
        .expect("run");
    assert!(o.status.success());
    let want = home.join(".local/share/pengyos/games/pengy-lander/best.txt");
    assert!(
        want.is_file(),
        "the high score is not at {}",
        want.display()
    );
    let xdg = tmpdir("xdg2");
    let o = Command::new(BIN)
        .args(["--headless", "--seed", "1", "--ticks", "60"])
        .env("XDG_DATA_HOME", &xdg)
        .stdin(Stdio::null())
        .output()
        .expect("run");
    assert!(o.status.success());
    assert!(
        xdg.join("pengyos/games/pengy-lander/best.txt").is_file(),
        "XDG_DATA_HOME was not honoured"
    );
    let _ = fs::remove_dir_all(&home);
    let _ = fs::remove_dir_all(&xdg);
}
