# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-10-18

The first PengyOS pack-in solitaire: Klondike, draw three, unlimited redeals,
for the terminal. Rust 2021, `std` only, zero dependencies.

### Added

- **The game** (`src/lib.rs`, pure): the 52-card deck and the seeded
  xorshift64* shuffle; the 1..7 Klondike deal; the stock, the waste, the four
  foundations and the seven tableau columns; tableau builds down by rank with
  alternating colours, moving a run as a unit; foundations up by suit from the
  Ace; a King only onto an empty column; auto-flip the moment a card is
  uncovered; draw three with unlimited redeals (an empty stock turns the waste
  back over, reversed and face down); auto-collect of the **safe** cards only,
  applied until stable, so it is idempotent; multi-level undo capped at 50 that
  restores an identical state hash; standard Klondike scoring (+5 waste to
  tableau, +10 to a foundation, +5 flip, −15 foundation back down, −100 per
  recycle) floored at zero; a move counter and an elapsed timer; win at 52.
- **The terminal front-end** (`src/main.rs`): `stty raw -echo` through
  `std::process::Command` with a `Drop` guard *and* a panic hook that run
  `stty sane`; the alternate screen (`?1049h`/`?1049l`) with the cursor hidden;
  one home + full repaint per frame (`\x1b[H` plus the frame), no per-cell
  addressing, so no flicker; a stdin reader thread feeding the loop over
  `std::sync::mpsc`; a fixed 60 ms timestep that sleeps instead of spinning;
  `q` and `Ctrl-C` quit cleanly.
- **Headless, the way the gate proves it** (`--headless`, `--replay`,
  `--state`, `--save`, `--record`, `--ticks`, `--print-frame`): with no TTY, or
  with any of those flags, the game never touches the terminal and never reads
  keys — it runs the scripted path, prints the plain-text state as `key=value`
  lines and exits 0. A replay is one line of keys per tick, blank for no input,
  `#` for a comment, `_` for the space key. `hash=` is FNV-1a over the whole
  position, so same seed + same script ⇒ same hash.
- **The frame** (`frame(&Game) -> String`): a pure function of state, designed
  for 80x24, degrading in a fixed order — border, score panel, title, key
  legend — and never dropping the cards: a pile that outgrows the grid
  compresses its cascade and counts the overflow with a `+n` marker. `--mono`
  draws plain ASCII; colour (red suits, dim decoration) is opt-in and
  `NO_COLOR` is honoured.
- **The record** at `$XDG_DATA_HOME/pengyos/games/pengy-solitaire/best.txt`
  (default `~/.local/share/...`): best score, the moves that scored it, the
  fastest win, games played, wins, the seed. Tier-1 mutable, under `$HOME`,
  never `/usr`; a write failure is ignored.
- **Fixtures**: `fixtures/near-win.state` (a legal position one move from
  winning — 51 cards up, the King of Diamonds on the waste),
  `fixtures/win.replay` (the seven-tick script that wins it: `state=win`,
  `foundations=52`, `score=700`, `moves=121`), `fixtures/demo.replay` (a
  900-tick session from seed 21).
- **Tests** (`cargo test`, 68 tests, under a second): 40 rule tests, 19 CLI
  tests driving the real binary with no pty, 9 tool-contract tests.
- **The launcher** `launcher/pengy-solitaire.desktop` (`Terminal=true`,
  `Categories=Game;`).
- **The Buildroot recipe skeleton**
  `buildroot-external/package/pengy-solitaire/` (`Config.in` +
  `pengy-solitaire.mk`): a `local`-source recipe that runs
  `cargo build --offline --release` on this crate and installs
  `/usr/bin/pengy-solitaire` plus the menu entry. Image wiring — the `source`
  line in `buildroot-external/Config.in`, the layer `.config`s, the rootfs
  overlay — is deliberately left to the single integration pass that wires all
  ten pack-in games.
