# pengy-lander — Ice Lander

Land the probe on a pad. Not fast, not tilted, not out of fuel.

The 1979 Lunar Lander cart, rebuilt for the PengyOS desktop: a 60-metre ridge
of ice broken by two to four flat landing pads, a probe with 100 units of fuel,
moon gravity, and three numbers that all have to be small at the moment the
feet touch — sideways speed under 1.0 m/s, descent under 2.0 m/s, tilt under 5
degrees. Land on a 5x pad and the wave is worth five times what a 1x pad is.
Every wave is fresh terrain, and every wave pays a bonus for it. Crash, and the
run is over. It is one of the ten pack-in games PengyOS ships — Windows had
Solitaire and Minesweeper, the Atari 2600 had its cart, BSD had `greedy` and
`phantasia`; PengyOS ships ten little complete games, and this is one of them.

Rust 2021. The rules are **`std` only**; the window is libcosmic, like the
other PengyOS Desktop apps. No network, no root, no daemon.

## Play it

On a PengyOS Desktop it is in the App Library as **Ice Lander** (Games). From the
repo, on a build host with the COSMIC toolchain:

```sh
cd tools/pengy-lander
cargo run --release --features cosmic   # the window
cargo run -- --help                     # every flag and control
cargo run -- --headless --seed 20 --ticks 3900 --no-save   # prove it works with no display
```

| Control | What it does |
|---|---|
| space / up / W | Thrust, 4.0 m/s^2, for as long as it is held. Burns 1 unit of fuel per second. |
| left / right | Rotate, 90 deg/s, for as long as it is held. A and D too. |
| N / Enter | New terrain: the next wave. From a landed pad this banks the landing; from the air it abandons the wave; after a crash it starts a new run. |
| Ctrl+N / F2 | New game on the next seed. The high score is kept. |
| Ctrl+Q | Quit. The header picks the moon or the asteroid. |

## The rules, exactly

- A tick is 1/30 s. Physics runs in metres and m/s: **gravity 1.62 m/s^2**
  (the Moon, `--body moon`, the default) or **5.0 m/s^2** (`--body asteroid`),
  **thrust 4.0 m/s^2** while fuel remains, **rotation 90 deg/s**, **fuel 100
  units burning 1 unit per second of thrust**.
- The ground is a **60 x 24 scaled side view of a ridge** — one column per
  metre — broken by **2 to 4 flat pads 6 to 12 metres wide**, worth **1x, 2x
  and 5x**. The ridge never rises above 12 m, and the probe always spawns at
  22 m heading straight up, so the spawn is always clear of terrain and above
  the highest ridge.
- **Touchdown on a pad is a landing when |vx| < 1.0 m/s, |vy| < 2.0 m/s and
  |tilt| < 5 degrees.** Every comparison is strict, so arriving at exactly
  1.0 m/s is a crash. Anything else is a crash, and the screen says which:
  off pad, too fast sideways, too fast down, too tilted, out of fuel.
- **Score: 100 x pad multiplier, plus 10 per unit of fuel left, plus 50 per
  wave already banked** (the fresh-terrain wave bonus). The score of a run is
  the sum of its waves; a crash ends the run and banks what you have.
- The HUD shows altitude, vx, vy, fuel, tilt, the thrust flame, the wave and
  the score. The pad you are aimed at is named on the score strip.
- `--body asteroid` is a **suicide run, by construction**: 5.0 m/s^2 against a
  4.0 m/s^2 engine means the nose can never hold the probe up, so the descent
  always accelerates and no pad can be landed. It is there to be flown, not
  won; `tests/rules.rs` asserts that property rather than pretending.

All arithmetic is **integer fixed point** (positions x900 000 per metre,
velocities x30 000 per m/s) and all trigonometry is a 120-entry table, so a run
is bit-for-bit reproducible on any machine — no floating point, no `libm`, no
platform drift.

## Headless: how the game is proven to work

The repo gate and `pengyos-qemu-verify` have no display, so the game must
work without one. It does: with `--headless` or `--replay` it never opens a
window, never reads keys, prints plain text and exits 0.

```sh
pengy-lander --headless --seed 20 --ticks 3900 --no-save    # the built-in autopilot flies
pengy-lander --replay fixtures/win.replay --seed 20
pengy-lander --headless --seed 12 --ticks 900 --record /tmp/you.replay --no-save   # record a session
```

