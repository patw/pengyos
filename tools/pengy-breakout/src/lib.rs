//! pengy-breakout — Aurora Breakout: every rule of the game, in pure Rust.
//!
//! This file is the whole game and it touches **nothing** outside itself:
//! no I/O, no clock, no threads, no process, no system randomness. The only
//! randomness is the seeded xorshift64* below, so a run is a pure function of
//! `(seed, script)`. That is what makes `--replay` an acceptance harness, what
//! makes the frame a diffable pure function (`Game::render`), and what lets the
//! repo gate prove the game works in a place with no terminal at all.
//!
//! The front-end (`src/main.rs`) owns the keyboard, the screen, the clock and
//! the high-score file; it only ever calls [`Game::key`], [`Game::tick`],
//! [`Game::render`] and the state helpers here.
//!
//! ## Geometry, in one place
//!
//! The field is [`COLS`] x [`ROWS`] = 60x24 cells. Row 0 is the ceiling, rows
//! [`WALL_TOP`]..[`WALL_BOTTOM`] are the brick wall (6 rows x 10 columns, each
//! brick 6 cells wide), row [`PADDLE_ROW`] is the paddle row. Below it is the
//! floor: a ball that gets there costs a life.
//!
//! ## Physics
//!
//! The ball is one cell. Its position is fixed-point at [`FP_SCALE`] units per
//! cell; its velocity is a *direction* (a unit vector times 100, from an
//! integer angle table) and a *speed* in hundredths of a cell per tick. A tick
//! is [`SUBSTEPS`] sub-steps, and the fastest ball in the game moves
//! [`MAX_SUBSTEP_CELLS`] of a cell per sub-step — under half a cell, so it can
//! never tunnel through a 1-cell-tall brick, a 1-cell-tall paddle gap, or a
//! wall. Every collision is resolved inside a sub-step, never "after the
//! tick", which is what makes the trajectory independent of the frame rate.

use std::fmt::Write as _;

// ---------------------------------------------------------------------------
// Field geometry
// ---------------------------------------------------------------------------

/// Field width in cells.
pub const COLS: usize = 60;
/// Field height in cells. The reference window is 80x24, so the field fills
/// the window exactly and the score panel lives in the 18-column side margin.
pub const ROWS: usize = 24;
/// The paddle row: the last row of the field.
pub const PADDLE_ROW: usize = ROWS - 1;
/// First brick row.
pub const WALL_TOP: usize = 1;
/// Last brick row.
pub const WALL_BOTTOM: usize = WALL_TOP + BRICK_ROWS - 1;

/// Brick rows in a wall.
pub const BRICK_ROWS: usize = 6;
/// Brick columns in a wall.
pub const BRICK_COLS: usize = 10;
/// Cells per brick. 10 columns x 6 cells = the 60-cell field exactly.
pub const BRICK_W: usize = COLS / BRICK_COLS;
/// Bricks in a full 6x10 wall.
pub const BRICKS: usize = BRICK_ROWS * BRICK_COLS;

/// Paddle width in cells.
pub const PADDLE_W: usize = 8;
/// Cells the paddle moves per keypress. 2 cells per 40 ms tick is fast enough
/// to reach either side of a 60-cell field from the middle in under a second.
pub const PADDLE_STEP: i32 = 4;

/// Reference window: 80 columns.
pub const REF_WIDTH: usize = 80;
/// Reference window: 24 rows.
pub const REF_HEIGHT: usize = 24;
/// Width that needs the side panel (field + borders + 18-column panel).
pub const PANEL_W: usize = REF_WIDTH - (COLS + 2);
/// Width below which the side panel is dropped.
pub const PANEL_MIN_W: usize = REF_WIDTH;
/// Width below which the border is dropped (field + 2 border columns).
pub const BORDER_W: usize = COLS + 2;
/// Height below which the middle of the field collapses (see `visible_rows`).
pub const MIN_HEIGHT: usize = BRICK_ROWS + 2;

// ---------------------------------------------------------------------------
// Rules and tuning
// ---------------------------------------------------------------------------

/// Milliseconds per tick (fixed timestep; the front-end sleeps this long).
pub const TICK_MS: u64 = 40;

/// Fixed-point scale: units per cell. 400 = a quarter cell, so four
/// sub-steps of the base speed are exact integers.
pub const FP_SCALE: i64 = 400;
/// Sub-steps per tick. Four is the floor: see [`MAX_SUBSTEP_CELLS`].
pub const SUBSTEPS: usize = 4;

/// Serve speed, in hundredths of a cell per tick (100 = 1 cell per tick).
pub const SPEED_BASE: i64 = 100;
/// Speed cap. 180 keeps the fastest sub-step under half a cell.
pub const SPEED_MAX: i64 = 180;
/// Speed multiplier applied every `SPEED_STEP_BRICKS` bricks, in percent.
pub const SPEED_PERCENT: i64 = 104;
/// Bricks cleared per speed step: +4% per 10 bricks.
pub const SPEED_STEP_BRICKS: usize = 10;

/// The largest distance any axis moves in one sub-step, in cells:
/// `SPEED_MAX * 87 / 100 / FP_SCALE * (FP_SCALE / SUBSTEPS)` = 0.39 cells.
/// Under half a cell, and a brick is one cell tall — the no-tunnel proof.
pub const MAX_SUBSTEP_CELLS: f64 = 0.4;

/// Lives per game.
pub const LIVES: u32 = 3;
/// Levels per game.
pub const LEVELS: usize = 5;
/// Ticks a ball sticks to the paddle before each serve (1 second).
pub const SERVE_COUNTDOWN: u64 = 25;
/// Score bonus for clearing a wall.
pub const LEVEL_BONUS: u64 = 100;
/// Ticks a game may run before the autopilot's stall guard gives up (demo).
pub const MAX_TICKS: u64 = 200_000;

/// The serve angle, in degrees from vertical (a 30-degree serve is never a
/// vertical loop and never the 60-degree wall-scalding shot).
pub const SERVE_ANGLE: i64 = 30;
/// The autopilot aims the paddle centre at the predicted landing column when
/// it is further than this many cells away.
pub const AUTOPILOT_DEADBAND: i32 = 0;
/// Ticks the autopilot's landing prediction looks ahead.
pub const AUTOPILOT_LOOKAHEAD: usize = 600;
pub const AUTOPILOT_AIM_BIAS: i32 = 2;

