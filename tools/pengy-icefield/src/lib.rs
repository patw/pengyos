//! pengy-icefield — Icefield (Minesweeper, the Windows pack-in): **every rule and
//! the renderer, in pure code**.
//!
//! This file never touches the outside world: no I/O, no clock, no threads, no
//! files, no system entropy. A run is a function of `(seed, script)`, which is
//! what makes the headless harness (`--replay`) a real acceptance test and what
//! makes `render()` a pure, diffable function of state.
//!
//! The game: a field of ice, `mines` of them hiding crevasses. A cell you reveal
//! that is solid shows how many crevasses touch it (the 8 surrounding cells); a 0
//! floods its whole contiguous zero region and the numbers on its border. Flag a
//! cell you are sure is a crevasse. Chord (space on a revealed number whose flag
//! count equals it) opens every unflagged neighbour at once — and a wrong flag
//! means the chord steps into a crevasse, exactly as in the original. The first
//! reveal of a run is always safe: the crevasses are placed *after* it, never in
//! the clicked cell or its 8 neighbours. Clear every solid cell to win.
//!
//! See `spec.md` for the contract and `tests/rules.rs` for the rules asserted.

use std::fmt::Write as _;

// ---------------------------------------------------------------------------
// Tuning constants — the difficulty of the game lives here.
// ---------------------------------------------------------------------------

/// Fixed timestep of the front-end loop, in milliseconds. Minesweeper is
/// turn-based; the tick only drives the loop cadence and the timer. 100 ms is
/// exactly 10 ticks a second, so the timer is integer seconds with no floats.
pub const TICK_MS: u64 = 100;
/// Ticks per second, derived from [`TICK_MS`].
pub const TICKS_PER_SECOND: u64 = 1000 / TICK_MS;

/// One board cell is this many terminal columns wide (glyph + gap).
pub const CELL_W: usize = 2;

/// Smallest / largest board the renderer is laid out for.
pub const MIN_WIDTH: usize = 4;
pub const MAX_WIDTH: usize = 40;
pub const MIN_HEIGHT: usize = 3;
pub const MAX_HEIGHT: usize = 24;

/// The reference window the layout is designed for: everything fits here.
pub const REF_WIDTH: usize = 80;
/// Reference window height.
pub const REF_HEIGHT: usize = 24;

/// Width at or above which the score panel line is drawn.
pub const PANEL_W: usize = 40;
/// Width at or above which the header line is drawn.
pub const HEADER_W: usize = 30;
/// Width at or above which the keys line is drawn.
pub const KEYS_W: usize = 58;

/// A difficulty preset: label, board, crevasse count.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Preset {
    pub label: &'static str,
    pub width: usize,
    pub height: usize,
    pub mines: usize,
}

/// The three presets, in the order the `1`/`2`/`3` keys pick them. Beginner is
/// the 9x9/10 board the Windows pack-in shipped with; expert is 30x16/99.
pub const PRESETS: [Preset; 3] = [
    Preset {
        label: "beginner",
        width: 9,
        height: 9,
        mines: 10,
    },
    Preset {
        label: "intermediate",
        width: 16,
        height: 16,
        mines: 40,
    },
    Preset {
        label: "expert",
        width: 30,
        height: 16,
        mines: 99,
    },
];

/// The preset a run starts on.
pub const DEFAULT_PRESET: usize = 0;

/// Marks "this board is not one of the three presets" (`--width/--height/--mines`).
pub const CUSTOM_PRESET: usize = usize::MAX;

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

    /// Next 64-bit value.
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545_FF49_6413_F8B9)
    }

    /// Next value in `0..n`. `n` must be > 0 (asserted: dealing with an empty
    /// candidate list is a hard bug, not a graceful case).
    pub fn below(&mut self, n: u64) -> u64 {
        assert!(n > 0, "Rng::below(0) — no cell to deal from");
        self.next_u64() % n
    }
}

// ---------------------------------------------------------------------------
// Directions, states, causes
// ---------------------------------------------------------------------------

/// A cursor move.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dir {
    Left,
    Down,
    Up,
    Right,
}

pub use Dir::*;

impl Dir {
    /// Every direction, in a fixed order.
    pub const ALL: [Dir; 4] = [Left, Down, Up, Right];

    /// The key that moves this way, as it appears in a replay file.
    pub fn key(self) -> char {
        match self {
            Left => 'h',
            Down => 'j',
            Up => 'k',
            Right => 'l',
        }
    }

    /// Parse a key character into a cursor move. Accepts `hjkl`, the arrow
    /// keys' CSI letter (`A` up, `B` down, `C` right, `D` left — a terminal
    /// sends `ESC [ A` and the reader hands us the `A`) and the WASD aliases.
    pub fn parse(c: char) -> Option<Dir> {
        match c {
            'h' | 'H' | 'D' | 'a' => Some(Left),
            'j' | 'J' | 'B' | 's' => Some(Down),
            'k' | 'K' | 'A' | 'w' => Some(Up),
            'l' | 'L' | 'C' => Some(Right),
            _ => None,
        }
    }
}

