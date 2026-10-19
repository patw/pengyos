//! pengy-lander — Ice Lander: **every rule and the renderer, in pure code**.
//!
//! This file never touches the outside world: no I/O, no clock, no threads, no
//! files, no system entropy. A run is a function of `(seed, script)`, which is
//! what makes the headless harness (`--replay`) a real acceptance test and what
//! makes [`Game::render`] / [`frame`] a pure, diffable function of state.
//!
//! The game: the 1979 Lunar Lander cart. A ridge of ice broken by two to four
//! flat landing pads, a probe with 100 units of fuel, moon gravity, and three
//! numbers that must all be small at the moment the feet touch: sideways speed
//! under 1.0 m/s, descent under 2.0 m/s, tilt under 5 degrees. Land on a 5x pad
//! and the wave is worth five times what a 1x pad is. Every wave is fresh
//! terrain, and every wave pays a bonus for it. Crash, and the run is over.
//!
//! All arithmetic is integer fixed point (see [`VSCALE`] and [`PSCALE`]) and
//! all trigonometry is a 120-entry table, so a run is bit-for-bit reproducible
//! on every machine `cargo build --release` runs on — no floating point, no
//! `libm`, no platform drift.
//!
//! See `spec.md` for the contract and `tests/rules.rs` for the rules asserted.

use std::fmt::Write as _;

// ---------------------------------------------------------------------------
// Tuning constants — the difficulty of the game lives here.
// ---------------------------------------------------------------------------

/// Fixed timestep: 1/30 s. The physics is defined *per tick*, in metres and
/// m/s, so this constant is the game's speed control.
pub const TICK_MS: u64 = 33;

/// Field width in columns. One column is exactly one metre of ground.
pub const COLS: usize = 60;
/// Field height in rows (the terrain view). The world is [`WORLD_H_M`] metres
/// tall, so the view is a 3/4 scale map of it.
pub const FIELD_ROWS: usize = 18;
/// The window the game is designed for.
pub const REF_WIDTH: usize = 80;
/// The window height the game is designed for.
pub const REF_HEIGHT: usize = 24;

/// World height in metres.
pub const WORLD_H_M: i64 = 24;
/// The ridge never rises above this, so the spawn (at [`SPAWN_Y_M`]) is always
/// clear of terrain and above the highest ridge.
pub const MAX_RIDGE_M: i64 = 12;
/// The ridge never falls below this.
pub const MIN_RIDGE_M: i64 = 2;
/// The probe always spawns at this altitude, heading straight up.
pub const SPAWN_Y_M: i64 = 22;

/// Velocity scale: 30 000 units per m/s. Chosen so every per-tick delta of
/// every tuning constant is an exact integer (1/30 s of 1.62 m/s^2 is 54
/// mm/s, of 5.0 is 166 2/3 mm/s, and 30 000/30 divides both exactly).
pub const VSCALE: i64 = 30_000;
/// Position scale: 900 000 units per metre = [`VSCALE`] * 30, which makes the
/// position update `x += vx` per tick exact, with no rounding term.
pub const PSCALE: i64 = 900_000;

/// Moon gravity, 1.62 m/s^2, as the per-tick velocity delta in [`VSCALE`]
/// units. `--body moon` (the default).
pub const G_MOON: i64 = 1_620;
/// Asteroid gravity, 5.0 m/s^2, same units. `--body asteroid`.
pub const G_ASTEROID: i64 = 5_000;
/// Engine thrust, 4.0 m/s^2, as a per-tick velocity delta along the heading.
pub const THRUST_ACC: i64 = 4_000;
/// Rotation: 90 deg/s = 3 degrees per tick. The heading is therefore always a
/// multiple of 3 degrees, which is why [`SIN_TABLE`] has 120 entries.
pub const ROT_PER_TICK: i64 = 3;
/// Degrees per full turn.
pub const DEG_TURN: i64 = 360;

/// Fuel: 100 units, burning 1 unit per second of thrust. Stored x30, so the
/// burn is exactly 1 unit per tick and the displayed fuel is `fuel / 30`.
pub const FUEL_START: i64 = 3_000;
/// Fuel burned per tick of thrust.
pub const FUEL_BURN: i64 = 1;
/// Fuel units at the start, for display and scoring.
pub const FUEL_UNITS: i64 = 100;

/// Landing limits. Touchdown is a landing, not a crash, only when all three
/// hold strictly: |vx| < 1.0 m/s, |vy| < 2.0 m/s, |tilt| < 5 degrees.
pub const MAX_VX: i64 = 30_000;
/// The descent limit, 2.0 m/s.
pub const MAX_VY: i64 = 60_000;
/// The tilt limit, in degrees.
pub const MAX_TILT_DEG: i64 = 5;

/// Pads are 6 to 12 columns (metres) wide.
pub const PAD_MIN_W: usize = 6;
/// Longest pad.
pub const PAD_MAX_W: usize = 12;
/// A terrain has at least this many pads.
pub const MIN_PADS: usize = 2;
/// and at most this many.
pub const MAX_PADS: usize = 4;
/// The pad multipliers in play: 1x, 2x, 5x.
pub const MULTS: [u64; 3] = [1, 2, 5];

/// A landing is worth 100 x the pad multiplier.
pub const LAND_BASE: u64 = 100;
/// Plus 10 per unit of fuel left.
pub const FUEL_POINTS: u64 = 10;
/// Plus a wave bonus of 50 per wave already banked: fresh terrain pays.
pub const WAVE_BONUS: u64 = 50;

/// The bodies `--body` accepts.
pub const BODIES: [&str; 2] = ["moon", "asteroid"];

// ---------------------------------------------------------------------------
// Trigonometry: a 120-entry table, (sin, cos) x1000 at 3-degree steps.
// ---------------------------------------------------------------------------

