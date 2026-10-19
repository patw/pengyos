# Thaw Invaders

**Something is melting the ice from above. You have one laser.**

Thaw Invaders is a PengyOS pack-in game: the 1978 arcade original
(Space Invaders) on the COSMIC desktop. The rules are Rust 2021 and **`std`
only**; the window is libcosmic, like the other PengyOS Desktop apps. It is one
of ten small, complete
games PengyOS ships in the image, in the tradition of Windows Solitaire, the Atari
2600 pack-in cart and the BSD games: they just work, they have a score, and they
are good little games rather than demos.

An 11x5 formation of aliens marches across the field, stepping one column every
N ticks. Touching a wall reverses it and drops it one row. N shrinks as the
formation thins, so the last few aliens are the fast ones. Aliens drop bombs; a
bomb that reaches you takes a life. Three destructible shields stand between you
and the formation. Clear the wave and the next one starts one row lower and
faster. If the formation reaches your row, the ice is gone.

## Run it

On a PengyOS Desktop it is in the App Library as **Thaw Invaders** (Games).
From the repo, on a build host with the COSMIC toolchain:

```sh
cd tools/pengy-invaders
cargo run --release --features cosmic   # the window
cargo test                              # the whole suite, no display needed
```

```sh
pengy-invaders                       # play
pengy-invaders --seed 7              # a reproducible run
pengy-invaders --headless            # prove it works with no display
pengy-invaders --replay fixtures/win.replay --seed 7   # replay a session
pengy-invaders --help                # every flag and every control
```

## Controls

| Control | What it does |
|---|---|
| left / right | Move the ship. Held, it keeps moving. A and D work too. |
| space / up | Fire. One laser in flight, 2 ticks of cooldown; held, it fires again as soon as it may. Space also starts the run. |
| Esc / P | Pause / resume. |
| Ctrl+N / F2 | New game (next seed in the seed family, best kept). |
| Ctrl+Q | Quit. |

## How it plays

* **Field** 60x24. **Formation** 11 columns x 5 rows, aliens 2 cells wide, spaced
  4 apart, starting centred on row 2.
* **Tempo** 40 ms per tick. The formation steps one column every
  `8 * alive / 55 / wave` ticks, floor 1.
* **Ship** 3 columns wide, 2 columns per keypress, on row 23.
* **Laser** one in flight, 2-tick cooldown, 2 rows per tick upward. A hit kills
  the *lowest* alien in that column.
* **Bombs** each living alien rolls every tick at 0.002 (+0.0005 per wave);
  bombs fall 1 row per tick, 3 on screen at most.
* **Shields** three 8x4 blocks; any hit erodes the cell it lands on, and an
  alien that touches a shield grinds it away.
* **Lives** 3. A hit clears the bombs and recentres the ship.
* **Score** by row: bottom 10, then 20, 30, 40, top 50; a cleared wave scores
  100 x the wave number. The best score is kept.

## Flags

`--seed N`, `--headless`, `--replay FILE`, `--record FILE`, `--ticks N`,
`--wave N`, `--autopilot`, `--mono`, `--no-color`, `--print-frame`,
`--width N`, `--height N`, `--data-dir DIR`, `--no-save`, `--help`,
`--version`. `--help` lists them all with their meaning.

* **No display needed.** With `--headless` or `--replay` the game never opens
  a window and never reads keys: it runs the scripted path, prints plain text,
  and exits 0. That is how the repo gate and `pengyos-qemu-verify` prove the
  game works, because neither has a display.
* **Reproducible.** Every run takes `--seed N`; the generator is a hand-written
  seeded xorshift64*. Same seed + same inputs = the same run, bit for bit, and
  the end state prints as a 16-digit hash.
* **`--print-frame`** prints the final position as a text frame (a debugging
  aid); `NO_COLOR` is honoured there and `--mono` makes it pure ASCII.
* **80x24** is the reference window. Smaller windows drop the border, then the
  title, then the legend — never the field, and never the score.

## The high score

The best score is kept in

```
$XDG_DATA_HOME/pengyos/games/pengy-invaders/best.txt
```

which defaults to `~/.local/share/pengyos/games/pengy-invaders/best.txt`. That is
the only place the game ever writes: under the player's home, never `/usr`, never
the repo. `--no-save` skips it entirely (the tests use it).

## Tests

```sh
cd tools/pengy-invaders
cargo test              # rules + CLI + the tool-directory contract
cargo test --quiet      # the same, quietly
```

* `tests/rules.rs` — the rules, driven through the library: reversal and
  descent at both edges, the step period and its floor, the lowest alien in a
  column, laser/shield and bomb/shield erosion, three lives, wave advance, the
  breach, the bomb cap, determinism, and the renderer's window contract.
* `tests/cli.rs` — the built binary, with no display: `--help`,
  `--version`, the headless path, `NO_COLOR`, `--mono`, the window sizes, the
  committed replay fixture, the high-score file, and a scripted session that
  must end in the same state every time.
* `tests/github_ready.rs` — the tool-directory contract: the docs, the
  verifier, no dependencies without `cosmic`, the libcosmic pin, the launcher
  and icon in the Desktop overlay, the build-cosmic.sh and post-build wiring.

The suite runs in well under a second and needs no display.

`fixtures/win.replay` is a committed session: 1034 lines, one line of input per
tick (`a`/`d` move, `f` fire, blank = no input, `#` = comment). Play it back with

```sh
cargo run --release -- --replay fixtures/win.replay --seed 7 --no-save
```

and it clears the first wave for 3350 points, deterministically.

## Layout

| Path | What it is |
|------|------------|
| `src/lib.rs` | Every rule, and the text frame `--print-frame` prints. Pure: no I/O, no clock, no threads, no files. |
| `src/main.rs` | Hands the command line to `cli::main`: the whole program is in the library, so `pengy-games` can carry it too. |
| `src/cli.rs` | The command line, the record file, and the headless harness (`--headless`, `--replay`, `--record`); with no harness flag it opens the window. |
| `src/app.rs` | The window (libcosmic, behind the `cosmic` feature; a child module of `cli`): the game drawn on a canvas, the keyboard (and mouse) turned into the game's keys, the game's own fixed tick. |
| `tests/` | The three suites above. |
| `fixtures/win.replay` | The committed winning session. |
| `verify.toml` | How the repo gate tests this tool. |

## In the image

Thaw Invaders ships on the Desktop editions inside **one shared binary**,
[`pengy-games`](../pengy-games/), with the other seven games, so the image
carries one copy of libcosmic for all eight. `scripts/build-cosmic.sh` builds
`pengy-games` (which links this crate with `--features cosmic`), and
`board/desktop/post-build.sh` installs it with `/usr/bin/pengy-invaders` as a symlink
to it: run under that name, it runs Thaw Invaders, with every argument. The launcher
(`usr/share/applications/io.catbee.pengyos.ThawInvaders.desktop`, `Exec=pengy-invaders`) and
the icon (`usr/share/icons/hicolor/scalable/apps/io.catbee.pengyos.ThawInvaders.svg`)
are in the Desktop overlay. Core and Server do not ship it. This crate's own
`pengy-invaders` binary is the same program, for tests and development.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
