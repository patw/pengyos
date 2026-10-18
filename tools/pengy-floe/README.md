# pengy-floe — Flappy Pengy

A one-button penguin flapper for the PengyOS terminal: 40x20 playfield, drifting
ice pillars with gaps, cruel gravity, a score, a persisted best, and a medal when
you are doing well. It is the PengyOS pack-in game in the flappy-bird slot
(Windows had Solitaire, the Atari 2600 had its cart, BSD had `greedy`; PengyOS
ships ten little games, and this is one of them).

Rust 2021, **`std` only, zero external crates**, no network, no root, no daemon.
It runs in any terminal, and it runs with no terminal at all.

## Play it

```sh
cd tools/pengy-floe
cargo run --release            # play (needs a terminal)
cargo run -- --help            # every flag and key
```

| Key | What it does |
|---|---|
| `space` / `up` / `w` | Flap. On the death screen it restarts instantly. |
| `p` | Pause / resume. |
| `r` | Restart (next seed in the seed family). |
| `q` | Quit, restoring the terminal. `Ctrl-C` and `Escape` quit too. |

Gravity adds 1 to `vy` every 80 ms tick (capped at 3); a flap sets `vy` to −2 and
never stacks. Ice pillars are 2 columns wide, one every 12 columns, drifting at 1
column per 2 ticks and ramping to 1 column per tick by score 30. The gap is 8 rows
and narrows to 5 by score 25, always leaving at least 2 rows of clearance. Score
+1 per pillar passed; touching the ceiling, the floor, or a pillar kills you.
Medals: **10 ice, 25 bronze, 50 silver, 100 gold**.

The hitbox is deliberately forgiving: the sprite is 4 columns wide, the collision
box is the 2 columns in the middle, so a near miss looks like a hit and is not.

## Proving it works with no terminal

The repo gate and `pengyos-qemu-verify` have no pty, so the game is built to be
proven headlessly. `--headless` (and any run whose stdin is not a tty) never
touches the terminal, never reads keys, prints plain text, and exits 0.

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
| `src/main.rs` | The terminal front-end: `stty raw`, the stdin reader thread, the fixed-timestep loop, ANSI painting, the CLI, the high-score file. |
| `tests/rules.rs` | The rules contract: gravity, flap clamps, gap legality, scoring, collisions, states, medals, determinism, rendering. |
| `tests/cli.rs` | The real binary with no pty: `--help`, `--headless`, `--replay`, exit codes, `NO_COLOR`, persistence. |
| `tests/github_ready.rs` | The tool-directory contract (docs, license, verifier, empty dependencies, launcher, recipe). |
| `fixtures/win.replay` | A committed 1039-tick session that scores 70 and wins the silver medal. |
| `launcher/pengy-floe.desktop` | The Desktop menu entry (`Terminal=true`, `Categories=Game;`). |
| `../../buildroot-external/package/pengy-floe/` | The Buildroot recipe skeleton that builds this crate from the repo into `/usr/bin`. |

## Buildroot

`buildroot-external/package/pengy-floe/` carries `Config.in` + `pengy-floe.mk`:
a `local`-source recipe that runs `cargo build --offline --release` on this
directory and installs the binary to `/usr/bin/pengy-floe` plus the `.desktop`
file. It needs no vendored crates and no download digest, because the crate has
**no dependencies** — that is the point of the zero-dep rule.

Image wiring (the `source` line in `buildroot-external/Config.in`, the layer
`.config`, the menu) is deliberately **not** in this change: it is one shared
file touched by ten games, so it is a single integration pass, not ten.

## Tests

```sh
cd tools/pengy-floe && cargo test
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
