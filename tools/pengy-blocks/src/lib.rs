//! pengy-blocks — Glacier Blocks: **every rule and the renderer, in pure code**.
//!
//! This file never touches the outside world: no I/O, no clock, no threads, no
//! files, no system entropy. A run is a function of `(seed, script)`, which is
//! what makes the headless harness (`--replay`) a real acceptance test and what
//! makes `render()` a pure, diffable function of state.
//!
//! The game: ten columns, twenty rows, all seven tetrominoes, no mercy. A
//! 7-bag randomiser, gravity that accelerates by level (800 ms down to 80 ms),
//! wall kicks, a 300 ms lock delay that resets on a successful move, soft drop
//! at 1 point per row and hard drop at 2, 100/300/500/800 x level for 1/2/3/4
//! lines, a next-3 preview, a hold slot, and a game over when the spawn area
//! overlaps.
//!
//! See `spec.md` for the contract and `tests/rules.rs` for the rules asserted.

use std::fmt::Write as _;

// ---------------------------------------------------------------------------
// Tuning constants — the difficulty of the game lives here.
// ---------------------------------------------------------------------------

/// Fixed timestep of the front-end loop, in milliseconds. Every timer in the
/// game (gravity, the lock delay) is measured in ticks of this size, so a run
/// is a function of `(seed, script)` and not of wall-clock time.
pub const TICK_MS: u64 = 20;

/// The well is 10 columns wide.
pub const COLS: usize = 10;
/// ...and 20 rows tall — the part the player sees.
pub const ROWS: usize = 20;
/// Hidden spawn rows above the visible well. A piece spawns here, and the
/// spawn area is what a top-out is judged against.
pub const HIDDEN_ROWS: usize = 2;
/// Total board rows: visible plus hidden.
pub const BOARD_ROWS: usize = ROWS + HIDDEN_ROWS;

/// Gravity interval in milliseconds, indexed by `level - 1`. Level 11 and
/// above use the last entry (80 ms).
pub const GRAVITY_MS: [u64; 11] = [800, 720, 630, 550, 470, 380, 300, 220, 130, 100, 80];

/// The lock delay: a piece that cannot move down locks after this long.
pub const LOCK_DELAY_MS: u64 = 300;
/// A successful move resets the lock delay, but only this many times: without
/// the cap, a player who keeps wiggling holds a piece down for ever.
pub const MAX_LOCK_RESETS: u32 = 15;

/// Line-clear score, indexed by lines cleared, times the level.
pub const CLEAR_SCORE: [u64; 5] = [0, 100, 300, 500, 800];
/// Soft drop: 1 point per row moved by a keypress.
pub const SOFT_DROP_POINTS: u64 = 1;
/// Hard drop: 2 points per row dropped.
pub const HARD_DROP_POINTS_PER_ROW: u64 = 2;

/// The rotation kicks, in order: in place, +1 column, -1 column, +2 columns,
/// -2 columns, then up one row. `(dcol, drow)`; a negative `drow` is up.
pub const KICKS: [Cell; 6] = [(0, 0), (1, 0), (-1, 0), (2, 0), (-2, 0), (0, -1)];

/// Where a piece spawns: the top-left of its bounding box, in board
/// coordinates (row 0 is the top hidden row).
pub const SPAWN_ROW: i32 = 0;
/// ...and column 3, which centres a 3-wide piece in a 10-wide well.
pub const SPAWN_COL: i32 = 3;

/// The reference window the layout is designed for.
pub const REF_WIDTH: usize = 80;
/// Reference height: title + 20 field rows + 2 borders + legend.
pub const REF_HEIGHT: usize = 24;
/// Width at or above which the well is drawn with its border.
pub const BORDER_W: usize = COLS * 2 + 2;
/// Width at or above which the side panels (hold, next, score) are drawn.
pub const PANEL_W: usize = 40;
/// Height at or above which the side panels are drawn.
pub const PANEL_H: usize = 14;
/// Width at or above which the title line is drawn.
pub const TITLE_W: usize = 40;
/// The key legend the bottom line carries.
pub const LEGEND: &str = "h/j/k/l move  x/z rotate  space drop  c hold  p pause  r restart  q quit";

// ---------------------------------------------------------------------------
// Seeded randomness — xorshift64*, hand-written. No `rand`, no system entropy.
// ---------------------------------------------------------------------------

/// A seeded xorshift64* generator. Every run takes `--seed N` and is
/// reproducible bit for bit.
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

    /// Next value in `0..n`. `n` must be > 0.
    pub fn below(&mut self, n: u64) -> u64 {
        assert!(n > 0, "Rng::below(0)");
        self.next_u64() % n
    }
}

// ---------------------------------------------------------------------------
// The seven tetrominoes
// ---------------------------------------------------------------------------

/// The seven pieces, in the canonical bag order.
pub const PIECE_NAMES: [&str; 7] = ["I", "J", "L", "O", "S", "T", "Z"];

/// One cell of a piece: `(drow, dcol)` from the piece's anchor.
pub type Cell = (i32, i32);

