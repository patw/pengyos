//! The Thaw Invaders program: the command line, the record file, the
//! headless harness and, with the `cosmic` feature, the window (`app`, a child
//! of this module). It lives in the library so the shared `pengy-games` binary
//! can carry it; `src/main.rs` only hands it the command line.
//!
//! ```text
//! pengy-invaders                       the window (src/app.rs, libcosmic)
//! pengy-invaders --headless            the demo player, no window, plain text
//! pengy-invaders --replay FILE         a recorded session, no window
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
    plain_state, Game, RenderOpts, State, BASE_PERIOD, BOMB_RATE_BASE, BOMB_RATE_WAVE, COLS,
    FIRE_COOLDOWN, FORM_COLS, FORM_ROWS, LASER_SPEED, MAX_BOMBS, MOVE_STEP, REF_WIDTH, ROWS,
    START_LIVES, TICK_MS,
};

#[cfg(feature = "cosmic")]
#[path = "app.rs"]
mod app;

/// Crate version, from Cargo.toml.
const VERSION: &str = env!("CARGO_PKG_VERSION");

const USAGE: &str = "\
pengy-invaders — Thaw Invaders: something is melting the ice from above and
you have one laser. The 1978 arcade original, on the COSMIC desktop.

USAGE
  pengy-invaders [FLAG]...

FLAGS
  --seed N          Seed the run (default 1). Same seed + same inputs = the
                    same run, bit for bit.
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
  --ticks N         Ticks for the headless/autopilot path (default 12000).
  --wave N          Start on wave N (default 1). Waves get lower and faster.
  --autopilot       Interactive: let the built-in autopilot fly the ship.
                    Demo mode.
  --mono            ASCII-only glyphs (no box-drawing characters), no colour.
  --no-color        No ANSI colour. NO_COLOR in the environment is honoured
                    automatically and implies this.
  --print-frame     With --headless/--replay: also print the final frame.
  --width N         Window width for the headless frame (default 80).
  --height N        Window height for the headless frame (default 24).
  --data-dir DIR    Where the high score is kept (default
                    $XDG_DATA_HOME/pengyos/games/pengy-invaders, i.e.
                    ~/.local/share/pengyos/games/pengy-invaders).
  --no-save         Do not read or write the high score (tests, sandboxes).
  --help, -h        This text.
  --version         Version.

KEYS (the window)
  left / right      Move the ship. Held, it keeps moving. A and D work too.
  space / up        Fire. One laser in flight, 2 ticks of cooldown; held, it
                    fires again as soon as it may. Space also starts the run.
  Esc / P           Pause / resume.
  Ctrl+N / F2       New game (next seed in the seed family, best kept).
  Ctrl+Q            Quit.

RULES IN ONE BREATH
  A formation of 11x5 aliens, 2 cells wide and spaced 4 apart, marches across a
  60x24 field, stepping one column every N ticks. Touching a wall reverses it
  and drops it one row. N shrinks as the formation thins, so the last few
  aliens are the fast ones. Aliens drop bombs; a bomb that reaches you takes a
  life. Three destructible shields stand between you and the formation. Clear
  the wave and the next one starts one row lower and faster. If the formation
  reaches your row, the ice is gone.
";

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/// Parsed command line. Every flag in `USAGE` maps to exactly one field.
struct Args {
    seed: u64,
    headless: bool,
    replay: Option<String>,
    record: Option<String>,
    ticks: u64,
    wave: u32,
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
            headless: false,
            replay: None,
            record: None,
            ticks: 12_000,
            wave: 1,
            autopilot: false,
            mono: false,
            no_color: false,
            print_frame: false,
            width: REF_WIDTH,
            height: 24,
            data_dir: None,
            no_save: false,
            help: false,
            version: false,
        }
    }
}

fn usage_error(msg: &str) -> ! {
    eprintln!("pengy-invaders: {msg}\nTry --help.");
    process::exit(2);
}

