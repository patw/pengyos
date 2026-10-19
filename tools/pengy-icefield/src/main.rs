//! pengy-icefield — the terminal front-end for Icefield (Minesweeper).
//!
//! This file owns everything that touches the outside world: the clock, the
//! keyboard, the screen, the record file. Every rule lives in `src/lib.rs`; the
//! loop below only ever calls `Game::key`, `Game::tick`, `Game::render` and the
//! persistence helpers here.
//!
//! Constraints that shape this file (spec.md "Constraints"):
//! * std only — raw keyboard via `stty raw -echo` (`std::process::Command`),
//!   a stdin reader thread feeding an `mpsc` channel, ANSI escapes on stdout;
//! * never require a TTY: `--headless` / `--replay` / a non-tty stdin run the
//!   scripted path, print plain text and exit 0 (the repo gate has no pty);
//! * the terminal is restored from a `Drop` guard *and* a panic hook, so a
//!   crash cannot leave the user's shell in raw mode.

use std::env;
use std::fs;
use std::io::{self, IsTerminal, Read, Write};
use std::panic;
use std::path::PathBuf;
use std::process::{self, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::thread;
use std::time::{Duration, Instant};

use pengy_icefield::{
    plain_state, Game, RenderOpts, State, CUSTOM_PRESET, DEFAULT_PRESET, MAX_HEIGHT,
    MAX_WIDTH, MIN_HEIGHT, MIN_WIDTH, PRESETS, TICK_MS,
};

/// Crate version, from Cargo.toml.
const VERSION: &str = env!("CARGO_PKG_VERSION");

const USAGE: &str = "\
pengy-icefield — Icefield: clear the icefield without stepping through it.
Numbers count the crevasses touching that cell. (Minesweeper, the pack-in.)

USAGE
  pengy-icefield [FLAG]...

FLAGS
  --seed N          Seed the run (default 1). Same seed + same inputs = the
                    same run, bit for bit.
  --difficulty D    beginner | intermediate | expert | 1 | 2 | 3 (default 1).
  --width N         Custom board width, 4..=40. With --height (and --mines)
                    this makes a custom board and leaves the presets.
  --height N        Custom board height, 3..=24.
  --mines N         Custom crevasse count, 1..=(width*height - 1).
  --headless        No TTY, no rendering, no keys: run the built-in solver for
                    --ticks ticks, print the plain-text state, exit 0. This is
                    how the game is proven to work where there is no pty (the
                    repo gate, pengyos-qemu-verify).
  --replay FILE     Play a recorded session headlessly: one line of input per
                    tick, a blank line means no input, a '#' line is a comment
                    and is not a tick. Stops at the end of the run or at EOF.
                    Prints the final state as key=value lines and exits 0.
                    This is the acceptance harness.
  --record FILE     With --headless: write the solver's input script to FILE in
                    --replay format (used to make the fixtures).
  --ticks N         Ticks for the headless/solver path (default 4000).
  --autopilot       Interactive: let the built-in solver play. Demo mode.
  --mono            ASCII-only glyphs (no box-drawing characters), no colour.
  --no-color        No ANSI colour. NO_COLOR in the environment is honoured
                    automatically and implies this.
  --print-frame     With --headless/--replay: also print the final frame.
  --cols N          Window width for the headless frame (default 80).
  --rows N          Window height for the headless frame (default 24).
  --data-dir DIR    Where the records are kept (default
                    $XDG_DATA_HOME/pengyos/games/pengy-icefield, i.e.
                    ~/.local/share/pengyos/games/pengy-icefield).
  --no-save         Do not read or write the records (tests, sandboxes).
  --help, -h        This text.
  --version         Version.

KEYS
  arrows / h j k l   Move the cursor (clamps at the edges; WASD works too).
  space              Reveal the cell under the cursor. The first reveal of a
                     run is always safe: the crevasses are dealt after it.
  f                  Flag the cell under the cursor (a crevasse you are sure
                     of). Press again to take the flag off.
  c                  Chord: on a revealed number whose flag count equals it,
                     open every unflagged neighbour. A wrong flag loses.
  1 / 2 / 3          New run: beginner 9x9/10, intermediate 16x16/40,
                     expert 30x16/99.
  n                  New run on the same difficulty (next seed).
  q                  Quit (restores the terminal). Ctrl-C quits too.
  anything else      Ignored. Escape is not a quit key: terminals send the
                     arrow keys as ESC [ A, so a bare ESC must stay harmless.

RULES IN ONE BREATH
  Open a solid cell and it shows how many crevasses touch it, counting the 8
  cells around it. A 0 opens its whole contiguous zero region and every number
  on that region's border, so one good click clears a bay of the field. Flag
  the cells you know are crevasses, then chord on a satisfied number to open
  everything left around it in one key. The first click of a run never opens a
  crevasse and never opens a cell next to one. Open every solid cell to win;
  the timer runs from that first click to the end of the run. Best time per
  difficulty and best score persist.
";

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/// Parsed command line. Every flag in `USAGE` maps to exactly one field.
struct Args {
    seed: u64,
    seed_given: bool,
    difficulty: usize,
    width: usize,
    height: usize,
    mines: usize,
    custom: bool,
    headless: bool,
    replay: Option<String>,
    record: Option<String>,
    ticks: u64,
    autopilot: bool,
    mono: bool,
    no_color: bool,
    print_frame: bool,
    cols: usize,
    rows: usize,
    data_dir: Option<PathBuf>,
    no_save: bool,
    help: bool,
    version: bool,
}

impl Default for Args {
    fn default() -> Self {
        let p = PRESETS[DEFAULT_PRESET];
        Self {
            seed: 1,
            seed_given: false,
            difficulty: DEFAULT_PRESET,
            width: p.width,
            height: p.height,
            mines: p.mines,
            custom: false,
            headless: false,
            replay: None,
            record: None,
            ticks: 4000,
            autopilot: false,
            mono: false,
            no_color: false,
            print_frame: false,
            cols: 80,
            rows: 24,
            data_dir: None,
            no_save: false,
            help: false,
            version: false,
        }
    }
}

/// Usage error: one line on stderr, exit 2 (the repo's CLI convention).
fn usage_error(msg: &str) -> ! {
    eprintln!("pengy-icefield: {msg}");
    eprintln!("pengy-icefield: try --help");
    process::exit(2);
}

/// `--difficulty` accepts the preset index, its number key, or its name.
fn parse_difficulty(v: &str) -> Option<usize> {
    match v.trim().to_ascii_lowercase().as_str() {
        "1" | "beginner" => Some(0),
        "2" | "intermediate" => Some(1),
        "3" | "expert" => Some(2),
        _ => None,
    }
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
            "--difficulty" => {
                let v = value("difficulty");
                a.difficulty = parse_difficulty(&v)
                    .unwrap_or_else(|| usage_error(&format!("--difficulty: {v} is not a difficulty (beginner, intermediate, expert, 1, 2, 3)")));
            }
            "--width" => {
                let v = value("number");
                a.width = v
                    .parse::<usize>()
                    .unwrap_or_else(|_| usage_error(&format!("--width: not a number: {v}")));
                a.custom = true;
            }
            "--height" => {
                let v = value("number");
                a.height = v
                    .parse::<usize>()
                    .unwrap_or_else(|_| usage_error(&format!("--height: not a number: {v}")));
                a.custom = true;
            }
            "--mines" => {
                let v = value("number");
                a.mines = v
                    .parse::<usize>()
                    .unwrap_or_else(|_| usage_error(&format!("--mines: not a number: {v}")));
                a.custom = true;
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
            "--cols" => {
                let v = value("number");
                a.cols = v
                    .parse::<usize>()
                    .unwrap_or_else(|_| usage_error(&format!("--cols: not a number: {v}")));
            }
            "--rows" => {
                let v = value("number");
                a.rows = v
                    .parse::<usize>()
                    .unwrap_or_else(|_| usage_error(&format!("--rows: not a number: {v}")));
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
    if a.cols == 0 || a.rows == 0 {
        usage_error("--cols and --rows must be >= 1");
    }
    if !(MIN_WIDTH..=MAX_WIDTH).contains(&a.width) {
        usage_error(&format!(
            "--width must be {MIN_WIDTH}..={MAX_WIDTH}, got {}",
            a.width
        ));
    }
    if !(MIN_HEIGHT..=MAX_HEIGHT).contains(&a.height) {
        usage_error(&format!(
            "--height must be {MIN_HEIGHT}..={MAX_HEIGHT}, got {}",
            a.height
        ));
    }
    if a.mines == 0 || a.mines >= a.width * a.height {
        usage_error(&format!(
            "--mines must be 1..={}, got {}",
            a.width * a.height - 1,
            a.mines
        ));
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

/// Build the game the arguments describe: a preset, or a custom board.
fn new_game(a: &Args) -> Game {
    if a.custom {
        Game::with(a.seed, a.width, a.height, a.mines)
    } else {
        Game::preset(a.seed, a.difficulty)
    }
}

// ---------------------------------------------------------------------------
// Records — Tier-1 mutable state, under $HOME only.
// ---------------------------------------------------------------------------

/// `$XDG_DATA_HOME/pengyos/games/pengy-icefield`, defaulting to `~/.local/share`.
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
    base.join("pengyos").join("games").join("pengy-icefield")
}

/// The persisted records: the best score of any run, and the best time per
/// difficulty. A time of `-1` means "this difficulty has not been cleared yet".
#[derive(Clone, Copy, Debug)]
struct Records {
    best_score: i64,
    best_seconds: u64,
    best_seed: u64,
    times: [i64; 3],
}

impl Default for Records {
    fn default() -> Self {
        Self {
            best_score: 0,
            best_seconds: 0,
            best_seed: 0,
            times: [-1, -1, -1],
        }
    }
}

impl Records {
    fn load(dir: &PathBuf) -> Records {
        let text = match fs::read_to_string(dir.join("records.txt")) {
            Ok(t) => t,
            Err(_) => return Records::default(),
        };
        let mut r = Records::default();
        for line in text.lines() {
            let mut kv = line.splitn(2, '=');
            let (k, v) = match (kv.next(), kv.next()) {
                (Some(k), Some(v)) => (k.trim(), v.trim()),
                _ => continue,
            };
            match k {
                "best_score" => r.best_score = v.parse().unwrap_or(0),
                "best_seconds" => r.best_seconds = v.parse().unwrap_or(0),
                "best_seed" => r.best_seed = v.parse().unwrap_or(0),
                "time_beginner" => r.times[0] = v.parse().unwrap_or(-1),
                "time_intermediate" => r.times[1] = v.parse().unwrap_or(-1),
                "time_expert" => r.times[2] = v.parse().unwrap_or(-1),
                _ => {}
            }
        }
        r
    }

    /// Fold this run into the records.
    fn apply(&mut self, g: &Game) {
        let score = g.score();
        if score > self.best_score {
            self.best_score = score;
            self.best_seconds = g.seconds();
            self.best_seed = g.seed;
        }
        if g.state == State::Won && g.preset != CUSTOM_PRESET {
            let t = g.seconds() as i64;
            let slot = &mut self.times[g.preset];
            if *slot < 0 || t < *slot {
                *slot = t;
            }
        }
    }

    /// Write the records back. Every failure is ignored: a read-only `$HOME`
    /// must never cost the player a game.
    fn save(&self, dir: &PathBuf) {
        if fs::create_dir_all(dir).is_ok() {
            let body = format!(
                "# pengy-icefield records (Icefield, pengy-icefield {VERSION})\nbest_score={s}\nbest_seconds={ss}\nbest_seed={seed}\ntime_beginner={t0}\ntime_intermediate={t1}\ntime_expert={t2}\n",
                s = self.best_score,
                ss = self.best_seconds,
                seed = self.best_seed,
                t0 = self.times[0],
                t1 = self.times[1],
                t2 = self.times[2],
            );
            let _ = fs::write(dir.join("records.txt"), body);
        }
    }

    /// Load the records into the game's display fields.
    fn into_game(self, g: &mut Game) {
        g.best_score = self.best_score;
        if g.preset != CUSTOM_PRESET && self.times[g.preset] >= 0 {
            g.best_time = self.times[g.preset] as u64;
        }
    }
}

// ---------------------------------------------------------------------------
// Terminal: stty raw, alternate screen, the restore guard.
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
    alt: bool,
}

impl TerminalGuard {
    /// Take over the terminal: raw/-echo, alternate screen, hidden cursor.
    /// Anything that fails is simply skipped — the game then runs in the
    /// normal screen buffer with cooked input, which is degraded, not broken.
    fn take() -> TerminalGuard {
        let stty = stty(&["raw", "-echo"]);
        let mut out = io::stdout();
        let alt = write!(out, "{ENTER_ALT}{HIDE_CURSOR}").is_ok();
        let _ = out.flush();
        TerminalGuard { stty, alt }
    }
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let mut out = io::stdout();
        let _ = write!(out, "{SHOW_CURSOR}{LEAVE_ALT}");
        let _ = out.flush();
        self.alt = false;
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
                        // Keys are ASCII. UTF-8 continuation bytes are
                        // skipped; the arrow keys arrive as `ESC [ A` and the
                        // letter is what `Dir::parse` reads.
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

// ---------------------------------------------------------------------------
// The interactive loop
// ---------------------------------------------------------------------------

/// Paint one frame: home, write the whole frame, flush. One write per frame,
/// no per-cell addressing — that is what keeps it flicker-free.
fn paint(out: &mut impl Write, g: &Game, opts: RenderOpts) {
    let frame = g.render(opts);
    let _ = write!(out, "\x1b[H{frame}");
    let _ = out.flush();
}

fn run_interactive(args: &Args, opts: RenderOpts, dir: PathBuf) -> i32 {
    let guard = TerminalGuard::take();
    let rx = spawn_key_reader();
    let mut g = new_game(args);
    if !args.no_save {
        Records::load(&dir).into_game(&mut g);
    }
    let mut out = io::stdout();
    let tick = Duration::from_millis(TICK_MS);
    let mut deadline = Instant::now() + tick;
    let mut quit = false;

    while !quit {
        paint(&mut out, &g, opts);
        // Drain keys until the tick deadline; recv_timeout sleeps, so this is
        // a fixed timestep with no busy-wait.
        while !quit {
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            match rx.recv_timeout(deadline - now) {
                Ok(c) => quit = g.key(c),
                Err(RecvTimeoutError::Timeout) | Err(RecvTimeoutError::Disconnected) => break,
            }
        }
        if quit {
            break;
        }
        if args.autopilot && !g.state.is_over() {
            if let Some(k) = g.autopilot_key() {
                g.key(k);
            }
        }
        g.tick();

        if g.state.is_over() {
            // The end of a run is a two-second event, not a ten-second one:
            // paint the result, bank the records, and hand control back.
            if !args.no_save {
                let mut r = Records::load(&dir);
                r.apply(&g);
                r.save(&dir);
            }
            paint(&mut out, &g, opts);
            let _ = out.flush();
            match rx.recv_timeout(Duration::from_secs(2)) {
                Ok(c) => quit = g.key(c),
                Err(_) => quit = true,
            }
        }
        deadline = Instant::now() + tick;
    }

    if !args.no_save {
        let mut r = Records::load(&dir);
        r.apply(&g);
        r.save(&dir);
    }
    drop(guard);
    0
}

// ---------------------------------------------------------------------------
// Headless: the scripted paths. No TTY is ever required.
// ---------------------------------------------------------------------------

/// One line of a replay file → the key for that tick, `None` = no input.
/// A blank line is no input; a `#` line is a comment, not a tick. A line that
/// is a single space is the reveal key, which is why the reader trims only the
/// line ending.
fn replay_key(line: &str) -> Option<char> {
    line.chars().next()
}

fn run_replay(args: &Args, path: &str, dir: PathBuf) -> i32 {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("pengy-icefield: cannot read replay file {path}: {e}");
            return 66;
        }
    };
    // The seed is a command-line argument, never a file property: a fixture
    // replays on whatever seed the harness asks for.
    let mut g = new_game(args);
    if !args.no_save {
        Records::load(&dir).into_game(&mut g);
    }
    let mut quit = false;
    let mut stopped_at: Option<usize> = None;
    for (n, raw) in text.lines().enumerate() {
        let line = raw.trim_end_matches('\r');
        if line.starts_with('#') {
            continue; // a comment is not a tick
        }
        // The session is over when the run is over: a finished board is frozen,
        // and `n` on the end screen *restarts* (which would silently start a new
        // session in the middle of a replay). So the harness stops at the end.
        if g.state.is_over() {
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
        let mut r = Records::load(&dir);
        r.apply(&g);
        r.save(&dir);
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
        Records::load(&dir).into_game(&mut g);
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
        if g.state.is_over() {
            break;
        }
    }
    if !args.no_save {
        let mut r = Records::load(&dir);
        r.apply(&g);
        r.save(&dir);
    }
    if let Some(path) = &args.record {
        let header = format!(
            "# pengy-icefield replay fixture (Icefield, Minesweeper).\n# One line of input per tick; blank = no input; '#' = comment.\n# Keys: h j k l move, space reveal, f flag, c chord, n new, q quit.\n# Play it back with: pengy-icefield --replay <this file> --seed {}\n",
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
            eprintln!("pengy-icefield: cannot write {path}: {e}");
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
    let (cols, rows) = if tty {
        match terminal_size() {
            Some((rows, cols)) => (cols, rows),
            None => (args.cols, args.rows),
        }
    } else {
        (args.cols, args.rows)
    };
    RenderOpts {
        width: cols,
        height: rows,
        mono: args.mono,
        color: !args.no_color,
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

fn main() {
    let args = parse_args();
    if args.help {
        print!("{USAGE}");
        process::exit(0);
    }
    if args.version {
        println!("pengy-icefield {VERSION}");
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