/// `SHAPES[kind * 4 + rotation]` is the four cells of that rotation state,
/// inside a 4x4 bounding box (3x3 for J, L, S, T, Z — the unused row is simply
/// empty). Rotation 0 is the spawn orientation, 1 is clockwise from it,
/// 2 = 180, 3 = counter-clockwise.
///
/// These are the standard SRS orientations, so the kick table behaves the way
/// any Tetris player expects: a flat I stands up against a wall, a T reaches a
/// column it could not reach flat.
pub const SHAPES: [[Cell; 4]; 28] = [
    // I
    [(1, 0), (1, 1), (1, 2), (1, 3)],
    [(0, 2), (1, 2), (2, 2), (3, 2)],
    [(2, 0), (2, 1), (2, 2), (2, 3)],
    [(0, 1), (1, 1), (2, 1), (3, 1)],
    // J
    [(0, 0), (1, 0), (1, 1), (1, 2)],
    [(0, 1), (1, 1), (2, 1), (2, 0)],
    [(1, 0), (1, 1), (1, 2), (2, 2)],
    [(0, 1), (0, 2), (1, 1), (2, 1)],
    // L
    [(0, 2), (1, 0), (1, 1), (1, 2)],
    [(0, 1), (1, 1), (2, 1), (2, 2)],
    [(1, 0), (1, 1), (1, 2), (2, 0)],
    [(0, 0), (0, 1), (1, 1), (2, 1)],
    // O — a 2x2 square; rotating it changes nothing, which is the rule.
    [(1, 1), (1, 2), (2, 1), (2, 2)],
    [(1, 1), (1, 2), (2, 1), (2, 2)],
    [(1, 1), (1, 2), (2, 1), (2, 2)],
    [(1, 1), (1, 2), (2, 1), (2, 2)],
    // S
    [(0, 1), (0, 2), (1, 0), (1, 1)],
    [(0, 1), (1, 1), (1, 2), (2, 2)],
    [(1, 1), (1, 2), (2, 0), (2, 1)],
    [(0, 0), (1, 0), (1, 1), (2, 1)],
    // T
    [(0, 1), (1, 0), (1, 1), (1, 2)],
    [(0, 1), (1, 1), (1, 2), (2, 1)],
    [(1, 0), (1, 1), (1, 2), (2, 1)],
    [(0, 1), (1, 0), (1, 1), (2, 1)],
    // Z
    [(0, 0), (0, 1), (1, 1), (1, 2)],
    [(0, 2), (1, 1), (1, 2), (2, 1)],
    [(1, 0), (1, 1), (2, 1), (2, 2)],
    [(0, 1), (1, 0), (1, 1), (2, 0)],
];

/// The four cells of `kind` in `rot`.
pub fn shape(kind: usize, rot: usize) -> [Cell; 4] {
    SHAPES[kind * 4 + rot % 4]
}

/// The absolute board cells of a piece.
pub fn cells(kind: usize, rot: usize, row: i32, col: i32) -> [Cell; 4] {
    shape(kind, rot).map(|(dr, dc)| (row + dr, col + dc))
}

/// Does this piece placement fit? Every cell must be inside the well (the floor
/// and both walls are solid) and on an empty cell.
pub fn fits(board: &[[bool; COLS]; BOARD_ROWS], kind: usize, rot: usize, row: i32, col: i32) -> bool {
    for (r, c) in cells(kind, rot, row, col) {
        if r < 0 || r >= BOARD_ROWS as i32 || c < 0 || c >= COLS as i32 {
            return false;
        }
        if board[r as usize][c as usize] {
            return false;
        }
    }
    true
}

/// The row the piece lands on: the lowest anchor row it reaches going straight
/// down from `row`. Pure, so the tests and the autopilot can pin it.
pub fn landing_row(
    board: &[[bool; COLS]; BOARD_ROWS],
    kind: usize,
    rot: usize,
    row: i32,
    col: i32,
) -> i32 {
    let mut r = row;
    while fits(board, kind, rot, r + 1, col) {
        r += 1;
    }
    r
}

/// An empty well.
pub fn empty_board() -> [[bool; COLS]; BOARD_ROWS] {
    [[false; COLS]; BOARD_ROWS]
}

// ---------------------------------------------------------------------------
// The 7-bag randomiser
// ---------------------------------------------------------------------------

/// The 7-bag randomiser: every piece appears exactly once per bag, so a player
/// never waits for an I for ever and never gets three Ss in a row.
///
/// Two bags are kept — the current one and the next — which is what lets the
/// next-3 preview look ahead without consuming randomness.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Bag {
    pub rng: Rng,
    pub cur: [usize; 7],
    pub next: [usize; 7],
    pub pos: usize,
}

impl Bag {
    /// Seed the bag. The first bag is shuffled too, so seed 1 is not special.
    pub fn new(seed: u64) -> Self {
        let mut rng = Rng::new(seed);
        let cur = shuffle(&mut rng);
        let next = shuffle(&mut rng);
        Self {
            rng,
            cur,
            next,
            pos: 0,
        }
    }

    /// The next piece kind, 0..6.
    pub fn take(&mut self) -> usize {
        if self.pos == 7 {
            self.cur = self.next;
            self.next = shuffle(&mut self.rng);
            self.pos = 0;
        }
        let k = self.cur[self.pos];
        self.pos += 1;
        k
    }

    /// The next `n` pieces, without consuming them (`n <= 7`).
    pub fn peek(&self, n: usize) -> Vec<usize> {
        let mut v = Vec::with_capacity(n);
        for i in self.pos..7 {
            if v.len() == n {
                return v;
            }
            v.push(self.cur[i]);
        }
        for i in 0..7 {
            if v.len() == n {
                return v;
            }
            v.push(self.next[i]);
        }
        v
    }
}

/// Fisher-Yates over 0..6.
fn shuffle(rng: &mut Rng) -> [usize; 7] {
    let mut a = [0usize, 1, 2, 3, 4, 5, 6];
    for i in (1..7).rev() {
        let j = rng.below((i + 1) as u64) as usize;
        a.swap(i, j);
    }
    a
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

/// The game's states. A finished game is frozen: ticking changes nothing.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Play,
    Paused,
    Over,
}

/// Why the game ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cause {
    /// The spawn area overlapped: the well is full to the top.
    TopOut,
}

impl Cause {
    pub fn name(&self) -> &'static str {
        match self {
            Cause::TopOut => "top-out",
        }
    }
}

/// Gravity for a level, in milliseconds.
pub fn gravity_ms(level: usize) -> u64 {
    GRAVITY_MS[(level - 1).min(GRAVITY_MS.len() - 1)]
}