fn parse_args(argv: Vec<String>) -> Args {
    let mut a = Args::default();
    let mut i = 0;
    while i < argv.len() {
        let flag = argv[i].as_str();
        let next = |i: &mut usize| -> String {
            *i += 1;
            argv.get(*i).cloned().unwrap_or_default()
        };
        match flag {
            "--help" | "-h" => a.help = true,
            "--version" => a.version = true,
            "--seed" => {
                let v = next(&mut i);
                a.seed = v.parse().unwrap_or_else(|_| usage_error("--seed wants a number"))
            }
            "--ticks" => {
                let v = next(&mut i);
                a.ticks = v.parse().unwrap_or_else(|_| usage_error("--ticks wants a number"))
            }
            "--wave" => {
                let v = next(&mut i);
                a.wave = v
                    .parse()
                    .unwrap_or_else(|_| usage_error("--wave wants a number"));
                if a.wave < 1 {
                    usage_error("--wave wants 1 or more");
                }
            }
            "--width" => {
                let v = next(&mut i);
                a.width = v
                    .parse()
                    .unwrap_or_else(|_| usage_error("--width wants a number"));
                if a.width < 1 {
                    usage_error("--width wants 1 or more");
                }
            }
            "--height" => {
                let v = next(&mut i);
                a.height = v
                    .parse()
                    .unwrap_or_else(|_| usage_error("--height wants a number"));
                if a.height < 1 {
                    usage_error("--height wants 1 or more");
                }
            }
            "--headless" => a.headless = true,
            "--autopilot" => a.autopilot = true,
            "--mono" => a.mono = true,
            "--no-color" => a.no_color = true,
            "--print-frame" => a.print_frame = true,
            "--no-save" => a.no_save = true,
            "--replay" => a.replay = Some(next(&mut i)),
            "--record" => a.record = Some(next(&mut i)),
            "--data-dir" => a.data_dir = Some(PathBuf::from(next(&mut i))),
            other => usage_error(&format!("unknown flag {other}")),
        }
        i += 1;
    }
    // NO_COLOR is an environment-wide promise: honour it without a flag.
    if env::var_os("NO_COLOR").is_some() {
        a.no_color = true;
    }
    if a.wave < 1 {
        a.wave = 1;
    }
    a
}

// ---------------------------------------------------------------------------
// Persistence: $XDG_DATA_HOME/pengyos/games/pengy-invaders, under $HOME.
// ---------------------------------------------------------------------------

/// `$XDG_DATA_HOME/pengyos/games/pengy-invaders`, defaulting to
/// `~/.local/share`. Never `/usr`, never the repo, never a sibling of the
/// binary: Tier-1 mutable state lives under the player's home.
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
    base.join("pengyos").join("games").join("pengy-invaders")
}

/// The persisted record: the best score, the wave it reached, and the seed it
/// happened on.
#[derive(Clone, Copy, Debug, Default)]
struct Best {
    score: u64,
    wave: u32,
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
                "wave" => b.wave = v.parse().unwrap_or(0),
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
            b.wave = g.wave;
            b.seed = g.seed;
        }
        if fs::create_dir_all(dir).is_ok() {
            let body = format!(
                "# pengy-invaders high score (Thaw Invaders, pengy-invaders {VERSION})\nscore={}\nwave={}\nseed={}\n",
                b.score, b.wave, b.seed
            );
            let _ = fs::write(dir.join("best.txt"), body);
        }
        b
    }
}

// ---------------------------------------------------------------------------
// Headless: the scripted paths. No window is ever opened.
// ---------------------------------------------------------------------------

/// Build the run the flags describe: seed, starting wave, title screen.
fn new_game(args: &Args) -> Game {
    let mut g = Game::new(args.seed);
    if args.wave > 1 {
        // Start mid-game the same way a cleared wave does: one row lower,
        // faster, ship centred, field clear.
        g.wave = args.wave;
        g.spawn_wave();
        g.state = State::Play;
    }
    g
}

/// Bank the run into the persisted record and put the record's new numbers
/// back into the game, so what the run reports is what the file now holds.
fn bank(dir: &PathBuf, g: &mut Game) {
    let b = Best::save(dir, g);
    g.best_score = b.score;
    g.best_wave = b.wave;
}

