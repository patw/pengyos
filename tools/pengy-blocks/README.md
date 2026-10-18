# pengy-blocks — Glacier Blocks

Ten columns, twenty rows, seven pieces, no mercy. The real Tetris, for the
PengyOS terminal: a 7-bag randomiser, gravity that accelerates by level
(800 ms down to 80 ms), wall kicks, a 300 ms lock delay that resets when you
move, soft drop at 1 point a row, hard drop at 2, 100/300/500/800 × level for
1/2/3/4 lines, a next-3 preview, a hold slot, and a score that persists.

Every OS used to ship a couple of small complete games that just worked —
Windows had Solitaire and Minesweeper, the Atari 2600 had its pack-in cart, BSD
had `greedy` and `phantasia`. PengyOS ships ten: floe, merge, icefield,
breakout, invaders, lander, **blocks**, pong, solitaire, rogue. This is blocks.

Rust 2021, **`std` only, zero external crates**, no network, no root, no
daemon. It runs in any terminal, and it runs with no terminal at all.

## Play it

```sh
cd tools/pengy-blocks
cargo run --release            # play (needs a terminal)
cargo run -- --help            # every flag and key
cargo run -- --headless --seed 42 --no-save   # prove it works with no pty
```

| Key | What it does |
|---|---|
| `h` `l` | Move left / right. `a` `d` and the arrow keys work too. |
| `j` | Soft drop: one row per press, 1 point per row. `Down` works too. |
| `k` `x` | Rotate clockwise. `Up` works too. |
| `z` | Rotate counter-clockwise. |
| `space` | Hard drop — straight to the floor, 2 points per row, locks at once. |
| `c` | Hold: swap the current piece with the hold slot. Once per piece. |
| `p` | Pause / resume. |
| `r` | Restart (next seed in the seed family, best score kept). |
| `q` | Quit, restoring the terminal. `Ctrl-C` quits too. |
| anything else | Ignored. `Escape` is deliberately not a quit key: terminals send the arrow keys as `ESC [ A`, so a bare `ESC` has to stay harmless. |

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

The repo gate and `pengyos-qemu-verify` have no pty, so the game must work
without one. It does: with no TTY, with `--headless`, or with `--replay`, it
never touches the terminal, never reads keys, prints plain text and exits 0.

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
| `src/main.rs` | The terminal front-end: `stty raw -echo`, a stdin reader thread feeding the loop over `std::sync::mpsc`, the alternate screen with one home+repaint per frame, a fixed-timestep loop that sleeps instead of spinning, the CLI, the best-score file. |
| `tests/rules.rs` | The rules: the 7-bag invariant over 70 draws, every wall-kick case, line-clear collapse (a single, a tetris, a non-adjacent pair), the scoring table, the level thresholds, the lock delay and its reset and its cap, the spawn-overlap game over, the key table, determinism, the pinned golden end state, rendering and the degrade rules. |
| `tests/cli.rs` | The real binary with no pty: `--help`, `--headless`, `--replay`, `--record`, exit codes 0/2/66, `NO_COLOR`, `--mono`, frame geometry, persistence in the data dir and nowhere else. |
| `tests/github_ready.rs` | The tool-directory contract (docs, license, verifier, empty dependencies, pure library, launcher, recipe). |
| `fixtures/win.replay` | A committed 1940-tick winning session, seed 42, 168 lines, level 17. |
| `launcher/pengy-blocks.desktop` | The Desktop menu entry (`Terminal=true`, `Categories=Game;`). |
| `../../buildroot-external/package/pengy-blocks/` | The Buildroot recipe skeleton that builds this crate from the repo into `/usr/bin`. |

## Buildroot

`buildroot-external/package/pengy-blocks/` carries `Config.in` +
`pengy-blocks.mk`: a `local`-source recipe that runs `cargo build --offline
--release` on this directory and installs the binary to
`/usr/bin/pengy-blocks` plus the `.desktop` file. It needs no vendored crates
and no download digest, because the crate has **no dependencies** — that is the
point of the zero-dep rule.

Image wiring (the `source` line in `buildroot-external/Config.in`, the layer
`.config`, the menu) is deliberately **not** in this change: it is one shared
file touched by ten games, so it is a single integration pass, not ten.

## Tests

```sh
cd tools/pengy-blocks && cargo test     # 70 rule tests + 21 CLI tests + 10 contract tests, <1s
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