/// `SIN_TABLE[i] = (sin(3i deg), cos(3i deg)) * 1000`. The heading is always a
/// multiple of 3 degrees, so thrust components are exact table lookups: no
/// floating point, no `libm`, no platform-dependent rounding.
pub const SIN_TABLE: [(i64, i64); 120] = [
    //   0.. 27 deg
    (0, 1000), (52, 999), (105, 995), (156, 988), (208, 978), (259, 966), (309, 951), (358, 934), (407, 914), (454, 891),
    //  30.. 57 deg
    (500, 866), (545, 839), (588, 809), (629, 777), (669, 743), (707, 707), (743, 669), (777, 629), (809, 588), (839, 545),
    //  60.. 87 deg
    (866, 500), (891, 454), (914, 407), (934, 358), (951, 309), (966, 259), (978, 208), (988, 156), (995, 105), (999, 52),
    //  90..117 deg
    (1000, 0), (999, -52), (995, -105), (988, -156), (978, -208), (966, -259), (951, -309), (934, -358), (914, -407), (891, -454),
    // 120..147 deg
    (866, -500), (839, -545), (809, -588), (777, -629), (743, -669), (707, -707), (669, -743), (629, -777), (588, -809), (545, -839),
    // 150..177 deg
    (500, -866), (454, -891), (407, -914), (358, -934), (309, -951), (259, -966), (208, -978), (156, -988), (105, -995), (52, -999),
    // 180..207 deg
    (0, -1000), (-52, -999), (-105, -995), (-156, -988), (-208, -978), (-259, -966), (-309, -951), (-358, -934), (-407, -914), (-454, -891),
    // 210..237 deg
    (-500, -866), (-545, -839), (-588, -809), (-629, -777), (-669, -743), (-707, -707), (-743, -669), (-777, -629), (-809, -588), (-839, -545),
    // 240..267 deg
    (-866, -500), (-891, -454), (-914, -407), (-934, -358), (-951, -309), (-966, -259), (-978, -208), (-988, -156), (-995, -105), (-999, -52),
    // 270..297 deg
    (-1000, 0), (-999, 52), (-995, 105), (-988, 156), (-978, 208), (-966, 259), (-951, 309), (-934, 358), (-914, 407), (-891, 454),
    // 300..327 deg
    (-866, 500), (-839, 545), (-809, 588), (-777, 629), (-743, 669), (-707, 707), (-669, 743), (-629, 777), (-588, 809), (-545, 839),
    // 330..357 deg
    (-500, 866), (-454, 891), (-407, 914), (-358, 934), (-309, 951), (-259, 966), (-208, 978), (-156, 988), (-105, 995), (-52, 999),
];

/// Table index for a heading in degrees (any heading, wrapped).
pub fn deg_index(deg: i64) -> usize {
    let d = ((deg % DEG_TURN) + DEG_TURN) % DEG_TURN;
    (d / ROT_PER_TICK) as usize
}

/// Tilt: how far the heading is from straight up, 0..180 degrees.
pub fn tilt_of(deg: i64) -> i64 {
    let d = ((deg % DEG_TURN) + DEG_TURN) % DEG_TURN;
    if d > 180 {
        DEG_TURN - d
    } else {
        d
    }
}

// ---------------------------------------------------------------------------
// Seeded RNG
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
// Bodies, states, causes
// ---------------------------------------------------------------------------

/// The gravity body. `--body moon` (default) is the Moon at 1.62 m/s^2;
/// `--body asteroid` is a small asteroid at 5.0 m/s^2, which is a much harder
/// game: the same 4.0 m/s^2 engine has to spend most of its thrust just
/// hovering.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Body {
    Moon,
    Asteroid,
}

impl Body {
    pub fn name(self) -> &'static str {
        match self {
            Body::Moon => "moon",
            Body::Asteroid => "asteroid",
        }
    }

    /// Per-tick gravity, in [`VSCALE`] units.
    pub fn gravity(self) -> i64 {
        match self {
            Body::Moon => G_MOON,
            Body::Asteroid => G_ASTEROID,
        }
    }

    /// Parse `--body`.
    pub fn parse(s: &str) -> Option<Body> {
        match s.to_ascii_lowercase().as_str() {
            "moon" | "m" | "luna" => Some(Body::Moon),
            "asteroid" | "a" | "ast" => Some(Body::Asteroid),
            _ => None,
        }
    }
}

/// The state of the run.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    /// In the air, controls live.
    Flying,
    /// Feet down on a pad: the wave is banked. `n` flies the next wave.
    Landed,
    /// Feet down on rock, or too fast, or too tilted. The run is over.
    Crashed,
}

impl State {
    /// The run is finished: only `n`, `r` and `q` do anything.
    pub fn is_over(self) -> bool {
        matches!(self, State::Crashed)
    }
}

/// Why the run ended. Shown on the end screen and in `cause=`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cause {
    /// Touchdown outside any pad.
    OffPad,
    /// |vx| >= 1.0 m/s at touchdown.
    TooFastSideways,
    /// |vy| >= 2.0 m/s at touchdown.
    TooFastDescent,
    /// |tilt| >= 5 degrees at touchdown.
    TooTilted,
    /// Out of fuel with the probe still falling: it fell the rest of the way.
    OutOfFuel,
}

impl Cause {
    pub fn text(self) -> &'static str {
        match self {
            Cause::OffPad => "off pad",
            Cause::TooFastSideways => "too fast sideways",
            Cause::TooFastDescent => "too fast down",
            Cause::TooTilted => "too tilted",
            Cause::OutOfFuel => "out of fuel",
        }
    }
}

// ---------------------------------------------------------------------------
// Terrain
// ---------------------------------------------------------------------------

/// One landing pad: the columns it covers (inclusive) and its multiplier.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Pad {
    /// First column.
    pub start: usize,
    /// Last column, inclusive.
    pub end: usize,
    /// 1, 2 or 5.
    pub mult: u64,
}

impl Pad {
    /// Width in columns (= metres).
    pub fn width(&self) -> usize {
        self.end - self.start + 1
    }

    /// Centre column.
    pub fn centre(&self) -> usize {
        (self.start + self.end) / 2
    }

    /// Is this column on the pad?
    pub fn covers(&self, col: usize) -> bool {
        col >= self.start && col <= self.end
    }
}

/// The ground: one height in metres per column, and the pads on it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Terrain {
    /// Ground height in metres, one per column.
    pub h: Vec<i64>,
    /// The pads, left to right.
    pub pads: Vec<Pad>,
}

impl Terrain {
    /// Ground height at a column.
    pub fn at(&self, col: usize) -> i64 {
        self.h[col.min(self.h.len() - 1)]
    }

    /// Highest ridge.
    pub fn max_h(&self) -> i64 {
        self.h.iter().copied().max().unwrap_or(0)
    }

    /// The pad covering a column, if any.
    pub fn pad_at(&self, col: usize) -> Option<&Pad> {
        self.pads.iter().find(|p| p.covers(col))
    }

    /// The pad the autopilot aims at: the highest multiplier, tie-broken by
    /// the shortest horizontal distance from `x` (in metres).
    pub fn best_pad(&self, x_m: i64) -> Option<&Pad> {
        let mut best: Option<&Pad> = None;
        let mut best_key: Option<(u64, i64)> = None;
        for p in &self.pads {
            let key = (p.mult, (p.centre() as i64 - x_m).abs());
            // Highest multiplier wins; among equals, the nearest.
            let better = match best_key {
                None => true,
                Some((m, d)) => p.mult > m || (p.mult == m && key.1 < d),
            };
            if better {
                best_key = Some(key);
                best = Some(p);
            }
        }
        best
    }
}

