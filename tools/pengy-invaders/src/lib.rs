//! pengy-invaders — Thaw Invaders: the 1978 arcade original for the terminal.
//!
//! **Every rule and the renderer live in this file, and they are pure.** No
//! I/O, no clock, no threads, no files, no environment, no randomness beyond
//! the seeded generator below. A run is a function of `(seed, script)`: the
//! front-end in `src/main.rs` only ever calls [`Game::key`], [`Game::tick`],
//! [`Game::render`] and the persistence helpers, which is what makes the
//! `--replay` harness, the unit tests and the repo gate all work without a
//! terminal.
//!
//! The game: something is melting the ice from above. You have one laser.
//! A formation of 11x5 aliens sweeps left and right over a 60x24 field,
//! descending one row every time it touches a wall and stepping faster as the
//! formation thins. Aliens drop bombs; three destructible shields stand
//! between the formation and you; you have three lives and one laser in
//! flight. Clear a wave and the next one starts one row lower and faster.
//!
//! See `spec.md` for the contract this file implements.

use std::fmt::Write as _;

// ---------------------------------------------------------------------------
// Tuning constants — the numbers the spec quotes. Changing one is a design
// change; `tests/rules.rs` asserts the important ones.
// ---------------------------------------------------------------------------

/// Fixed timestep: 40 ms per tick (25 ticks per second).
pub const TICK_MS: u64 = 40;

/// Field width in columns.
pub const COLS: usize = 60;
/// Field height in rows.
pub const ROWS: usize = 24;

/// The row the ship sits on. Aliens reaching it end the game.
pub const PLAYER_ROW: usize = 23;
/// The ship is 3 columns wide, so this is its half-width.
pub const SHIP_HALF: usize = 1;
/// The ship's leftmost centre column.
pub const MIN_X: usize = 1;
/// The ship's rightmost centre column.
pub const MAX_X: usize = COLS - 2;
/// One keypress moves the ship this many columns.
pub const MOVE_STEP: usize = 2;

/// The laser travels this many rows per tick, upward.
pub const LASER_SPEED: usize = 2;
/// Ticks of cooldown after a shot: one laser in flight, then a beat.
pub const FIRE_COOLDOWN: u64 = 2;

/// Formation columns (the classic 11).
pub const FORM_COLS: usize = 11;
/// Formation rows (the classic 5).
pub const FORM_ROWS: usize = 5;
/// An alien is 2 cells wide.
pub const ALIEN_W: usize = 2;
/// Column pitch: 2 cells of alien + 2 cells of gap.
pub const COL_SPACING: usize = 4;
/// Width of the whole formation in cells: 11*4 - 2.
pub const FORM_W: usize = FORM_COLS * COL_SPACING - (COL_SPACING - ALIEN_W);
/// Aliens in a full formation.
pub const ALIENS: usize = FORM_COLS * FORM_ROWS;
/// The formation's starting left column (centred).
pub const FORM_X0: usize = (COLS - FORM_W) / 2;
/// The formation's top row on wave 1; each wave starts one row lower.
pub const FORM_ROW0: usize = 2;
/// The formation reverses when its outermost cell reaches these columns.
pub const LEFT_EDGE: usize = 1;
/// The formation reverses when its outermost cell reaches these columns.
pub const RIGHT_EDGE: usize = COLS - 2;
/// Step period at a full formation: 1 column every 8 ticks.
pub const BASE_PERIOD: u64 = 8;
/// The floor of the step period.
pub const MIN_PERIOD: u64 = 1;

/// Three shields.
pub const SHIELD_COUNT: usize = 3;
/// A shield is 8 cells wide…
pub const SHIELD_W: usize = 8;
/// …and 4 cells tall.
pub const SHIELD_H: usize = 4;
/// The top row of every shield.
pub const SHIELD_TOP: usize = 18;
/// Left column of each shield.
pub const SHIELD_X: [usize; SHIELD_COUNT] = [8, 26, 44];

/// At most three bombs on screen at once.
pub const MAX_BOMBS: usize = 3;
/// A bomb falls 1 cell per tick.
pub const BOMB_SPEED: usize = 1;
/// Per-alien bomb probability, in units of 1/BOMB_RATE_SCALE: 0.002 base…
pub const BOMB_RATE_BASE: u64 = 20;
/// …plus 0.0005 per wave.
pub const BOMB_RATE_WAVE: u64 = 5;
/// The denominator of the bomb probability.
pub const BOMB_RATE_SCALE: u64 = 10_000;

/// Lives at the start of a run.
pub const START_LIVES: u32 = 3;
/// Wave-clear bonus, multiplied by the wave number.
pub const WAVE_BONUS: u64 = 100;
/// Points for the bottom row of the formation (the top row is 50).
pub const ROW_POINTS: u64 = 10;

/// Reference window width.
pub const REF_WIDTH: usize = 80;
/// Reference window height: the full chrome (title, border, legend) needs
/// `ROWS + 4` rows. The game is playable at 80x24, where the field is the
/// whole screen and the HUD rides on the field's top row.
pub const REF_HEIGHT: usize = ROWS + 4;
/// Minimum width for the title line.
pub const TITLE_W: usize = 68;
/// Minimum width for the border.
pub const BORDER_W: usize = COLS + 2;

/// The key legend, carried by the bottom border and the legend line.
pub const LEGEND: &str = "a/d move   space fire   p pause   r restart   q quit";

