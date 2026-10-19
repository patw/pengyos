# pengy-blocks — Glacier Blocks

Ten columns, twenty rows, seven pieces, no mercy. The real Tetris, for the PengyOS desktop: a 7-bag randomiser, gravity that accelerates by level
(800 ms down to 80 ms), wall kicks, a 300 ms lock delay that resets when you
move, soft drop at 1 point a row, hard drop at 2, 100/300/500/800 × level for
1/2/3/4 lines, a next-3 preview, a hold slot, and a score that persists.

Every OS used to ship a couple of small complete games that just worked —
Windows had Solitaire and Minesweeper, the Atari 2600 had its pack-in cart, BSD
had `greedy` and `phantasia`. PengyOS ships ten: floe, merge, icefield,
breakout, invaders, lander, **blocks**, pong, solitaire, rogue. This is blocks.

Rust 2021. The rules are **`std` only**; the window is libcosmic, like the
other PengyOS Desktop apps. No network, no root, no daemon.

## Play it

On a PengyOS Desktop it is in the App Library as **Glacier Blocks** (Games). From the
repo, on a build host with the COSMIC toolchain:

```sh
cd tools/pengy-blocks
cargo run --release --features cosmic   # the window
cargo run -- --help                     # every flag and control
cargo run -- --headless --seed 42 --no-save   # prove it works with no display
```

| Control | What it does |
|---|---|
| left / right | Move. Held, the piece keeps sliding. A and D work too. |
| down | Soft drop, 1 point per row. S works too. |
| up / X | Rotate clockwise. W works too. |
| Z | Rotate counter-clockwise. |
| space | Hard drop — straight to the floor, 2 points per row. |
| shift / C | Hold (swap the piece with the hold slot; once per piece). |
| Esc / P | Pause / resume. |
| Ctrl+N / F2 | New game (next seed in the seed family, best kept). |
| Ctrl+Q | Quit. |

## The rules, exactly

- The well is **10x20** with **two hidden spawn rows** above it. A piece spawns
  at the top of the hidden area, centred on columns 3-5.
- All **seven tetrominoes**, dealt from a **7-bag**: every piece appears exactly
  once per bag, so you never wait for an I for ever and never get three Ss in a
  row.
- **Gravity by level**: 800, 720, 630, 550, 470, 380, 300, 220, 130, 100, 80 ms
  per row, and 80 ms from level 11 up. **Level = 1 + lines/10.**
- **Rotation with wall kicks**: in place, then +1 column, then −1, then +2, then
  −2, then up one row. The shapes are the standard SRS orientations, so a flat
  I stands up against a wall and a T reaches a column it could not reach flat.
- **Lock delay**: a piece that cannot move down locks after **300 ms**. A
  successful move or rotation resets the delay — up to **15** times, so a piece
  can be slid along the floor but not held down for ever.
- **Line clears**: 1, 2, 3, 4 lines score **100 / 300 / 500 / 800 × level**, and
  the well collapses: every row above a cleared row falls by exactly the number
  of rows cleared below it.
- **Soft drop** is 1 point per row moved by a keypress; **hard drop** is 2
  points per row and locks immediately.
- **Next-3 preview**, and a **hold** slot that swaps once per piece.
- **Game over** when a spawn overlaps the well — the stack has reached the
  spawn area.

## Headless: how the game is proven to work

The repo gate and `pengyos-qemu-verify` have no display, so the game must
work without one. It does: with `--headless` or `--replay` it never opens a
window, never reads keys, prints plain text and exits 0.

```sh
pengy-blocks --headless --seed 42 --no-save                 # the built-in player plays
pengy-blocks --replay fixtures/win.replay --seed 42         # the acceptance harness
pengy-blocks --headless --seed 21 --record /tmp/you.replay --no-save   # record a session
```

A replay file is **one line of input per tick**: a line with characters is those
keypresses, a blank line is no input, a `#` line is a comment and is not a tick.
`_` is the hard drop (a trailing space is too easy for an editor to eat). The
harness stops at the game-over tick (a finished game is frozen) or at EOF,
prints the final state as `key=value` lines, and exits 0. `hash=` is FNV-1a over
the whole state: same seed, same script, same hash.

