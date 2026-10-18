//! pengy-floe — *Flappy Pengy*, the PengyOS pack-in flappy-bird game.
//!
//! **This file is the whole game, and it is pure.** No I/O, no clock, no
//! randomness beyond a seeded generator, no threads, no terminal, no files.
//! Every rule, every state transition and the renderer live here so that:
//!
//! * `cargo test` exercises the real game (the tests drive exactly the code
//!   the player drives), and
//! * `--headless` / `--replay` can prove the game works with no pty, which is
//!   how the repo gate and `pengyos-qemu-verify` can run it at all.
//!
//! Determinism contract: a run is a function of `(seed, script)`. The RNG is a
//! hand-written xorshift64* (see [`Rng`]), time advances in fixed 80 ms ticks
//! that the *caller* supplies, and nothing else touches the outside world.
//! Same seed + same input script ⇒ bit-for-bit identical state.
//!
//! The front-end (`src/main.rs`) owns the terminal, the clock and the files;
//! it only ever calls the public API below.

#![forbid(unsafe_code)]

// ---------------------------------------------------------------------------
// Playfield and physics constants — the contract, in one place.
// ---------------------------------------------------------------------------

/// Playfield width in columns.
pub const COLS: usize = 40;
/// Playfield height in rows.
pub const ROWS: usize = 20;
/// Fixed timestep: one tick is exactly 80 ms of game time.
pub const TICK_MS: u64 = 80;
/// Left column of the penguin's collision box (2 columns wide, 1 row tall).
pub const PENGUIN_COL: usize = 8;
/// Width of the penguin's collision box.
pub const PENGUIN_W: usize = 2;
/// Width of the drawn penguin sprite. It is wider than the hitbox by one
/// column on each side, which is the "slightly forgiving" hitbox: the sprite
/// may overlap a pillar by a column and the player survives.
pub const PENGUIN_SPRITE_W: usize = 4;
/// Rows added to `vy` every tick.
pub const GRAVITY: i32 = 1;
/// Terminal velocity (rows per tick, downward).
pub const VY_MAX: i32 = 3;
/// Velocity a flap sets. It never stacks: flapping twice in a tick is one flap.
pub const FLAP_VY: i32 = -2;
/// Pillar width in columns.
pub const PILLAR_W: usize = 2;
/// Columns between consecutive pillar spawn points.
pub const SPACING_COLS: i32 = 12;
/// Gap height at score 0.
pub const GAP_MAX: i32 = 8;
/// Gap height from [`GAP_SHRINK_SCORE`] on.
pub const GAP_MIN: i32 = 5;
/// Score at which the gap has shrunk to [`GAP_MIN`].
pub const GAP_SHRINK_SCORE: i32 = 25;
/// Score at which the world reaches 1 column per tick.
pub const SPEED_FULL_SCORE: i32 = 30;
/// Minimum ice-free rows the generator must leave above and below the gap.
pub const CLEARANCE: i32 = 2;
/// Row the penguin dies against (touching it is death).
pub const CEILING_ROW: i32 = 0;
/// Row the penguin dies against (touching it is death).
pub const FLOOR_ROW: i32 = ROWS as i32 - 1;
/// Speed is carried in 1/60 of a column per tick.
pub const SPEED_UNIT: i32 = 60;
/// Row the penguin starts on.
pub const START_ROW: i32 = ROWS as i32 / 2;

/// Score for the entry-level ice medal.
pub const MEDAL_ICE: i32 = 10;
/// Score for the bronze medal.
pub const MEDAL_BRONZE: i32 = 25;
/// Score for the silver medal.
pub const MEDAL_SILVER: i32 = 50;
/// Score for the gold medal.
pub const MEDAL_GOLD: i32 = 100;

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

    /// Raw generator state (part of the game state, so hashes cover it).
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

    /// Next value in `0..n`. `n` must be > 0 (asserted: an impossible gap
    /// would start here, so this is a hard bug, not a graceful case).
    pub fn below(&mut self, n: u64) -> u64 {
        assert!(n > 0, "Rng::below(0) — no legal gap position");
        self.next_u64() % n
    }
}

// ---------------------------------------------------------------------------
// Game state
// ---------------------------------------------------------------------------

/// The three states of a session.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    /// Ticks advance; keys are live.
    Playing,
    /// Ticks are frozen; the frame shows a PAUSED banner.
    Paused,
    /// Frozen after a crash; the frame shows score, best and medal.
    Dead,
}

