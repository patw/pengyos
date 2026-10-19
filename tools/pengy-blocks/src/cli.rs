//! The Glacier Blocks program: the command line, the record file, the
//! headless harness and, with the `cosmic` feature, the window (`app`, a child
//! of this module). It lives in the library so the shared `pengy-games` binary
//! can carry it; `src/main.rs` only hands it the command line.
//!
//! ```text
//! pengy-blocks                       the window (src/app.rs, libcosmic)
//! pengy-blocks --headless            the demo player, no window, plain text
//! pengy-blocks --replay FILE         a recorded session, no window
//! ```
//!
//! Every rule lives in the crate root (`src/lib.rs`), which stays pure: this
//! module is where the files, the clock and the window are.

use std::env;
use std::fmt::Write as _;
use std::fs;
use std::path::PathBuf;
use std::process;

use crate::{
    plain_state, Game, RenderOpts, State, CLEAR_SCORE, GRAVITY_MS, HARD_DROP_POINTS_PER_ROW,
    LEGEND, LOCK_DELAY_MS, MAX_LOCK_RESETS, REF_HEIGHT, REF_WIDTH, SOFT_DROP_POINTS,
};

#[cfg(feature = "cosmic")]
#[path = "app.rs"]
mod app;

/// Crate version, from Cargo.toml.
const VERSION: &str = env!("CARGO_PKG_VERSION");

const USAGE: &str = "\
pengy-blocks — Glacier Blocks: ten columns, twenty rows, seven pieces, no mercy.

USAGE
  pengy-blocks [FLAG]...

FLAGS
  --seed N          Seed the run (default 1). Same seed + same inputs = the
                    same game, bit for bit.
  --headless        No window, no keys: run the built-in autopilot
                    for --ticks ticks, print the plain-text state, exit 0.
                    This is how the game is proven to work where there is no
                    display (the repo gate, pengyos-qemu-verify).
  --replay FILE     Play a recorded session headlessly: one line of input per
                    tick, a blank line means no input, a '#' line is a comment
                    and is not a tick. Stops at the game-over tick or at EOF.
                    Prints the final state as key=value lines and exits 0.
                    This is the acceptance harness.
  --record FILE     With --headless: write the autopilot's input script to
                    FILE in --replay format (used to make the fixtures).
  --ticks N         Ticks for the headless/autopilot path (default 20000).
  --autopilot       Interactive: let the built-in autopilot play. Demo mode.
  --mono            ASCII-only glyphs (no box-drawing, no block characters),
                    no colour, no side panels.
  --no-color        No ANSI colour. NO_COLOR in the environment is honoured
                    automatically and implies this.
  --print-frame     With --headless/--replay: also print the final frame as
                    plain text.
  --width N         Window width for the headless frame (default 80).
  --height N        Window height for the headless frame (default 24).
  --data-dir DIR    Where the best score is kept (default
                    $XDG_DATA_HOME/pengyos/games/pengy-blocks, i.e.
                    ~/.local/share/pengyos/games/pengy-blocks).
  --no-save         Do not read or write the best score (tests, sandboxes).
  --help, -h        This text.
  --version         Version.

KEYS (the window)
  left / right      Move. Held, the piece keeps sliding. A and D work too.
  down              Soft drop, 1 point per row. S works too.
  up / X            Rotate clockwise. W works too.
  Z                 Rotate counter-clockwise.
  space             Hard drop — straight to the floor, 2 points per row.
  shift / C         Hold (swap the piece with the hold slot; once per piece).
  Esc / P           Pause / resume.
  Ctrl+N / F2       New game (next seed in the seed family, best kept).
  Ctrl+Q            Quit.

RULES IN ONE BREATH
  The well is 10x20 with two hidden spawn rows above it. All seven tetrominoes
  come out of a 7-bag, so you never wait for an I for ever. Gravity is
  800/720/630/550/470/380/300/220/130/100/80 ms by level, and level is
  1 + lines/10. Rotation tries in place, then +/-1 column, then +/-2, then up
  one. A piece that cannot move down locks after a 300 ms lock delay, which a
  successful move resets (up to 15 times). Clearing 1/2/3/4 lines scores
  100/300/500/800 times the level. The game ends when a spawn overlaps the
  well.
