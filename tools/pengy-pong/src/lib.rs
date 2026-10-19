//! pengy-pong — Penguin Pong: **every rule and the renderer, in pure code**.
//!
//! This file never touches the outside world: no I/O, no clock, no threads, no
//! files, no system entropy. A run is a function of `(seed, script)`, which is
//! what makes the headless harness (`--replay`) a real acceptance test and what
//! makes `render()` a pure, diffable function of state.
//!
//! The game: the 1972 arcade original. Two paddles on a 60x20 field, one ball,
//! first to eleven. The ball crosses the field one cell per tick at serve
//! speed; a paddle bounce sets the new angle from the row the ball met the
//! paddle on and lifts the speed by 5%, capped at twice the serve speed. Play
//! the penguin at difficulty 1, 2 or 3, or a friend on the same keyboard.
//!
//! See `spec.md` for the contract and `tests/rules.rs` for the rules asserted.

use std::fmt::Write as _;

// ---------------------------------------------------------------------------
// Tuning constants — the difficulty of the game lives here.
// ---------------------------------------------------------------------------

/// Fixed timestep of the front-end loop, in milliseconds. The ball moves one
/// cell per tick at serve speed, so this *is* the game's speed control.
pub const TICK_MS: u64 = 40;

/// Field width in columns.
pub const COLS: usize = 60;
/// Field height in rows.
pub const ROWS: usize = 20;

/// A paddle is 4 rows tall and 1 column wide.
pub const PADDLE_H: usize = 4;
/// The column the player's paddle lives on.
pub const LEFT_COL: usize = 1;
/// The column the penguin's (or player 2's) paddle lives on.
pub const RIGHT_COL: usize = 58;

/// Fixed-point scale: positions and velocities are cells x [`FP_SCALE`]. The
/// ball's position is fixed-point so a 50-degree bounce is a real angle and not
/// a rounding artefact.
pub const FP_SCALE: i64 = 100;
/// Serve speed: one cell per tick, in fixed-point units.
pub const SERVE_SPEED: i64 = 100;
/// The speed cap: twice the serve speed.
pub const MAX_SPEED: i64 = 200;
/// After a paddle hit the speed is `speed * SPEED_PERCENT / 100`.
pub const SPEED_PERCENT: i64 = 105;

/// The serve countdown: 1 second at 40 ms per tick.
pub const SERVE_COUNTDOWN: u64 = 25;

/// First to eleven. At 10-10 a two-point margin is required.
pub const WIN_SCORE: u64 = 11;
/// Hard cap: a match can never pass this score.
pub const CAP_SCORE: u64 = 15;

/// The three AI difficulties: rows per tick the penguin may move.
pub const AI_CAPS: [usize; 3] = [1, 2, 3];
/// The three AI difficulties: ticks of reaction delay (6 / 3 / 1).
pub const AI_DELAYS: [u64; 3] = [6, 3, 1];
/// Highest selectable difficulty.
pub const MAX_DIFFICULTY: u8 = 3;
/// The autopilot's reaction delay, in ticks: a strong human (2 ticks), not a
/// perfect machine. It is what makes the demo path beatable by level 3.
pub const AUTOPILOT_DELAY: usize = 2;
/// Which paddle row each level tries to meet the ball with: 1 is the shallow
/// near-centre bounce, 3 is the steep edge. A paddle that only ever centres on
/// the ball returns a shallow ball, and a rally of shallow balls never ends —
/// aiming is what makes a rally develop.
pub const AI_AIM: [usize; 3] = [1, 1, 1];
/// The autopilot plays the steep edge bounce.
pub const AUTOPILOT_AIM: usize = 1;
/// The autopilot's speed cap, in rows per tick: a strong human hand (2), not
/// the machine's 3. It is what keeps the demo path losable against level 3.
pub const AUTOPILOT_CAP: usize = 2;
/// The rally cap, in ticks (900 ticks = 36 seconds). A rally that never ends
/// is a stall, not a game: past the cap the point goes to the player who hit
/// the ball last, because the other one has had the whole rally to return it.
/// This is what guarantees every point ends.
pub const MAX_RALLY_TICKS: u64 = 900;

/// Spin: a paddle that is moving when the ball meets it adds this much vy per
/// row per tick of paddle motion (fixed-point). It is the 1972 trick that makes
/// a rally develop — a paddle that only ever centres on the ball returns the
/// same shallow ball for ever, and a paddle that is chasing it does not.
pub const SPIN_PER_ROW: i64 = 40;
/// The reaction-delay history ring is as long as the longest delay.
pub const AI_VIEW_LEN: usize = 6;

/// One entry of the bounce-angle table.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Bounce {
    /// Which paddle row the ball met: 0 = top row .. 3 = bottom row.
    pub slot: usize,
    /// The outgoing angle, in degrees, measured from the horizontal. Positive
    /// angles send the ball down the field (rows grow downwards).
    pub angle: i32,
    /// tan(angle) x [`FP_SCALE`]: the vy gained per unit of speed.
    pub tan: i64,
}

