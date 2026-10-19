# pengy-pong — Penguin Pong

Two paddles, one ball, first to eleven. Play the penguin at difficulty 1, 2 or
3, or a friend on the same keyboard.

The 1972 arcade original, rebuilt for the PengyOS desktop: a 60x20 field,
4-row paddles, a ball that crosses it one cell per tick, a bounce angle that
comes from the row the ball met the paddle on, a speed that climbs 5% per hit up
to twice the serve speed, a 1-second countdown between points, and a score that
persists. It is one of the ten pack-in games PengyOS ships — Windows had
Solitaire and Minesweeper, the Atari 2600 had its cart, BSD had `greedy` and
`phantasia`; PengyOS ships ten little complete games, and this is one of them.

Rust 2021. The rules are **`std` only**; the window is libcosmic, like the
other PengyOS Desktop apps. No network, no root, no daemon.

## Play it

On a PengyOS Desktop it is in the App Library as **Penguin Pong** (Games). From the
repo, on a build host with the COSMIC toolchain:

```sh
cd tools/pengy-pong
cargo run --release --features cosmic   # the window
cargo run -- --help                     # every flag and control
cargo run -- --headless --seed 7 --difficulty 2   # prove it works with no display
```

| Control | What it does |
|---|---|
| W / S | Your paddle up / down, for as long as it is held. |
| up / down | Your paddle too in one-player; the right-hand paddle in two-player (hotseat). |
| 1 / 2 / 3 | Penguin difficulty (Easy, Medium, Hard). Picks one-player. |
| M | Toggle one-player / two-player. The header has both. |
| space | Serve now (skip the 1-second countdown). |
| Esc / P | Pause / resume. |
| Ctrl+N / F2 | New match (next seed in the seed family, best kept). |
| Ctrl+Q | Quit. |

## The rules, exactly

- The field is 60x20. The paddles are 4 rows tall and 1 column wide, on columns
  1 and 58. The ball is 1 cell.
- A tick is 40 ms. The ball moves 1 cell per tick along the field at serve
  speed; its `vy` changes only when a paddle hits it.
- **The bounce angle comes from the row the ball met the paddle on**: the two
  inner rows give ±10 degrees (the shallowest bounce, and the closest a 4-row
  paddle gets to straight), the two edge rows give ±50 degrees. Every angle is a
  multiple of 10 degrees.
- **The speed rises 5% per paddle hit, capped at twice the serve speed**, and
  resets to serve speed on the next point.
- The serve alternates and travels **toward the player who lost the point**,
  after a 1-second countdown. `space` serves early.
- The top and bottom walls reflect the ball. A paddle hit is a *crossing* of the
  paddle face, so a fast ball can never tunnel through a paddle.
- **First to 11 wins. At 10-10 a two-point margin is required. 15 is the hard
  cap.**
- A paddle that is moving when it hits the ball adds spin, which is what makes a
  rally develop instead of looping for ever.
- A rally longer than 900 ticks (36 s) is awarded to the player who hit the ball
  last — the other one has had the whole rally to return it. This is the stall
  guard: every point ends.
- The penguin's difficulty caps its speed at **1 / 2 / 3 rows per tick** and
  gives it a reaction delay of **6 / 3 / 1 ticks**: it only ever plays the ball
  as it was that many ticks ago. Level 1 is for learning the angle table, level
  2 is a match, level 3 is the one that beats you.

## Headless: how the game is proven to work

The repo gate and `pengyos-qemu-verify` have no display, so the game must
work without one. It does: with `--headless` or `--replay` it never opens a
window, never reads keys, prints plain text and exits 0.

```sh
pengy-pong --headless --seed 7 --ticks 60000 --no-save   # the built-in autopilot plays
pengy-pong --replay fixtures/win.replay --seed 7 --mode 1p --difficulty 2
pengy-pong --headless --seed 21 --record /tmp/you.replay --no-save   # record a session
```

