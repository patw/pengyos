//! pengy-rogue — the pure rules core of **Ice Rogue**.
//!
//! Rogue (1980) — the original dungeon crawl, the game that named the genre and
//! the reason every terminal had a `curses`. Five floors down, one penguin,
//! permanent death, and at the bottom The Thaw.
//!
//! EVERY rule, the map generator, the field of view, the combat resolution and
//! the renderer live in this file, and this file is **pure**: no files, no
//! keyboard, no screen, no clock, no threads, no process, no system entropy.
//! The only randomness is the seeded xorshift64* below, so a run is a function
//! of `(seed, script)`: the same seed and the same keys produce the same dungeon,
//! the same combat rolls and the same state hash, byte for byte. The terminal
//! front-end (`src/main.rs`) is the only thing that touches the outside world.
//!
//! Coordinate convention, everywhere: `x` is the column, `y` is the row, `(0,0)`
//! is the top-left tile of the map.

use std::fmt::Write as _;

// ---------------------------------------------------------------------------
// Seeded randomness — xorshift64*, hand-written. No `rand`, no system entropy.
// ---------------------------------------------------------------------------

/// A seeded xorshift64* generator. Every run takes `--seed N` and is
/// reproducible bit for bit: the same seed digs the same five floors, rolls the
/// same combat, spawns the same monsters.
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

    /// Restore a generator state read back from a saved position.
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

    /// Next value in `0..n`. `n` must be > 0.
    pub fn below(&mut self, n: u64) -> u64 {
        assert!(n > 0, "Rng::below(0)");
        self.next_u64() % n
    }

    /// Next value in `lo..=hi`.
    pub fn range(&mut self, lo: u64, hi: u64) -> u64 {
        assert!(hi >= lo, "Rng::range with hi < lo");
        lo + self.below(hi - lo + 1)
    }
}

// ---------------------------------------------------------------------------
// The world, as numbers
// ---------------------------------------------------------------------------

/// The reference window the layout is designed for.
pub const REF_WIDTH: usize = 80;
pub const REF_HEIGHT: usize = 24;

/// The dungeon floor: 54x15 tiles fits an 80x24 window with a 24-column status
/// panel beside it, which is the whole point of the size.
pub const MAP_W: usize = 54;
pub const MAP_H: usize = 15;
/// The map plus its border, and the panel, is what a full frame needs.
pub const PANEL_W: usize = 24;
pub const PANEL_MIN_WIDTH: usize = MAP_W + 2 + PANEL_W; // 80
pub const BORDER_MIN_WIDTH: usize = MAP_W + 2; // 56
pub const BORDER_MIN_HEIGHT: usize = MAP_H + 2; // 17
pub const TITLE_MIN_HEIGHT: usize = MAP_H + 4; // 19
pub const STATUS_MIN_HEIGHT: usize = MAP_H + 5; // 20
pub const LEGEND_MIN_HEIGHT: usize = MAP_H + 7; // 22

/// Five floors down. Floor 5 holds The Thaw.
pub const FLOORS: usize = 5;
/// Field-of-view radius, in tiles (Chebyshev), with line of sight.
pub const FOV_RADIUS: i32 = 7;

/// The penguin's starting stats: 12 HP, attack 1d4+1, defence 0.
pub const START_HP: i32 = 12;
pub const START_MAX_HP: i32 = 12;
pub const START_WEAPON: i32 = 0;
pub const START_DEFENCE: i32 = 0;

/// The die the penguin swings: 1d4+1, plus the weapon bonus, minus the target's
/// defence, floored at 0.
pub const ATTACK_DIE_FACES: i32 = 4;
pub const ATTACK_DIE_BONUS: i32 = 1;

/// A fish heals this much, capped at the maximum.
pub const FISH_HEAL: i32 = 3;
/// A parka is worth this much defence.
pub const PARKA_DEFENCE: i32 = 1;
/// A gold pile is worth 1..=5 points.
pub const GOLD_MIN: i32 = 1;
pub const GOLD_MAX: i32 = 5;

/// Points per floor reached, and per kill, in the score formula
/// `floor * 100 + gold + kills`.
pub const SCORE_PER_FLOOR: i64 = 100;
pub const SCORE_PER_KILL: i64 = 1;

/// Frame cadence for the front-end. The game is turn based: this is how often
/// the screen is repainted, not how often anything happens.
pub const TICK_MS: u64 = 110;

/// Terrain bytes. Keeping them as the glyphs they render as makes the map, the
/// saved positions and the frame one consistent alphabet.
pub const WALL: u8 = b'#';
pub const FLOOR: u8 = b'.';
pub const STAIRS: u8 = b'>';

/// Item glyphs, exactly as the spec lists them.
pub const ITEM_FISH: char = '!';
pub const ITEM_SCROLL: char = '?';
pub const ITEM_GOLD: char = '$';
pub const ITEM_PARKA: char = '+';
pub const ITEM_GLYPHS: [char; 4] = [ITEM_FISH, ITEM_SCROLL, ITEM_GOLD, ITEM_PARKA];

/// The player glyph, and the wall/floor/stairs glyphs.
pub const PLAYER_GLYPH: char = '@';

/// The eight directions: the four cardinals and the four diagonals.
pub const DIRS: [(i32, i32); 8] = [
    (0, -1), // k  up
    (0, 1),  // j  down
    (-1, 0), // h  left
    (1, 0),  // l  right
    (-1, -1), // y  up-left
    (1, -1), // u  up-right
    (-1, 1), // b  down-left
    (1, 1),  // n  down-right
];

/// The key that moves in direction `i`.
pub const DIR_KEYS: [char; 8] = ['k', 'j', 'h', 'l', 'y', 'u', 'b', 'n'];

/// The key legend, quoted by `--help` and by the frame, so the text can never
/// drift from the keys `Game::key` accepts.
pub const LEGEND: &str = "hjkl/yubn move  . wait  g pick up  i inventory  > descend  q quit";

/// The title, quoted by `--help`, the frame and the record.
pub const GAME_NAME: &str = "Ice Rogue";
pub const GAME_SUBTITLE: &str = "Rogue (1980) — the original dungeon crawl";

// ---------------------------------------------------------------------------
// Monsters
// ---------------------------------------------------------------------------

/// The four inhabitants of the ice. Stats exactly as the spec lists them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MonsterKind {
    Rat,
    IceCrab,
    Wolf,
    Thaw,
}

pub const MONSTER_KINDS: [MonsterKind; 4] = [
    MonsterKind::Rat,
    MonsterKind::IceCrab,
    MonsterKind::Wolf,
    MonsterKind::Thaw,
];

impl MonsterKind {
    pub fn glyph(self) -> char {
        match self {
            MonsterKind::Rat => 'r',
            MonsterKind::IceCrab => 'c',
            MonsterKind::Wolf => 'w',
            MonsterKind::Thaw => 'T',
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            MonsterKind::Rat => "rat",
            MonsterKind::IceCrab => "ice crab",
            MonsterKind::Wolf => "wolf",
            MonsterKind::Thaw => "The Thaw",
        }
    }

    /// Hit points it spawns with.
    pub fn hp(self) -> i32 {
        match self {
            MonsterKind::Rat => 2,
            MonsterKind::IceCrab => 4,
            MonsterKind::Wolf => 6,
            MonsterKind::Thaw => 20,
        }
    }

    /// Flat damage it lands on you, before your defence.
    pub fn damage(self) -> i32 {
        match self {
            MonsterKind::Rat => 1,
            MonsterKind::IceCrab => 2,
            MonsterKind::Wolf => 3,
            MonsterKind::Thaw => 4,
        }
    }

    /// Monsters have no armour: the attack roll subtracts 0.
    pub fn defence(self) -> i32 {
        0
    }

    pub fn from_glyph(c: char) -> Option<MonsterKind> {
        MONSTER_KINDS.iter().copied().find(|k| k.glyph() == c)
    }
}

/// A live monster. `hp` is the current hit points, so a saved position can
/// carry a wounded boss.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Monster {
    pub kind: MonsterKind,
    pub x: usize,
    pub y: usize,
    pub hp: i32,
}

/// An item on a tile. One item per tile, enforced at placement time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Item {
    pub x: usize,
    pub y: usize,
    pub glyph: char,
    /// Gold piles carry their value; every other item uses 0.
    pub value: i32,
}

