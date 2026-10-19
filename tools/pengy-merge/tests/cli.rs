//! End-to-end tests: the real binary, driven the way the gate and
//! `pengyos-qemu-verify` drive it — no pty, no interaction, exit codes and
//! stdout asserted.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};

const BIN: &str = env!("CARGO_BIN_EXE_pengy-merge");

fn out(args: &[&str]) -> Output {
    Command::new(BIN)
        .args(args)
        // No pty, ever: stdin is /dev/null, so the binary must take its
        // headless path. This is the shape of the repo gate's environment.
        .stdin(Stdio::null())
        .output()
        .expect("run pengy-merge")
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
        "pengy-merge-test-{}-{}-{}",
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

fn fixture() -> String {
    format!(
        "{}/fixtures/win.replay",
        env!("CARGO_MANIFEST_DIR")
    )
}

// ---------------------------------------------------------------------------
// --help / --version
// ---------------------------------------------------------------------------

#[test]
fn help_lists_every_flag_and_key_and_exits_zero() {
    let text = stdout_of(&["--help"]);
    for flag in [
        "--seed",
        "--size",
        "--goal",
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
    for control in ["arrows", "Ctrl+Z", "Ctrl+N", "Ctrl+Q"] {
        assert!(text.contains(control), "--help lists the control {control}");
    }
    assert!(text.contains("2048"), "--help says what the game is");
}

#[test]
fn version_prints_the_crate_version() {
    let text = stdout_of(&["--version"]);
    assert!(text.starts_with("pengy-merge "), "{text}");
    assert!(text.contains(env!("CARGO_PKG_VERSION")));
}

// ---------------------------------------------------------------------------
// The headless contract: no pty, plain text, exit 0
// ---------------------------------------------------------------------------

#[test]
fn headless_needs_no_terminal_and_prints_plain_state() {
    let text = stdout_of(&["--headless", "--seed", "7", "--ticks", "600", "--no-save"]);
    assert!(!text.contains('\x1b'), "headless emits no escape bytes");
    assert_eq!(get(&text, "state"), "over");
    assert_eq!(get(&text, "seed"), "7");
    assert_eq!(get(&text, "size"), "4");
    assert_eq!(get(&text, "goal"), "2048");
    assert!(get(&text, "score").parse::<i64>().unwrap() > 0);
    assert!(get(&text, "max").parse::<u32>().unwrap() >= 128);
    assert!(text.contains("grid:"));
}

#[test]
fn with_no_harness_flag_it_opens_the_window_and_a_build_without_one_says_so() {
    // No --headless and no --replay means the window. The suite builds without
    // the `cosmic` feature, so the binary must say how to get one and exit 2,
    // never fall into a scripted run nobody asked for.
    let o = Command::new(env!("CARGO_BIN_EXE_pengy-merge"))
        .args(["--seed", "3", "--no-save"])
        .stdin(Stdio::null())
        .output()
        .expect("the binary runs");
    assert_eq!(o.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&o.stderr).contains("--features cosmic"), "it says how to build the window");
    assert!(o.stdout.is_empty(), "and it plays nothing");
}

#[test]
fn headless_is_bit_for_bit_reproducible() {
    let a = stdout_of(&["--headless", "--seed", "7", "--ticks", "600", "--no-save"]);
    let b = stdout_of(&["--headless", "--seed", "7", "--ticks", "600", "--no-save"]);
    assert_eq!(a, b);
    let c = stdout_of(&["--headless", "--seed", "8", "--ticks", "600", "--no-save"]);
    assert_ne!(a, c, "a different seed is a different run");
}

#[test]
fn a_custom_size_and_goal_play_headlessly() {
    let text = stdout_of(&[
        "--headless",
        "--seed",
        "5",
        "--size",
        "5",
        "--goal",
        "128",
        "--ticks",
        "900",
        "--no-save",
    ]);
    assert_eq!(get(&text, "size"), "5");
    assert_eq!(get(&text, "goal"), "128");
    assert_eq!(get(&text, "state"), "won", "the bot reaches 128 on 5x5");
    assert_eq!(get(&text, "cause"), "goal");
    assert!(get(&text, "max").parse::<u32>().unwrap() >= 128);
}

// ---------------------------------------------------------------------------
// The replay harness
// ---------------------------------------------------------------------------

#[test]
fn the_committed_fixture_replays_to_a_winning_score() {
    let f = fixture();
    let text = stdout_of(&["--replay", &f, "--seed", "7", "--no-save"]);
    assert_eq!(get(&text, "state"), "over");
    assert_eq!(get(&text, "max"), "512", "the fixture reaches 512");
    assert!(get(&text, "score").parse::<i64>().unwrap() >= 2000);
    assert_eq!(get(&text, "hash"), "9e91c46c6aafb114");
    assert_eq!(get(&text, "quit"), "0");
    assert_eq!(get(&text, "replay"), f);
    assert_eq!(get(&text, "stopped_at"), "eof");
}

#[test]
fn replaying_twice_is_byte_identical() {
    let f = fixture();
    let a = stdout_of(&["--replay", &f, "--seed", "7", "--no-save"]);
    let b = stdout_of(&["--replay", &f, "--seed", "7", "--no-save"]);
    assert_eq!(a, b);
}

#[test]
fn record_then_replay_round_trips() {
    let dir = tmpdir("record");
    let path = dir.join("session.replay");
    let p = path.to_string_lossy().to_string();
    let rec = stdout_of(&[
        "--headless",
        "--seed",
        "21",
        "--ticks",
        "4000",
        "--no-save",
        "--record",
        &p,
    ]);
    assert!(rec.contains("recorded="), "{rec}");
    let script = fs::read_to_string(&path).expect("recorded script");
    assert!(script.lines().next().unwrap().starts_with('#'));
    let ticks = script.lines().filter(|l| !l.starts_with('#')).count();
    assert!(ticks > 100, "a whole session ({ticks} ticks)");

    let a = stdout_of(&["--replay", &p, "--seed", "21", "--no-save"]);
    let b = stdout_of(&["--replay", &p, "--seed", "21", "--no-save"]);
    assert_eq!(a, b, "the recorded session replays deterministically");
    assert_eq!(get(&a, "seed"), "21");
    assert_eq!(get(&a, "state"), "over");
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn a_replay_line_per_tick_accepts_blank_lines_and_comments() {
    let dir = tmpdir("script");
    let path = dir.join("scripted.replay");
    // A hand-written session: three slides, a blank tick, a comment, an undo.
    fs::write(
        &path,
        "# hand-written session\nh\n\nj\n# a comment is not a tick\nl\nh\nu\n",
    )
    .expect("write script");
    let p = path.to_string_lossy().to_string();
    let text = stdout_of(&["--replay", &p, "--seed", "4", "--no-save"]);
    assert_eq!(get(&text, "state"), "playing");
    assert_eq!(get(&text, "moves"), "3", "four slides moved, the undo took one back");
    assert_eq!(get(&text, "undos"), "1");
    assert_eq!(get(&text, "stopped_at"), "eof");
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn a_missing_replay_file_is_exit_66() {
    let o = out(&["--replay", "/nonexistent/pengy-merge.replay", "--no-save"]);
    assert_eq!(o.status.code(), Some(66));
    assert!(
        String::from_utf8_lossy(&o.stderr).contains("cannot read replay file"),
        "one diagnostic on stderr"
    );
}

// ---------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------

#[test]
fn no_color_emits_no_escape_bytes() {
    let f = fixture();
    let o = Command::new(BIN)
        .args(["--replay", &f, "--seed", "7", "--no-save", "--print-frame"])
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .output()
        .expect("run");
    assert!(o.status.success());
    let text = String::from_utf8_lossy(&o.stdout).to_string();
    assert!(!text.contains('\x1b'), "NO_COLOR means no escapes at all");
    assert!(text.contains("ICEFLOE 2048"));
}

#[test]
fn mono_is_ascii_only() {
    let f = fixture();
    let text = stdout_of(&[
        "--replay",
        &f,
        "--seed",
        "7",
        "--no-save",
        "--print-frame",
        "--mono",
    ]);
    assert!(
        text.is_ascii(),
        "--mono must be ASCII-only: {:?}",
        text.chars().filter(|c| !c.is_ascii()).collect::<String>()
    );
    assert!(text.contains("+-----+"), "the mono box is ASCII");
}

#[test]
fn colour_is_on_by_default_and_the_frame_is_the_window() {
    let f = fixture();
    let text = stdout_of(&[
        "--replay",
        &f,
        "--seed",
        "7",
        "--no-save",
        "--print-frame",
        "--width",
        "80",
        "--height",
        "24",
    ]);
    assert!(text.contains("\x1b["), "colour is on by default");
    let frame: Vec<&str> = text.split("stopped_at=eof\n").last().unwrap().split('\n').collect();
    assert!(frame.len() >= 24, "the frame is 24 rows: {}", frame.len());
    for row in frame.iter().take(24) {
        // Visible width: strip the escapes, then measure.
        let visible: String = {
            let mut s = String::new();
            let mut it = row.chars().peekable();
            while let Some(c) = it.next() {
                if c != '\x1b' {
                    s.push(c);
                    continue;
                }
                if it.peek() == Some(&'[') {
                    it.next();
                    for c in it.by_ref() {
                        if (c >= '@' && c <= '~') || c == 'm' {
                            break;
                        }
                    }
                }
            }
            s
        };
        assert_eq!(visible.chars().count(), 80, "row {row:?} is not 80 wide");
    }
}

#[test]
fn a_tiny_window_still_paints_without_wrapping() {
    let f = fixture();
    let text = stdout_of(&[
        "--replay",
        &f,
        "--seed",
        "7",
        "--no-save",
        "--print-frame",
        "--mono",
        "--width",
        "18",
        "--height",
        "6",
    ]);
    let frame = text.split("stopped_at=eof\n").last().unwrap();
    for row in frame.split('\n').take(6) {
        assert!(row.chars().count() <= 18, "row {row:?} overflows 18 columns");
    }
    assert!(frame.contains("2"), "the field is still drawn");
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

#[test]
fn the_high_score_lives_in_the_data_dir_and_nowhere_else() {
    let dir = tmpdir("data");
    let d = dir.to_string_lossy().to_string();
    let text = stdout_of(&["--headless", "--seed", "7", "--ticks", "600", "--data-dir", &d]);
    let score = get(&text, "score");
    let best_file = dir.join("best.txt");
    let body = fs::read_to_string(&best_file).expect("best.txt was written");
    assert!(body.contains(&format!("score={score}")), "{body}");
    assert!(body.contains("max=512"));
    // Nothing outside the data dir.
    let entries: Vec<String> = fs::read_dir(&dir)
        .expect("read dir")
        .flatten()
        .map(|e| e.file_name().to_string_lossy().to_string())
        .collect();
    assert_eq!(entries, vec!["best.txt".to_string()]);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn no_save_reads_and_writes_nothing() {
    let dir = tmpdir("nosave");
    let d = dir.to_string_lossy().to_string();
    let text = stdout_of(&[
        "--headless",
        "--seed",
        "7",
        "--ticks",
        "600",
        "--data-dir",
        &d,
        "--no-save",
    ]);
    assert!(fs::read_dir(&dir).expect("dir exists").next().is_none());
    assert_eq!(get(&text, "best"), "0", "--no-save reads nothing");
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn the_best_score_is_carried_into_the_next_run() {
    let dir = tmpdir("carry");
    let d = dir.to_string_lossy().to_string();
    let first = stdout_of(&["--headless", "--seed", "7", "--ticks", "600", "--data-dir", &d]);
    let best = get(&first, "score");
    let second = stdout_of(&[
        "--headless",
        "--seed",
        "2",
        "--ticks",
        "80",
        "--data-dir",
        &d,
    ]);
    assert_eq!(get(&second, "best"), best, "the record is read back");
    let _ = fs::remove_dir_all(&dir);
}

// ---------------------------------------------------------------------------
// Usage errors
// ---------------------------------------------------------------------------

#[test]
fn an_unknown_flag_is_exit_2_with_one_line() {
    let o = out(&["--nope"]);
    assert_eq!(o.status.code(), Some(2));
    let err = String::from_utf8_lossy(&o.stderr).to_string();
    assert!(err.contains("unknown flag: --nope"), "{err}");
    assert_eq!(err.lines().count(), 2, "one diagnostic plus the pointer");
}

#[test]
fn bad_numbers_sizes_and_goals_are_exit_2() {
    for args in [
        vec!["--seed", "x"],
        vec!["--size", "2"],
        vec!["--size", "9"],
        vec!["--goal", "3"],
        vec!["--goal", "300"],
        vec!["--width", "0"],
        vec!["--ticks"],
        vec!["--record", "x.replay"],
        vec!["--print-frame"],
        vec!["--record", "x.replay", "--headless"],
    ] {
        let mut a = args.clone();
        a.insert(0, "--no-save");
        let o = out(&a);
        assert_eq!(o.status.code(), Some(2), "{args:?} must be a usage error");
    }
}

#[test]
fn record_and_replay_are_mutually_exclusive() {
    let f = fixture();
    let o = out(&["--headless", "--record", "/tmp/x.replay", "--replay", &f]);
    assert_eq!(o.status.code(), Some(2));
}