/// Why a run ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cause {
    /// The penguin touched the ceiling row.
    Ceiling,
    /// The penguin touched the floor row.
    Floor,
    /// The penguin's hitbox overlapped a pillar cell.
    Pillar,
}

impl Cause {
    /// Human-readable cause, for the death banner.
    pub fn label(self) -> &'static str {
        match self {
            Cause::Ceiling => "clipped the ceiling",
            Cause::Floor => "hit the floor",
            Cause::Pillar => "hit an ice pillar",
        }
    }
}

/// One ice pillar: its left column and the gap rows it carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Pillar {
    /// Left column; the pillar occupies `x..=x + PILLAR_W - 1`.
    pub x: i32,
    /// First gap row (inclusive).
    pub gap_top: i32,
    /// Last gap row (inclusive).
    pub gap_bottom: i32,
    /// Set once the trailing edge has passed the penguin and the point is banked.
    pub scored: bool,
}

impl Pillar {
    /// Right column of the pillar.
    pub fn right(&self) -> i32 {
        self.x + PILLAR_W as i32 - 1
    }
    /// Gap height in rows.
    pub fn gap_height(&self) -> i32 {
        self.gap_bottom - self.gap_top + 1
    }
    /// True when `row` is inside the gap (the hole you fly through).
    pub fn in_gap(&self, row: i32) -> bool {
        row >= self.gap_top && row <= self.gap_bottom
    }
}

/// A Flappy Pengy session. Pure state; see the module docs.
#[derive(Clone, Debug, PartialEq)]
pub struct Game {
    /// The seed this run started from.
    pub seed: u64,
    /// Generator state (part of the state, so a hash covers it).
    pub rng: Rng,
    /// Playing / Paused / Dead.
    pub state: State,
    /// Ticks elapsed in this run.
    pub ticks: u64,
    /// Pillars passed.
    pub score: i32,
    /// Best score ever seen by this game object. The front-end seeds it from
    /// the persisted high score and persists it again on death.
    pub best: i32,
    /// Penguin row.
    pub y: i32,
    /// Penguin vertical velocity (rows per tick).
    pub vy: i32,
    /// Live pillars, in spawn order.
    pub pillars: Vec<Pillar>,
    /// Columns of world travel until the next pillar spawns.
    pub next_spawn: i32,
    /// Fractional column accumulator (units of 1/60 column per tick).
    pub sub: i32,
    /// Set when the run ended.
    pub cause: Option<Cause>,
    /// Flaps performed (diagnostics, autopilot tuning).
    pub flaps: u32,
}

impl Game {
    /// A fresh run at `seed`, with the first pillar entering from the right.
    pub fn new(seed: u64) -> Self {
        let mut g = Game {
            seed,
            rng: Rng::new(seed),
            state: State::Playing,
            ticks: 0,
            score: 0,
            best: 0,
            y: START_ROW,
            vy: 0,
            pillars: Vec::new(),
            next_spawn: SPACING_COLS,
            sub: 0,
            cause: None,
            flaps: 0,
        };
        let p = g.make_pillar();
        g.pillars.push(p);
        g
    }

    /// Gap height for a score: 8 rows, shrinking linearly to 5 at
    /// [`GAP_SHRINK_SCORE`], never below 5, never above 8.
    pub fn gap_height_for(score: i32) -> i32 {
        let s = score.clamp(0, GAP_SHRINK_SCORE);
        let h = GAP_MAX - (GAP_MAX - GAP_MIN) * s / GAP_SHRINK_SCORE;
        h.clamp(GAP_MIN, GAP_MAX)
    }

    /// Carrying speed in 1/60 columns per tick: 30 (1 column per 2 ticks) at
    /// score 0, ramping linearly to 60 (1 column per tick) at
    /// [`SPEED_FULL_SCORE`], then flat.
    pub fn speed_rate_for(score: i32) -> i32 {
        let s = score.clamp(0, SPEED_FULL_SCORE);
        SPEED_UNIT / 2 + (SPEED_UNIT / 2) * s / SPEED_FULL_SCORE
    }

