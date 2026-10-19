# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Changed

- Penguin Pong is a window now: a libcosmic app (`src/app.rs`, the `cosmic`
  feature) for the COSMIC Desktop, drawn on a canvas, with the game's own fixed
  tick. It replaces the terminal front-end, which drew badly in COSMIC Terminal
  (`stty raw` turns off output processing, so frames staircased).
- A finished game stays on its end screen until a new game or quit (the
  terminal version quit itself two seconds after game over), and it is banked
  into the record once (the terminal version banked it again on every key on
  the end screen and on exit).
- Shipped inside the shared [`pengy-games`](../pengy-games/) binary with the
  other seven games (one libcosmic for all eight): `/usr/bin/pengy-pong` is a
  symlink to it. The launcher (`io.catbee.pengyos.PenguinPong.desktop`) and an icon
  are in the Desktop overlay. The Buildroot recipe and `launcher/` are gone;
  Core and Server do not ship the game.
- The program moved into the library (`src/cli.rs`, `pub fn main(argv)`, the
  window its child module), so `pengy-games` can call it; `src/main.rs` only
  hands it the command line.
- The rules (`src/lib.rs`) and the headless harness (`--headless`, `--replay`,
  `--record`, `--print-frame`) are unchanged and still need no display; with
  no harness flag and no window built in, the binary exits 2 and says so.

## [0.1.0] - 2026-10-18

### Added

- The game: **Penguin Pong**, the 1972 arcade original for the terminal — a
  60x20 field, 4-row paddles on columns 1 and 58, a 40 ms tick, a ball that
  crosses the field one cell per tick at serve speed, a bounce angle taken from
  the paddle row the ball met (inner rows ±10°, edge rows ±50°, every angle a
  multiple of 10°), a speed ladder of +5% per hit capped at twice the serve
  speed, paddle-motion spin, wall bounces, a 1-second serve countdown toward the
  player who lost the point, first to 11 with win-by-2 at 10-10 and a hard cap of
  15, a 900-tick rally cap so no point ever stalls, 1P against the penguin at
  difficulty 1/2/3 (caps 1/2/3 rows per tick, reaction delays 6/3/1 ticks) and 2P
  hotseat, pause, restart, quit, and a persisted best result.
- `src/lib.rs` — **every rule and the renderer, pure**: no I/O, no clock, no
  threads, no files. A hand-written seeded xorshift64* is the only source of
  randomness, so a run is a function of `(seed, script)`. Rendering is
  `render(&Game, RenderOpts) -> String`: same state, same bytes.
- `src/main.rs` — the terminal front-end: `stty raw -echo` via
  `std::process::Command` with the restore in a `Drop` guard *and* a panic hook,
  a stdin reader thread feeding the loop over `std::sync::mpsc`, the alternate
  screen with one home+repaint per frame, a fixed-timestep loop that sleeps
  instead of spinning, arrow-key decoding, the CLI, and the best-score file.
- Headless contract: `--headless` (and any run with a non-tty stdin) never
  renders and never reads keys — the built-in autopilot plays, the plain-text
  state is printed, exit 0. `--replay FILE` plays a recorded session (one line of
  input per tick, blank = no input, `#` = comment), stops at the match-over tick
  or EOF, prints `key=value` lines and exits 0. `--record FILE` writes one.
  `--print-frame`, `--width`, `--height`, `--mono`, `--no-color` (and `NO_COLOR`),
  `--data-dir`, `--no-save`, `--help`, `--version`. Exit 0 / 2 (usage) / 66
  (unreadable replay).
- Persistence: `$XDG_DATA_HOME/pengyos/games/pengy-pong/best.txt` (`wins=`,
  `margin=`, `rally=`, `seed=`), Tier-1 mutable under `$HOME`; nothing is written
  anywhere else, and a write failure is ignored.
- Tests: 36 rule tests (`tests/rules.rs`) covering the field and paddle
  geometry, the reflection angle table, `vy` from the angle and the speed, the
  5% ladder and its cap, points and serve direction, walls and the no-tunnelling
  rule at the speed cap, win-by-2 and the 15-point cap, the frozen finished
  match, the AI's cap over 500 ticks at every level, the reaction-delay ring, the
  key table, the seeded RNG, determinism, the pinned golden end state
  (seed 1, level 2, 11-5, hash `b936f7f3084410a2`), frame geometry and the
  degrade rules, mono/no-colour, plain-state output; 17 end-to-end tests
  (`tests/cli.rs`) that run the real binary with no pty (`--help`, `--version`,
  headless, piped stdin, reproducibility, the committed fixture, record→replay
  round-trip, the stop-at-end-tick report, a hand-written script, `NO_COLOR`,
  `--mono`, frame geometry, exit codes 0/2/66, persistence in the data dir and
  nowhere else); and 10 tool-directory contract tests (`tests/github_ready.rs`).
- `fixtures/win.replay`: a committed 7319-tick session, seed 7, level 2, 11-7,
  pinned state hash `070441adffab99fc`.
- Tool-directory contract: `README.md`, `spec.md`, `LICENSE` (MIT,
  "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`, `verify.toml`
  (`cargo test --quiet` in this directory), and a `Cargo.toml` whose
  `[dependencies]` table is empty and stays empty.
- `launcher/pengy-pong.desktop`: `Exec=pengy-pong`, `Terminal=true`,
  `Categories=Game;`.
- Buildroot recipe skeleton at `buildroot-external/package/pengy-pong/`
  (`Config.in` + `pengy-pong.mk`): a `local`-source recipe that runs
  `cargo build --offline --release` on this crate and installs
  `/usr/bin/pengy-pong` plus the `.desktop` file. No download, so no digest to
  pin; no crates, so nothing to vendor. Image wiring (`Config.in` source line,
  layer `.config`, menu) is deliberately left to the integration pass.