/// The eight neighbour offsets, in a fixed order (N, NE, E, SE, S, SW, W, NW).
/// Fixed order is what makes a flood and a chord deterministic.
pub const NEIGHBOURS: [(i32, i32); 8] = [
    (-1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, -1),
];

/// The four states of a run. `Ready` is the state before the first reveal,
/// when the crevasses have not been placed yet and the timer has not started.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Ready,
    Playing,
    Won,
    Lost,
}

impl State {
    /// A finished run is frozen: reveals, flags and chords do nothing.
    pub fn is_over(self) -> bool {
        matches!(self, State::Won | State::Lost)
    }
}

/// Why the run ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cause {
    /// Every solid cell is revealed.
    Cleared,
    /// The reveal landed on a crevasse.
    Stepped,
    /// A chord landed on a crevasse: the flags were wrong.
    Chord,
}

impl Cause {
    /// The end-screen line.
    pub fn text(self) -> &'static str {
        match self {
            Cause::Cleared => "field clear",
            Cause::Stepped => "you stepped through",
            Cause::Chord => "chorded into a crevasse",
        }
    }
}

// ---------------------------------------------------------------------------
// Cells
// ---------------------------------------------------------------------------

/// One board cell. 1 byte, and the whole board is one flat `Vec`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Cell {
    /// Hides a crevasse.
    pub mine: bool,
    /// Revealed (opened).
    pub revealed: bool,
    /// Flagged as a crevasse. Only legal on an unrevealed cell.
    pub flag: bool,
    /// Crevasses among the 8 neighbours. Meaningful once the board is dealt.
    pub adj: u8,
    /// The crevasse the player stepped into (exactly one on a loss).
    pub hit: bool,
}

// ---------------------------------------------------------------------------
// The game
// ---------------------------------------------------------------------------

/// The whole run. Every field here is part of the state, so `canonical()` and
/// `state_hash()` cover a complete session.
pub struct Game {
    pub seed: u64,
    pub rng: Rng,
    pub width: usize,
    pub height: usize,
    /// Crevasses on the board. On a board too small for the safe-zone rule the
    /// deal places as many as it legally can, so this is the *dealt* count.
    pub mines: usize,
    /// Index into [`PRESETS`], or [`CUSTOM_PRESET`].
    pub preset: usize,
    pub cells: Vec<Cell>,
    /// Cursor, as (row, col).
    pub row: usize,
    pub col: usize,
    pub state: State,
    pub cause: Option<Cause>,
    /// Ticks since the timer started (0 until the first reveal).
    pub ticks: u64,
    /// Ticks since the run began — the headless budget counter.
    pub total_ticks: u64,
    /// Reveals performed (a flood counts every cell it opens).
    pub reveals: u64,
    /// Cursor moves performed.
    pub moves: u64,
    /// Best time in seconds for the current difficulty, loaded by the front-end.
    pub best_time: u64,
    /// Best score of any difficulty, loaded by the front-end.
    pub best_score: i64,
    /// The autopilot's queued keys (demo/fixture generation). Derived from the
    /// rest of the state, so it cannot make a run non-reproducible.
    pub plan: Vec<char>,
}

impl Game {
    /// A beginner run.
    pub fn new(seed: u64) -> Self {
        Self::preset(seed, DEFAULT_PRESET)
    }

    /// A run on one of the three presets.
    pub fn preset(seed: u64, preset: usize) -> Self {
        let p = PRESETS[preset.min(PRESETS.len() - 1)];
        Self::build(seed, p.width, p.height, p.mines, preset)
    }

    /// A custom run. `mines` must be `< width * height`; the front-end validates
    /// the CLI, so this is the documented precondition.
    pub fn with(seed: u64, width: usize, height: usize, mines: usize) -> Self {
        assert!(
            width >= MIN_WIDTH && width <= MAX_WIDTH,
            "width must be {MIN_WIDTH}..={MAX_WIDTH}, got {width}"
        );
        assert!(
            height >= MIN_HEIGHT && height <= MAX_HEIGHT,
            "height must be {MIN_HEIGHT}..={MAX_HEIGHT}, got {height}"
        );
        assert!(
            mines > 0 && mines < width * height,
            "mines must be 1..{}, got {mines}",
            width * height - 1
        );
        Self::build(seed, width, height, mines, CUSTOM_PRESET)
    }

    fn build(seed: u64, width: usize, height: usize, mines: usize, preset: usize) -> Self {
        let mut g = Self {
            seed,
            rng: Rng::new(seed),
            width,
            height,
            mines,
            preset,
            cells: vec![Cell::default(); width * height],
            row: height / 2,
            col: width / 2,
            state: State::Ready,
            cause: None,
            ticks: 0,
            total_ticks: 0,
            reveals: 0,
            moves: 0,
            best_time: 0,
            best_score: 0,
            plan: Vec::new(),
        };
        // The cursor starts on a cell, never off it.
        g.row = g.row.min(height - 1);
        g.col = g.col.min(width - 1);
        g
    }