/// The level a line count is worth: level 1 to start, +1 every 10 lines.
pub fn level_for(lines: u64) -> usize {
    1 + (lines / 10) as usize
}

// ---------------------------------------------------------------------------
// The game
// ---------------------------------------------------------------------------

/// The whole game: the well, the current piece, the bag, the score.
#[derive(Clone, Debug)]
pub struct Game {
    pub seed: u64,
    pub ticks: u64,
    /// Elapsed game time, in milliseconds: `ticks * `[`TICK_MS`].
    pub ms: u64,
    pub state: State,
    pub cause: Option<Cause>,
    /// The well, row 0 at the top. The first [`HIDDEN_ROWS`] rows are hidden.
    pub board: [[bool; COLS]; BOARD_ROWS],
    /// Which piece locked into each cell, so the well keeps its colours.
    pub colour: [[u8; COLS]; BOARD_ROWS],
    pub kind: usize,
    pub rot: usize,
    pub row: i32,
    pub col: i32,
    /// The held piece, `None` when the slot is empty.
    pub held: Option<usize>,
    /// One hold per piece: `false` from the moment the piece is held until it
    /// locks.
    pub can_hold: bool,
    pub score: u64,
    pub lines: u64,
    pub level: usize,
    pub pieces: u64,
    /// `clears[n]` = how many times `n` lines were cleared at once.
    pub clears: [u64; 5],
    /// Points scored by soft drop and by hard drop, reported separately.
    pub soft_points: u64,
    pub hard_points: u64,
    pub bag: Bag,
    /// The autopilot's plan for the current piece: `(rotation, column)`.
    /// Computed once when the piece spawns, so the demo player steers to one
    /// place instead of re-deciding every tick (a state-dependent tie-break
    /// makes it oscillate between two equal placements for ever).
    pub plan: Option<(usize, i32)>,
    /// Milliseconds since the last gravity step.
    pub gravity_acc: u64,
    /// Milliseconds the current piece has been grounded.
    pub lock_acc: u64,
    /// How many times the lock delay has been reset for this piece.
    pub lock_resets: u32,
    /// The persisted record, filled in by the front-end.
    pub best_score: u64,
    pub best_lines: u64,
    pub best_level: usize,
    pub best_games: u64,
}

impl Game {
    /// A game on `seed`.
    pub fn new(seed: u64) -> Self {
        let mut g = Game {
            seed,
            ticks: 0,
            ms: 0,
            state: State::Play,
            cause: None,
            board: empty_board(),
            colour: [[0; COLS]; BOARD_ROWS],
            kind: 0,
            rot: 0,
            row: SPAWN_ROW,
            col: SPAWN_COL,
            held: None,
            can_hold: true,
            score: 0,
            lines: 0,
            level: 1,
            pieces: 0,
            clears: [0; 5],
            soft_points: 0,
            hard_points: 0,
            bag: Bag::new(seed),
            plan: None,
            gravity_acc: 0,
            lock_acc: 0,
            lock_resets: 0,
            best_score: 0,
            best_lines: 0,
            best_level: 0,
            best_games: 0,
        };
        g.spawn();
        g
    }

    /// The cells of the current piece.
    pub fn cur_cells(&self) -> [Cell; 4] {
        cells(self.kind, self.rot, self.row, self.col)
    }

    /// Can the current piece move down?
    pub fn can_move_down(&self) -> bool {
        fits(&self.board, self.kind, self.rot, self.row + 1, self.col)
    }

    /// Is the current piece resting on something?
    pub fn grounded(&self) -> bool {
        !self.can_move_down()
    }

    /// Spawn the next piece. A spawn that overlaps the well is the game over.
    fn spawn(&mut self) {
        self.kind = self.bag.take();
        self.rot = 0;
        self.row = SPAWN_ROW;
        self.col = SPAWN_COL;
        self.can_hold = true;
        self.gravity_acc = 0;
        self.lock_acc = 0;
        self.lock_resets = 0;
        self.plan = None;
        self.pieces += 1;
        if !fits(&self.board, self.kind, self.rot, self.row, self.col) {
            self.state = State::Over;
            self.cause = Some(Cause::TopOut);
        }
    }

    /// Try a move. On success the lock delay resets (up to [`MAX_LOCK_RESETS`]
    /// times), which is the rule that lets a player slide a piece along the
    /// floor instead of having it freeze where it landed.
    fn try_move(&mut self, drow: i32, dcol: i32) -> bool {
        if self.state != State::Play {
            return false;
        }
        if !fits(&self.board, self.kind, self.rot, self.row + drow, self.col + dcol) {
            return false;
        }
        self.row += drow;
        self.col += dcol;
        self.reset_lock();
        true
    }

    /// Reset the lock delay after a successful move, if the piece is grounded
    /// and the reset budget is not spent.
    fn reset_lock(&mut self) {
        if self.grounded() && self.lock_resets < MAX_LOCK_RESETS {
            self.lock_acc = 0;
            self.lock_resets += 1;
        }
    }

    /// Rotate by `dir` (+1 clockwise, -1 counter-clockwise), trying the kicks in
    /// [`KICKS`]. True when a rotation happened.
    fn rotate(&mut self, dir: i32) -> bool {
        if self.state != State::Play {
            return false;
        }
        let (rot, kick) = self.kick(self.rot, dir);
        let Some((dc, dr)) = kick else {
            return false;
        };
        self.rot = rot;
        self.row += dr;
        self.col += dc;
        self.reset_lock();
        true
    }

    /// Which kick the rotation to `rot + dir` would use on this board: the
    /// target rotation and, if any kick fits, its `(dcol, drow)`. Exposed so a
    /// test can walk the whole table — in place, +/-1 column, +/-2 columns, up
    /// one row — against hand-built boards.
    pub fn kick(&self, from: usize, dir: i32) -> (usize, Option<Cell>) {
        let rot = ((from as i32 + dir).rem_euclid(4)) as usize;
        for &(dc, dr) in KICKS.iter() {
            if fits(&self.board, self.kind, rot, self.row + dr, self.col + dc) {
                return (rot, Some((dc, dr)));
            }
        }
        (rot, None)
    }