/// A tile coordinate.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Pos {
    pub x: usize,
    pub y: usize,
}

impl Pos {
    pub fn new(x: usize, y: usize) -> Pos {
        Pos { x, y }
    }
}

/// The flat index of a tile.
pub const fn idx(x: usize, y: usize) -> usize {
    y * MAP_W + x
}

/// Is `(x,y)` inside the map?
pub const fn in_bounds(x: i32, y: i32) -> bool {
    x >= 0 && y >= 0 && (x as usize) < MAP_W && (y as usize) < MAP_H
}

// ---------------------------------------------------------------------------
// Rooms, corridors, and the proof that a floor is connected
// ---------------------------------------------------------------------------

/// A room: its top-left tile and its interior size. Rooms never share a wall —
/// two rooms are always separated by at least one full tile of rock, which is
/// what makes the corridors the only way between them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Room {
    pub x: usize,
    pub y: usize,
    pub w: usize,
    pub h: usize,
}

/// Room size limits from the spec: 4x4 .. 10x6.
pub const ROOM_W_MIN: usize = 4;
pub const ROOM_W_MAX: usize = 10;
pub const ROOM_H_MIN: usize = 4;
pub const ROOM_H_MAX: usize = 6;
/// Rooms per floor: 4 .. 9.
pub const ROOMS_MIN: usize = 4;
pub const ROOMS_MAX: usize = 9;

impl Room {
    pub fn cx(self) -> usize {
        self.x + self.w / 2
    }
    pub fn cy(self) -> usize {
        self.y + self.h / 2
    }
    pub fn contains(self, x: usize, y: usize) -> bool {
        x >= self.x && x < self.x + self.w && y >= self.y && y < self.y + self.h
    }
    /// Two rooms overlap when they are closer than one tile of rock apart.
    pub fn overlaps(self, o: Room) -> bool {
        !(self.x > o.x + o.w + 1
            || o.x > self.x + self.w + 1
            || self.y > o.y + o.h + 1
            || o.y > self.y + self.h + 1)
    }
}

/// A generated floor: the terrain, what is on it, and the two special tiles.
#[derive(Clone, Debug)]
pub struct Floor {
    pub map: Vec<u8>,
    pub items: Vec<Item>,
    pub monsters: Vec<Monster>,
    pub rooms: Vec<Room>,
    /// Where the penguin arrives from the floor above.
    pub entry: Pos,
    /// The stairs down, in the last room.
    pub stairs: Pos,
}

/// 4-connected flood fill over non-wall tiles, from `start`. Returns the set of
/// reachable tiles. This is the connectivity proof: the stairs must be in it.
pub fn flood_fill(map: &[u8], start: Pos) -> Vec<bool> {
    let mut seen = vec![false; MAP_W * MAP_H];
    let mut stack = vec![start];
    seen[idx(start.x, start.y)] = true;
    while let Some(p) = stack.pop() {
        for (dx, dy) in DIRS {
            if dx == 0 && dy == 0 {
                continue;
            }
            // 4-connected: a diagonal is not a walk between two tiles of rock.
            if dx != 0 && dy != 0 {
                continue;
            }
            let (nx, ny) = (p.x as i32 + dx, p.y as i32 + dy);
            if !in_bounds(nx, ny) {
                continue;
            }
            let i = idx(nx as usize, ny as usize);
            if seen[i] || map[i] == WALL {
                continue;
            }
            seen[i] = true;
            stack.push(Pos::new(nx as usize, ny as usize));
        }
    }
    seen
}

/// Is a floor fully connected: every room centre, the entry and the stairs in
/// one 4-connected region?
pub fn floor_is_connected(floor: &Floor) -> bool {
    let reach = flood_fill(&floor.map, floor.entry);
    if !reach[idx(floor.stairs.x, floor.stairs.y)] {
        return false;
    }
    floor
        .rooms
        .iter()
        .all(|r| reach[idx(r.cx(), r.cy())])
}

/// Carve a horizontal corridor segment at row `y`, from `x0` to `x1` inclusive.
/// Only rock is carved; an existing floor is left alone.
fn carve_h(map: &mut [u8], y: usize, x0: usize, x1: usize) {
    for x in x0.min(x1)..=x0.max(x1) {
        if x > 0 && x < MAP_W - 1 {
            map[idx(x, y)] = FLOOR;
        }
    }
}

/// Carve a vertical corridor segment at column `x`, from `y0` to `y1` inclusive.
fn carve_v(map: &mut [u8], x: usize, y0: usize, y1: usize) {
    for y in y0.min(y1)..=y0.max(y1) {
        if y > 0 && y < MAP_H - 1 {
            map[idx(x, y)] = FLOOR;
        }
    }
}

/// Pick a free tile inside a room: floor, not the entry, not the stairs, not
/// holding an item or a monster.
fn free_tile(floor: &mut Floor, room: Room, rng: &mut Rng) -> Option<Pos> {
    for _ in 0..24 {
        let x = rng.range(room.x as u64, (room.x + room.w - 1) as u64) as usize;
        let y = rng.range(room.y as u64, (room.y + room.h - 1) as u64) as usize;
        if floor.map[idx(x, y)] != FLOOR {
            continue;
        }
        if (x, y) == (floor.entry.x, floor.entry.y) || (x, y) == (floor.stairs.x, floor.stairs.y) {
            continue;
        }
        if floor.items.iter().any(|i| i.x == x && i.y == y) {
            continue;
        }
        if floor.monsters.iter().any(|m| m.x == x && m.y == y) {
            continue;
        }
        return Some(Pos::new(x, y));
    }
    None
}

/// Generate one floor. The layout (rooms, corridors, entry, stairs) is retried
/// until it is non-overlapping and provably connected, so a returned floor is
/// always a floor you can walk from the entry to the stairs. Items, monsters
/// and the boss are placed on top of a layout that passed.
pub fn gen_floor(rng: &mut Rng, depth: usize) -> Floor {
    assert!((1..=FLOORS).contains(&depth), "gen_floor: depth out of range");
    for _ in 0..512 {
        if let Some(f) = try_layout(rng, depth) {
            return f;
        }
    }
    // Unreachable in practice: the acceptance test over 100 seeds proves every
    // seed lays out in a handful of tries. A fixed, trivially connected floor
    // keeps the generator total rather than a panic.
    fallback_floor(depth)
}

/// One layout attempt: rooms, corridors, entry, stairs, connectivity check.
fn try_layout(rng: &mut Rng, depth: usize) -> Option<Floor> {
    let count = rng.range(ROOMS_MIN as u64, ROOMS_MAX as u64) as usize;
    let mut rooms: Vec<Room> = Vec::new();
    let mut tries = 0;
    while rooms.len() < count && tries < 300 {
        tries += 1;
        let w = rng.range(ROOM_W_MIN as u64, ROOM_W_MAX as u64) as usize;
        let h = rng.range(ROOM_H_MIN as u64, ROOM_H_MAX as u64) as usize;
        let x = rng.range(1, (MAP_W - 2 - w) as u64) as usize;
        let y = rng.range(1, (MAP_H - 2 - h) as u64) as usize;
        let r = Room { x, y, w, h };
        if r.x + r.w > MAP_W - 1 || r.y + r.h > MAP_H - 1 {
            continue;
        }
        if rooms.iter().any(|o| o.overlaps(r)) {
            continue;
        }
        rooms.push(r);
    }
    if rooms.len() < ROOMS_MIN {
        return None;
    }

    let mut map = vec![WALL; MAP_W * MAP_H];
    for r in &rooms {
        for y in r.y..r.y + r.h {
            for x in r.x..r.x + r.w {
                map[idx(x, y)] = FLOOR;
            }
        }
    }
    // L-shaped corridors joining each room to the next, in room order. The
    // bend order is the coin flip, which is what makes the corridors look
    // hand-drawn instead of a grid.
    for i in 0..rooms.len() - 1 {
        let (a, b) = (rooms[i], rooms[i + 1]);
        if rng.below(2) == 0 {
            carve_h(&mut map, a.cy(), a.cx(), b.cx());
            carve_v(&mut map, b.cx(), a.cy(), b.cy());
        } else {
            carve_v(&mut map, a.cy(), a.cx(), b.cx());
            carve_h(&mut map, b.cy(), a.cx(), b.cx());
        }
    }

    let entry = rooms[0].cx();
    let entry = Pos::new(entry, rooms[0].cy());
    let stairs = Pos::new(rooms[rooms.len() - 1].cx(), rooms[rooms.len() - 1].cy());
    map[idx(stairs.x, stairs.y)] = STAIRS;

    let mut floor = Floor {
        map,
        items: Vec::new(),
        monsters: Vec::new(),
        rooms,
        entry,
        stairs,
    };
    if !floor_is_connected(&floor) {
        return None;
    }
    place_contents(&mut floor, rng, depth);
    Some(floor)
}