";

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/// Parsed command line. Every flag in `USAGE` maps to exactly one field.
struct Args {
    seed: u64,
    seed_given: bool,
    headless: bool,
    replay: Option<String>,
    record: Option<String>,
    ticks: u64,
    autopilot: bool,
    mono: bool,
    no_color: bool,
    print_frame: bool,
    width: usize,
    height: usize,
    data_dir: Option<PathBuf>,
    no_save: bool,
    help: bool,
    version: bool,
}

impl Default for Args {
    fn default() -> Self {
        Self {
            seed: 1,
            seed_given: false,
            headless: false,
            replay: None,
            record: None,
            ticks: 20_000,
            autopilot: false,
            mono: false,
            no_color: false,
            print_frame: false,
            width: REF_WIDTH,
            height: REF_HEIGHT,
            data_dir: None,
            no_save: false,
            help: false,
            version: false,
        }
    }
}

/// Usage error: one line on stderr, exit 2 (the repo's CLI convention).
fn usage_error(msg: &str) -> ! {
    eprintln!("pengy-blocks: {msg}");
    eprintln!("pengy-blocks: try --help");
    process::exit(2);
}

fn parse_args(argv: Vec<String>) -> Args {
    let mut a = Args::default();
    let mut i = 0;
    while i < argv.len() {
        let flag = argv[i].as_str();
        let mut value = |what: &str| -> String {
            i += 1;
            match argv.get(i) {
                Some(v) => v.clone(),
                None => usage_error(&format!("{flag} needs a {what}")),
            }
        };
        match flag {
            "--help" | "-h" => a.help = true,
            "--version" => a.version = true,
            "--seed" => {
                let v = value("number");
                a.seed = v
                    .parse::<u64>()
                    .unwrap_or_else(|_| usage_error(&format!("--seed: not a number: {v}")));
                a.seed_given = true;
            }
            "--headless" => a.headless = true,
            "--replay" => a.replay = Some(value("file")),
            "--record" => a.record = Some(value("file")),
            "--ticks" => {
                let v = value("number");
                a.ticks = v
                    .parse::<u64>()
                    .unwrap_or_else(|_| usage_error(&format!("--ticks: not a number: {v}")));
            }
            "--autopilot" => a.autopilot = true,
            "--mono" => a.mono = true,
            "--no-color" => a.no_color = true,
            "--print-frame" => a.print_frame = true,
            "--width" => {
                let v = value("number");
                a.width = v
                    .parse::<usize>()
                    .unwrap_or_else(|_| usage_error(&format!("--width: not a number: {v}")));
            }
            "--height" => {
                let v = value("number");
                a.height = v
                    .parse::<usize>()
                    .unwrap_or_else(|_| usage_error(&format!("--height: not a number: {v}")));
            }
            "--data-dir" => a.data_dir = Some(PathBuf::from(value("directory"))),
            "--no-save" => a.no_save = true,
            other => usage_error(&format!("unknown flag: {other}")),
        }
        i += 1;
    }
    if a.mono {
        // Mono is the ASCII-only, colour-free presentation.
        a.no_color = true;
    }
    if env::var_os("NO_COLOR").is_some() {
        a.no_color = true;
    }
    if a.width == 0 || a.height == 0 {
        usage_error("--width and --height must be >= 1");
    }
    if a.record.is_some() && a.replay.is_some() {
        usage_error("--record and --replay are mutually exclusive");
    }
    if a.record.is_some() && !a.headless {
        usage_error("--record needs --headless");
    }
    if a.record.is_some() && !a.seed_given {
        usage_error("--record needs --seed (a recorded session must pin its seed)");
    }
    if a.print_frame && !(a.headless || a.replay.is_some()) {
        usage_error("--print-frame needs --headless or --replay");
    }
    a
}

// ---------------------------------------------------------------------------
// Best score — Tier-1 mutable state, under $HOME only.
// ---------------------------------------------------------------------------