/// The reflection angle table, in 10-degree steps. A 4-row paddle has no exact
/// centre row, so the two inner rows give the shallowest bounce the game allows
/// (+/-10 degrees — the closest thing to "straight" a 4-row paddle has) and the
/// two edge rows give the full +/-50 degrees. Every angle is a multiple of 10.
pub const BOUNCES: [Bounce; 4] = [
    Bounce { slot: 0, angle: -50, tan: -119 },
    Bounce { slot: 1, angle: -10, tan: -18 },
    Bounce { slot: 2, angle: 10, tan: 18 },
    Bounce { slot: 3, angle: 50, tan: 119 },
];

/// Look up the bounce for a paddle hit. `row` is the ball's row, `top` the
/// paddle's top row; the slot is clamped to the paddle, so a hit is always one
/// of the four table entries.
pub fn bounce_for(row: usize, top: usize) -> Bounce {
    BOUNCES[row.saturating_sub(top).min(PADDLE_H - 1)]
}

/// vy for a bounce at the given speed: `speed * tan(angle)`, fixed-point.
pub fn vy_for(b: Bounce, speed: i64) -> i64 {
    speed * b.tan / FP_SCALE
}

/// The speed after one paddle hit, capped at [`MAX_SPEED`].
pub fn next_speed(speed: i64) -> i64 {
    (speed * SPEED_PERCENT / 100).min(MAX_SPEED)
}

/// Width the field needs with its border. Below it the border goes.
pub const BORDER_W: usize = COLS + 2;
/// Width at or above which the title/scoreboard line is drawn.
pub const TITLE_W: usize = 68;
/// Width at or above which the header rule line is drawn (needs the height too).
pub const HEADER_W: usize = 30;
/// The key legend the bottom border carries.
pub const LEGEND: &str = "w/s move  p pause  r restart  m 1P/2P  1/2/3 level  q quit";
/// The reference window the layout is designed for.
pub const REF_WIDTH: usize = 80;
/// Reference window height: title + status + border + 20 field rows + border.
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
// Sides, states, causes, modes
// ---------------------------------------------------------------------------

/// A side of the field. `Left` is the human player in 1P.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Left,
    Right,
}

pub use Side::*;

impl Side {
    /// The other side.
    pub fn other(self) -> Side {
        match self {
            Left => Right,
            Right => Left,
        }
    }
    /// The column this side's paddle sits on.
    pub fn col(self) -> usize {
        match self {
            Left => LEFT_COL,
            Right => RIGHT_COL,
        }
    }
    /// The fixed-point x of this side's paddle *face* — the side the ball hits.
    pub fn face(self) -> i64 {
        match self {
            Left => (LEFT_COL as i64 + 1) * FP_SCALE,
            Right => RIGHT_COL as i64 * FP_SCALE,
        }
    }
    /// The fixed-point x past which this side has conceded the point.
    pub fn goal_line(self) -> i64 {
        match self {
            Left => 0,
            Right => (COLS as i64 - 1) * FP_SCALE,
        }
    }
    /// The name the frame and the plain-text state print.
    pub fn name(self) -> &'static str {
        match self {
            Left => "left",
            Right => "right",
        }
    }
}

/// The game's states. A finished match is frozen: ticking changes nothing.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    /// The 1-second countdown before a serve.
    Serve,
    /// The ball is in play.
    Play,
    /// Frozen by `p`.
    Pause,
    /// The match is over.
    Over,
}

/// Why the match ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cause {
    /// First to eleven, with the win-by-2 margin satisfied.
    Won,
    /// The hard 15-point cap ended the match.
    Cap,
}

impl Cause {
    /// The one-line text the end screen shows.
    pub fn text(self) -> &'static str {
        match self {
            Cause::Won => "first to eleven, win by two",
            Cause::Cap => "the 15-point cap",
        }
    }
}

/// 1P is human-left versus the penguin; 2P is hotseat.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    OneP,
    TwoP,
}

impl Mode {
    /// The short name the frame and the plain-text state print.
    pub fn name(self) -> &'static str {
        match self {
            Mode::OneP => "1P",
            Mode::TwoP => "2P",
        }
    }
}

// ---------------------------------------------------------------------------
// The pure geometry helpers the rules (and the tests) run on
// ---------------------------------------------------------------------------

/// The row range a paddle covers: `top ..= top + PADDLE_H - 1`.
pub fn paddle_rows(top: usize) -> (usize, usize) {
    (top, top + PADDLE_H - 1)
}

/// Clamp a paddle top row into the field.
pub fn clamp_top(top: usize) -> usize {
    top.min(ROWS - PADDLE_H)
}