/// The key legend, shown in the side panel and in the bottom border.
pub const LEGEND: &str = "a/d move  space serve  p pause  r restart  q quit";

// ---------------------------------------------------------------------------
// Seeded RNG — xorshift64*, hand-written. No `rand`, no system entropy.
// ---------------------------------------------------------------------------

/// xorshift64* (Vigna): 64-bit state, 2^64-1 period, `O(1)` per draw.
///
/// The state is never zero (a zero seed is remapped), so the period is the
/// full one. Every run takes `--seed N` and is bit-for-bit reproducible.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rng {
    state: u64,
}

impl Rng {
    /// Seed the stream. 0 is remapped to the golden constant: a zero state
    /// would make xorshift produce zeroes for ever.
    pub fn new(seed: u64) -> Self {
        Rng {
            state: if seed == 0 { 0x9E37_79B9_7F4A_7C15 } else { seed },
        }
    }

    /// The current state (for the canonical state string / hash).
    pub fn state(&self) -> u64 {
        self.state
    }

    /// The next 64 bits.
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545_FF4B_6F6F_921F)
    }

    /// A value in `0..n` (n = 0 returns 0, never a panic).
    pub fn below(&mut self, n: u64) -> u64 {
        if n == 0 {
            0
        } else {
            self.next_u64() % n
        }
    }
}

// ---------------------------------------------------------------------------
// Directions — an integer angle table, no floating-point trig at runtime
// ---------------------------------------------------------------------------

/// A unit direction, scaled by 100. `x` is rightward, `y` is downward (screen
/// coordinates: row 0 is the top), so an upward ball has a negative `y`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Dir {
    pub x: i64,
    pub y: i64,
}

/// `sin(deg) * 100` and `cos(deg) * 100` for deg = 0, 5, ... 60, rounded once,
/// by hand. The game never calls `sin`/`cos`: the bounce angle is an index
/// into this table, so a trajectory is integer arithmetic all the way down.
pub const ANGLE_SIN: [i64; 13] = [0, 9, 17, 26, 34, 42, 50, 57, 64, 71, 77, 82, 87];
/// The cosine half of the same table.
pub const ANGLE_COS: [i64; 13] = [100, 100, 98, 97, 94, 91, 87, 82, 77, 71, 64, 57, 50];

/// The widest bounce off the paddle edge, in degrees.
pub const MAX_ANGLE: i64 = 60;
/// Bounce angles come in 5-degree steps.
pub const ANGLE_STEP: i64 = 5;

/// The bounce angle for a paddle hit offset, in degrees: offset `-1.0` (the
/// paddle's left edge) is `-60`, `0.0` (the centre) is `0`, `+1.0` (the right
/// edge) is `+60`. Offsets outside `[-1.0, 1.0]` clamp.
pub fn angle_deg(offset: f64) -> i64 {
    let clamped = offset.clamp(-1.0, 1.0);
    // Round to the nearest 5-degree step, half away from zero.
    let steps = (clamped * MAX_ANGLE as f64 / ANGLE_STEP as f64).round();
    (steps as i64 * ANGLE_STEP).clamp(-MAX_ANGLE, MAX_ANGLE)
}

/// The direction a paddle hit at `offset` produces: `angle_deg` mapped through
/// the integer table, with `y` always negative (upward) — a paddle never
/// smashes the ball down into the floor.
pub fn bounce_dir(offset: f64) -> Dir {
    let deg = angle_deg(offset);
    let idx = (deg.unsigned_abs() / ANGLE_STEP as u64) as usize;
    Dir {
        x: ANGLE_SIN[idx] * deg.signum(),
        y: -ANGLE_COS[idx],
    }
}

/// The paddle hit offset for a ball at `col` meeting a paddle whose left edge
/// is at `left`: `-1.0` at the left edge, `+1.0` at the right edge, `0.0` at
/// the centre. The paddle is [`PADDLE_W`] cells wide, so the offsets come in
/// steps of `2 / PADDLE_W` = 0.25.
pub fn offset_for_hit(left: usize, col: usize) -> f64 {
    let centre = left as f64 + (PADDLE_W as f64 - 1.0) / 2.0;
    let half = (PADDLE_W as f64 - 1.0) / 2.0;
    ((col as f64 - centre) / half).clamp(-1.0, 1.0)
}

/// Distance one sub-step moves along one axis, in fixed-point units.
/// Rounded to nearest so a long rally does not drift.
pub fn sub_delta(speed: i64, comp: i64) -> i64 {
    let mag = (speed * comp.abs() + 50) / 100;
    if comp < 0 {
        -mag
    } else {
        mag
    }
}

/// What one sub-step of the ball produced. The paddle, the lives, the score
/// and the wall are the caller's business; the geometry is here.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Hit {
    /// Nothing was met.
    Nothing,
    /// The ball entered a brick cell; it was reflected on the face it crossed.
    Brick { row: usize, col: usize },
    /// The ball met the paddle row moving downward, at this column. Whether
    /// the paddle is *there* is the caller's question.
    Paddle { col: usize },
    /// The ball is below the paddle row: the floor.
    Floor,
}

/// Step the ball one sub-step against the side walls, the ceiling and the wall
/// of bricks. The ball's fixed-point position and direction are updated in
/// place; `bricks` is read-only (the caller clears the brick it is told about,
/// so this function has no score, no lives and no wall mutation).
///
/// `Game::sub_step` and [`Game::predict_col`] both call this, which is the
/// structural reason the autopilot's prediction is the real physics.
pub fn ball_step(
    x: &mut i64,
    y: &mut i64,
    dir: &mut Dir,
    speed: i64,
    bricks: &[[u8; BRICK_COLS]; BRICK_ROWS],
) -> Hit {
    let prev_col = *x / FP_SCALE;
    let prev_row = *y / FP_SCALE;
    *x += sub_delta(speed, dir.x);
    *y += sub_delta(speed, dir.y);

    // Side walls: clamp inside the field and reflect horizontally.
    if *x < 0 {
        *x = 0;
        dir.x = dir.x.abs();
    } else if *x >= (COLS as i64) * FP_SCALE {
        *x = (COLS as i64 - 1) * FP_SCALE;
        dir.x = -dir.x.abs();
    }
    // Ceiling.
    if *y < 0 {
        *y = 0;
        dir.y = dir.y.abs();
    }

    let col = (*x / FP_SCALE) as usize;
    let row = (*y / FP_SCALE) as usize;

    // A brick cell: reflect on the axis the ball *changed cell on*. A
    // diagonal entry prefers the vertical face, which is the face a 1-cell
    // tall brick is crossed on.
    if row >= WALL_TOP && row <= WALL_BOTTOM && col < COLS {
        if bricks[row - WALL_TOP][col / BRICK_W] == 1 {
            if prev_row != row as i64 {
                dir.y = -dir.y;
                *y = prev_row * FP_SCALE + (*y % FP_SCALE);
            } else {
                dir.x = -dir.x;
                *x = prev_col * FP_SCALE + (*x % FP_SCALE);
            }
            return Hit::Brick { row, col };
        }
    }

    // The paddle row, moving downward.
    if row == PADDLE_ROW && dir.y > 0 {
        return Hit::Paddle { col };
    }
    // Below the paddle row: gone.
    if row > PADDLE_ROW {
        return Hit::Floor;
    }
    Hit::Nothing
}