    /// Soft drop: one row, 1 point. A soft drop onto the floor starts the lock
    /// delay, which is the standard behaviour.
    fn soft_drop(&mut self) -> bool {
        if self.try_move(1, 0) {
            self.score += SOFT_DROP_POINTS;
            self.soft_points += SOFT_DROP_POINTS;
            true
        } else {
            false
        }
    }

    /// Hard drop: fall to the floor, 2 points per row, lock immediately.
    fn hard_drop(&mut self) {
        if self.state != State::Play {
            return;
        }
        let land = landing_row(&self.board, self.kind, self.rot, self.row, self.col);
        let rows = (land - self.row).max(0) as u64;
        self.row = land;
        let pts = rows * HARD_DROP_POINTS_PER_ROW;
        self.score += pts;
        self.hard_points += pts;
        self.lock();
    }

    /// Lock the current piece into the well, clear lines, score them, spawn.
    fn lock(&mut self) {
        if self.state != State::Play {
            return;
        }
        let colour = piece_colour(self.kind);
        for (r, c) in self.cur_cells() {
            if r >= 0 && (r as usize) < BOARD_ROWS && c >= 0 && (c as usize) < COLS {
                self.board[r as usize][c as usize] = true;
                self.colour[r as usize][c as usize] = colour as u8;
            }
        }
        let n = self.clear_lines();
        if n > 0 {
            self.lines += n as u64;
            self.clears[n] += 1;
            self.score += CLEAR_SCORE[n] * self.level as u64;
            self.level = level_for(self.lines);
        }
        self.spawn();
    }

    /// Remove every full row and collapse the well: the rows above the clears
    /// fall by exactly the number of cleared rows below them. Returns how many
    /// rows went.
    fn clear_lines(&mut self) -> usize {
        let mut kept: [[bool; COLS]; BOARD_ROWS] = empty_board();
        let mut kept_colour: [[u8; COLS]; BOARD_ROWS] = [[0; COLS]; BOARD_ROWS];
        // Fill from the floor upwards: a row falls by the number of cleared
        // rows *below* it, which is what "the well collapses" means. Packing
        // from the top makes the stack rise into the spawn area instead.
        let mut w = BOARD_ROWS;
        let mut n = 0usize;
        for r in (0..BOARD_ROWS).rev() {
            if self.board[r].iter().all(|c| *c) {
                n += 1;
                continue;
            }
            w -= 1;
            kept[w] = self.board[r];
            kept_colour[w] = self.colour[r];
        }
        if n > 0 {
            for r in 0..BOARD_ROWS {
                self.board[r] = kept[r];
                self.colour[r] = kept_colour[r];
            }
        }
        n
    }

    /// Swap the current piece with the hold slot. One hold per piece.
    fn hold(&mut self) -> bool {
        if self.state != State::Play || !self.can_hold {
            return false;
        }
        let incoming = match self.held {
            Some(k) => k,
            None => self.bag.take(),
        };
        self.held = Some(self.kind);
        self.kind = incoming;
        self.rot = 0;
        self.row = SPAWN_ROW;
        self.col = SPAWN_COL;
        self.gravity_acc = 0;
        self.lock_acc = 0;
        self.lock_resets = 0;
        self.plan = None;
        self.can_hold = false;
        if !fits(&self.board, self.kind, self.rot, self.row, self.col) {
            self.state = State::Over;
            self.cause = Some(Cause::TopOut);
        }
        true
    }

    /// The next three pieces, for the preview panel.
    pub fn preview(&self) -> Vec<usize> {
        self.bag.peek(3)
    }

    /// Advance the game by one [`TICK_MS`] tick: gravity, then the lock delay.
    /// A paused or finished game does nothing.
    pub fn tick(&mut self) {
        if self.state != State::Play {
            return;
        }
        self.ticks += 1;
        self.ms += TICK_MS;

        self.gravity_acc += TICK_MS;
        if self.gravity_acc >= gravity_ms(self.level) {
            self.gravity_acc = 0;
            let _ = self.try_move(1, 0);
        }

        if self.grounded() {
            self.lock_acc += TICK_MS;
            if self.lock_acc >= LOCK_DELAY_MS {
                self.lock();
            }
        } else {
            self.lock_acc = 0;
        }
    }

    /// Start a fresh game on the next seed in the family. The persisted record
    /// is carried over; the score is not.
    pub fn restart(&mut self) {
        let seed = self.seed.wrapping_add(1);
        let rec = (
            self.best_score,
            self.best_lines,
            self.best_level,
            self.best_games,
        );
        *self = Game::new(seed);
        (self.best_score, self.best_lines, self.best_level, self.best_games) = rec;
    }

    /// The key table. Returns `true` for the quit key.
    ///
    /// `h`/`a` left, `l`/`d` right, `j` soft drop, `k`/`x` rotate clockwise,
    /// `z` counter-clockwise, `space` hard drop, `c` hold, `p` pause, `r`
    /// restart, `q` quit. Everything else is ignored.
    pub fn key(&mut self, c: char) -> bool {
        match c {
            'q' | 'Q' => return true,
            'r' | 'R' => {
                self.restart();
                return false;
            }
            'p' | 'P' => {
                match self.state {
                    State::Play => self.state = State::Paused,
                    State::Paused => self.state = State::Play,
                    State::Over => {}
                }
                return false;
            }
            _ => {}
        }
        if self.state != State::Play {
            return false;
        }
        match c {
            'h' | 'a' | 'H' | 'A' => {
                self.try_move(0, -1);
            }
            'l' | 'd' | 'L' | 'D' => {
                self.try_move(0, 1);
            }
            'j' | 'J' => {
                self.soft_drop();
            }
            'k' | 'x' | 'K' | 'X' => {
                self.rotate(1);
            }
            'z' | 'Z' => {
                self.rotate(-1);
            }
            ' ' => self.hard_drop(),
            'c' | 'C' => {
                self.hold();
            }
            _ => {}
        }
        false
    }

