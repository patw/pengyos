//! pengy-merge — Icefloe 2048: **every rule and the renderer, in pure code**.
//!
//! This file never touches the outside world: no I/O, no clock, no threads, no
//! files, no system entropy. A run is a function of `(seed, script)`, which is
//! what makes the headless harness (`--replay`) a real acceptance test and what
//! makes `render()` a pure, diffable function of state.
//!
//! The game: a 4x4 field of ice floes numbered 2, 4, 8, … Sliding the field in
//! any direction moves every floe as far as it goes; two equal floes that meet
//! fuse into one floe of double value, and the fused value is added to the
//! score. A floe can fuse at most once per slide. After every slide that moved
//! something, one new floe appears in a random empty cell (2 with 90%
//! probability, 4 with 10%). Reach 2048 to win; the field is lost when a slide
//! changes nothing and no fuse is possible.
//!
//! See `spec.md` for the contract and `tests/rules.rs` for the rules asserted.

use std::fmt::Write as _;

// ---------------------------------------------------------------------------
// Tuning constants — the difficulty of the game lives here.
// ---------------------------------------------------------------------------

/// Default field side. `--size` accepts [`MIN_SIZE`]..=[`MAX_SIZE`].
pub const DEFAULT_SIZE: usize = 4;
/// Smallest field that is still a game (3x3 is brutal on purpose).
pub const MIN_SIZE: usize = 3;
/// Largest field the renderer is laid out for.
pub const MAX_SIZE: usize = 8;

/// The default winning floe.
pub const DEFAULT_GOAL: u32 = 2048;

/// Fixed timestep of the front-end loop, in milliseconds. The puzzle itself is
/// turn-based — the tick only drives the loop cadence and the fuse flash.
pub const TICK_MS: u64 = 110;

/// How many ticks a fused or newly spawned floe flashes for. Cosmetic, and
/// part of the state, so a replay stays byte-identical.
pub const FLASH_TICKS: u8 = 3;

/// A new floe is a 2 with this percent probability, otherwise a 4.
pub const SPAWN_TWO_PERCENT: u64 = 90;

/// Floes spawned at the start of a run.
pub const START_SPAWNS: usize = 2;

/// Rendered width of one cell, in columns (values are right-aligned in it).
pub const CELL_W: usize = 5;

/// Width at or above which the score panel and the keys line are drawn.
pub const PANEL_W: usize = 42;
/// Width at or above which the header line is drawn.
pub const HEADER_W: usize = 30;

/// The reference window the layout is designed for.
pub const REF_WIDTH: usize = 80;
/// Reference window height.
pub const REF_HEIGHT: usize = 24;

// ---------------------------------------------------------------------------
// RNG — hand-written xorshift64*, seeded and reproducible.
// ---------------------------------------------------------------------------

/// A seeded xorshift64* generator. No `rand`, no system entropy: every run
/// takes `--seed N` and is reproducible bit-for-bit.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rng {
    state: u64,
}

impl Rng {
    /// Seed the generator. The seed is mixed (splitmix-style) so neighbouring
    /// seeds are not correlated, and a zero state is remapped (xorshift must
    /// never hold 0).
    pub fn new(seed: u64) -> Self {
        let mut s = seed.wrapping_add(0x9E37_79B9_7F4A_7C15);
        s ^= s >> 30;
        s = s.wrapping_mul(0xBF58_476D_1CE4_70B9);
        s ^= s >> 27;
        s = s.wrapping_mul(0x94D0_49BB_1331_11EB);
        s ^= s >> 31;
        Self {
            state: if s == 0 { 0x2545_FF49_6413_F8B9 } else { s },
        }
    }

    /// Raw generator state. Part of the game state, so hashes cover it.
    pub fn state(&self) -> u64 {
        self.state
    }

    /// Restore the raw generator state (used by undo).
    pub fn set_state(&mut self, state: u64) {
        self.state = if state == 0 { 0x2545_FF49_6413_F8B9 } else { state };
    }

    /// Next 64-bit value.
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545_FF49_6413_F8B9)
    }

    /// Next value in `0..n`. `n` must be > 0 (asserted: an unseedable spawn is
    /// a hard bug, not a graceful case).
    pub fn below(&mut self, n: u64) -> u64 {
        assert!(n > 0, "Rng::below(0) — no empty cell to spawn into");
        self.next_u64() % n
    }
}

