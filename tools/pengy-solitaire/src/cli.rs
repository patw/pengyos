//! The Frostbite Solitaire program: the command line, the record file, the
//! headless harness and, with the `cosmic` feature, the window (`app`, a child
//! of this module). It lives in the library so the shared `pengy-games` binary
//! can carry it; `src/main.rs` only hands it the command line.
//!
//! ```text
//! pengy-solitaire                       the window (src/app.rs, libcosmic)
//! pengy-solitaire --headless            the demo player, no window, plain text
//! pengy-solitaire --replay FILE         a recorded session, no window
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
    parse_state, plain_state, Game, RenderOpts, State, DRAW_COUNT, GAME_NAME, GAME_SUBTITLE,
    MAX_HISTORY, REF_HEIGHT, REF_WIDTH, SCORE_FLIP, SCORE_FOUNDATION_TO_TABLEAU, SCORE_RECYCLE,
    SCORE_TO_FOUNDATION, SCORE_WASTE_TO_TABLEAU, TICK_MS,
};

#[cfg(feature = "cosmic")]
#[path = "app.rs"]
mod app;

/// Crate version, from Cargo.toml.
const VERSION: &str = env!("CARGO_PKG_VERSION");

const USAGE: &str = "\
pengy-solitaire — Frostbite Solitaire: Klondike, draw three, unlimited redeals.
The Windows 3.0 pack-in, on the COSMIC desktop.

USAGE
  pengy-solitaire              open the window
  pengy-solitaire [FLAG]...

FLAGS
  --seed N          Seed the deal (default 1). Same seed + same inputs = the
                    same game, bit for bit.
  --headless        No TTY, no rendering, no keys: let the built-in demo player
                    run for --ticks ticks, print the plain-text state, exit 0.
                    This is how the game is proven to work where there is no
                    pty (the repo gate, pengyos-qemu-verify).
  --replay FILE     Play a recorded session headlessly: one line of input per
                    tick, a blank line means no input, a '#' line is a comment
                    and is not a tick. Stops at the win tick, at a quit key, or
                    at EOF. Prints the final state as key=value lines and exits
                    0. This is the acceptance harness.
  --state FILE      Start from a saved position (the format `--save` writes)
                    instead of dealing. Use it with --replay to pin a fixture
                    deal: the one-move-from-winning fixtures live this way.
  --save FILE       With --headless/--replay: write the final position to FILE.
  --record FILE     With --headless: write the demo player's input script to
                    FILE in --replay format (used to make the fixtures).
  --ticks N         Ticks for the headless path (default 4000).
  --autopilot       In the window: let the built-in demo player play. Demo mode.
  --mono            With --print-frame: ASCII-only text (no box drawing, no
                    suit characters).
  --no-color        Accepted for compatibility; the text frame has no colour.
  --print-frame     With --headless/--replay: also print the final position
                    as a text frame (a debugging aid).
  --width N         Width of the --print-frame text (default 80).
  --height N        Height of the --print-frame text (default 24).
  --data-dir DIR    Where the record is kept (default
                    $XDG_DATA_HOME/pengyos/games/pengy-solitaire, i.e.
                    ~/.local/share/pengyos/games/pengy-solitaire).
  --no-save         Do not read or write the record (tests, sandboxes).
  --help, -h        This text.
  --version         Version.

MOUSE AND KEYS (the window)
  drag a card       Move it, and every card on top of it, to another pile.
                    Dropped on a column, the part of the run that fits moves.
  click the stock   Draw three; an empty stock recycles the waste (-100).
  double-click      Send a card to its foundation, when it can go.
  F                 Auto-collect every card that is safe to collect.
  Ctrl+Z            Undo (up to 50 deep).
  Ctrl+N / F2       New deal.
  Ctrl+Q            Quit.

SCORING
  waste -> tableau +5, any -> foundation +10, tableau flip +5,
  foundation -> tableau -15, recycling the waste -100, floored at zero.
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
    state: Option<String>,
    save: Option<String>,
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
            state: None,
            save: None,
            record: None,
            ticks: 4_000,
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
    eprintln!("pengy-solitaire: {msg}");
    eprintln!("pengy-solitaire: try --help");
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
            "--state" => a.state = Some(value("file")),
            "--save" => a.save = Some(value("file")),
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
    if a.ticks == 0 {
        usage_error("--ticks must be >= 1");
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
    if a.save.is_some() && a.replay.is_none() && !a.headless {
        usage_error("--save needs --headless or --replay");
    }
    if a.print_frame && !(a.headless || a.replay.is_some()) {
        usage_error("--print-frame needs --headless or --replay");
    }
    a
}