/// Move a paddle by `delta` rows (positive = down), clamped to the field.
pub fn move_top(top: usize, delta: i32) -> usize {
    let t = (top as i32 + delta).max(0);
    clamp_top(t as usize)
}

/// Does the paddle at `top` cover `row`?
pub fn covers(top: usize, row: usize) -> bool {
    let (a, b) = paddle_rows(top);
    row >= a && row <= b
}

/// The ball's cell row from its fixed-point y.
pub fn ball_row(y: i64) -> usize {
    (y / FP_SCALE).clamp(0, ROWS as i64 - 1) as usize
}

/// The ball's cell column from its fixed-point x.
pub fn ball_col(x: i64) -> usize {
    (x / FP_SCALE).clamp(0, COLS as i64 - 1) as usize
}

/// The fixed-point row a paddle's centre sits at.
pub fn paddle_centre(top: usize) -> i64 {
    (top as i64 * FP_SCALE) + (PADDLE_H as i64 * FP_SCALE) / 2
}

/// Where the ball will meet the paddle on `side` if nothing changes: its row
/// now, walked forward by the ticks it takes to reach the paddle face, with the
/// top/bottom walls reflected. Pure, so the tests can pin it.
pub fn predict_row(x: i64, y: i64, vx: i64, vy: i64, side: Side) -> i32 {
    if vx == 0 {
        return ball_row(y) as i32;
    }
    let face = side.face();
    let dist = match side {
        Left => x - face,
        Right => face - x,
    };
    let ticks = if dist > 0 { dist / vx.abs() } else { 0 };
    let span = (ROWS as i64 - 1) * FP_SCALE;
    let mut yy = y + vy * ticks;
    if span > 0 {
        let period = 2 * span;
        let mut m = yy.rem_euclid(period);
        if m > span {
            m = period - m;
        }
        yy = m;
    }
    (((yy + FP_SCALE / 2) / FP_SCALE) as usize).min(ROWS - 1) as i32
}

/// The tracking rule every paddle-steering bot uses: aim the paddle's centre at
/// the row it wants, and move at most `cap` rows this tick.
///
/// The dead-band is the paddle's two inner rows (measured in half-rows, so it
/// is exact for a 4-row paddle): a bot that has the ball on an inner row stops,
/// and starts again the tick the ball leaves it. The returned delta is always
/// clamped to `cap`, so a bot can never move faster than its level allows.
pub fn track_step(top: usize, want_row: i32, cap: i32) -> Option<i32> {
    // Twice the row minus twice the paddle centre: an integer, no rounding.
    let want2 = 2 * want_row - (2 * top as i32 + (PADDLE_H as i32 - 1));
    if want2.abs() <= 1 {
        return None;
    }
    let mut step = want2 / 2;
    if step == 0 {
        step = if want2 > 0 { 1 } else { -1 };
    }
    Some(step.clamp(-cap, cap))
}

// ---------------------------------------------------------------------------
// The game
// ---------------------------------------------------------------------------

/// The whole match state. Every rule is a transition on this struct.
#[derive(Clone, Debug)]
pub struct Game {
    /// The seed this run was built from.
    pub seed: u64,
    /// The seeded generator (serve angle jitter only).
    pub rng: Rng,
    /// 1P or 2P.
    pub mode: Mode,
    /// The penguin's difficulty, 1..=3.
    pub difficulty: u8,
    /// The state machine.
    pub state: State,
    /// Why the match ended, once it has.
    pub cause: Option<Cause>,
    /// Ticks elapsed in this match.
    pub ticks: u64,
    /// Ticks left before the serve fires.
    pub countdown: u64,
    /// The side the next serve travels toward (the player who lost the point).
    pub serve_to: Side,
    /// Ball position, fixed-point cells.
    pub ball_x: i64,
    /// Ball position, fixed-point cells.
    pub ball_y: i64,
    /// Ball velocity, fixed-point cells per tick.
    pub vx: i64,
    /// Ball velocity, fixed-point cells per tick.
    pub vy: i64,
    /// Current |vx| — the speed the 5% ladder walks up to [`MAX_SPEED`].
    pub speed: i64,
    /// Top row of the left paddle.
    pub left_top: usize,
    /// Top row of the right paddle.
    pub right_top: usize,
    /// Points taken by the left player.
    pub left_score: u64,
    /// Points taken by the right player.
    pub right_score: u64,
    /// Paddle hits since the last serve (the speed ladder).
    pub hits: u64,
    /// Total paddle hits in the match.
    pub total_hits: u64,
    /// Points played.
    pub points: u64,
    /// Ticks the current rally has lasted.
    pub rally: u64,
    /// Longest rally in the match.
    pub longest_rally: u64,
    /// Bounces off the top/bottom walls in the current rally.
    pub wall_bounces: u64,
    /// Rows the left paddle moved this tick (spin, see [`SPIN_PER_ROW`]).
    pub left_delta: i32,
    /// Rows the right paddle moved this tick.
    pub right_delta: i32,
    /// The side that hit the ball last in this rally (the rally cap awards the
    /// point to it).
    pub last_hitter: Option<Side>,
    /// The penguin's reaction-delay history of ball states (x, y, vx, vy, all
    /// fixed-point), and the ring position the next sample lands at.
    pub ai_view: [(i64, i64, i64, i64); AI_VIEW_LEN],
    /// Ring position for [`Game::ai_view`].
    pub ai_head: usize,
    /// Matches the player has banked as won (persisted best).
    pub best_wins: u64,
    /// Best winning margin banked.
    pub best_margin: i64,
    /// Longest rally ever banked.
    pub best_rally: u64,
}