/// Generate one terrain from the generator. Always yields [`MIN_PADS`]..=
/// [`MAX_PADS`] pads of width [`PAD_MIN_W`]..=[`PAD_MAX_W`], on a ridge that
/// never exceeds [`MAX_RIDGE_M`].
pub fn generate_terrain(rng: &mut Rng) -> Terrain {
    // A ridge that walks one metre at a time, so it reads as ground and not as
    // spikes. Slope is limited to +/-1 m per column.
    let mut h = vec![0i64; COLS];
    let mut cur = MIN_RIDGE_M + rng.below(4) as i64; // 2..5
    for c in 0..COLS {
        let step = match rng.below(3) {
            0 => -1,
            1 => 0,
            _ => 1,
        };
        cur = (cur + step).clamp(MIN_RIDGE_M, MAX_RIDGE_M);
        h[c] = cur;
    }

    // Pads: 2..4 of them, laid left to right with at least 3 columns of ridge
    // between them. A pad is cut 2 metres down into the ridge it sits on, so it
    // reads as a bench in the ice, and it is always flat.
    let want = MIN_PADS + rng.below((MAX_PADS - MIN_PADS + 1) as u64) as usize; // 2..4
    let mut pads: Vec<Pad> = Vec::new();
    let mut pos = 1 + rng.below(5) as usize; // 1..5
    while pads.len() < want {
        let width = PAD_MIN_W + rng.below((PAD_MAX_W - PAD_MIN_W + 1) as u64) as usize;
        if pos + width + 3 > COLS {
            break; // no room left for this pad, and none for a gap
        }
        let flat = (h[pos] - 2).clamp(MIN_RIDGE_M, MAX_RIDGE_M);
        for c in pos..pos + width {
            h[c] = flat;
        }
        pads.push(Pad {
            start: pos,
            end: pos + width - 1,
            mult: 1,
        });
        pos += width + 3 + rng.below(4) as usize;
    }
    // A terrain with fewer pads than MIN_PADS can only happen if the ridge ran
    // out of room; force the minimum by carving the first pad at the left edge.
    while pads.len() < MIN_PADS {
        let start = pads.len() * (PAD_MIN_W + 3);
        let flat = (h[start] - 2).clamp(MIN_RIDGE_M, MAX_RIDGE_M);
        for c in start..start + PAD_MIN_W {
            h[c] = flat;
        }
        pads.push(Pad {
            start,
            end: start + PAD_MIN_W - 1,
            mult: 1,
        });
    }

    // Multipliers: one 5x pad, one 2x pad, the rest 1x.
    let five = rng.below(pads.len() as u64) as usize;
    pads[five].mult = 5;
    let two = rng.below(pads.len() as u64) as usize;
    if two != five {
        pads[two].mult = 2;
    }
    Terrain { h, pads }
}

/// The spawn: above the highest ridge, clear of terrain, heading straight up,
/// drifting sideways by at most 1.5 m/s.
pub fn spawn_point(rng: &mut Rng, terrain: &Terrain) -> (i64, i64, i64, i64, i64) {
    let x = (4 + rng.below((COLS as i64 - 8) as u64) as i64) * PSCALE; // 4..=55 m
    let y = SPAWN_Y_M * PSCALE;
    // Above the highest ridge, always: SPAWN_Y_M > MAX_RIDGE_M >= max ridge.
    let vx = (rng.below(7) as i64 - 3) * (VSCALE / 2); // -1.5 .. +1.5 m/s
    let vy = 0;
    let heading = 0;
    let _ = terrain.max_h();
    (x, y, vx, vy, heading)
}

// ---------------------------------------------------------------------------
// Landing classification
// ---------------------------------------------------------------------------

/// The verdict of a touchdown.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Touch {
    /// A landing. `Some(mult)` is the pad's multiplier.
    Landed(u64),
    /// A crash, with the reason.
    Crashed(Cause),
}

/// Classify a touchdown, exactly as `spec.md` states it: a landing when the
/// feet are on a pad **and** |vx| < [`MAX_VX`] **and** |vy| < [`MAX_VY`]
/// **and** tilt < [`MAX_TILT_DEG`]. Every comparison is strict, so a probe
/// arriving at exactly 1.0 m/s is a crash.
///
/// The order is the order of blame: no pad is the first thing said, then
/// sideways speed, then descent, then tilt.
pub fn classify_touch(on_pad_mult: Option<u64>, vx: i64, vy: i64, tilt_deg: i64) -> Touch {
    match on_pad_mult {
        None => return Touch::Crashed(Cause::OffPad),
        Some(m) => {
            if vx.abs() >= MAX_VX {
                return Touch::Crashed(Cause::TooFastSideways);
            }
            if vy.abs() >= MAX_VY {
                return Touch::Crashed(Cause::TooFastDescent);
            }
            if tilt_deg >= MAX_TILT_DEG {
                return Touch::Crashed(Cause::TooTilted);
            }
            Touch::Landed(m)
        }
    }
}

/// Score of one landing: 100 x multiplier, plus 10 per unit of fuel left, plus
/// 50 per wave already banked (the fresh-terrain wave bonus).
pub fn landing_score(mult: u64, fuel_units: i64, waves_landed: u64) -> u64 {
    LAND_BASE * mult
        + FUEL_POINTS * fuel_units.max(0) as u64
        + WAVE_BONUS * waves_landed
}

// ---------------------------------------------------------------------------
// The game
// ---------------------------------------------------------------------------

/// One run of Ice Lander. Every field here is game state; the whole struct is
/// what [`Game::canonical`] serialises and [`Game::state_hash`] fingerprints.
#[derive(Clone, Debug)]
pub struct Game {
    /// The seed this run started on.
    pub seed: u64,
    /// The gravity body.
    pub body: Body,
    /// Generator state — part of the run, so a replay is reproducible.
    pub rng: Rng,
    /// The ground.
    pub terrain: Terrain,

    /// What is happening.
    pub state: State,
    /// Why it ended, once it has.
    pub cause: Option<Cause>,

    // Probe, all in fixed point: positions in [`PSCALE`], velocities in
    // [`VSCALE`], heading in degrees.
    /// Horizontal position.
    pub x: i64,
    /// Altitude above the base of the world.
    pub y: i64,
    /// Horizontal velocity, + is right.
    pub vx: i64,
    /// Vertical velocity, + is up.
    pub vy: i64,
    /// Heading in degrees, 0 = straight up, + = clockwise.
    pub heading: i64,
    /// Fuel, x30 units.
    pub fuel: i64,
    /// Is the engine firing this tick.
    pub thrust: bool,