// ---------------------------------------------------------------------------
// The record — Tier-1 mutable state, under $HOME only
// ---------------------------------------------------------------------------

/// `$XDG_DATA_HOME/pengyos/games/pengy-solitaire`, defaulting to
/// `~/.local/share`. Never `/usr`, never the repo, never a sibling of the
/// binary.
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
    base.join("pengyos")
        .join("games")
        .join("pengy-solitaire")
}

/// The persisted record: best score, the fastest win, games played, wins.
#[derive(Clone, Copy, Debug, Default)]
struct Best {
    score: u64,
    moves: u64,
    ms: u64,
    games: u64,
    wins: u64,
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
                "moves" => b.moves = v.parse().unwrap_or(0),
                "ms" => b.ms = v.parse().unwrap_or(0),
                "games" => b.games = v.parse().unwrap_or(0),
                "wins" => b.wins = v.parse().unwrap_or(0),
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
        if g.score as u64 > b.score {
            b.score = g.score as u64;
            b.seed = g.seed;
            b.moves = g.moves;
        }
        if g.state == State::Win {
            b.wins += 1;
            if b.ms == 0 || g.ms < b.ms {
                b.ms = g.ms;
            }
        }
        if fs::create_dir_all(dir).is_ok() {
            let body = format!(
                "# pengy-solitaire record (Frostbite Solitaire, pengy-solitaire {VERSION})\nscore={}\nmoves={}\nms={}\ngames={}\nwins={}\nseed={}\n",
                b.score, b.moves, b.ms, b.games, b.wins, b.seed
            );
            let _ = fs::write(dir.join("best.txt"), body);
        }
        b
    }
}

/// Bank the game into the record and put the record's new numbers back into the
/// game, so what the run reports is what the file now holds.
fn bank(dir: &PathBuf, g: &mut Game) {
    let b = Best::save(dir, g);
    apply_best(g, &b);
}

fn apply_best(g: &mut Game, b: &Best) {
    g.best_score = b.score;
    g.best_moves = b.moves;
    g.best_ms = b.ms;
    g.best_games = b.games;
    g.best_wins = b.wins;
}

fn start_game(args: &Args) -> Result<Game, i32> {
    match &args.state {
        Some(path) => {
            let text = match fs::read_to_string(path) {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("pengy-solitaire: cannot read state file {path}: {e}");
                    return Err(66);
                }
            };
            match parse_state(&text) {
                Some(mut g) => {
                    // The seed is a command-line argument, never a file
                    // property: a fixture replays on whatever seed the harness
                    // asks for, and the hash covers it.
                    g.seed = args.seed;
                    g.rng = crate::Rng::new(args.seed);
                    Ok(g)
                }
                None => {
                    eprintln!("pengy-solitaire: state file {path} is not a position");
                    Err(66)
                }
            }
        }
        None => Ok(Game::new(args.seed)),
    }
}

/// One line of a replay file → the keys for that tick. A line with characters
/// is those keypresses, applied in order (a held key autorepeats, so a line may
/// carry more than one). A blank line is no input; a `#` line is a comment and
/// is not a tick. The space key is the take/drop key, and a trailing space is
/// the kind of thing an editor eats, so the file spells it `_`.
fn replay_keys(line: &str) -> Vec<char> {
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
            eprintln!("pengy-solitaire: cannot read replay file {path}: {e}");
            return 66;
        }
    };
    let mut g = match start_game(args) {
        Ok(g) => g,
        Err(code) => return code,
    };
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
        // The session is over when the game is won: a finished game is frozen,
        // and `n` on the win screen starts a new deal, which would silently
        // start a second session in the middle of a replay.
        if g.state == State::Win {
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
    if let Some(path) = &args.save {
        if let Err(e) = write_state(path, &g) {
            eprintln!("pengy-solitaire: {e}");
            return 66;
        }
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
        print!("{}", g.render(render_opts(args)));
    }
    0
}