// ---------------------------------------------------------------------------
// Levels — five 6x10 walls
// ---------------------------------------------------------------------------

/// The five wall layouts, row by row, top first. `#` is a brick, `.` is open.
///
/// 1. **Solid** — the 1976 wall, 60 bricks.
/// 2. **Chevron** — a V; the ball runs down the arms and the middle is open.
/// 3. **Checker** — 30 bricks, every one of them an edge brick.
/// 4. **Gate** — a full wall with a 2-column gap: a straight shot to the floor.
/// 5. **Pyramid** — a diamond; the top row is a single brick.
pub const LAYOUTS: [[&str; BRICK_ROWS]; LEVELS] = [
    [
        "##########",
        "##########",
        "##########",
        "##########",
        "##########",
        "##########",
    ],
    [
        "#........#",
        "##......##",
        ".##....##.",
        "..##..##..",
        "...####...",
        "....##....",
    ],
    [
        "#.#.#.#.#.",
        ".#.#.#.#.#",
        "#.#.#.#.#.",
        ".#.#.#.#.#",
        "#.#.#.#.#.",
        ".#.#.#.#.#",
    ],
    [
        "####..####",
        "####..####",
        "####..####",
        "####..####",
        "####..####",
        "####..####",
    ],
    [
        "....##....",
        "...####...",
        "..######..",
        ".########.",
        "##########",
        "##########",
    ],
];

/// The layout for a level (0-based). Out of range clamps to the last level.
pub fn layout_rows(level: usize) -> [&'static str; BRICK_ROWS] {
    LAYOUTS[level.min(LEVELS - 1)]
}

/// Is cell `(row, col)` of the wall a brick? Rows above [`WALL_TOP`] and below
/// [`WALL_BOTTOM`] are open.
pub fn is_brick_cell(level: usize, row: usize, col: usize) -> bool {
    if row < WALL_TOP || row > WALL_BOTTOM || col >= COLS {
        return false;
    }
    let r = row - WALL_TOP;
    let line = layout_rows(level)[r];
    let b = line.as_bytes();
    let j = col / BRICK_W;
    j < BRICK_COLS && b[j] == b'#'
}

/// Bricks in one row of a level's wall. Rows outside the wall are 0.
pub fn bricks_in_row(level: usize, row: usize) -> usize {
    if row < WALL_TOP || row > WALL_BOTTOM {
        return 0;
    }
    layout_rows(level)[row - WALL_TOP]
        .bytes()
        .filter(|&c| c == b'#')
        .count()
}

/// Bricks in a whole level's wall.
pub fn bricks_in_level(level: usize) -> usize {
    layout_rows(level)
        .iter()
        .map(|l| l.bytes().filter(|&c| c == b'#').count())
        .sum()
}

/// Bricks in the whole game (all five walls).
pub fn bricks_in_game() -> usize {
    (0..LEVELS).map(bricks_in_level).sum()
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/// What the game is doing.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    /// The ball is stuck to the paddle, counting down to the serve.
    Serve,
    /// The ball is in play.
    Play,
    /// Frozen by `p`.
    Pause,
    /// Over: lives ran out, or all five walls are gone.
    Over,
}

impl State {
    /// The name in the `key=value` output.
    pub fn name(self) -> &'static str {
        match self {
            State::Serve => "serve",
            State::Play => "play",
            State::Pause => "pause",
            State::Over => "over",
        }
    }
}

/// Why the game ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cause {
    /// The third ball went through the paddle.
    Out,
    /// All five walls cleared.
    Complete,
}

impl Cause {
    /// The name in the `key=value` output.
    pub fn name(self) -> &'static str {
        match self {
            Cause::Out => "out",
            Cause::Complete => "complete",
        }
    }

    /// The end-screen text.
    pub fn text(self) -> &'static str {
        match self {
            Cause::Out => "out of lives",
            Cause::Complete => "all five walls cleared",
        }
    }
}

/// The whole game. `Copy`, so the autopilot can simulate a throwaway copy of
/// it to predict where the ball lands.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Game {
    /// The seed this run was built from.
    pub seed: u64,
    /// The RNG (serve angles; nothing else is random).
    pub rng: Rng,
    /// The state machine.
    pub state: State,
    /// Why it ended, once it has.
    pub cause: Option<Cause>,

    /// Ball x, in fixed-point units ([`FP_SCALE`] per cell).
    pub x: i64,
    /// Ball y, in fixed-point units.
    pub y: i64,
    /// Direction: a unit vector times 100.
    pub dir: Dir,
    /// Speed, in hundredths of a cell per tick.
    pub speed: i64,

    /// Left edge of the paddle, 0..=COLS - PADDLE_W.
    pub paddle_left: usize,

    /// The wall: 1 = brick present, 0 = cleared.
    pub bricks: [[u8; BRICK_COLS]; BRICK_ROWS],
    /// Bricks left in the current wall.
    pub bricks_left: usize,
    /// Bricks cleared in the whole game.
    pub cleared: usize,

    /// 0-based level index.
    pub level: usize,
    /// Lives left.
    pub lives: u32,
    /// Score.
    pub score: u64,
    /// The persisted high score, loaded by the front-end.
    pub high_score: u64,

    /// Ticks elapsed.
    pub ticks: u64,
    /// Ticks left on the serve countdown.
    pub countdown: u64,
    /// Balls served.
    pub serves: u64,
    /// Paddle hits.
    pub hits: u64,
    /// Bricks broken this life (drives the speed ladder).
    pub cleared_this_life: usize,
    /// Balls lost to the floor.
    pub deaths: u32,
    /// Longest rally (ticks between a serve and the point ending).
    pub rally: u64,
    /// Longest rally of the game.
    pub longest_rally: u64,
    /// Bricks cleared in the longest single life.
    pub best_life_bricks: usize,
}