    /// Build the next pillar at the spawn column with a legal gap:
    /// `gap_top` is drawn from `CLEARANCE ..= ROWS - CLEARANCE - h`, so the
    /// gap is always fully inside the playfield with at least [`CLEARANCE`]
    /// rows of ice-free space above and below it.
    fn make_pillar(&mut self) -> Pillar {
        let h = Self::gap_height_for(self.score);
        let choices = ROWS as i32 - 2 * CLEARANCE + 1 - h;
        let gap_top = CLEARANCE + self.rng.below(choices as u64) as i32;
        Pillar {
            x: COLS as i32,
            gap_top,
            gap_bottom: gap_top + h - 1,
            scored: false,
        }
    }

    /// Flap: sets `vy` to [`FLAP_VY`]. It never stacks, and it is ignored
    /// while paused or dead.
    pub fn flap(&mut self) {
        if self.state == State::Playing {
            self.vy = FLAP_VY;
            self.flaps += 1;
        }
    }

    /// Toggle pause. A dead run never unpauses.
    pub fn toggle_pause(&mut self) {
        self.state = match self.state {
            State::Playing => State::Paused,
            State::Paused => State::Playing,
            State::Dead => State::Dead,
        };
    }

    /// Restart: a brand-new run in the same seed family (seed + 1), keeping
    /// the best score.
    pub fn restart(&mut self) {
        self.restart_at(self.seed.wrapping_add(1));
    }

    /// Restart at an explicit seed, keeping the best score.
    pub fn restart_at(&mut self, seed: u64) {
        let best = self.best.max(self.score);
        *self = Self::new(seed);
        self.best = best;
    }

    fn die(&mut self, cause: Cause) {
        if self.state != State::Dead {
            self.state = State::Dead;
            self.cause = Some(cause);
            if self.score > self.best {
                self.best = self.score;
            }
        }
    }

    /// Advance exactly one fixed tick. A paused or dead game does nothing.
    pub fn tick(&mut self) {
        if self.state != State::Playing {
            return;
        }
        self.ticks += 1;

        // 1. physics: gravity, integrate, clamp against the boundaries.
        self.vy = (self.vy + GRAVITY).min(VY_MAX);
        self.y += self.vy;
        if self.y <= CEILING_ROW {
            self.y = CEILING_ROW;
            self.die(Cause::Ceiling);
            return;
        }
        if self.y >= FLOOR_ROW {
            self.y = FLOOR_ROW;
            self.die(Cause::Floor);
            return;
        }

        // 2. world travel: whole columns, from the fractional accumulator.
        let cols = self.advance();
        for p in self.pillars.iter_mut() {
            p.x -= cols;
        }

        // 3. spawning: one pillar per SPACING_COLS of travel.
        self.next_spawn -= cols;
        if self.next_spawn <= 0 {
            let p = self.make_pillar();
            self.pillars.push(p);
            self.next_spawn += SPACING_COLS;
        }

        // 4. scoring: exactly once per pillar, when its trailing edge passes
        //    the penguin's leading column.
        for p in self.pillars.iter_mut() {
            if !p.scored && p.right() < PENGUIN_COL as i32 {
                p.scored = true;
                self.score += 1;
            }
        }

        // 5. collision against pillar cells only (the gap is the hole).
        if self.hit_pillar() {
            self.die(Cause::Pillar);
            return;
        }

        // 6. housekeeping.
        self.pillars.retain(|p| p.right() >= -1);
        if self.score > self.best {
            self.best = self.score;
        }
    }

    /// Consume the fractional accumulator; return the columns travelled this
    /// tick (0 or 1 — the top speed is 1 column per tick).
    fn advance(&mut self) -> i32 {
        self.sub += Self::speed_rate_for(self.score);
        if self.sub >= SPEED_UNIT {
            self.sub -= SPEED_UNIT;
            1
        } else {
            0
        }
    }

    /// True when the penguin's hitbox overlaps a solid pillar cell.
    pub fn hit_pillar(&self) -> bool {
        let left = PENGUIN_COL as i32;
        let right = PENGUIN_COL as i32 + PENGUIN_W as i32 - 1;
        self.pillars.iter().any(|p| {
            p.x <= right && p.right() >= left && (self.y < p.gap_top || self.y > p.gap_bottom)
        })
    }

    /// True when cell `(x, row)` is solid ice.
    pub fn pillar_at(&self, x: i32, row: i32) -> bool {
        self.pillars.iter().any(|p| {
            p.x <= x && x <= p.right() && row >= 0 && row < ROWS as i32 && !p.in_gap(row)
        })
    }

