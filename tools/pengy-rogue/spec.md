# pengy-rogue — spec

**Ice Rogue**: Rogue (1980) — the original dungeon crawl. Five floors down, one
penguin, permanent death, and at the bottom The Thaw. One of the ten pack-in
games; the slot next to blocks, breakout, floe, icefield, invaders, lander,
merge, pong and solitaire.

This file is the contract. The tests in `tests/` enforce it; the README is the
user-facing version of the same rules.

## Purpose

Every OS used to ship a couple of small, complete games that just worked.
PengyOS ships none. The pack-in games are terminal-first, Rust,
zero-dependency, and *good little games*: tuned rules, a score, a persistent
record, a clean quit, no crashes, no flicker.

Rogue is the terminal's own game: it is why `curses` exists. It is also the
pack-in that needs the least — no fonts, no GPU, no X, no Wayland — and the one
whose rules are most worth stating exactly, because a dungeon crawl is pure
state transition.

## Constraints (non-negotiable)

1. **Rust 2021, `std` only, zero external crates.** No libc, no `rand`, no
   crossterm, no clap, no serde, no ncurses. `[dependencies]` stays empty
   forever; `tests/github_ready.rs` asserts it. This is what makes the Buildroot
   recipe an offline, reproducible `cargo build --release`.
2. **The library is pure.** `src/lib.rs` has no files, no keyboard, no screen,
   no clock, no threads, no process, no system entropy. The only randomness is
   the seeded xorshift64*. `tests/github_ready.rs` greps for the forbidden
   imports.
3. **Rendering is a pure function of state**: `render(&Game, RenderOpts) ->
   String` (and `frame(&Game)` for the reference window), one string per frame,
   the front-end writing `\x1b[H` plus the frame. No per-cell cursor
   addressing, so no flicker.
4. **Never require a TTY.** No TTY, `--headless`, or `--replay`: no rendering,
   no key reads, plain text, exit 0. The repo gate and `pengyos-qemu-verify`
   have no pty, so this is how the game is proven.
5. **Determinism.** Every run takes `--seed N`; the same seed and the same
   script produce the same state hash, byte for byte. The generator, the
   monster spawns, the wander dice and every combat roll draw from the one
   stream.
6. **The terminal is restored on every exit**, including a panic: a `Drop`
   guard plus a panic hook run `stty sane` and leave the alternate screen. The
   release profile deliberately does **not** set `panic = "abort"`, because
   destructors must run.
7. **Mutable state lives in one place**:
   `$XDG_DATA_HOME/pengyos/games/pengy-rogue/` (default `~/.local/share/...`),
   under `$HOME`. Never `/usr`, never the repo, never a sibling of the binary.
   A write failure is ignored.
8. **Playable in 80x24**, degrading in a fixed order in a smaller window.
9. **No network, no services, no root, no cron, no daemon, no nested jobs, no
   process left running.**

## The world

- The map is `54 x 15` tiles: `#` wall, `.` floor, `>` stairs. You are `@`.
- **Five floors.** Floor `n` is generated from the run's generator stream, so a
  seed is a whole dungeon, not just a floor.
- **Rooms:** 4 to 9 per floor, interior 4x4 to 10x6, never overlapping (two
  rooms are always separated by at least one full tile of rock).
- **Corridors:** L-shaped, joining each room to the next in room order; the bend
  order is a coin flip from the stream.
- **Connectivity is proven at generation time**: a 4-connected flood fill from
  the entry must reach the stairs *and* the middle of every room. A layout that
  fails is thrown away and the floor is regenerated. The acceptance test runs
  100 seeds x 5 floors.
- The **entry** is the middle of the first room (you arrive there from the floor
  above); the **stairs** are the middle of the last room.

## The penguin

| Stat | Value |
|---|---|
| hp | 12 (max 12) |
| attack | 1d4+1 |
| weapon bonus | 0 |
| defence | 0 |

**Attack roll** = `1d4 + 1 + weapon − target defence`, minimum 0.
**Monster damage** = `monster damage − your defence`, minimum 0 (monsters roll
no die: the spec gives them flat damage, which is what makes a fight solvable
in your head).

**Permadeath.** 0 hp is `dead`, and `dead` is final: no save, no undo, no key
changes the position.

## Monsters

| Glyph | Monster | hp | damage |
|---|---|---|---|
| `r` | rat | 2 | 1 |
| `c` | ice crab | 4 | 2 |
| `w` | wolf | 6 | 3 |
| `T` | **The Thaw** | 20 | 4 |