    /// Wave number, 1-based.
    pub wave: u64,
    /// Waves banked (successful landings).
    pub waves_landed: u64,
    /// Score banked from finished waves.
    pub total: u64,
    /// Points scored by the landing that ended the current wave.
    pub last_points: u64,
    /// Ticks since the run started.
    pub ticks: u64,
    /// Ticks this wave.
    pub wave_ticks: u64,
    /// The pad the last landing paid on.
    pub last_mult: u64,

    // The persisted record, mirrored in so the HUD can show it. Owned by
    // src/main.rs; the library never reads or writes a file.
    /// Best score.
    pub best_score: u64,
    /// Best wave count.
    pub best_waves: u64,
    /// The seed the best score happened on.
    pub best_seed: u64,
}

impl Game {
    /// A run on `seed`, on the Moon.
    pub fn new(seed: u64) -> Self {
        Self::with_body(seed, Body::Moon)
    }

    /// A run on `seed` with a chosen body. Terrain and spawn are drawn from
    /// the seeded generator, so the same seed gives the same ground.
    pub fn with_body(seed: u64, body: Body) -> Self {
        let mut rng = Rng::new(seed);
        let terrain = generate_terrain(&mut rng);
        let (x, y, vx, vy, heading) = spawn_point(&mut rng, &terrain);
        Game {
            seed,
            body,
            rng,
            terrain,
            state: State::Flying,
            cause: None,
            x,
            y,
            vx,
            vy,
            heading,
            fuel: FUEL_START,
            thrust: false,
            wave: 1,
            waves_landed: 0,
            total: 0,
            last_points: 0,
            ticks: 0,
            wave_ticks: 0,
            last_mult: 0,
            best_score: 0,
            best_waves: 0,
            best_seed: 0,
        }
    }

    /// Restart the run on the next seed of this seed family, keeping the
    /// persisted record. Terrain and spawn change; the score starts at zero.
    pub fn restart(&mut self) {
        let seed = self.seed.wrapping_add(1);
        let body = self.body;
        let (bs, bw, bseed) = (self.best_score, self.best_waves, self.best_seed);
        *self = Game::with_body(seed, body);
        self.best_score = bs;
        self.best_waves = bw;
        self.best_seed = bseed;
    }

    /// Fresh terrain and the next wave. From the air this abandons the wave in
    /// progress; from a landed pad it banks the landing and flies the next
    /// wave. The run's banked score is never lost.
    pub fn new_terrain(&mut self) {
        self.wave += 1;
        self.terrain = generate_terrain(&mut self.rng);
        let (x, y, vx, vy, heading) = spawn_point(&mut self.rng, &self.terrain);
        self.x = x;
        self.y = y;
        self.vx = vx;
        self.vy = vy;
        self.heading = heading;
        self.fuel = FUEL_START;
        self.thrust = false;
        self.state = State::Flying;
        self.cause = None;
        self.wave_ticks = 0;
        self.last_points = 0;
        self.last_mult = 0;
    }

    /// The probe's column.
    pub fn col(&self) -> usize {
        ((self.x / PSCALE).max(0)) as usize % COLS
    }

    /// Altitude above the ground under the probe, in metres (x1000).
    pub fn altitude_mm(&self) -> i64 {
        (self.y / 1000) - self.terrain.at(self.col()) * 1000
    }

    /// Altitude above the ground, in metres.
    pub fn altitude(&self) -> i64 {
        self.y / PSCALE - self.terrain.at(self.col())
    }

    /// Tilt from straight up, 0..180 degrees.
    pub fn tilt(&self) -> i64 {
        tilt_of(self.heading)
    }

    /// Fuel in whole units.
    pub fn fuel_units(&self) -> i64 {
        self.fuel / 30
    }

    /// The score this wave would pay if it landed on `mult` right now.
    pub fn projected_score(&self, mult: u64) -> u64 {
        self.total + landing_score(mult, self.fuel_units(), self.waves_landed)
    }

    /// The run's score: everything banked.
    pub fn score(&self) -> u64 {
        self.total
    }

    /// Is the engine firing (fuel permitting)?
    pub fn engine_on(&self) -> bool {
        self.thrust && self.fuel > 0 && self.state == State::Flying
    }

    /// Thrust acceleration components for this tick, in [`VSCALE`] units per
    /// tick: `(ax, ay)`. Zero when the engine is off.
    pub fn thrust_accel(&self) -> (i64, i64) {
        if !self.engine_on() {
            return (0, 0);
        }
        let (s, c) = SIN_TABLE[deg_index(self.heading)];
        (
            THRUST_ACC * s / 1000,
            THRUST_ACC * c / 1000,
        )
    }

    // -----------------------------------------------------------------------
    // Input
    // -----------------------------------------------------------------------

    /// Handle one keypress. Returns true when the front-end must quit.
    ///
    /// `space` thrust, `a`/`d` rotate, `n` new terrain, `r` restart, `q` quit.
    /// The arrow keys are accepted as rotation. Everything else is ignored.
    /// `Escape` is deliberately inert: terminals deliver the arrow keys as
    /// `ESC [ A`, so a bare `ESC` handler would make every arrow fatal.
    pub fn key(&mut self, c: char) -> bool {
        match c {
            'q' | 'Q' => return true,
            ' ' => {
                if self.state == State::Flying {
                    self.thrust = true;
                }
            }
            'a' | 'A' | 'h' => self.rotate(-ROT_PER_TICK),
            'd' | 'D' | 'l' => self.rotate(ROT_PER_TICK),
            // A crash ends the run: new terrain then is a new run, never a
            // way to fly on with the crashed run's score.
            'n' | 'N' if self.state == State::Crashed => self.restart(),
            'n' | 'N' => self.new_terrain(),
            'r' | 'R' => self.restart(),
            _ => {}
        }
        false
    }

    /// Rotate, wrapping the heading into 0..360.
    pub fn rotate(&mut self, delta: i64) {
        if self.state != State::Flying {
            return;
        }
        self.heading = ((self.heading + delta) % DEG_TURN + DEG_TURN) % DEG_TURN;
    }

    // -----------------------------------------------------------------------
    // Physics
    // -----------------------------------------------------------------------