    /// Start a fresh run on the same board geometry, on the next seed.
    pub fn restart(&mut self) {
        let seed = self.seed.wrapping_add(1);
        let preset = self.preset;
        let (w, h, m) = (self.width, self.height, self.mines);
        let best_time = self.best_time;
        let best_score = self.best_score;
        *self = if preset == CUSTOM_PRESET {
            Game::with(seed, w, h, m)
        } else {
            Game::preset(seed, preset)
        };
        self.best_time = best_time;
        self.best_score = best_score;
    }

    /// Start a fresh run on a preset difficulty.
    pub fn set_preset(&mut self, preset: usize) {
        let seed = self.seed.wrapping_add(1);
        let best_time = self.best_time;
        let best_score = self.best_score;
        *self = Game::preset(seed, preset);
        self.best_time = best_time;
        self.best_score = best_score;
    }

    // -- geometry -----------------------------------------------------------

    /// Flat index of a cell.
    pub fn idx(&self, row: usize, col: usize) -> usize {
        row * self.width + col
    }

    /// The cell at (row, col). Panics out of bounds — the front-end never does.
    pub fn cell(&self, row: usize, col: usize) -> Cell {
        self.cells[self.idx(row, col)]
    }

    /// In bounds?
    pub fn inside(&self, row: i32, col: i32) -> bool {
        row >= 0 && col >= 0 && (row as usize) < self.height && (col as usize) < self.width
    }

    /// The in-bounds neighbours of a cell, in [`NEIGHBOURS`] order.
    pub fn neighbours(&self, row: usize, col: usize) -> Vec<(usize, usize)> {
        NEIGHBOURS
            .iter()
            .filter_map(|&(dr, dc)| {
                let (r, c) = (row as i32 + dr, col as i32 + dc);
                if self.inside(r, c) {
                    Some((r as usize, c as usize))
                } else {
                    None
                }
            })
            .collect()
    }

    /// Every cell index, in row-major order.
    pub fn all_cells(&self) -> Vec<(usize, usize)> {
        (0..self.height)
            .flat_map(|r| (0..self.width).map(move |c| (r, c)))
            .collect()
    }

    /// Cells that are neither revealed nor flagged, in row-major order.
    pub fn closed_cells(&self) -> Vec<(usize, usize)> {
        self.all_cells()
            .into_iter()
            .filter(|&(r, c)| {
                let cell = self.cell(r, c);
                !cell.revealed && !cell.flag
            })
            .collect()
    }

    /// How many crevasses are still unaccounted for (not flagged).
    pub fn mines_left(&self) -> usize {
        self.mines.saturating_sub(self.flag_count())
    }

    /// Current flag count.
    pub fn flag_count(&self) -> usize {
        self.cells.iter().filter(|c| c.flag).count()
    }

    /// Revealed cell count.
    pub fn revealed_count(&self) -> usize {
        self.cells.iter().filter(|c| c.revealed).count()
    }

    /// Solid (non-crevasse) cells left to open.
    pub fn safe_left(&self) -> usize {
        self.cells
            .iter()
            .filter(|c| !c.mine && !c.revealed)
            .count()
    }

    /// Elapsed time in seconds. The timer runs from the first reveal to the end
    /// of the run, so this is frozen on a finished board.
    pub fn seconds(&self) -> u64 {
        self.ticks / TICKS_PER_SECOND
    }

    /// The difficulty label: the preset name, or `custom WxH/M`.
    pub fn difficulty(&self) -> String {
        if self.preset == CUSTOM_PRESET {
            format!("custom {}x{}/{}", self.width, self.height, self.mines)
        } else {
            PRESETS[self.preset].label.to_string()
        }
    }

    // -- the rules ----------------------------------------------------------

    /// Deal the crevasses, keeping `safe_row/safe_col` and its 8 neighbours
    /// clear, then fill in every cell's neighbour count.
    ///
    /// This is the first-reveal rule, and it is a rule, not a nicety: the
    /// clicked cell and its whole 8-neighbourhood are excluded from the deal,
    /// so the first reveal of a run always opens a zero region (or, on a board
    /// too small to hold `mines` outside the safe zone, the largest legal deal).
    pub fn deal(&mut self, safe_row: usize, safe_col: usize) {
        let safe: Vec<usize> = std::iter::once(self.idx(safe_row, safe_col))
            .chain(
                self.neighbours(safe_row, safe_col)
                    .into_iter()
                    .map(|(r, c)| self.idx(r, c)),
            )
            .collect();
        let mut pool: Vec<usize> = (0..self.cells.len())
            .filter(|i| !safe.contains(i))
            .collect();
        // The deal can only place as many crevasses as the pool holds. A board
        // smaller than the safe zone plus the requested count shrinks the count
        // instead of breaking the rule.
        let count = self.mines.min(pool.len());
        for i in 0..count {
            let j = i + self.rng.below((pool.len() - i) as u64) as usize;
            pool.swap(i, j);
        }
        for &i in &pool[..count] {
            self.cells[i].mine = true;
        }
        self.mines = count;
        for r in 0..self.height {
            for c in 0..self.width {
                let n = self
                    .neighbours(r, c)
                    .iter()
                    .filter(|&&(nr, nc)| self.cell(nr, nc).mine)
                    .count();
                let i = self.idx(r, c);
                self.cells[i].adj = n as u8;
            }
        }
    }