// ---------------------------------------------------------------------------
// RNG — hand-written seeded xorshift64*, reproducible bit for bit.
// ---------------------------------------------------------------------------

/// A seeded xorshift64* generator. No `rand`, no system entropy: every run
/// takes `--seed N`, and the generator state is part of the game state, so a
/// state hash covers it.
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

    /// Raw generator state.
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
// States, causes, directions
// ---------------------------------------------------------------------------

/// The state machine. `Ready` is the title screen: the field is drawn, the
/// formation is marching, and nothing can hurt you until you press space.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Ready,
    Play,
    Pause,
    Over,
}

impl State {
    /// Lowercase name, as printed in `key=value` output.
    pub fn name(self) -> &'static str {
        match self {
            State::Ready => "ready",
            State::Play => "play",
            State::Pause => "pause",
            State::Over => "over",
        }
    }
}

/// Why the run ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cause {
    /// The last life is gone.
    Destroyed,
    /// The formation reached the ship's row.
    Breach,
}

impl Cause {
    /// Lowercase name, as printed in `key=value` output.
    pub fn name(self) -> &'static str {
        match self {
            Cause::Destroyed => "destroyed",
            Cause::Breach => "breach",
        }
    }

    /// The text the end screen shows.
    pub fn text(self) -> &'static str {
        match self {
            Cause::Destroyed => "the ship was destroyed",
            Cause::Breach => "the formation reached the ship",
        }
    }
}

/// The direction the formation is marching.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dir {
    Right,
    Left,
}

impl Dir {
    /// Column delta of one step.
    pub fn delta(self) -> i64 {
        match self {
            Dir::Right => 1,
            Dir::Left => -1,
        }
    }

    /// The other direction.
    pub fn flip(self) -> Dir {
        match self {
            Dir::Right => Dir::Left,
            Dir::Left => Dir::Right,
        }
    }
}

// ---------------------------------------------------------------------------
// Field pieces
// ---------------------------------------------------------------------------

/// The laser in flight. `row` is a signed row so it can leave the top of the
/// field cleanly.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Laser {
    /// Column.
    pub col: usize,
    /// Row, counted from the top.
    pub row: i64,
}

/// A bomb in flight.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Bomb {
    /// Column.
    pub col: usize,
    /// Row, counted from the top.
    pub row: usize,
}

/// The row index of the lowest living alien in formation column `fc`, `None`
/// when that column is empty.
pub fn lowest_alien(aliens: &[[bool; FORM_COLS]; FORM_ROWS], fc: usize) -> Option<usize> {
    (0..FORM_ROWS).rev().find(|&ri| aliens[ri][fc])
}

/// Which formation column (if any) covers screen column `col` when the
/// formation's left cell is at `formation_x`.
pub fn formation_col_at(formation_x: usize, col: usize) -> Option<usize> {
    if col < formation_x {
        return None;
    }
    let off = col - formation_x;
    if off % COL_SPACING >= ALIEN_W {
        return None;
    }
    let fc = off / COL_SPACING;
    if fc < FORM_COLS {
        Some(fc)
    } else {
        None
    }
}

/// Screen column of a formation column's left cell.
pub fn alien_left_col(formation_x: usize, fc: usize) -> usize {
    formation_x + fc * COL_SPACING
}

/// Screen row of a formation row.
pub fn alien_row(formation_row: usize, ri: usize) -> usize {
    formation_row + ri
}

/// Points for killing the alien on formation row `ri` (bottom row 10 … top row
/// 50).
pub fn score_for_row(ri: usize) -> u64 {
    (FORM_ROWS as u64 - ri as u64) * ROW_POINTS
}

/// Step period for `alive` aliens on `wave`: `8 * alive / 55`, floored at 1.
/// The wave divides it, which is what makes each wave faster.
pub fn period_for(alive: usize, wave: u32) -> u64 {
    let n = (BASE_PERIOD * alive as u64) / (ALIENS as u64 * wave as u64);
    if n < MIN_PERIOD {
        MIN_PERIOD
    } else {
        n
    }
}

/// Per-alien bomb probability for `wave`, in units of [`BOMB_RATE_SCALE`].
pub fn bomb_rate_for(wave: u32) -> u64 {
    BOMB_RATE_BASE + BOMB_RATE_WAVE * (wave as u64 - 1)
}

/// Which shield (if any) covers `(row, col)`, as `(shield index, row, column)`
/// inside that shield.
pub fn shield_cell_at(row: usize, col: usize) -> Option<(usize, usize, usize)> {
    if row < SHIELD_TOP || row >= SHIELD_TOP + SHIELD_H {
        return None;
    }
    for s in 0..SHIELD_COUNT {
        let x = SHIELD_X[s];
        if col >= x && col < x + SHIELD_W {
            return Some((s, row - SHIELD_TOP, col - x));
        }
    }
    None
}

/// The ship covers these columns.
pub fn ship_covers(x: usize) -> (usize, usize) {
    (x.saturating_sub(SHIP_HALF), x + SHIP_HALF)
}

// ---------------------------------------------------------------------------
// The game
// ---------------------------------------------------------------------------