/// `$XDG_DATA_HOME/pengyos/games/pengy-blocks`, defaulting to `~/.local/share`.
/// Never `/usr`, never the repo, never a sibling of the binary.
fn data_dir(explicit: Option<PathBuf>) -> PathBuf {
    if let Some(d) = explicit {
        return d;
    }
    let base = match env::var_os("XDG_DATA_HOME") {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => {
            let home = env::var_os("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            home.join(".local/share")
        }
    };
    base.join("pengyos").join("games").join("pengy-blocks")
}

/// The persisted record: best score, most lines, highest level, games played.
#[derive(Clone, Copy, Debug, Default)]
struct Best {
    score: u64,
    lines: u64,
    level: u64,
    games: u64,
    seed: u64,
}

impl Best {
    fn load(dir: &PathBuf) -> Best {
        let text = match fs::read_to_string(dir.join("best.txt")) {
            Ok(t) => t,
            Err(_) => return Best::default(),
        };
        let mut b = Best::default();
        for line in text.lines() {
            let mut kv = line.splitn(2, '=');
            let (k, v) = match (kv.next(), kv.next()) {
                (Some(k), Some(v)) => (k.trim(), v.trim()),
                _ => continue,
            };
            match k {
                "score" => b.score = v.parse().unwrap_or(0),
                "lines" => b.lines = v.parse().unwrap_or(0),
                "level" => b.level = v.parse().unwrap_or(0),
                "games" => b.games = v.parse().unwrap_or(0),
                "seed" => b.seed = v.parse().unwrap_or(0),
                _ => {}
            }
        }
        b
    }

    /// Merge this game into the record and write it back. Every failure is
    /// ignored: a read-only `$HOME` must never cost the player a game.
    fn save(dir: &PathBuf, g: &Game) -> Best {
        let mut b = Best::load(dir);
        b.games += 1;
        if g.score > b.score {
            b.score = g.score;
            b.seed = g.seed;
        }
        if g.lines > b.lines {
            b.lines = g.lines;
        }
        if (g.level as u64) > b.level {
            b.level = g.level as u64;
        }
        if fs::create_dir_all(dir).is_ok() {
            let body = format!(
                "# pengy-blocks best score (Glacier Blocks, pengy-blocks {VERSION})\nscore={}\nlines={}\nlevel={}\ngames={}\nseed={}\n",
                b.score, b.lines, b.level, b.games, b.seed
            );
            let _ = fs::write(dir.join("best.txt"), body);
        }
        b
    }
}

// ---------------------------------------------------------------------------
// Headless: the scripted paths. No window is ever opened.
// ---------------------------------------------------------------------------

/// Bank the game into the persisted record and put the record's new numbers
/// back into the game, so what the run reports is what the file now holds.
fn bank(dir: &PathBuf, g: &mut Game) {
    let b = Best::save(dir, g);
    apply_best(g, &b);
}

fn apply_best(g: &mut Game, b: &Best) {
    g.best_score = b.score;
    g.best_lines = b.lines;
    g.best_level = b.level as usize;
    g.best_games = b.games;
}

/// One line of a replay file → the keys for that tick. A line with characters
/// is those keypresses, applied in order (a held key autorepeats, so a line may
/// carry more than one). A blank line is no input; a `#` line is a comment and
/// is not a tick (the caller skips it).
fn replay_keys(line: &str) -> Vec<char> {
    // A space is the hard drop, and a trailing space at the end of a line is
    // the kind of thing an editor eats, so the file spells it `_`. Spaces
    // inside a line are separators and are ignored; a blank line is no input.
    line.trim_end()
        .chars()
        .filter(|c| *c != ' ')
        .map(|c| if c == '_' { ' ' } else { c })
        .collect()
}

fn run_replay(args: &Args, path: &str, dir: PathBuf) -> i32 {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("pengy-blocks: cannot read replay file {path}: {e}");
            return 66;
        }
    };
    // The seed is a command-line argument, never a file property: a fixture
    // replays on whatever seed the harness asks for.
    let mut g = Game::new(args.seed);
    if !args.no_save {
        apply_best(&mut g, &Best::load(&dir));
    }
    let mut quit = false;
    let mut stopped_at: Option<usize> = None;
    for (n, raw) in text.lines().enumerate() {
        let line = raw.trim_end_matches('\r');
        if line.starts_with('#') {
            continue; // a comment is not a tick
        }
        // The session is over when the game is over: a finished game is
        // frozen, and `r` on the end screen *restarts*, which would silently
        // start a new session in the middle of a replay. So the harness stops
        // at the end tick.
        if g.state == State::Over {
            stopped_at = Some(n + 1);
            break;
        }
        for c in replay_keys(line) {
            if g.key(c) {
                quit = true;
                stopped_at = Some(n + 1);
                break;
            }
        }
        if quit {
            break;
        }
        g.tick();
    }
    if !args.no_save {
        bank(&dir, &mut g);
    }
    for (k, v) in g.result_kv() {
        println!("{k}={v}");
    }
    println!("replay={path}");
    println!("quit={}", u8::from(quit));
    println!(
        "stopped_at={}",
        stopped_at.map(|n| n.to_string()).unwrap_or_else(|| "eof".into())
    );
    if args.print_frame {
        print!("{}", g.render(render_opts(args, false)));
    }
    0
}

