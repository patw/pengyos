# Changelog

All notable changes to `pengy-invaders` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Thaw Invaders is a window now: a libcosmic app (`src/app.rs`, the `cosmic`
  feature) for the COSMIC Desktop, drawn on a canvas, with the game's own fixed
  tick. It replaces the terminal front-end, which drew badly in COSMIC Terminal
  (`stty raw` turns off output processing, so frames staircased).
- A finished game stays on its end screen until a new game or quit (the
  terminal version quit itself two seconds after game over), and it is banked
  into the record once (the terminal version banked it again on every key on
  the end screen and on exit).
- Shipped inside the shared [`pengy-games`](../pengy-games/) binary with the
  other seven games (one libcosmic for all eight): `/usr/bin/pengy-invaders` is a
  symlink to it. The launcher (`io.catbee.pengyos.ThawInvaders.desktop`) and an icon
  are in the Desktop overlay. The Buildroot recipe and `launcher/` are gone;
  Core and Server do not ship the game.
- The program moved into the library (`src/cli.rs`, `pub fn main(argv)`, the
  window its child module), so `pengy-games` can call it; `src/main.rs` only
  hands it the command line.
- The rules (`src/lib.rs`) and the headless harness (`--headless`, `--replay`,
  `--record`, `--print-frame`) are unchanged and still need no display; with
  no harness flag and no window built in, the binary exits 2 and says so.

### Fixed

- The laser was hitscan: on its first step it killed the lowest alien in its
  column wherever that alien was, before checking shields or bombs, so shots
  went through your own shields. It now kills only on the alien's row
  (`a_laser_takes_its_flight_time_and_a_shield_below_the_alien_stops_it`).
- The autopilot leads its shots by the laser's flight time
  (`Game::formation_x_after`) and steps out from under falling bombs;
  `fixtures/win.replay` is re-recorded (seed 7, wave 1 cleared, score 3350).

## [0.1.0] - 2026-10-18

The first release: Thaw Invaders, the PengyOS pack-in game in the invaders slot.

### Added

- `src/lib.rs` — **every rule and the renderer, pure**: no I/O, no clock, no
  threads, no files. A hand-written seeded xorshift64* is the only source of
  randomness, so a run is a function of `(seed, script)`. The 60x24 field; the
  11x5 formation, 2 cells wide and spaced 4 apart, stepping one column every
  `8 * alive / 55 / wave` ticks (floor 1) and reversing *and* descending at each
  wall; the ship, 2 columns per keypress on row 23; one laser in flight with a
  2-tick cooldown, 2 rows per tick, killing the lowest alien in its column;
  per-alien bomb drops at 0.002 + 0.0005/wave, 3 bombs max, 1 row per tick;
  three destructible 8x4 shields that erode on any hit and grind under the
  formation; 3 lives; wave advance one row lower and faster; the breach; score
  by row (10..50) and 100 x wave. Rendering is
  `render(&Game, RenderOpts) -> String` (and `frame(&Game)`): same state, same
  bytes, with the border/title/legend degrade and the HUD riding on the field's
  top row at 80x24.
- `src/main.rs` — the terminal front-end: `stty raw -echo` via
  `std::process::Command` with the restore in a `Drop` guard *and* a panic hook,
  a stdin reader thread feeding the loop over `std::sync::mpsc`, the alternate
  screen with one home+repaint per frame, a fixed-timestep loop that sleeps
  (40 ms, no busy-wait), arrow-key translation, Ctrl-C quit, the CLI
  (`--seed`, `--headless`, `--replay`, `--record`, `--ticks`, `--wave`,
  `--autopilot`, `--mono`, `--no-color`, `--print-frame`, `--width`, `--height`,
  `--data-dir`, `--no-save`, `--help`, `--version`), `NO_COLOR` support, the
  no-pty headless path, the replay harness, and the high score under
  `$XDG_DATA_HOME/pengyos/games/pengy-invaders/`.
- `tests/rules.rs` — the rules contract: geometry, the step period and its
  floor, reversal and descent at both edges, the lowest alien in a column, the
  laser's cooldown and speed, laser/shield and bomb/shield erosion, holes in a
  shield, bomb rate by wave, the 3-bomb cap, three lives, wave advance, the
  breach, determinism, and the renderer's window contract.
- `tests/cli.rs` — the built binary with a pipe for stdin: `--help`,
  `--version`, usage errors, the headless path, `NO_COLOR`, `--mono`, the
  window sizes, the committed replay fixture, replay determinism, the
  high-score file and its location, `--no-save`, and a scripted session that
  ends in the same state every time.
- `tests/github_ready.rs` — the tool-directory contract: the docs, the
  verifier, the empty `[dependencies]`, the launcher, the Buildroot recipe
  skeleton, and the fixture's shape.
- `fixtures/win.replay` — a committed 900-line session that clears three waves
  for 6040 points on seed 7, replayed by the acceptance harness.
- `launcher/pengy-invaders.desktop` — the menu entry: `Exec=pengy-invaders`,
  `Terminal=true`, `Categories=Game;`.
- `buildroot-external/package/pengy-invaders/` — the recipe skeleton
  (`Config.in` + `pengy-invaders.mk`): the local site method over this repo, an
  offline `cargo build --release`, the binary to `/usr/bin`, the launcher to
  `/usr/share/applications`. Image wiring is a separate integration job.
- `README.md`, `spec.md`, `LICENSE` (MIT), `CHANGELOG.md`, `verify.toml`.
