# pengy-floe — Flappy Pengy

A one-button penguin flapper for the PengyOS desktop: 40x20 playfield, drifting
ice pillars with gaps, cruel gravity, a score, a persisted best, and a medal when
you are doing well. It is the PengyOS pack-in game in the flappy-bird slot
(Windows had Solitaire, the Atari 2600 had its cart, BSD had `greedy`; PengyOS
ships ten little games, and this is one of them).

Rust 2021. The rules are **`std` only**; the window is libcosmic, like the
other PengyOS Desktop apps. No network, no root, no daemon.

## Play it

On a PengyOS Desktop it is in the App Library as **Flappy Pengy** (Games). From the
repo, on a build host with the COSMIC toolchain:

```sh
cd tools/pengy-floe
cargo run --release --features cosmic   # the window
cargo run -- --help                     # every flag and control
cargo run -- --headless --no-save
```

| Control | What it does |
|---|---|
| space / up / W | Flap. A click on the sky flaps too. On the death screen a flap starts the next run at once. |
| Esc / P | Pause / resume. |
| Ctrl+N / F2 | New run (same seed family: seed + 1). |
| Ctrl+Q | Quit. |

## Proving it works with no display

The repo gate and `pengyos-qemu-verify` have no display, so the game is built
to be proven headlessly. `--headless` never opens a window, never reads keys,
prints plain text, and exits 0.

```sh
# 1. The unit + end-to-end suite (40 tests, ~0.1 s).
cargo test

# 2. The acceptance harness: replay a recorded session and read the result.
cargo run --release -- --replay fixtures/win.replay --seed 2
#   state=dead seed=2 ticks=1039 score=70 best=70 ... medal=silver

# 3. A scripted demo run.
cargo run --release -- --headless --seed 2 --ticks 400 --no-save

# 4. Record your own fixture (the autopilot flies, the script is written out).
cargo run --release -- --headless --seed 13 --ticks 4000 --no-save --record my.replay
cargo run --release -- --replay my.replay --seed 13
```

`--replay FILE` takes one line of input per tick: a line with a character is that
key, a blank line is no input, a `#` line is a comment and is not a tick. It stops
at the death tick (a dead run is frozen) and prints the final state as
`key=value` lines on exit 0. Same seed + same script ⇒ the same `hash=`.

## Flags

`--seed N`, `--headless`, `--replay FILE`, `--record FILE`, `--ticks N`,
`--autopilot`, `--mono`, `--no-color`, `--print-frame`, `--width N`,
`--height N`, `--data-dir DIR`, `--no-save`, `--help`, `--version`.
`NO_COLOR` in the environment is honoured automatically. `--help` documents all
of them. Exit codes: **0** success, **2** usage error, **66** unreadable replay
file.

## Where the high score lives

`$XDG_DATA_HOME/pengyos/games/pengy-floe/best.txt`, defaulting to
`~/.local/share/pengyos/games/pengy-floe/best.txt` — Tier-1 mutable state under
`$HOME`. The game never writes anywhere else, and never writes into `/usr`.
`--no-save` reads and writes nothing.

## Layout

| Path | What it is |
|---|---|
| `src/lib.rs` | **Every rule and the renderer.** Pure: no I/O, no clock, no threads, no files, seeded RNG only. |
| `src/main.rs` | Hands the command line to `cli::main`: the whole program is in the library, so `pengy-games` can carry it too. |
| `src/cli.rs` | The command line, the record file, and the headless harness (`--headless`, `--replay`, `--record`); with no harness flag it opens the window. |
| `src/app.rs` | The window (libcosmic, behind the `cosmic` feature; a child module of `cli`): the game drawn on a canvas, the keyboard (and mouse) turned into the game's keys, the game's own fixed tick. |
| `tests/rules.rs` | The rules contract: gravity, flap clamps, gap legality, scoring, collisions, states, medals, determinism, rendering. |
| `tests/cli.rs` | The real binary with no display: `--help`, `--headless`, `--replay`, exit codes, `NO_COLOR`, persistence. |
| `tests/github_ready.rs` | The tool-directory contract: docs, license, verifier, no dependencies without `cosmic`, the libcosmic pin, a pure library, the launcher and icon in the overlay, the build-cosmic.sh and post-build wiring. |
| `fixtures/win.replay` | A committed 1039-tick session that scores 70 and wins the silver medal. |

## In the image

Flappy Pengy ships on the Desktop editions inside **one shared binary**,
[`pengy-games`](../pengy-games/), with the other seven games, so the image
carries one copy of libcosmic for all eight. `scripts/build-cosmic.sh` builds
`pengy-games` (which links this crate with `--features cosmic`), and
`board/desktop/post-build.sh` installs it with `/usr/bin/pengy-floe` as a symlink
to it: run under that name, it runs Flappy Pengy, with every argument. The launcher
(`usr/share/applications/io.catbee.pengyos.FlappyPengy.desktop`, `Exec=pengy-floe`) and
the icon (`usr/share/icons/hicolor/scalable/apps/io.catbee.pengyos.FlappyPengy.svg`)
are in the Desktop overlay. Core and Server do not ship it. This crate's own
`pengy-floe` binary is the same program, for tests and development.

## Tests

```sh
cd tools/pengy-floe && cargo test
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
