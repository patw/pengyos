//! End-to-end tests: the real binary, driven the way the gate and
//! `pengyos-qemu-verify` drive it — no pty, no interaction, exit codes and
//! stdout asserted.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};

const BIN: &str = env!("CARGO_BIN_EXE_pengy-pong");

fn out(args: &[&str]) -> Output {
    Command::new(BIN)
        .args(args)
        // No pty, ever: stdin is /dev/null, so the binary must take its
        // headless path. This is the shape of the repo gate's environment.
        .stdin(Stdio::null())
        .output()
        .expect("run pengy-pong")
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
        "pengy-pong-test-{}-{}-{}",
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
fn help_lists_every_flag_and_key_and_exits_zero() {
    let text = stdout_of(&["--help"]);
    for flag in [
        "--seed",
        "--mode",
        "--difficulty",
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
    for key in ["w / s", "k / j", "1 / 2 / 3", "m", "space", "p", "r", "q", "Ctrl-C"] {
        assert!(text.contains(key), "--help must list the {key} key");
    }
    assert!(text.contains("first to eleven"), "--help says what the game is");
    assert!(text.contains("60x20"), "--help states the field");
    assert!(text.contains("50 degrees"), "--help states the bounce angles");
}

#[test]
fn version_prints_the_crate_version() {
    let text = stdout_of(&["--version"]);
    assert!(text.starts_with("pengy-pong "), "{text}");
    assert!(text.contains(env!("CARGO_PKG_VERSION")));
}

#[test]
fn a_bad_flag_is_a_usage_error_exit_2() {
    assert_eq!(code(&["--nope"]), Some(2));
    assert_eq!(code(&["--seed"]), Some(2), "--seed with no value");
    assert_eq!(code(&["--seed", "x"]), Some(2));
    assert_eq!(code(&["--difficulty", "9"]), Some(2));
    assert_eq!(code(&["--mode", "3p"]), Some(2));
    assert_eq!(code(&["--width", "0"]), Some(2));
    assert_eq!(
        code(&["--record", "x", "--replay", "y"]),
        Some(2),
        "--record and --replay are exclusive"
    );
    assert_eq!(code(&["--record", "x"]), Some(2), "--record needs --headless");
    assert_eq!(code(&["--print-frame"]), Some(2), "--print-frame needs a scripted path");
}

#[test]
fn a_missing_replay_file_is_exit_66() {
    assert_eq!(code(&["--replay", "/nonexistent/pengy-pong.replay"]), Some(66));
}

// ---------------------------------------------------------------------------
// The headless contract: no pty, plain text, exit 0
// ---------------------------------------------------------------------------

#[test]
fn headless_needs_no_terminal_and_prints_plain_state() {
    let text = stdout_of(&["--headless", "--seed", "7", "--ticks", "60000", "--no-save"]);
    assert!(!text.contains('\x1b'), "headless emits no escape bytes");
    assert_eq!(get(&text, "state"), "over");
    assert_eq!(get(&text, "seed"), "7");
    assert_eq!(get(&text, "mode"), "1P");
    assert_eq!(get(&text, "difficulty"), "2");
    assert_eq!(get(&text, "winner"), "left");
    assert_eq!(get(&text, "left"), "11");
    assert_eq!(get(&text, "cause"), "won");
    assert_eq!(get(&text, "hash").len(), 16);
    // The field is dumped as ASCII rows.
    assert!(text.contains("field:"));
    assert_eq!(text.lines().filter(|l| l.starts_with('|')).count(), 20);
}

#[test]
fn piped_stdin_takes_the_headless_path_without_a_flag() {
    // The gate's shape: stdin is a pipe, not a tty. No --headless, no crash.
    let o = Command::new(BIN)
        .args(["--seed", "3", "--ticks", "500", "--no-save"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .expect("spawn")
        .wait_with_output()
        .expect("wait");
    assert!(o.status.success());
    let text = String::from_utf8_lossy(&o.stdout).to_string();
    assert!(!text.contains('\x1b'), "no escapes without a tty");
    assert!(text.contains("seed=3"));
}

#[test]
fn headless_is_bit_for_bit_reproducible() {
    let a = stdout_of(&["--headless", "--seed", "11", "--ticks", "60000", "--no-save"]);
    let b = stdout_of(&["--headless", "--seed", "11", "--ticks", "60000", "--no-save"]);
    assert_eq!(a, b, "same seed, same output");
    let c = stdout_of(&["--headless", "--seed", "12", "--ticks", "60000", "--no-save"]);
    assert_ne!(a, c, "a different seed is a different match");
}

#[test]
fn every_level_finishes_and_the_hard_one_is_the_hard_one() {
    let mut player_points = Vec::new();
    for d in ["1", "2", "3"] {
        let mut pts = 0;
        for seed in ["1", "2", "3", "4"] {
            let t = stdout_of(&[
                "--headless",
                "--seed",
                seed,
                "--difficulty",
                d,
                "--ticks",
                "60000",
                "--no-save",
            ]);
            assert_eq!(get(&t, "state"), "over", "level {d} seed {seed} finished");
            pts += get(&t, "left").parse::<u32>().unwrap();
        }
        player_points.push(pts);
    }
    assert!(
        player_points[0] > player_points[2],
        "level 1 is the easy one, level 3 the hard one: {player_points:?}"
    );
    assert!(player_points[1] > player_points[2], "level 2 sits between: {player_points:?}");
}

// ---------------------------------------------------------------------------
// The replay harness
// ---------------------------------------------------------------------------

#[test]
fn the_committed_fixture_replays_to_a_won_match_and_is_deterministic() {
    let args = [
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "7",
        "--mode",
        "1p",
        "--difficulty",
        "2",
        "--no-save",
    ];
    let a = stdout_of(&args);
    let b = stdout_of(&args);
    assert_eq!(a, b, "the same seed and script are byte-identical");
    assert_eq!(get(&a, "state"), "over");
    assert_eq!(get(&a, "winner"), "left", "the fixture wins something");
    assert_eq!(get(&a, "left"), "11");
    assert_eq!(get(&a, "cause"), "won");
    assert_eq!(get(&a, "hash"), "070441adffab99fc");
    assert_eq!(get(&a, "stopped_at"), "eof", "the script ends on the last tick");
    assert_eq!(get(&a, "quit"), "0");
    assert_eq!(get(&a, "replay"), "fixtures/win.replay");
}

#[test]
fn a_replay_stops_at_the_end_tick_and_says_so() {
    // The fixture has 7319 ticks of script; the match ends on the last one, and
    // a longer file would stop at the end tick and report it.
    let dir = tmpdir("stop");
    let path = dir.join("long.replay");
    let fixture = fs::read_to_string("fixtures/win.replay").expect("fixture");
    let mut body = fixture.clone();
    for _ in 0..500 {
        body.push_str("w\n");
    }
    fs::write(&path, body).expect("write");
    let text = stdout_of(&[
        "--replay",
        path.to_str().unwrap(),
        "--seed",
        "7",
        "--mode",
        "1p",
        "--difficulty",
        "2",
        "--no-save",
    ]);
    assert_eq!(get(&text, "state"), "over");
    let stopped = get(&text, "stopped_at");
    assert_ne!(stopped, "eof", "the harness stopped at the end tick");
    let n: usize = stopped.parse().expect("a tick number");
    assert!(n > 7000 && n < 7400, "stopped at tick {n}");
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn a_hand_written_script_is_reproducible_and_prints_its_state() {
    let dir = tmpdir("script");
    let path = dir.join("hand.replay");
    // Blank lines are ticks with no input; '#' lines are comments, not ticks.
    fs::write(
        &path,
        "# hand-written script\n\n\nw\ns\n\nspace\n \nq\n".replace("space", " "),
    )
    .expect("write");
    let a = stdout_of(&[
        "--replay",
        path.to_str().unwrap(),
        "--seed",
        "5",
        "--no-save",
        "--print-frame",
    ]);
    let b = stdout_of(&[
        "--replay",
        path.to_str().unwrap(),
        "--seed",
        "5",
        "--no-save",
        "--print-frame",
    ]);
    assert_eq!(a, b);
    assert_eq!(get(&a, "ticks"), "7", "blank lines are ticks, comments are not");
    assert_eq!(get(&a, "quit"), "1", "q quits the replay");
    assert_eq!(get(&a, "stopped_at"), "9", "the q line is line 9 of the file");
    assert!(a.contains("PENGUIN PONG"), "--print-frame prints the frame");
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn record_then_replay_round_trips() {
    let dir = tmpdir("record");
    let path = dir.join("r.replay");
    let p = path.to_str().unwrap();
    let rec = stdout_of(&[
        "--headless",
        "--seed",
        "21",
        "--difficulty",
        "2",
        "--ticks",
        "60000",
        "--no-save",
        "--record",
        p,
    ]);
    assert!(rec.contains("recorded="), "{rec}");
    let replayed = stdout_of(&[
        "--replay",
        p,
        "--seed",
        "21",
        "--mode",
        "1p",
        "--difficulty",
        "2",
        "--no-save",
    ]);
    // The recorded session replays to the same match: same score, same hash.
    for key in ["left", "right", "points", "hits", "ticks", "hash"] {
        assert_eq!(
            get(&rec, key),
            get(&replayed, key),
            "{key} survived the record/replay round trip"
        );
    }
    let _ = fs::remove_dir_all(dir);
}

// ---------------------------------------------------------------------------
// Presentation flags
// ---------------------------------------------------------------------------

#[test]
fn mono_and_no_color_emit_no_escape_bytes() {
    let mono = stdout_of(&[
        "--headless",
        "--seed", "2",
        "--ticks", "300",
        "--no-save",
        "--print-frame",
        "--mono",
    ]);
    assert!(!mono.contains('\x1b'), "--mono emits no escapes");
    let plain = stdout_of(&[
        "--headless",
        "--seed", "2",
        "--ticks", "300",
        "--no-save",
        "--print-frame",
        "--no-color",
    ]);
    assert!(!plain.contains('\x1b'), "--no-color emits no escapes");
    let colour = stdout_of(&[
        "--headless",
        "--seed", "2",
        "--ticks", "300",
        "--no-save",
        "--print-frame",
    ]);
    assert!(colour.contains("\x1b[1;36m"), "the frame paints in colour by default");
}

#[test]
fn no_color_in_the_environment_is_honoured() {
    let o = Command::new(BIN)
        .args(["--headless", "--seed", "2", "--ticks", "300", "--no-save", "--print-frame"])
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .output()
        .expect("run");
    assert!(o.status.success());
    let text = String::from_utf8_lossy(&o.stdout).to_string();
    assert!(!text.contains('\x1b'), "NO_COLOR means no escapes");
}

#[test]
fn the_frame_is_the_window_it_is_told_about() {
    for (w, h) in [(80usize, 24usize), (62, 22), (40, 10), (20, 5)] {
        let text = stdout_of(&[
            "--headless",
            "--seed", "4",
            "--ticks", "120",
            "--no-save",
            "--print-frame",
            "--mono",
            "--width",
            &w.to_string(),
            "--height",
            &h.to_string(),
        ]);
        let frame = text.rsplit("field:\n").last().unwrap_or(&text);
        let _ = frame;
        // The frame is the last `height` lines of the output.
        let lines: Vec<&str> = text.lines().collect();
        let tail = &lines[lines.len() - h..];
        for line in tail {
            assert!(
                line.chars().count() <= w,
                "{w}x{h}: a row is wider than the window: {line:?}"
            );
        }
    }
}

// ---------------------------------------------------------------------------
// Persistence: $XDG_DATA_HOME/pengyos/games/pengy-pong, and nowhere else
// ---------------------------------------------------------------------------

#[test]
fn the_best_result_is_written_in_the_data_dir_and_read_back() {
    let dir = tmpdir("best");
    let p = dir.join("pengyos/games/pengy-pong");
    let pp = p.to_str().unwrap();
    let first = stdout_of(&[
        "--headless",
        "--seed", "7",
        "--ticks", "60000",
        "--data-dir",
        pp,
        "--no-save",
    ]);
    assert_eq!(get(&first, "best_wins"), "0", "--no-save writes nothing");
    assert!(!p.exists(), "--no-save creates nothing");

    let second = stdout_of(&["--headless", "--seed", "7", "--ticks", "60000", "--data-dir", pp]);
    assert_eq!(get(&second, "best_wins"), "1", "the win is banked");
    assert_eq!(get(&second, "best_margin"), "4", "11-7 is a margin of 4");
    let body = fs::read_to_string(p.join("best.txt")).expect("best.txt");
    assert!(body.contains("wins=1"), "{body}");
    assert!(body.contains("margin=4"));
    assert!(body.contains("rally="));
    assert!(body.contains("seed=7"));

    // The next run reads it back.
    let third = stdout_of(&["--headless", "--seed", "8", "--ticks", "60000", "--data-dir", pp]);
    assert_eq!(get(&third, "best_wins"), "2", "the banked best is read back and grows");
    let best_rally: u64 = get(&third, "best_rally").parse().unwrap();
    let second_rally: u64 = get(&second, "longest_rally").parse().unwrap();
    let third_rally: u64 = get(&third, "longest_rally").parse().unwrap();
    assert_eq!(best_rally, second_rally.max(third_rally), "the longest rally is the best one");
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn the_game_writes_nothing_outside_the_data_dir() {
    let dir = tmpdir("scope");
    let home = dir.join("home");
    fs::create_dir_all(&home).expect("home");
    let o = Command::new(BIN)
        .args(["--headless", "--seed", "9", "--ticks", "60000"])
        .env("HOME", &home)
        .env_remove("XDG_DATA_HOME")
        .stdin(Stdio::null())
        .output()
        .expect("run");
    assert!(o.status.success());
    let expected = home.join(".local/share/pengyos/games/pengy-pong/best.txt");
    assert!(expected.is_file(), "the best file is under \\$HOME/.local/share");
    // Nothing anywhere else under $HOME.
    let mut files = vec![home.clone()];
    let mut found = Vec::new();
    while let Some(d) = files.pop() {
        if let Ok(rd) = fs::read_dir(&d) {
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() {
                    files.push(p);
                } else {
                    found.push(p);
                }
            }
        }
    }
    assert_eq!(found, vec![expected], "the game wrote exactly one file");
    let _ = fs::remove_dir_all(dir);
}