impl Game {
    /// A game at `seed`: level 1, three lives, ball on the paddle, ready to
    /// serve. This is the only constructor.
    pub fn new(seed: u64) -> Self {
        let rng = Rng::new(seed);
        let mut g = Game {
            seed,
            rng,
            state: State::Serve,
            cause: None,
            x: 0,
            y: 0,
            dir: Dir { x: 0, y: -100 },
            speed: SPEED_BASE,
            paddle_left: (COLS - PADDLE_W) / 2,
            bricks: [[0; BRICK_COLS]; BRICK_ROWS],
            bricks_left: 0,
            cleared: 0,
            level: 0,
            lives: LIVES,
            score: 0,
            high_score: 0,
            ticks: 0,
            countdown: SERVE_COUNTDOWN,
            serves: 0,
            hits: 0,
            cleared_this_life: 0,
            deaths: 0,
            rally: 0,
            longest_rally: 0,
            best_life_bricks: 0,
        };
        g.build_wall(0);
        g.stick_ball();
        g.pick_serve_dir();
        g
    }

    /// Start a run at `seed` with a persisted high score already loaded.
    pub fn with(seed: u64, high_score: u64) -> Self {
        let mut g = Game::new(seed);
        g.high_score = high_score;
        g
    }

    /// Restart: same seed family, fresh wall, lives and score. The high score
    /// is a record, not a run property, so it survives.
    pub fn restart(&mut self) {
        let seed = self.seed;
        let high = self.high_score;
        *self = Game::new(seed);
        self.high_score = high;
    }

    /// Lay out level `level`'s wall.
    fn build_wall(&mut self, level: usize) {
        self.level = level;
        let rows = layout_rows(level);
        for (r, line) in rows.iter().enumerate() {
            for (j, c) in line.bytes().enumerate() {
                self.bricks[r][j] = u8::from(c == b'#');
            }
        }
        self.bricks_left = bricks_in_level(level);
    }

    /// Put the ball on the paddle, at its centre, one row above the paddle.
    fn stick_ball(&mut self) {
        let centre = self.paddle_left + PADDLE_W / 2;
        self.x = centre as i64 * FP_SCALE;
        self.y = (PADDLE_ROW - 1) as i64 * FP_SCALE;
    }

    /// Pick the serve direction: `SERVE_ANGLE` degrees off vertical, to a side
    /// chosen by the seeded RNG.
    fn pick_serve_dir(&mut self) {
        let rightward = self.rng.below(2) == 0;
        let offset = if rightward {
            SERVE_ANGLE as f64 / MAX_ANGLE as f64
        } else {
            -(SERVE_ANGLE as f64 / MAX_ANGLE as f64)
        };
        self.dir = bounce_dir(offset);
    }

    // -- accessors ----------------------------------------------------------

    /// The ball's column.
    pub fn ball_col(&self) -> usize {
        (self.x / FP_SCALE).clamp(0, COLS as i64 - 1) as usize
    }

    /// The ball's row.
    pub fn ball_row(&self) -> usize {
        (self.y / FP_SCALE).clamp(0, ROWS as i64 - 1) as usize
    }

    /// The paddle's centre column.
    pub fn paddle_centre(&self) -> usize {
        self.paddle_left + PADDLE_W / 2
    }

    /// Does the paddle cover `col`?
    pub fn paddle_covers(&self, col: usize) -> bool {
        col >= self.paddle_left && col < self.paddle_left + PADDLE_W
    }

    /// The speed step this many bricks clears (the ladder, as a pure function).
    pub fn speed_for_cleared(cleared: usize) -> i64 {
        let steps = (cleared / SPEED_STEP_BRICKS) as u32;
        let mut s = SPEED_BASE;
        for _ in 0..steps {
            s = (s * SPEED_PERCENT / 100).min(SPEED_MAX);
        }
        s
    }

    // -- the paddle ---------------------------------------------------------

    /// Move the paddle by `delta` cells, clamped to the field.
    pub fn move_paddle(&mut self, delta: i32) {
        let max = (COLS - PADDLE_W) as i32;
        let mut left = self.paddle_left as i32 + delta;
        if left < 0 {
            left = 0;
        }
        if left > max {
            left = max;
        }
        self.paddle_left = left as usize;
    }

    /// Put the paddle's centre at `col` (used by the autopilot).
    pub fn centre_paddle_on(&mut self, col: usize) {
        let want = (col as i32 - (PADDLE_W as i32 / 2)).clamp(0, (COLS - PADDLE_W) as i32);
        self.paddle_left = want as usize;
    }

    // -- the ball -----------------------------------------------------------

    /// Launch the stuck ball in the chosen serve direction.
    pub fn serve_now(&mut self) {
        if self.state == State::Serve {
            self.launch();
        }
    }

    fn launch(&mut self) {
        self.state = State::Play;
        self.serves += 1;
        self.rally = 0;
        self.countdown = 0;
        self.cleared_this_life = 0;
    }

    /// The ball reached the floor: lose a life, then serve again, or end.
    fn lose_life(&mut self) {
        self.deaths += 1;
        self.lives = self.lives.saturating_sub(1);
        if self.rally > self.longest_rally {
            self.longest_rally = self.rally;
        }
        if self.cleared_this_life > self.best_life_bricks {
            self.best_life_bricks = self.cleared_this_life;
        }
        if self.lives == 0 {
            self.state = State::Over;
            self.cause = Some(Cause::Out);
            return;
        }
        self.speed = SPEED_BASE;
        self.state = State::Serve;
        self.countdown = SERVE_COUNTDOWN;
        self.stick_ball();
        self.pick_serve_dir();
    }

