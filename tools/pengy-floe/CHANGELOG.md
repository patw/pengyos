# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Changed

- Flappy Pengy is a window now: a libcosmic app (`src/app.rs`, the `cosmic`
  feature) for the COSMIC Desktop, drawn on a canvas, with the game's own fixed
  tick. It replaces the terminal front-end, which drew badly in COSMIC Terminal
  (`stty raw` turns off output processing, so frames staircased).
- A finished game stays on its end screen until a new game or quit (the
  terminal version quit itself two seconds after game over), and it is banked
  into the record once (the terminal version banked it again on every key on
  the end screen and on exit).
- Shipped inside the shared [`pengy-games`](../pengy-games/) binary with the
  other seven games (one libcosmic for all eight): `/usr/bin/pengy-floe` is a
  symlink to it. The launcher (`io.catbee.pengyos.FlappyPengy.desktop`) and an icon
  are in the Desktop overlay. The Buildroot recipe and `launcher/` are gone;
  Core and Server do not ship the game.
- The program moved into the library (`src/cli.rs`, `pub fn main(argv)`, the
  window its child module), so `pengy-games` can call it; `src/main.rs` only
  hands it the command line.
- The rules (`src/lib.rs`) and the headless harness (`--headless`, `--replay`,
  `--record`, `--print-frame`) are unchanged and still need no display; with
  no harness flag and no window built in, the binary exits 2 and says so.
- A fresh window, and a new game, wait for the first flap: the penguin no longer
  falls to the floor before the player has touched a key.

## [0.1.0] - 2026-10-18

### Added

- The game: **Flappy Pengy**, a one-button penguin flapper for the terminal —
  40x20 playfield, 80 ms fixed tick, gravity `+1` clamped to 3, a flap that sets
  `vy = -2` and never stacks, 2-column ice pillars every 12 columns, a gap of 8
  rows narrowing to 5 by score 25 with at least 2 rows of clearance always,
  carrying speed ramping from 1 column per 2 ticks to 1 per tick by score 30,
  one point per pillar passed, ceiling/floor/pillar death, medals at
  10/25/50/100, and an instant restart (`space` on the death screen).
- `src/lib.rs` — **every rule and the renderer, pure**: no I/O, no clock, no
  threads, no files. A hand-written seeded xorshift64* is the only source of
  randomness, so a run is a function of `(seed, script)`. Rendering is
  `render(&Game, RenderOpts) -> String`: same state, same bytes.
- `src/main.rs` — the terminal front-end: `stty raw -echo` via
  `std::process::Command` with the restore in a `Drop` guard *and* a panic hook,
  a stdin reader thread feeding the loop over `std::sync::mpsc`, the alternate
  screen with one home+repaint per frame, a fixed-timestep loop that sleeps
  instead of spinning, and the CLI.
- Headless contract: `--headless` (and any run with a non-tty stdin) never
  renders and never reads keys — it runs the built-in autopilot, prints
  plain-text state and exits 0. `--replay FILE` plays a recorded session
  (one line per tick, blank = no input, `#` = comment), stops at the death
  tick, and prints the result as `key=value` lines. `--record FILE` writes a
  session out in that format. This is the acceptance harness: the repo gate and
  `pengyos-qemu-verify` have no pty.
- Determinism surface: `canonical()`, `state_hash()` (FNV-1a over the canonical
  state) and `result_kv()`, so a run can be compared by fingerprint.
- High score persistence at `$XDG_DATA_HOME/pengyos/games/pengy-floe/best.txt`
  (default `~/.local/share/…`) — Tier-1 mutable, under `$HOME`, never `/usr`;
  `--no-save` reads and writes nothing; a failed write is ignored.
- Presentation: colour SGR by default, `NO_COLOR` and `--mono` emit no escape
  bytes at all, and the frame degrades by dropping the border, then the score
  panel, then clipping — every row is clipped to the window width.
- 40 tests, all green in well under a second: 25 rules tests (gravity
  integration, flap clamps, gap legality swept over 400 seeds, one point per
  pillar, boundary-cell collisions for ceiling/floor/pillar, the forgiving
  hitbox, the speed ramp, spawn spacing, pause/restart/medals, the golden
  scripted end state, frame purity and the degrade rules) and 15 end-to-end
  tests that run the real binary with no pty (`--help`, `--headless`, piped
  stdin, the committed fixture, record→replay round-trip, `NO_COLOR`, `--mono`,
  exit codes 0/2/66, persistence in the data dir and nowhere else).
- `fixtures/win.replay`: a committed 1039-tick session, seed 2, score 70,
  silver medal, pinned state hash.
- Tool-directory contract: `README.md`, `spec.md`, `LICENSE` (MIT,
  "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`, `verify.toml`
  (`cargo test --quiet` in this directory), and a `Cargo.toml` whose
  `[dependencies]` table is empty and stays empty.
- `launcher/pengy-floe.desktop`: `Exec=pengy-floe`, `Terminal=true`,
  `Categories=Game;`.
- Buildroot recipe skeleton at `buildroot-external/package/pengy-floe/`
  (`Config.in` + `pengy-floe.mk`): a `local`-source recipe that runs
  `cargo build --offline --release` on this crate and installs
  `/usr/bin/pengy-floe` plus the `.desktop` file. No download, so no digest to
  pin; no crates, so nothing to vendor. Image wiring (`Config.in` source line,
  layer `.config`, menu) is deliberately left to the integration pass.