    /// Place crevasses at fixed cell indices and start the run: the hand-built
    /// board hook. It recomputes every neighbour count, so a test can pin an
    /// exact field and watch a flood, a chord or a win play out on it.
    pub fn set_board(&mut self, mines: &[usize]) {
        for c in &mut self.cells {
            *c = Cell::default();
        }
        for &i in mines {
            assert!(i < self.cells.len(), "cell {i} is off the board");
            self.cells[i].mine = true;
        }
        self.mines = mines.len();
        for r in 0..self.height {
            for c in 0..self.width {
                let n = self
                    .neighbours(r, c)
                    .into_iter()
                    .filter(|(nr, nc)| self.cell(*nr, *nc).mine)
                    .count();
                let i = self.idx(r, c);
                self.cells[i].adj = n as u8;
            }
        }
        self.state = State::Playing;
        self.cause = None;
    }

    /// Move the cursor. It clamps at the edges (it does not wrap).
    pub fn move_cursor(&mut self, dir: Dir) {
        match dir {
            Dir::Left => self.col = self.col.saturating_sub(1),
            Dir::Right => self.col = (self.col + 1).min(self.width - 1),
            Dir::Up => self.row = self.row.saturating_sub(1),
            Dir::Down => self.row = (self.row + 1).min(self.height - 1),
        }
        self.moves += 1;
    }

    /// Reveal the cursor cell.
    ///
    /// A flag blocks the reveal (as in the original: a flagged cell is a cell
    /// the player has decided not to open). The first reveal of a run deals the
    /// crevasses first, so it is safe by construction, and starts the timer.
    pub fn reveal(&mut self) {
        if self.state.is_over() {
            return;
        }
        let (r, c) = (self.row, self.col);
        if self.cell(r, c).flag || self.cell(r, c).revealed {
            return;
        }
        if self.state == State::Ready {
            self.deal(r, c);
            self.state = State::Playing;
        }
        if self.cell(r, c).mine {
            self.step(r, c, Cause::Stepped);
            return;
        }
        self.flood(r, c);
        self.check_win();
    }

    /// Flood the contiguous zero region containing (row, col) and every number
    /// bordering it. A zero cell's neighbours have no crevasse among them, so a
    /// flood never opens a crevasse.
    pub fn flood(&mut self, row: usize, col: usize) {
        let mut stack = vec![(row, col)];
        while let Some((r, c)) = stack.pop() {
            let i = self.idx(r, c);
            if self.cells[i].revealed || self.cells[i].flag {
                continue;
            }
            self.cells[i].revealed = true;
            self.cells[i].flag = false;
            self.reveals += 1;
            if self.cells[i].adj == 0 {
                for (nr, nc) in self.neighbours(r, c) {
                    let j = self.idx(nr, nc);
                    if !self.cells[j].revealed && !self.cells[j].flag {
                        stack.push((nr, nc));
                    }
                }
            }
        }
    }

    /// Toggle the flag on the cursor cell. Only unrevealed cells can be flagged.
    pub fn flag(&mut self) {
        if self.state.is_over() {
            return;
        }
        let i = self.idx(self.row, self.col);
        if self.cells[i].revealed {
            return;
        }
        self.cells[i].flag = !self.cells[i].flag;
    }

    /// Chord: on a revealed number whose flag count equals it, open every
    /// unflagged neighbour. A wrong flag means one of those neighbours is a
    /// crevasse, and the chord steps into it — you lose, as in the original.
    ///
    /// It fires only on a revealed cell, only on a number (never on a 0), and
    /// only when the flag count equals the number exactly.
    pub fn chord(&mut self) {
        if self.state.is_over() {
            return;
        }
        let (r, c) = (self.row, self.col);
        let cell = self.cell(r, c);
        if !cell.revealed || cell.adj == 0 {
            return;
        }
        let ns = self.neighbours(r, c);
        let flags = ns
            .iter()
            .filter(|&&(nr, nc)| self.cell(nr, nc).flag)
            .count();
        if flags != cell.adj as usize {
            return;
        }
        let targets: Vec<(usize, usize)> = ns
            .into_iter()
            .filter(|&(nr, nc)| {
                let n = self.cell(nr, nc);
                !n.flag && !n.revealed
            })
            .collect();
        if targets.is_empty() {
            return;
        }
        for (nr, nc) in targets {
            if self.cell(nr, nc).mine {
                self.step(nr, nc, Cause::Chord);
                return;
            }
            self.flood(nr, nc);
        }
        self.check_win();
    }

    /// The run is lost: mark the crevasse stepped into, uncover every other
    /// crevasse, stop the timer.
    fn step(&mut self, row: usize, col: usize, cause: Cause) {
        let hit = self.idx(row, col);
        self.cells[hit].hit = true;
        for i in 0..self.cells.len() {
            if self.cells[i].mine {
                self.cells[i].revealed = true;
            }
        }
        self.state = State::Lost;
        self.cause = Some(cause);
    }