impl Game {
    /// A fresh match: paddles centred, 1P at difficulty 2, first serve toward
    /// the left player.
    pub fn new(seed: u64) -> Self {
        Self::with(seed, Mode::OneP, 2)
    }

    /// A fresh match with the mode and the penguin's difficulty pinned.
    pub fn with(seed: u64, mode: Mode, difficulty: u8) -> Self {
        let d = difficulty.clamp(1, MAX_DIFFICULTY);
        let top = (ROWS - PADDLE_H) / 2;
        let ball_y = ((ROWS - 1) as i64 * FP_SCALE) / 2;
        let ball_x = (COLS / 2) as i64 * FP_SCALE;
        let g = Game {
            seed,
            rng: Rng::new(seed),
            mode,
            difficulty: d,
            state: State::Serve,
            cause: None,
            ticks: 0,
            countdown: SERVE_COUNTDOWN,
            serve_to: Left,
            ball_x,
            ball_y,
            vx: 0,
            vy: 0,
            speed: SERVE_SPEED,
            left_top: top,
            right_top: top,
            left_score: 0,
            right_score: 0,
            hits: 0,
            total_hits: 0,
            points: 0,
            rally: 0,
            longest_rally: 0,
            wall_bounces: 0,
            left_delta: 0,
            right_delta: 0,
            last_hitter: None,
            ai_view: [(ball_x, ball_y, 0, 0); AI_VIEW_LEN],
            ai_head: 0,
            best_wins: 0,
            best_margin: 0,
            best_rally: 0,
        };
        g
    }

    /// Start the match over: same seed family (`seed + 1`), banked best kept.
    pub fn restart(&mut self) {
        let (mode, d, wins, margin, rally) =
            (self.mode, self.difficulty, self.best_wins, self.best_margin, self.best_rally);
        let seed = self.seed.wrapping_add(1);
        let mut g = Game::with(seed, mode, d);
        g.best_wins = wins;
        g.best_margin = margin;
        g.best_rally = rally;
        *self = g;
    }

    /// The ball's current cell row.
    pub fn ball_row(&self) -> usize {
        ball_row(self.ball_y)
    }

    /// The ball's current cell column.
    pub fn ball_col(&self) -> usize {
        ball_col(self.ball_x)
    }

    /// The top row of the paddle on this side.
    pub fn top(&self, side: Side) -> usize {
        match side {
            Left => self.left_top,
            Right => self.right_top,
        }
    }

    /// The score of a side.
    pub fn score(&self, side: Side) -> u64 {
        match side {
            Left => self.left_score,
            Right => self.right_score,
        }
    }

    /// Move the paddle on this side by `delta` rows, clamped to the field. The
    /// rows actually moved are recorded as this tick's spin.
    pub fn set_top(&mut self, side: Side, delta: i32) {
        match side {
            Left => {
                let t = move_top(self.left_top, delta);
                self.left_delta += t as i32 - self.left_top as i32;
                self.left_top = t;
            }
            Right => {
                let t = move_top(self.right_top, delta);
                self.right_delta += t as i32 - self.right_top as i32;
                self.right_top = t;
            }
        }
    }

    /// Toggle 1P / 2P. In 2P the penguin is replaced by a second human.
    pub fn toggle_mode(&mut self) {
        self.mode = match self.mode {
            Mode::OneP => Mode::TwoP,
            Mode::TwoP => Mode::OneP,
        };
    }

    /// Set the penguin's difficulty (1, 2 or 3). Choosing a difficulty is a
    /// statement that you are playing the penguin, so it also picks 1P.
    pub fn set_difficulty(&mut self, d: u8) {
        self.difficulty = d.clamp(1, MAX_DIFFICULTY);
        self.mode = Mode::OneP;
    }

    /// Pause / resume. A finished match cannot be paused.
    pub fn toggle_pause(&mut self) {
        self.state = match self.state {
            State::Play => State::Pause,
            State::Pause => State::Play,
            s => s,
        };
    }