/// One run of Thaw Invaders. Every field here is game state: the hash covers
/// all of it, and `render` is a pure function of it.
#[derive(Clone, Debug, PartialEq)]
pub struct Game {
    /// The seed this run was built from.
    pub seed: u64,
    /// The seeded generator (bomb drops only).
    pub rng: Rng,
    /// The state machine.
    pub state: State,
    /// Why the run ended, once it has.
    pub cause: Option<Cause>,
    /// Ticks elapsed in this run.
    pub ticks: u64,
    /// The wave being played, 1-based.
    pub wave: u32,
    /// Score.
    pub score: u64,
    /// Lives left.
    pub lives: u32,
    /// Centre column of the ship.
    pub player_x: usize,
    /// Ticks left before the laser can fire again.
    pub cooldown: u64,
    /// The laser in flight, if any.
    pub laser: Option<Laser>,
    /// Left cell of the formation.
    pub formation_x: usize,
    /// Top row of the formation.
    pub formation_row: usize,
    /// March direction.
    pub dir: Dir,
    /// Ticks until the formation steps one column.
    pub step_countdown: u64,
    /// `aliens[row][col]` — true while that alien is alive.
    pub aliens: [[bool; FORM_COLS]; FORM_ROWS],
    /// Bombs in flight.
    pub bombs: Vec<Bomb>,
    /// `shields[s][row][col]` — true while that cell is intact.
    pub shields: [[[bool; SHIELD_W]; SHIELD_H]; SHIELD_COUNT],
    /// Aliens killed.
    pub kills: u64,
    /// Shots fired.
    pub shots: u64,
    /// Bombs shot down.
    pub bombs_shot: u64,
    /// Times the ship was hit.
    pub hits_taken: u64,
    /// Waves fully cleared.
    pub waves_cleared: u32,
    /// Reversals of the formation.
    pub reversals: u64,
    /// Descents of the formation.
    pub descents: u64,
    /// Highest score banked (persisted best).
    pub best_score: u64,
    /// Highest wave banked (persisted best).
    pub best_wave: u32,
}

/// A fresh run: wave 1, ship centred, 3 lives, title screen.
impl Default for Game {
    fn default() -> Self {
        Self::new(1)
    }
}

impl Game {
    /// A fresh run on `seed`, starting on the title screen.
    pub fn new(seed: u64) -> Self {
        let mut g = Game {
            seed,
            rng: Rng::new(seed),
            state: State::Ready,
            cause: None,
            ticks: 0,
            wave: 1,
            score: 0,
            lives: START_LIVES,
            player_x: COLS / 2,
            cooldown: 0,
            laser: None,
            formation_x: FORM_X0,
            formation_row: FORM_ROW0,
            dir: Dir::Right,
            step_countdown: period_for(ALIENS, 1),
            aliens: [[true; FORM_COLS]; FORM_ROWS],
            bombs: Vec::new(),
            shields: [[[true; SHIELD_W]; SHIELD_H]; SHIELD_COUNT],
            kills: 0,
            shots: 0,
            bombs_shot: 0,
            hits_taken: 0,
            waves_cleared: 0,
            reversals: 0,
            descents: 0,
            best_score: 0,
            best_wave: 0,
        };
        g.rng = Rng::new(seed);
        g
    }

    /// Restart: the same rules, the next seed in the seed family, best kept.
    pub fn restart(&mut self) {
        let seed = self.seed.wrapping_add(1);
        let (best_score, best_wave) = (self.best_score, self.best_wave);
        *self = Game::new(seed);
        self.best_score = best_score;
        self.best_wave = best_wave;
        self.state = State::Play;
    }

    /// Aliens alive.
    pub fn alive(&self) -> usize {
        self.aliens.iter().flatten().filter(|a| **a).count()
    }

    /// Step period right now.
    pub fn step_period(&self) -> u64 {
        period_for(self.alive(), self.wave)
    }

    /// Bomb probability right now.
    pub fn bomb_rate(&self) -> u64 {
        bomb_rate_for(self.wave)
    }

    /// Intact shield cells.
    pub fn shield_cells(&self) -> usize {
        self.shields
            .iter()
            .flatten()
            .flatten()
            .filter(|c| **c)
            .count()
    }

    /// The bottom row of the formation.
    pub fn formation_bottom(&self) -> usize {
        self.formation_row + FORM_ROWS - 1
    }

    /// True when the formation has reached the ship's row.
    pub fn breached(&self) -> bool {
        self.formation_bottom() >= PLAYER_ROW
    }

    /// The lowest living alien's screen row, `None` when the wave is clear.
    pub fn lowest_alien_row(&self) -> Option<usize> {
        (0..FORM_ROWS).rev().find(|&ri| self.aliens[ri].iter().any(|a| *a)).map(|ri| alien_row(self.formation_row, ri))
    }

    /// Start the run from the title screen.
    pub fn start(&mut self) {
        if self.state == State::Ready {
            self.state = State::Play;
        }
    }

    /// End the run with `cause`.
    fn over(&mut self, cause: Cause) {
        self.state = State::Over;
        self.cause = Some(cause);
        self.laser = None;
        self.bombs.clear();
    }

    /// Fire the laser. Ignored while a laser is in flight or the cooldown has
    /// not expired — that is the "one laser in flight, 2-tick cooldown" rule.
    pub fn fire(&mut self) {
        if self.state != State::Play {
            return;
        }
        if self.laser.is_none() && self.cooldown == 0 {
            self.laser = Some(Laser {
                col: self.player_x,
                row: PLAYER_ROW as i64,
            });
            self.cooldown = FIRE_COOLDOWN;
            self.shots += 1;
        }
    }