/// Place the items, the monsters and (on floor 5) The Thaw.
fn place_contents(floor: &mut Floor, rng: &mut Rng, depth: usize) {
    // Items: one per room, two thirds of rooms get one. Never on the entry,
    // the stairs, a monster or another item — one item per tile.
    for i in 0..floor.rooms.len() {
        if rng.below(3) == 0 {
            continue;
        }
        let Some(p) = free_tile(floor, floor.rooms[i], rng) else {
            continue;
        };
        let glyph = match rng.below(100) {
            0..=44 => ITEM_GOLD,
            45..=69 => ITEM_FISH,
            70..=84 => ITEM_SCROLL,
            _ => ITEM_PARKA,
        };
        let value = if glyph == ITEM_GOLD {
            rng.range(GOLD_MIN as u64, GOLD_MAX as u64) as i32
        } else {
            0
        };
        floor.items.push(Item {
            x: p.x,
            y: p.y,
            glyph,
            value,
        });
    }

    // Monsters: more of them, and meaner ones, the deeper you dig. They never
    // spawn in the entry room, so arriving on a floor is never arriving dead.
    let count = 2 + depth + rng.below(3) as usize;
    let pool = monster_pool(depth);
    for _ in 0..count {
        let kind = pool[rng.below(pool.len() as u64) as usize];
        let room = floor.rooms[rng.below(floor.rooms.len() as u64) as usize];
        if room.contains(floor.entry.x, floor.entry.y) {
            continue;
        }
        if let Some(p) = free_tile(floor, room, rng) {
            floor.monsters.push(Monster {
                kind,
                x: p.x,
                y: p.y,
                hp: kind.hp(),
            });
        }
    }

    // The boss lives on floor 5 and nowhere else.
    if depth == FLOORS {
        let boss_room = floor.rooms[floor.rooms.len() - 1];
        let p = free_tile(floor, boss_room, rng).unwrap_or(floor.stairs);
        floor.monsters.push(Monster {
            kind: MonsterKind::Thaw,
            x: p.x.max(1).min(MAP_W - 2),
            y: p.y.max(1).min(MAP_H - 2),
            hp: MonsterKind::Thaw.hp(),
        });
    }
}

/// Which monsters live at which depth.
pub fn monster_pool(depth: usize) -> Vec<MonsterKind> {
    match depth {
        1 => vec![
            MonsterKind::Rat,
            MonsterKind::Rat,
            MonsterKind::Rat,
            MonsterKind::IceCrab,
        ],
        2 => vec![
            MonsterKind::Rat,
            MonsterKind::Rat,
            MonsterKind::IceCrab,
            MonsterKind::IceCrab,
            MonsterKind::Wolf,
        ],
        3 => vec![
            MonsterKind::Rat,
            MonsterKind::IceCrab,
            MonsterKind::IceCrab,
            MonsterKind::Wolf,
            MonsterKind::Wolf,
        ],
        _ => vec![
            MonsterKind::IceCrab,
            MonsterKind::Wolf,
            MonsterKind::Wolf,
            MonsterKind::Wolf,
        ],
    }
}

/// A hand-made floor used only if 512 layout attempts all fail, which the
/// 100-seed acceptance test proves never happens.
fn fallback_floor(depth: usize) -> Floor {
    let mut map = vec![WALL; MAP_W * MAP_H];
    for y in 2..MAP_H - 2 {
        for x in 2..MAP_W - 2 {
            map[idx(x, y)] = FLOOR;
        }
    }
    let entry = Pos::new(3, 3);
    let stairs = Pos::new(MAP_W - 4, MAP_H - 4);
    map[idx(stairs.x, stairs.y)] = STAIRS;
    let mut monsters = Vec::new();
    if depth == FLOORS {
        monsters.push(Monster {
            kind: MonsterKind::Thaw,
            x: MAP_W - 6,
            y: MAP_H - 4,
            hp: MonsterKind::Thaw.hp(),
        });
    }
    Floor {
        map,
        items: Vec::new(),
        monsters,
        rooms: vec![
            Room {
                x: 2,
                y: 2,
                w: 6,
                h: 5,
            },
            Room {
                x: MAP_W - 8,
                y: MAP_H - 7,
                w: 6,
                h: 5,
            },
        ],
        entry,
        stairs,
    }
}

// ---------------------------------------------------------------------------
// Field of view — radius 7 with line of sight
// ---------------------------------------------------------------------------

/// Bresenham line of sight between two tiles. A wall blocks the view; the
/// endpoint tiles never block themselves, so you always see the wall you are
/// standing next to.
pub fn line_of_sight(map: &[u8], x0: usize, y0: usize, x1: usize, y1: usize) -> bool {
    let (mut x, mut y) = (x0 as i32, y0 as i32);
    let (tx, ty) = (x1 as i32, y1 as i32);
    let dx = (tx - x).abs();
    let dy = -(ty - y).abs();
    let sx = if x < tx { 1 } else { -1 };
    let sy = if y < ty { 1 } else { -1 };
    let mut err = dx + dy;
    loop {
        if x == tx && y == ty {
            return true;
        }
        let e2 = 2 * err;
        if e2 >= dy {
            err += dy;
            x += sx;
        }
        if e2 <= dx {
            err += dx;
            y += sy;
        }
        if x == tx && y == ty {
            return true;
        }
        if !in_bounds(x, y) {
            return false;
        }
        if map[idx(x as usize, y as usize)] == WALL {
            return false;
        }
    }
}

/// The tiles within `FOV_RADIUS` (Chebyshev) of `(px,py)` that the penguin can
/// see. The radius is a square: a corridor you look straight down is visible to
/// its end, which is what a 7-tile radius has to mean for a game in a box.
pub fn compute_fov(map: &[u8], px: usize, py: usize) -> Vec<bool> {
    let mut vis = vec![false; MAP_W * MAP_H];
    vis[idx(px, py)] = true;
    for y in 0..MAP_H {
        for x in 0..MAP_W {
            let d = (x as i32 - px as i32).abs().max((y as i32 - py as i32).abs());
            if d > FOV_RADIUS {
                continue;
            }
            if line_of_sight(map, px, py, x, y) {
                vis[idx(x, y)] = true;
            }
        }
    }
    vis
}

/// Can a monster at `(x,y)` see the penguin? Same radius, same walls, so a
/// wolf around a corner is wandering, not hunting.
pub fn monster_sees_player(map: &[u8], mx: usize, my: usize, px: usize, py: usize) -> bool {
    let d = (mx as i32 - px as i32).abs().max((my as i32 - py as i32).abs());
    d <= FOV_RADIUS && line_of_sight(map, mx, my, px, py)
}

// ---------------------------------------------------------------------------
// The game
// ---------------------------------------------------------------------------

/// The three states of a run. Permadeath: `Dead` and `Win` are terminal, there
/// is no save and no undo.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Play,
    Dead,
    Win,
}

/// The whole run. Every field here is part of the state hash.
pub struct Game {
    pub seed: u64,
    pub rng: Rng,
    /// 1 ..= 5.
    pub floor: usize,
    pub map: Vec<u8>,
    pub items: Vec<Item>,
    pub monsters: Vec<Monster>,
    pub rooms: Vec<Room>,
    pub entry: Pos,
    pub stairs: Pos,
    pub player: Pos,

    pub hp: i32,
    pub max_hp: i32,
    /// The weapon bonus in the attack roll. The ice has no swords: 0.
    pub weapon: i32,
    pub defence: i32,
    pub gold: i32,
    pub kills: i32,
    /// Player actions taken. Every one of them advanced the monsters.
    pub turns: u64,
    /// Elapsed wall time, owned by the front-end; never in the hash.
    pub ms: u64,
    pub state: State,
    /// Tiles ever seen (the dim layer), and the tiles seen right now.
    pub seen: Vec<bool>,
    pub visible: Vec<bool>,
    pub message: String,
    /// The glyphs picked up, in order: the inventory `i` prints.
    pub inventory: Vec<char>,
    /// Floors descended to, i.e. the deepest floor reached.
    pub deepest: usize,
    /// The last direction moved, so a held key keeps walking.
    pub last_dir: Option<usize>,
    /// The tile the penguin came from, so the demo player cannot oscillate.
    pub came_from: Option<Pos>,