A replay file is **one line of input per tick**: a line's characters are those
keypresses, **a line containing a space is one tick of thrust**, a blank line
is no input, a `#` line is a comment and is not a tick. The harness stops at
the crash tick (a wrecked probe is frozen) or at EOF, prints the final state as
`key=value` lines, and exits 0. `hash=` is FNV-1a over the whole state: same
seed, same script, same hash.

`fixtures/win.replay` is a committed 3900-tick session — seed 20, six waves
landed, **8390 points**, 91 units of fuel left, hash `22392a0ed4250697`.

The autopilot is a real controller, not a cheat: it aims at the richest pad it
can reach, flies a proportional vertical loop as a 10-tick **duty cycle**
(the engine is binary, so the wanted thrust is flown pulse-density modulated),
kills its sideways speed high up where tilt is cheap, and goes nose-up for the
last 1.5 metres where tilt is what crashes you.

## Flags

`--seed N` (default 1), `--body moon|asteroid`, `--headless`, `--replay FILE`,
`--record FILE`, `--ticks N`, `--autopilot`, `--mono`, `--no-color`,
`--print-frame`, `--width N`, `--height N`, `--data-dir DIR`, `--no-save`,
`--help`, `--version`. `NO_COLOR` is honoured. Exit codes: **0** success,
**2** usage error, **66** unreadable replay file.

Designed for **80x24**. In a smaller window it degrades in order: the panel
moves above the field, then the title goes, then the border, then the key
legend, and the field is clipped from the top — the ground is never clipped.
`--mono` draws in plain ASCII when the locale cannot render the box drawing.

## Where the high score lives

`$XDG_DATA_HOME/pengyos/games/pengy-lander/best.txt` (default
`~/.local/share/pengyos/games/pengy-lander/best.txt`): best score, the waves it
took, the seed it happened on, and how many runs have been flown. Tier-1
mutable, under `$HOME`, never `/usr`, never the repo. A failure to write is
ignored — a read-only `$HOME` must not cost you a game.

## Layout

| Path | What is in it |
|---|---|
| `src/lib.rs` | **Every rule and the renderer, pure**: no I/O, no clock, no threads, no files. The seeded xorshift64* is the only randomness, so a run is a function of `(seed, script)`. |
| `src/main.rs` | Hands the command line to `cli::main`: the whole program is in the library, so `pengy-games` can carry it too. |
| `src/cli.rs` | The command line, the record file, and the headless harness (`--headless`, `--replay`, `--record`); with no harness flag it opens the window. |
| `src/app.rs` | The window (libcosmic, behind the `cosmic` feature; a child module of `cli`): the game drawn on a canvas, the keyboard (and mouse) turned into the game's keys, the game's own fixed tick. |
| `tests/rules.rs` | The rules: exact integration of gravity and thrust over N steps, the fuel floor and the engine stopping, the landing limits from just inside to just past each one, the order of blame, terrain generation over 200 seeds, score monotonicity, keys, determinism, the autopilot, rendering. |
| `tests/cli.rs` | The real binary with no display: `--help`, `--headless`, `--replay`, `--record`, exit codes 0/2/66, `NO_COLOR`, `--mono`, frame geometry, persistence in the data dir and nowhere else. |
| `tests/github_ready.rs` | The tool-directory contract: docs, license, verifier, no dependencies without `cosmic`, the libcosmic pin, a pure library, the launcher and icon in the overlay, the build-cosmic.sh and post-build wiring. |
| `fixtures/win.replay` | A committed 3900-tick winning session, seed 20, 8390 points. |

## In the image

Ice Lander ships on the Desktop editions inside **one shared binary**,
[`pengy-games`](../pengy-games/), with the other seven games, so the image
carries one copy of libcosmic for all eight. `scripts/build-cosmic.sh` builds
`pengy-games` (which links this crate with `--features cosmic`), and
`board/desktop/post-build.sh` installs it with `/usr/bin/pengy-lander` as a symlink
to it: run under that name, it runs Ice Lander, with every argument. The launcher
(`usr/share/applications/io.catbee.pengyos.IceLander.desktop`, `Exec=pengy-lander`) and
the icon (`usr/share/icons/hicolor/scalable/apps/io.catbee.pengyos.IceLander.svg`)
are in the Desktop overlay. Core and Server do not ship it. This crate's own
`pengy-lander` binary is the same program, for tests and development.

## Tests

```sh
cd tools/pengy-lander && cargo test          # ~1 s
cargo test --quiet --test cli               # the binary, with no display
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
