//! The front-end contract, driven the way a player and the gate drive it:
//! the built binary, with a pipe for stdin and a temp dir for the record.
//!
//! Every test here runs the real binary. None of them needs a pty — that is
//! the point of the headless path, and the reason the repo gate can prove the
//! game works.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

const BIN: &str = env!("CARGO_BIN_EXE_pengy-invaders");

/// A unique scratch directory per test, cleaned by the OS like anything in
/// the temp dir and never shared between tests.
fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "pengy-invaders-cli-{}-{}",
        tag,
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&dir);
    let _ = fs::create_dir_all(&dir);
    dir
}

/// Run the binary with no TTY anywhere: stdin is closed, stdout is captured.
fn run(args: &[&str]) -> Output {
    run_with(args, &[])
}

/// Run the binary with extra environment variables.
fn run_with(args: &[&str], envs: &[&str]) -> Output {
    let mut cmd = Command::new(BIN);
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for kv in envs {
        let (k, v) = kv.split_once('=').unwrap();
        cmd.env(k, v);
    }
    cmd.output().expect("the binary runs")
}

fn stdout(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).to_string()
}

fn kv_of(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))
        .collect()
}

fn get(kv: &[(String, String)], key: &str) -> String {
    kv.iter()
        .find(|(k, _)| k == key)
        .map(|(_, v)| v.clone())
        .unwrap_or_default()
}

fn fixture() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/win.replay")
}

// ---------------------------------------------------------------------------
// --help / --version
// ---------------------------------------------------------------------------

#[test]
fn help_lists_every_flag_and_every_key() {
    let out = run(&["--help"]);
    assert!(out.status.success(), "--help exits 0");
    let text = stdout(&out);
    for flag in [
        "--seed",
        "--headless",
        "--replay",
        "--record",
        "--ticks",
        "--wave",
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
        assert!(text.contains(flag), "--help lists {flag}");
    }
    for control in ["left / right", "space / up", "Esc / P", "Ctrl+N", "Ctrl+Q"] {
        assert!(text.contains(control), "--help lists the control {control}");
    }
    assert!(text.contains("60x24"), "--help states the field");
    assert!(text.contains("80"), "--help states the window");
}

#[test]
fn version_prints_the_crate_version() {
    let out = run(&["--version"]);
    assert!(out.status.success());
    assert_eq!(stdout(&out).trim(), "pengy-invaders 0.1.0");
}

#[test]
fn an_unknown_flag_is_a_usage_error() {
    let out = run(&["--nope"]);
    assert_eq!(out.status.code(), Some(2), "usage errors exit 2");
    assert!(stdout(&out).is_empty(), "a usage error prints no game output");
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("unknown flag"),
        "and says which flag"
    );
}

// ---------------------------------------------------------------------------
// The headless path: no TTY, plain text, exit 0
// ---------------------------------------------------------------------------