    /// Move the ship by `delta` columns, clamped to the field.
    pub fn move_ship(&mut self, delta: i32) {
        let x = self.player_x as i64 + delta as i64;
        self.player_x = x.clamp(MIN_X as i64, MAX_X as i64) as usize;
    }

    /// Hand one keypress to the game. Returns true when the key quits.
    ///
    /// `q` quits from any state. `p` pauses/resumes in play. `r` restarts.
    /// Space starts the run from the title screen and fires in play. `a`/`d`
    /// (and the front-end's arrow-key translations `h`/`l`) move the ship.
    /// Anything else is ignored.
    pub fn key(&mut self, c: char) -> bool {
        match c {
            'q' | 'Q' => return true,
            'p' | 'P' => {
                match self.state {
                    State::Play => self.state = State::Pause,
                    State::Pause => self.state = State::Play,
                    _ => {}
                }
                return false;
            }
            'r' | 'R' => {
                self.restart();
                return false;
            }
            _ => {}
        }
        match self.state {
            State::Ready => {
                if c == ' ' {
                    self.state = State::Play;
                }
            }
            State::Play => match c {
                'a' | 'A' | 'h' => self.move_ship(-(MOVE_STEP as i32)),
                'd' | 'D' | 'l' => self.move_ship(MOVE_STEP as i32),
                ' ' => self.fire(),
                _ => {}
            },
            State::Pause | State::Over => {}
        }
        false
    }

    /// Advance the run by one tick. Order is fixed and is part of the
    /// contract: cooldown, laser, formation, bombs, wave.
    pub fn tick(&mut self) {
        if self.state != State::Play {
            return;
        }
        self.ticks += 1;
        if self.cooldown > 0 {
            self.cooldown -= 1;
        }
        self.step_laser();
        self.step_formation();
        self.step_bombs();
        self.check_wave();
    }

    /// The laser moves up [`LASER_SPEED`] rows, checking every cell it
    /// crosses: an alien (the lowest in that column dies), a bomb (it is
    /// destroyed), a shield cell (it erodes). The laser is consumed by the
    /// first thing it touches, and by leaving the top of the field.
    fn step_laser(&mut self) {
        let Some(l) = self.laser else { return };
        let mut row = l.row;
        for _ in 0..LASER_SPEED {
            row -= 1;
            if row < 0 {
                self.laser = None;
                return;
            }
            let r = row as usize;
            if r >= ROWS {
                continue;
            }
            // Only the cell the laser is crossing: the lowest alien in the
            // column dies when the laser reaches *its* row, not from the
            // ship's row. Shields and bombs below it are met first.
            if let Some(fc) = formation_col_at(self.formation_x, l.col) {
                if let Some(ri) = lowest_alien(&self.aliens, fc) {
                    if alien_row(self.formation_row, ri) == r {
                        self.kill_alien(fc, ri);
                        self.laser = None;
                        return;
                    }
                }
            }
            if let Some(i) = self.bombs.iter().position(|b| b.col == l.col && b.row == r) {
                self.bombs.remove(i);
                self.bombs_shot += 1;
                self.laser = None;
                return;
            }
            if let Some((s, sr, sc)) = shield_cell_at(r, l.col) {
                // An intact cell takes the shot and erodes; a hole lets the
                // laser through.
                if self.shields[s][sr][sc] {
                    self.shields[s][sr][sc] = false;
                    self.laser = None;
                    return;
                }
            }
        }
        self.laser = Some(Laser { col: l.col, row });
    }

    /// Kill the alien at formation cell `(ri, fc)` and score it.
    fn kill_alien(&mut self, fc: usize, ri: usize) {
        self.aliens[ri][fc] = false;
        self.score += score_for_row(ri);
        self.kills += 1;
    }

    /// The formation steps one column every [`Game::step_period`] ticks; on
    /// touching either wall it reverses *and* descends one row. A formation
    /// that reaches the ship's row ends the run.
    fn step_formation(&mut self) {
        self.step_countdown = self.step_countdown.saturating_sub(1);
        if self.step_countdown > 0 {
            return;
        }
        self.step_countdown = self.step_period();
        let next = self.formation_x as i64 + self.dir.delta();
        let right = next as usize + FORM_W - 1;
        if self.dir == Dir::Right && right >= RIGHT_EDGE {
            self.formation_x = RIGHT_EDGE - (FORM_W - 1);
            self.dir = Dir::Left;
            self.reversals += 1;
            self.descend();
        } else if self.dir == Dir::Left && next <= LEFT_EDGE as i64 - 1 {
            self.formation_x = LEFT_EDGE;
            self.dir = Dir::Right;
            self.reversals += 1;
            self.descend();
        } else {
            self.formation_x = next as usize;
        }
        self.alien_shield_contact();
        if self.breached() {
            self.over(Cause::Breach);
        }
    }

    /// Drop the formation one row (it has just touched a wall).
    fn descend(&mut self) {
        self.formation_row += 1;
        self.descents += 1;
    }

    /// An alien that touches a shield erodes that cell.
    fn alien_shield_contact(&mut self) {
        for ri in 0..FORM_ROWS {
            for fc in 0..FORM_COLS {
                if !self.aliens[ri][fc] {
                    continue;
                }
                let row = alien_row(self.formation_row, ri);
                let left = alien_left_col(self.formation_x, fc);
                for col in left..left + ALIEN_W {
                    if let Some((s, sr, sc)) = shield_cell_at(row, col) {
                        self.shields[s][sr][sc] = false;
                    }
                }
            }
        }
    }