    // -----------------------------------------------------------------------
    // The autopilot — the demo / headless player.
    // -----------------------------------------------------------------------

    /// The keys the autopilot wants to press on this tick: a greedy placement
    /// player that scores every rotation and column by the board it leaves
    /// (lines cleared, height, holes, bumpiness, wells), steers to the plan it
    /// made when the piece spawned, then hard drops. One key per tick, so a
    /// session is a real key script.
    ///
    /// It is a pure function of state, so `--headless` and `--record` produce a
    /// session that `--replay` reproduces bit for bit.
    pub fn autopilot_keys(&mut self) -> String {
        if self.state != State::Play {
            return String::new();
        }
        if self.plan.is_none() {
            self.plan = self.autopilot_target();
        }
        let (rot, col) = self.plan.unwrap_or((self.rot, self.col));
        if self.rot != rot {
            // Rotate first: which columns the piece occupies depends on its
            // rotation. Take the short way round; if the rotation is blocked,
            // slide first (a kick needs room, and room is what sliding buys).
            let dir = if (rot as i32 - self.rot as i32).rem_euclid(4) == 3 {
                -1
            } else {
                1
            };
            let newrot = ((self.rot as i32 + dir).rem_euclid(4)) as usize;
            let kick = KICKS
                .iter()
                .any(|(dc, dr)| fits(&self.board, self.kind, newrot, self.row + dr, self.col + dc));
            if kick {
                return String::from(if dir == 1 { "x" } else { "z" });
            }
            if self.col > col && fits(&self.board, self.kind, self.rot, self.row, self.col - 1) {
                return String::from("h");
            }
            if self.col < col && fits(&self.board, self.kind, self.rot, self.row, self.col + 1) {
                return String::from("l");
            }
            return String::from(" ");
        }
        if self.col > col {
            return String::from(if fits(&self.board, self.kind, self.rot, self.row, self.col - 1) {
                "h"
            } else {
                " "
            });
        }
        if self.col < col {
            return String::from(if fits(&self.board, self.kind, self.rot, self.row, self.col + 1) {
                "l"
            } else {
                " "
            });
        }
        String::from(" ")
    }

    /// The best (rotation, column) for the current piece, `None` when nothing
    /// fits. Two-ply: score each placement of this piece, then add the best
    /// placement of the *next* piece on the board it leaves. One-ply greedy
    /// cannot see that four flat pieces make a line, so it stacks a tower in a
    /// corner and tops out in eighteen pieces; two-ply stacking clears lines.
    ///
    /// The scan order (rotation 0 first, then the left-most column) is the
    /// tie-break, and it is a function of the board only — never of where the
    /// piece currently is, which is what keeps the plan stable.
    fn autopilot_target(&self) -> Option<(usize, i32)> {
        let mut best: Option<(i64, usize, i32)> = None;
        for rot in 0..4 {
            for col in -2..(COLS as i32 + 2) {
                if !fits(&self.board, self.kind, rot, SPAWN_ROW, col) {
                    continue;
                }
                let land = landing_row(&self.board, self.kind, rot, SPAWN_ROW, col);
                let s = evaluate(&self.board, self.kind, rot, land, col);
                if best.map(|(bs, _, _)| s > bs).unwrap_or(true) {
                    best = Some((s, rot, col));
                }
            }
        }
        best.map(|(_, r, c)| (r, c))
    }
}

/// Place a piece on the board and collapse the rows it completes. Pure, so the
/// autopilot can look ahead and the tests can pin the collapse.
pub fn place(
    board: &[[bool; COLS]; BOARD_ROWS],
    kind: usize,
    rot: usize,
    row: i32,
    col: i32,
) -> ([[bool; COLS]; BOARD_ROWS], usize) {
    let mut b = *board;
    for (r, c) in cells(kind, rot, row, col) {
        if r >= 0 && (r as usize) < BOARD_ROWS && c >= 0 && (c as usize) < COLS {
            b[r as usize][c as usize] = true;
        }
    }
    let mut kept: [[bool; COLS]; BOARD_ROWS] = empty_board();
    let mut w = BOARD_ROWS;
    let mut n = 0usize;
    for r in (0..BOARD_ROWS).rev() {
        if b[r].iter().all(|c| *c) {
            n += 1;
            continue;
        }
        w -= 1;
        kept[w] = b[r];
    }
    if n > 0 {
        for r in 0..BOARD_ROWS {
            b[r] = kept[r];
        }
    }
    (b, n)
}

/// The static worth of a board: flat, hole-free and low is good, and a deep
/// well is worth having because an I dropped into it clears four.
pub fn board_score(board: &[[bool; COLS]; BOARD_ROWS]) -> i64 {
    let (holes, max_h, bump, wells) = board_stats(board);
    let agg: i64 = column_heights(board).iter().sum::<usize>() as i64;
    -(agg) * WEIGHT_HEIGHT
        - (max_h as i64) * WEIGHT_HEIGHT
        - (holes as i64) * WEIGHT_HOLES
        - (bump as i64) * WEIGHT_BUMP
        + (wells as i64) * WEIGHT_WELL
}

/// The column heights of a board: for each column, the distance from the floor
/// to the topmost filled cell. An empty column is 0.
pub fn column_heights(board: &[[bool; COLS]; BOARD_ROWS]) -> [usize; COLS] {
    let mut h = [0usize; COLS];
    for c in 0..COLS {
        for r in 0..BOARD_ROWS {
            if board[r][c] {
                h[c] = h[c].max(BOARD_ROWS - r);
            }
        }
    }
    h
}