    /// A wall is empty: bank the bonus, advance, or win.
    fn clear_wall(&mut self) {
        self.score += LEVEL_BONUS;
        if self.level + 1 >= LEVELS {
            self.state = State::Over;
            self.cause = Some(Cause::Complete);
            return;
        }
        self.build_wall(self.level + 1);
        self.speed = SPEED_BASE;
        self.cleared_this_life = 0;
        self.state = State::Serve;
        self.countdown = SERVE_COUNTDOWN;
        self.stick_ball();
        self.pick_serve_dir();
    }

    /// One sub-step: move the ball, then react to what it met. This is the
    /// whole physics; a tick is [`SUBSTEPS`] calls of it. The geometry lives in
    /// [`ball_step`], which the autopilot's prediction calls too, so the
    /// prediction can never drift from the rules.
    fn sub_step(&mut self) {
        match ball_step(&mut self.x, &mut self.y, &mut self.dir, self.speed, &self.bricks) {
            Hit::Nothing => {}
            Hit::Brick { row, col } => self.break_brick(row, col),
            Hit::Paddle { col } => {
                // A downward ball meeting a covered paddle cell bounces at the
                // angle its hit offset maps to. The ball is pushed one cell up
                // so the next sub-step cannot meet the same face twice.
                if self.paddle_covers(col) {
                    self.dir = bounce_dir(offset_for_hit(self.paddle_left, col));
                    self.y = (PADDLE_ROW - 1) as i64 * FP_SCALE;
                    self.hits += 1;
                }
            }
            Hit::Floor => self.lose_life(),
        }
    }

    /// Break the brick under `(row, col)` and bank the score and the speed
    /// ladder. The reflection itself happened in [`ball_step`].
    fn break_brick(&mut self, row: usize, col: usize) {
        if row < WALL_TOP || row > WALL_BOTTOM || col >= COLS {
            return;
        }
        let r = row - WALL_TOP;
        let j = col / BRICK_W;
        if self.bricks[r][j] == 0 {
            return;
        }
        self.bricks[r][j] = 0;
        self.bricks_left -= 1;
        self.cleared += 1;
        self.cleared_this_life += 1;
        self.score += 1;
        if self.cleared % SPEED_STEP_BRICKS == 0 {
            self.speed = (self.speed * SPEED_PERCENT / 100).min(SPEED_MAX);
        }
    }

    /// Advance the game by one tick.
    pub fn tick(&mut self) {
        self.ticks += 1;
        match self.state {
            State::Serve => {
                self.stick_ball();
                self.countdown = self.countdown.saturating_sub(1);
                if self.countdown == 0 {
                    self.launch();
                }
            }
            State::Play => {
                self.rally += 1;
                for _ in 0..SUBSTEPS {
                    if self.state != State::Play {
                        break;
                    }
                    self.sub_step();
                }
                if self.state == State::Play && self.bricks_left == 0 {
                    self.clear_wall();
                }
            }
            State::Pause | State::Over => {}
        }
    }

    // -- keys ---------------------------------------------------------------

    /// The paddle key for a character: `Some(-1)` left, `Some(+1)` right,
    /// `None` for anything else. `a`/`d`, `h`/`l` and the arrow letters the
    /// front-end delivers (`D` = left arrow, `C` = right arrow) all work.
    /// Uppercase `D` is the left arrow and lowercase `d` is the right: the
    /// front-end never sends a lowercase `D`, so the two never collide.
    pub fn paddle_key(&self, c: char) -> Option<i32> {
        match c {
            'a' | 'A' | 'h' | 'H' | 'D' => Some(-PADDLE_STEP),
            'd' | 'l' | 'L' | 'C' => Some(PADDLE_STEP),
            _ => None,
        }
    }

    /// Apply one keypress. Returns true when the key quits.
    ///
    /// `q` quits, `space` serves, `p` pauses, `r` restarts. A paddle key works
    /// in every state except `Over` (the end screen is not playable).
    pub fn key(&mut self, c: char) -> bool {
        match c {
            'q' | 'Q' => return true,
            ' ' => {
                self.serve_now();
            }
            'p' | 'P' => self.toggle_pause(),
            'r' | 'R' => self.restart(),
            _ => {
                if let Some(d) = self.paddle_key(c) {
                    if self.state != State::Over {
                        self.move_paddle(d);
                    }
                }
            }
        }
        false
    }

    /// `p`: pause and resume. Never pauses a finished game.
    pub fn toggle_pause(&mut self) {
        match self.state {
            State::Play => self.state = State::Pause,
            State::Pause => self.state = State::Play,
            _ => {}
        }
    }

    // -- the autopilot (demo mode, the headless path, the fixture maker) ----

    /// Where the ball will meet the paddle row, simulated forward with the same
    /// [`ball_step`] the tick uses. Falls back to the ball's current column
    /// when the lookahead runs out, which is the honest answer: "I do not know
    /// yet". The paddle is not in the simulation — the question is where the
    /// ball arrives, not where the paddle is.
    pub fn predict_col(&self) -> usize {
        let mut x = self.x;
        let mut y = self.y;
        let mut dir = self.dir;
        let mut bricks = self.bricks;
        let speed = self.speed;
        for _ in 0..AUTOPILOT_LOOKAHEAD * SUBSTEPS {
            match ball_step(&mut x, &mut y, &mut dir, speed, &bricks) {
                Hit::Nothing => {}
                Hit::Brick { row, col } => {
                    if row >= WALL_TOP && row <= WALL_BOTTOM && col < COLS {
                        bricks[row - WALL_TOP][col / BRICK_W] = 0;
                    }
                }
                Hit::Paddle { col } => return col,
                Hit::Floor => return (x / FP_SCALE).clamp(0, COLS as i64 - 1) as usize,
            }
        }
        (x / FP_SCALE).clamp(0, COLS as i64 - 1) as usize
    }

