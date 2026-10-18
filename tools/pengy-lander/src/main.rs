//! pengy-lander — the terminal front-end for Ice Lander.
//!
//! This file owns everything that touches the outside world: the clock, the
//! keyboard, the screen, the high-score file. Every rule lives in `src/lib.rs`;
//! the loop below only ever calls `Game::key`, `Game::tick`, `Game::render` and
//! the persistence helpers here.
//!
//! Constraints that shape this file (spec.md "Constraints"):
//! * std only — raw keyboard via `stty raw -echo` (`std::process::Command`),
//!   a stdin reader thread feeding the loop over `std::sync::mpsc`, ANSI
//!   escapes on stdout, one home+repaint per frame;
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

use pengy_lander::{
    legend, plain_state, Body, Game, RenderOpts, State, BODIES, FUEL_UNITS, LAND_BASE,
    MAX_TILT_DEG, MAX_VX, MAX_VY, REF_HEIGHT, REF_WIDTH, TICK_MS, THRUST_ACC, WAVE_BONUS,
};

/// Crate version, from Cargo.toml.
const VERSION: &str = env!("CARGO_PKG_VERSION");

const USAGE: &str = "\
pengy-lander — Ice Lander: land the probe on a pad. Not fast, not tilted, not
out of fuel. The 1979 Lunar Lander cart, on the PengyOS terminal.

USAGE
  pengy-lander [FLAG]...

FLAGS
  --seed N          Seed the run (default 1). Same seed + same inputs = the
                    same run, bit for bit.
  --body moon|asteroid
                    Gravity: moon 1.62 m/s^2 (default), asteroid 5.0 m/s^2.
                    The engine is 4.0 m/s^2 either way, so an asteroid is a
                    much harder game.
  --headless        No TTY, no rendering, no keys: run the built-in autopilot
                    for --ticks ticks, print the plain-text state, exit 0.
                    This is how the game is proven to work where there is no
                    pty (the repo gate, pengyos-qemu-verify).
  --replay FILE     Play a recorded session headlessly: one line of input per
                    tick, a blank line means no input, a '#' line is a comment
                    and is not a tick. A line containing a space is a tick of
                    thrust. Stops at the crash tick or at EOF. Prints the
                    final state as key=value lines and exits 0. This is the
                    acceptance harness.
  --record FILE     With --headless: write the autopilot's input script to
                    FILE in --replay format (used to make the fixtures).
  --ticks N         Ticks for the headless/autopilot path (default 3000).
  --autopilot       Interactive: let the built-in autopilot fly. Demo mode.
  --mono            ASCII-only glyphs (no box-drawing characters), no colour.
  --no-color        No ANSI colour. NO_COLOR in the environment is honoured
                    automatically and implies this.
  --print-frame     With --headless/--replay: also print the final frame as
                    plain text.
  --width N         Window width for the headless frame (default 80).
  --height N        Window height for the headless frame (default 24).
  --data-dir DIR    Where the high score is kept (default
                    $XDG_DATA_HOME/pengyos/games/pengy-lander, i.e.
                    ~/.local/share/pengyos/games/pengy-lander).
  --no-save         Do not read or write the high score (tests, sandboxes).
  --help, -h        This text.
  --version         Version.