// ---------------------------------------------------------------------------
// Directions, states, causes
// ---------------------------------------------------------------------------

/// A slide direction.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dir {
    Left,
    Right,
    Up,
    Down,
}

pub use Dir::*;

impl Dir {
    /// Every direction, in a fixed order (the order the rules code sweeps).
    pub const ALL: [Dir; 4] = [Left, Right, Up, Down];

    /// The key that slides this way, as it appears in a replay file.
    pub fn key(self) -> char {
        match self {
            Left => 'h',
            Down => 'j',
            Up => 'k',
            Right => 'l',
        }
    }

    /// Parse a key character into a slide. Accepts `hjkl`, the arrow keys'
    /// CSI letter (`A` up, `B` down, `C` right, `D` left — a terminal sends
    /// `ESC [ A` and the reader hands us the `A`), and the WASD aliases.
    pub fn parse(c: char) -> Option<Dir> {
        match c {
            'h' | 'H' | 'D' => Some(Left),
            'j' | 'J' | 'B' => Some(Down),
            'k' | 'K' | 'A' => Some(Up),
            'l' | 'L' | 'C' => Some(Right),
            'a' => Some(Left),
            's' | 'S' => Some(Down),
            'w' | 'W' => Some(Up),
            _ => None,
        }
    }

    /// Human name, for the status line.
    pub fn name(self) -> &'static str {
        match self {
            Left => "left",
            Right => "right",
            Up => "up",
            Down => "down",
        }
    }
}

/// The three states of a run.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    /// Slides are live.
    Playing,
    /// The goal floe has been reached: the frame offers `c` (continue),
    /// `r` (restart) and `q` (quit). Slides are frozen until `c`.
    Won,
    /// No slide changes anything and no fuse is possible. The run is finished.
    Over,
}

/// Why a run ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cause {
    /// The goal floe appeared.
    Goal,
    /// The field is full and nothing can fuse.
    Stuck,
}

impl Cause {
    /// Human text for the status line.
    pub fn text(self) -> &'static str {
        match self {
            Cause::Goal => "reached the goal floe",
            Cause::Stuck => "the floes jammed: no slide moves and nothing fuses",
        }
    }
}

// ---------------------------------------------------------------------------
// The slide — the heart of the game, and the part the tests aim at.
// ---------------------------------------------------------------------------

/// What one line (row or column) did during a slide.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct LineSlide {
    /// The line after the slide, in travel order (index 0 is the cell the
    /// direction of travel pushes against).
    pub cells: Vec<u32>,
    /// Sum of the fused values — exactly what the score gains.
    pub gained: i64,
    /// Number of fuses.
    pub fuses: usize,
    /// Indices (in travel order) of the cells that hold a fused floe: these
    /// are the cells that flash.
    pub fused_at: Vec<usize>,
    /// True when the slide moved at least one floe.
    pub changed: bool,
}

/// Slide one line in travel order: pack every floe toward index 0, fusing
/// equal neighbours **from the direction of travel**, and never fusing a floe
/// twice.
///
/// This is the whole rule set in twelve lines, and the tests in
/// `tests/rules.rs` aim straight at it:
/// `[2,2,2,2] -> [4,4]`, `[4,4,8] -> [8,8]`, `[2,4,2] -> [2,4,2]`,
/// `[4,4,8,8] -> [8,16]`.
pub fn slide_line(input: &[u32]) -> LineSlide {
    let n = input.len();
    let mut cells: Vec<u32> = Vec::with_capacity(n);
    let mut fused_last = false;
    let mut out = LineSlide::default();
    for &v in input.iter().filter(|v| **v != 0) {
        match cells.last() {
            // Equal neighbour that is not itself the product of this slide's
            // fuse: fuse. A floe formed by a fuse this slide never fuses again.
            Some(&last) if last == v && !fused_last => {
                let merged = last + v;
                let at = cells.len() - 1;
                cells[at] = merged;
                fused_last = true;
                out.gained += merged as i64;
                out.fuses += 1;
                out.fused_at.push(at);
            }
            _ => {
                cells.push(v);
                fused_last = false;
            }
        }
    }
    while cells.len() < n {
        cells.push(0);
    }
    out.changed = cells != input;
    out.cells = cells;
    out
}