fn write_state(path: &str, g: &Game) -> Result<(), String> {
    let p = PathBuf::from(path);
    if let Some(parent) = p.parent() {
        if !parent.as_os_str().is_empty() {
            let _ = fs::create_dir_all(parent);
        }
    }
    fs::write(&p, g.state_text()).map_err(|e| format!("cannot write {path}: {e}"))
}

fn run_headless(args: &Args, dir: PathBuf) -> i32 {
    let mut g = match start_game(args) {
        Ok(g) => g,
        Err(code) => return code,
    };
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
        if g.state == State::Win {
            break;
        }
    }
    if !args.no_save {
        bank(&dir, &mut g);
    }
    if let Some(path) = &args.record {
        let header = format!(
            "# pengy-solitaire replay fixture (Frostbite Solitaire, Klondike draw 3).\n# One line of input per tick; blank = no input; '#' = comment; '_' = the space key.\n# Play it back with: pengy-solitaire --replay <this file> --seed {}\n",
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
            eprintln!("pengy-solitaire: cannot write {path}: {e}");
            return 66;
        }
        println!("recorded={} ticks={}", path, script.len());
    }
    if let Some(path) = &args.save {
        if let Err(e) = write_state(path, &g) {
            eprintln!("pengy-solitaire: {e}");
            return 66;
        }
    }
    print!("{}", plain_state(&g));
    if args.print_frame {
        print!("{}", g.render(render_opts(args)));
    }
    0
}

/// The text frame `--print-frame` prints: plain text at the requested size.
fn render_opts(args: &Args) -> RenderOpts {
    RenderOpts {
        width: args.width,
        height: args.height,
        mono: args.mono,
        color: false,
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

/// The part of `--help` that quotes the rules and the tuning constants, so the
/// text can never drift from the rules in `src/lib.rs`.
fn help_tail() -> String {
    let mut s = String::new();
    let _ = write!(
        s,
        "RULES\n  \
           Seven tableau columns of 1..7 cards (top face up), four foundations, a\n  \
           24-card stock and a waste. Draw {DRAW_COUNT} at a time; unlimited passes: an\n  \
           empty stock recycles the waste back, reversed and face down.\n  \
           Tableau builds down by rank, alternating colours, and a run moves as a unit.\n  \
           Foundations build up by suit from the Ace. Only a King goes on an empty column.\n  \
           A tableau card flips the moment it is uncovered. All 52 on the foundations wins.\n",
    );
    let _ = write!(
        s,
        "SCORING\n  \
           waste->tableau {SCORE_WASTE_TO_TABLEAU}, any->foundation {SCORE_TO_FOUNDATION}, flip {SCORE_FLIP},\n  \
           foundation->tableau {SCORE_FOUNDATION_TO_TABLEAU}, recycle {SCORE_RECYCLE}, floored at 0.\n",
        SCORE_RECYCLE = -SCORE_RECYCLE
    );
    let _ = write!(
        s,
        "TUNING\n  \
           Tick {TICK_MS} ms, draw {DRAW_COUNT}, undo depth {MAX_HISTORY}.\n",
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
        println!("pengy-solitaire {VERSION} ({GAME_NAME} — {GAME_SUBTITLE})");
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

#[cfg(feature = "cosmic")]
fn run_window(args: &Args, dir: PathBuf) -> i32 {
    let game = match start_game(args) {
        Ok(g) => g,
        Err(code) => return code,
    };
    let flags = app::Flags {
        game,
        dir: (!args.no_save).then_some(dir),
        autopilot: args.autopilot,
    };
    let settings = cosmic::app::Settings::default()
        .size(cosmic::iced::Size::new(1040.0, 760.0))
        .size_limits(cosmic::iced::Limits::NONE.min_width(520.0).min_height(420.0));
    match cosmic::app::run::<app::App>(settings, flags) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("pengy-solitaire: {err}");
            1
        }
    }
}

#[cfg(not(feature = "cosmic"))]
fn run_window(_args: &Args, _dir: PathBuf) -> i32 {
    eprintln!("pengy-solitaire: built without the window (--features cosmic); --headless and --replay work");
    2
}