    /// The paddle centre the autopilot wants for a ball landing at `landing`.
    ///
    /// A centre hit sends the ball straight up, which is what you want when
    /// there is a brick above the landing column. When that column is already
    /// empty, a centre hit is a vertical loop that never ends, so the aim is
    /// offset by [`AUTOPILOT_AIM_BIAS`] cells to the side that has bricks left:
    /// the ball comes back at ~34 degrees and travels toward them.
    pub fn aim_centre(&self, landing: usize) -> i32 {
        let j = landing / BRICK_W;
        let column_clear = (0..BRICK_ROWS).all(|r| self.bricks[r][j] == 0);
        if !column_clear {
            return landing as i32;
        }
        // Nearest brick column that still has a brick, by centre distance.
        let mut best: Option<(i64, usize)> = None;
        for c in 0..BRICK_COLS {
            if (0..BRICK_ROWS).any(|r| self.bricks[r][c] == 1) {
                let centre = c * BRICK_W + BRICK_W / 2;
                let d = (centre as i64 - landing as i64).abs();
                if best.map(|(bd, _)| d < bd).unwrap_or(true) {
                    best = Some((d, centre));
                }
            }
        }
        let centre = match best {
            Some((_, centre)) => centre,
            None => return landing as i32,
        };
        // Aim the angle at the target: to move `dx` cells sideways over the
        // `dy` cells from the paddle to the middle of the wall, the hit offset
        // is dx/dy, and an offset of 1.0 is `half` cells of paddle.
        let dx = centre as i32 - landing as i32;
        let sign = if dx >= 0 { 1 } else { -1 };
        let dy = (PADDLE_ROW - (WALL_TOP + BRICK_ROWS / 2)) as i32;
        let half = ((PADDLE_W - 1) / 2) as i32;
        let want = (dx.abs() * half / dy).clamp(1, half);
        // Stall breaker: a long rally with no brick means this angle is
        // looping, so walk the aim one cell steeper every 200 ticks. A pure
        // function of the rally, so the session stays deterministic.
        let extra = ((self.rally / 200) % (half as u64 + 1)) as i32;
        let offset_cells = (want + extra).clamp(1, half);
        (landing as i32 - offset_cells * sign).clamp(0, COLS as i32)
    }

    /// The keys the autopilot wants on this tick: serve when stuck, then track
    /// the predicted landing column with the paddle. Deterministic, so a
    /// recorded session replays identically.
    pub fn autopilot_keys(&self) -> String {
        if self.state == State::Serve {
            return " ".to_string();
        }
        if self.state != State::Play {
            return String::new();
        }
        let want = self.aim_centre(self.predict_col());
        let have = self.paddle_centre() as i32;
        let diff = want - have;
        if diff < -AUTOPILOT_DEADBAND {
            "a".to_string()
        } else if diff > AUTOPILOT_DEADBAND {
            "d".to_string()
        } else {
            String::new()
        }
    }

    // -- state as data ------------------------------------------------------

    /// The state as one canonical string: every number that defines the run,
    /// in a fixed order. The determinism test compares these.
    pub fn canonical(&self) -> String {
        let mut s = String::new();
        let _ = writeln!(
            s,
            "seed={} state={} cause={} level={} lives={} score={} high={}",
            self.seed,
            self.state.name(),
            self.cause.map(Cause::name).unwrap_or("none"),
            self.level + 1,
            self.lives,
            self.score,
            self.high_score,
        );
        let _ = writeln!(
            s,
            "x={} y={} dirx={} diry={} speed={} paddle={}",
            self.x,
            self.y,
            self.dir.x,
            self.dir.y,
            self.speed,
            self.paddle_left,
        );
        let _ = writeln!(
            s,
            "ticks={} countdown={} serves={} hits={} deaths={} cleared={} bricks_left={} rally={} longest={} best_life_bricks={} rng={}",
            self.ticks,
            self.countdown,
            self.serves,
            self.hits,
            self.deaths,
            self.cleared,
            self.bricks_left,
            self.rally,
            self.longest_rally,
            self.best_life_bricks,
            self.rng.state(),
        );
        for r in 0..BRICK_ROWS {
            let _ = writeln!(
                s,
                "wall{}={}",
                r,
                self.bricks[r]
                    .iter()
                    .map(|&b| if b == 0 { '.' } else { '#' })
                    .collect::<String>()
            );
        }
        s
    }

    /// FNV-1a over the canonical state. Same seed + same script = same hash.
    pub fn state_hash(&self) -> u64 {
        let mut h: u64 = 0xcbf2_9ce4_8422_2325;
        for b in self.canonical().as_bytes() {
            h ^= *b as u64;
            h = h.wrapping_mul(0x1000_0000_01b3);
        }
        h
    }

    /// The result as `key=value` pairs: what `--headless` and `--replay` print.
    pub fn result_kv(&self) -> Vec<(String, String)> {
        let mut kv: Vec<(String, String)> = Vec::new();
        let mut add = |k: &str, v: String| kv.push((k.to_string(), v));
        add("game", "pengy-breakout".to_string());
        add("seed", self.seed.to_string());
        add("state", self.state.name().to_string());
        add(
            "cause",
            self.cause.map(Cause::name).unwrap_or("none").to_string(),
        );
        add("level", (self.level + 1).to_string());
        add("levels", LEVELS.to_string());
        add("lives", self.lives.to_string());
        add("score", self.score.to_string());
        add("high_score", self.high_score.to_string());
        add("bricks_cleared", self.cleared.to_string());
        add("bricks_left", self.bricks_left.to_string());
        add("ticks", self.ticks.to_string());
        add("serves", self.serves.to_string());
        add("hits", self.hits.to_string());
        add("deaths", self.deaths.to_string());
        add("longest_rally", self.longest_rally.to_string());
        add("best_life_bricks", self.best_life_bricks.to_string());
        add("speed", self.speed.to_string());
        add("paddle", self.paddle_left.to_string());
        add("ball_row", self.ball_row().to_string());
        add("ball_col", self.ball_col().to_string());
        add("hash", format!("{:016x}", self.state_hash()));
        kv
    }

    /// The game is finished (won or lost).
    pub fn is_over(&self) -> bool {
        self.state == State::Over
    }

    /// Did the player clear all five walls?
    pub fn is_won(&self) -> bool {
        self.cause == Some(Cause::Complete)
    }

    // -- rendering ----------------------------------------------------------