    // The persisted record, filled in by the front-end. In the frame, not in
    // the hash: it is history, not the position.
    pub best_score: u64,
    pub best_floor: u64,
    pub best_kills: u64,
    pub best_games: u64,
    pub best_wins: u64,
    pub best_seed: u64,
}

impl Game {
    /// Dig a new run: floor 1 of `seed`, the penguin at the entry, the view
    /// computed. Nothing here reads a clock, a file or the environment.
    pub fn new(seed: u64) -> Game {
        let mut rng = Rng::new(seed);
        let floor = gen_floor(&mut rng, 1);
        Game::from_floor(seed, rng, floor, 1, "You enter the ice. Floor 1.")
    }

    fn from_floor(seed: u64, rng: Rng, floor: Floor, depth: usize, msg: &str) -> Game {
        let mut g = Game {
            seed,
            rng,
            floor: depth,
            map: floor.map,
            items: floor.items,
            monsters: floor.monsters,
            rooms: floor.rooms,
            entry: floor.entry,
            stairs: floor.stairs,
            player: floor.entry,
            hp: START_HP,
            max_hp: START_MAX_HP,
            weapon: START_WEAPON,
            defence: START_DEFENCE,
            gold: 0,
            kills: 0,
            turns: 0,
            ms: 0,
            state: State::Play,
            seen: vec![false; MAP_W * MAP_H],
            visible: vec![false; MAP_W * MAP_H],
            message: msg.to_string(),
            inventory: Vec::new(),
            deepest: depth,
            last_dir: None,
            came_from: None,
            best_score: 0,
            best_floor: 0,
            best_kills: 0,
            best_games: 0,
            best_wins: 0,
            best_seed: 0,
        };
        g.refresh_fov();
        g
    }

    /// Advance the frame clock by one tick. The game is turn based — nothing
    /// in the rules happens here. The front-end calls it once per repaint so
    /// the panel's `time` is the wall clock, and it is deliberately *not* part
    /// of the state hash: a replay is a script, not a stopwatch.
    pub fn tick(&mut self) {
        self.ms += TICK_MS;
    }

    /// Recompute the view and mark everything visible as seen.
    pub fn refresh_fov(&mut self) {
        self.visible = compute_fov(&self.map, self.player.x, self.player.y);
        for i in 0..self.map.len() {
            if self.visible[i] {
                self.seen[i] = true;
            }
        }
    }

    /// The score formula, exactly: `floor * 100 + gold + kills`.
    pub fn score(&self) -> i64 {
        self.floor as i64 * SCORE_PER_FLOOR
            + self.gold as i64 * 1
            + self.kills as i64 * SCORE_PER_KILL
    }

    /// The tile at `(x,y)`.
    pub fn tile(&self, x: usize, y: usize) -> u8 {
        self.map[idx(x, y)]
    }

    /// The monster at `(x,y)`, if any.
    pub fn monster_at(&self, x: usize, y: usize) -> Option<usize> {
        self.monsters.iter().position(|m| m.x == x && m.y == y)
    }

    /// The item at `(x,y)`, if any.
    pub fn item_at(&self, x: usize, y: usize) -> Option<usize> {
        self.items.iter().position(|i| i.x == x && i.y == y)
    }

    /// Can the penguin stand on this tile? Walls are the only thing that stops
    /// you; monsters are attacked, not walked through.
    pub fn walkable(&self, x: usize, y: usize) -> bool {
        self.map[idx(x, y)] != WALL
    }

    /// The attack roll: `1d4 + 1 + weapon - target defence`, floored at 0.
    /// Rolls the die, so this consumes the stream: a run is its seed.
    pub fn player_attack_roll(&mut self, target_defence: i32) -> i32 {
        let die = self.rng.below(ATTACK_DIE_FACES as u64) as i32 + 1;
        (die + ATTACK_DIE_BONUS + self.weapon - target_defence).max(0)
    }

    /// Damage a monster lands on the penguin: its flat damage minus defence,
    /// floored at 0. No die: the spec gives monsters fixed damage, which is
    /// what makes a fight solvable in your head.
    pub fn monster_damage(&self, kind: MonsterKind) -> i32 {
        (kind.damage() - self.defence).max(0)
    }

    // -- the player's turn --------------------------------------------------

    /// Handle one keypress. Returns `true` when the game asked to quit.
    ///
    /// A key that costs a turn advances the turn and then every monster acts.
    /// A key that costs nothing (the inventory, a bump into a wall) does not:
    /// the ice does not move when you look at your pack.
    pub fn key(&mut self, c: char) -> bool {
        match c {
            'q' | 'Q' => return true,
            _ => {}
        }
        if self.state != State::Play {
            return false;
        }
        match c {
            'h' | 'j' | 'k' | 'l' | 'y' | 'u' | 'b' | 'n' => self.try_move(c),
            '.' => self.end_turn("You wait."),
            'g' | ',' => self.pick_up(),
            'i' => {
                self.message = self.inventory_text();
                return false;
            }
            '>' => self.descend(),
            _ => return false,
        }
        false
    }

    /// The key for direction index `i`.
    pub fn dir_key(i: usize) -> char {
        DIR_KEYS[i]
    }

    /// The direction index for a key, `None` for a non-movement key.
    pub fn dir_of(c: char) -> Option<usize> {
        match c {
            'k' => Some(0),
            'j' => Some(1),
            'h' => Some(2),
            'l' => Some(3),
            'y' => Some(4),
            'u' => Some(5),
            'b' => Some(6),
            'n' => Some(7),
            _ => None,
        }
    }

    /// Step one tile. Bumping a monster attacks it; bumping a wall is free.
    fn try_move(&mut self, c: char) {
        let Some(i) = Self::dir_of(c) else {
            return;
        };
        let (dx, dy) = DIRS[i];
        let nx = self.player.x as i32 + dx;
        let ny = self.player.y as i32 + dy;
        if !in_bounds(nx, ny) {
            self.message = "The ice does not end here.".to_string();
            return;
        }
        let (x, y) = (nx as usize, ny as usize);
        if let Some(mi) = self.monster_at(x, y) {
            self.attack(mi);
            return;
        }
        if !self.walkable(x, y) {
            self.message = "You hit the wall.".to_string();
            return;
        }
        self.came_from = Some(self.player);
        self.last_dir = Some(i);
        self.player = Pos::new(x, y);
        let glyph = self.tile(x, y);
        if let Some(ii) = self.item_at(x, y) {
            let g = self.items[ii].glyph;
            self.message = format!("You see {}, press g.", item_name(g));
        } else if glyph == STAIRS {
            self.message = "Stairs down. Press >.".to_string();
        } else {
            self.message = String::new();
        }
        self.end_turn(&self.message.clone());
    }

    /// Swing at monster `i`: the roll, the kill, the win.
    fn attack(&mut self, i: usize) {
        let m = self.monsters[i];
        let roll = self.player_attack_roll(m.kind.defence());
        let hp = m.hp - roll;
        self.monsters[i].hp = hp;
        if hp <= 0 {
            self.kills += 1;
            self.monsters.remove(i);
            self.message = format!("You kill {}!", m.kind.name());
            if m.kind == MonsterKind::Thaw {
                self.state = State::Win;
                self.message = "The Thaw is broken. The ice holds. You win.".to_string();
            }
        } else {
            self.message = format!("You hit {} for {}.", m.kind.name(), roll);
        }
        self.end_turn(&self.message.clone());
    }