    /// The run is won when every solid cell is open. Flags are optional.
    fn check_win(&mut self) {
        if self.safe_left() == 0 {
            self.state = State::Won;
            self.cause = Some(Cause::Cleared);
        }
    }

    /// The score, computed from the state (so it can never drift out of step
    /// with the board): 10 per solid cell opened, 25 per correct flag, -20 per
    /// wrong flag, and on a win a 500 bonus plus 10 per second under double par
    /// (par is one second per crevasse).
    pub fn score(&self) -> i64 {
        let opened = self.cells.iter().filter(|c| c.revealed && !c.mine).count() as i64;
        let right = self.cells.iter().filter(|c| c.flag && c.mine).count() as i64;
        let wrong = self.cells.iter().filter(|c| c.flag && !c.mine).count() as i64;
        let mut s = 10 * opened + 25 * right - 20 * wrong;
        if self.state == State::Won {
            let par = self.mines as i64;
            s += 500 + (2 * par).saturating_sub(self.seconds() as i64) * 10;
        }
        s
    }

    // -- the loop -----------------------------------------------------------

    /// Advance one tick. The timer runs only while the run is playing, so it is
    /// 0 before the first reveal and frozen after the last.
    pub fn tick(&mut self) {
        self.total_ticks += 1;
        if self.state == State::Playing {
            self.ticks += 1;
        }
        self.plan.clear();
    }

    /// Feed one key. Returns `true` when the game asked to quit.
    ///
    /// `q` (and Ctrl-C, byte 0x03) quit. A bare `ESC` is never a quit: terminals
    /// send the arrow keys as `ESC [ A`, so ESC must stay harmless.
    pub fn key(&mut self, c: char) -> bool {
        match c {
            'q' | 'Q' | '\u{3}' => return true,
            'n' | 'N' | 'r' | 'R' => self.restart(),
            '1' => self.set_preset(0),
            '2' => self.set_preset(1),
            '3' => self.set_preset(2),
            ' ' => self.reveal(),
            'f' | 'F' => self.flag(),
            'c' | 'C' => self.chord(),
            _ => {
                if let Some(d) = Dir::parse(c) {
                    self.move_cursor(d);
                }
            }
        }
        false
    }

    // -- the autopilot (demo mode, and how the fixtures are recorded) -------

    /// The next key of a deterministic, non-cheating solver: chord when a
    /// number is satisfied, flag what the numbers force, open what they prove
    /// safe, and otherwise open the cell the constraints make least likely to
    /// be a crevasse. Derived only from the visible state, so a replay of what
    /// it records is an honest session.
    pub fn autopilot_key(&mut self) -> Option<char> {
        if self.state.is_over() {
            return None;
        }
        if self.plan.is_empty() {
            self.plan = self.build_plan();
        }
        if self.plan.is_empty() {
            return None;
        }
        Some(self.plan.remove(0))
    }

    /// Keys that walk the cursor to (row, col): horizontal first, then vertical.
    fn path_to(&self, row: usize, col: usize) -> Vec<char> {
        let mut keys = Vec::new();
        for _ in 0..self.col.saturating_sub(col) {
            keys.push(Dir::Left.key());
        }
        for _ in 0..col.saturating_sub(self.col) {
            keys.push(Dir::Right.key());
        }
        for _ in 0..self.row.saturating_sub(row) {
            keys.push(Dir::Up.key());
        }
        for _ in 0..row.saturating_sub(self.row) {
            keys.push(Dir::Down.key());
        }
        keys
    }