    /// Fire the serve now: the countdown ends and the ball is launched.
    pub fn serve_now(&mut self) {
        if self.state == State::Serve {
            self.countdown = 0;
            self.launch();
        }
    }

    /// Launch the ball: toward the player who lost the last point, at serve
    /// speed, with a seeded shallow angle so serves are not all identical.
    fn launch(&mut self) {
        self.speed = SERVE_SPEED;
        self.vx = match self.serve_to {
            Left => -SERVE_SPEED,
            Right => SERVE_SPEED,
        };
        // A shallow serve: +/-10 degrees, sign seeded. Never straight down the
        // middle, never steep enough to reach a wall before the first paddle.
        let sign = if self.rng.below(2) == 0 { -1i64 } else { 1i64 };
        self.vy = sign * vy_for(BOUNCES[2], SERVE_SPEED);
        self.ball_x = (COLS / 2) as i64 * FP_SCALE;
        self.ball_y = ((ROWS - 1) as i64 * FP_SCALE) / 2;
        self.hits = 0;
        self.rally = 0;
        self.wall_bounces = 0;
        self.state = State::Play;
    }

    /// The penguin's move for this tick: `Some(delta)` rows, `None` = stay put.
    ///
    /// The penguin only sees the ball as it was [`AI_DELAYS`] ticks ago (its
    /// reaction delay) and may move at most [`AI_CAPS`] rows this tick. Both
    /// limits are hard: the delta is clamped to the cap and the paddle top is
    /// clamped to the field.
    pub fn ai_move(&mut self) -> Option<i32> {
        if self.mode != Mode::OneP {
            return None;
        }
        let d = self.difficulty as usize - 1;
        let cap = AI_CAPS[d] as i32;
        let delay = AI_DELAYS[d] as usize;
        // The delayed view: the sample from `delay` ticks ago. The penguin
        // chases the ball it can see — the row that sample is on. Its cap is
        // what makes the level: a steep, fast ball outruns a low cap, and a
        // long delay puts it behind every bounce.
        let (x, y, vx, vy) = self.ai_view[(self.ai_head + AI_VIEW_LEN - delay) % AI_VIEW_LEN];
        let _ = (x, vx);
        let want = ball_row(y + vy * (delay as i64)) as i32;
        // Aim: put the paddle so the ball meets it on this level's row.
        track_step(self.right_top, want - AI_AIM[d] as i32, cap)
    }

    /// Advance the match by one tick. A paused or finished match does nothing
    /// except count the tick.
    pub fn tick(&mut self) {
        if self.state == State::Over {
            // A finished match is frozen: ticking changes nothing at all.
            return;
        }
        self.ticks += 1;
        match self.state {
            State::Serve => {
                self.countdown = self.countdown.saturating_sub(1);
                if self.countdown == 0 {
                    self.launch();
                }
            }
            State::Play => {
                self.rally += 1;
                if self.rally > self.longest_rally {
                    self.longest_rally = self.rally;
                }
                self.step_ball();
                // The rally cap: a stall is a point for the last hitter.
                if self.state == State::Play && self.rally > MAX_RALLY_TICKS {
                    let winner = self.last_hitter.unwrap_or(self.serve_to.other());
                    self.point(winner);
                }
            }
            State::Pause | State::Over => {}
        }
        // Spin is a per-tick quantity: the paddle motion that counted for this
        // tick's bounce is forgotten next tick.
        self.left_delta = 0;
        self.right_delta = 0;
    }

    /// One tick of ball motion: the penguin moves, the ball moves, walls,
    /// paddle faces, goal lines.
    fn step_ball(&mut self) {
        // The reaction-delay history is sampled every tick of play, before
        // anybody moves: the penguin reads it `AI_DELAYS` ticks back, the
        // autopilot reads it `AUTOPILOT_DELAY` ticks back.
        self.ai_view[self.ai_head] = (self.ball_x, self.ball_y, self.vx, self.vy);
        self.ai_head = (self.ai_head + 1) % AI_VIEW_LEN;
        // The penguin moves before the ball is resolved, so its reaction to a
        // bounce is measured from the tick the bounce happened.
        if let Some(delta) = self.ai_move() {
            self.set_top(Right, delta);
        }

        let prev_x = self.ball_x;
        self.ball_x += self.vx;
        self.ball_y += self.vy;

        // Top / bottom walls: reflect vy and clamp back inside the field.
        let top_wall = 0i64;
        let bottom_wall = (ROWS as i64 - 1) * FP_SCALE;
        if self.ball_y <= top_wall && self.vy < 0 {
            self.ball_y = top_wall + (top_wall - self.ball_y);
            self.vy = -self.vy;
            self.wall_bounces += 1;
        } else if self.ball_y >= bottom_wall && self.vy > 0 {
            self.ball_y = bottom_wall - (self.ball_y - bottom_wall);
            self.vy = -self.vy;
            self.wall_bounces += 1;
        }

        // Paddle faces. A hit is a *crossing* of the face, so a fast ball can
        // never tunnel through a paddle.
        let row = self.ball_row();
        if self.vx < 0 && prev_x > Left.face() && self.ball_x <= Left.face() {
            if covers(self.left_top, row) {
                self.bounce(Left);
            }
        } else if self.vx > 0 && prev_x < Right.face() && self.ball_x >= Right.face() {
            if covers(self.right_top, row) {
                self.bounce(Right);
            }
        }

        // Goal lines: the ball went past a paddle.
        if self.vx < 0 && self.ball_x <= Left.goal_line() {
            self.point(Right);
        } else if self.vx > 0 && self.ball_x >= Right.goal_line() {
            self.point(Left);
        }
    }

