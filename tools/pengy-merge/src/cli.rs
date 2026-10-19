//! The Icefloe 2048 program: the command line, the record file, the
//! headless harness and, with the `cosmic` feature, the window (`app`, a child
//! of this module). It lives in the library so the shared `pengy-games` binary
//! can carry it; `src/main.rs` only hands it the command line.
//!
//! ```text
//! pengy-merge                       the window (src/app.rs, libcosmic)
//! pengy-merge --headless            the demo player, no window, plain text
//! pengy-merge --replay FILE         a recorded session, no window
//! ```
//!
//! Every rule lives in the crate root (`src/lib.rs`), which stays pure: this
//! module is where the files, the clock and the window are.

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process;

use crate::{
    plain_state, Game, RenderOpts, State, DEFAULT_GOAL, DEFAULT_SIZE, MAX_SIZE, MIN_SIZE,
};

#[cfg(feature = "cosmic")]
#[path = "app.rs"]
mod app;

/// Crate version, from Cargo.toml.
const VERSION: &str = env!("CARGO_PKG_VERSION");

const USAGE: &str = "\
pengy-merge — Icefloe 2048: slide the ice floes, equal floes fuse into one
bigger floe, reach 2048.

USAGE
  pengy-merge [FLAG]...

FLAGS
  --seed N          Seed the run (default 1). Same seed + same inputs = the
                    same run, bit for bit.
  --size N          Field side: 3..=8 (default 4).
  --goal N          The floe that wins the run (default 2048).
  --headless        No window, no keys: run the built-in autopilot
                    for --ticks turns, print the plain-text state, exit 0.
                    This is how the game is proven to work where there is no
                    display (the repo gate, pengyos-qemu-verify).
  --replay FILE     Play a recorded session headlessly: one line of input per
                    tick, a blank line means no input, a '#' line is a comment
                    and is not a tick. Stops at the game-over tick or at EOF.
                    Prints the final state as key=value lines and exits 0.
                    This is the acceptance harness.
  --record FILE     With --headless: write the autopilot's input script to
                    FILE in --replay format (used to make the fixtures).
  --ticks N         Ticks for the headless/autopilot path (default 400).
  --autopilot       Interactive: let the built-in autopilot slide. Demo mode.
  --mono            ASCII-only glyphs (no box-drawing characters), no colour.
  --no-color        No ANSI colour. NO_COLOR in the environment is honoured
                    automatically and implies this.
  --print-frame     With --headless/--replay: also print the final frame as
                    plain text.
  --width N         Window width for the headless frame (default 80).
  --height N        Window height for the headless frame (default 24).
  --data-dir DIR    Where the high score is kept (default
                    $XDG_DATA_HOME/pengyos/games/pengy-merge, i.e.
                    ~/.local/share/pengyos/games/pengy-merge).
  --no-save         Do not read or write the high score (tests, sandboxes).
  --help, -h        This text.
  --version         Version.

KEYS (the window)
  arrows            Slide left, down, up, right. WASD and hjkl work too.
  Ctrl+Z / U        Undo the last slide (one step; the spawned floe goes back).
  C                 Keep playing after reaching the goal.
  Ctrl+N / F2       New game (same seed family: seed + 1, best score kept).
  Ctrl+Q            Quit.

RULES IN ONE BREATH
  A slide moves every floe in the field as far as it goes. Two equal floes that
  meet fuse into one of double value and the fused value is added to the score;
  fusing happens from the direction of travel and a floe fuses at most once per
  slide, so 4 4 8 8 sliding left becomes 8 16, and 2 4 2 does not chain. After
  any slide that moved something, one floe appears in a random empty cell: a 2
  nine times out of ten, a 4 once. Reach 2048 to win. The run is over when a
  slide changes nothing and nothing can fuse. Best score persists.
";

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/// Parsed command line. Every flag in `USAGE` maps to exactly one field.
struct Args {
    seed: u64,
    seed_given: bool,
    size: usize,
    goal: u32,
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
            size: DEFAULT_SIZE,
            goal: DEFAULT_GOAL,
            headless: false,
            replay: None,
            record: None,
            ticks: 400,
            autopilot: false,
            mono: false,
            no_color: false,
            print_frame: false,
            width: 80,
            height: 24,
            data_dir: None,
            no_save: false,
            help: false,
            version: false,
        }
    }
}