/// `(holes, max height, bumpiness, well depth)` for a board.
fn board_stats(board: &[[bool; COLS]; BOARD_ROWS]) -> (usize, usize, usize, usize) {
    let heights = column_heights(board);
    // A hole is an empty cell *under* the topmost filled cell of its column:
    // space that no piece can reach from above any more. Scanning top-down is
    // the whole point — scanning bottom-up counts every cell of open air above
    // the stack as a hole, which makes every board look catastrophic and turns
    // the demo player into a corner-stacking hermit.
    let mut holes = 0usize;
    for c in 0..COLS {
        let mut seen = false;
        for r in 0..BOARD_ROWS {
            if board[r][c] {
                seen = true;
            } else if seen {
                holes += 1;
            }
        }
    }
    let max_h = *heights.iter().max().unwrap_or(&0);
    let mut bump = 0usize;
    for c in 1..COLS {
        bump += heights[c].abs_diff(heights[c - 1]);
    }
    let mut wells = 0usize;
    for c in 0..COLS {
        let l = if c == 0 { heights[c] + 1 } else { heights[c - 1] };
        let r = if c + 1 == COLS { heights[c] + 1 } else { heights[c + 1] };
        if heights[c] < l && heights[c] < r {
            wells += l.abs_diff(heights[c]).min(r.abs_diff(heights[c]));
        }
    }
    (holes, max_h, bump, wells)
}

/// The row the stacking strategy is filling: the lowest row of the board that
/// is not yet complete. `None` when the board is empty of work.
pub fn target_row(board: &[[bool; COLS]; BOARD_ROWS]) -> Option<usize> {
    (0..BOARD_ROWS).rev().find(|r| !board[*r].iter().all(|c| *c))
}

/// The autopilot's weights. The demo player is a *stacking strategy*, not a
/// search, and the weights are a strict priority order rather than a tuning
/// surface: clear the line, then keep the stack as low as possible, then keep
/// it hole-free, then keep the right-hand column as the well an I drops into,
/// then sit as low as possible, then fill from the left. That is the order a
/// competent human plays in, and it is why the demo clears lines instead of
/// building a tower in a corner.
pub const WEIGHT_CLEAR: i64 = 1_000_000_000;
pub const WEIGHT_MAX_HEIGHT: i64 = 1_000_000;
pub const WEIGHT_HOLES: i64 = 100_000;
pub const WEIGHT_WELL_COLUMN: i64 = 60_000;
pub const WEIGHT_BUMP: i64 = 5_000;
pub const WEIGHT_DEPTH: i64 = 100;
pub const WEIGHT_COLUMN: i64 = 1; // reserved: the fill order
/// The weights [`board_score`] uses: the static worth of a board, independent
/// of any placement.
pub const WEIGHT_HEIGHT: i64 = 2;
pub const WEIGHT_WELL: i64 = 10;

/// The column the autopilot keeps empty as its I well.
pub const WELL_COLUMN: usize = COLS - 1;

/// The autopilot's score for placing `kind`/`rot` with its anchor at `(row,
/// col)`: higher is better. See the weights for the priority order.
pub fn evaluate(
    board: &[[bool; COLS]; BOARD_ROWS],
    kind: usize,
    rot: usize,
    row: i32,
    col: i32,
) -> i64 {
    let (after, cleared) = place(board, kind, rot, row, col);
    let (holes, max_h, bump, _) = board_stats(&after);
    let sum_rows: i64 = cells(kind, rot, row, col)
        .iter()
        .map(|(r, _)| *r as i64)
        .sum();
    let in_well = cells(kind, rot, row, col)
        .iter()
        .filter(|(_, c)| *c == WELL_COLUMN as i32)
        .count() as i64;
    (cleared as i64) * WEIGHT_CLEAR
        - (max_h as i64) * WEIGHT_MAX_HEIGHT
        - (holes as i64) * WEIGHT_HOLES
        - in_well * WEIGHT_WELL_COLUMN
        - (bump as i64) * WEIGHT_BUMP
        + sum_rows * WEIGHT_DEPTH
}

// ---------------------------------------------------------------------------
// State reporting — the headless contract
// ---------------------------------------------------------------------------

