# pengy-merge — Icefloe 2048

Slide the ice floes. Equal floes fuse into one bigger floe. Reach 2048.

A 4x4 field (5x5 with `--size 5`), a score, a persisted best, single-step undo,
and a clean quit. It is the PengyOS pack-in game in the 2048 slot — Windows had
Solitaire and Minesweeper, the Atari 2600 had its cart, BSD had `greedy` and
`phantasia`; PengyOS ships ten little complete games, and this is one of them.

Rust 2021. The rules are **`std` only**; the window is libcosmic, like the
other PengyOS Desktop apps. No network, no root, no daemon.

## Play it

On a PengyOS Desktop it is in the App Library as **Icefloe 2048** (Games). From the
repo, on a build host with the COSMIC toolchain:

```sh
cd tools/pengy-merge
cargo run --release --features cosmic   # the window
cargo run -- --help                     # every flag and control
cargo run -- --headless --no-save
```

| Control | What it does |
|---|---|
| arrows | Slide left, down, up, right. WASD and hjkl work too. |
| Ctrl+Z / U | Undo the last slide (one step; the spawned floe goes back). |
| C | Keep playing after reaching the goal. |
| Ctrl+N / F2 | New game (same seed family: seed + 1, best score kept). |
| Ctrl+Q | Quit. |

## The rules, exactly

- A slide moves **every** floe in the field as far as it goes.
- Two equal floes that meet fuse into one of double value. Fusing happens
  **from the direction of travel**, and a floe fuses **at most once per slide**:
  `4 4 8 8` sliding left is `8 16`, not `16 8`; `2 4 2` does not chain.
- The score gains exactly the sum of the fused values.
- After any slide that moved something, one floe appears in a random empty
  cell: a 2 nine times out of ten, a 4 once.
- Reach 2048 (`--goal N` changes it) and the run is won; `c` keeps playing.
- The run is over when a slide changes nothing and nothing can fuse.

## Proving it works with no display

The repo gate and `pengyos-qemu-verify` have no display, so the game is built
to be proven headlessly. `--headless` never opens a window, never reads keys,
prints plain text, and exits 0.

```sh
# 1. The unit + end-to-end suite (73 tests, ~0.1 s).
cargo test

# 2. The acceptance harness: replay a recorded session and read the result.
cargo run --release -- --replay fixtures/win.replay --seed 7
#   state=over seed=7 moves=549 score=7976 max=512 hash=9e91c46c6aafb114

# 3. A scripted demo run.
cargo run --release -- --headless --seed 7 --ticks 4000 --no-save

# 4. Record your own fixture (the bot slides, the script is written out).
cargo run --release -- --headless --seed 21 --ticks 4000 --no-save --record my.replay
cargo run --release -- --replay my.replay --seed 21
```

`--replay FILE` takes one line of input per tick: a line with a character is
that key, a blank line is no input, a `#` line is a comment and is not a tick.
It stops at the end tick (a finished run is frozen) and prints the final state
as `key=value` lines on exit 0. Same seed + same script ⇒ the same `hash=`.

## Flags

`--seed N`, `--size N` (3..=8, default 4), `--goal N` (default 2048),
`--headless`, `--replay FILE`, `--record FILE`, `--ticks N`, `--autopilot`,
`--mono`, `--no-color`, `--print-frame`, `--width N`, `--height N`,
`--data-dir DIR`, `--no-save`, `--help`, `--version`.
`NO_COLOR` in the environment is honoured automatically. `--help` documents all
of them. Exit codes: **0** success, **2** usage error, **66** unreadable replay
file.

## Where the best score lives

`$XDG_DATA_HOME/pengyos/games/pengy-merge/best.txt`, defaulting to
`~/.local/share/pengyos/games/pengy-merge/best.txt` — Tier-1 mutable state
under `$HOME`. The game never writes anywhere else, and never writes into
`/usr`. `--no-save` reads and writes nothing.

## Layout

| Path | What it is |
|---|---|
| `src/lib.rs` | **Every rule and the renderer.** Pure: no I/O, no clock, no threads, no files, seeded RNG only. |
| `src/main.rs` | Hands the command line to `cli::main`: the whole program is in the library, so `pengy-games` can carry it too. |
| `src/cli.rs` | The command line, the record file, and the headless harness (`--headless`, `--replay`, `--record`); with no harness flag it opens the window. |
| `src/app.rs` | The window (libcosmic, behind the `cosmic` feature; a child module of `cli`): the game drawn on a canvas, the keyboard (and mouse) turned into the game's keys, the game's own fixed tick. |
| `tests/rules.rs` | The rules contract: the slide edge cases, scoring, spawning, game over, winning, undo/restart/quit keys, determinism, the golden scripted end state, rendering. |
| `tests/cli.rs` | The real binary with no display: `--help`, `--headless`, `--replay`, `--record`, exit codes, `NO_COLOR`, `--mono`, persistence. |
| `tests/github_ready.rs` | The tool-directory contract: docs, license, verifier, no dependencies without `cosmic`, the libcosmic pin, a pure library, the launcher and icon in the overlay, the build-cosmic.sh and post-build wiring. |
| `fixtures/win.replay` | A committed 549-tick session, seed 7, score 7976, a 512 floe. |

## In the image

Icefloe 2048 ships on the Desktop editions inside **one shared binary**,
[`pengy-games`](../pengy-games/), with the other seven games, so the image
carries one copy of libcosmic for all eight. `scripts/build-cosmic.sh` builds
`pengy-games` (which links this crate with `--features cosmic`), and
`board/desktop/post-build.sh` installs it with `/usr/bin/pengy-merge` as a symlink
to it: run under that name, it runs Icefloe 2048, with every argument. The launcher
(`usr/share/applications/io.catbee.pengyos.Icefloe.desktop`, `Exec=pengy-merge`) and
the icon (`usr/share/icons/hicolor/scalable/apps/io.catbee.pengyos.Icefloe.svg`)
are in the Desktop overlay. Core and Server do not ship it. This crate's own
`pengy-merge` binary is the same program, for tests and development.

## Tests

```sh
cd tools/pengy-merge && cargo test
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