    /// The solver's next move as a key queue.
    fn build_plan(&self) -> Vec<char> {
        // The first reveal is a rule: open the centre of the field.
        if self.state == State::Ready {
            let mut keys = self.path_to(self.height / 2, self.width / 2);
            keys.push(' ');
            return keys;
        }
        let mut chord = None;
        let mut mine = None;
        for (r, c) in self.all_cells() {
            let cell = self.cell(r, c);
            if !cell.revealed || cell.adj == 0 {
                continue;
            }
            let ns = self.neighbours(r, c);
            let flags = ns.iter().filter(|&&(nr, nc)| self.cell(nr, nc).flag).count();
            let closed: Vec<(usize, usize)> = ns
                .into_iter()
                .filter(|&(nr, nc)| !self.cell(nr, nc).revealed && !self.cell(nr, nc).flag)
                .collect();
            let need = (cell.adj as usize).saturating_sub(flags);
            if need == 0 && !closed.is_empty() && chord.is_none() {
                // Satisfied: every closed neighbour is solid. Chord it.
                chord = Some((r, c));
            } else if need == closed.len() && !closed.is_empty() && mine.is_none() {
                // Every closed neighbour must be a crevasse. Flag the first.
                mine = Some(closed[0]);
            }
        }
        // The subset rule: two numbers whose closed sets nest say what the
        // difference of the sets is worth.
        let mut pair: Option<(usize, usize, bool)> = None;
        'pairs: for (ar, ac) in self.all_cells() {
            let a = self.cell(ar, ac);
            if !a.revealed || a.adj == 0 {
                continue;
            }
            let set_a: Vec<(usize, usize)> = self.closed_neighbours(ar, ac);
            if set_a.is_empty() {
                continue;
            }
            let rem_a = (a.adj as usize).saturating_sub(
                set_a
                    .iter()
                    .filter(|&&(nr, nc)| self.cell(nr, nc).flag)
                    .count(),
            );
            for (br, bc) in self.all_cells() {
                let b = self.cell(br, bc);
                if !b.revealed || b.adj == 0 {
                    continue;
                }
                let set_b = self.closed_neighbours(br, bc);
                if set_b.len() <= set_a.len() || !set_a.iter().all(|p| set_b.contains(p)) {
                    continue;
                }
                let rem_b = (b.adj as usize).saturating_sub(
                    set_b
                        .iter()
                        .filter(|&&(nr, nc)| self.cell(nr, nc).flag)
                        .count(),
                );
                if rem_b < rem_a {
                    continue;
                }
                let extra: Vec<(usize, usize)> = set_b
                    .iter()
                    .copied()
                    .filter(|p| !set_a.contains(p))
                    .collect();
                let mines_in_extra = rem_b - rem_a;
                if mines_in_extra == extra.len() {
                    pair = Some((extra[0].0, extra[0].1, true));
                    break 'pairs;
                }
                if mines_in_extra == 0 {
                    pair = Some((extra[0].0, extra[0].1, false));
                    break 'pairs;
                }
            }
        }
        if let Some((r, c)) = chord {
            let mut keys = self.path_to(r, c);
            keys.push('c');
            return keys;
        } else if let Some((r, c)) = mine {
            let mut keys = self.path_to(r, c);
            keys.push('f');
            return keys;
        } else if let Some((r, c, is_mine)) = pair {
            let mut keys = self.path_to(r, c);
            keys.push(if is_mine { 'f' } else { ' ' });
            return keys;
        }
        // Nothing is forced: open the cell the numbers make least likely to be a
        // crevasse (lowest summed constraint pressure, ties broken by row-major
        // order, so the choice is deterministic).
        let closed = self.closed_cells();
        if closed.is_empty() {
            return Vec::new();
        }
        let mut best: Option<(u64, usize, usize)> = None;
        for (r, c) in closed {
            let mut pressure = 0u64;
            for (nr, nc) in self.neighbours(r, c) {
                let n = self.cell(nr, nc);
                if n.revealed && n.adj > 0 {
                    let set = self.closed_neighbours(nr, nc);
                    let flags = set
                        .iter()
                        .filter(|&&(x, y)| self.cell(x, y).flag)
                        .count();
                    let need = (n.adj as usize).saturating_sub(flags);
                    if !set.is_empty() {
                        pressure += (need as u64 * 100) / set.len() as u64;
                    }
                }
            }
            if best.map(|(p, _, _)| pressure < p).unwrap_or(true) {
                best = Some((pressure, r, c));
            }
        }
        let (_, r, c) = best.expect("closed cells exist");
        let mut keys = self.path_to(r, c);
        keys.push(' ');
        keys
    }

    /// Closed (unrevealed, unflagged) neighbours of a cell, in order.
    fn closed_neighbours(&self, row: usize, col: usize) -> Vec<(usize, usize)> {
        self.neighbours(row, col)
            .into_iter()
            .filter(|&(r, c)| !self.cell(r, c).revealed && !self.cell(r, c).flag)
            .collect()
    }

    // -- determinism surface ------------------------------------------------

    /// Canonical serialization of everything that defines the run. Two games
    /// with the same string have the same [`Game::state_hash`].
    pub fn canonical(&self) -> String {
        let mut s = String::with_capacity(128);
        let _ = write!(
            s,
            "seed={seed};rng={rng:x};w={w};h={h};mines={mines};preset={preset};\
             cursor={row},{col};state={state:?};cause={cause:?};ticks={ticks};\
             total={total};reveals={reveals};moves={moves};best_time={bt};\
             best_score={bs};plan={plan};cells=[",
            seed = self.seed,
            rng = self.rng.state(),
            w = self.width,
            h = self.height,
            mines = self.mines,
            preset = self.preset,
            row = self.row,
            col = self.col,
            state = self.state,
            cause = self.cause,
            ticks = self.ticks,
            total = self.total_ticks,
            reveals = self.reveals,
            moves = self.moves,
            bt = self.best_time,
            bs = self.best_score,
            plan = self.plan.iter().collect::<String>(),
        );
        for c in &self.cells {
            let _ = write!(
                s,
                "{}{}{}{}{};",
                if c.mine { "m" } else { "." },
                if c.revealed { "r" } else { "" },
                if c.flag { "f" } else { "" },
                if c.hit { "h" } else { "" },
                c.adj
            );
        }
        let _ = write!(s, "]");
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
            ("difficulty".into(), self.difficulty()),
            ("width".into(), self.width.to_string()),
            ("height".into(), self.height.to_string()),
            ("mines".into(), self.mines.to_string()),
            ("seconds".into(), self.seconds().to_string()),
            ("ticks".into(), self.ticks.to_string()),
            ("total_ticks".into(), self.total_ticks.to_string()),
            ("reveals".into(), self.reveals.to_string()),
            ("moves".into(), self.moves.to_string()),
            ("flags".into(), self.flag_count().to_string()),
            ("mines_left".into(), self.mines_left().to_string()),
            ("revealed".into(), self.revealed_count().to_string()),
            ("safe_left".into(), self.safe_left().to_string()),
            ("score".into(), self.score().to_string()),
            ("best_time".into(), self.best_time.to_string()),
            ("best_score".into(), self.best_score.to_string()),
            ("hash".into(), format!("{:016x}", self.state_hash())),
        ];
        if let Some(c) = self.cause {
            kv.push(("cause".into(), format!("{:?}", c).to_lowercase()));
        }
        kv
    }

    // -- rendering ----------------------------------------------------------

    /// The frame, as a pure function of `(self, opts)`: one string, painted by
    /// the front-end with a single home + write.
    pub fn render(&self, opts: RenderOpts) -> String {
        let p = Painter { opts };
        let board_w = 1 + CELL_W * self.width;
        let mut out = String::with_capacity(opts.width * opts.height);

        // Vertical budget: header, panel, board, keys, status. Small windows
        // drop the panel and the header, then the keys line — never the board.
        let header = opts.width >= HEADER_W;
        let panel = opts.width >= PANEL_W;
        let keys = opts.width >= KEYS_W;
        let mut rows = opts.height;
        if header && rows > 6 {
            rows -= 1;
        }
        if panel && rows > 5 {
            rows -= 1;
        }
        // The board gets the rows it needs; the keys/status lines take what is
        // left, from the bottom up.
        let board_rows = self.height.min(rows.saturating_sub(if keys { 2 } else { 0 }));
        let spare = rows.saturating_sub(board_rows);
        let status = spare >= 1;
        let keys = keys && spare >= 2;

        let (start_row, start_col, vis_rows, vis_cols) = self.viewport(board_rows, vis_cols_for(opts.width));

        if header {
            let _ = writeln!(
                out,
                "{}",
                p.line(&format!(
                    "Icefield  {}  seed {}",
                    self.difficulty(),
                    self.seed
                ))
            );
        }
        if panel {
            let _ = writeln!(
                out,
                "{}",
                p.line(&format!(
                    "flags {:>3}  time {:>4}  score {:>6}  best {:>6}",
                    self.flag_count(),
                    self.seconds(),
                    self.score(),
                    self.best_score
                ))
            );
        }

        // The board: one leading border column, CELL_W columns per cell.
        let border = opts.width >= board_w + 1;
        for r in start_row..start_row + vis_rows {
            let mut line = String::new();
            if border {
                line.push('│');
            }
            for c in start_col..start_col + vis_cols {
                let cursor = r == self.row && c == self.col;
                line.push_str(&p.cell(self, r, c, cursor));
            }
            let _ = writeln!(out, "{}\x1b[K", line);
        }

        if status {
            let msg = match self.state {
                State::Ready => format!(
                    "first reveal is safe — {} crevasses under {} cells",
                    self.mines,
                    self.cells.len()
                ),
                State::Playing => format!(
                    "{} solid cells to open, {} crevasses unflagged",
                    self.safe_left(),
                    self.mines_left()
                ),
                State::Won | State::Lost => {
                    let c = self.cause.unwrap_or(Cause::Cleared).text();
                    format!(
                        "{c}  {}  time {}  score {}",
                        if self.state == State::Won {
                            "won"
                        } else {
                            "lost"
                        },
                        self.seconds(),
                        self.score()
                    )
                }
            };
            let _ = writeln!(out, "{}", p.line(&msg));
        }
        if keys {
            let _ = writeln!(
                out,
                "{}",
                p.dim("arrows/hjkl move · space open · f flag · c chord · 1/2/3 difficulty · n new · q quit")
            );
        }
        // Clear the tail of the screen: a window that just shrank must not keep
        // last frame's rows.
        out.push_str("\x1b[J");
        out
    }

    /// The board viewport for a `rows x cols` cell budget: as much of the board
    /// as fits, positioned so the cursor is inside it.
    fn viewport(&self, rows: usize, cols: usize) -> (usize, usize, usize, usize) {
        let vis_cols = cols.min(self.width).max(1);
        let vis_rows = rows.min(self.height).max(1);
        let start_col = if self.width <= vis_cols {
            0
        } else if self.col < vis_cols / 2 {
            0
        } else if self.col + vis_cols / 2 >= self.width {
            self.width - vis_cols
        } else {
            self.col - vis_cols / 2
        };
        let start_row = if self.height <= vis_rows {
            0
        } else if self.row < vis_rows / 2 {
            0
        } else if self.row + vis_rows / 2 >= self.height {
            self.height - vis_rows
        } else {
            self.row - vis_rows / 2
        };
        (start_row, start_col, vis_rows, vis_cols)
    }
}