impl Game {
    /// The state as one canonical string: the well, the piece, the bag, the
    /// score. [`Game::state_hash`] hashes it, so "same seed, same script, same
    /// run" is one number.
    pub fn canonical(&self) -> String {
        let mut s = String::new();
        for r in 0..BOARD_ROWS {
            for c in 0..COLS {
                s.push(if self.board[r][c] { '#' } else { '.' });
            }
        }
        let _ = write!(
            s,
            "|p{}:{}:{}|held={held:?}|score={score}|lines={lines}|level={level}|pieces={pieces}|clears={clears:?}|soft={soft}|hard={hard}|ticks={ticks}|ms={ms}|state={state:?}|cause={cause:?}|rng={rng:016x}|bag={pos}/{cur:?}+{next:?}",
            self.kind,
            self.row,
            self.col,
            held = self.held,
            score = self.score,
            lines = self.lines,
            level = self.level,
            pieces = self.pieces,
            clears = self.clears,
            soft = self.soft_points,
            hard = self.hard_points,
            ticks = self.ticks,
            ms = self.ms,
            state = self.state,
            cause = self.cause,
            rng = self.bag.rng.state(),
            pos = self.bag.pos,
            cur = self.bag.cur,
            next = self.bag.next,
        );
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

    /// The game as `key=value` pairs — what `--replay` prints on exit 0, and
    /// what the acceptance harness greps.
    pub fn result_kv(&self) -> Vec<(String, String)> {
        let mut kv = vec![
            ("state".into(), format!("{:?}", self.state).to_lowercase()),
            ("seed".into(), self.seed.to_string()),
            ("ticks".into(), self.ticks.to_string()),
            ("ms".into(), self.ms.to_string()),
            ("score".into(), self.score.to_string()),
            ("lines".into(), self.lines.to_string()),
            ("level".into(), self.level.to_string()),
            ("pieces".into(), self.pieces.to_string()),
            ("clear1".into(), self.clears[1].to_string()),
            ("clear2".into(), self.clears[2].to_string()),
            ("clear3".into(), self.clears[3].to_string()),
            ("clear4".into(), self.clears[4].to_string()),
            ("soft_points".into(), self.soft_points.to_string()),
            ("hard_points".into(), self.hard_points.to_string()),
            (
                "held".into(),
                self.held
                    .map(|k| PIECE_NAMES[k].to_string())
                    .unwrap_or_else(|| "-".into()),
            ),
            ("best_score".into(), self.best_score.to_string()),
            ("best_lines".into(), self.best_lines.to_string()),
            ("best_level".into(), self.best_level.to_string()),
            ("best_games".into(), self.best_games.to_string()),
            ("hash".into(), format!("{:016x}", self.state_hash())),
        ];
        if let Some(c) = self.cause {
            kv.push(("cause".into(), c.name().to_string()));
        }
        kv
    }
}

/// The preview as letters, for the plain-text report.
pub fn preview_names(v: &[usize]) -> String {
    v.iter()
        .map(|k| PIECE_NAMES[*k].to_string())
        .collect::<Vec<_>>()
        .join(" ")
}

/// The plain-text state `--headless` prints: `key=value` lines, then the well
/// as ASCII rows. No escapes, diffable, and the shape the gate greps.
pub fn plain_state(g: &Game) -> String {
    let mut s = String::new();
    for (k, v) in g.result_kv() {
        let _ = writeln!(s, "{k}={v}");
    }
    let _ = writeln!(s, "next={}", preview_names(&g.preview()));
    let _ = writeln!(s, "well:");
    let cur = g.cur_cells();
    for r in HIDDEN_ROWS..BOARD_ROWS {
        let mut line = String::new();
        for c in 0..COLS {
            let on_piece = cur
                .iter()
                .any(|(pr, pc)| *pr == r as i32 && *pc == c as i32);
            let ch = if g.board[r][c] {
                '#'
            } else if on_piece && g.state == State::Play {
                'o'
            } else {
                '.'
            };
            line.push(ch);
        }
        let _ = writeln!(s, "|{line}|");
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

/// The glyph set: box-drawing and blocks, or pure ASCII under `--mono`.
#[derive(Clone, Copy, Debug)]
struct Painter {
    mono: bool,
    color: bool,
}

impl Painter {
    fn tl(&self) -> char {
        if self.mono {
            '+'
        } else {
            '┌'
        }
    }
    fn tr(&self) -> char {
        if self.mono {
            '+'
        } else {
            '┐'
        }
    }
    fn bl(&self) -> char {
        if self.mono {
            '+'
        } else {
            '└'
        }
    }
    fn br(&self) -> char {
        if self.mono {
            '+'
        } else {
            '┘'
        }
    }
    fn h(&self) -> char {
        if self.mono {
            '-'
        } else {
            '─'
        }
    }
    fn v(&self) -> char {
        if self.mono {
            '|'
        } else {
            '│'
        }
    }
    /// The two characters of a filled cell, coloured by the piece that locked
    /// there. Colour is a background fill, which is what makes a 2-char cell
    /// read as a solid block.
    fn cell(&self, colour: usize) -> &'static str {
        match (self.mono, self.color) {
            (true, _) => "[]",
            (false, false) => "[]",
            (false, true) => match colour {
                1 => "\x1b[41m  \x1b[0m",
                2 => "\x1b[42m  \x1b[0m",
                3 => "\x1b[43m  \x1b[0m",
                4 => "\x1b[44m  \x1b[0m",
                5 => "\x1b[45m  \x1b[0m",
                6 => "\x1b[46m  \x1b[0m",
                _ => "\x1b[47m  \x1b[0m",
            },
        }
    }
    /// The two characters of a ghost (landing preview) cell.
    fn ghost(&self) -> &'static str {
        if self.mono {
            ".."
        } else {
            "░░"
        }
    }
    fn empty(&self) -> &'static str {
        "  "
    }
}

/// The colour index of a piece: I cyan, J blue, L and O yellow, S green,
/// T magenta, Z red.
pub fn piece_colour(kind: usize) -> usize {
    match kind {
        0 => 6, // I — cyan
        1 => 4, // J — blue
        2 => 3, // L — yellow
        3 => 3, // O — yellow
        4 => 2, // S — green
        5 => 5, // T — magenta
        _ => 1, // Z — red
    }
}

/// Fit a line to the window width: pad it with spaces, or clip it, counting
/// only the characters the terminal draws (ANSI escapes take no columns).
/// Clipping matters in a narrow window: the legend and the title are longer
/// than 40 columns, and a line that overflows makes the terminal scroll.
pub fn pad_to(s: &str, width: usize) -> String {
    let mut out = String::new();
    let mut visible = 0usize;
    let chars: Vec<char> = s.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '\x1b' {
            out.push('\x1b');
            i += 1;
            while i < chars.len() {
                let c = chars[i];
                out.push(c);
                i += 1;
                if c == 'm' {
                    break;
                }
            }
        } else {
            if visible >= width {
                break;
            }
            out.push(chars[i]);
            visible += 1;
            i += 1;
        }
    }
    for _ in visible..width {
        out.push(' ');
    }
    out
}