    /// Bombs: each living alien rolls once per tick (row-major order, so the
    /// roll order is fixed), at most [`MAX_BOMBS`] on screen. Then every bomb
    /// falls [`BOMB_SPEED`] rows and collides: the ship (a life is lost), a
    /// shield cell (it erodes), or off the bottom of the field (it is gone).
    fn step_bombs(&mut self) {
        let rate = self.bomb_rate();
        'spawn: for ri in 0..FORM_ROWS {
            for fc in 0..FORM_COLS {
                if !self.aliens[ri][fc] {
                    continue;
                }
                if self.bombs.len() >= MAX_BOMBS {
                    break 'spawn;
                }
                if self.rng.below(BOMB_RATE_SCALE) < rate {
                    self.bombs.push(Bomb {
                        col: alien_left_col(self.formation_x, fc) + 1,
                        row: alien_row(self.formation_row, ri) + 1,
                    });
                }
            }
        }
        let mut i = 0;
        while i < self.bombs.len() {
            self.bombs[i].row += BOMB_SPEED;
            let b = self.bombs[i];
            if b.row >= ROWS {
                self.bombs.remove(i);
                continue;
            }
            let (lo, hi) = ship_covers(self.player_x);
            if b.row == PLAYER_ROW && b.col >= lo && b.col <= hi {
                self.bombs.remove(i);
                self.lose_life();
                continue;
            }
            if let Some((s, sr, sc)) = shield_cell_at(b.row, b.col) {
                if self.shields[s][sr][sc] {
                    self.shields[s][sr][sc] = false;
                    self.bombs.remove(i);
                    continue;
                }
            }
            i += 1;
        }
    }

    /// The ship was hit: a life, the bombs, the laser and the position go.
    fn lose_life(&mut self) {
        self.lives = self.lives.saturating_sub(1);
        self.hits_taken += 1;
        self.bombs.clear();
        self.laser = None;
        self.cooldown = 0;
        self.player_x = COLS / 2;
        if self.lives == 0 {
            self.over(Cause::Destroyed);
        }
    }

    /// A cleared wave scores its bonus and starts the next one, one row lower
    /// and faster.
    fn check_wave(&mut self) {
        if self.state != State::Play || self.alive() != 0 {
            return;
        }
        self.score += WAVE_BONUS * self.wave as u64;
        self.waves_cleared += 1;
        self.wave += 1;
        self.spawn_wave();
    }

    /// Refill the formation one row lower, centred, marching right, and clear
    /// the field of bombs and the laser. Public so `--wave N` can start a run
    /// mid-game the same way a cleared wave does.
    pub fn spawn_wave(&mut self) {
        self.aliens = [[true; FORM_COLS]; FORM_ROWS];
        self.formation_x = FORM_X0;
        self.formation_row = FORM_ROW0 + (self.wave as usize - 1);
        self.dir = Dir::Right;
        self.step_countdown = self.step_period();
        self.bombs.clear();
        self.laser = None;
        self.cooldown = 0;
        if self.breached() {
            self.over(Cause::Breach);
        }
    }

    /// Where the formation's left cell will be after `ticks` more ticks: the
    /// same countdown, step and wall-reversal rules as [`Game::tick`], run
    /// forward without touching the state. The autopilot leads its shots with
    /// it, because a laser takes time to climb.
    pub fn formation_x_after(&self, ticks: usize) -> usize {
        let period = self.step_period();
        let (mut x, mut dir, mut countdown) = (self.formation_x, self.dir, self.step_countdown);
        for _ in 0..ticks {
            countdown = countdown.saturating_sub(1);
            if countdown > 0 {
                continue;
            }
            countdown = period;
            let next = x as i64 + dir.delta();
            if dir == Dir::Right && next as usize + FORM_W - 1 >= RIGHT_EDGE {
                x = RIGHT_EDGE - (FORM_W - 1);
                dir = Dir::Left;
            } else if dir == Dir::Left && next <= LEFT_EDGE as i64 - 1 {
                x = LEFT_EDGE;
                dir = Dir::Right;
            } else {
                x = next as usize;
            }
        }
        x
    }

    /// Would a laser fired from column `col` this tick meet a living alien?
    /// The laser crosses row `r` on tick `ceil((PLAYER_ROW - r) / 2)`, before
    /// that tick's formation step, so the alien is where the formation will be
    /// one tick earlier.
    fn shot_lands(&self, col: usize) -> bool {
        (0..FORM_COLS).any(|fc| {
            let Some(ri) = lowest_alien(&self.aliens, fc) else {
                return false;
            };
            let rise = PLAYER_ROW - alien_row(self.formation_row, ri);
            let k = (rise + LASER_SPEED - 1) / LASER_SPEED;
            let left = alien_left_col(self.formation_x_after(k.saturating_sub(1)), fc);
            col >= left && col < left + ALIEN_W
        })
    }

    /// A bomb that will reach the ship's row within a few ticks inside these
    /// ship columns.
    fn bomb_threat(&self, x: usize) -> bool {
        let (lo, hi) = ship_covers(x);
        self.bombs
            .iter()
            .any(|b| b.row + 4 >= PLAYER_ROW && b.col + 1 >= lo && b.col <= hi + 1)
    }

    /// The autopilot's keys for this tick: get out from under a falling bomb,
    /// otherwise line up under the lowest living alien nearest the ship,
    /// leading it by the laser's flight time, and fire when the shot will
    /// land. Used by `--headless` and `--autopilot`, and by the tests.
    /// Deterministic: a function of state.
    pub fn autopilot_keys(&self) -> String {
        if self.state != State::Play {
            return String::new();
        }
        let x = self.player_x;
        let left = x.saturating_sub(MOVE_STEP).max(MIN_X);
        let right = (x + MOVE_STEP).min(MAX_X);
        let mut keys = String::new();
        let mut nx = x;
        if self.bomb_threat(x) {
            // Dodge first: a life is worth more than a shot.
            if !self.bomb_threat(left) && left != x {
                keys.push('a');
                nx = left;
            } else if !self.bomb_threat(right) && right != x {
                keys.push('d');
                nx = right;
            }
        } else {
            // The nearest lowest alien, by column distance from the ship, at
            // the column it will be on when a shot gets there.
            let mut best: Option<(usize, usize)> = None;
            for ri in (0..FORM_ROWS).rev() {
                let rise = PLAYER_ROW - alien_row(self.formation_row, ri);
                let ahead = self.formation_x_after(((rise + LASER_SPEED - 1) / LASER_SPEED).saturating_sub(1));
                for fc in 0..FORM_COLS {
                    if !self.aliens[ri][fc] {
                        continue;
                    }
                    let col = alien_left_col(ahead, fc);
                    let d = (col as i64 - x as i64).unsigned_abs() as usize;
                    if best.map(|(bd, _)| d < bd).unwrap_or(true) {
                        best = Some((d, col));
                    }
                }
                if best.is_some() {
                    break;
                }
            }
            if let Some((_, col)) = best {
                // Two columns per keypress; never step under a bomb.
                if col + 1 < x && !self.bomb_threat(left) {
                    keys.push('a');
                    nx = left;
                } else if col > x + 1 && !self.bomb_threat(right) {
                    keys.push('d');
                    nx = right;
                }
            }
        }
        if self.laser.is_none() && self.cooldown == 0 && self.shot_lands(nx) {
            keys.push(' ');
        }
        keys
    }

    /// A hash of the whole state: 16 hex digits. Same seed + same script =
    /// the same hash, which is what the determinism tests assert.
    pub fn state_hash(&self) -> u64 {
        let mut h: u64 = 0xcbf2_9ce4_8422_2325;
        let mut mix = |v: u64| {
            h ^= v;
            h = h.wrapping_mul(0x1000_0000_01b3);
        };
        mix(self.seed);
        mix(self.rng.state());
        mix(self.state as u64);
        mix(self.cause.map(|c| c as u64 + 1).unwrap_or(0));
        mix(self.ticks);
        mix(self.wave as u64);
        mix(self.score);
        mix(self.lives as u64);
        mix(self.player_x as u64);
        mix(self.cooldown);
        mix(self.formation_x as u64);
        mix(self.formation_row as u64);
        mix(self.dir as u64);
        mix(self.step_countdown);
        mix(self.kills);
        mix(self.shots);
        mix(self.bombs_shot);
        mix(self.hits_taken);
        mix(self.waves_cleared as u64);
        mix(self.reversals);
        mix(self.descents);
        mix(self.bombs.len() as u64);
        for b in &self.bombs {
            mix((b.row as u64) << 8 | b.col as u64);
        }
        if let Some(l) = self.laser {
            mix((l.row as u64) << 8 | l.col as u64);
        }
        for ri in 0..FORM_ROWS {
            for fc in 0..FORM_COLS {
                mix(self.aliens[ri][fc] as u64);
            }
        }
        for s in 0..SHIELD_COUNT {
            for r in 0..SHIELD_H {
                for c in 0..SHIELD_W {
                    mix(self.shields[s][r][c] as u64);
                }
            }
        }
        h
    }

    /// The `key=value` lines `--headless` and `--replay` print. The shape the
    /// gate greps.
    pub fn result_kv(&self) -> Vec<(String, String)> {
        let mut kv = vec![
            ("state".into(), self.state.name().to_string()),
            ("seed".into(), self.seed.to_string()),
            ("ticks".into(), self.ticks.to_string()),
            ("wave".into(), self.wave.to_string()),
            ("score".into(), self.score.to_string()),
            ("lives".into(), self.lives.to_string()),
            ("aliens".into(), self.alive().to_string()),
            ("kills".into(), self.kills.to_string()),
            ("shots".into(), self.shots.to_string()),
            ("bombs".into(), self.bombs.len().to_string()),
            ("bombs_shot".into(), self.bombs_shot.to_string()),
            ("shield_cells".into(), self.shield_cells().to_string()),
            ("hits_taken".into(), self.hits_taken.to_string()),
            ("waves_cleared".into(), self.waves_cleared.to_string()),
            ("reversals".into(), self.reversals.to_string()),
            ("descents".into(), self.descents.to_string()),
            ("player_x".into(), self.player_x.to_string()),
            ("formation_x".into(), self.formation_x.to_string()),
            ("formation_row".into(), self.formation_row.to_string()),
            ("period".into(), self.step_period().to_string()),
            ("best_score".into(), self.best_score.to_string()),
            ("best_wave".into(), self.best_wave.to_string()),
            ("hash".into(), format!("{:016x}", self.state_hash())),
        ];
        if let Some(c) = self.cause {
            kv.push(("cause".into(), c.name().to_string()));
        }
        kv
    }
}