    /// A paddle bounce: flip x, walk the speed ladder, take vy from the angle
    /// table, and pin the ball to the face it just hit.
    fn bounce(&mut self, side: Side) {
        let b = bounce_for(self.ball_row(), self.top(side));
        self.speed = next_speed(self.speed);
        self.vx = match side {
            Left => self.speed,
            Right => -self.speed,
        };
        // The table angle, plus the spin the paddle's own motion adds, clamped
        // to the steepest angle the table allows (50 degrees at this speed).
        let spin = match side {
            Left => self.left_delta,
            Right => self.right_delta,
        } as i64
            * SPIN_PER_ROW;
        let steep = vy_for(BOUNCES[3], self.speed).abs();
        let raw = vy_for(b, self.speed) + spin;
        self.vy = raw.clamp(-steep, steep);
        self.ball_x = side.face();
        self.hits += 1;
        self.total_hits += 1;
        self.last_hitter = Some(side);
    }

    /// A point: score it, check the match, and set up the next serve.
    fn point(&mut self, winner: Side) {
        match winner {
            Left => self.left_score += 1,
            Right => self.right_score += 1,
        }
        self.points += 1;
        self.serve_to = winner.other();
        self.vx = 0;
        self.vy = 0;
        self.ball_x = (COLS / 2) as i64 * FP_SCALE;
        self.ball_y = ((ROWS - 1) as i64 * FP_SCALE) / 2;
        self.speed = SERVE_SPEED;
        self.hits = 0;
        self.wall_bounces = 0;
        self.last_hitter = None;
        if let Some(c) = self.match_over() {
            self.state = State::Over;
            self.cause = Some(c);
        } else {
            self.state = State::Serve;
            self.countdown = SERVE_COUNTDOWN;
        }
    }

    /// First to [`WIN_SCORE`] with a two-point margin, or the [`CAP_SCORE`] cap.
    pub fn match_over(&self) -> Option<Cause> {
        let (l, r) = (self.left_score, self.right_score);
        let top = l.max(r);
        let diff = l.abs_diff(r);
        if top >= CAP_SCORE {
            Some(Cause::Cap)
        } else if top >= WIN_SCORE && diff >= 2 {
            Some(Cause::Won)
        } else {
            None
        }
    }

    /// The side that won the match, once it is over.
    pub fn winner(&self) -> Option<Side> {
        if self.state != State::Over {
            return None;
        }
        match self.left_score.cmp(&self.right_score) {
            std::cmp::Ordering::Greater => Some(Left),
            std::cmp::Ordering::Less => Some(Right),
            std::cmp::Ordering::Equal => None,
        }
    }

    /// The margin the left player won (or lost) the match by.
    pub fn margin(&self) -> i64 {
        self.left_score as i64 - self.right_score as i64
    }

    // -- keys ---------------------------------------------------------------

    /// The paddle a key steers in the current mode, and how far it moves:
    /// one row per keypress. `None` = the key is not a paddle key.
    ///
    /// `w`/`s` are the left player's keys in both modes. `k`/`j` (what the
    /// front-end delivers for the arrow keys) steer the right paddle in 2P and
    /// the player's own paddle in 1P.
    pub fn paddle_key(&self, c: char) -> Option<(Side, i32)> {
        let dir = match c {
            'w' | 'W' => Some(-1),
            's' | 'S' => Some(1),
            'k' | 'K' => Some(-1),
            'j' | 'J' => Some(1),
            _ => None,
        }?;
        let side = match c {
            'w' | 'W' | 's' | 'S' => Left,
            // Arrows: the right-hand paddle in hotseat, the player's own in 1P.
            _ => match self.mode {
                Mode::OneP => Left,
                Mode::TwoP => Right,
            },
        };
        Some((side, dir))
    }