    /// The medal earned at the current score, or `None` below [`MEDAL_ICE`].
    pub fn medal(&self) -> Option<&'static str> {
        Self::medal_for(self.score)
    }

    /// Medal for a score: 10 ice, 25 bronze, 50 silver, 100 gold.
    pub fn medal_for(score: i32) -> Option<&'static str> {
        if score >= MEDAL_GOLD {
            Some("gold")
        } else if score >= MEDAL_SILVER {
            Some("silver")
        } else if score >= MEDAL_BRONZE {
            Some("bronze")
        } else if score >= MEDAL_ICE {
            Some("ice")
        } else {
            None
        }
    }

    /// Apply one key; returns `true` when the front-end should quit.
    /// `space`/`up`/`w` flap, `p` pause, `r` restart, `q` quit, ctrl-c quits.
    /// A flap on a dead run restarts it: death should cost two seconds.
    pub fn key(&mut self, c: char) -> bool {
        match c {
            ' ' | 'w' | 'W' | 'k' | 'K' => {
                if self.state == State::Dead {
                    self.restart();
                } else {
                    self.flap();
                }
                false
            }
            'p' | 'P' => {
                self.toggle_pause();
                false
            }
            'r' | 'R' => {
                self.restart();
                false
            }
            'q' | 'Q' | '\u{3}' => true,
            _ => false,
        }
    }

    /// The built-in autopilot: the key a bot presses this tick, or `None` for
    /// "no input". It aims at the centre of the nearest gap ahead and flaps
    /// when it is at/below that line and not already rising. This is what
    /// `--headless` demos and what `--record` writes into a replay fixture.
    pub fn autopilot_key(&self) -> Option<char> {
        if self.state != State::Playing {
            return None;
        }
        let target = self
            .pillars
            .iter()
            .find(|p| p.right() >= PENGUIN_COL as i32)
            .map(|p| (p.gap_top + p.gap_bottom) / 2)
            .unwrap_or(START_ROW);
        if self.y >= target && self.vy >= 0 {
            Some(' ')
        } else {
            None
        }
    }

    /// Canonical serialization of everything that defines the run. Two games
    /// with the same string have the same [`Game::state_hash`].
    pub fn canonical(&self) -> String {
        use std::fmt::Write as _;
        let mut s = String::with_capacity(128);
        let _ = write!(
            s,
            "seed={seed};rng={rng:x};state={state:?};ticks={ticks};score={score};best={best};y={y};\
             vy={vy};flaps={flaps};cause={cause:?};sub={sub};next_spawn={ns};pillars=[",
            seed = self.seed,
            rng = self.rng.state(),
            state = self.state,
            ticks = self.ticks,
            score = self.score,
            best = self.best,
            y = self.y,
            vy = self.vy,
            flaps = self.flaps,
            cause = self.cause,
            sub = self.sub,
            ns = self.next_spawn,
        );
        for p in &self.pillars {
            let _ = write!(
                s,
                "{}:{}-{}:{};",
                p.x,
                p.gap_top,
                p.gap_bottom,
                u8::from(p.scored)
            );
        }
        s.push(']');
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
            ("ticks".into(), self.ticks.to_string()),
            ("score".into(), self.score.to_string()),
            ("best".into(), self.best.to_string()),
            ("y".into(), self.y.to_string()),
            ("vy".into(), self.vy.to_string()),
            ("flaps".into(), self.flaps.to_string()),
            ("hash".into(), format!("{:016x}", self.state_hash())),
        ];
        if let Some(c) = self.cause {
            kv.push(("cause".into(), format!("{:?}", c).to_lowercase()));
        }
        if let Some(m) = self.medal() {
            kv.push(("medal".into(), m.to_string()));
        }
        kv
    }
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
    /// Emit ANSI colour. Off under `NO_COLOR`, `--mono`, or headless.
    pub color: bool,
}

impl Default for RenderOpts {
    /// The 80x24 reference window, colour on.
    fn default() -> Self {
        Self {
            width: 80,
            height: 24,
            mono: false,
            color: true,
        }
    }
}

const SGR_RESET: &str = "\x1b[0m";
/// Colour off emits no escapes at all.
const SGR_PLAIN: &str = "";
const SGR_PILLAR: &str = "\x1b[34m";
const SGR_ICE: &str = "\x1b[2;36m";
const SGR_PENGUIN: &str = "\x1b[1;33m";
const SGR_TEXT: &str = "\x1b[36m";

