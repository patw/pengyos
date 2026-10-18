# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-18

### Added

- The game: **Glacier Blocks**, the pack-in falling-blocks game for the
  PengyOS terminal — a 10x20 well with two hidden spawn rows, all seven
  tetrominoes in the standard SRS orientations, a 7-bag randomiser, gravity of
  800/720/630/550/470/380/300/220/130/100/80 ms by level (level = 1 + lines/10,
  80 ms from level 11 up), rotation with wall kicks (in place, +/-1 column,
  +/-2 columns, up one row), a 300 ms lock delay that a successful move resets
  up to 15 times, soft drop at 1 point per row and hard drop at 2, 100/300/500/800
  x level for 1/2/3/4 lines with a correct collapse, a next-3 preview, a hold
  slot (once per piece), a landing ghost, pause, restart, quit, and a persisted
  best score.
- `src/lib.rs` — **every rule and the renderer, pure**: no I/O, no clock, no
  threads, no files. A hand-written seeded xorshift64* is the only source of
  randomness, so a run is a function of `(seed, script)`. Rendering is
  `render(&Game, RenderOpts) -> String`: same state, same bytes. The demo
  player (`autopilot_keys`) is a stacking strategy with a strict priority order
  — clear the line, keep the stack low, keep it hole-free, keep the right-hand
  column as the I well — so the headless path plays a real game (168 lines,
  level 17 on seed 42) instead of building a tower.
- `src/main.rs` — the terminal front-end: `stty raw -echo` via
  `std::process::Command` with the restore in a `Drop` guard *and* a panic hook,
  a stdin reader thread feeding the loop over `std::sync::mpsc`, the alternate
  screen with one home+repaint per frame, a fixed-timestep loop that sleeps
  instead of spinning, arrow-key decoding, the CLI, and the best-score file.
- Headless contract: `--headless` (and any run with a non-tty stdin) never
  renders and never reads keys — the built-in player plays, the plain-text
  state is printed, exit 0. `--replay FILE` plays a recorded session (one line
  of input per tick, blank = no input, `#` = comment, `_` = the hard drop),
  stops at the game-over tick or EOF, prints `key=value` lines and exits 0.
  `--record FILE` writes one. `--print-frame`, `--width`, `--height`, `--mono`,
  `--no-color` (and `NO_COLOR`), `--data-dir`, `--no-save`, `--help`,
  `--version`. Exit 0 / 2 (usage) / 66 (unreadable replay).
- Persistence: `$XDG_DATA_HOME/pengyos/games/pengy-blocks/best.txt` (`score=`,
  `lines=`, `level=`, `games=`, `seed=`), Tier-1 mutable under `$HOME`; nothing
  is written anywhere else, and a write failure is ignored.
- Tests: 70 rule tests (`tests/rules.rs`) covering the 7-bag invariant over 70
  draws from seven seeds, every wall-kick case (in place, +1, -1, +2, -2, up 1,
  and the refused rotation), line-clear collapse on hand-built boards (a single
  row, a 4-line clear, and a non-adjacent pair), the scoring table and its
  level multiplier, the gravity ladder and the level thresholds, the lock delay
  (locks at 300 ms, resets on a successful move, capped at 15 resets), the
  spawn-overlap game over, the frozen finished game, the key table, the
  determinism of `(seed, script)`, the pinned golden end state (seed 42, score
  174528, 168 lines, level 17, 464 pieces, hash `53cbffd1a21e3566`), frame
  geometry and the degrade rules, mono/no-colour, and the plain-state report;
  21 end-to-end tests (`tests/cli.rs`) that run the real binary with no pty
  (`--help`, `--version`, headless, piped stdin, reproducibility, the committed
  fixture, record->replay round-trip, a hand-written script, `NO_COLOR`,
  `--mono`, small windows, exit codes 0/2/66, persistence in the data dir and
  nowhere else); and 10 tool-directory contract tests (`tests/github_ready.rs`).
- `fixtures/win.replay`: a committed 1940-tick session, seed 42, 168 lines,
  level 17, score 174528, pinned state hash `53cbffd1a21e3566`.
- Tool-directory contract: `README.md`, `spec.md`, `LICENSE` (MIT,
  "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`, `verify.toml`
  (`cargo test --quiet` in this directory), and a `Cargo.toml` whose
  `[dependencies]` table is empty and stays empty.
- `launcher/pengy-blocks.desktop`: the Desktop menu entry (`Terminal=true`,
  `Categories=Game;`).
- `../../buildroot-external/package/pengy-blocks/`: the Buildroot recipe
  skeleton (`Config.in` + `pengy-blocks.mk`) that builds this crate from the
  repo with an offline `cargo build --release` and installs the binary to
  `/usr/bin/pengy-blocks` plus the `.desktop` file. Image wiring
  (`buildroot-external/Config.in`, the layer `.config`s, the rootfs overlay) is
  deliberately left to the integration pass.