/// How many board columns fit in a window of `width` terminal columns.
pub fn vis_cols_for(width: usize) -> usize {
    // One column is the border; every cell is CELL_W wide.
    (width.saturating_sub(1)) / CELL_W
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

/// The colour of a cell. The classic Minesweeper number palette: 1 blue,
/// 2 green, 3 red, 4 navy, 5 maroon, 6 teal, 7 magenta, 8 yellow.
pub fn sgr_for(cell: Cell) -> &'static str {
    if cell.flag && !cell.revealed {
        return "\x1b[1;33m";
    }
    if cell.revealed && cell.mine {
        return if cell.hit {
            "\x1b[1;37;41m"
        } else {
            "\x1b[1;31m"
        };
    }
    if cell.flag && cell.revealed {
        return "\x1b[31m"; // a flag over a crevasse, uncovered on the loss
    }
    if !cell.revealed {
        return "\x1b[2;36m";
    }
    match cell.adj {
        0 => "\x1b[0m",
        1 => "\x1b[34m",
        2 => "\x1b[32m",
        3 => "\x1b[1;31m",
        4 => "\x1b[38;5;20m",
        5 => "\x1b[31m",
        6 => "\x1b[36m",
        7 => "\x1b[35m",
        _ => "\x1b[33m",
    }
}