    /// Pick up the item underfoot. Consumables apply on pickup: a fish heals,
    /// a scroll maps the floor, a parka is worn, gold is scored.
    fn pick_up(&mut self) {
        let Some(i) = self.item_at(self.player.x, self.player.y) else {
            self.message = "There is nothing here.".to_string();
            return;
        };
        let item = self.items.remove(i);
        self.inventory.push(item.glyph);
        match item.glyph {
            ITEM_FISH => {
                let healed = (self.max_hp - self.hp).min(FISH_HEAL);
                self.hp += healed;
                self.message = format!("You eat the fish. +{healed} hp.");
            }
            ITEM_SCROLL => {
                for i in 0..self.map.len() {
                    self.seen[i] = true;
                }
                self.message = "The scroll maps the floor.".to_string();
            }
            ITEM_PARKA => {
                self.defence += PARKA_DEFENCE;
                self.message = "You put on the parka. +1 defence.".to_string();
            }
            ITEM_GOLD => {
                self.gold += item.value;
                self.message = format!("You pick up {} gold.", item.value);
            }
            other => self.message = format!("You pick up {}.", item_name(other)),
        }
        self.end_turn(&self.message.clone());
    }

    /// The stairs, if you are standing on them.
    fn descend(&mut self) {
        if self.tile(self.player.x, self.player.y) != STAIRS {
            self.message = "There are no stairs here.".to_string();
            return;
        }
        if self.floor >= FLOORS {
            self.message = "The stairs end here. The Thaw is below.".to_string();
            return;
        }
        let depth = self.floor + 1;
        let floor = gen_floor(&mut self.rng, depth);
        let msg = format!("You enter the ice. Floor {depth}.");
        let carried = Carried {
            seed: self.seed,
            rng: self.rng,
            hp: self.hp,
            max_hp: self.max_hp,
            weapon: self.weapon,
            defence: self.defence,
            gold: self.gold,
            kills: self.kills,
            turns: self.turns,
            ms: self.ms,
            inventory: self.inventory.clone(),
            deepest: self.deepest,
            best_score: self.best_score,
            best_floor: self.best_floor,
            best_kills: self.best_kills,
            best_games: self.best_games,
            best_wins: self.best_wins,
            best_seed: self.best_seed,
        };
        *self = Game::from_floor(carried.seed, carried.rng, floor, depth, &msg);
        carried.restore(self);
        self.deepest = self.deepest.max(depth);
        // Going down is a turn like any other: the floor below gets its move.
        self.end_turn(&msg);
    }

    /// End the player's turn: the monsters act, the view updates, death is
    /// checked. Every turn costs exactly one player action.
    fn end_turn(&mut self, msg: &str) {
        self.message = msg.to_string();
        self.turns += 1;
        self.monsters_act();
        self.refresh_fov();
        if self.hp <= 0 && self.state == State::Play {
            self.hp = 0;
            self.state = State::Dead;
            self.message = format!("You die. The ice keeps you. Floor {}.", self.floor);
        }
    }

    // -- the monsters' turn -------------------------------------------------

    /// Every live monster acts, in spawn order, so a turn is a pure function of
    /// the state. Adjacent: it hits you. In sight: it walks at you. Out of
    /// sight: it wanders.
    fn monsters_act(&mut self) {
        for i in 0..self.monsters.len() {
            if self.state != State::Play {
                return;
            }
            let m = self.monsters[i];
            let (dx, dy) = (self.player.x as i32 - m.x as i32, self.player.y as i32 - m.y as i32);
            if dx.abs() <= 1 && dy.abs() <= 1 {
                let dmg = self.monster_damage(m.kind);
                self.hp -= dmg;
                self.message = if dmg == 0 {
                    format!("{} hits your parka.", m.kind.name())
                } else {
                    format!("{} hits you for {}.", m.kind.name(), dmg)
                };
                if self.hp <= 0 {
                    return; // end_turn turns this into permadeath
                }
                continue;
            }
            let step = if monster_sees_player(&self.map, m.x, m.y, self.player.x, self.player.y) {
                self.chase_step(m.x, m.y)
            } else {
                self.wander_step(m.x, m.y)
            };
            if let Some((nx, ny)) = step {
                self.monsters[i].x = nx;
                self.monsters[i].y = ny;
            }
        }
    }

    /// The greedy step toward the penguin: the walkable neighbour that closes
    /// the distance. Ties break on the direction order, so it is deterministic.
    fn chase_step(&self, mx: usize, my: usize) -> Option<(usize, usize)> {
        let (px, py) = (self.player.x as i32, self.player.y as i32);
        let (mx, my) = (mx as i32, my as i32);
        let here = (mx - px).abs() + (my - py).abs();
        let mut best: Option<(i32, usize, usize)> = None;
        for (dx, dy) in DIRS {
            let (nx, ny) = (mx + dx, my + dy);
            if !in_bounds(nx, ny) {
                continue;
            }
            let (ux, uy) = (nx as usize, ny as usize);
            if !self.walkable(ux, uy) {
                continue;
            }
            if (ux, uy) == (self.player.x, self.player.y) {
                continue; // the penguin's tile is not a walking destination
            }
            if self.monster_at(ux, uy).is_some() {
                continue; // monsters do not stack
            }
            let d = (nx - px).abs() + (ny - py).abs();
            if d >= here {
                continue;
            }
            if best.map(|(bd, _, _)| d < bd).unwrap_or(true) {
                best = Some((d, ux, uy));
            }
        }
        best.map(|(_, x, y)| (x, y))
    }

    /// A monster you cannot see wanders: a random legal step, and a third of
    /// the time it stays where it is.
    fn wander_step(&mut self, mx: usize, my: usize) -> Option<(usize, usize)> {
        if self.rng.below(3) == 0 {
            return None;
        }
        let i = self.rng.below(DIRS.len() as u64) as usize;
        let (dx, dy) = DIRS[i];
        let (nx, ny) = (mx as i32 + dx, my as i32 + dy);
        if !in_bounds(nx, ny) {
            return None;
        }
        let (ux, uy) = (nx as usize, ny as usize);
        if !self.walkable(ux, uy) {
            return None;
        }
        if (ux, uy) == (self.player.x, self.player.y) {
            return None;
        }
        if self.monster_at(ux, uy).is_some() {
            return None;
        }
        Some((ux, uy))
    }

    // -- the inventory ------------------------------------------------------

    pub fn inventory_text(&self) -> String {
        if self.inventory.is_empty() {
            return "Your pack is empty.".to_string();
        }
        let mut counts: [(char, usize); 4] = [
            (ITEM_GOLD, 0),
            (ITEM_FISH, 0),
            (ITEM_SCROLL, 0),
            (ITEM_PARKA, 0),
        ];
        for g in &self.inventory {
            for (cg, n) in counts.iter_mut() {
                if cg == g {
                    *n += 1;
                }
            }
        }
        let parts: Vec<String> = counts
            .iter()
            .filter(|(_, n)| *n > 0)
            .map(|(g, n)| format!("{n} {}", item_name(*g)))
            .collect();
        parts.join(", ")
    }

    // -- fingerprints -------------------------------------------------------