KEYS
  space              Thrust, 4.0 m/s^2, for as long as it is held. Burns 1
                     unit of fuel per second.
  a / d              Rotate left / right, 90 deg/s. The arrow keys work too.
  n                  New terrain: the next wave. From a landed pad this banks
                     the landing; from the air it abandons the wave.
  r                  Restart the run on the next seed. The high score is kept.
  q                  Quit (restores the terminal). Ctrl-C quits too.
  anything else      Ignored. Escape is not a quit key: terminals send the
                     arrow keys as ESC [ A, so a bare ESC must stay harmless.

RULES IN ONE BREATH
  Physics runs at 1/30 s per tick, in metres and m/s: gravity pulls you down,
  thrust pushes you along the nose, rotation is 90 deg/s, fuel is 100 units at
  1 unit per second of thrust. The ground is a 60-metre ridge with two to four
  flat pads 6 to 12 metres wide, worth 1x, 2x and 5x. Touchdown is a landing
  only when |vx| < 1.0 m/s, |vy| < 2.0 m/s and |tilt| < 5 degrees, and the feet
  are on a pad. Anything else is a crash and the run is over. A landing pays
  100 x the pad multiplier, plus 10 per unit of fuel left, plus 50 per wave
  already banked. n flies the next wave on fresh terrain.
";

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/// Parsed command line. Every flag in `USAGE` maps to exactly one field.
struct Args {
    seed: u64,
    seed_given: bool,
    body: Body,
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
            body: Body::Moon,
            headless: false,
            replay: None,
            record: None,
            ticks: 3000,
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
    eprintln!("pengy-lander: {msg}");
    eprintln!("pengy-lander: try --help");
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
            "--body" => {
                let v = value("body");
                a.body = match Body::parse(&v) {
                    Some(b) => b,
                    None => usage_error(&format!(
                        "--body: {} or {}, got {v}",
                        BODIES[0], BODIES[1]
                    )),
                };
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
// High score — Tier-1 mutable state, under $HOME only.
// ---------------------------------------------------------------------------

/// `$XDG_DATA_HOME/pengyos/games/pengy-lander`, defaulting to `~/.local/share`.
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
    base.join("pengyos").join("games").join("pengy-lander")
}

/// The persisted record: best score, the waves it took, the seed it happened
/// on, and how many runs have been flown.
#[derive(Clone, Copy, Debug, Default)]
struct Best {
    score: u64,
    waves: u64,
    seed: u64,
    runs: u64,
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
                "waves" => b.waves = v.parse().unwrap_or(0),
                "seed" => b.seed = v.parse().unwrap_or(0),
                "runs" => b.runs = v.parse().unwrap_or(0),
                _ => {}
            }
        }
        b
    }

    /// Merge this run into the record and write it back. Every failure is
    /// ignored: a read-only `$HOME` must never cost the player a game.
    fn save(dir: &PathBuf, g: &Game) -> Best {
        let mut b = Best::load(dir);
        b.runs += 1;
        if g.score() > b.score {
            b.score = g.score();
            b.seed = g.seed;
        }
        if g.waves_landed > b.waves {
            b.waves = g.waves_landed;
        }
        if fs::create_dir_all(dir).is_ok() {
            let body = format!(
                "# pengy-lander high score (Ice Lander, pengy-lander {VERSION})\nscore={}\nwaves={}\nseed={}\nruns={}\n",
                b.score, b.waves, b.seed, b.runs
            );
            let _ = fs::write(dir.join("best.txt"), body);
        }
        b
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
        // Only touch the terminal when stdin really is one.
        let tty = io::stdin().is_terminal();
        let stty = if tty { stty(&["raw", "-echo"]) } else { false };
        let mut out = io::stdout();
        let alt = tty && write!(out, "{ENTER_ALT}{HIDE_CURSOR}").is_ok();
        let _ = out.flush();
        TerminalGuard { stty, alt }
    }
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        if self.alt {
            let mut out = io::stdout();
            let _ = write!(out, "{SHOW_CURSOR}{LEAVE_ALT}");
            let _ = out.flush();
            self.alt = false;
        }
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
                        // letter is what `Game::key` reads.
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
/// library understands (`a` left, `d` right). A bare Escape is dropped: it is
/// deliberately not a quit key, because terminals send the arrows as `ESC [ A`.
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
                if c == '[' || c == 'O' {
                    self.state = 2;
                    None
                } else {
                    // A lone escape, or ESC followed by something that is not
                    // a CSI: harmless, and the next byte is a fresh key.
                    self.state = 0;
                    None
                }
            }
            _ => {
                self.state = 0;
                match c {
                    'A' | 'D' => Some('a'),
                    'B' | 'C' => Some('d'),
                    _ => None,
                }
            }
        }
    }
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
    let mut filter = KeyFilter::new();
    let mut g = Game::with_body(args.seed, args.body);
    if !args.no_save {
        mirror(&mut g, &Best::load(&dir));
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
        if args.autopilot {
            for k in g.autopilot_keys().chars() {
                g.key(k);
            }
            if g.state == State::Landed {
                g.key('n');
            }
        }
        g.tick();

        if g.state == State::Crashed {
            // A crash ends the run: bank the score, show the reason, and hand
            // the terminal back. Any key (or 2 seconds) closes it.
            if !args.no_save {
                bank(&dir, &mut g);
            }
            paint(&mut out, &g, opts);
            let _ = out.flush();
            match rx.recv_timeout(Duration::from_secs(2)) {
                Ok(_) => quit = true,
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

/// Put the persisted record into the game, so the HUD and the report show it.
fn mirror(g: &mut Game, b: &Best) {
    g.best_score = b.score;
    g.best_waves = b.waves;
    g.best_seed = b.seed;
}

/// Bank the run into the persisted record and put the record's new numbers
/// back into the game, so what the run reports is what the file now holds.
fn bank(dir: &PathBuf, g: &mut Game) {
    let b = Best::save(dir, g);
    mirror(g, &b);
}

/// One line of a replay file → the keys for that tick. A line's characters are
/// the keypresses, applied in order, and **a space is the thrust key**: a line
/// of one space is one tick of thrust. A blank line is no input; a `#` line is
/// a comment and is not a tick.
fn replay_keys(line: &str) -> Vec<char> {
    line.chars().filter(|c| *c != '\r').collect()
}

fn run_replay(args: &Args, path: &str, dir: PathBuf) -> i32 {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("pengy-lander: cannot read replay file {path}: {e}");
            return 66;
        }
    };
    // The seed is a command-line argument, never a file property: a fixture
    // replays on whatever seed the harness asks for.
    let mut g = Game::with_body(args.seed, args.body);
    if !args.no_save {
        mirror(&mut g, &Best::load(&dir));
    }
    let mut quit = false;
    let mut stopped_at: Option<usize> = None;
    for (n, raw) in text.lines().enumerate() {
        let line = raw.trim_end_matches('\r');
        if line.starts_with('#') {
            continue; // a comment is not a tick
        }
        // The session is over when the probe is wrecked: a crashed probe is
        // frozen, and `r` on the end screen *restarts* (that is the restart the
        // player wants, and it would silently start a new session in the middle
        // of a replay). So the harness stops at the crash tick.
        if g.state == State::Crashed {
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
    let mut g = Game::with_body(args.seed, args.body);
    if !args.no_save {
        mirror(&mut g, &Best::load(&dir));
    }
    let mut script: Vec<String> = Vec::new();
    for _ in 0..args.ticks {
        let mut keys = g.autopilot_keys();
        if g.state == State::Landed {
            keys.push('n'); // fly the next wave on fresh terrain
        }
        if args.record.is_some() {
            script.push(keys.clone());
        }
        for c in keys.chars() {
            g.key(c);
        }
        g.tick();
        if g.state == State::Crashed {
            break;
        }
    }
    if !args.no_save {
        bank(&dir, &mut g);
    }
    if let Some(path) = &args.record {
        let header = format!(
            "# pengy-lander replay fixture (Ice Lander).\n# One line of input per tick; blank = no input; a space = one tick of thrust; '#' = comment.\n# Play it back with: pengy-lander --replay <this file> --seed {} --body {}\n",
            args.seed,
            args.body.name()
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
            eprintln!("pengy-lander: cannot write {path}: {e}");
            return 66;
        }
        println!("recorded={} ticks={}", path, script.len());
    }
    if args.print_frame {
        // The frame *is* the field: print the numbers, then the screen, and
        // skip the duplicate ASCII field so the output stays parseable.
        for (k, v) in g.result_kv() {
            println!("{k}={v}");
        }
        print!("{}", g.render(render_opts(args, false)));
    } else {
        print!("{}", plain_state(&g));
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
        color: !args.no_color,
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

/// The part of `--help` that quotes the tuning constants, so the text can
/// never drift from the rules in `src/lib.rs`.
fn help_tail() -> String {
    let mut s = String::from("PHYSICS (per 1/30 s tick)\n");
    // Every number below is quoted from src/lib.rs, in integer fixed point, so
    // the help text can never drift from the rules.
    let _ = write!(
        s,
        "  gravity: moon 1.62 m/s^2, asteroid 5.0 m/s^2; thrust {t}.{t1} m/s^2 while\n  fuel remains; rotation 90 deg/s; fuel {fuel} units, 1 unit per second of thrust.\nLANDING\n  |vx| < {vx}.{vx1} m/s, |vy| < {vy}.{vy1} m/s, |tilt| < {tilt} degrees, feet on a pad.\nSCORING\n  {base} x pad multiplier (1x/2x/5x) + {fp} per unit of fuel left + {wb} per wave\n  already banked. n = fresh terrain, next wave. r = restart, best kept.\n",
        t = THRUST_ACC / 1000,
        t1 = THRUST_ACC % 1000 / 100,
        fuel = FUEL_UNITS,
        vx = MAX_VX / 30_000,
        vx1 = MAX_VX % 30_000 / 3_000,
        vy = MAX_VY / 30_000,
        vy1 = MAX_VY % 30_000 / 3_000,
        tilt = MAX_TILT_DEG,
        base = LAND_BASE,
        fp = 10,
        wb = WAVE_BONUS
    );
    let _ = write!(s, "KEYS\n  {}\n", legend(false));
    s
}

fn main() {
    let args = parse_args();
    if args.help {
        print!("{USAGE}{}", help_tail());
        process::exit(0);
    }
    if args.version {
        println!("pengy-lander {VERSION}");
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
