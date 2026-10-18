//! The real binary, driven with no pty — the way the repo gate and
//! pengyos-qemu-verify run it.
//!
//! Every test here runs the built `pengy-solitaire` with piped stdin/stdout, so
//! it is exactly the no-terminal path: no rendering, no keys, plain text, exit
//! 0. The exit codes, the flags, the record file and the fixtures are all
//! asserted here.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

fn tool() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// Run the binary with no terminal attached to stdin.
fn run<S: AsRef<std::ffi::OsStr>>(args: &[S]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_pengy-solitaire"))
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .expect("the binary runs")
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

/// A scratch data dir under target/, so a test never touches the real record.
fn scratch(name: &str) -> PathBuf {
    let dir = tool().join("target").join("test-tmp").join(name);
    let _ = fs::remove_dir_all(&dir);
    let _ = fs::create_dir_all(&dir);
    dir
}

fn kv(text: &str, key: &str) -> String {
    text.lines()
        .find_map(|l| {
            let (k, v) = l.split_once('=')?;
            (k == key).then(|| v.to_string())
        })
        .unwrap_or_else(|| panic!("no {key}= in the output:\n{text}"))
}

// ---------------------------------------------------------------------------
// --help and --version
// ---------------------------------------------------------------------------

#[test]
fn help_lists_every_flag_and_every_key() {
    let o = run(&["--help"]);
    assert_eq!(code(&o), 0);
    let s = stdout(&o);
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
        assert!(s.contains(flag), "--help lists {flag}");
    }
    for key in [
        "space", "d draw", "f collect", "u undo", "n new", "q quit", "hjkl",
    ] {
        assert!(s.contains(key), "--help lists the key {key}");
    }
    assert!(s.contains("Frostbite Solitaire"), "and says what it is");
    assert!(s.contains("Klondike"), "and which game it is");
    assert!(s.contains("draw three"), "and the draw rule");
    assert!(s.contains("NO_COLOR") || s.contains("no colour"), "and the colour rule");
    // The scoring and the tuning constants are quoted from the library.
    assert!(s.contains("+5") && s.contains("+10") && s.contains("-15") && s.contains("-100"));
}

#[test]
fn version_prints_the_crate_version() {
    let o = run(&["--version"]);
    assert_eq!(code(&o), 0);
    let s = stdout(&o);
    assert!(s.contains("pengy-solitaire"));
    assert!(s.contains(env!("CARGO_PKG_VERSION")));
}

// ---------------------------------------------------------------------------
// The headless path: no pty, plain text, exit 0
// ---------------------------------------------------------------------------

#[test]
fn headless_needs_no_terminal_and_prints_plain_text() {
    let o = run(&["--headless", "--seed", "7", "--ticks", "200", "--no-save"]);
    assert_eq!(code(&o), 0, "the gate has no pty: this must exit 0");
    let s = stdout(&o);
    assert!(!s.contains('\x1b'), "no escapes in the plain-text state");
    for key in ["state=", "seed=7", "score=", "moves=", "hash=", "stock=", "waste=", "tableau[1]="] {
        assert!(s.contains(key), "the dump carries {key}");
    }
    assert_eq!(stderr(&o), "");
}

#[test]
fn a_non_tty_stdin_takes_the_headless_path_automatically() {
    // No --headless at all: piped stdin is not a terminal, so the game must
    // fall back to the scripted path and exit 0, not crash and not paint.
    let o = run(&["--seed", "3", "--ticks", "50", "--no-save"]);
    assert_eq!(code(&o), 0);
    let s = stdout(&o);
    assert!(s.contains("state="));
    assert!(!s.contains("\x1b[?1049h"), "the alternate screen is never entered");
    assert!(!s.contains('\x1b'));
}

#[test]
fn headless_is_bit_for_bit_reproducible() {
    let a = stdout(&run(&["--headless", "--seed", "42", "--ticks", "400", "--no-save"]));
    let b = stdout(&run(&["--headless", "--seed", "42", "--ticks", "400", "--no-save"]));
    assert_eq!(a, b, "same seed, same tick count, same output");
    assert_eq!(kv(&a, "seed"), "42");
    assert_eq!(kv(&a, "ticks"), "400");
    let c = stdout(&run(&["--headless", "--seed", "43", "--ticks", "400", "--no-save"]));
    assert_ne!(kv(&a, "hash"), kv(&c, "hash"), "a different seed is a different game");
}

