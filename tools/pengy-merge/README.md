# pengy-merge — Icefloe 2048

Slide the ice floes. Equal floes fuse into one bigger floe. Reach 2048.

A 4x4 field (5x5 with `--size 5`), a score, a persisted best, single-step undo,
and a clean quit. It is the PengyOS pack-in game in the 2048 slot — Windows had
Solitaire and Minesweeper, the Atari 2600 had its cart, BSD had `greedy` and
`phantasia`; PengyOS ships ten little complete games, and this is one of them.

Rust 2021, **`std` only, zero external crates**, no network, no root, no daemon.
It runs in any terminal, and it runs with no terminal at all.

## Play it

```sh
cd tools/pengy-merge
cargo run --release            # play (needs a terminal)
cargo run -- --help            # every flag and key
```

| Key | What it does |
|---|---|
| `h` `j` `k` `l` | Slide left, down, up, right. The arrow keys and WASD work too. |
| `u` | Undo the last slide — one step, and the floe that slide dealt goes back with it. |
| `r` | Restart (next seed in the seed family, best score kept). |
| `c` | Keep playing after a win. |
| `q` | Quit, restoring the terminal. `Ctrl-C` quits too. |
| anything else | Ignored. `Escape` is deliberately not a quit key: terminals send the arrow keys as `ESC [ A`, so a bare `ESC` has to stay harmless. |

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

## Proving it works with no terminal

The repo gate and `pengyos-qemu-verify` have no pty, so the game is built to be
proven headlessly. `--headless` (and any run whose stdin is not a tty) never
touches the terminal, never reads keys, prints plain text, and exits 0.

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
| `src/main.rs` | The terminal front-end: `stty raw`, the stdin reader thread, the fixed-timestep loop, ANSI painting, the CLI, the best-score file. |
| `tests/rules.rs` | The rules contract: the slide edge cases, scoring, spawning, game over, winning, undo/restart/quit keys, determinism, the golden scripted end state, rendering. |
| `tests/cli.rs` | The real binary with no pty: `--help`, `--headless`, `--replay`, `--record`, exit codes, `NO_COLOR`, `--mono`, persistence. |
| `tests/github_ready.rs` | The tool-directory contract (docs, license, verifier, empty dependencies, pure library, launcher, recipe). |
| `fixtures/win.replay` | A committed 549-tick session, seed 7, score 7976, a 512 floe. |
| `launcher/pengy-merge.desktop` | The Desktop menu entry (`Terminal=true`, `Categories=Game;`). |
| `../../buildroot-external/package/pengy-merge/` | The Buildroot recipe skeleton that builds this crate from the repo into `/usr/bin`. |

## Buildroot

`buildroot-external/package/pengy-merge/` carries `Config.in` +
`pengy-merge.mk`: a `local`-source recipe that runs `cargo build --offline
--release` on this directory and installs the binary to
`/usr/bin/pengy-merge` plus the `.desktop` file. It needs no vendored crates
and no download digest, because the crate has **no dependencies** — that is the
point of the zero-dep rule.

Image wiring (the `source` line in `buildroot-external/Config.in`, the layer
`.config`, the menu) is deliberately **not** in this change: it is one shared
file touched by ten games, so it is a single integration pass, not ten.

## Tests

```sh
cd tools/pengy-merge && cargo test
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