// ---------------------------------------------------------------------------
// Game state
// ---------------------------------------------------------------------------

/// Everything a single-step undo can put back.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub grid: Vec<u32>,
    pub score: i64,
    pub moves: u64,
    pub fuses: u64,
    pub max_tile: u32,
    pub rng: u64,
    pub state: State,
    pub cause: Option<Cause>,
    pub continued: bool,
}

/// One run of Icefloe 2048.
#[derive(Clone, Debug)]
pub struct Game {
    /// Field side.
    pub size: usize,
    /// The floe that wins the run.
    pub goal: u32,
    /// Row-major field; `0` is an empty cell.
    pub grid: Vec<u32>,
    /// Sum of every fused value this run.
    pub score: i64,
    /// Best score read from (and later merged into) the persisted record.
    pub best: i64,
    /// The seed this run started from.
    pub seed: u64,
    /// The generator. Part of the state: a spawn is reproducible.
    pub rng: Rng,
    pub state: State,
    /// Ticks elapsed. The puzzle is turn-based; this drives the loop cadence.
    pub ticks: u64,
    /// Slides that changed the field.
    pub moves: u64,
    /// Fuses performed.
    pub fuses: u64,
    /// Largest floe on the field.
    pub max_tile: u32,
    /// Undos performed.
    pub undos: u64,
    /// The one step of history `u` can take back.
    pub history: Option<Snapshot>,
    /// Why the run ended, when it has.
    pub cause: Option<Cause>,
    /// The player pressed `c` after winning and kept playing.
    pub continued: bool,
    /// Per-cell fuse/spawn flash countdown (cosmetic, part of the state).
    pub flash: Vec<u8>,
    /// The last slide performed, for the status line.
    pub last_dir: Option<Dir>,
}

impl Game {
    /// A fresh 4x4 run toward 2048, with [`START_SPAWNS`] floes dealt.
    pub fn new(seed: u64) -> Self {
        Self::with(seed, DEFAULT_SIZE, DEFAULT_GOAL)
    }

    /// A fresh run with a chosen field size and goal.
    pub fn with(seed: u64, size: usize, goal: u32) -> Self {
        assert!(
            (MIN_SIZE..=MAX_SIZE).contains(&size),
            "field size must be {MIN_SIZE}..={MAX_SIZE}"
        );
        assert!(goal >= 4, "the goal floe must be at least 4");
        let n = size * size;
        let mut g = Game {
            size,
            goal,
            grid: vec![0; n],
            score: 0,
            best: 0,
            seed,
            rng: Rng::new(seed),
            state: State::Playing,
            ticks: 0,
            moves: 0,
            fuses: 0,
            max_tile: 0,
            undos: 0,
            history: None,
            cause: None,
            continued: false,
            flash: vec![0; n],
            last_dir: None,
        };
        for _ in 0..START_SPAWNS {
            g.spawn();
        }
        g
    }

    /// Restart: the same field size and goal, the next seed in the family,
    /// the best score kept.
    pub fn restart(&mut self) {
        let seed = self.seed.wrapping_add(1);
        let size = self.size;
        let goal = self.goal;
        let best = self.best;
        *self = Game::with(seed, size, goal);
        self.best = best;
    }

    // -- field helpers ------------------------------------------------------

    /// Cell value at (row, col).
    pub fn at(&self, row: usize, col: usize) -> u32 {
        self.grid[row * self.size + col]
    }

    /// Indices of the empty cells, in row-major order.
    pub fn empty_cells(&self) -> Vec<usize> {
        (0..self.grid.len()).filter(|&i| self.grid[i] == 0).collect()
    }

    /// The line of cells in travel order for `dir`: index 0 is the cell the
    /// direction of travel pushes against.
    fn line(&self, dir: Dir, lane: usize) -> Vec<u32> {
        let n = self.size;
        (0..n)
            .map(|i| match dir {
                Left => self.at(lane, i),
                Right => self.at(lane, n - 1 - i),
                Up => self.at(i, lane),
                Down => self.at(n - 1 - i, lane),
            })
            .collect()
    }