    /// Apply one key. Returns `true` when the front-end must quit.
    ///
    /// The front-end hands this the *letter* of an escape sequence: the arrow
    /// keys arrive as `ESC [ A/B/C/D` and are mapped to `k`/`j` (see
    /// [`Game::paddle_key`]).
    pub fn key(&mut self, c: char) -> bool {
        match c {
            'q' | 'Q' => return true,
            ' ' => self.serve_now(),
            'p' | 'P' => self.toggle_pause(),
            'r' | 'R' => self.restart(),
            'm' | 'M' => self.toggle_mode(),
            '1' => self.set_difficulty(1),
            '2' => self.set_difficulty(2),
            '3' => self.set_difficulty(3),
            _ => {}
        }
        if let Some((side, dir)) = self.paddle_key(c) {
            if self.state != State::Over {
                self.set_top(side, dir);
            }
        }
        false
    }

    /// Where the ball will meet the left paddle if nothing changes: the row it
    /// is on now, walked forward by the ticks it takes to reach the paddle face,
    /// with the top/bottom walls reflected. The intercept the autopilot aims at.
    pub fn intercept_row(&self, side: Side) -> i32 {
        predict_row(self.ball_x, self.ball_y, self.vx, self.vy, side)
    }

    /// The autopilot's keys for the left paddle this tick: one character per
    /// keypress, so an empty string is "no input".
    ///
    /// It is the difficulty-3 penguin aimed at the left paddle with no reaction
    /// delay, playing the intercept: at most 3 rows per tick, and it never
    /// exceeds that cap. This is what drives `--headless`, `--autopilot` and
    /// `--record`, and what the committed replay fixture records.
    pub fn autopilot_keys(&self) -> String {
        let cap = AUTOPILOT_CAP as i32;
        // A strong human, not a god: the intercept is computed from the ball
        // as it was `AUTOPILOT_DELAY` ticks ago, so a steep shot off a paddle
        // edge at full speed can beat it.
        let (x, y, vx, vy) =
            self.ai_view[(self.ai_head + AI_VIEW_LEN - AUTOPILOT_DELAY) % AI_VIEW_LEN];
        let want = if vx == 0 {
            self.ball_row() as i32
        } else {
            predict_row(x, y, vx, vy, Left)
        };
        let step = match track_step(self.left_top, want - AUTOPILOT_AIM as i32, cap) {
            None => 0,
            Some(s) => s,
        };
        if step == 0 {
            return String::new();
        }
        let ch = if step < 0 { 'w' } else { 's' };
        ch.to_string().repeat(step.unsigned_abs() as usize)
    }

    // -- determinism surface ------------------------------------------------