#[test]
fn print_frame_draws_the_reference_window_headlessly() {
    let o = run(&[
        "--headless",
        "--seed",
        "7",
        "--ticks",
        "1",
        "--no-save",
        "--print-frame",
    ]);
    assert_eq!(code(&o), 0);
    let s = stdout(&o);
    let start = s
        .lines()
        .position(|l| l.starts_with('┌'))
        .expect("the frame starts with the border");
    let lines: Vec<&str> = s.lines().skip(start).collect();
    assert!(lines.len() >= 24, "the frame is 24 rows: {}", lines.len());
    assert_eq!(lines[0].chars().count(), 80, "and 80 columns");
    assert!(lines[23].starts_with('└'), "and closes at the bottom");
    assert!(s.contains("Frostbite Solitaire"));
    assert!(s.contains("┌───┐"), "cards are drawn");
    assert!(!s.contains("\x1b[H"), "the headless frame carries no cursor addressing");
    assert!(!s.contains("\x1b[?1049"), "and no alternate screen");
}

#[test]
fn mono_is_ascii_and_no_colour() {
    let o = run(&[
        "--headless",
        "--seed",
        "7",
        "--ticks",
        "1",
        "--no-save",
        "--print-frame",
        "--mono",
    ]);
    assert_eq!(code(&o), 0);
    let s = stdout(&o);
    assert!(s.is_ascii(), "--mono is plain ASCII, escapes included");
    assert!(s.contains("+---+"));
    assert!(!s.contains("♠"));
}

#[test]
fn no_colour_is_honoured_in_the_environment() {
    // NO_COLOR is the environment contract; the headless frame is plain text
    // with or without it, and the flag is accepted either way.
    let out = Command::new(env!("CARGO_BIN_EXE_pengy-solitaire"))
        .args(["--headless", "--seed", "7", "--ticks", "1", "--no-save", "--print-frame"])
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .output()
        .expect("the binary runs");
    assert_eq!(out.status.code(), Some(0));
    let s = String::from_utf8_lossy(&out.stdout).to_string();
    assert!(!s.contains('\x1b'), "NO_COLOR means no escapes at all");
}

// ---------------------------------------------------------------------------
// The replay harness
// ---------------------------------------------------------------------------

#[test]
fn the_committed_replay_wins_and_reports_the_result_as_key_values() {
    let o = run(&[
        "--state",
        "fixtures/near-win.state",
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "1",
        "--no-save",
    ]);
    assert_eq!(code(&o), 0, "the acceptance harness exits 0");
    let s = stdout(&o);
    assert_eq!(kv(&s, "state"), "win");
    assert_eq!(kv(&s, "foundations"), "52");
    assert_eq!(kv(&s, "score"), "700");
    assert_eq!(kv(&s, "moves"), "121");
    assert_eq!(kv(&s, "quit"), "0");
    assert!(kv(&s, "hash").chars().all(|c| c.is_ascii_hexdigit()));
    assert_eq!(kv(&s, "replay"), "fixtures/win.replay");
}

#[test]
fn a_replay_is_deterministic_and_a_replay_of_a_replay_is_the_same() {
    let args = [
        "--state",
        "fixtures/near-win.state",
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "1",
        "--no-save",
    ];
    let a = stdout(&run(&args));
    let b = stdout(&run(&args));
    assert_eq!(a, b, "byte for byte");
    // The seed on the command line is what the fixture is played on.
    assert_eq!(kv(&a, "seed"), "1");
    let c = stdout(&run(&[
        "--state",
        "fixtures/near-win.state",
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "9",
        "--no-save",
    ]));
    assert_eq!(kv(&c, "seed"), "9", "the seed is a command-line argument");
    assert_eq!(kv(&c, "state"), "win", "the fixture wins on any seed");
    assert_eq!(kv(&c, "score"), kv(&a, "score"));
}