    /// Paint the whole screen. The front-end writes this with one `home` plus
    /// one write, which repaints with no leftovers and no flicker.
    ///
    /// Exactly `opts.height` rows, every row exactly `opts.width` visible
    /// columns. The field is 24 rows and the reference window is 24 rows, so
    /// the score panel lives in the 18-column side margin, not in extra rows:
    /// the panel needs [`PANEL_MIN_W`] columns, the border [`BORDER_W`], and
    /// below [`MIN_HEIGHT`] rows the *middle* of the field collapses (the wall
    /// rows and the paddle row are never dropped).
    pub fn render(&self, opts: RenderOpts) -> String {
        let p = Painter {
            mono: opts.mono,
            color: opts.color && !opts.mono,
        };
        let panel = opts.width >= PANEL_MIN_W && opts.height >= MIN_HEIGHT;
        let border = opts.width >= BORDER_W;
        // In a window too narrow for the side panel the score line and the key
        // legend ride the top and bottom borders, so the keys are always on
        // screen. That spends two rows, so the field band is two rows shorter.
        let chrome = border && !panel;
        let field_top = usize::from(chrome);
        let field_rows = opts.height.saturating_sub(usize::from(chrome) * 2);

        let panel_lines: Vec<String> = if panel {
            self.panel_lines(&p)
        } else {
            Vec::new()
        };

        let mut out = String::new();
        for screen_row in 0..opts.height {
            if chrome && (screen_row == 0 || screen_row + 1 == opts.height) {
                let line = self.chrome_line(&p, screen_row == 0);
                let visible = strip_sgr(&line).chars().count();
                if visible <= opts.width {
                    let _ = writeln!(out, "{line}{}", " ".repeat(opts.width - visible));
                } else {
                    let clipped: String = strip_sgr(&line).chars().take(opts.width).collect();
                    let _ = writeln!(out, "{clipped}");
                }
                continue;
            }
            let Some(field_row) = visible_row(field_rows, screen_row - field_top) else {
                let _ = writeln!(out, "{}", " ".repeat(opts.width));
                continue;
            };
            let mut line = String::new();
            if border {
                line.push(self.border_cell(&p, screen_row, opts.height));
            }
            line.push_str(&self.field_row(&p, field_row));
            if border {
                line.push(self.border_cell(&p, screen_row, opts.height));
            }
            if panel {
                let _ = write!(line, " {}", panel_lines[screen_row]);
            }
            // Width is measured on the *visible* text: an escape costs nothing
            // on screen. A row that fits is emitted verbatim (colour intact);
            // a row that does not is truncated to plain text, which is what
            // keeps a narrow window from wrapping.
            let visible = strip_sgr(&line).chars().count();
            if visible <= opts.width {
                let _ = writeln!(out, "{line}{}", " ".repeat(opts.width - visible));
            } else {
                let clipped: String = strip_sgr(&line).chars().take(opts.width).collect();
                let _ = writeln!(out, "{clipped}");
            }
        }
        out
    }

    /// The score line (top) and the key legend line (bottom), used as borders
    /// when there is no room for the side panel. Exactly [`BORDER_W`] columns.
    fn chrome_line(&self, p: &Painter, top: bool) -> String {
        let c = p.corner();
        let h = p.hline();
        let room = COLS;
        let text = if top {
            let lives: String = (0..LIVES)
                .map(|i| {
                    if i < self.lives {
                        p.life()
                    } else {
                        p.life_empty()
                    }
                })
                .collect();
            format!(
                "score {}  best {}  lives {lives}  level {}/{}  bricks {}",
                self.score,
                self.high_score.max(self.score),
                self.level + 1,
                LEVELS,
                self.bricks_left
            )
        } else {
            LEGEND.to_string()
        };
        let text: String = text.chars().take(room.saturating_sub(4)).collect();
        let pad = room - text.chars().count();
        let left = pad / 2;
        let line = format!(
            "{}{}{}{}{}",
            if top { c.0 } else { c.2 },
            h.to_string().repeat(left),
            text,
            h.to_string().repeat(pad - left),
            if top { c.1 } else { c.3 }
        );
        if p.color {
            format!("\x1b[90m{line}\x1b[0m")
        } else {
            line
        }
    }

    /// The left/right border cell for a screen row: corner at the ends, the
    /// key legend carried along the bottom border.
    fn border_cell(&self, p: &Painter, screen_row: usize, height: usize) -> char {
        if screen_row == 0 {
            p.corner().0
        } else if screen_row + 1 == height {
            p.corner().2
        } else {
            p.vline()
        }
    }

    /// One field row: the wall, the paddle, the ball. Exactly `COLS` columns.
    fn field_row(&self, p: &Painter, row: usize) -> String {
        let mut cells: Vec<char> = vec![p.floor(); COLS];
        for col in 0..COLS {
            let r = row;
            if r >= WALL_TOP && r <= WALL_BOTTOM {
                let j = col / BRICK_W;
                if self.bricks[r - WALL_TOP][j] == 1 {
                    cells[col] = p.brick(r - WALL_TOP);
                }
            }
        }
        if row == PADDLE_ROW {
            for c in self.paddle_left..(self.paddle_left + PADDLE_W).min(COLS) {
                cells[c] = p.paddle();
            }
        }
        if self.ball_row() == row {
            let c = self.ball_col();
            if c < COLS {
                cells[c] = p.ball();
            }
        }
        let raw: String = cells.into_iter().collect();
        if p.color {
            // Colour is applied per run of identical glyphs, so a brick band
            // is one escape, not sixty.
            let mut s = String::new();
            let mut i = 0;
            while i < COLS {
                let ch = raw.as_bytes()[i] as char;
                let mut j = i;
                while j < COLS && (raw.as_bytes()[j] as char) == ch {
                    j += 1;
                }
                let chunk: String = raw[i..j].chars().collect();
                let code = p.color_for(ch, row);
                match code {
                    Some(c) => {
                        let _ = write!(s, "\x1b[{c}m{chunk}\x1b[0m");
                    }
                    None => s.push_str(&chunk),
                }
                i = j;
            }
            s
        } else {
            raw
        }
    }