    /// Canonical serialization of everything that defines the run. Two games
    /// with the same string have the same [`Game::state_hash`].
    pub fn canonical(&self) -> String {
        let mut s = String::with_capacity(256);
        let _ = write!(
            s,
            "seed={seed};rng={rng:x};mode={mode:?};diff={diff};state={state:?};cause={cause:?};\
             ticks={ticks};countdown={cd};serve_to={serve:?};\
             bx={bx};by={by};vx={vx};vy={vy};speed={speed};\
             ltop={ltop};rtop={rtop};ls={ls};rs={rs};hits={hits};thits={thits};\
             points={points};rally={rally};longest={longest};walls={walls};             ldelta={ldelta};rdelta={rdelta};hitter={hitter:?};view=[",
            seed = self.seed,
            rng = self.rng.state(),
            mode = self.mode,
            diff = self.difficulty,
            state = self.state,
            cause = self.cause,
            ticks = self.ticks,
            cd = self.countdown,
            serve = self.serve_to,
            bx = self.ball_x,
            by = self.ball_y,
            vx = self.vx,
            vy = self.vy,
            speed = self.speed,
            ltop = self.left_top,
            rtop = self.right_top,
            ls = self.left_score,
            rs = self.right_score,
            hits = self.hits,
            thits = self.total_hits,
            points = self.points,
            rally = self.rally,
            longest = self.longest_rally,
            walls = self.wall_bounces,
            ldelta = self.left_delta,
            rdelta = self.right_delta,
            hitter = self.last_hitter,
        );
        for &(x, y, vx, vy) in &self.ai_view {
            let _ = write!(s, "({x},{y},{vx},{vy}),");
        }
        let _ = write!(
            s,
            "];head={head};best=[{w},{m},{r}]",
            head = self.ai_head,
            w = self.best_wins,
            m = self.best_margin,
            r = self.best_rally
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

    /// The match as `key=value` pairs — what `--replay` prints on exit 0, and
    /// what the acceptance harness greps.
    pub fn result_kv(&self) -> Vec<(String, String)> {
        let mut kv = vec![
            ("state".into(), format!("{:?}", self.state).to_lowercase()),
            ("seed".into(), self.seed.to_string()),
            ("mode".into(), self.mode.name().to_string()),
            ("difficulty".into(), self.difficulty.to_string()),
            ("ticks".into(), self.ticks.to_string()),
            ("points".into(), self.points.to_string()),
            ("left".into(), self.left_score.to_string()),
            ("right".into(), self.right_score.to_string()),
            ("hits".into(), self.total_hits.to_string()),
            ("speed".into(), self.speed.to_string()),
            ("rally".into(), self.rally.to_string()),
            ("longest_rally".into(), self.longest_rally.to_string()),
            ("best_wins".into(), self.best_wins.to_string()),
            ("best_margin".into(), self.best_margin.to_string()),
            ("best_rally".into(), self.best_rally.to_string()),
            ("hash".into(), format!("{:016x}", self.state_hash())),
        ];
        if let Some(c) = self.cause {
            kv.push(("cause".into(), format!("{:?}", c).to_lowercase()));
        }
        if let Some(w) = self.winner() {
            kv.push(("winner".into(), w.name().to_string()));
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
    let _ = writeln!(s, "field:");
    for r in 0..ROWS {
        let mut line = String::new();
        for c in 0..COLS {
            let ch = if (c == LEFT_COL && covers(g.left_top, r))
                || (c == RIGHT_COL && covers(g.right_top, r))
            {
                '#'
            } else if c == g.ball_col() && r == g.ball_row() {
                'O'
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
    fn net(&self) -> char {
        if self.mono {
            ':'
        } else {
            '┆'
        }
    }
}

/// One field row: the net, the paddles, the ball. Exactly `COLS` columns.
fn field_row(g: &Game, p: &Painter, row: usize) -> String {
    let mut cells: Vec<char> = vec![' '; COLS];
    cells[COLS / 2] = p.net();
    if covers(g.left_top, row) {
        cells[LEFT_COL] = p.paddle();
    }
    if covers(g.right_top, row) {
        cells[RIGHT_COL] = p.paddle();
    }
    if g.ball_row() == row {
        let c = g.ball_col();
        if c < COLS {
            cells[c] = p.ball();
        }
    }
    cells.into_iter().collect()
}

impl Game {
    /// The title + scoreboard line.
    fn title_line(&self, p: &Painter) -> String {
        let text = format!(
            "PENGUIN PONG  you {ls:>2}  {opp:<2} {rs:>2}   {mode} d{diff} seed {seed}   best {wins} win{plural} by {margin:+}",
            ls = self.left_score,
            rs = self.right_score,
            opp = if self.mode == Mode::OneP { "penguin" } else { "p2" },
            mode = self.mode.name(),
            diff = self.difficulty,
            seed = self.seed,
            wins = self.best_wins,
            plural = if self.best_wins == 1 { "" } else { "s" },
            margin = self.best_margin,
        );
        if p.color {
            format!("\x1b[1;36m{text}\x1b[0m")
        } else {
            text
        }
    }

    /// The status line: what the player has to do right now.
    fn status_line(&self, p: &Painter) -> String {
        let text = match self.state {
            State::Serve => format!(
                "SERVE IN {:>2}   space serves now   serve travels toward {}",
                self.countdown,
                self.serve_to.name()
            ),
            State::Play => format!(
                "speed {:>3}/{MAX_SPEED}   hits {}   walls {}   rally {}   longest {}",
                self.speed,
                self.hits,
                self.wall_bounces,
                self.rally,
                self.longest_rally
            ),
            State::Pause => "PAUSED   p resumes   r restarts   q quits".to_string(),
            State::Over => {
                let w = self
                    .winner()
                    .map(|s| s.name().to_string())
                    .unwrap_or_else(|| "nobody".into());
                format!(
                    "MATCH OVER   {w} wins {}-{}   ({})",
                    self.left_score,
                    self.right_score,
                    self.cause.map(Cause::text).unwrap_or("")
                )
            }
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
    /// Degrade rules (spec.md "Rendering"): the title line needs [`TITLE_W`]
    /// columns and the full [`REF_HEIGHT`] rows, the border (which carries the
    /// key legend) needs [`BORDER_W`] columns and `ROWS + 2` rows, the status
    /// line needs `ROWS + 3` rows; below that the title goes, then the status,
    /// then the border, and a short window clips the field from the bottom. The
    /// 60-column field itself is never dropped.
    pub fn render(&self, opts: RenderOpts) -> String {
        let p = Painter {
            mono: opts.mono,
            color: opts.color && !opts.mono,
        };
        let title = opts.width >= TITLE_W && opts.height >= REF_HEIGHT;
        let border = opts.width >= BORDER_W && opts.height >= ROWS + 2;
        let status = opts.height >= ROWS + 3;

        let mut lines: Vec<String> = Vec::new();
        if title {
            lines.push(self.title_line(&p));
        }
        if status {
            lines.push(self.status_line(&p));
        }
        if border {
            lines.push(self.top_border(&p));
        }
        for r in 0..ROWS {
            let row = field_row(self, &p, r);
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