    /// The whole run as one canonical string. Two runs with the same seed and
    /// the same keys produce the same string, byte for byte. The elapsed timer
    /// is deliberately *not* in here: it is the wall clock, not the position.
    pub fn canonical(&self) -> String {
        let mut s = String::new();
        let _ = write!(
            s,
            "seed={seed}|rng={rng:016x}|floor={floor}|deepest={deepest}|player={px},{py}|entry={ex},{ey}|stairs={sx},{sy}",
            seed = self.seed,
            rng = self.rng.state(),
            floor = self.floor,
            deepest = self.deepest,
            px = self.player.x,
            py = self.player.y,
            ex = self.entry.x,
            ey = self.entry.y,
            sx = self.stairs.x,
            sy = self.stairs.y,
        );
        let _ = write!(
            s,
            "|hp={hp}/{max}|weapon={w}|def={d}|gold={gold}|kills={kills}|turns={turns}|state={state:?}",
            hp = self.hp,
            max = self.max_hp,
            w = self.weapon,
            d = self.defence,
            gold = self.gold,
            kills = self.kills,
            turns = self.turns,
            state = self.state,
        );
        let _ = write!(s, "|map={}", self.map_text());
        let _ = write!(s, "|monsters={}", self.monsters_text());
        let _ = write!(s, "|items={}", self.items_text());
        let _ = write!(
            s,
            "|inv={}|seen={}|visible={}|msg={}",
            self.inventory.iter().collect::<String>(),
            self.seen.iter().filter(|b| **b).count(),
            self.visible.iter().filter(|b| **b).count(),
            self.message,
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

    /// The run as `key=value` pairs — what `--replay` prints on exit 0, and
    /// what the acceptance harness greps.
    pub fn result_kv(&self) -> Vec<(String, String)> {
        let mut kv = vec![
            ("state".into(), format!("{:?}", self.state).to_lowercase()),
            ("seed".into(), self.seed.to_string()),
            ("floor".into(), self.floor.to_string()),
            ("deepest".into(), self.deepest.to_string()),
            ("turns".into(), self.turns.to_string()),
            ("ms".into(), self.ms.to_string()),
            ("time".into(), clock(self.ms)),
            ("hp".into(), self.hp.to_string()),
            ("max_hp".into(), self.max_hp.to_string()),
            ("defence".into(), self.defence.to_string()),
            ("weapon".into(), self.weapon.to_string()),
            ("gold".into(), self.gold.to_string()),
            ("kills".into(), self.kills.to_string()),
            ("monsters".into(), self.monsters.len().to_string()),
            ("items".into(), self.items.len().to_string()),
            ("score".into(), self.score().to_string()),
            ("seen".into(), self.seen.iter().filter(|b| **b).count().to_string()),
            ("best_score".into(), self.best_score.to_string()),
            ("best_floor".into(), self.best_floor.to_string()),
            ("best_kills".into(), self.best_kills.to_string()),
            ("best_games".into(), self.best_games.to_string()),
            ("best_wins".into(), self.best_wins.to_string()),
            ("hash".into(), format!("{:016x}", self.state_hash())),
        ];
        kv.push(("message".into(), self.message.clone()));
        kv
    }

    // -- saved positions ---------------------------------------------------

    /// The terrain as rows of `#`, `.` and `>`.
    pub fn map_text(&self) -> String {
        let mut s = String::new();
        for y in 0..MAP_H {
            for x in 0..MAP_W {
                s.push(self.map[idx(x, y)] as char);
            }
            s.push('\n');
        }
        s
    }

    pub fn monsters_text(&self) -> String {
        self.monsters
            .iter()
            .map(|m| format!("{}@{},{}:{}", m.kind.glyph(), m.x, m.y, m.hp))
            .collect::<Vec<String>>()
            .join(";")
    }

    pub fn items_text(&self) -> String {
        self.items
            .iter()
            .map(|i| format!("{}@{},{}:{}", i.glyph, i.x, i.y, i.value))
            .collect::<Vec<String>>()
            .join(";")
    }

    /// A saved position, in the format [`parse_state`] reads. Used by
    /// `--save`/`--state` and by the fixtures: a boss one hit from death is a
    /// position, not a story about how it got there.
    pub fn state_text(&self) -> String {
        let mut s = String::new();
        let _ = write!(
            s,
            "# pengy-rogue position (Ice Rogue)\nfloor={}\nhp={}\nmax_hp={}\nweapon={}\ndefence={}\ngold={}\nkills={}\nturns={}\nplayer={},{}\nentry={},{}\nstairs={},{}\nrng={:016x}\nstate={}\nmonsters={}\nitems={}\nmap=\n{}",
            self.floor,
            self.hp,
            self.max_hp,
            self.weapon,
            self.defence,
            self.gold,
            self.kills,
            self.turns,
            self.player.x,
            self.player.y,
            self.entry.x,
            self.entry.y,
            self.stairs.x,
            self.stairs.y,
            self.rng.state(),
            format!("{:?}", self.state).to_lowercase(),
            self.monsters_text(),
            self.items_text(),
            self.map_text(),
        );
        s
    }

    // -- the demo player ---------------------------------------------------

    /// The demo player's next keypresses, one tick at a time: pick up what is
    /// underfoot, fight what is adjacent, hunt The Thaw on floor 5, otherwise
    /// head for the stairs and, failing that, walk into the dark. A greedy
    /// penguin, not a champion: its job is to prove the game runs with no pty,
    /// and to make fixtures.
    pub fn autopilot_keys(&mut self) -> String {
        if self.state != State::Play {
            return String::new();
        }
        if self.item_at(self.player.x, self.player.y).is_some() {
            return "g".to_string();
        }
        if self.tile(self.player.x, self.player.y) == STAIRS && self.floor < FLOORS {
            return ">".to_string();
        }
        // Fight anything adjacent: the demo penguin swings, it does not stand
        // there and take the hits.
        if let Some(m) = self
            .monsters
            .iter()
            .find(|m| (m.x as i32 - self.player.x as i32).abs() <= 1
                && (m.y as i32 - self.player.y as i32).abs() <= 1)
        {
            let (dx, dy) = (m.x as i32 - self.player.x as i32, m.y as i32 - self.player.y as i32);
            if let Some(i) = DIRS.iter().position(|&(ax, ay)| ax == dx && ay == dy) {
                return DIR_KEYS[i].to_string();
            }
        }
        // Fight anything adjacent, and hunt the boss on its floor.
        let target = if self.floor == FLOORS {
            self.monsters
                .iter()
                .find(|m| m.kind == MonsterKind::Thaw)
                .map(|m| Pos::new(m.x, m.y))
        } else {
            None
        };
        if let Some(t) = target {
            if let Some(k) = self.greedy_step(t) {
                return k.to_string();
            }
        }
        if let Some(stairs) = self.known_stairs() {
            if let Some(k) = self.greedy_step(stairs) {
                return k.to_string();
            }
        }
        // Explore: prefer a tile we have not seen, and prefer to keep walking
        // the way we were going, so the demo penguin sweeps the floor instead
        // of jittering on one spot.
        let mut best: Option<(i32, usize)> = None;
        for (i, &(dx, dy)) in DIRS.iter().enumerate() {
            let (nx, ny) = (self.player.x as i32 + dx, self.player.y as i32 + dy);
            if !in_bounds(nx, ny) {
                continue;
            }
            let (ux, uy) = (nx as usize, ny as usize);
            if !self.walkable(ux, uy) || self.monster_at(ux, uy).is_some() {
                continue;
            }
            if self.came_from == Some(Pos::new(ux, uy)) {
                continue;
            }
            let mut cost = 0;
            if self.seen[idx(ux, uy)] {
                cost += 4;
            }
            if self.last_dir == Some(i) {
                cost -= 2;
            }
            if best.map(|(c, _)| cost < c).unwrap_or(true) {
                best = Some((cost, i));
            }
        }
        match best {
            Some((_, i)) => DIR_KEYS[i].to_string(),
            None => ".".to_string(),
        }
    }

    /// The stairs, when the map has revealed them.
    fn known_stairs(&self) -> Option<Pos> {
        if self.seen[idx(self.stairs.x, self.stairs.y)] {
            Some(self.stairs)
        } else {
            None
        }
    }

    /// The greedy walk toward a tile, over the tiles the penguin has seen.
    /// Never walks into a monster, never walks back where it came from.
    fn greedy_step(&self, goal: Pos) -> Option<char> {
        let (gx, gy) = (goal.x as i32, goal.y as i32);
        let here = (self.player.x as i32 - gx).abs() + (self.player.y as i32 - gy).abs();
        let mut best: Option<(i32, usize, usize)> = None;
        for (i, &(dx, dy)) in DIRS.iter().enumerate() {
            let (nx, ny) = (self.player.x as i32 + dx, self.player.y as i32 + dy);
            if !in_bounds(nx, ny) {
                continue;
            }
            let (ux, uy) = (nx as usize, ny as usize);
            if !self.walkable(ux, uy) {
                continue;
            }
            if self.monster_at(ux, uy).is_some() {
                continue;
            }
            if self.came_from == Some(Pos::new(ux, uy)) {
                continue;
            }
            if !self.seen[idx(ux, uy)] {
                continue;
            }
            let d = (nx - gx).abs() + (ny - gy).abs();
            if d >= here {
                continue;
            }
            if best.map(|(bd, _, _)| d < bd).unwrap_or(true) {
                best = Some((d, i, ux * MAP_W + uy));
            }
        }
        best.map(|(_, i, _)| DIR_KEYS[i])
    }
}

/// The run-carried stats a descent keeps. `descend` rebuilds the game from the
/// new floor, so the player's history has to travel with it.
struct Carried {
    seed: u64,
    rng: Rng,
    hp: i32,
    max_hp: i32,
    weapon: i32,
    defence: i32,
    gold: i32,
    kills: i32,
    turns: u64,
    ms: u64,
    inventory: Vec<char>,
    deepest: usize,
    best_score: u64,
    best_floor: u64,
    best_kills: u64,
    best_games: u64,
    best_wins: u64,
    best_seed: u64,
}

impl Carried {
    fn restore(&self, g: &mut Game) {
        g.hp = self.hp;
        g.max_hp = self.max_hp;
        g.weapon = self.weapon;
        g.defence = self.defence;
        g.gold = self.gold;
        g.kills = self.kills;
        g.turns = self.turns;
        g.ms = self.ms;
        g.inventory = self.inventory.clone();
        g.deepest = self.deepest;
        g.best_score = self.best_score;
        g.best_floor = self.best_floor;
        g.best_kills = self.best_kills;
        g.best_games = self.best_games;
        g.best_wins = self.best_wins;
        g.best_seed = self.best_seed;
    }
}

/// The item name for messages and the inventory.
pub fn item_name(glyph: char) -> &'static str {
    match glyph {
        ITEM_FISH => "a fish",
        ITEM_SCROLL => "a scroll",
        ITEM_GOLD => "gold",
        ITEM_PARKA => "a parka",
        _ => "an oddity",
    }
}

/// Milliseconds as `m:ss`.
pub fn clock(ms: u64) -> String {
    let secs = ms / 1000;
    format!("{}:{:02}", secs / 60, secs % 60)
}

// ---------------------------------------------------------------------------
// Saved positions, read back
// ---------------------------------------------------------------------------

/// Read a saved position written by [`Game::state_text`]. `None` for anything
/// that is not a legal position: a broken file is an error the front-end turns
/// into exit 66, never a panic.
pub fn parse_state(text: &str) -> Option<Game> {
    let mut floor = 1usize;
    let mut hp = START_HP;
    let mut max_hp = START_MAX_HP;
    let mut weapon = START_WEAPON;
    let mut defence = START_DEFENCE;
    let mut gold = 0i32;
    let mut kills = 0i32;
    let mut turns = 0u64;
    let mut player = Pos::new(1, 1);
    let mut entry = Pos::new(1, 1);
    let mut stairs = Pos::new(2, 2);
    let mut rng_state: Option<u64> = None;
    let mut state = State::Play;
    let mut monsters: Vec<Monster> = Vec::new();
    let mut items: Vec<Item> = Vec::new();
    let mut map_rows: Vec<String> = Vec::new();

    let mut lines = text.lines();
    while let Some(line) = lines.next() {
        let line = line.trim_end_matches('\r');
        if line.starts_with('#') || line.trim().is_empty() {
            continue;
        }
        let mut kv = line.splitn(2, '=');
        let (k, v) = match (kv.next(), kv.next()) {
            (Some(k), Some(v)) => (k.trim(), v.trim()),
            _ => return None,
        };
        match k {
            "floor" => floor = v.parse().ok()?,
            "hp" => hp = v.parse().ok()?,
            "max_hp" => max_hp = v.parse().ok()?,
            "weapon" => weapon = v.parse().ok()?,
            "defence" => defence = v.parse().ok()?,
            "gold" => gold = v.parse().ok()?,
            "kills" => kills = v.parse().ok()?,
            "turns" => turns = v.parse().ok()?,
            "rng" => rng_state = Some(u64::from_str_radix(v, 16).ok()?),
            "state" => {
                state = match v {
                    "play" => State::Play,
                    "dead" => State::Dead,
                    "win" => State::Win,
                    _ => return None,
                }
            }
            "player" | "entry" | "stairs" => {
                let p = parse_pos(v)?;
                match k {
                    "player" => player = p,
                    "entry" => entry = p,
                    _ => stairs = p,
                }
            }
            "monsters" => {
                monsters = parse_monsters(v)?;
            }
            "items" => {
                items = parse_items(v)?;
            }
            "map" => {
                for _ in 0..MAP_H {
                    let row = lines.next()?;
                    if row.chars().count() != MAP_W {
                        return None;
                    }
                    if !row.chars().all(|c| matches!(c, '#' | '.' | '>')) {
                        return None;
                    }
                    map_rows.push(row.chars().collect());
                }
            }
            _ => return None,
        }
    }

    if map_rows.len() != MAP_H {
        return None;
    }
    if !(1..=FLOORS).contains(&floor) {
        return None;
    }
    let mut map = vec![WALL; MAP_W * MAP_H];
    for (y, row) in map_rows.iter().enumerate() {
        for (x, c) in row.chars().enumerate() {
            map[idx(x, y)] = c as u8;
        }
    }
    for p in [player, entry, stairs] {
        if !in_bounds(p.x as i32, p.y as i32) || map[idx(p.x, p.y)] == WALL {
            return None;
        }
    }
    if map[idx(stairs.x, stairs.y)] != STAIRS {
        return None;
    }
    for m in &monsters {
        if map[idx(m.x, m.y)] == WALL || m.hp <= 0 {
            return None;
        }
    }
    for i in &items {
        if map[idx(i.x, i.y)] == WALL || !ITEM_GLYPHS.contains(&i.glyph) {
            return None;
        }
    }
    if monsters.iter().any(|m| (m.x, m.y) == (player.x, player.y)) {
        return None;
    }

    let mut rng = Rng::new(0);
    if let Some(s) = rng_state {
        rng.set_state(s);
    }
    // The rooms are not part of a saved position: nothing in the rules reads
    // them after generation.
    let rooms = vec![Room {
        x: 1,
        y: 1,
        w: 4,
        h: 4,
    }];
    let mut g = Game {
        seed: 0,
        rng,
        floor,
        map,
        items,
        monsters,
        rooms,
        entry,
        stairs,
        player,
        hp,
        max_hp,
        weapon,
        defence,
        gold,
        kills,
        turns,
        ms: 0,
        state,
        seen: vec![false; MAP_W * MAP_H],
        visible: vec![false; MAP_W * MAP_H],
        message: String::new(),
        inventory: Vec::new(),
        deepest: floor,
        last_dir: None,
        came_from: None,
        best_score: 0,
        best_floor: 0,
        best_kills: 0,
        best_games: 0,
        best_wins: 0,
        best_seed: 0,
    };
    g.refresh_fov();
    Some(g)
}

fn parse_pos(v: &str) -> Option<Pos> {
    let mut it = v.split(',');
    let x: usize = it.next()?.trim().parse().ok()?;
    let y: usize = it.next()?.trim().parse().ok()?;
    if it.next().is_some() || !in_bounds(x as i32, y as i32) {
        return None;
    }
    Some(Pos::new(x, y))
}

fn parse_monsters(v: &str) -> Option<Vec<Monster>> {
    if v.is_empty() {
        return Some(Vec::new());
    }
    let mut out = Vec::new();
    for part in v.split(';') {
        let (at, hp) = part.rsplit_once(':').unwrap_or((part, ""));
        let (g, pos) = at.split_once('@')?;
        let kind = MonsterKind::from_glyph(g.chars().next()?)?;
        let p = parse_pos(pos)?;
        let hp: i32 = if hp.is_empty() {
            kind.hp()
        } else {
            hp.parse().ok()?
        };
        out.push(Monster {
            kind,
            x: p.x,
            y: p.y,
            hp,
        });
    }
    Some(out)
}

fn parse_items(v: &str) -> Option<Vec<Item>> {
    if v.is_empty() {
        return Some(Vec::new());
    }
    let mut out = Vec::new();
    for part in v.split(';') {
        let (at, value) = part.rsplit_once(':').unwrap_or((part, ""));
        let (g, pos) = at.split_once('@')?;
        let glyph = g.chars().next()?;
        if !ITEM_GLYPHS.contains(&glyph) {
            return None;
        }
        let p = parse_pos(pos)?;
        let value: i32 = if value.is_empty() { 0 } else { value.parse().ok()? };
        out.push(Item {
            x: p.x,
            y: p.y,
            glyph,
            value,
        });
    }
    Some(out)
}

// ---------------------------------------------------------------------------
// Rendering — a pure function of state
// ---------------------------------------------------------------------------

/// How to draw. The front-end fills this in from the window size, `--mono` and
/// `NO_COLOR`; the headless paths use `--width`/`--height`.
#[derive(Clone, Copy, Debug)]
pub struct RenderOpts {
    pub width: usize,
    pub height: usize,
    /// ASCII-only glyphs, no colour, no dim: for a terminal whose locale
    /// cannot render them, and for anything capturing the frame.
    pub mono: bool,
    /// Emit ANSI colour and dim.
    pub color: bool,
}

impl Default for RenderOpts {
    fn default() -> Self {
        Self {
            width: REF_WIDTH,
            height: REF_HEIGHT,
            mono: false,
            color: false,
        }
    }
}

/// Wrap a glyph in an SGR code, when colour is on.
fn paint(s: &str, code: Option<&str>, o: RenderOpts) -> String {
    match code {
        Some(c) if o.color && !o.mono => format!("\x1b[{c}m{s}\x1b[0m"),
        _ => s.to_string(),
    }
}

/// The frame: the whole screen as one string. The front-end writes `\x1b[H`
/// plus this string per frame, and that is the whole repaint — no per-cell
/// cursor addressing, so no flicker.
pub fn frame(g: &Game) -> String {
    render(g, RenderOpts::default())
}

/// The frame at a given size. Degrades in a fixed order when the window is
/// smaller than the reference: the score panel, the border, the title, the
/// status line, the key legend. The map is always drawn, clipped to the window.
pub fn render(g: &Game, o: RenderOpts) -> String {
    let w = o.width.max(20);
    let h = o.height.max(8);
    let panel = w >= PANEL_MIN_WIDTH;
    let border = w >= BORDER_MIN_WIDTH && h >= BORDER_MIN_HEIGHT;
    let title = h >= TITLE_MIN_HEIGHT;
    let status = h >= STATUS_MIN_HEIGHT;
    let legend = h >= LEGEND_MIN_HEIGHT;

    let map_x = usize::from(border);
    let map_y = usize::from(border) + usize::from(title);
    let panel_x = map_x + MAP_W + usize::from(border) + 1;

    let mut lines: Vec<String> = (0..h).map(|_| String::new()).collect();

    if title {
        lines[0] = fit(
            &format!(
                " {GAME_NAME} — floor {}/{}   best {}",
                g.floor, FLOORS, g.best_score
            ),
            w,
        );
    }

    let (side, hline, corner_a, corner_b, corner_c, corner_d) = if o.mono {
        ("|", "-", "+", "+", "+", "+")
    } else {
        ("│", "─", "┌", "┐", "└", "┘")
    };
    if border {
        let top = paint(
            &format!("{corner_a}{}{corner_b}", hline.repeat(MAP_W)),
            Some("2"),
            o,
        );
        if map_y > 0 {
            lines[map_y - 1] = fit(&top, w);
        }
        let bottom = paint(
            &format!("{corner_c}{}{corner_d}", hline.repeat(MAP_W)),
            Some("2"),
            o,
        );
        if map_y + MAP_H < h {
            lines[map_y + MAP_H] = fit(&bottom, w);
        }
    }

    // The map, clipped to the window. Always drawn: it is the game.
    for row in 0..MAP_H {
        let screen_row = map_y + row;
        if screen_row >= h {
            break;
        }
        let mut s = String::new();
        if border {
            s.push_str(&paint(side, Some("2"), o));
        }
        for col in 0..MAP_W {
            if map_x + col >= w {
                break;
            }
            s.push_str(&tile_glyph(g, col, row, o));
        }
        if border && map_x + MAP_W < w {
            s.push_str(&paint(side, Some("2"), o));
        }
        lines[screen_row] = s;
    }

    if panel {
        for (i, line) in panel_lines(g).iter().enumerate() {
            let row = map_y + i;
            if row >= h || i >= MAP_H {
                break;
            }
            // The panel lives to the right of the map, on the map's own rows:
            // pad the row out to the panel column, then write the line. It
            // never overwrites the map.
            let mut s = lines[row].clone();
            let used = visible_len(&s);
            if used < panel_x {
                s.push_str(&" ".repeat(panel_x - used));
            }
            s.push_str(line);
            lines[row] = fit(&s, w);
        }
    }

    let mut row = map_y + MAP_H + usize::from(border);
    if status && row < h {
        lines[row] = fit(&status_line(g), w);
        row += 1;
    }
    if row < h {
        lines[row] = fit(&g.message, w);
        row += 1;
    }
    if legend && row < h {
        lines[row] = fit(LEGEND, w);
        row += 1;
    }
    if legend && row < h {
        lines[row] = fit(GLYPHS, w);
    }

    let mut out = String::new();
    for line in lines.iter() {
        let _ = writeln!(out, "{line}");
    }
    out
}

/// The status line under the map.
pub fn status_line(g: &Game) -> String {
    format!(
        " hp {}/{}  atk 1d4+{}  def {}  gold {}  kills {}  score {}  turn {}",
        g.hp,
        g.max_hp,
        g.weapon + 1,
        g.defence,
        g.gold,
        g.kills,
        g.score(),
        g.turns
    )
}

/// The right-hand panel: the run's numbers and the record. Exactly
/// [`MAP_H`] lines, one per map row, so the panel can never write over the map.
pub fn panel_lines(g: &Game) -> Vec<String> {
    vec![
        format!("floor {}/{}", g.floor, FLOORS),
        format!("hp  {}/{}", g.hp, g.max_hp),
        format!("atk 1d4+{}", g.weapon + 1),
        format!("def {}", g.defence),
        format!("gold {}", g.gold),
        format!("kills {}", g.kills),
        format!("score {}", g.score()),
        format!("turn {}", g.turns),
        format!("time {}", clock(g.ms)),
        String::new(),
        format!("best {}", g.best_score),
        format!("best floor {}", g.best_floor),
        format!("best kills {}", g.best_kills),
        format!("games {} wins {}", g.best_games, g.best_wins),
        String::new(),
    ]
}

/// One tile as its glyph, with its colour. Unexplored tiles are blank;
/// explored-but-unseen tiles are the dim layer (a plain `.` in mono).
fn tile_glyph(g: &Game, x: usize, y: usize, o: RenderOpts) -> String {
    let i = idx(x, y);
    if !g.seen[i] {
        return " ".to_string();
    }
    let visible = g.visible[i];
    if (x, y) == (g.player.x, g.player.y) {
        return paint(&PLAYER_GLYPH.to_string(), Some("1;97"), o);
    }
    if !visible {
        let glyph = match g.map[i] {
            WALL => '#',
            STAIRS => '>',
            _ => '.',
        };
        return paint(&glyph.to_string(), Some("2"), o);
    }
    if let Some(mi) = g.monster_at(x, y) {
        let m = g.monsters[mi];
        let code = if m.kind == MonsterKind::Thaw {
            "1;91"
        } else {
            "31"
        };
        return paint(&m.kind.glyph().to_string(), Some(code), o);
    }
    if let Some(ii) = g.item_at(x, y) {
        let it = g.items[ii];
        let code = match it.glyph {
            ITEM_GOLD => "33",
            ITEM_FISH => "36",
            ITEM_SCROLL => "35",
            _ => "34",
        };
        return paint(&it.glyph.to_string(), Some(code), o);
    }
    match g.map[i] {
        WALL => paint("#", Some("2"), o),
        STAIRS => paint(">", Some("1;34"), o),
        _ => paint(".", None, o),
    }
}

/// Truncate to `w` *visible* columns, keeping any SGR sequence intact. The
/// frame is built from coloured spans, so a naive byte count would cut the
/// window at the wrong column.
pub fn fit(s: &str, w: usize) -> String {
    let mut out = String::new();
    let mut used = 0usize;
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            out.push(c);
            if chars.peek() == Some(&'[') {
                out.push('[');
                chars.next();
                while let Some(&d) = chars.peek() {
                    chars.next();
                    out.push(d);
                    if d.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        if used >= w {
            break;
        }
        out.push(c);
        used += 1;
    }
    out
}

/// The glyph key, quoted under the legend.
pub const GLYPHS: &str = "@ you  r rat  c crab  w wolf  T Thaw  ! fish  ? scroll  $ gold  + parka";

/// Columns a string occupies on screen, ignoring SGR sequences.
pub fn visible_len(s: &str) -> usize {
    let mut n = 0usize;
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            if chars.peek() == Some(&'[') {
                chars.next();
                while let Some(&d) = chars.peek() {
                    chars.next();
                    if d.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        n += 1;
    }
    n
}

/// The plain-text state the headless paths print: `key=value` lines, no ANSI,
/// no terminal. This is what the repo gate and `pengyos-qemu-verify` read.
pub fn plain_state(g: &Game) -> String {
    let mut s = String::new();
    for (k, v) in g.result_kv() {
        let _ = writeln!(s, "{k}={v}");
    }
    s
}
