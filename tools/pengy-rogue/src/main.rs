//! pengy-rogue — the terminal front-end for Ice Rogue.
//!
//! This file owns everything that touches the outside world: the clock, the
//! keyboard, the screen, the best-score file. Every rule lives in `src/lib.rs`;
//! the loop below only ever calls `Game::key`, `Game::tick`, `Game::render` and
//! the persistence helpers here.
//!
//! Constraints that shape this file (spec.md "Constraints"):
//! * std only — raw keyboard via `stty raw -echo` (`std::process::Command`),
//!   a stdin reader thread feeding the loop over `mpsc`, ANSI escapes on stdout;
//! * never require a TTY: `--headless` / `--replay` / a non-tty stdin run the
//!   scripted path, print plain text and exit 0 (the repo gate and
//!   pengyos-qemu-verify have no pty);
//! * the terminal is restored from a `Drop` guard *and* a panic hook, so a
//!   crash cannot leave the user's shell in raw mode.

use std::env;
use std::fmt::Write as _;
use std::fs;
use std::io::{self, IsTerminal, Read, Write};
use std::panic;
use std::path::PathBuf;
use std::process::{self, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::thread;
use std::time::{Duration, Instant};

use pengy_rogue::{
    parse_state, plain_state, Game, RenderOpts, State, ATTACK_DIE_BONUS, ATTACK_DIE_FACES,
    FISH_HEAL, FLOORS, FOV_RADIUS, GAME_NAME, GAME_SUBTITLE, GOLD_MAX, GOLD_MIN, LEGEND,
    MAP_H, MAP_W, PARKA_DEFENCE, REF_HEIGHT, REF_WIDTH, ROOMS_MAX, ROOMS_MIN, SCORE_PER_FLOOR,
    SCORE_PER_KILL, START_DEFENCE, START_HP, START_MAX_HP, START_WEAPON, TICK_MS,
};

/// Crate version, from Cargo.toml.
const VERSION: &str = env!("CARGO_PKG_VERSION");

const USAGE: &str = "\
pengy-rogue — Ice Rogue: Rogue (1980), the original dungeon crawl.
Five floors down, one penguin, permanent death. Rust, std only.

USAGE
  pengy-rogue [FLAG]...

FLAGS
  --seed N          Seed the dungeon (default 1). Same seed + same inputs = the
                    same five floors, the same monsters, the same combat rolls,
                    bit for bit.
  --headless        No TTY, no rendering, no keys: let the built-in demo penguin
                    run for --ticks ticks, print the plain-text state, exit 0.
                    This is how the game is proven to work where there is no
                    pty (the repo gate, pengyos-qemu-verify).
  --replay FILE     Play a recorded session headlessly: one line of input per
                    tick, a blank line means no input, a '#' line is a comment
                    and is not a tick. Stops at a terminal state, at a quit key,
                    or at EOF. Prints the final state as key=value lines and
                    exits 0. This is the acceptance harness.
  --state FILE      Start from a saved position (the format --save writes)
                    instead of digging a new dungeon. Use it with --replay to
                    pin a fixture: the boss fixtures live this way.
  --save FILE       With --headless/--replay: write the final position to FILE.
  --record FILE     With --headless: write the demo penguin's input script to
                    FILE in --replay format (used to make the fixtures).
  --ticks N         Ticks for the headless path (default 4000).
  --autopilot       Interactive: let the built-in demo penguin play. Demo mode.
  --mono            ASCII-only glyphs (no box drawing, no dim), no colour. For
                    a terminal whose locale cannot render them.
  --no-color        No ANSI colour. NO_COLOR in the environment is honoured
                    automatically and implies this.
  --print-frame     With --headless/--replay: also print the final frame.
  --width N         Window width for the headless frame (default 80).
  --height N        Window height for the headless frame (default 24).
  --data-dir DIR    Where the record is kept (default
                    $XDG_DATA_HOME/pengyos/games/pengy-rogue, i.e.
                    ~/.local/share/pengyos/games/pengy-rogue).
  --no-save         Do not read or write the record (tests, sandboxes).
  --help, -h        This text.
  --version         Version.

KEYS
  h j k l           Move left, down, up, right. The arrow keys work too.
  y u b n           The four diagonals: up-left, up-right, down-left,
                    down-right.
  .                 Wait one turn.
  g                 Pick up the item under your feet.
  i                 Inventory. Costs no turn.
  >                 Descend, when you are standing on the stairs.
  q                 Quit, restoring the terminal. Ctrl-C quits too.
  anything else     Ignored. Escape is not a quit key: terminals send the
                    arrow keys as ESC [ A, so a bare ESC must stay harmless.

SCORING
  score = floor * 100 + gold + kills. Permadeath: a dead penguin is a dead
  run, and the score is what the run earned.
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
    eprintln!("pengy-rogue: {msg}");
    eprintln!("pengy-rogue: try --help");
    process::exit(2);
}

fn parse_args() -> Args {
    let mut a = Args::default();
    let argv: Vec<String> = env::args().skip(1).collect();
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

/// `$XDG_DATA_HOME/pengyos/games/pengy-rogue`, defaulting to `~/.local/share`.
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
    base.join("pengyos")
        .join("games")
        .join("pengy-rogue")
}

/// The persisted record: best score, the deepest floor, the most kills, games
/// played, wins, and the seed that scored it.
#[derive(Clone, Copy, Debug, Default)]
struct Best {
    score: u64,
    floor: u64,
    kills: u64,
    turns: u64,
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
                "floor" => b.floor = v.parse().unwrap_or(0),
                "kills" => b.kills = v.parse().unwrap_or(0),
                "turns" => b.turns = v.parse().unwrap_or(0),
                "games" => b.games = v.parse().unwrap_or(0),
                "wins" => b.wins = v.parse().unwrap_or(0),
                "seed" => b.seed = v.parse().unwrap_or(0),
                _ => {}
            }
        }
        b
    }

    /// Merge this run into the record and write it back. Every failure is
    /// ignored: a read-only `$HOME` must never cost the player a run.
    fn save(dir: &PathBuf, g: &Game) -> Best {
        let mut b = Best::load(dir);
        b.games += 1;
        let score = g.score().max(0) as u64;
        if score > b.score {
            b.score = score;
            b.seed = g.seed;
            b.floor = g.deepest as u64;
            b.kills = g.kills as u64;
            b.turns = g.turns;
        }
        if g.state == State::Win {
            b.wins += 1;
        }
        b.floor = b.floor.max(g.deepest as u64);
        b.kills = b.kills.max(g.kills as u64);
        if fs::create_dir_all(dir).is_ok() {
            let body = format!(
                "# pengy-rogue record (Ice Rogue, pengy-rogue {VERSION})\nscore={}\nfloor={}\nkills={}\nturns={}\ngames={}\nwins={}\nseed={}\n",
                b.score, b.floor, b.kills, b.turns, b.games, b.wins, b.seed
            );
            let _ = fs::write(dir.join("best.txt"), body);
        }
        b
    }
}

