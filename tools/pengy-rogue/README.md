# pengy-rogue — Ice Rogue

Rogue (1980) — the original dungeon crawl, the game that named the genre, in a
COSMIC window: ice walls, a lamp's worth of sight, and every creature drawn.
Five floors down, one penguin, permanent death, and at the bottom **The Thaw**. Every OS used to ship a couple
of small complete games that just worked: Windows had Solitaire and Minesweeper,
the Atari 2600 had its pack-in cart, BSD had `greedy` and `phantasia`. PengyOS
ships ten small complete games; this is rogue.

Rust 2021. The rules are **`std` only**; the window is libcosmic, like the
other PengyOS Desktop apps. No network, no root, no daemon.

## Play it

On a PengyOS Desktop it is in the App Library as **Ice Rogue** (Games). From the
repo, on a build host with the COSMIC toolchain:

```sh
cd tools/pengy-rogue
cargo run --release --features cosmic   # the window
cargo run -- --help                     # every flag and control
cargo run -- --headless --seed 7 --no-save    # prove it works with no display
```

| Control | What it does |
|---|---|
| arrows / h j k l | Move left, down, up, right. Walk into a monster to fight. |
| y u b n | The four diagonals; Home, PgUp, End and PgDn too. |
| . | Wait one turn. |
| G | Pick up the item under your feet. |
| > | Descend, when you are standing on the stairs. |
| Ctrl+N / F2 | New game. |
| Ctrl+Q | Quit. |

## The rules, exactly

- **Five floors**, each procedurally generated: **4 to 9 rooms** (4x4 to 10x6),
  non-overlapping, joined by **L-shaped corridors**. Generation is retried until
  the floor is provably **connected** — a flood fill from the entry must reach
  the stairs and the middle of every room. `@` is you, `#` wall, `.` floor,
  `>` stairs.
- **You**: 12 hp, attack **1d4+1**, defence 0. Your attack roll is
  `1d4 + 1 + weapon − target defence`, minimum 0.
- **Monsters**: `r` rat (2 hp, 1 dmg), `c` ice crab (4 hp, 2 dmg), `w` wolf
  (6 hp, 3 dmg), and **`T` The Thaw** (20 hp, 4 dmg) who lives on floor 5 and
  nowhere else. A monster's damage is its damage minus your defence, minimum 0.
  Turn based: every action you take advances one turn, then every monster acts.
  A monster that can see you (radius 7, walls block) walks at you; adjacent, it
  hits you; out of sight, it wanders.
- **Items**, one per tile: `!` fish heals 3, `?` scroll maps the floor, `$` gold
  (1 to 5, it scores), `+` parka +1 defence. Pick up with `g`; they apply on
  pickup.
- **Field of view** radius 7 with line of sight. Tiles you have seen stay drawn
  **dim**; tiles you have never seen are blank.
- **Permadeath.** No save, no undo. `>` on floor 5 is the bottom: The Thaw is
  there. Kill it and you win.

## Scoring

```
score = floor * 100 + gold + kills
```

The best score, the deepest floor, the most kills, games played and wins persist
at `$XDG_DATA_HOME/pengyos/games/pengy-rogue/best.txt` (default
`~/.local/share/pengyos/games/pengy-rogue/`) — under your home, never `/usr`.

## Headless: how the game is proven to work

The repo gate and `pengyos-qemu-verify` have no display, so the game must
work without one. It does: with `--headless` or `--replay` it never opens a
window, never reads keys, prints plain text and exits 0.

```sh
pengy-rogue --headless --seed 7 --no-save
pengy-rogue --state fixtures/boss.state --replay fixtures/win.replay --seed 1 --no-save
pengy-rogue --headless --seed 7 --record /tmp/you.replay --no-save
pengy-rogue --replay fixtures/demo.replay --seed 7 --no-save --print-frame
```

A replay file is **one line of input per tick**: a line with characters is those
keypresses, a blank line is no input, a `#` line is a comment and is not a tick.
The run prints `key=value` lines — `state=`, `floor=`, `score=`, `hash=` — and
exits 0. `hash=` is FNV-1a over the whole position, so **the same seed and the
same script give the same hash**, byte for byte.

Fixtures in `fixtures/`:

| File | What it proves |
|---|---|
| `boss.state` | Floor 5, The Thaw wounded to 1 hp, standing right below the penguin. |
| `win.replay` | The one-swing session that beats it: `state=win`, `kills=8`, `score=517`. |
| `demo.replay` | A whole autopilot session from seed 7: 280 turns, floor 4, eight kills, `score=417`, then permadeath; reproducible. |

## Flags

`--seed N`, `--headless`, `--replay FILE`, `--state FILE`, `--save FILE`,
`--record FILE`, `--ticks N`, `--autopilot`, `--mono`, `--no-color`,
`--print-frame`, `--width N`, `--height N`, `--data-dir DIR`, `--no-save`,
`--help`, `--version`. `NO_COLOR` in the environment is honoured automatically.
`--mono` draws plain ASCII: no box drawing, no dim, no colour.

The layout is designed for **80x24** and degrades in a fixed order in a smaller
window — the score panel, then the border, then the title, then the status line,
then the key legend. The map is always drawn, clipped to the window.

## Tests

```sh
cd tools/pengy-rogue
cargo test          # 44 tests, well under a second
```

- `tests/rules.rs` — the generator (100 seeds: connected, no room overlap,
  stairs reachable, one item per tile), the field of view (a wall blocks, the
  radius edge is in and the next tile out), combat (the roll formula,
  deterministic for a fixed seed, permadeath), the monsters (a monster in sight
  closes the distance, a monster out of sight wanders), the items, the stairs,
  the score formula, the saved-position round trip, the frame, determinism.
- `tests/cli.rs` — the real binary with no display: `--help`, `--headless`,
  `--replay`, `--record`, `--save`, `--mono`, `NO_COLOR`, `--print-frame` at
  80x24 and at 40x12, the exit codes (2 for a bad flag, 66 for a bad file), and
  the record landing only in the data dir.
- `tests/github_ready.rs` — the tool-directory contract: the docs, the
  verifier, no dependencies without `cosmic`, the libcosmic pin, the pure
  library, the fixtures, the launcher and icon in the Desktop overlay, and the
  build-cosmic.sh and post-build wiring.

## Layout

```
src/lib.rs    every rule, the map generator, the FOV, the text frame — pure, no I/O
src/main.rs   hands the command line to cli::main
src/cli.rs    the CLI, the record file, the headless harness; no flag opens the window
src/app.rs    the window (libcosmic): the map on a canvas, the side panel, the keys
tests/        rules, cli, github_ready
fixtures/     boss.state, win.replay, demo.replay
```

## In the image

Ice Rogue ships on the Desktop editions inside **one shared binary**,
[`pengy-games`](../pengy-games/), with the other seven games, so the image
carries one copy of libcosmic for all eight. `scripts/build-cosmic.sh` builds
`pengy-games` (which links this crate with `--features cosmic`), and
`board/desktop/post-build.sh` installs it with `/usr/bin/pengy-rogue` as a symlink
to it: run under that name, it runs Ice Rogue, with every argument. The launcher
(`usr/share/applications/io.catbee.pengyos.IceRogue.desktop`, `Exec=pengy-rogue`) and
the icon (`usr/share/icons/hicolor/scalable/apps/io.catbee.pengyos.IceRogue.svg`)
are in the Desktop overlay. Core and Server do not ship it. This crate's own
`pengy-rogue` binary is the same program, for tests and development.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