#[test]
fn headless_needs_no_terminal_and_exits_zero() {
    let dir = scratch("headless");
    let out = run(&[
        "--headless",
        "--seed",
        "5",
        "--ticks",
        "600",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert!(out.status.success(), "headless exits 0");
    let text = stdout(&out);
    assert!(!text.contains('\x1b'), "headless prints no escapes");
    assert!(text.contains("field:"), "headless prints the field");
    let kv = kv_of(&text);
    assert_eq!(get(&kv, "seed"), "5");
    assert!(!get(&kv, "hash").is_empty());
    assert!(get(&kv, "kills").parse::<u64>().unwrap() > 0, "the autopilot shoots");
}

#[test]
fn with_no_harness_flag_it_opens_the_window_and_a_build_without_one_says_so() {
    // No --headless and no --replay means the window. The suite builds without
    // the `cosmic` feature, so the binary must say how to get one and exit 2,
    // never fall into a scripted run nobody asked for.
    let o = Command::new(env!("CARGO_BIN_EXE_pengy-invaders"))
        .args(["--seed", "3", "--no-save"])
        .stdin(Stdio::null())
        .output()
        .expect("the binary runs");
    assert_eq!(o.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&o.stderr).contains("--features cosmic"), "it says how to build the window");
    assert!(o.stdout.is_empty(), "and it plays nothing");
}

#[test]
fn print_frame_draws_the_field_and_mono_is_ascii_only() {
    let out = run(&[
        "--headless", "--seed", "2", "--ticks", "120", "--no-save", "--print-frame", "--mono",
    ]);
    assert!(out.status.success());
    let text = stdout(&out);
    assert!(text.contains("score"), "the frame carries the score");
    assert!(
        text.chars().all(|c| c.is_ascii()),
        "--mono is ASCII-only, everywhere"
    );
    let colour = run(&[
        "--headless", "--seed", "2", "--ticks", "120", "--no-save", "--print-frame",
    ]);
    assert!(stdout(&colour).contains('\x1b'), "colour is on by default");
}

#[test]
fn no_color_is_honoured_without_a_flag() {
    let out = run_with(
        &[
            "--headless", "--seed", "2", "--ticks", "120", "--no-save", "--print-frame",
        ],
        &["NO_COLOR=1"],
    );
    assert!(out.status.success());
    assert!(
        !stdout(&out).contains('\x1b'),
        "NO_COLOR suppresses every escape"
    );
}

#[test]
fn the_window_flags_size_the_headless_frame() {
    for (w, h) in [(80usize, 24usize), (80, 28), (60, 24), (40, 12)] {
        let out = run(&[
            "--headless",
            "--seed",
            "4",
            "--ticks",
            "80",
            "--no-save",
            "--print-frame",
            "--mono",
            "--width",
            &w.to_string(),
            "--height",
            &h.to_string(),
        ]);
        assert!(out.status.success(), "{w}x{h} renders");
        let text = stdout(&out);
        let lines: Vec<&str> = text.lines().collect();
        // The frame is printed last: take its `height` rows.
        assert!(lines.len() >= h, "{w}x{h} printed a frame");
        let frame = &lines[lines.len() - h..];
        assert_eq!(frame.len(), h, "{w}x{h} draws exactly {h} rows");
        for (i, line) in frame.iter().enumerate() {
            assert_eq!(line.chars().count(), w, "{w}x{h}: row {i} is {w} columns");
        }
        let joined = frame.join(" ");
        assert!(joined.contains("score"), "{w}x{h} shows the score");
    }
}

// ---------------------------------------------------------------------------
// The replay harness
// ---------------------------------------------------------------------------

#[test]
fn the_committed_replay_fixture_wins_something() {
    let out = run(&[
        "--replay",
        fixture().to_str().unwrap(),
        "--seed",
        "7",
        "--no-save",
    ]);
    assert!(out.status.success(), "--replay exits 0");
    let kv = kv_of(&stdout(&out));
    assert_eq!(get(&kv, "seed"), "7");
    assert_eq!(get(&kv, "quit"), "0", "the fixture does not quit");
    let cleared: u64 = get(&kv, "waves_cleared").parse().unwrap();
    assert!(cleared >= 1, "the fixture clears a wave: {cleared}");
    let score: u64 = get(&kv, "score").parse().unwrap();
    assert!(score >= 100, "the fixture scores: {score}");
    assert_eq!(get(&kv, "state"), "over", "the session ends");
    assert!(
        !get(&kv, "cause").is_empty(),
        "and names the cause: {}",
        get(&kv, "cause")
    );
}

#[test]
fn a_replay_is_deterministic_and_the_seed_is_the_harnesss_choice() {
    let a = stdout(&run(&[
        "--replay",
        fixture().to_str().unwrap(),
        "--seed",
        "7",
        "--no-save",
    ]));
    let b = stdout(&run(&[
        "--replay",
        fixture().to_str().unwrap(),
        "--seed",
        "7",
        "--no-save",
    ]));
    assert_eq!(a, b, "same seed + same script = byte-identical output");
    let c = stdout(&run(&[
        "--replay",
        fixture().to_str().unwrap(),
        "--seed",
        "8",
        "--no-save",
    ]));
    assert_ne!(a, c, "a different seed is a different run");
    let ha = get(&kv_of(&a), "hash");
    let hc = get(&kv_of(&c), "hash");
    assert_ne!(ha, hc, "the state hash follows the seed");
}

#[test]
fn a_missing_replay_file_is_a_clean_failure() {
    let out = run(&["--replay", "/nonexistent/pengy-invaders.replay", "--no-save"]);
    assert_eq!(out.status.code(), Some(66), "a missing fixture exits 66");
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("cannot read"),
        "and says why"
    );
}

#[test]
fn a_replay_stops_at_the_end_of_the_session() {
    // The run ends inside the fixture, not at its last line.
    let lines = std::fs::read_to_string(fixture())
        .unwrap()
        .lines()
        .filter(|l| !l.starts_with('#'))
        .count() as u64;
    let out = run(&[
        "--replay",
        fixture().to_str().unwrap(),
        "--seed",
        "7",
        "--no-save",
    ]);
    let kv = kv_of(&stdout(&out));
    let ticks: u64 = get(&kv, "ticks").parse().unwrap();
    assert!(ticks > 0 && ticks <= lines, "the session fits in the file");
    // `stopped_at` is the file line the session stopped on, or `eof` when the
    // script ran out first. Header lines are not ticks, so a line number is
    // always at or after the end tick.
    let stopped = get(&kv, "stopped_at");
    match stopped.parse::<usize>() {
        Ok(n) => assert!(
            n >= ticks as usize + 1 && n <= 900,
            "the end tick is reported as a file line: {n} for {ticks} ticks"
        ),
        Err(_) => assert_eq!(stopped, "eof", "stopped_at is a line number or eof"),
    }
}

// ---------------------------------------------------------------------------
// The record: $XDG_DATA_HOME/pengyos/games/pengy-invaders, under $HOME
// ---------------------------------------------------------------------------