/// Bank the run into the record and put the record's new numbers back into the
/// game, so what the run reports is what the file now holds.
fn bank(dir: &PathBuf, g: &mut Game) {
    let b = Best::save(dir, g);
    apply_best(g, &b);
}

fn apply_best(g: &mut Game, b: &Best) {
    g.best_score = b.score;
    g.best_floor = b.floor;
    g.best_kills = b.kills;
    g.best_games = b.games;
    g.best_wins = b.wins;
    g.best_seed = b.seed;
}

// ---------------------------------------------------------------------------
// Terminal: stty raw, the alternate screen, the restore guard
// ---------------------------------------------------------------------------

/// Run `stty` on our own stdin. True when it succeeded.
fn stty(args: &[&str]) -> bool {
    Command::new("stty")
        .args(args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Terminal size from `stty size` ("rows cols"); `None` when stty is missing.
fn terminal_size() -> Option<(usize, usize)> {
    let out = Command::new("stty")
        .arg("size")
        .stdin(Stdio::inherit())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let mut it = s.split_whitespace();
    let rows = it.next()?.parse().ok()?;
    let cols = it.next()?.parse().ok()?;
    // A pty with no size set (a `script`-allocated one, say) reports 0x0.
    // Treat that as "unknown" and fall back to the requested window.
    if rows < 1 || cols < 1 {
        return None;
    }
    Some((rows, cols))
}

const ENTER_ALT: &str = "\x1b[?1049h";
const LEAVE_ALT: &str = "\x1b[?1049l";
const HIDE_CURSOR: &str = "\x1b[?25l";
const SHOW_CURSOR: &str = "\x1b[?25h";

/// Restores the terminal on drop, i.e. on every normal exit, on `q`, and on a
/// panic (unwinding runs destructors — which is why the release profile does
/// not set `panic = "abort"`).
struct TerminalGuard {
    stty: bool,
}

impl TerminalGuard {
    /// Take over the terminal: raw/-echo, the alternate screen, hidden cursor.
    /// Anything that fails is simply skipped — the game then runs on the normal
    /// screen buffer with cooked input, which is degraded, not broken.
    fn take() -> TerminalGuard {
        let stty = stty(&["raw", "-echo"]);
        let mut out = io::stdout();
        let _ = write!(out, "{ENTER_ALT}{HIDE_CURSOR}");
        let _ = out.flush();
        TerminalGuard { stty }
    }
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let mut out = io::stdout();
        let _ = write!(out, "{SHOW_CURSOR}{LEAVE_ALT}");
        let _ = out.flush();
        if self.stty {
            stty(&["sane"]);
            self.stty = false;
        }
    }
}

/// A panic must not strand the user's shell in raw mode: restore first, then
/// let the default hook print the report.
fn install_panic_hook() {
    let prior = panic::take_hook();
    panic::set_hook(Box::new(move |info| {
        let mut out = io::stdout();
        let _ = write!(out, "{SHOW_CURSOR}{LEAVE_ALT}");
        let _ = out.flush();
        stty(&["sane"]);
        prior(info);
    }));
}

/// Read stdin bytes on a worker thread and hand them to the loop as chars.
/// `std` has no non-blocking read, so the thread is the only way to keep the
/// tick loop on a fixed timestep instead of blocking on the keyboard.
fn spawn_key_reader() -> Receiver<char> {
    let (tx, rx): (Sender<char>, Receiver<char>) = mpsc::channel();
    thread::spawn(move || {
        let mut buf = [0u8; 64];
        let mut stdin = io::stdin();
        loop {
            match stdin.read(&mut buf) {
                Ok(0) => return,
                Ok(n) => {
                    for &b in &buf[..n] {
                        if b < 0x80 {
                            if tx.send(b as char).is_err() {
                                return;
                            }
                        }
                    }
                }
                Err(_) => return,
            }
        }
    });
    rx
}

/// Translate the terminal's arrow-key escape sequences into the letters the
/// library understands (`h` left, `j` down, `k` up, `l` right). A bare Escape
/// is swallowed: it is deliberately not a quit key, because terminals send the
/// arrows as `ESC [ A`.
struct KeyFilter {
    /// 0 = idle, 1 = saw ESC, 2 = saw `ESC [`
    state: u8,
}

impl KeyFilter {
    fn new() -> Self {
        KeyFilter { state: 0 }
    }

    /// The key to hand the game, `None` when this byte is part of (or the
    /// start of) an escape sequence.
    fn push(&mut self, c: char) -> Option<char> {
        match self.state {
            0 => {
                if c == '\x1b' {
                    self.state = 1;
                    None
                } else {
                    Some(c)
                }
            }
            1 => {
                self.state = if c == '[' || c == 'O' { 2 } else { 0 };
                None
            }
            _ => {
                self.state = 0;
                match c {
                    'A' => Some('h'),
                    'B' => Some('j'),
                    'C' => Some('l'),
                    'D' => Some('k'),
                    _ => None,
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Starting position: a new dungeon, or a saved position
// ---------------------------------------------------------------------------

/// The starting position of a run: `--state FILE` loads a saved position,
/// otherwise the seed digs a new one. A broken state file is exit 66, never a
/// panic.
fn start_game(args: &Args) -> Result<Game, i32> {
    match &args.state {
        Some(path) => {
            let text = match fs::read_to_string(path) {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("pengy-rogue: cannot read state file {path}: {e}");
                    return Err(66);
                }
            };
            match parse_state(&text) {
                Some(mut g) => {
                    // The seed is a command-line argument, never a file
                    // property: a fixture replays on whatever seed the harness
                    // asks for, and the hash covers it.
                    g.seed = args.seed;
                    g.rng = pengy_rogue::Rng::new(args.seed);
                    Ok(g)
                }
                None => {
                    eprintln!("pengy-rogue: state file {path} is not a position");
                    Err(66)
                }
            }
        }
        None => Ok(Game::new(args.seed)),
    }
}

// ---------------------------------------------------------------------------
// The interactive loop
// ---------------------------------------------------------------------------

/// Paint one frame: home, write the whole frame, flush. One write per frame,
/// no per-cell addressing — that is what keeps it flicker-free.
fn paint(out: &mut impl Write, g: &Game, opts: RenderOpts) {
    let frame = pengy_rogue::render(g, opts);
    let _ = write!(out, "\x1b[H{frame}");
    let _ = out.flush();
}

fn run_interactive(args: &Args, opts: RenderOpts, dir: PathBuf) -> i32 {
    let mut g = match start_game(args) {
        Ok(g) => g,
        Err(code) => return code,
    };
    if !args.no_save {
        apply_best(&mut g, &Best::load(&dir));
    }
    let guard = TerminalGuard::take();
    let rx = spawn_key_reader();
    let mut filter = KeyFilter::new();
    let mut out = io::stdout();
    let tick = Duration::from_millis(TICK_MS);
    let mut deadline = Instant::now() + tick;
    let mut quit = false;

    while !quit {
        paint(&mut out, &g, opts);
        // Drain keys until the tick deadline; recv_timeout sleeps, so this is a
        // fixed timestep with no busy-wait.
        while !quit {
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            match rx.recv_timeout(deadline - now) {
                Ok(c) => {
                    if c == '\x03' {
                        // Ctrl-C quits.
                        quit = true;
                        break;
                    }
                    if let Some(k) = filter.push(c) {
                        quit = g.key(k);
                    }
                }
                Err(RecvTimeoutError::Timeout) | Err(RecvTimeoutError::Disconnected) => break,
            }
        }
        if quit {
            break;
        }
        if args.autopilot && g.state == State::Play {
            for k in g.autopilot_keys().chars() {
                g.key(k);
            }
        }
        g.tick();

        if g.state != State::Play {
            // The end of a run is a two-second event, not a ten-second one:
            // paint the result, bank it, and hand control back.
            if !args.no_save {
                bank(&dir, &mut g);
            }
            paint(&mut out, &g, opts);
            let _ = out.flush();
            match rx.recv_timeout(Duration::from_secs(2)) {
                Ok(c) => quit = c == '\x03' || g.key(c),
                Err(_) => quit = true,
            }
        }
        deadline = Instant::now() + tick;
    }

    if !args.no_save {
        bank(&dir, &mut g);
    }
    drop(guard);
    0
}

// ---------------------------------------------------------------------------
// Headless: the scripted paths. No TTY is ever required.
// ---------------------------------------------------------------------------

/// One line of a replay file → the keys for that tick. A line with characters
/// is those keypresses, applied in order (a held key autorepeats, so a line may
/// carry more than one). A blank line is no input; a `#` line is a comment and
/// is not a tick.
fn replay_keys(line: &str) -> Vec<char> {
    line.trim_end().chars().filter(|c| *c != ' ').collect()
}

fn run_replay(args: &Args, path: &str, dir: PathBuf) -> i32 {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("pengy-rogue: cannot read replay file {path}: {e}");
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
        // The run is over when the penguin dies or The Thaw breaks: a finished
        // run is frozen, and a script that keeps typing must not keep playing.
        if g.state != State::Play {
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
            eprintln!("pengy-rogue: {e}");
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
        print!("{}", pengy_rogue::render(&g, render_opts(args, false)));
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
        if g.state != State::Play {
            break;
        }
        let keys = g.autopilot_keys();
        if args.record.is_some() {
            script.push(keys.clone());
        }
        for c in keys.chars() {
            g.key(c);
        }
        g.tick();
    }
    if !args.no_save {
        bank(&dir, &mut g);
    }
    if let Some(path) = &args.record {
        let header = format!(
            "# pengy-rogue replay fixture (Ice Rogue, Rogue 1980, seed {}).\n# One line of input per tick; blank = no input; '#' = comment.\n# Play it back with: pengy-rogue --replay <this file> --seed {}\n",
            args.seed, args.seed
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
            eprintln!("pengy-rogue: cannot write {path}: {e}");
            return 66;
        }
        println!("recorded={} ticks={}", path, script.len());
    }
    if let Some(path) = &args.save {
        if let Err(e) = write_state(path, &g) {
            eprintln!("pengy-rogue: {e}");
            return 66;
        }
    }
    print!("{}", plain_state(&g));
    if args.print_frame {
        print!("{}", pengy_rogue::render(&g, render_opts(args, false)));
    }
    0
}

fn render_opts(args: &Args, tty: bool) -> RenderOpts {
    let (width, height) = if tty {
        match terminal_size() {
            Some((rows, cols)) => (cols, rows),
            None => (args.width, args.height),
        }
    } else {
        (args.width, args.height)
    };
    RenderOpts {
        width,
        height,
        mono: args.mono,
        // With no terminal there is nobody to render colour for: the headless
        // frame is plain text.
        color: !args.no_color && tty,
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
           {ROOMS_MIN}..{ROOMS_MAX} non-overlapping rooms per floor, joined by L-shaped corridors,\n  \
           always connected: the stairs are reachable from the entry, proved by a flood\n  \
           fill at generation time. Five floors; floor 5 holds The Thaw.\n  \
           You are @, walls are #, floor is ., stairs are >. Monsters are r rat (2 hp, 1 dmg),\n  \
           c ice crab (4 hp, 2 dmg), w wolf (6 hp, 3 dmg), T The Thaw (20 hp, 4 dmg).\n  \
           Items: ! fish heals {FISH_HEAL}, ? scroll maps the floor, $ gold {GOLD_MIN}..{GOLD_MAX} scores,\n  \
           + parka +{PARKA_DEFENCE} defence. One item per tile. Walk into a monster to hit it.\n  \
           Your attack roll is 1d{ATTACK_DIE_FACES}+{ATTACK_DIE_BONUS}+weapon - target defence, minimum 0; a monster's damage\n  \
           is its damage - your defence, minimum 0. You start with {START_HP}/{START_MAX_HP} hp, attack 1d4+{START_WEAPON}\n  \
           and defence {START_DEFENCE}. Permadeath: no save, no undo.\n",
        ATTACK_DIE_FACES = ATTACK_DIE_FACES,
        ATTACK_DIE_BONUS = ATTACK_DIE_BONUS,
    );
    let _ = write!(
        s,
        "FIELD OF VIEW\n  \
           Radius {FOV_RADIUS} with line of sight; walls block it. Tiles ever seen stay drawn dim\n  \
           (a plain '.' with --mono); tiles you have never seen are blank.\n",
    );
    let _ = write!(
        s,
        "SCORING\n  \
           score = floor * {SCORE_PER_FLOOR} + gold + kills * {SCORE_PER_KILL}.\n",
    );
    let _ = write!(
        s,
        "TUNING\n  \
           Map {MAP_W}x{MAP_H}, floors {FLOORS}, tick {TICK_MS} ms, reference window {REF_WIDTH}x{REF_HEIGHT}.\nKEYS\n  {LEGEND}\n",
    );
    s
}

fn main() {
    let args = parse_args();
    if args.help {
        print!("{USAGE}{}", help_tail());
        process::exit(0);
    }
    if args.version {
        println!("pengy-rogue {VERSION} ({GAME_NAME} — {GAME_SUBTITLE})");
        process::exit(0);
    }

    install_panic_hook();
    let dir = data_dir(args.data_dir.clone());

    // No TTY, --headless, or a scripted session: never touch the terminal,
    // never read keys, print plain text, exit 0.
    let tty = io::stdin().is_terminal() && io::stdout().is_terminal();
    if let Some(path) = args.replay.clone() {
        process::exit(run_replay(&args, &path, dir));
    }
    if args.headless || !tty {
        process::exit(run_headless(&args, dir));
    }
    process::exit(run_interactive(&args, render_opts(&args, true), dir));
}
