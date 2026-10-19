# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Changed

- Ice Lander is a window now: a libcosmic app (`src/app.rs`, the `cosmic`
  feature) for the COSMIC Desktop, drawn on a canvas, with the game's own fixed
  tick. It replaces the terminal front-end, which drew badly in COSMIC Terminal
  (`stty raw` turns off output processing, so frames staircased).
- A finished game stays on its end screen until a new game or quit (the
  terminal version quit itself two seconds after game over), and it is banked
  into the record once (the terminal version banked it again on every key on
  the end screen and on exit).
- Shipped inside the shared [`pengy-games`](../pengy-games/) binary with the
  other seven games (one libcosmic for all eight): `/usr/bin/pengy-lander` is a
  symlink to it. The launcher (`io.catbee.pengyos.IceLander.desktop`) and an icon
  are in the Desktop overlay. The Buildroot recipe and `launcher/` are gone;
  Core and Server do not ship the game.
- The program moved into the library (`src/cli.rs`, `pub fn main(argv)`, the
  window its child module), so `pengy-games` can call it; `src/main.rs` only
  hands it the command line.
- The rules (`src/lib.rs`) and the headless harness (`--headless`, `--replay`,
  `--record`, `--print-frame`) are unchanged and still need no display; with
  no harness flag and no window built in, the binary exits 2 and says so.
- A fresh window, a new game and each new wave wait, hovering, for the first
  control: the probe no longer starts falling before the player is ready.

### Fixed

- `n` (new terrain) after a crash starts a new run, as `r` does. It used to
  fly the next wave with the crashed run's score intact; the terminal version
  hid that by quitting on any key after a crash
  (`new_terrain_after_a_crash_is_a_new_run_not_a_second_life`).

## [0.1.0] - 2026-10-18

### Added

- **Ice Lander, the PengyOS pack-in lander (game 7/10).** The 1979 Lunar Lander
  cart on a 60x24 terminal ridge: moon (1.62 m/s^2) or asteroid (5.0 m/s^2)
  gravity, a 4.0 m/s^2 engine, 100 units of fuel at 1 unit per second of
  thrust, 90 deg/s rotation, and a ridge broken by 2-4 flat pads worth 1x, 2x
  and 5x. Touchdown is a landing only when |vx| < 1.0 m/s, |vy| < 2.0 m/s and
  |tilt| < 5 degrees, on a pad; anything else is a crash and ends the run.
  `n` flies the next wave on fresh terrain and pays a wave bonus.
- `src/lib.rs` — every rule and the renderer, **pure**: integer fixed point
  (no floating point, no `libm`), a 120-entry sine table, a seeded
  xorshift64* generator, and `render(&Game) -> String` as a pure function of
  state. Zero external crates, `std` only.
- `src/main.rs` — the terminal front-end: `stty raw -echo` through
  `std::process::Command`, a stdin reader thread feeding the loop over
  `std::sync::mpsc`, the alternate screen with one home+repaint per frame, a
  fixed 1/30 s timestep that sleeps instead of spinning, and a `Drop` guard
  plus a panic hook that restore the terminal.
- Headless contract: `--headless`, a non-tty stdin and `--replay FILE` never
  touch the terminal, print plain text and exit 0 — the shape the repo gate
  and `pengyos-qemu-verify` have (no pty). `--record FILE` writes a session in
  the replay format; `fixtures/win.replay` is a committed 3900-tick, 6-wave,
  8390-point session with a pinned state hash.
- Keys: `space` thrust, `a`/`d` rotate, `n` new terrain, `r` restart, `q`
  quit (Ctrl-C quits too). `Escape` is inert on purpose: terminals deliver the
  arrow keys as `ESC [ A`.
- High score at `$XDG_DATA_HOME/pengyos/games/pengy-lander/best.txt`
  (default `~/.local/share/...`): Tier-1 mutable, under `$HOME`, never `/usr`.
  `--data-dir` redirects it, `--no-save` disables it.
- `--mono` (ASCII-only glyphs), `--no-color` and `NO_COLOR`, `--width`/
  `--height`, `--print-frame`, `--body moon|asteroid`, `--help`, `--version`.
  Designed for 80x24; in a smaller window the panel, then the title, then the
  border, then the legend go, and the field is clipped from the top.
- Tests: `tests/rules.rs` (integration of gravity and thrust over N steps, the
  fuel floor, the landing limits from just inside to just past each one, the
  order of blame, terrain generation over 200 seeds, score monotonicity,
  determinism, the asteroid's impossibility, rendering), `tests/cli.rs` (the
  real binary with no pty: exit codes 0/2/66, `--replay`, `--record`,
  `NO_COLOR`, `--mono`, frame geometry, persistence in the data dir and
  nowhere else) and `tests/github_ready.rs` (the tool-directory contract).
- `launcher/pengy-lander.desktop` (`Terminal=true`, `Categories=Game;`) and
  the Buildroot recipe skeleton at
  `buildroot-external/package/pengy-lander/` (`Config.in` + `pengy-lander.mk`),
  a `local`-source offline `cargo build --release` into `/usr/bin`. Image
  wiring is deliberately left to the integration pass.