/// Glyphs and colours for one paint. Mono is ASCII-only; colour off emits
/// plain text (NO_COLOR), which keeps the frame diffable.
struct Painter {
    mono: bool,
    color: bool,
}

impl Painter {
    fn ice(&self) -> char {
        if self.mono {
            '-'
        } else {
            '\u{2588}'
        }
    }
    fn pillar(&self) -> char {
        if self.mono {
            '#'
        } else {
            '\u{2588}'
        }
    }
    /// A box corner.
    fn corner(&self, top: bool, left: bool) -> char {
        match (self.mono, top, left) {
            (true, _, _) => '+',
            (false, true, true) => '\u{250c}',
            (false, true, false) => '\u{2510}',
            (false, false, true) => '\u{2514}',
            (false, false, false) => '\u{2518}',
        }
    }

    /// The box's horizontal fill.
    fn fill(&self) -> char {
        if self.mono {
            '-'
        } else {
            '\u{2500}'
        }
    }
    fn wall(&self) -> char {
        if self.mono {
            '|'
        } else {
            '\u{2502}'
        }
    }
    fn sgr(&self, pillar: bool, ice: bool, penguin: bool) -> &'static str {
        if !self.color {
            // Colour off means NO escapes at all: the frame stays plain text
            // and diffable (NO_COLOR, --mono, headless).
            return SGR_PLAIN;
        }
        if penguin {
            SGR_PENGUIN
        } else if pillar {
            SGR_PILLAR
        } else if ice {
            SGR_ICE
        } else {
            SGR_RESET
        }
    }
    fn text(&self, s: &str) -> String {
        if self.color {
            format!("{SGR_TEXT}{s}{SGR_RESET}")
        } else {
            s.to_string()
        }
    }
}

/// The penguin sprite: `PENGUIN_SPRITE_W` columns starting one column to the
/// left of the hitbox. Dead shows a flattened penguin.
fn sprite(dead: bool) -> &'static str {
    if dead {
        "x@ox"
    } else {
        ">@->"
    }
}

/// One playfield row as exactly `COLS` plain characters (no border, no ANSI).
/// The headless plain-text projection and the render tests use this.
pub fn playfield_row(g: &Game, row: i32) -> String {
    let p = Painter {
        mono: true,
        color: false,
    };
    let mut cells = vec![p.ice(); COLS];
    if row != CEILING_ROW && row != FLOOR_ROW {
        for i in 0..COLS {
            if g.pillar_at(i as i32, row) {
                cells[i] = p.pillar();
            } else {
                cells[i] = ' ';
            }
        }
    }
    if row == g.y {
        let start = PENGUIN_COL as i32 - 1;
        for (k, ch) in sprite(g.state == State::Dead).chars().enumerate() {
            let x = start + k as i32;
            if x >= 0 && (x as usize) < COLS {
                cells[x as usize] = ch;
            }
        }
    }
    cells.into_iter().collect()
}