/// Pad a line to the window width, counting only the characters the terminal
/// draws (ANSI escapes take no columns).
pub fn pad_only(s: &str, width: usize) -> String {
    let mut visible = 0usize;
    let chars: Vec<char> = s.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '\x1b' {
            while i < chars.len() && chars[i] != 'm' {
                i += 1;
            }
            i += 1; // the 'm'
        } else {
            visible += 1;
            i += 1;
        }
    }
    let mut out = String::with_capacity(s.len() + width.saturating_sub(visible));
    out.push_str(s);
    for _ in visible..width {
        out.push(' ');
    }
    out
}

impl Game {
    /// The frame: one string, one write. Same state, same bytes.
    pub fn render(&self, opts: RenderOpts) -> String {
        let p = Painter {
            mono: opts.mono,
            color: opts.color && !opts.mono,
        };
        let w = opts.width.max(1);
        let h = opts.height.max(1);

        // The degrade ladder, in the order things are given up: the side
        // panels, then the title, then the legend, then the border, and a
        // short window clips the well from the bottom.
        let border = w >= BORDER_W;
        let panels = w >= PANEL_W && h >= PANEL_H;
        let title = w >= TITLE_W && h >= 23;
        let legend = w >= TITLE_W && h >= REF_HEIGHT;

        let mut out = String::new();
        if title {
            let _ = writeln!(out, "{}", pad_to(&self.title_line(), w));
        }

        let border_rows = if border { 2 } else { 0 };
        let legend_rows = if legend { 1 } else { 0 };
        let used = out.lines().count() + legend_rows + border_rows;
        let rows = ROWS.min(h.saturating_sub(used));

        let ghost = self.ghost_cells();
        let cur = self.cur_cells();

        if border {
            let _ = writeln!(
                out,
                "{}",
                pad_to(
                    &format!("{}{}{}", p.tl(), p.h().to_string().repeat(COLS * 2), p.tr()),
                    w
                )
            );
        }
        for dr in 0..rows {
            let r = HIDDEN_ROWS + dr;
            let mut line = String::new();
            if border {
                line.push(p.v());
            }
            for c in 0..COLS {
                let here = cur.iter().any(|(pr, pc)| *pr == r as i32 && *pc == c as i32);
                if self.state == State::Play && here {
                    line.push_str(p.cell(piece_colour(self.kind)));
                } else if self.board[r][c] {
                    line.push_str(p.cell(self.colour[r][c] as usize));
                } else if ghost.iter().any(|(gr, gc)| *gr == r as i32 && *gc == c as i32) {
                    line.push_str(p.ghost());
                } else {
                    line.push_str(p.empty());
                }
            }
            if border {
                line.push(p.v());
            }
            if panels {
                let _ = write!(line, "{}", self.panel_column(dr, opts.mono));
            }
            let _ = writeln!(out, "{}", pad_to(&line, w));
        }
        if border {
            let _ = writeln!(
                out,
                "{}",
                pad_to(
                    &format!(
                        "{}{}{}",
                        p.bl(),
                        p.h().to_string().repeat(COLS * 2),
                        p.br()
                    ),
                    w
                )
            );
        }
        if legend {
            let _ = writeln!(out, "{}", pad_to(LEGEND, w));
        }
        if self.state != State::Play {
            let msg = match self.state {
                State::Over => format!(
                    " GAME OVER — {}  score {}  lines {}  level {} ",
                    self.cause.map(|c| c.name()).unwrap_or("over"),
                    self.score,
                    self.lines,
                    self.level
                ),
                State::Paused => " PAUSED — p resumes ".to_string(),
                State::Play => String::new(),
            };
            let _ = writeln!(out, "{}", pad_to(&msg, w));
        }
        out
    }

    fn title_line(&self) -> String {
        format!(
            "GLACIER BLOCKS  score {}  level {}  lines {}  best {}",
            self.score, self.level, self.lines, self.best_score
        )
    }

    /// The side panel content for field row `dr`: the hold slot, the next-3
    /// preview and the score panel. Plain text, so `--mono` needs no case.
    fn panel_column(&self, dr: usize, mono: bool) -> String {
        let mut s = String::from("   ");
        if dr == 0 {
            s += "HOLD   ";
        } else if (1..=3).contains(&dr) {
            s += &mini_row(self.held.map(|k| (k, 0)), dr - 1, mono);
        } else if dr == 5 {
            s += "NEXT   ";
        } else if (6..=14).contains(&dr) {
            let i = (dr - 6) / 3;
            let sub = (dr - 6) % 3;
            s += &mini_row(self.preview().get(i).copied().map(|k| (k, 0)), sub, mono);
        } else if dr == 16 {
            let _ = write!(s, "{:<12}", format!("score {}", self.score));
        } else if dr == 17 {
            let _ = write!(s, "{:<12}", format!("level {}", self.level));
        } else if dr == 18 {
            let _ = write!(s, "{:<12}", format!("lines {}", self.lines));
        } else if dr == 19 {
            let _ = write!(s, "{:<12}", format!("best {}", self.best_score));
        }
        s
    }

    /// The colour each locked cell was locked with, so the well stays readable.
    fn ghost_cells(&self) -> [Cell; 4] {
        if self.state != State::Play {
            return [(-1, -1); 4];
        }
        let land = landing_row(&self.board, self.kind, self.rot, self.row, self.col);
        cells(self.kind, self.rot, land, self.col)
    }
}

/// One row of a piece drawn in a 4x3 mini box, for the hold and next panels.
/// Two characters per cell, like the well: `██`, or `[]` under `--mono`.
fn mini_row(piece: Option<(usize, usize)>, row: usize, mono: bool) -> String {
    let block = if mono { "[]" } else { "██" };
    let mut s = String::new();
    for c in 0..4 {
        let on = piece
            .map(|(k, r)| {
                shape(k, r)
                    .iter()
                    .any(|(dr, dc)| *dr == row as i32 && *dc == c as i32)
            })
            .unwrap_or(false);
        s.push_str(if on { block } else { "  " });
    }
    s
}
