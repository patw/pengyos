//! The real binary, run with no pty — the way the repo gate and
//! `pengyos-qemu-verify` run it. Every test here spawns the built binary with
//! piped (non-tty) stdin and asserts on what it prints and its exit code.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_pengy-blocks")
}

fn tool() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// Run the game with piped stdin (never a tty) and the given flags.
fn run(args: &[&str]) -> Output {
    Command::new(bin())
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn")
        .wait_with_output()
        .expect("wait")
}

fn stdout(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).to_string()
}
fn stderr(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).to_string()
}
fn code(o: &Output) -> i32 {
    o.status.code().unwrap_or(-1)
}

fn value(text: &str, key: &str) -> String {
    for line in text.lines() {
        if let Some(v) = line.strip_prefix(&format!("{key}=")) {
            return v.trim().to_string();
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// --help and --version
// ---------------------------------------------------------------------------

#[test]
fn help_lists_every_flag_and_every_key() {
    let o = run(&["--help"]);
    assert_eq!(code(&o), 0);
    let text = stdout(&o);
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
        assert!(text.contains(flag), "--help is missing {flag}");
    }
    for control in ["left / right", "space", "Hold", "Esc / P", "Ctrl+N", "Ctrl+Q"] {
        assert!(text.contains(control), "--help lists the control {control}");
    }
    assert!(text.contains("800"), "the gravity ladder is quoted");
    assert!(text.contains("300 ms"), "and the lock delay");
}

#[test]
fn version_prints_the_crate_version() {
    let o = run(&["--version"]);
    assert_eq!(code(&o), 0);
    assert!(stdout(&o).starts_with("pengy-blocks 0.1.0"));
}

// ---------------------------------------------------------------------------
// The headless path: no pty, no rendering, exit 0
// ---------------------------------------------------------------------------

#[test]
fn with_no_harness_flag_it_opens_the_window_and_a_build_without_one_says_so() {
    // No --headless and no --replay means the window. The suite builds without
    // the `cosmic` feature, so the binary must say how to get one and exit 2,
    // never fall into a scripted run nobody asked for.
    let o = Command::new(env!("CARGO_BIN_EXE_pengy-blocks"))
        .args(["--seed", "3", "--no-save"])
        .stdin(Stdio::null())
        .output()
        .expect("the binary runs");
    assert_eq!(o.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&o.stderr).contains("--features cosmic"), "it says how to build the window");
    assert!(o.stdout.is_empty(), "and it plays nothing");
}


#[test]
fn the_headless_run_is_reproducible_and_seed_dependent() {
    let a = stdout(&run(&["--headless", "--seed", "1234", "--no-save"]));
    let b = stdout(&run(&["--headless", "--seed", "1234", "--no-save"]));
    assert_eq!(a, b, "bit for bit");
    let c = stdout(&run(&["--headless", "--seed", "1235", "--no-save"]));
    assert_ne!(value(&a, "hash"), value(&c, "hash"));
}

#[test]
fn print_frame_draws_the_final_frame_as_plain_text() {
    let o = run(&[
        "--headless",
        "--seed",
        "7",
        "--no-save",
        "--print-frame",
        "--width",
        "80",
        "--height",
        "24",
    ]);
    assert_eq!(code(&o), 0);
    let text = stdout(&o);
    assert!(text.contains("well:"));
    assert!(text.contains("GLACIER BLOCKS"));
    assert!(!text.contains('\x1b'), "the plain frame carries no escapes");
}

// ---------------------------------------------------------------------------
// The replay harness
// ---------------------------------------------------------------------------

#[test]
fn the_committed_fixture_replays_to_its_pinned_result() {
    let fixture = tool().join("fixtures").join("win.replay");
    let o = run(&[
        "--replay",
        fixture.to_str().unwrap(),
        "--seed",
        "42",
        "--no-save",
    ]);
    assert_eq!(code(&o), 0, "the fixture must replay: {}", stderr(&o));
    let text = stdout(&o);
    assert_eq!(value(&text, "state"), "over");
    assert_eq!(value(&text, "score"), "174528");
    assert_eq!(value(&text, "lines"), "168");
    assert_eq!(value(&text, "level"), "17");
    assert_eq!(value(&text, "pieces"), "464");
    assert_eq!(value(&text, "hash"), "53cbffd1a21e3566");
    assert_eq!(value(&text, "quit"), "0");
    assert_eq!(value(&text, "cause"), "top-out");
}

#[test]
fn a_recorded_session_replays_to_the_same_state() {
    let dir = std::env::temp_dir().join("pengy-blocks-record");
    let _ = fs::remove_dir_all(&dir);
    let file = dir.join("s.replay");
    let rec = run(&[
        "--headless",
        "--seed",
        "5",
        "--no-save",
        "--record",
        file.to_str().unwrap(),
    ]);
    assert_eq!(code(&rec), 0);
    let plain = stdout(&rec);
    let rep = run(&[
        "--replay",
        file.to_str().unwrap(),
        "--seed",
        "5",
        "--no-save",
    ]);
    assert_eq!(code(&rep), 0);
    let text = stdout(&rep);
    assert_eq!(value(&text, "hash"), value(&plain, "hash"));
    assert_eq!(value(&text, "score"), value(&plain, "score"));
    assert_eq!(value(&text, "ticks"), value(&plain, "ticks"));
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn a_hand_written_script_is_played_tick_by_tick() {
    let dir = std::env::temp_dir().join("pengy-blocks-script");
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let file = dir.join("hand.replay");
    // 40 ticks: slide left, rotate, soft drop, hard drop. No comments.
    let script = "h\nh\nx\nj\nj\n_\n\n\nh\n_\n";
    fs::write(&file, script).unwrap();
    let o = run(&[
        "--replay",
        file.to_str().unwrap(),
        "--seed",
        "1",
        "--no-save",
    ]);
    assert_eq!(code(&o), 0);
    let text = stdout(&o);
    assert_eq!(value(&text, "ticks"), "10", "one line is one tick");
    assert_eq!(value(&text, "pieces"), "3", "the two hard drops locked two pieces");
    assert_eq!(value(&text, "seed"), "1");
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn a_missing_replay_file_is_exit_66() {
    let o = run(&["--replay", "/nonexistent/pengy-blocks.replay", "--no-save"]);
    assert_eq!(code(&o), 66);
    assert!(stderr(&o).contains("cannot read replay file"));
}

// ---------------------------------------------------------------------------
// Presentation flags
// ---------------------------------------------------------------------------

#[test]
fn no_colour_and_mono_are_honoured_in_the_frame() {
    let frame = |flags: &[&str]| {
        run(&[
            "--headless",
            "--seed",
            "7",
            "--no-save",
            "--print-frame",
            "--width",
            "80",
            "--height",
            "24",
        ])
    };
    let colour = stdout(&frame(&[]));
    assert!(colour.contains("GLACIER BLOCKS"));
    let mono = run(&[
        "--headless",
        "--seed",
        "7",
        "--no-save",
        "--print-frame",
        "--mono",
        "--width",
        "80",
        "--height",
        "24",
    ]);
    let text = stdout(&mono);
    assert!(!text.contains('│') && !text.contains('█'), "--mono is ASCII");
    assert!(text.contains('+') && text.contains('-'));
    assert!(text.contains("[]"), "--mono fills cells with []");
    assert!(colour.contains("[]"), "the headless frame is plain text: [] cells");
}

#[test]
fn no_colour_is_honoured_in_the_frame_too() {
    // NO_COLOR is an environment contract, and the frame is the only place
    // colour appears in a headless run.
    let o = Command::new(bin())
        .args([
            "--headless",
            "--seed",
            "7",
            "--no-save",
            "--print-frame",
            "--no-color",
        ])
        .env("NO_COLOR", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap()
        .wait_with_output()
        .unwrap();
    assert_eq!(code(&o), 0);
    assert!(!stdout(&o).contains('\x1b'));
}

#[test]
fn a_small_window_degrades_without_crashing() {
    for (w, h) in [("1", "1"), ("20", "10"), ("40", "14"), ("80", "24")] {
        let o = run(&[
            "--headless",
            "--seed",
            "7",
            "--no-save",
            "--print-frame",
            "--width",
            w,
            "--height",
            h,
            "--mono",
        ]);
        assert_eq!(code(&o), 0, "{w}x{h}");
        assert!(stdout(&o).contains("well:"));
    }
}

// ---------------------------------------------------------------------------
// Usage errors
// ---------------------------------------------------------------------------

#[test]
fn an_unknown_flag_is_a_usage_error_with_exit_2() {
    let o = run(&["--nonsense"]);
    assert_eq!(code(&o), 2);
    assert!(stderr(&o).contains("unknown flag"));
    assert!(stderr(&o).contains("--help"));
}

#[test]
fn a_flag_that_needs_a_value_says_so() {
    for flag in ["--seed", "--ticks", "--width", "--data-dir", "--replay"] {
        let o = run(&[flag]);
        assert_eq!(code(&o), 2, "{flag} with no value");
        assert!(stderr(&o).contains(flag));
    }
}

#[test]
fn a_bad_number_is_a_usage_error() {
    let o = run(&["--seed", "bananas", "--no-save"]);
    assert_eq!(code(&o), 2);
    assert!(stderr(&o).contains("not a number"));
}

#[test]
fn record_needs_headless_and_a_pinned_seed() {
    let o = run(&["--record", "/tmp/x.replay"]);
    assert_eq!(code(&o), 2);
    assert!(stderr(&o).contains("--headless"));
    let o = run(&["--headless", "--record", "/tmp/x.replay"]);
    assert_eq!(code(&o), 2);
    assert!(stderr(&o).contains("--seed"));
    let o = run(&[
        "--headless",
        "--seed",
        "1",
        "--record",
        "/tmp/x.replay",
        "--replay",
        "/tmp/y.replay",
        "--no-save",
    ]);
    assert_eq!(code(&o), 2, "--record and --replay are exclusive");
}

// ---------------------------------------------------------------------------
// Persistence: the one place the game is allowed to write
// ---------------------------------------------------------------------------

#[test]
fn the_best_score_lives_in_the_data_dir_and_nowhere_else() {
    let dir = std::env::temp_dir().join("pengy-blocks-data");
    let _ = fs::remove_dir_all(&dir);
    let o = run(&[
        "--headless",
        "--seed",
        "7",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert_eq!(code(&o), 0);
    let best = dir.join("best.txt");
    assert!(best.is_file(), "the record was written");
    let text = fs::read_to_string(&best).unwrap();
    assert!(text.contains("score="), "score is persisted");
    assert!(text.contains("lines="), "and lines");
    assert!(text.contains("level="), "and the level");
    assert!(text.contains("games="), "and the games played");
    // The next run reads it back.
    let again = run(&["--headless", "--seed", "7", "--data-dir", dir.to_str().unwrap()]);
    let text = stdout(&again);
    assert!(value(&text, "best_score").parse::<u64>().unwrap() > 0);
    assert_eq!(value(&text, "best_games"), "2");
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn the_default_data_dir_is_under_the_home_xdg_convention() {
    // Run with HOME pointed at a scratch directory and no XDG_DATA_HOME: the
    // file must land in $HOME/.local/share/pengyos/games/pengy-blocks.
    let home = std::env::temp_dir().join("pengy-blocks-home");
    let _ = fs::remove_dir_all(&home);
    fs::create_dir_all(&home).unwrap();
    let o = Command::new(bin())
        .args(["--headless", "--seed", "3"])
        .env("HOME", &home)
        .env_remove("XDG_DATA_HOME")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap()
        .wait_with_output()
        .unwrap();
    assert_eq!(code(&o), 0);
    let expected = home
        .join(".local/share/pengyos/games/pengy-blocks")
        .join("best.txt");
    assert!(expected.is_file(), "the record is under $HOME/.local/share");
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn xdg_data_home_is_honoured() {
    let base = std::env::temp_dir().join("pengy-blocks-xdg");
    let _ = fs::remove_dir_all(&base);
    fs::create_dir_all(&base).unwrap();
    let o = Command::new(bin())
        .args(["--headless", "--seed", "3"])
        .env("XDG_DATA_HOME", &base)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap()
        .wait_with_output()
        .unwrap();
    assert_eq!(code(&o), 0);
    assert!(base
        .join("pengyos/games/pengy-blocks/best.txt")
        .is_file());
    let _ = fs::remove_dir_all(&base);
}

#[test]
fn a_run_never_leaves_a_process_behind() {
    // The headless path is a straight line to exit; if the tick loop leaked a
    // thread the process would not exit at all, and this call would time out.
    let o = run(&["--headless", "--seed", "1", "--ticks", "50", "--no-save"]);
    assert_eq!(code(&o), 0);
    assert_eq!(value(&stdout(&o), "ticks"), "50");
}