/// Truncate a rendered row to `max` visible cells. ANSI escapes take no
/// space, so they are walked past, not counted.
fn clip_visible(line: &str, max: usize) -> String {
    let mut out = String::with_capacity(line.len());
    let mut used = 0usize;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            out.push(c);
            if chars.next() == Some('[') {
                out.push('[');
                for c in chars.by_ref() {
                    out.push(c);
                    if c.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        if used >= max {
            break;
        }
        out.push(c);
        used += 1;
    }
    out
}

impl Game {
    /// The full frame at the reference 80x24 window, colour on.
    pub fn frame(&self) -> String {
        self.render(RenderOpts::default())
    }

    /// The frame for a given window. Pure: same state + same opts ⇒ same bytes.
    ///
    /// Degrade rules (spec.md "Rendering"):
    /// * `width >= COLS + 2` draws the box border; narrower drops the border;
    /// * `height >= 21` draws header + playfield + bottom border,
    ///   `>= 23` adds the keys line, `>= 24` the status line;
    /// * `height <= 20` drops the chrome and clips the playfield from the bottom.
    pub fn render(&self, opts: RenderOpts) -> String {
        let p = Painter {
            mono: opts.mono,
            color: opts.color,
        };
        let w = opts.width.max(1);
        let h = opts.height.max(1);
        let bordered = w >= COLS + 2;
        let inner_w = if bordered { w - 2 } else { w };

        let mut lines: Vec<String> = Vec::with_capacity(h);

        // header (needs header + 20 playfield rows + bottom border)
        if h >= 21 {
            let head = format!(
                " Flappy Pengy  score {}  best {}  seed {} {}",
                self.score,
                self.best,
                self.seed,
                match self.state {
                    State::Playing => "",
                    State::Paused => " PAUSED",
                    State::Dead => " DEAD",
                }
            );
            lines.push(self.box_line(&head, w, bordered, &p, true));
        }

        // playfield, clipped from the bottom when the window is short
        let field_rows = h.saturating_sub(lines.len()).min(ROWS);
        for row in 0..field_rows {
            lines.push(self.field_line(row as i32, inner_w, bordered, &p));
        }

        if h >= 22 && lines.len() < h {
            lines.push(self.box_line("", w, bordered, &p, false));
        }
        if h >= 23 && lines.len() < h {
            lines.push(p.text("space/up/w flap   p pause   r restart   q quit"));
        }
        if h >= 24 && lines.len() < h {
            lines.push(self.status_line(&p));
        }

        lines.truncate(h);
        // Nothing may exceed the window: every row is clipped to `w` visible
        // cells (ANSI escapes occupy none). A 30-column terminal gets a
        // 30-column game, not a wrapped mess.
        let lines: Vec<String> = lines.into_iter().map(|l| clip_visible(&l, w)).collect();
        let mut s = lines.join("\n");
        s.push('\n');
        s
    }

    fn status_line(&self, p: &Painter) -> String {
        match self.state {
            State::Playing => p.text(&format!(
                "score {}   best {}   tick {}   gap {}   speed {}/{}",
                self.score,
                self.best,
                self.ticks,
                Game::gap_height_for(self.score),
                Game::speed_rate_for(self.score),
                SPEED_UNIT
            )),
            State::Paused => p.text("PAUSED — p resumes, q quits"),
            State::Dead => {
                let medal = self
                    .medal()
                    .map(|m| format!(", {m} medal"))
                    .unwrap_or_default();
                p.text(&format!(
                    "{} — score {}, best {}{}.  r restarts, q quits",
                    self.cause.map(Cause::label).unwrap_or("dead"),
                    self.score,
                    self.best,
                    medal
                ))
            }
        }
    }

    fn box_line(&self, text: &str, w: usize, bordered: bool, p: &Painter, top: bool) -> String {
        if !bordered {
            return p.text(text.trim_start());
        }
        let inner = w - 2;
        let t: String = text.chars().take(inner).collect();
        let pad = inner - t.chars().count();
        let mut s = String::new();
        s.push(p.corner(top, true));
        s.push_str(&t);
        for _ in 0..pad {
            s.push(p.fill());
        }
        s.push(p.corner(top, false));
        s
    }

    fn field_line(&self, row: i32, inner_w: usize, bordered: bool, p: &Painter) -> String {
        let mut s = String::new();
        let mut last = "";
        if bordered {
            let wgr = p.sgr(false, true, false);
            s.push_str(wgr);
            s.push(p.wall());
            last = wgr;
        }
        let sprite_left = PENGUIN_COL as i32 - 1;
        let sprite_right = PENGUIN_COL as i32 + PENGUIN_SPRITE_W as i32 - 2;
        let dead = self.state == State::Dead;
        for i in 0..inner_w {
            let x = i as i32;
            let is_ice = row == CEILING_ROW || row == FLOOR_ROW;
            let is_pillar = !is_ice && self.pillar_at(x, row);
            let is_penguin = row == self.y && x >= sprite_left && x <= sprite_right;
            let ch = if is_penguin {
                let k = (x - sprite_left) as usize;
                sprite(dead).chars().nth(k).unwrap_or(' ')
            } else if is_ice {
                p.ice()
            } else if is_pillar {
                p.pillar()
            } else {
                ' '
            };
            let sgr = p.sgr(is_pillar, is_ice, is_penguin);
            if sgr != last {
                s.push_str(sgr);
                last = sgr;
            }
            s.push(ch);
        }
        if p.color {
            s.push_str(SGR_RESET);
        }
        if bordered {
            let wgr = p.sgr(false, true, false);
            s.push_str(wgr);
            s.push(p.wall());
        }
        s
    }
}

/// Plain-text state dump for `--headless` (no ANSI, no glyphs, diffable).
pub fn plain_state(g: &Game) -> String {
    use std::fmt::Write as _;
    let mut s = String::new();
    for (k, v) in g.result_kv() {
        let _ = writeln!(s, "{k}={v}");
    }
    s
}