/// Usage error: one line on stderr, exit 2 (the repo's CLI convention).
fn usage_error(msg: &str) -> ! {
    eprintln!("pengy-merge: {msg}");
    eprintln!("pengy-merge: try --help");
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
            "--size" => {
                let v = value("number");
                a.size = v
                    .parse::<usize>()
                    .unwrap_or_else(|_| usage_error(&format!("--size: not a number: {v}")));
            }
            "--goal" => {
                let v = value("number");
                a.goal = v
                    .parse::<u32>()
                    .unwrap_or_else(|_| usage_error(&format!("--goal: not a number: {v}")));
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
    if !(MIN_SIZE..=MAX_SIZE).contains(&a.size) {
        usage_error(&format!("--size must be {MIN_SIZE}..={MAX_SIZE}, got {}", a.size));
    }
    if a.goal < 4 {
        usage_error("--goal must be >= 4");
    }
    if !a.goal.is_power_of_two() {
        usage_error(&format!("--goal must be a power of two, got {}", a.goal));
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
// High score persistence — Tier-1 mutable state, under $HOME only.
// ---------------------------------------------------------------------------

/// `$XDG_DATA_HOME/pengyos/games/pengy-merge`, defaulting to `~/.local/share`.
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
    base.join("pengyos").join("games").join("pengy-merge")
}

/// The persisted record: best score, the floe it ended on, the seed it happened
/// on, and how many slides it took.
#[derive(Clone, Copy, Debug, Default)]
struct Best {
    score: i64,
    max: u32,
    moves: u64,
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
                "max" => b.max = v.parse().unwrap_or(0),
                "moves" => b.moves = v.parse().unwrap_or(0),
                "seed" => b.seed = v.parse().unwrap_or(0),
                _ => {}
            }
        }
        b
    }

    /// Merge this run into the record and write it back. Every failure is
    /// ignored: a read-only `$HOME` must never cost the player a game.
    fn save(dir: &PathBuf, g: &Game) -> Best {
        let mut b = Best::load(dir);
        if g.score > b.score {
            b.score = g.score;
            b.max = g.max_tile;
            b.moves = g.moves;
            b.seed = g.seed;
        }
        if fs::create_dir_all(dir).is_ok() {
            let body = format!(
                "# pengy-merge best score (Icefloe 2048, pengy-merge {})\nscore={}\nmax={}\nmoves={}\nseed={}\n",
                VERSION, b.score, b.max, b.moves, b.seed
            );
            let _ = fs::write(dir.join("best.txt"), body);
        }
        b
    }
}

// ---------------------------------------------------------------------------
// Headless: the scripted paths. No window is ever opened.
// ---------------------------------------------------------------------------

/// One line of a replay file → the key for that tick, `None` = no input.
/// Blank line: no input. `#` line: a comment, not a tick (the caller skips it).
fn replay_key(line: &str) -> Option<char> {
    line.chars().next()
}

fn run_replay(args: &Args, path: &str, dir: PathBuf) -> i32 {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("pengy-merge: cannot read replay file {path}: {e}");
            return 66;
        }
    };
    // The seed is a command-line argument, never a file property: a fixture
    // replays on whatever seed the harness asks for.
    let mut g = Game::with(args.seed, args.size, args.goal);
    if !args.no_save {
        g.best = Best::load(&dir).score;
    }
    let mut quit = false;
    let mut stopped_at: Option<usize> = None;
    for (n, raw) in text.lines().enumerate() {
        let line = raw.trim_end_matches('\r');
        if line.starts_with('#') {
            continue; // a comment is not a tick
        }
        // The session is over when the run is over: a finished run is frozen,
        // and `r` on the end screen *restarts* (that is the two-second restart
        // the player wants, and it would silently start a new session in the
        // middle of a replay). So the harness stops at the end tick.
        if g.state != State::Playing {
            stopped_at = Some(n + 1);
            break;
        }
        if let Some(c) = replay_key(line) {
            if g.key(c) {
                quit = true;
                stopped_at = Some(n + 1);
                break;
            }
        }
        g.tick();
    }
    if !args.no_save {
        Best::save(&dir, &g);
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
    let mut g = Game::with(args.seed, args.size, args.goal);
    if !args.no_save {
        g.best = Best::load(&dir).score;
    }
    let mut script: Vec<String> = Vec::new();
    for _ in 0..args.ticks {
        let key = g.autopilot_key();
        if args.record.is_some() {
            script.push(key.map(|c| c.to_string()).unwrap_or_default());
        }
        if let Some(c) = key {
            g.key(c);
        }
        g.tick();
        if g.state != State::Playing {
            break;
        }
    }
    if !args.no_save {
        Best::save(&dir, &g);
    }
    if let Some(path) = &args.record {
        let header = format!(
            "# pengy-merge replay fixture (Icefloe 2048).\n# One line of input per tick; blank = no input; '#' = comment.\n# Play it back with: pengy-merge --replay <this file> --seed {}\n",
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
            eprintln!("pengy-merge: cannot write {path}: {e}");
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

fn render_opts(args: &Args, _tty: bool) -> RenderOpts {
    let (width, height) = (args.width, args.height);
    RenderOpts {
        width,
        height,
        mono: args.mono,
        color: !args.no_color,
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

/// Run the program on a command line (without the program name). Every
/// path ends the process with its exit code.
pub fn main(argv: Vec<String>) {
    let args = parse_args(argv);
    if args.help {
        print!("{USAGE}");
        process::exit(0);
    }
    if args.version {
        println!("pengy-merge {VERSION}");
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
    eprintln!("pengy-merge: built without the window (--features cosmic); --headless and --replay work");
    2
}