    fn write_line(&mut self, dir: Dir, lane: usize, cells: &[u32]) {
        let n = self.size;
        for (i, &v) in cells.iter().enumerate() {
            let idx = match dir {
                Left => lane * n + i,
                Right => lane * n + (n - 1 - i),
                Up => i * n + lane,
                Down => (n - 1 - i) * n + lane,
            };
            self.grid[idx] = v;
        }
    }

    // -- rules --------------------------------------------------------------

    /// Slide the field. Returns what the slide did; the field is updated.
    /// Score gains exactly the sum of the fused values.
    pub fn slide(&mut self, dir: Dir) -> LineSlide {
        let mut total = LineSlide::default();
        for lane in 0..self.size {
            let before = self.line(dir, lane);
            let slid = slide_line(&before);
            if slid.changed {
                self.write_line(dir, lane, &slid.cells);
                total.changed = true;
                total.gained += slid.gained;
                total.fuses += slid.fuses;
                for at in slid.fused_at {
                    let idx = match dir {
                        Left => lane * self.size + at,
                        Right => lane * self.size + (self.size - 1 - at),
                        Up => at * self.size + lane,
                        Down => (self.size - 1 - at) * self.size + lane,
                    };
                    self.flash[idx] = FLASH_TICKS;
                }
            }
        }
        self.score += total.gained;
        self.fuses += total.fuses as u64;
        self.max_tile = self.max_floe();
        self.last_dir = Some(dir);
        total
    }

    /// Deal one floe into a random empty cell: a 2 with
    /// [`SPAWN_TWO_PERCENT`], otherwise a 4. `None` when the field is full.
    pub fn spawn(&mut self) -> Option<usize> {
        let empty = self.empty_cells();
        if empty.is_empty() {
            return None;
        }
        let pick = empty[self.rng.below(empty.len() as u64) as usize];
        let value = if self.rng.below(100) < SPAWN_TWO_PERCENT {
            2
        } else {
            4
        };
        self.grid[pick] = value;
        self.flash[pick] = FLASH_TICKS;
        if value > self.max_tile {
            self.max_tile = value;
        }
        Some(pick)
    }

    /// The largest floe on the field.
    pub fn max_floe(&self) -> u32 {
        self.grid.iter().copied().max().unwrap_or(0)
    }

    /// Would this slide move anything? (A slide that moves nothing scores
    /// nothing and spawns nothing.)
    pub fn is_legal(&self, dir: Dir) -> bool {
        (0..self.size).any(|lane| slide_line(&self.line(dir, lane)).changed)
    }

    /// Is any slide legal? False exactly when the field is full and no two
    /// neighbours can fuse.
    pub fn can_move(&self) -> bool {
        Dir::ALL.iter().any(|&d| self.is_legal(d))
    }

    /// One tick: decay the flash countdown. The puzzle is turn-based, so time
    /// alone never changes the field — that is what makes a replay file a
    /// faithful record of a session.
    pub fn tick(&mut self) {
        self.ticks += 1;
        for f in self.flash.iter_mut() {
            if *f > 0 {
                *f -= 1;
            }
        }
    }

    /// Take back the last slide, including the floe it spawned. One step only:
    /// a second `u` does nothing.
    pub fn undo(&mut self) -> bool {
        match self.history.take() {
            Some(s) => {
                self.grid = s.grid;
                self.score = s.score;
                self.moves = s.moves;
                self.fuses = s.fuses;
                self.max_tile = s.max_tile;
                self.rng.set_state(s.rng);
                self.state = s.state;
                self.cause = s.cause;
                self.continued = s.continued;
                self.flash = vec![0; self.grid.len()];
                self.last_dir = None;
                self.undos += 1;
                true
            }
            None => false,
        }
    }