`fixtures/win.replay` is a committed 1940-tick session — seed 42, **168 lines,
level 17, score 174528**, hash `53cbffd1a21e3566`.

## Flags

`--seed N` (default 1), `--headless`, `--replay FILE`, `--record FILE`,
`--ticks N`, `--autopilot`, `--mono`, `--no-color`, `--print-frame`,
`--width N`, `--height N`, `--data-dir DIR`, `--no-save`, `--help`,
`--version`. `NO_COLOR` is honoured. Exit codes: **0** success, **2** usage
error, **66** unreadable replay file.

Designed for **80x24**. In a smaller window it degrades in a fixed order: the
side panels (hold, next-3, score) go first, then the title, then the key
legend, then the border; a short window clips the well from the bottom. The
10-column well is never dropped. `--mono` draws in plain ASCII when the locale
cannot render the box drawing.

## Where the best score lives

`$XDG_DATA_HOME/pengyos/games/pengy-blocks/best.txt` (default
`~/.local/share/pengyos/games/pengy-blocks/best.txt`): best score, most lines,
highest level, games played, the seed the best score happened on. Tier-1
mutable, under `$HOME`, never `/usr`, never the repo. A failure to write is
ignored — a read-only `$HOME` must not cost you a game.

## Layout

| Path | What is in it |
|---|---|
| `src/lib.rs` | **Every rule and the renderer, pure**: no I/O, no clock, no threads, no files. The seeded xorshift64* is the only randomness, so a run is a function of `(seed, script)`. |
| `src/main.rs` | Hands the command line to `cli::main`: the whole program is in the library, so `pengy-games` can carry it too. |
| `src/cli.rs` | The command line, the record file, and the headless harness (`--headless`, `--replay`, `--record`); with no harness flag it opens the window. |
| `src/app.rs` | The window (libcosmic, behind the `cosmic` feature; a child module of `cli`): the game drawn on a canvas, the keyboard (and mouse) turned into the game's keys, the game's own fixed tick. |
| `tests/rules.rs` | The rules: the 7-bag invariant over 70 draws, every wall-kick case, line-clear collapse (a single, a tetris, a non-adjacent pair), the scoring table, the level thresholds, the lock delay and its reset and its cap, the spawn-overlap game over, the key table, determinism, the pinned golden end state, rendering and the degrade rules. |
| `tests/cli.rs` | The real binary with no display: `--help`, `--headless`, `--replay`, `--record`, exit codes 0/2/66, `NO_COLOR`, `--mono`, frame geometry, persistence in the data dir and nowhere else. |
| `tests/github_ready.rs` | The tool-directory contract: docs, license, verifier, no dependencies without `cosmic`, the libcosmic pin, a pure library, the launcher and icon in the overlay, the build-cosmic.sh and post-build wiring. |
| `fixtures/win.replay` | A committed 1940-tick winning session, seed 42, 168 lines, level 17. |

## In the image

Glacier Blocks ships on the Desktop editions inside **one shared binary**,
[`pengy-games`](../pengy-games/), with the other seven games, so the image
carries one copy of libcosmic for all eight. `scripts/build-cosmic.sh` builds
`pengy-games` (which links this crate with `--features cosmic`), and
`board/desktop/post-build.sh` installs it with `/usr/bin/pengy-blocks` as a symlink
to it: run under that name, it runs Glacier Blocks, with every argument. The launcher
(`usr/share/applications/io.catbee.pengyos.GlacierBlocks.desktop`, `Exec=pengy-blocks`) and
the icon (`usr/share/icons/hicolor/scalable/apps/io.catbee.pengyos.GlacierBlocks.svg`)
are in the Desktop overlay. Core and Server do not ship it. This crate's own
`pengy-blocks` binary is the same program, for tests and development.

## Tests

```sh
cd tools/pengy-blocks && cargo test     # 70 rule tests + 21 CLI tests + 10 contract tests, <1s
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