struct Painter {
    opts: RenderOpts,
}

impl Painter {
    /// One text line, padded and cleared to the window width.
    fn line(&self, text: &str) -> String {
        let t: String = text.chars().take(self.opts.width).collect();
        format!("{t}\x1b[K")
    }

    fn dim(&self, text: &str) -> String {
        let t = self.line(text);
        if self.opts.color {
            format!("\x1b[2m{t}\x1b[0m")
        } else {
            t
        }
    }

    /// The cursor cell as a `CELL_W`-column span in inverse video.
    fn cell(&self, g: &Game, row: usize, col: usize, cursor: bool) -> String {
        let cell = g.cell(row, col);
        let glyph = self.glyph(cell);
        let sgr = if self.opts.color { sgr_for(cell) } else { "" };
        let pad = " ".repeat(CELL_W - 1);
        let body = format!("{glyph}{pad}");
        if cursor {
            if self.opts.color {
                format!("\x1b[7m{sgr}{body}\x1b[0m")
            } else {
                // No colour: the cursor cell is the one that starts with `[`.
                format!("[{glyph}")
            }
        } else if self.opts.color {
            format!("{sgr}{body}\x1b[0m")
        } else {
            body
        }
    }

    /// The one-character face of a cell. Mono is ASCII-only: `.` closed, `F`
    /// flag, `*` crevasse, `x` a flag that was wrong, digits for the counts.
    fn glyph(&self, cell: Cell) -> char {
        if self.opts.mono {
            if cell.revealed && cell.mine {
                '*'
            } else if cell.flag && cell.revealed {
                'x'
            } else if cell.flag {
                'F'
            } else if cell.revealed {
                if cell.adj == 0 {
                    ' '
                } else {
                    char::from(b'0' + cell.adj)
                }
            } else {
                '.'
            }
        } else if cell.revealed && cell.mine {
            '✶'
        } else if cell.flag && cell.revealed {
            '✗'
        } else if cell.flag {
            '⚑'
        } else if cell.revealed {
            if cell.adj == 0 {
                ' '
            } else {
                char::from(b'0' + cell.adj)
            }
        } else {
            '░'
        }
    }
}

/// The plain-text state `--headless` prints: `key=value` lines, then the field
/// as ASCII rows. No escapes, diffable, and the shape the gate greps.
pub fn plain_state(g: &Game) -> String {
    let mut s = String::new();
    for (k, v) in g.result_kv() {
        let _ = writeln!(s, "{k}={v}");
    }
    let _ = writeln!(s, "field:");
    for r in 0..g.height {
        let _ = writeln!(
            s,
            "|{}|",
            (0..g.width)
                .map(|c| {
                    let cell = g.cell(r, c);
                    let ch = if cell.revealed && cell.mine {
                        '*'
                    } else if cell.flag && cell.revealed {
                        'x'
                    } else if cell.flag {
                        'F'
                    } else if cell.revealed {
                        if cell.adj == 0 {
                            ' '
                        } else {
                            char::from(b'0' + cell.adj)
                        }
                    } else {
                        '.'
                    };
                    if r == g.row && c == g.col {
                        format!("[{ch}]")
                    } else {
                        format!(" {ch} ")
                    }
                })
                .collect::<Vec<_>>()
                .join("|")
        );
    }
    s
}

/// Strip every ANSI escape out of a frame — the shape the frame tests diff.
pub fn strip_sgr(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut it = s.chars().peekable();
    while let Some(c) = it.next() {
        if c != '\x1b' {
            out.push(c);
            continue;
        }
        match it.next() {
            Some('[') => {
                while let Some(&n) = it.peek() {
                    let _ = it.next();
                    if ('0'..='9').contains(&n) || n == ';' || n == '?' {
                        continue;
                    }
                    break;
                }
            }
            Some(_) => {}
            None => break,
        }
    }
    out
}