- The boss spawns on **floor 5 only**, and exactly once.
- Monsters never spawn in the entry room, never on the stairs, never on an item,
  never two on one tile, never wounded.
- **Turn based:** every player action advances one turn, then every live
  monster acts, in spawn order.
  - adjacent (Chebyshev 1) to you: it hits you;
  - else in sight (radius 7, walls block): it steps to the walkable neighbour
    that closes the Manhattan distance, never onto you, never onto another
    monster;
  - else: it wanders — a random legal step, and a third of the time it stays.
- Walking **into** a monster attacks it instead of moving.

## Items

One item per tile. `g` picks up the item underfoot; consumables apply on pickup.

| Glyph | Item | Effect |
|---|---|---|
| `!` | fish | heals 3, capped at max hp |
| `?` | scroll | maps the floor (every tile becomes explored) |
| `$` | gold | 1 to 5 points, scored |
| `+` | parka | +1 defence |

## Field of view

Radius **7** (Chebyshev, so a square) with Bresenham line of sight; walls block
it, endpoints never block themselves.

- Tiles in sight draw normally.
- Tiles ever seen, not in sight, draw **dim** — a plain `.` in `--mono`.
- Tiles never seen draw **blank**.

## Keys

| Key | Action | Costs a turn |
|---|---|---|
| `h j k l` | move (arrows work too) | yes, if the step happens |
| `y u b n` | the four diagonals | yes, if the step happens |
| `.` | wait | yes |
| `g` | pick up | yes, if there is something |
| `i` | inventory | no |
| `>` | descend (only while standing on the stairs) | yes, if it happens |
| `q` | quit, restore the terminal | — |
| anything else | ignored | no |

Bumping a wall costs nothing. A bump into a monster is an attack and costs a
turn. `Escape` is not a quit key.

## States

`play` → `dead` (hp 0) or `win` (The Thaw killed). Both are terminal: the run
prints its result, banks the record, and a scripted session stops there.

## Score

```
score = floor * 100 + gold + kills
```

`floor` is the floor you are on when the run ends, so a deep death scores.

## CLI

`--seed N` (default 1), `--headless`, `--replay FILE`, `--state FILE`,
`--save FILE`, `--record FILE`, `--ticks N` (default 4000), `--autopilot`,
`--mono`, `--no-color`, `--print-frame`, `--width N`, `--height N`,
`--data-dir DIR`, `--no-save`, `--help`, `--version`. `NO_COLOR` is honoured.

Exit codes: `0` success (including every headless/replay path), `2` a usage
error, `66` an unreadable or illegal `--state`/`--replay` file.

A replay file is one line of input per tick; blank = no input; `#` = comment,
not a tick.

## Acceptance criteria

Machine-checked by `cargo test`:

1. 100 seeds x 5 floors: the stairs are reachable from the entry by flood fill,
   every room's middle is reachable, no two rooms overlap, room sizes are in
   range, one item per tile, no monster on the entry/stairs/an item, monsters
   spawn at full hp.
2. FOV: a wall column blocks the view behind it and the wall itself is visible;
   the tile at radius 7 is in and the tile at 8 is out, along a corridor and at
   the corner of the square; explored-not-seen tiles draw dim (mono `.`),
   never-seen tiles draw blank.
3. Combat: the roll is `1d4+1+weapon−defence`, min 0, over the whole die; the
   same seed and the same script give the same hash; a monster's damage is its
   damage minus defence, min 0; 0 hp is `dead` and `dead` is final.
4. A monster in sight closes the distance every turn and stops at arm's length;
   a monster out of sight wanders (it does not monotonically close).
5. The boss spawns on floor 5 only, exactly once, and killing it is `win`.
6. `score == floor*100 + gold + kills`, and a descent carries hp/gold/kills/
   defence and costs a turn.
7. Determinism: same seed + same script ⇒ same `state_hash()`; the CLI replay of
   a committed fixture reproduces its recorded `hash=`.
8. `--headless` and `--replay` need no pty, print `key=value` lines, exit 0.
9. The frame is a pure function of state, 24 rows at 80x24, and degrades in the
   documented order at 60x24 and 40x12 without dropping the map.
10. The record is written only under the data dir; `--no-save` writes nothing.
11. The tool directory carries README/spec/LICENSE/CHANGELOG/verify.toml, the
    launcher, the fixtures and the recipe skeleton, and touches no shared file.

## Out of scope for 0.1.0

Ranged spells, rings, wands, food as a survival clock, monsters with unique
attacks, the mazelike generated vault, saving a run in progress, and the
multi-floor amulet. The pack-in is the five floors and the boss.