A replay file is **one line of input per tick**: a line with characters is those
keypresses, a blank line is no input, a `#` line is a comment and is not a tick.
The harness stops at the match-over tick (a finished match is frozen) or at EOF,
prints the final state as `key=value` lines, and exits 0. `hash=` is FNV-1a over
the whole state: same seed, same script, same hash.

`fixtures/win.replay` is a committed 7319-tick session — seed 7, level 2, 11-7,
hash `070441adffab99fc`.

## Flags

`--seed N` (default 1), `--mode 1p|2p`, `--difficulty 1..=3` (default 2),
`--headless`, `--replay FILE`, `--record FILE`, `--ticks N`, `--autopilot`,
`--mono`, `--no-color`, `--print-frame`, `--width N`, `--height N`,
`--data-dir DIR`, `--no-save`, `--help`, `--version`. `NO_COLOR` is honoured.
Exit codes: **0** success, **2** usage error, **66** unreadable replay file.

Designed for **80x24**. In a smaller window it degrades: the title line goes,
then the status line, then the border (which carries the key legend), and a
short window clips the field from the bottom. The 60-column field is never
dropped. `--mono` draws in plain ASCII when the locale cannot render the box
drawing.

## Where the best result lives

`$XDG_DATA_HOME/pengyos/games/pengy-pong/best.txt` (default
`~/.local/share/pengyos/games/pengy-pong/best.txt`): matches won, best margin,
longest rally, the seed it happened on. Tier-1 mutable, under `$HOME`, never
`/usr`, never the repo. A failure to write is ignored — a read-only `$HOME`
must not cost you a game.

## Layout

| Path | What is in it |
|---|---|
| `src/lib.rs` | **Every rule and the renderer, pure**: no I/O, no clock, no threads, no files. The seeded xorshift64* is the only randomness, so a run is a function of `(seed, script)`. |
| `src/main.rs` | Hands the command line to `cli::main`: the whole program is in the library, so `pengy-games` can carry it too. |
| `src/cli.rs` | The command line, the record file, and the headless harness (`--headless`, `--replay`, `--record`); with no harness flag it opens the window. |
| `src/app.rs` | The window (libcosmic, behind the `cosmic` feature; a child module of `cli`): the game drawn on a canvas, the keyboard (and mouse) turned into the game's keys, the game's own fixed tick. |
| `tests/rules.rs` | The rules: field and paddles, the angle table, the speed ladder and its cap, points and serve direction, walls and the no-tunnel rule, win-by-2 and the 15 cap, the AI cap over 500 ticks, keys, determinism, the pinned golden end state, rendering. |
| `tests/cli.rs` | The real binary with no display: `--help`, `--headless`, `--replay`, `--record`, exit codes 0/2/66, `NO_COLOR`, `--mono`, frame geometry, persistence in the data dir and nowhere else. |
| `tests/github_ready.rs` | The tool-directory contract: docs, license, verifier, no dependencies without `cosmic`, the libcosmic pin, a pure library, the launcher and icon in the overlay, the build-cosmic.sh and post-build wiring. |
| `fixtures/win.replay` | A committed 7319-tick winning session, seed 7, 11-7. |

## In the image

Penguin Pong ships on the Desktop editions inside **one shared binary**,
[`pengy-games`](../pengy-games/), with the other seven games, so the image
carries one copy of libcosmic for all eight. `scripts/build-cosmic.sh` builds
`pengy-games` (which links this crate with `--features cosmic`), and
`board/desktop/post-build.sh` installs it with `/usr/bin/pengy-pong` as a symlink
to it: run under that name, it runs Penguin Pong, with every argument. The launcher
(`usr/share/applications/io.catbee.pengyos.PenguinPong.desktop`, `Exec=pengy-pong`) and
the icon (`usr/share/icons/hicolor/scalable/apps/io.catbee.pengyos.PenguinPong.svg`)
are in the Desktop overlay. Core and Server do not ship it. This crate's own
`pengy-pong` binary is the same program, for tests and development.

## Tests

```sh
cd tools/pengy-pong && cargo test
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
