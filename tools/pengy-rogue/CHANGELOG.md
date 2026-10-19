# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Changed

- Ice Rogue is a window now: a libcosmic app (`src/app.rs`, the `cosmic`
  feature) for the COSMIC Desktop, drawn on a canvas, with the game's own fixed
  tick. It replaces the terminal front-end, which drew badly in COSMIC Terminal
  (`stty raw` turns off output processing, so frames staircased).
- A finished game stays on its end screen until a new game or quit (the
  terminal version quit itself two seconds after game over), and it is banked
  into the record once (the terminal version banked it again on every key on
  the end screen and on exit).
- Shipped inside the shared [`pengy-games`](../pengy-games/) binary with the
  other seven games (one libcosmic for all eight): `/usr/bin/pengy-rogue` is a
  symlink to it. The launcher (`io.catbee.pengyos.IceRogue.desktop`) and an icon
  are in the Desktop overlay. The Buildroot recipe and `launcher/` are gone;
  Core and Server do not ship the game.
- The program moved into the library (`src/cli.rs`, `pub fn main(argv)`, the
  window its child module), so `pengy-games` can call it; `src/main.rs` only
  hands it the command line.
- The rules (`src/lib.rs`) and the headless harness (`--headless`, `--replay`,
  `--record`, `--print-frame`) are unchanged and still need no display; with
  no harness flag and no window built in, the binary exits 2 and says so.

### Fixed

- The end screen stays up. A finished game used to quit the program 2 seconds
  after it ended (3 in Invaders; in Lander any key, `r` included, quit), so a
  game launched from the menu closed its window before the score could be
  read. It is now frozen on the death or win screen until `q`.
- Corridors: the vertical-first bend carved `carve_v(a.cy, a.cx, b.cx)` (row and
  column swapped), so half the joins dug a stray strip near the left edge
  instead of the real leg, and the connectivity check silently threw away
  the floors that broke. Every corridor tile is now on an L between
  consecutive rooms (`every_corridor_tile_is_on_an_l_between_consecutive_rooms`).
- The demo penguin explores by a breadth-first walk to the nearest frontier,
  stairs or boss over the tiles it has seen; the local rule wandered a lit
  room for ever once the corridors were right. `fixtures/demo.replay` is
  re-recorded (seed 7: 280 turns, floor 4, 8 kills, score 417).

## [0.1.0] — 2026-10-18

The first PengyOS pack-in rogue: Rogue (1980), the original dungeon crawl, for
the terminal. Rust 2021, `std` only, zero dependencies.

### Added

- **The game** (`src/lib.rs`, pure): the seeded xorshift64* generator; five
  procedurally generated floors of 4..9 non-overlapping rooms (4x4..10x6) joined
  by L-shaped corridors, with connectivity **proven at generation time** by a
  4-connected flood fill from the entry to the stairs and to the middle of every
  room (a layout that fails is thrown away and the floor is regenerated); the
  penguin `@` with 12 hp, attack 1d4+1, defence 0; the rat (2 hp, 1 dmg), the
  ice crab (4 hp, 2 dmg), the wolf (6 hp, 3 dmg) and **The Thaw** (20 hp, 4 dmg)
  who spawns on floor 5 only; the attack roll `1d4+1+weapon−defence`, floored at
  0, and flat monster damage floored the same way; turn-based monster turns
  (adjacent hits you, in sight it walks at you, out of sight it wanders);
  bump-to-attack; the fish (`!`, heals 3), the scroll (`?`, maps the floor), the
  gold (`$`, 1..5, scores) and the parka (`+`, +1 defence), one item per tile,
  applied on pickup; the stairs and the descent, carrying hp/gold/kills/defence;
  **permadeath** (no save, no undo); the win on The Thaw; the score formula
  `floor*100 + gold + kills`; the field of view, radius 7 with Bresenham line of
  sight, walls blocking, explored-not-seen tiles kept as the dim layer and
  never-seen tiles blank.
- **The terminal front-end** (`src/main.rs`): `stty raw -echo` through
  `std::process::Command` with a `Drop` guard *and* a panic hook that run
  `stty sane`; the alternate screen (`?1049h`/`?1049l`) with the cursor hidden;
  one home + full repaint per frame (`\x1b[H` plus the frame), no per-cell
  addressing, so no flicker; a stdin reader thread feeding the loop over
  `std::sync::mpsc`; a fixed 110 ms timestep that sleeps instead of spinning;
  arrow keys translated to `hjkl`; `q` and `Ctrl-C` quit cleanly.
- **Headless, the way the gate proves it** (`--headless`, `--replay`,
  `--state`, `--save`, `--record`, `--ticks`, `--print-frame`, `--autopilot`):
  with no TTY, or with any of those flags, the game never touches the terminal
  and never reads keys — it runs the scripted path, prints the plain-text state
  as `key=value` lines and exits 0. A replay is one line of keys per tick, blank
  for no input, `#` for a comment. `hash=` is FNV-1a over the whole position, so
  same seed + same script ⇒ same hash. Exit 2 for a usage error, 66 for an
  unreadable position or replay file.
- **The frame** (`render(&Game, RenderOpts) -> String`, `frame(&Game)`): a pure
  function of state, designed for 80x24 — the 54x15 map with its border, a
  24-column status panel beside it, the title, the status line, the message line
  and the key legend — degrading in a fixed order (panel, border, title, status,
  legend) and never dropping the map. `--mono` draws plain ASCII (no box
  drawing, no dim, no colour); colour is opt-in and `NO_COLOR` is honoured.
- **The record** at `$XDG_DATA_HOME/pengyos/games/pengy-rogue/best.txt`
  (default `~/.local/share/...`): best score, the deepest floor, the most kills,
  games played, wins, the seed. Tier-1 mutable, under `$HOME`, never `/usr`; a
  write failure is ignored.
- **Fixtures**: `fixtures/boss.state` (floor 5, The Thaw wounded to 1 hp,
  standing directly below the penguin), `fixtures/win.replay` (the one-swing
  session that beats it: `state=win`, `kills=8`, `score=517`),
  `fixtures/demo.replay` (the 400-tick autopilot session from seed 12: floor 4,
  `score=412`, reproducible to the hash).
- **Tests** (`cargo test`, 44 tests, well under a second): 29 rule tests, 15 CLI
  tests driving the real binary with no pty, 10 tool-contract tests.
- **The launcher** `launcher/pengy-rogue.desktop` (`Terminal=true`,
  `Categories=Game;`).
- **The Buildroot recipe skeleton**
  `buildroot-external/package/pengy-rogue/` (`Config.in` + `pengy-rogue.mk`):
  the `local` site method over this repo, an offline `cargo build --release`,
  the binary to `/usr/bin/pengy-rogue`, the launcher to
  `/usr/share/applications/`. Image wiring (the `Config.in` source, the layer
  defconfig, the rootfs overlay) is deliberately left to the integration pass.