    /// The key for this tick a simple bot presses: the corner strategy —
    /// build toward the lower-left, and among the slides that move something
    /// prefer the one that opens the most field and fuses the most. This is
    /// what `--headless` demos, what `--record` writes into a fixture, and
    /// what makes a committed replay a *won* session.
    pub fn autopilot_key(&self) -> Option<char> {
        if self.state != State::Playing {
            return None;
        }
        // Preference weights: the lower-left corner is the building corner,
        // so left and down are worth more than right and up.
        let weight = |d: Dir| -> i64 {
            match d {
                Left => 6,
                Down => 4,
                Right => 2,
                Up => 0,
            }
        };
        let mut best: Option<(i64, Dir)> = None;
        for &d in Dir::ALL.iter() {
            if !self.is_legal(d) {
                continue;
            }
            let mut probe = self.clone();
            let slid = probe.slide(d);
            probe.max_tile = probe.max_floe();
            let empties = probe.empty_cells().len() as i64;
            let score = weight(d) + 3 * empties + (slid.gained as i64) / 2;
            match best {
                Some((b, _)) if score <= b => {}
                _ => best = Some((score, d)),
            }
        }
        best.map(|(_, d)| d.key())
    }

    // -- keys ---------------------------------------------------------------

    /// Feed one key. Returns `true` when the game asked to quit.
    ///
    /// `hjkl` / arrows / WASD slide, `u` undoes one step, `r` restarts,
    /// `c` continues after a win, `q` (and Ctrl-C) quits. Everything else is
    /// ignored. `Escape` is deliberately **not** a quit key: a terminal sends
    /// arrow keys as `ESC [ A`, so treating a bare `ESC` as quit would make
    /// the arrow keys fatal.
    pub fn key(&mut self, c: char) -> bool {
        match c {
            'q' | 'Q' | '\u{3}' => return true,
            'r' | 'R' => {
                self.restart();
                return false;
            }
            'u' | 'U' => {
                self.undo();
                return false;
            }
            'c' | 'C' => {
                if self.state == State::Won {
                    self.state = State::Playing;
                    self.continued = true;
                }
                return false;
            }
            _ => {}
        }
        if let Some(d) = Dir::parse(c) {
            self.play(d);
        }
        false
    }

    /// One turn: slide, then — only when the slide moved something — spawn,
    /// then resolve the win/lose states.
    pub fn play(&mut self, dir: Dir) -> LineSlide {
        if self.state != State::Playing {
            return LineSlide::default();
        }
        let snap = Snapshot {
            grid: self.grid.clone(),
            score: self.score,
            moves: self.moves,
            fuses: self.fuses,
            max_tile: self.max_tile,
            rng: self.rng.state(),
            state: self.state,
            cause: self.cause,
            continued: self.continued,
        };
        let slid = self.slide(dir);
        if !slid.changed {
            // A slide that moves nothing is not a turn: it must not replace the
            // undo step of the last real slide. Only a jammed field ends here,
            // and only then is there anything new to take back.
            if !self.can_move() {
                self.state = State::Over;
                self.cause = Some(Cause::Stuck);
                if self.history.is_none() {
                    self.history = Some(snap);
                }
            }
            return slid;
        }
        self.moves += 1;
        self.spawn();
        self.history = Some(snap);
        if self.max_tile >= self.goal && !self.continued {
            self.state = State::Won;
            self.cause = Some(Cause::Goal);
        } else if !self.can_move() {
            self.state = State::Over;
            self.cause = Some(Cause::Stuck);
        }
        slid
    }

    // -- determinism surface ------------------------------------------------

    /// Canonical serialization of everything that defines the run. Two games
    /// with the same string have the same [`Game::state_hash`].
    pub fn canonical(&self) -> String {
        let mut s = String::with_capacity(128);
        let _ = write!(
            s,
            "seed={seed};rng={rng:x};size={size};goal={goal};state={state:?};ticks={ticks};\
             moves={moves};fuses={fuses};score={score};best={best};max={max};undos={undos};\
             cause={cause:?};continued={cont};dir={dir:?};grid=[",
            seed = self.seed,
            rng = self.rng.state(),
            size = self.size,
            goal = self.goal,
            state = self.state,
            ticks = self.ticks,
            moves = self.moves,
            fuses = self.fuses,
            score = self.score,
            best = self.best,
            max = self.max_tile,
            undos = self.undos,
            cause = self.cause,
            cont = u8::from(self.continued),
            dir = self.last_dir,
        );
        for &v in &self.grid {
            let _ = write!(s, "{v},");
        }
        let _ = write!(s, "];flash=[");
        for &f in &self.flash {
            let _ = write!(s, "{f},");
        }
        let _ = write!(s, "]");
        if let Some(h) = &self.history {
            let _ = write!(
                s,
                ";undo=[{},{},{}]",
                h.score,
                h.moves,
                h.grid.iter().copied().max().unwrap_or(0)
            );
        }
        s
    }