// ---------------------------------------------------------------------------
// Plain text: the headless state dump
// ---------------------------------------------------------------------------

/// The field as plain ASCII rows (no escapes), the shape `--headless` prints.
pub fn field_text(g: &Game) -> String {
    let p = Painter {
        mono: true,
        color: false,
    };
    let mut s = String::new();
    for r in 0..ROWS {
        let _ = writeln!(s, "|{}|", field_row(g, &p, r, r == 0));
    }
    s
}

/// The plain-text state `--headless` prints: `key=value` lines, then the
/// field. Diffable, no escapes.
pub fn plain_state(g: &Game) -> String {
    let mut s = String::new();
    for (k, v) in g.result_kv() {
        let _ = writeln!(s, "{k}={v}");
    }
    let _ = writeln!(s, "field:");
    s.push_str(&field_text(g));
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
    /// Emit ANSI colour. Off under `NO_COLOR`, `--mono` and headless.
    pub color: bool,
}

impl Default for RenderOpts {
    /// The 80x28 reference window, colour on.
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
    fn corner(&self) -> (char, char, char, char) {
        if self.mono {
            ('+', '+', '+', '+')
        } else {
            ('┌', '┐', '└', '┘')
        }
    }
    fn hline(&self) -> char {
        if self.mono {
            '-'
        } else {
            '─'
        }
    }
    fn vline(&self) -> char {
        if self.mono {
            '|'
        } else {
            '│'
        }
    }
    /// Alien glyphs, one per formation row (bottom row first, top row last).
    fn alien(&self, ri: usize) -> char {
        if self.mono {
            ['E', 'D', 'C', 'B', 'A'][ri]
        } else {
            ['▬', '●', '■', '◆', '▲'][ri]
        }
    }
    /// Alien colour per formation row: magenta at the top down to cyan at the
    /// bottom.
    fn alien_sgr(&self, ri: usize) -> &'static str {
        match ri {
            0 => "\x1b[36m",
            1 => "\x1b[32m",
            2 => "\x1b[33m",
            3 => "\x1b[35m",
            _ => "\x1b[31m",
        }
    }
    fn ship(&self) -> [char; 3] {
        if self.mono {
            ['-', '+', '-']
        } else {
            ['─', '▲', '─']
        }
    }
    fn laser(&self) -> char {
        if self.mono {
            '|'
        } else {
            '│'
        }
    }
    fn bomb(&self) -> char {
        if self.mono {
            'v'
        } else {
            '▼'
        }
    }
    fn shield(&self) -> char {
        if self.mono {
            '#'
        } else {
            '▓'
        }
    }
}