#[test]
fn the_high_score_persists_in_the_data_dir_and_only_there() {
    let dir = scratch("best");
    let first = run(&[
        "--headless", "--seed", "7", "--ticks", "900", "--data-dir", dir.to_str().unwrap(),
    ]);
    assert!(first.status.success());
    let score: u64 = get(&kv_of(&stdout(&first)), "score").parse().unwrap();
    assert!(score > 0);
    let best = fs::read_to_string(dir.join("best.txt")).expect("best.txt is written");
    let kv = kv_of(&best);
    assert_eq!(
        get(&kv, "score").parse::<u64>().unwrap(),
        score,
        "the record holds this run's score"
    );
    // The next run sees it.
    let second = run(&[
        "--headless", "--seed", "7", "--ticks", "10", "--data-dir", dir.to_str().unwrap(),
    ]);
    let kv2 = kv_of(&stdout(&second));
    assert_eq!(
        get(&kv2, "best_score").parse::<u64>().unwrap(),
        score,
        "the record is read back"
    );
    // Nothing is written outside the data dir.
    let entries: Vec<_> = fs::read_dir(&dir).unwrap().flatten().map(|e| e.file_name().to_string_lossy().to_string()).collect();
    assert_eq!(entries, vec!["best.txt".to_string()], "only the record lives there");
}

#[test]
fn the_record_never_goes_downhill() {
    let dir = scratch("keep");
    let big = run(&[
        "--headless", "--seed", "7", "--ticks", "900", "--data-dir", dir.to_str().unwrap(),
    ]);
    let score: u64 = get(&kv_of(&stdout(&big)), "score").parse().unwrap();
    for _ in 0..3 {
        let small = run(&[
            "--headless", "--seed", "7", "--ticks", "20", "--data-dir", dir.to_str().unwrap(),
        ]);
        let kv = kv_of(&stdout(&small));
        assert!(
            get(&kv, "best_score").parse::<u64>().unwrap() >= score,
            "a worse run keeps the best"
        );
    }
    let best = fs::read_to_string(dir.join("best.txt")).unwrap();
    assert!(kv_of(&best).iter().any(|(k, v)| k == "score" && v.parse::<u64>().unwrap() >= score));
}

#[test]
fn no_save_touches_nothing() {
    let dir = scratch("nosave");
    let out = run(&[
        "--headless", "--seed", "3", "--ticks", "200", "--no-save", "--data-dir", dir.to_str().unwrap(),
    ]);
    assert!(out.status.success());
    assert!(
        fs::read_dir(&dir).map(|d| d.count()).unwrap_or(0) == 0,
        "--no-save writes no file"
    );
    let kv = kv_of(&stdout(&out));
    assert_eq!(get(&kv, "best_score"), "0");
}

#[test]
fn the_default_data_dir_is_the_xdg_games_path() {
    // Point HOME at a scratch tree and let the game work out its own path.
    let dir = scratch("xdg");
    let home = dir.join("home");
    let _ = fs::create_dir_all(&home);
    let out = Command::new(BIN)
        .args(["--headless", "--seed", "11", "--ticks", "150"])
        .env("HOME", &home)
        .env_remove("XDG_DATA_HOME")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .expect("the binary runs");
    assert!(out.status.success());
    let expected = home
        .join(".local/share/pengyos/games/pengy-invaders/best.txt");
    assert!(
        expected.is_file(),
        "the record is at $HOME/.local/share/pengyos/games/pengy-invaders/"
    );
    // And XDG_DATA_HOME wins when it is set.
    let xdg = dir.join("xdg");
    let out2 = Command::new(BIN)
        .args(["--headless", "--seed", "11", "--ticks", "150"])
        .env("HOME", &home)
        .env("XDG_DATA_HOME", &xdg)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .expect("the binary runs");
    assert!(out2.status.success());
    assert!(xdg
        .join("pengyos/games/pengy-invaders/best.txt")
        .is_file());
}

// ---------------------------------------------------------------------------
// The scripted session, through the library
// ---------------------------------------------------------------------------

#[test]
fn a_scripted_session_ends_in_the_same_state_every_time() {
    // The same script, written out and replayed twice: the acceptance shape.
    let dir = scratch("script");
    let script = dir.join("session.replay");
    let mut text = String::from("# a scripted session\n");
    for i in 0..400 {
        let line = match i % 9 {
            0 => "a",
            2 => "d",
            4 => "f",
            6 => "df",
            _ => "",
        };
        text.push_str(line);
        text.push('\n');
    }
    fs::write(&script, text).expect("write the script");
    let args = [
        "--replay".to_string(),
        script.to_str().unwrap().to_string(),
        "--seed".to_string(),
        "21".to_string(),
        "--no-save".to_string(),
    ];
    let a = stdout(&run(&args.iter().map(|s| s.as_str()).collect::<Vec<_>>()));
    let b = stdout(&run(&args.iter().map(|s| s.as_str()).collect::<Vec<_>>()));
    assert_eq!(a, b, "same seed + same script = the same end state");
    let kv = kv_of(&a);
    let hash = get(&kv, "hash");
    assert_eq!(hash.len(), 16, "the end state is a 16-digit hash");
    assert!(hash.chars().all(|c| c.is_ascii_hexdigit()));
    assert_eq!(get(&kv, "ticks"), "400", "the script ran to the end");
    assert_eq!(get(&kv, "stopped_at"), "eof", "and did not end early");
}