    /// FNV-1a 64 over [`Game::canonical`] — the cheap "same run?" fingerprint.
    pub fn state_hash(&self) -> u64 {
        let mut h: u64 = 0xcbf2_9ce4_8422_2325;
        for b in self.canonical().as_bytes() {
            h ^= *b as u64;
            h = h.wrapping_mul(0x1000_0000_01b3);
        }
        h
    }

    /// The run as `key=value` pairs — what `--replay` prints on exit 0, and
    /// what the acceptance harness greps.
    pub fn result_kv(&self) -> Vec<(String, String)> {
        let mut kv = vec![
            ("state".into(), format!("{:?}", self.state).to_lowercase()),
            ("seed".into(), self.seed.to_string()),
            ("size".into(), self.size.to_string()),
            ("goal".into(), self.goal.to_string()),
            ("ticks".into(), self.ticks.to_string()),
            ("moves".into(), self.moves.to_string()),
            ("fuses".into(), self.fuses.to_string()),
            ("score".into(), self.score.to_string()),
            ("best".into(), self.best.to_string()),
            ("max".into(), self.max_tile.to_string()),
            ("undos".into(), self.undos.to_string()),
            ("hash".into(), format!("{:016x}", self.state_hash())),
        ];
        if let Some(c) = self.cause {
            kv.push(("cause".into(), format!("{:?}", c).to_lowercase()));
        }
        kv
    }
}

/// The plain-text state `--headless` prints: `key=value` lines, then the field
/// as ASCII rows. No escapes, diffable, and the shape the gate greps.
pub fn plain_state(g: &Game) -> String {
    let mut s = String::new();
    for (k, v) in g.result_kv() {
        let _ = writeln!(s, "{k}={v}");
    }
    let _ = writeln!(s, "grid:");
    for r in 0..g.size {
        let _ = writeln!(
            s,
            "|{}|",
            (0..g.size)
                .map(|c| format!("{:>5}", g.at(r, c)))
                .collect::<Vec<_>>()
                .join("|")
        );
    }
    s
}

// ---------------------------------------------------------------------------
// Rendering — a pure function of state.
// ---------------------------------------------------------------------------

/// What the renderer needs from the front-end: window size and glyph policy.
/// Nothing else; the frame is a pure function of `(Game, RenderOpts)`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RenderOpts {
    /// Terminal width in columns.
    pub width: usize,
    /// Terminal height in rows.
    pub height: usize,
    /// ASCII-only glyphs (`--mono`).
    pub mono: bool,
    /// Emit ANSI colour. Off under `NO_COLOR`, `--mono`, and headless.
    pub color: bool,
}

impl Default for RenderOpts {
    /// The 80x24 reference window, colour on.
    fn default() -> Self {
        Self {
            width: REF_WIDTH,
            height: REF_HEIGHT,
            mono: false,
            color: true,
        }
    }
}

const SGR_RESET: &str = "\x1b[0m";
// Colour off emits no escapes at all: the code path that paints plain text
// never reaches for a constant, it just pushes the text.

/// The colour of a floe, by magnitude. Flashing floes (just fused, just
/// dealt) get the bright variant, so a move reads at a glance.
pub fn sgr_for(value: u32, flashing: bool) -> &'static str {
    if value == 0 {
        return if flashing { "\x1b[2;33m" } else { "\x1b[2;90m" };
    }
    let base = match value {
        2 => "\x1b[2;36m",
        4 => "\x1b[36m",
        8 => "\x1b[34m",
        16 => "\x1b[32m",
        32 => "\x1b[33m",
        64 => "\x1b[33;1m",
        128 => "\x1b[35m",
        256 => "\x1b[31m",
        512 => "\x1b[31;1m",
        1024 => "\x1b[37;1m",
        _ => "\x1b[1;30;47m",
    };
    if flashing && value < 2048 {
        // Brighten: add the bold attribute to the base colour.
        return match value {
            2 => "\x1b[1;36m",
            4 => "\x1b[1;36m",
            8 => "\x1b[1;34m",
            16 => "\x1b[1;32m",
            32 => "\x1b[1;33m",
            64 => "\x1b[1;33m",
            128 => "\x1b[1;35m",
            256 => "\x1b[1;31m",
            512 => "\x1b[1;31m",
            _ => "\x1b[1;37m",
        };
    }
    base
}