    /// Advance one 1/30 s tick.
    ///
    /// The integration is explicit Euler at the fixed step, in this order:
    /// thrust along the heading, then gravity, then position. Fuel is burned
    /// for the tick, floors at 0, and the engine stops when it hits 0.
    pub fn tick(&mut self) {
        if self.state != State::Flying {
            return; // a landed or crashed probe is frozen: nothing moves, and
            // a frozen tick is not a tick of the run.
        }
        self.ticks += 1;
        self.wave_ticks += 1;

        let firing = self.engine_on();
        let (ax, ay) = self.thrust_accel();
        if firing {
            self.fuel = (self.fuel - FUEL_BURN).max(0);
        }
        let g = self.body.gravity();

        self.vx += ax;
        self.vy += ay - g;

        self.x += self.vx;
        self.y += self.vy;

        // World walls: the field is 60 m wide, and the sides reflect.
        if self.x < 0 {
            self.x = -self.x;
            self.vx = -self.vx;
        }
        let max_x = (COLS as i64 - 1) * PSCALE;
        if self.x > max_x {
            self.x = 2 * max_x - self.x;
            self.vx = -self.vx;
        }
        // Ceiling.
        let max_y = WORLD_H_M * PSCALE;
        if self.y > max_y {
            self.y = 2 * max_y - self.y;
            if self.vy > 0 {
                self.vy = 0;
            }
        }
        if self.y < 0 {
            self.y = 0;
        }

        // Out of fuel: the probe is falling and cannot be saved. It is not a
        // crash yet — it still has to arrive — but the run is decided, so say
        // it plainly and stop the engine.
        if self.fuel == 0 {
            self.thrust = false;
            self.cause = Some(Cause::OutOfFuel);
        }

        // Touchdown.
        let col = self.col();
        let ground = self.terrain.at(col) * PSCALE;
        if self.y <= ground {
            self.y = ground;
            let mult = self.terrain.pad_at(col).map(|p| p.mult);
            match classify_touch(mult, self.vx, self.vy, self.tilt()) {
                Touch::Landed(m) => {
                    self.cause = None;
                    self.thrust = false;
                    self.vx = 0;
                    self.vy = 0;
                    self.last_mult = m;
                    self.last_points =
                        landing_score(m, self.fuel_units(), self.waves_landed);
                    self.total += self.last_points;
                    self.waves_landed += 1;
                    self.state = State::Landed;
                }
                Touch::Crashed(c) => {
                    self.cause = Some(c);
                    self.thrust = false;
                    self.vx = 0;
                    self.vy = 0;
                    self.state = State::Crashed;
                }
            }
        }

        // Thrust is a latch, not a mode: a keypress arms it for the tick that
        // consumes it, and the tick disarms it. A held key autorepeats, so a
        // held space keeps the engine burning; in a replay file, one line
        // containing a space is exactly one tick of thrust.
        self.thrust = false;
    }

    // -----------------------------------------------------------------------
    // Autopilot
    // -----------------------------------------------------------------------

    /// The autopilot's keypresses for this tick, as a string (possibly empty).
    ///
    /// A pure function of state: it aims at the best pad, flies a proportional
    /// controller in integer millimetres, picks the thrust direction by taking
    /// the table entry whose direction best matches the wanted acceleration,
    /// rotates toward it, and fires when it is pointing there. Used by
    /// `--headless` (the demo path) and by `--record` (the fixtures).
    pub fn autopilot_keys(&self) -> String {
        if self.state != State::Flying {
            return String::new();
        }
        // Which pad to fly to: the richest one the probe can actually reach.
        // Reach is a ratio, not a distance: falling at `vy` for `span / vy`
        // ticks covers `vx * that` metres, so a pad is in reach when
        // dist / vx_cruise <= span / vy_cruise. Chasing an unreachable 5x pad
        // is how an autopilot lands on rock.
        let xm0 = self.x / 900;
        let ym0 = self.y / 900;
        let mut pad: Option<&Pad> = None;
        let mut key: Option<(u8, u64, i64)> = None;
        for p in &self.terrain.pads {
            let tx = p.centre() as i64 * 1000;
            let ty = self.terrain.at(p.centre()) * 1000;
            let span = (ym0 - ty).max(0);
            let dist = (tx - xm0).abs();
            let reach = if span >= dist { 1 } else { 0 };
            let k = (reach, p.mult, -(dist / 1000));
            if key.map(|old| k > old).unwrap_or(true) {
                key = Some(k);
                pad = Some(p);
            }
        }
        let pad = match pad {
            Some(p) => p,
            None => return String::new(),
        };

        // Control units, all integer:
        //   position  mm          = x / 900   (PSCALE / 1000)
        //   velocity  mm per tick = v / 1000  (VSCALE / 30; 1 m/s = 30)
        //   accel     1/100 mm/tick^2 = v_delta / 10  (thrust 400, moon g 162)
        let xm = self.x / 900;
        let ym = self.y / 900;
        let vx = self.vx / 1000;
        let vy = self.vy / 1000;
        let tx = pad.centre() as i64 * 1000;
        let ty = self.terrain.at(pad.centre()) * 1000;
        let span = (ym - ty).max(0);

        let thrust100 = THRUST_ACC / 10; // 400
        let g100 = self.body.gravity() / 10;

        // Descent profile: a 0.83 m/s cruise, easing to 0.17 m/s over the last
        // metre. The floor is what makes the approach close - a pure
        // proportional profile decays exponentially and hovers for ever a
        // metre above the pad - and 0.17 m/s is an eighth of the 2.0 m/s the
        // pad allows, so the touchdown is legal by construction.
        // Energy management: the probe falls at up to 0.83 m/s and flies
        // sideways at up to 1.17 m/s, so the descent is slowed to whatever
        // leaves time to close the horizontal gap. Without this the autopilot
        // arrives under the pad, too far away, and lands on rock.
        let dist = (tx - xm).abs();
        let budget = span * 25 / dist.max(1);
        let cruise = span / 50;
        let vy_want = -cruise.min(budget).max(5).clamp(5, 20);
        // Approach speed: head for the pad centre, easing to a standstill as
        // the centre comes abeam.
        let vx_want = ((tx - xm) / 40).clamp(-25, 25);

        // The low phase is the landing phase: nose straight up, vertical
        // authority only. Sideways speed is killed high, where tilt is cheap;
        // down here, tilt is what crashes you.
        let low = span <= 1_500;
        let ax_cap = if low {
            0
        } else if span <= 5_000 {
            thrust100 / 6
        } else {
            thrust100 / 2
        };
        let ax = ((vx_want - vx) * 8).clamp(-ax_cap, ax_cap);
        // Wanted *thrust*: the net acceleration we want, plus gravity, because
        // gravity is always on. Gain 8: the loop closes once per 1/30 s and
        // moon gravity moves the velocity by 1.62 mm/tick in a single tick, so
        // a stiffer gain overshoots every time.
        let t_need = (g100 + (vy_want - vy) * 6).clamp(0, thrust100);

        // The engine is binary: full thrust along the nose, or nothing. So the
        // wanted *magnitude* is flown as a duty cycle over a 10-tick window -
        // pulse-density modulation. `ticks` is the phase, so it stays pure.
        let period = 10i64;
        let slots = ((t_need * period + thrust100 / 2) / thrust100).clamp(0, period);
        if slots <= 0 || self.fuel <= 0 {
            return String::new(); // free fall: gravity is the cheapest descent
        }

        // The table direction that best matches the wanted thrust.
        let mut best_i = 0usize;
        let mut best_dot = i64::MIN;
        for i in 0..120 {
            let (s, c) = SIN_TABLE[i];
            let dot = s * ax + c * t_need;
            if dot > best_dot {
                best_dot = dot;
                best_i = i;
            }
        }
        let want_deg = (best_i as i64 * ROT_PER_TICK) % DEG_TURN;
        let have = ((self.heading % DEG_TURN) + DEG_TURN) % DEG_TURN;
        let mut diff = want_deg - have;
        if diff > 180 {
            diff -= DEG_TURN;
        }
        if diff < -180 {
            diff += DEG_TURN;
        }

        let mut out = String::new();
        if diff >= ROT_PER_TICK {
            out.push('d');
        } else if diff <= -ROT_PER_TICK {
            out.push('a');
        }
        // Burn when the nose is on the wanted direction, on this tick of the
        // duty cycle. In the landing phase the nose has to be legal for the
        // landing, not just useful: 3 degrees, so the tilt at touchdown is
        // inside the 5-degree limit.
        let margin = if low {
            ROT_PER_TICK
        } else if span <= 5_000 {
            2 * ROT_PER_TICK
        } else {
            3 * ROT_PER_TICK
        };
        let phase = (self.ticks % period as u64) as i64;
        if diff.abs() <= margin && phase < slots {
            out.push(' ');
        }
        out
    }
    // -----------------------------------------------------------------------
    // Reporting
    // -----------------------------------------------------------------------