/// One line of a replay file → the keys for that tick. A line with characters
/// is those keypresses, applied in order (a held key autorepeats, so a line may
/// carry more than one). A blank line is no input; a `#` line is a comment and
/// is not a tick (the caller skips it).
///
/// `f` is the fire key. The space character is the fire key on the keyboard, and
/// a line of spaces would be indistinguishable from "no input", so the file
/// format spells fire `f` and ignores spaces.
fn replay_keys(line: &str) -> Vec<char> {
    line.trim_end_matches('\r')
        .chars()
        .filter(|c| *c != ' ')
        .map(|c| if c == 'f' { ' ' } else { c })
        .collect()
}

fn run_replay(args: &Args, path: &str, dir: PathBuf) -> i32 {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("pengy-invaders: cannot read replay file {path}: {e}");
            return 66;
        }
    };
    // The seed is a command-line argument, never a file property: a fixture
    // replays on whatever seed the harness asks for.
    let mut g = new_game(args);
    // The session starts on the title screen and the harness dismisses it, the
    // same way --headless does: a recorded script is the input of a *started*
    // run, so record and replay are symmetric.
    g.key(' ');
    if !args.no_save {
        let b = Best::load(&dir);
        g.best_score = b.score;
        g.best_wave = b.wave;
    }
    let mut quit = false;
    let mut stopped_at: Option<usize> = None;
    for (n, raw) in text.lines().enumerate() {
        let line = raw.trim_end_matches('\r');
        if line.starts_with('#') {
            continue; // a comment is not a tick
        }
        // The session is over when the game is over: a finished run is frozen,
        // and `r` on the end screen *restarts* (that is the restart the player
        // wants, and it would silently start a new session in the middle of a
        // replay). So the harness stops at the end tick.
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
    let mut g = new_game(args);
    if !args.no_save {
        let b = Best::load(&dir);
        g.best_score = b.score;
        g.best_wave = b.wave;
    }
    // The autopilot has to start the run: the title screen is not a game.
    g.key(' ');
    let mut script: Vec<String> = Vec::new();
    for _ in 0..args.ticks {
        let keys = g.autopilot_keys();
        if args.record.is_some() {
            // The file format spells fire `f` (see `replay_keys`).
            script.push(keys.replace(' ', "f"));
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
            "# pengy-invaders replay fixture (Thaw Invaders).\n# One line of input per tick; blank = no input; '#' = comment.\n# Play it back with: pengy-invaders --replay <this file> --seed {}\n",
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
            eprintln!("pengy-invaders: cannot write {path}: {e}");
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

/// The part of `--help` that quotes the tuning constants, so the text can
/// never drift from the rules in `src/lib.rs`.
fn help_tail() -> String {
    let mut s = String::from("SCORING\n");
    let _ = write!(
        s,
        "  Aliens by row: bottom {bottom}, then 20, 30, 40, top 50. A cleared wave\
         \n  scores {bonus} x the wave number.\nFIELD\n",
        bottom = 10,
        bonus = 100
    );
    let _ = write!(
        s,
        "  Field {COLS}x{ROWS}, formation {FORM_COLS}x{FORM_ROWS} (2 cells wide, spaced 4),\
         \n  ship on row 23, three 8x4 shields, 3 lives.\nTEMPO\n",
    );
    let _ = write!(
        s,
        "  {TICK_MS} ms per tick. Step period = {BASE_PERIOD} * alive / {ALIENS_TOTAL} / wave, floor 1.\n",
        ALIENS_TOTAL = FORM_COLS * FORM_ROWS
    );
    let _ = write!(
        s,
        "  Laser: 1 in flight, {FIRE_COOLDOWN}-tick cooldown, {LASER_SPEED} rows per tick.\n  Ship: {MOVE_STEP} columns per keypress.\nBOMBS\n"
    );
    let _ = write!(
        s,
        "  Per-alien chance per tick {base}/10000, +{per}/10000 per wave, {MAX_BOMBS} bombs\n  max, 1 row per tick.\n",
        base = BOMB_RATE_BASE,
        per = BOMB_RATE_WAVE
    );
    let _ = write!(s, "LIVES\n  {START_LIVES} lives; a hit clears the bombs and recentres the ship.\n");
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
        println!("pengy-invaders {VERSION}");
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
    eprintln!("pengy-invaders: built without the window (--features cosmic); --headless and --replay work");
    2
}