fn run_headless(args: &Args, dir: PathBuf) -> i32 {
    let mut g = Game::new(args.seed);
    if !args.no_save {
        apply_best(&mut g, &Best::load(&dir));
    }
    let mut script: Vec<String> = Vec::new();
    for _ in 0..args.ticks {
        let keys = g.autopilot_keys();
        if args.record.is_some() {
            script.push(keys.replace(' ', "_"));
        }
        for c in keys.chars() {
            g.key(c);
        }
        g.tick();
        if g.state == State::Over {
            break;
        }
    }
    if !args.no_save {
        bank(&dir, &mut g);
    }
    if let Some(path) = &args.record {
        let header = format!(
            "# pengy-blocks replay fixture (Glacier Blocks).\n# One line of input per tick; blank = no input; '#' = comment.\n# Play it back with: pengy-blocks --replay <this file> --seed {}\n",
            args.seed
        );
        // Exactly one line per tick: no blank separator between the header and
        // the script, or the first tick of the session goes missing.
        let body = format!("{}{}\n", header, script.join("\n"));
        let p = PathBuf::from(path);
        if let Some(parent) = p.parent() {
            if !parent.as_os_str().is_empty() {
                let _ = fs::create_dir_all(parent);
            }
        }
        if let Err(e) = fs::write(&p, body) {
            eprintln!("pengy-blocks: cannot write {path}: {e}");
            return 66;
        }
        println!("recorded={} ticks={}", path, script.len());
    }
    print!("{}", plain_state(&g));
    if args.print_frame {
        print!("{}", g.render(render_opts(args, false)));
    }
    0
}

fn render_opts(args: &Args, tty: bool) -> RenderOpts {
    let (width, height) = (args.width, args.height);
    RenderOpts {
        width,
        height,
        mono: args.mono,
        // With no terminal there is nobody to render colour for: the headless
        // frame is plain text, escapes included in the definition.
        color: !args.no_color && tty,
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

/// The part of `--help` that quotes the tuning constants, so the text can
/// never drift from the rules in `src/lib.rs`.
fn help_tail() -> String {
    let mut s = String::from("GRAVITY (milliseconds per row, by level)\n  ");
    for (i, g) in GRAVITY_MS.iter().enumerate() {
        let _ = write!(s, "L{}={}", i + 1, g);
        s.push(if i + 1 == GRAVITY_MS.len() { '\n' } else { ' ' });
    }
    let _ = write!(s, "  Level 11 and above: {} ms.\n", GRAVITY_MS[GRAVITY_MS.len() - 1]);
    let _ = write!(
        s,
        "SCORING\n  Lines: 1/2/3/4 = {} {} {} {} x level.\n  Soft drop {} per row, hard drop {} per row.\n  Lock delay {} ms, reset by a successful move (max {} times).\nKEYS\n  {LEGEND}\n",
        CLEAR_SCORE[1],
        CLEAR_SCORE[2],
        CLEAR_SCORE[3],
        CLEAR_SCORE[4],
        SOFT_DROP_POINTS,
        HARD_DROP_POINTS_PER_ROW,
        LOCK_DELAY_MS,
        MAX_LOCK_RESETS
    );
    s
}

/// Run the program on a command line (without the program name). Every
/// path ends the process with its exit code.
pub fn main(argv: Vec<String>) {
    let args = parse_args(argv);
    if args.help {
        print!("{USAGE}{}", help_tail());
        process::exit(0);
    }
    if args.version {
        println!("pengy-blocks {VERSION}");
        process::exit(0);
    }

    let dir = data_dir(args.data_dir.clone());

    // A scripted session never opens a window: it prints plain text, exits 0.
    if let Some(path) = args.replay.clone() {
        process::exit(run_replay(&args, &path, dir));
    }
    if args.headless {
        process::exit(run_headless(&args, dir));
    }
    process::exit(run_window(&args, dir));
}

/// No flag: the window (src/app.rs).
#[cfg(feature = "cosmic")]
fn run_window(args: &Args, dir: PathBuf) -> i32 {
    app::run(args, dir)
}

#[cfg(not(feature = "cosmic"))]
fn run_window(_args: &Args, _dir: PathBuf) -> i32 {
    eprintln!("pengy-blocks: built without the window (--features cosmic); --headless and --replay work");
    2
}