    /// The whole state as one canonical string. Two runs with the same string
    /// have the same [`Game::state_hash`].
    pub fn canonical(&self) -> String {
        let mut s = String::new();
        let _ = write!(
            s,
            "seed={};body={};state={:?};cause={:?};x={};y={};vx={};vy={};hd={};fuel={};thr={};wave={};landed={};total={};last={};ticks={};wt={};mult={};rng={};",
            self.seed,
            self.body.name(),
            self.state,
            self.cause,
            self.x,
            self.y,
            self.vx,
            self.vy,
            self.heading,
            self.fuel,
            self.engine_on(),
            self.wave,
            self.waves_landed,
            self.total,
            self.last_points,
            self.ticks,
            self.wave_ticks,
            self.last_mult,
            self.rng.state(),
        );
        for h in &self.terrain.h {
            let _ = write!(s, "{h},");
        }
        for p in self.pads() {
            let _ = write!(s, "{}-{}:{}", p.start, p.end, p.mult);
        }
        s
    }

    /// The pads, for canonicalisation.
    pub fn pads(&self) -> &[Pad] {
        &self.terrain.pads
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
            ("body".into(), self.body.name().to_string()),
            ("ticks".into(), self.ticks.to_string()),
            ("wave".into(), self.wave.to_string()),
            ("waves_landed".into(), self.waves_landed.to_string()),
            ("score".into(), self.score().to_string()),
            ("last_points".into(), self.last_points.to_string()),
            ("last_mult".into(), self.last_mult.to_string()),
            ("fuel".into(), self.fuel_units().to_string()),
            ("x".into(), (self.x / PSCALE).to_string()),
            ("y".into(), (self.y / PSCALE).to_string()),
            ("alt".into(), self.altitude().to_string()),
            ("vx".into(), mps_text(self.vx)),
            ("vy".into(), mps_text(self.vy)),
            ("tilt".into(), self.tilt().to_string()),
            ("heading".into(), self.heading.to_string()),
            ("pads".into(), self.pads().len().to_string()),
            ("best_score".into(), self.best_score.to_string()),
            ("best_waves".into(), self.best_waves.to_string()),
            ("best_seed".into(), self.best_seed.to_string()),
            ("hash".into(), format!("{:016x}", self.state_hash())),
        ];
        if let Some(c) = self.cause {
            kv.push(("cause".into(), format!("{:?}", c).to_lowercase()));
        }
        kv
    }
}

/// m/s as a fixed-point-safe decimal string, 2 dp, sign always shown.
pub fn mps_text(v: i64) -> String {
    let scaled = v * 100 / VSCALE; // hundredths of m/s
    let sign = if scaled < 0 { '-' } else { '+' };
    let a = scaled.abs();
    format!("{sign}{}.{:02}", a / 100, a % 100)
}

/// The plain-text state `--headless` prints: `key=value` lines, then the
/// terrain as ASCII rows. No escapes, diffable, and the shape the gate greps.
pub fn plain_state(g: &Game) -> String {
    let mut s = String::new();
    for (k, v) in g.result_kv() {
        let _ = writeln!(s, "{k}={v}");
    }
    let _ = writeln!(s, "field:");
    for row in 0..FIELD_ROWS {
        let mut line = String::new();
        for col in 0..COLS {
            let ground_row = ground_row(g.terrain.at(col));
            let is_pad = g.terrain.pad_at(col).is_some();
            let probe = row == probe_row(g.y) && col == g.col();
            if probe {
                line.push('O');
            } else if row == ground_row {
                line.push(if is_pad { '=' } else { '#' });
            } else if row > ground_row {
                line.push(if is_pad { '=' } else { '.' });
            } else {
                line.push(' ');
            }
        }
        let _ = writeln!(s, "|{line}|");
    }
    s
}

/// The field row a ground height in metres draws on (row 0 is the top).
pub fn ground_row(h_m: i64) -> usize {
    let rows = FIELD_ROWS as i64 - 1;
    let r = rows - (h_m * FIELD_ROWS as i64 / WORLD_H_M);
    r.clamp(0, FIELD_ROWS as i64 - 1) as usize
}

/// The field row an altitude (in [`PSCALE`] units) draws on.
pub fn probe_row(y: i64) -> usize {
    let rows = FIELD_ROWS as i64 - 1;
    let r = rows - (y * FIELD_ROWS as i64 / (WORLD_H_M * PSCALE));
    r.clamp(0, FIELD_ROWS as i64 - 1) as usize
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
    fn default() -> Self {
        RenderOpts {
            width: REF_WIDTH,
            height: REF_HEIGHT,
            mono: false,
            color: true,
        }
    }
}

/// The key legend, as one line.
pub fn legend(mono: bool) -> String {
    if mono {
        "space thrust  a/d rotate  n new terrain  r restart  q quit".to_string()
    } else {
        "space thrust \u{b7} a/d rotate \u{b7} n new terrain \u{b7} r restart \u{b7} q quit".to_string()
    }
}

