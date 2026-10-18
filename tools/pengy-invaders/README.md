# Thaw Invaders

**Something is melting the ice from above. You have one laser.**

Thaw Invaders is a PengyOS pack-in game: the 1978 arcade original
(Space Invaders) on a 60x24 terminal field, written in Rust with **zero
dependencies** — no crates, no libc, no ncurses. It is one of ten small, complete
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

```sh
cd tools/pengy-invaders
cargo run --release          # play it (needs a terminal)
cargo test                   # the whole suite, no terminal needed
```

Installed on a PengyOS image it is simply `pengy-invaders`, and it is in the
Games menu (the launcher entry runs it in a terminal).

```sh
pengy-invaders                       # play
pengy-invaders --seed 7              # a reproducible run
pengy-invaders --headless            # prove it works with no terminal
pengy-invaders --replay fixtures/win.replay --seed 7   # replay a session
pengy-invaders --help                # every flag and every key
```

## Keys

| Key | What it does |
|-----|--------------|
| `a` / `d` | Move the ship two columns left / right. The arrow keys work too. |
| `space` | Fire the laser. One laser in flight, 2 ticks of cooldown. |
| `p` | Pause / resume. |
| `r` | Restart (next seed in the seed family, best score kept). |
| `q` | Quit, cleanly, restoring the terminal. Ctrl-C quits too. |
| anything else | Ignored. A bare Escape is deliberately not a quit key. |

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

* **No terminal needed.** With no TTY, with `--headless`, or with `--replay`,
  the game never touches the terminal and never reads keys: it runs the scripted
  path, prints plain text, and exits 0. That is how the repo gate and
  `pengyos-qemu-verify` prove the game works, because neither has a pty.
* **Reproducible.** Every run takes `--seed N`; the generator is a hand-written
  seeded xorshift64*. Same seed + same inputs = the same run, bit for bit, and
  the end state prints as a 16-digit hash.
* **`NO_COLOR`** is honoured automatically; `--mono` draws with pure ASCII.
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
* `tests/cli.rs` — the built binary, with a pipe for stdin: `--help`,
  `--version`, the headless path, `NO_COLOR`, `--mono`, the window sizes, the
  committed replay fixture, the high-score file, and a scripted session that
  must end in the same state every time.
* `tests/github_ready.rs` — the tool-directory contract: the docs, the
  verifier, the empty `[dependencies]`, the launcher, the Buildroot recipe.

The suite runs in well under a second and needs no terminal.

`fixtures/win.replay` is a committed session: 900 lines, one line of input per
tick (`a`/`d` move, `f` fire, blank = no input, `#` = comment). Play it back with

```sh
cargo run --release -- --replay fixtures/win.replay --seed 7 --no-save
```

and it clears three waves for 6040 points, deterministically.

## Layout

| Path | What it is |
|------|------------|
| `src/lib.rs` | Every rule and the renderer. Pure: no I/O, no clock, no threads, no files. |
| `src/main.rs` | The terminal front-end: `stty raw`, the key reader thread, the tick loop, ANSI rendering, the CLI, the record. |
| `tests/` | The three suites above. |
| `fixtures/win.replay` | The committed winning session. |
| `launcher/pengy-invaders.desktop` | The menu entry (`Terminal=true`, `Categories=Game;`). |
| `verify.toml` | How the repo gate tests this tool. |
| `../../buildroot-external/package/pengy-invaders/` | The Buildroot recipe skeleton. |

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