#[test]
fn a_replay_can_be_recorded_and_played_back() {
    let dir = scratch("record");
    let file = dir.join("demo.replay");
    let rec = run(&[
        "--headless",
        "--seed",
        "21",
        "--ticks",
        "300",
        "--record",
        file.to_str().unwrap(),
        "--no-save",
    ]);
    assert_eq!(code(&rec), 0);
    assert!(file.is_file(), "the recording exists");
    let text = fs::read_to_string(&file).unwrap();
    assert!(text.starts_with('#'), "the fixture explains itself");
    assert!(text.contains("--replay") && text.contains("--seed 21"), "and says how to play it back");
    let back = run(&[
        "--replay",
        file.to_str().unwrap(),
        "--seed",
        "21",
        "--no-save",
    ]);
    assert_eq!(code(&back), 0);
    assert_eq!(kv(&stdout(&back), "seed"), "21");
    assert_eq!(kv(&stdout(&back), "ticks"), "300", "one tick per recorded line");
}

#[test]
fn a_position_can_be_saved_and_reloaded() {
    let dir = scratch("state");
    let file = dir.join("pos.state");
    let a = run(&[
        "--headless",
        "--seed",
        "11",
        "--ticks",
        "120",
        "--save",
        file.to_str().unwrap(),
        "--no-save",
    ]);
    assert_eq!(code(&a), 0);
    assert!(file.is_file());
    let b = run(&[
        "--state",
        file.to_str().unwrap(),
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "11",
        "--no-save",
    ]);
    assert_eq!(code(&b), 0, "the saved position loads back into a replay");
    assert!(stdout(&b).contains("state="));
}

#[test]
fn a_missing_replay_or_state_file_is_exit_66() {
    let o = run(&["--replay", "fixtures/nope.replay", "--no-save"]);
    assert_eq!(code(&o), 66);
    assert!(stderr(&o).contains("nope.replay"));
    assert_eq!(stdout(&o), "");

    let o = run(&["--state", "fixtures/nope.state", "--headless", "--no-save"]);
    assert_eq!(code(&o), 66);

    // A state file that is not a position is the same clean failure, not a panic.
    let dir = scratch("bad-state");
    let file = dir.join("bad.state");
    fs::write(&file, "tableau=0,0||||||\nfoundations=||||\nstock=\nwaste=1\n").unwrap();
    let o = run(&["--state", file.to_str().unwrap(), "--headless", "--no-save"]);
    assert_eq!(code(&o), 66);
    assert!(stderr(&o).contains("not a position"));
}

// ---------------------------------------------------------------------------
// Usage errors
// ---------------------------------------------------------------------------

#[test]
fn a_bad_command_line_is_exit_2_with_one_explained_line() {
    for args in [
        vec!["--nope"],
        vec!["--seed", "banana"],
        vec!["--seed"],
        vec!["--ticks", "zero"],
        vec!["--width", "0"],
        vec!["--height", "0"],
        vec!["--record", "x", "--replay", "fixtures/win.replay"],
        vec!["--record", "x", "--seed", "1"],
        vec!["--print-frame", "--seed", "1"],
        vec!["--save", "x", "--seed", "1"],
    ] {
        let o = run(&args);
        assert_eq!(code(&o), 2, "{args:?} is a usage error");
        assert!(stderr(&o).contains("pengy-solitaire"), "{args:?} names the tool");
        assert!(stderr(&o).contains("--help"), "{args:?} points at --help");
        assert_eq!(stdout(&o), "", "a usage error prints no game output");
    }
}

// ---------------------------------------------------------------------------
// The record: Tier-1 mutable state, under the player's home, nowhere else
// ---------------------------------------------------------------------------