/// The glyphs one paint uses. Mono is ASCII-only; colour off emits plain text
/// (NO_COLOR), which keeps the frame diffable.
struct Painter {
    mono: bool,
    color: bool,
}

impl Painter {
    fn corner(&self) -> (char, char, char, char) {
        if self.mono {
            ('+', '+', '+', '+')
        } else {
            ('\u{250c}', '\u{2510}', '\u{2514}', '\u{2518}')
        }
    }
    fn hline(&self) -> char {
        if self.mono {
            '-'
        } else {
            '\u{2500}'
        }
    }
    fn tee(&self) -> char {
        if self.mono {
            '+'
        } else {
            '\u{252c}'
        }
    }
    fn vline(&self) -> char {
        if self.mono {
            '|'
        } else {
            '\u{2502}'
        }
    }
    fn cross(&self) -> char {
        if self.mono {
            '+'
        } else {
            '\u{253c}'
        }
    }
    fn empty(&self) -> char {
        if self.mono {
            '.'
        } else {
            '\u{00b7}'
        }
    }
}

/// One cell: the value right-aligned in [`CELL_W`] columns, dots when empty.
fn cell_text(value: u32, mono: bool) -> String {
    if value == 0 {
        let p = Painter {
            mono,
            color: false,
        };
        let e = p.empty();
        return std::iter::repeat(e).take(CELL_W).collect();
    }
    format!("{value:>CELL_W$}")
}

impl Game {
    /// The top border of the field box.
    fn box_top(&self, p: &Painter) -> String {
        let seg: String = std::iter::repeat(p.hline()).take(CELL_W).collect();
        let mut s = String::new();
        s.push(p.corner().0);
        for i in 0..self.size {
            s.push_str(&seg);
            if i + 1 < self.size {
                s.push(p.tee());
            }
        }
        s.push(p.corner().1);
        s
    }

    /// The divider between two field rows.
    fn box_mid(&self, p: &Painter) -> String {
        let seg: String = std::iter::repeat(p.hline()).take(CELL_W).collect();
        let mut s = String::new();
        s.push(p.cross());
        for i in 0..self.size {
            s.push_str(&seg);
            if i + 1 < self.size {
                s.push(p.cross());
            }
        }
        s.push(p.cross());
        s
    }

    /// The bottom border of the field box.
    fn box_bottom(&self, p: &Painter) -> String {
        let seg: String = std::iter::repeat(p.hline()).take(CELL_W).collect();
        let mut s = String::new();
        s.push(p.corner().2);
        for i in 0..self.size {
            s.push_str(&seg);
            if i + 1 < self.size {
                s.push(p.cross());
            }
        }
        s.push(p.corner().3);
        s
    }

    /// One field row, with borders and per-floe colour.
    fn field_row(&self, row: usize, p: &Painter, border: bool) -> String {
        let v = p.vline();
        let mut s = String::new();
        if border {
            s.push(v);
        }
        for c in 0..self.size {
            if border && c > 0 {
                s.push(v);
            }
            let i = row * self.size + c;
            let value = self.grid[i];
            let text = cell_text(value, p.mono);
            if p.color {
                let _ = write!(s, "{}{text}{SGR_RESET}", sgr_for(value, self.flash[i] > 0));
            } else {
                s.push_str(&text);
            }
        }
        if border {
            s.push(v);
        }
        s
    }