    /// The side panel: title, score, lives, level, the key legend, and the
    /// one line of news the player needs right now.
    fn panel_lines(&self, p: &Painter) -> Vec<String> {
        let w = PANEL_W;
        let mut lines: Vec<String> = Vec::new();
        let push = |lines: &mut Vec<String>, text: String, color: Option<u8>| {
            let pad = w.saturating_sub(strip_sgr(&text).chars().count());
            let line = format!("{text}{}", " ".repeat(pad));
            lines.push(match color {
                Some(c) if p.color => format!("\x1b[{c}m{line}\x1b[0m"),
                _ => line,
            });
        };
        push(
            &mut lines,
            "AURORA BREAKOUT".to_string(),
            Some(1),
        );
        push(&mut lines, String::new(), None);
        push(
            &mut lines,
            format!(" score {:>6}", self.score),
            Some(1),
        );
        push(
            &mut lines,
            format!(" best  {:>6}", self.high_score.max(self.score)),
            Some(3),
        );
        let lives: String = (0..LIVES)
            .map(|i| {
                if i < self.lives {
                    p.life()
                } else {
                    p.life_empty()
                }
            })
            .collect();
        push(&mut lines, format!(" lives {:>6}", lives), Some(33));
        push(
            &mut lines,
            format!(" level {:>3}/{LEVELS}", self.level + 1),
            Some(36),
        );
        push(
            &mut lines,
            format!(" bricks {:>4}", self.bricks_left),
            Some(34),
        );
        push(
            &mut lines,
            format!(" speed {:>4}", self.speed),
            Some(35),
        );
        push(&mut lines, String::new(), None);
        push(&mut lines, self.news_line(), Some(33));
        push(&mut lines, String::new(), None);
        for key in LEGEND.split("  ") {
            push(&mut lines, format!(" {key}"), Some(90));
        }
        // Exactly `height` panel lines, top-aligned.
        while lines.len() < REF_HEIGHT {
            lines.push(String::new());
        }
        lines.truncate(REF_HEIGHT);
        lines
    }

    /// The one line that tells the player what is happening.
    fn news_line(&self) -> String {
        match self.state {
            State::Serve => format!("SERVE IN {:>2}", self.countdown),
            State::Play => {
                if self.rally > 40 && self.dir.y < 0 && self.ball_row() < WALL_TOP + 2 {
                    "nicely pitched".to_string()
                } else {
                    format!("rally {}", self.rally)
                }
            }
            State::Pause => "PAUSED".to_string(),
            State::Over => match self.cause {
                Some(Cause::Complete) => "WALLS DOWN. YOU WIN.".to_string(),
                Some(Cause::Out) => "OUT OF LIVES.".to_string(),
                None => "OVER".to_string(),
            },
        }
        .chars()
        .take(PANEL_W)
        .collect()
    }
}

/// The glyph set: box-drawing and blocks, or pure ASCII under `--mono`.
#[derive(Clone, Copy, Debug)]
struct Painter {
    mono: bool,
    color: bool,
}

/// The colour band per brick row (basic ANSI, so any terminal renders it).
const BRICK_COLORS: [u8; BRICK_ROWS] = [91, 95, 96, 94, 92, 93];
/// The `--mono` glyph band per brick row: the wall is readable with no colour.
/// Deliberately excludes `#`, which is the paddle's mono glyph.
const BRICK_GLYPHS: [char; BRICK_ROWS] = ['^', '%', '@', '+', '=', '~'];

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
    fn paddle(&self) -> char {
        if self.mono {
            '#'
        } else {
            '█'
        }
    }
    fn ball(&self) -> char {
        if self.mono {
            'O'
        } else {
            '●'
        }
    }
    fn floor(&self) -> char {
        ' '
    }
    fn life(&self) -> char {
        if self.mono {
            'o'
        } else {
            '●'
        }
    }
    fn life_empty(&self) -> char {
        if self.mono {
            '.'
        } else {
            '·'
        }
    }
    fn brick(&self, row: usize) -> char {
        if self.mono {
            BRICK_GLYPHS[row.min(BRICK_ROWS - 1)]
        } else {
            '█'
        }
    }
    /// The SGR code for a glyph, so the renderer's colour is a function of the
    /// glyph and the row and nothing else.
    fn color_for(&self, ch: char, row: usize) -> Option<u8> {
        if !self.color {
            return None;
        }
        if ch == self.paddle() {
            return Some(107);
        }
        if ch == self.ball() {
            return Some(97);
        }
        if !self.mono && ch == '█' {
            return Some(BRICK_COLORS[row.min(BRICK_ROWS - 1)]);
        }
        if self.mono && BRICK_GLYPHS.contains(&ch) {
            return Some(BRICK_COLORS[BRICK_GLYPHS.iter().position(|&g| g == ch).unwrap_or(0)]);
        }
        None
    }
}

/// Which field row a screen row shows. The field is [`ROWS`] tall and the
/// window is `height` rows: when the window is shorter than the field the
/// *middle* collapses, keeping the top `WALL_BOTTOM + 1` rows (the ceiling and
/// the whole wall) and the bottom rows down to the paddle. The wall rows and
/// the paddle row are never dropped. `None` = a blank row.
pub fn visible_row(height: usize, screen_row: usize) -> Option<usize> {
    if height >= ROWS {
        return if screen_row < ROWS { Some(screen_row) } else { None };
    }
    let head = (WALL_BOTTOM + 1).min(height.saturating_sub(1));
    let tail = height - head;
    if screen_row < head {
        Some(screen_row)
    } else if screen_row >= height - tail {
        let idx = screen_row - (height - tail);
        Some(ROWS - tail + idx)
    } else {
        None
    }
}

/// Strip every SGR escape out of a string (used to measure and clip coloured
/// rows to the window width without cutting an escape in half).
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
        }
    }
    out
}

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

/// The plain-text state `--headless` prints: `key=value` lines, then the field
/// as ASCII rows. No escapes, diffable, and the shape the gate greps.
pub fn plain_state(g: &Game) -> String {
    let mut s = String::new();
    for (k, v) in g.result_kv() {
        let _ = writeln!(s, "{k}={v}");
    }
    let _ = writeln!(s, "field:");
    for r in 0..ROWS {
        let mut line = String::new();
        for c in 0..COLS {
            let brick = r >= WALL_TOP
                && r <= WALL_BOTTOM
                && g.bricks[r - WALL_TOP][c / BRICK_W] == 1;
            let ch = if r == PADDLE_ROW && g.paddle_covers(c) {
                '#'
            } else if c == g.ball_col() && r == g.ball_row() {
                'O'
            } else if brick {
                BRICK_GLYPHS[r - WALL_TOP]
            } else {
                '.'
            };
            line.push(ch);
        }
        let _ = writeln!(s, "|{line}|");
    }
    s
}

/// The frame as plain text (no escapes), for `--print-frame` and for tests:
/// the same rows `render` produces, with colour off and `--mono` glyphs on.
pub fn plain_frame(g: &Game, opts: RenderOpts) -> String {
    g.render(RenderOpts {
        width: opts.width,
        height: opts.height,
        mono: true,
        color: false,
    })
}