/// One field row: aliens, shields, bombs, the laser, the ship, and (when the
/// HUD has no line of its own) the HUD on row 0. Exactly `COLS` columns.
fn field_row(g: &Game, p: &Painter, row: usize, hud: bool) -> String {
    let mut cells: Vec<char> = vec![' '; COLS];
    // Shields first: everything else draws over them.
    for s in 0..SHIELD_COUNT {
        for sr in 0..SHIELD_H {
            let r = SHIELD_TOP + sr;
            if r != row {
                continue;
            }
            for sc in 0..SHIELD_W {
                if g.shields[s][sr][sc] {
                    cells[SHIELD_X[s] + sc] = p.shield();
                }
            }
        }
    }
    // Aliens.
    for ri in 0..FORM_ROWS {
        if alien_row(g.formation_row, ri) != row {
            continue;
        }
        for fc in 0..FORM_COLS {
            if !g.aliens[ri][fc] {
                continue;
            }
            let left = alien_left_col(g.formation_x, fc);
            for c in left..left + ALIEN_W {
                if c < COLS {
                    cells[c] = p.alien(ri);
                }
            }
        }
    }
    // Bombs.
    for b in &g.bombs {
        if b.row == row && b.col < COLS {
            cells[b.col] = p.bomb();
        }
    }
    // The laser.
    if let Some(l) = g.laser {
        if l.row >= 0 && (l.row as usize) == row && l.col < COLS {
            cells[l.col] = p.laser();
        }
    }
    // The ship.
    if row == PLAYER_ROW {
        let sh = p.ship();
        for (i, c) in (0..3).map(|i| (i, g.player_x + i - 1)).collect::<Vec<(usize, usize)>>() {
            if c < COLS {
                cells[c] = sh[i];
            }
        }
    }
    // The HUD rides on the field's top row when there is no title line.
    if hud {
        let text = hud_line(g, false, p.mono);
        for (i, ch) in text.chars().enumerate() {
            if 1 + i < COLS {
                cells[1 + i] = ch;
            }
        }
    }
    // Colour: one SGR run per row group, so the frame stays small.
    if !p.color {
        return cells.into_iter().collect();
    }
    let mut out = String::with_capacity(COLS * 4);
    let mut cur = "";
    out.push_str("\x1b[0m");
    for c in cells {
        let sgr = colour_for(g, p, row, c);
        if sgr != cur {
            out.push_str(sgr);
            cur = sgr;
        }
        out.push(c);
    }
    out.push_str("\x1b[0m");
    out
}