/// Glyphs, chosen by the mono policy. Every non-ASCII glyph in the frame goes
/// through this, which is what makes `--mono` output pure ASCII.
struct Glyph {
    box_tl: char,
    box_tr: char,
    box_bl: char,
    box_br: char,
    hline: char,
    vline: char,
    ground: char,
    pad: char,
    flame: char,
    up: char,
    right: char,
    down: char,
    left: char,
    dot: char,
}

const GLYPHS_FANCY: Glyph = Glyph {
    box_tl: '\u{250c}',
    box_tr: '\u{2510}',
    box_bl: '\u{2514}',
    box_br: '\u{2518}',
    hline: '\u{2500}',
    vline: '\u{2502}',
    ground: '\u{2593}',
    pad: '\u{2550}',
    flame: '\u{2591}',
    up: '\u{25b2}',
    right: '\u{25b6}',
    down: '\u{25bc}',
    left: '\u{25c0}',
    dot: '\u{b7}',
};

const GLYPHS_MONO: Glyph = Glyph {
    box_tl: '+',
    box_tr: '+',
    box_bl: '+',
    box_br: '+',
    hline: '-',
    vline: '|',
    ground: '#',
    pad: '=',
    flame: '*',
    up: '^',
    right: '>',
    down: 'v',
    left: '<',
    dot: '-',
};

fn glyphs(mono: bool) -> &'static Glyph {
    if mono {
        &GLYPHS_MONO
    } else {
        &GLYPHS_FANCY
    }
}

/// ANSI colour codes. Empty strings when colour is off, so a colour-off frame
/// contains no escape bytes at all and is byte-for-byte ASCII.
struct Paint {
    cyan: String,
    yellow: String,
    green: String,
    dim: String,
    red: String,
    bold: String,
    off: String,
}

fn paint(color: bool) -> Paint {
    if color {
        Paint {
            cyan: "\x1b[36m".into(),
            yellow: "\x1b[33m".into(),
            green: "\x1b[32m".into(),
            dim: "\x1b[2m".into(),
            red: "\x1b[31m".into(),
            bold: "\x1b[1m".into(),
            off: "\x1b[0m".into(),
        }
    } else {
        Paint {
            cyan: String::new(),
            yellow: String::new(),
            green: String::new(),
            dim: String::new(),
            red: String::new(),
            bold: String::new(),
            off: String::new(),
        }
    }
}

/// Visible cell count: ANSI escapes occupy no cell, so width is measured on
/// the text that is actually drawn.
pub fn visible_len(s: &str) -> usize {
    let mut n = 0;
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c == '\x1b' {
            // Skip CSI: ESC [ params... final byte
            if it.next() == Some('[') {
                for c in it.by_ref() {
                    if matches!(c, '@'..='~') {
                        break;
                    }
                }
            }
        } else {
            n += 1;
        }
    }
    n
}

/// Pad a line to exactly `width` visible cells. A line that already fits is
/// emitted verbatim (colour and all); only an over-long line is stripped and
/// clipped. This is what keeps colour in the frame: clipping the stripped copy
/// and re-emitting it would paint every row plain.
fn fit(s: &str, width: usize) -> String {
    let len = visible_len(s);
    if len <= width {
        let mut out = String::with_capacity(s.len() + width - len);
        out.push_str(s);
        for _ in len..width {
            out.push(' ');
        }
        out
    } else {
        // Clip on visible cells, keeping the escape prefixes that are still
        // open: walk the source and stop at the width-th visible cell.
        let mut out = String::with_capacity(width + 8);
        let mut n = 0;
        let mut it = s.chars().peekable();
        while let Some(c) = it.next() {
            if c == '\x1b' {
                out.push(c);
                if it.peek() == Some(&'[') {
                    out.push('[');
                    it.next();
                    for c in it.by_ref() {
                        out.push(c);
                        if matches!(c, '@'..='~') {
                            break;
                        }
                    }
                }
                continue;
            }
            if n >= width {
                break;
            }
            out.push(c);
            n += 1;
        }
        out
    }
}

impl Game {
    /// The HUD panel: the instrument strip, as lines of plain text.
    fn hud_lines(&self, g: &Glyph, p: &Paint) -> Vec<String> {
        let flame = if self.engine_on() {
            format!("{}{flame}{flame}{off}", p.yellow, flame = g.flame, off = p.off)
        } else {
            format!("{}--{}", p.dim, p.off)
        };
        let mut v = Vec::new();
        v.push(format!(
            "{}ALT{:>6} m{}",
            p.cyan,
            self.altitude(),
            p.off
        ));
        v.push(format!(
            "{}VX {} m/s{}",
            p.cyan,
            mps_text(self.vx),
            p.off
        ));
        v.push(format!(
            "{}VY {} m/s{}",
            p.cyan,
            mps_text(self.vy),
            p.off
        ));
        v.push(format!(
            "{}FUEL{:>5} {}",
            p.cyan,
            self.fuel_units(),
            if self.fuel_units() < 25 {
                format!("{}LOW{}", p.red, p.off)
            } else {
                format!("{}ok{}", p.green, p.off)
            }
        ));
        v.push(format!(
            "{}TILT{:>5} deg{}",
            p.cyan,
            self.tilt(),
            p.off
        ));
        v.push(format!("{}THRUST {}", p.cyan, flame));
        v.push(format!(
            "{}WAVE {:>3}  {}",
            p.cyan,
            self.wave,
            match self.state {
                State::Flying => format!("{}in air{}", p.cyan, p.off),
                State::Landed => format!("{}LANDED{}", p.green, p.off),
                State::Crashed => format!("{}CRASH{}", p.red, p.off),
            }
        ));
        v
    }

    /// The score strip.
    fn score_lines(&self, g: &Glyph, p: &Paint) -> Vec<String> {
        let mut v = Vec::new();
        v.push(format!(
            "{}score {:>6}{}  wave {}{}  best {:>6}{}",
            p.bold,
            self.score(),
            p.off,
            self.waves_landed,
            p.off,
            self.best_score,
            p.dim
        ));
        let reason = match self.state {
            State::Flying => {
                let target = self.terrain.best_pad(self.x / PSCALE);
                format!(
                    "{}{} target {}x, pad at col {}{}",
                    p.dim,
                    g.dot,
                    target.map(|t| t.mult).unwrap_or(0),
                    target.map(|t| t.centre()).unwrap_or(0),
                    p.off
                )
            }
            State::Landed => format!("{}LANDED +{}{}", p.green, self.last_points, p.off),
            State::Crashed => format!(
                "{}{}CRASHED: {}{}",
                p.red,
                g.ground,
                self.cause.map(|c| c.text()).unwrap_or("crashed"),
                p.off
            ),
        };
        v.push(format!("{}{}", reason, p.off));
        v
    }

