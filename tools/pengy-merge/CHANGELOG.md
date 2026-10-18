# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-18

### Added

- The game: **Icefloe 2048**, a 2048-style sliding-floe puzzle for the
  terminal — 4x4 field (`--size 3..=8`), 110 ms fixed tick, a slide that packs
  every lane, fusing **from the direction of travel** with at most one fuse per
  floe per slide (`4 4 8 8` left is `8 16`, `2 4 2` does not chain), score =
  the sum of the fused values, a spawn after every slide that moved (2 at 90%,
  4 at 10%), win at 2048 with `c` to keep playing, game over when a slide
  changes nothing and nothing can fuse, single-step undo, restart, quit.
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
  plain-text state and exits 0. `--replay FILE` plays a recorded session (one
  line per tick, blank = no input, `#` = comment), stops at the end tick, and
  prints the result as `key=value` lines. `--record FILE` writes a session out
  in that format. This is the acceptance harness: the repo gate and
  `pengyos-qemu-verify` have no pty.
- Determinism surface: `canonical()`, `state_hash()` (FNV-1a over the canonical
  state) and `result_kv()`, so a run can be compared by fingerprint.
- Best-score persistence at
  `$XDG_DATA_HOME/pengyos/games/pengy-merge/best.txt` (default `~/.local/share/…`)
  — Tier-1 mutable, under `$HOME`, never `/usr`; `--no-save` reads and writes
  nothing; a failed write is ignored.
- Presentation: colour SGR by magnitude with a 3-tick flash on a fused or dealt
  floe, `NO_COLOR` and `--mono` emit no escape bytes at all, and the frame
  degrades by dropping the score panel, then the header, then the box, then
  clipping — every row is exactly the window width.
- 73 tests, all green in well under a second: 42 rules tests (the slide edge
  cases, no-double-fuse, score = sum of fuses, spawn placement swept over 400
  seeds, the 90/10 spawn split, game-over detection, winning and continuing,
  undo/restart/quit keys, key parsing, determinism, the golden scripted end
  state, the committed fixture, 5x5, frame geometry and the degrade rules), 21
  end-to-end tests that run the real binary with no pty (`--help`, `--headless`,
  piped stdin, the committed fixture, record→replay round-trip, a hand-written
  script, `NO_COLOR`, `--mono`, frame geometry, exit codes 0/2/66, persistence
  in the data dir and nowhere else), and 10 tool-directory contract tests.
- `fixtures/win.replay`: a committed 549-tick session, seed 7, score 7976, a
  512 floe, pinned state hash `9e91c46c6aafb114`.
- Tool-directory contract: `README.md`, `spec.md`, `LICENSE` (MIT,
  "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`, `verify.toml`
  (`cargo test --quiet` in this directory), and a `Cargo.toml` whose
  `[dependencies]` table is empty and stays empty.
- `launcher/pengy-merge.desktop`: `Exec=pengy-merge`, `Terminal=true`,
  `Categories=Game;`.
- Buildroot recipe skeleton at `buildroot-external/package/pengy-merge/`
  (`Config.in` + `pengy-merge.mk`): a `local`-source recipe that runs
  `cargo build --offline --release` on this crate and installs
  `/usr/bin/pengy-merge` plus the `.desktop` file. No download, so no digest to
  pin; no crates, so nothing to vendor. Image wiring (`Config.in` source line,
  layer `.config`, menu) is deliberately left to the integration pass.