#[test]
fn the_record_lives_in_the_data_dir_and_nothing_else_is_written() {
    let dir = scratch("record-write");
    let o = run(&[
        "--headless",
        "--seed",
        "7",
        "--ticks",
        "100",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert_eq!(code(&o), 0);
    let best = dir.join("best.txt");
    assert!(best.is_file(), "the record is written under the data dir");
    let text = fs::read_to_string(&best).unwrap();
    assert!(text.contains("score="), "it carries the score");
    assert!(text.contains("games=1"), "and counts the game");
    assert!(text.contains("pengy-solitaire"), "and says which game it is");

    // Exactly one file, in exactly one place.
    let files: Vec<String> = walk(&dir).into_iter().map(|p| p.file_name().unwrap().to_string_lossy().to_string()).collect();
    assert_eq!(files, vec!["best.txt".to_string()], "and nothing else is written");
}

#[test]
fn the_record_accumulates_and_keeps_the_best_score() {
    let dir = scratch("record-best");
    let args = |seed: &str| {
        vec![
            "--headless".to_string(),
            "--seed".to_string(),
            seed.to_string(),
            "--ticks".to_string(),
            "150".to_string(),
            "--data-dir".to_string(),
            dir.to_str().unwrap().to_string(),
        ]
    };
    let first = run(&args("7"));
    assert_eq!(code(&first), 0);
    let second = run(&args("21"));
    assert_eq!(code(&second), 0);
    let text = fs::read_to_string(dir.join("best.txt")).unwrap();
    assert!(text.contains("games=2"), "two games played: {text}");
    let best: u64 = text
        .lines()
        .find_map(|l| l.strip_prefix("score="))
        .unwrap()
        .parse()
        .unwrap();
    let a: u64 = kv(&stdout(&first), "score").parse().unwrap();
    let b: u64 = kv(&stdout(&second), "score").parse().unwrap();
    assert_eq!(best, a.max(b), "the record keeps the best score");
    // The game reports the record it just wrote.
    assert_eq!(kv(&stdout(&second), "best_score").parse::<u64>().unwrap(), best);
    assert_eq!(kv(&stdout(&second), "best_games"), "2");
}

#[test]
fn a_win_is_counted_in_the_record() {
    let dir = scratch("record-win");
    let o = run(&[
        "--state",
        "fixtures/near-win.state",
        "--replay",
        "fixtures/win.replay",
        "--seed",
        "1",
        "--data-dir",
        dir.to_str().unwrap(),
    ]);
    assert_eq!(code(&o), 0);
    let text = fs::read_to_string(dir.join("best.txt")).unwrap();
    assert!(text.contains("wins=1"), "the win is counted: {text}");
    assert!(text.contains("score=700"), "and the winning score is the record");
    assert!(kv(&stdout(&o), "best_wins") == "1");
}

#[test]
fn no_save_touches_nothing() {
    let dir = scratch("record-none");
    let o = run(&[
        "--headless",
        "--seed",
        "7",
        "--ticks",
        "50",
        "--data-dir",
        dir.to_str().unwrap(),
        "--no-save",
    ]);
    assert_eq!(code(&o), 0);
    assert_eq!(kv(&stdout(&o), "best_games"), "0");
    assert!(walk(&dir).is_empty(), "--no-save writes nothing");
}

#[test]
fn the_default_data_dir_is_the_xdg_path_under_the_home() {
    // Point HOME at a scratch tree with no XDG_DATA_HOME: the game must put its
    // record under ~/.local/share/pengyos/games/pengy-solitaire and nowhere else.
    let home = scratch("home");
    let out = Command::new(env!("CARGO_BIN_EXE_pengy-solitaire"))
        .args(["--headless", "--seed", "7", "--ticks", "40"])
        .env("HOME", &home)
        .env_remove("XDG_DATA_HOME")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .output()
        .expect("the binary runs");
    assert_eq!(out.status.code(), Some(0));
    let expected = home
        .join(".local/share")
        .join("pengyos")
        .join("games")
        .join("pengy-solitaire")
        .join("best.txt");
    assert!(expected.is_file(), "the record is at {}", expected.display());
    let xdg = home.join("xdg");
    let out2 = Command::new(env!("CARGO_BIN_EXE_pengy-solitaire"))
        .args(["--headless", "--seed", "7", "--ticks", "40"])
        .env("HOME", &home)
        .env("XDG_DATA_HOME", &xdg)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .output()
        .expect("the binary runs");
    assert_eq!(out2.status.code(), Some(0));
    assert!(
        xdg.join("pengyos")
            .join("games")
            .join("pengy-solitaire")
            .join("best.txt")
            .is_file(),
        "XDG_DATA_HOME wins when it is set"
    );
}

/// Every file under a directory, for the "nothing else was written" assertions.
fn walk(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(entries) = fs::read_dir(dir) {
        for e in entries.flatten() {
            let p = e.path();
            if p.is_dir() {
                out.extend(walk(&p));
            } else {
                out.push(p);
            }
        }
    }
    out
}