    /// The terrain view, `cols` columns by [`FIELD_ROWS`] rows, as lines
    /// without borders.
    fn field_lines(&self, cols: usize, g: &Glyph, p: &Paint) -> Vec<String> {
        let probe_row = probe_row(self.y);
        let probe_col = self.col();
        let mut out = Vec::with_capacity(FIELD_ROWS);
        for row in 0..FIELD_ROWS {
            let mut line = String::new();
            for col in 0..cols {
                if row == probe_row && col == probe_col {
                    let ch = match self.tilt() {
                        t if t >= 135 => g.down,
                        t if t >= 45 => {
                            if ((self.heading % DEG_TURN) + DEG_TURN) % DEG_TURN <= 180 {
                                g.right
                            } else {
                                g.left
                            }
                        }
                        _ => g.up,
                    };
                    let _ = write!(line, "{}{ch}{}", p.yellow, p.off);
                    continue;
                }
                let h = self.terrain.at(col);
                let grow = ground_row(h);
                let is_pad = self.terrain.pad_at(col).is_some();
                if row >= grow {
                    let _ = write!(
                        line,
                        "{}{ch}{off}",
                        if is_pad { p.green.clone() } else { p.dim.clone() },
                        ch = if is_pad { g.pad } else { g.ground },
                        off = p.off
                    );
                } else if row == probe_row + 1
                    && col == probe_col
                    && self.engine_on()
                    && self.tilt() < 45
                {
                    let _ = write!(
                        line,
                        "{}{flame}{off}",
                        p.yellow,
                        flame = g.flame,
                        off = p.off
                    );
                } else {
                    line.push(' ');
                }
            }
            out.push(line);
        }
        out
    }

    /// The whole screen, as one string: `height` lines, each exactly `width`
    /// visible cells, joined by `\r\n`. Pure function of `(state, opts)`.
    ///
    /// Degradation, in order: the panel moves above the field when the window
    /// is narrower than the field plus the panel, then the title, then the
    /// borders, then the legend, then the field is clipped from the top (the
    /// ground is never clipped).
    pub fn render(&self, opts: RenderOpts) -> String {
        let g = glyphs(opts.mono);
        let p = paint(opts.color);
        let w = opts.width.max(1);
        let h = opts.height.max(1);

        let panel_w = 18usize;
        let full_panel = w >= COLS + 2 + panel_w;
        let cols = if full_panel {
            COLS
        } else {
            (w.saturating_sub(2)).min(COLS).max(1)
        };
        let bordered = w >= cols + 2 && h >= FIELD_ROWS + 2;
        let title_w = w >= 40;
        let legend_w = w >= legend(opts.mono).chars().count() + 2;

        let field = self.field_lines(cols, g, &p);
        let hud = self.hud_lines(g, &p);
        let score = self.score_lines(g, &p);
        // The panel strip beside the field: instruments, a gap, then the score
        // and the reason. One vector, indexed by field row, so a line can never
        // repeat down the column.
        let mut side = hud.clone();
        side.push(String::new());
        side.extend(score.iter().map(|l| l.trim_end().to_string()));

        // The screen, as (drop priority, text). When the window is smaller
        // than the content, the lowest priority line goes first: the title,
        // then the legend, then the borders, then the score strip, then the
        // instrument lines from the bottom of the panel up, and only then the
        // field, from its top row down. The ground is the last thing in the
        // list, so the ground is the last thing to go.
        let mut lines: Vec<(u32, String)> = Vec::new();
        if title_w {
            lines.push((
                1,
                format!(
                    "{}Ice Lander{} {} wave {}  fuel {}{}",
                    p.bold,
                    p.off,
                    p.cyan,
                    self.wave,
                    self.fuel_units(),
                    p.off
                ),
            ));
        }
        if !full_panel {
            // Narrow: the instruments move above the field, pushed in reverse,
            // so what goes first is the bottom of the panel.
            for (i, l) in hud.iter().rev().enumerate() {
                lines.push((5 + i as u32, l.clone()));
            }
            for l in score.iter().rev() {
                lines.push((4, l.clone()));
            }
        }
        if bordered {
            let mut top = String::new();
            top.push(g.box_tl);
            for _ in 0..cols {
                top.push(g.hline);
            }
            top.push(g.box_tr);
            lines.push((3, top));
        }
        for (i, row) in field.iter().enumerate() {
            let mut line = String::new();
            if bordered {
                line.push(g.vline);
            }
            line.push_str(row);
            if bordered {
                line.push(g.vline);
            }
            if full_panel {
                line.push(' ');
                line.push_str(side.get(i).map(|s| s.as_str()).unwrap_or(""));
            }
            lines.push((12 + i as u32, line));
        }
        if bordered {
            let mut bot = String::new();
            bot.push(g.box_bl);
            for _ in 0..cols {
                bot.push(g.hline);
            }
            bot.push(g.box_br);
            lines.push((3, bot));
        }
        if full_panel {
            for l in &score {
                lines.push((4, l.clone()));
            }
        }
        if legend_w {
            lines.push((2, format!("{}{}{}", p.dim, legend(opts.mono), p.off)));
        }

        while lines.len() > h {
            // Drop the lowest priority line; a tie goes to the topmost one.
            let mut at = 0usize;
            for (i, (pr, _)) in lines.iter().enumerate() {
                if *pr < lines[at].0 {
                    at = i;
                }
            }
            lines.remove(at);
        }
        let mut lines: Vec<String> = lines.into_iter().map(|(_, l)| l).collect();
        // Pad out: a frame is exactly `height` rows, so a window taller than
        // the content gets blank rows below it and the cursor never walks off.
        while lines.len() < h {
            lines.push(String::new());
        }

        let mut out = String::new();
        for (i, l) in lines.iter().enumerate() {
            // Every row is padded to exactly the window width, so a shorter
            // row can never leave a ghost tail and no erase-to-end is needed.
            // That is what makes a frame pure ASCII when --mono and colour off
            // are on: the only escapes in a frame are colour.
            let _ = write!(out, "{}", fit(l, w));
            if i + 1 < lines.len() {
                out.push_str("\r\n");
            }
        }
        out
    }
}

/// The frame as a plain function of state, with the default window.
pub fn frame(g: &Game) -> String {
    g.render(RenderOpts::default())
}

/// The program around the rules: the command line, the record file, the
/// headless harness and the window. Nothing above this line depends on it.
pub mod cli;