    /// The status line: what happened, and what to do next.
    fn status_line(&self, p: &Painter) -> String {
        // Mono is ASCII-only, so the separator is a plain hyphen there.
        let dash = if p.mono { "-" } else { "\u{2014}" };
        let s = match self.state {
            State::Playing => match self.last_dir {
                Some(d) => format!(
                    "slid {} {dash} hjkl/arrows move, u undo, r restart, q quit",
                    d.name()
                ),
                None => format!("slide a floe {dash} hjkl or the arrow keys"),
            },
            State::Won => format!(
                "YOU WIN {dash} floe {} reached. c keeps playing, r restarts, q quits",
                self.max_tile
            ),
            State::Over => format!(
                "GAME OVER {dash} {}. r restarts, u takes the last slide back, q quits",
                self.cause.map(|c| c.text()).unwrap_or("jammed")
            ),
        };
        if p.color {
            let colour = match self.state {
                State::Playing => "\x1b[2;36m",
                State::Won => "\x1b[1;33m",
                State::Over => "\x1b[1;31m",
            };
            format!("{colour}{s}{SGR_RESET}")
        } else {
            s
        }
    }

    /// The frame: a pure function of `(Game, RenderOpts)`.
    ///
    /// Exactly `height` rows, every row exactly `width` columns, so one
    /// `home` + one write repaints the whole screen with no leftovers and no
    /// flicker. Degrade rules (spec.md "Rendering"): the score panel and keys
    /// line need [`PANEL_W`] columns, the header needs [`HEADER_W`], the box
    /// border needs the field width plus two, and a short window drops the
    /// keys line, then the header, then clips from the bottom.
    pub fn render(&self, opts: RenderOpts) -> String {
        let p = Painter {
            mono: opts.mono,
            color: opts.color && !opts.mono,
        };
        let grid_w = self.size * CELL_W + (self.size - 1);
        let border = opts.width >= grid_w + 2;
        let panel = opts.width >= PANEL_W;
        let header = opts.width >= HEADER_W;

        let mut lines: Vec<String> = Vec::new();
        if header {
            lines.push(if p.color {
                format!("\x1b[1;36mICEFLOE 2048\x1b[0m — slide the floes, equal floes fuse, reach {}", self.goal)
            } else {
                format!("ICEFLOE 2048 - slide the floes, equal floes fuse, reach {}", self.goal)
            });
        }
        if panel {
            let line = format!(
                "score {:>6}   best {:>6}   goal {:>5}   moves {:>4}   fuses {:>4}   seed {}",
                self.score, self.best, self.goal, self.moves, self.fuses, self.seed
            );
            lines.push(if p.color {
                format!("\x1b[36m{line}\x1b[0m")
            } else {
                line
            });
            lines.push(String::new());
        }
        if border {
            lines.push(self.box_top(&p));
        }
        for r in 0..self.size {
            lines.push(self.field_row(r, &p, border));
            if border && r + 1 < self.size {
                lines.push(self.box_mid(&p));
            }
        }
        if border {
            lines.push(self.box_bottom(&p));
        }
        if panel {
            lines.push(String::new());
            lines.push(self.status_line(&p));
        }

        // Fit the window exactly: keep the top rows, pad the rest with blanks,
        // and clip every row to the window width.
        lines.truncate(opts.height);
        while lines.len() < opts.height {
            lines.push(String::new());
        }
        let mut out = String::new();
        for line in lines {
            // Width is measured on the *visible* text: an escape costs nothing
            // on screen. A row that fits is emitted verbatim (colour intact);
            // a row that does not is truncated to plain text, which is what
            // keeps a narrow window from wrapping.
            let plain = strip_sgr(&line);
            let visible = plain.chars().count();
            if visible <= opts.width {
                let padded = format!("{line}{}", " ".repeat(opts.width - visible));
                let _ = write!(out, "{padded}\n");
            } else {
                let clipped: String = plain.chars().take(opts.width).collect();
                let _ = write!(out, "{clipped}\n");
            }
        }
        out
    }
}

/// Strip every SGR escape out of a string (used to clip coloured rows to the
/// window width without cutting an escape in half).
pub fn strip_sgr(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\x1b' {
            out.push(c);
            continue;
        }
        if chars.peek() == Some(&'[') {
            chars.next();
            for c in chars.by_ref() {
                if (c >= '@' && c <= '~') || c == 'm' {
                    break;
                }
            }
        } else {
            chars.next();
        }
    }
    out
}

/// The program around the rules: the command line, the record file, the
/// headless harness and the window. Nothing above this line depends on it.
pub mod cli;