/// The colour of one drawn cell.
fn colour_for(g: &Game, p: &Painter, row: usize, ch: char) -> &'static str {
    if ch == p.shield() {
        return "\x1b[36m";
    }
    if ch == p.bomb() {
        return "\x1b[91m";
    }
    if ch == p.laser() {
        return "\x1b[93m";
    }
    if row == PLAYER_ROW {
        return "\x1b[92m";
    }
    for ri in 0..FORM_ROWS {
        if ch == p.alien(ri) && alien_row(g.formation_row, ri) == row {
            return p.alien_sgr(ri);
        }
    }
    "\x1b[0m"
}

/// The score/wave/lives line. `title` adds the game name and the best score;
/// `mono` keeps it pure ASCII (the plain-text dump and `--mono`).
fn hud_line(g: &Game, title: bool, mono: bool) -> String {
    let heart = if mono { '#' } else { '♥' };
    let lives = (0..g.lives).map(|_| heart).collect::<String>();
    let lives = if g.lives == 0 {
        String::from("-")
    } else {
        lives
    };
    if title {
        format!(
            "THAW INVADERS  score {:>6}  wave {:>2}  lives {}  seed {}  best {} w{}",
            g.score, g.wave, lives, g.seed, g.best_score, g.best_wave
        )
    } else {
        format!(
            "score {:>6}  wave {:>2}  lives {}  N {:>2}  bombs {}  seed {}",
            g.score,
            g.wave,
            lives,
            g.step_period(),
            g.bombs.len(),
            g.seed
        )
    }
}

impl Game {
    /// The title line.
    fn title_line(&self, p: &Painter) -> String {
        let text = hud_line(self, true, p.mono);
        if p.color {
            format!("\x1b[1;36m{text}\x1b[0m")
        } else {
            text
        }
    }

    /// The legend / status line: what the player can do right now.
    fn status_line(&self, p: &Painter) -> String {
        let text = match self.state {
            State::Ready => format!(
                "THAW INVADERS   space starts   {}   q quits",
                LEGEND
            ),
            State::Play => String::from(LEGEND),
            State::Pause => String::from("PAUSED   p resumes   r restarts   q quits"),
            State::Over => format!(
                "GAME OVER   score {}   wave {}   {}   r restarts   q quits",
                self.score,
                self.wave,
                self.cause.map(Cause::text).unwrap_or("the ice held")
            ),
        };
        if p.color {
            format!("\x1b[1;33m{text}\x1b[0m")
        } else {
            text
        }
    }

    fn top_border(&self, p: &Painter) -> String {
        let c = p.corner();
        let h = p.hline().to_string().repeat(COLS);
        let line = format!("{}{h}{}", c.0, c.1);
        if p.color {
            format!("\x1b[90m{line}\x1b[0m")
        } else {
            line
        }
    }

    /// The bottom border carries the key legend, so the keys are always on
    /// screen without spending a row on them.
    fn bottom_border(&self, p: &Painter) -> String {
        let c = p.corner();
        let room = COLS.saturating_sub(2);
        let legend: String = LEGEND.chars().take(room).collect();
        let pad = room - legend.chars().count();
        let left = pad / 2;
        let line = format!(
            "{}{}{}{}{}",
            c.2,
            p.hline().to_string().repeat(left),
            legend,
            p.hline().to_string().repeat(pad - left),
            c.3
        );
        if p.color {
            format!("\x1b[90m{line}\x1b[0m")
        } else {
            line
        }
    }

    /// Paint the whole screen. The front-end writes this with one `home` plus
    /// one write, which repaints with no leftovers and no flicker.
    ///
    /// Exactly `height` rows, every row exactly `width` visible columns.
    /// Degrade rules (spec.md "Rendering"): the full chrome (title, border,
    /// legend) needs [`REF_HEIGHT`] rows and [`BORDER_W`] columns; the legend
    /// line needs `ROWS + 1` rows; below that the field is the whole screen
    /// and the HUD rides on the field's top row, which is always empty
    /// because the formation starts on row [`FORM_ROW0`]. The 60-column field
    /// is never dropped; a window shorter than [`ROWS`] clips the field from
    /// the bottom, and a narrower one clips rows from the right.
    pub fn render(&self, opts: RenderOpts) -> String {
        let p = Painter {
            mono: opts.mono,
            color: opts.color && !opts.mono,
        };
        let title = opts.width >= TITLE_W && opts.height >= REF_HEIGHT;
        let border = opts.width >= BORDER_W && opts.height >= REF_HEIGHT;
        let legend = opts.height >= ROWS + 1;

        let mut lines: Vec<String> = Vec::new();
        if title {
            lines.push(self.title_line(&p));
        }
        if border {
            lines.push(self.top_border(&p));
        }
        for r in 0..ROWS {
            let row = field_row(self, &p, r, !title && r == 0);
            lines.push(if border {
                let v = p.vline();
                if p.color {
                    format!("\x1b[90m{v}\x1b[0m{row}\x1b[90m{v}\x1b[0m")
                } else {
                    format!("{v}{row}{v}")
                }
            } else {
                row
            });
        }
        if border {
            lines.push(self.bottom_border(&p));
        }
        if legend {
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

/// The frame at the reference window: the renderer as a plain function of
/// state, exactly as the contract asks for.
pub fn frame(g: &Game) -> String {
    g.render(RenderOpts::default())
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
