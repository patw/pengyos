# pengy-pong — Penguin Pong

Two paddles, one ball, first to eleven. Play the penguin at difficulty 1, 2 or
3, or a friend on the same keyboard.

The 1972 arcade original, rebuilt for the PengyOS terminal: a 60x20 field,
4-row paddles, a ball that crosses it one cell per tick, a bounce angle that
comes from the row the ball met the paddle on, a speed that climbs 5% per hit up
to twice the serve speed, a 1-second countdown between points, and a score that
persists. It is one of the ten pack-in games PengyOS ships — Windows had
Solitaire and Minesweeper, the Atari 2600 had its cart, BSD had `greedy` and
`phantasia`; PengyOS ships ten little complete games, and this is one of them.

Rust 2021, **`std` only, zero external crates**, no network, no root, no daemon.
It runs in any terminal, and it runs with no terminal at all.

## Play it

```sh
cd tools/pengy-pong
cargo run --release            # play (needs a terminal)
cargo run -- --help            # every flag and key
cargo run -- --headless --seed 7 --difficulty 2   # prove it works with no pty
```

| Key | What it does |
|---|---|
| `w` `s` | Move your paddle up / down. The arrow keys work too. |
| `k` `j` | Up / down for the right-hand paddle in 2P; the same as `w`/`s` in 1P. |
| `1` `2` `3` | The penguin's difficulty. Picks 1P. |
| `m` | Toggle 1P / 2P (hotseat). |
| `space` | Serve now — skip the 1-second countdown. |
| `p` | Pause / resume. |
| `r` | Restart (next seed in the seed family, best result kept). |
| `q` | Quit, restoring the terminal. `Ctrl-C` quits too. |
| anything else | Ignored. `Escape` is deliberately not a quit key: terminals send the arrow keys as `ESC [ A`, so a bare `ESC` has to stay harmless. |

## The rules, exactly

- The field is 60x20. The paddles are 4 rows tall and 1 column wide, on columns
  1 and 58. The ball is 1 cell.
- A tick is 40 ms. The ball moves 1 cell per tick along the field at serve
  speed; its `vy` changes only when a paddle hits it.
- **The bounce angle comes from the row the ball met the paddle on**: the two
  inner rows give ±10 degrees (the shallowest bounce, and the closest a 4-row
  paddle gets to straight), the two edge rows give ±50 degrees. Every angle is a
  multiple of 10 degrees.
- **The speed rises 5% per paddle hit, capped at twice the serve speed**, and
  resets to serve speed on the next point.
- The serve alternates and travels **toward the player who lost the point**,
  after a 1-second countdown. `space` serves early.
- The top and bottom walls reflect the ball. A paddle hit is a *crossing* of the
  paddle face, so a fast ball can never tunnel through a paddle.
- **First to 11 wins. At 10-10 a two-point margin is required. 15 is the hard
  cap.**
- A paddle that is moving when it hits the ball adds spin, which is what makes a
  rally develop instead of looping for ever.
- A rally longer than 900 ticks (36 s) is awarded to the player who hit the ball
  last — the other one has had the whole rally to return it. This is the stall
  guard: every point ends.
- The penguin's difficulty caps its speed at **1 / 2 / 3 rows per tick** and
  gives it a reaction delay of **6 / 3 / 1 ticks**: it only ever plays the ball
  as it was that many ticks ago. Level 1 is for learning the angle table, level
  2 is a match, level 3 is the one that beats you.

## Headless: how the game is proven to work

The repo gate and `pengyos-qemu-verify` have no pty, so the game must work
without one. It does: with no TTY, with `--headless`, or with `--replay`, it
never touches the terminal, never reads keys, prints plain text and exits 0.

```sh
pengy-pong --headless --seed 7 --ticks 60000 --no-save   # the built-in autopilot plays
pengy-pong --replay fixtures/win.replay --seed 7 --mode 1p --difficulty 2
pengy-pong --headless --seed 21 --record /tmp/you.replay --no-save   # record a session
```

A replay file is **one line of input per tick**: a line with characters is those
keypresses, a blank line is no input, a `#` line is a comment and is not a tick.
The harness stops at the match-over tick (a finished match is frozen) or at EOF,
prints the final state as `key=value` lines, and exits 0. `hash=` is FNV-1a over
the whole state: same seed, same script, same hash.

`fixtures/win.replay` is a committed 7319-tick session — seed 7, level 2, 11-7,
hash `070441adffab99fc`.

## Flags

`--seed N` (default 1), `--mode 1p|2p`, `--difficulty 1..=3` (default 2),
`--headless`, `--replay FILE`, `--record FILE`, `--ticks N`, `--autopilot`,
`--mono`, `--no-color`, `--print-frame`, `--width N`, `--height N`,
`--data-dir DIR`, `--no-save`, `--help`, `--version`. `NO_COLOR` is honoured.
Exit codes: **0** success, **2** usage error, **66** unreadable replay file.

Designed for **80x24**. In a smaller window it degrades: the title line goes,
then the status line, then the border (which carries the key legend), and a
short window clips the field from the bottom. The 60-column field is never
dropped. `--mono` draws in plain ASCII when the locale cannot render the box
drawing.

## Where the best result lives

`$XDG_DATA_HOME/pengyos/games/pengy-pong/best.txt` (default
`~/.local/share/pengyos/games/pengy-pong/best.txt`): matches won, best margin,
longest rally, the seed it happened on. Tier-1 mutable, under `$HOME`, never
`/usr`, never the repo. A failure to write is ignored — a read-only `$HOME`
must not cost you a game.

## Layout

| Path | What is in it |
|---|---|
| `src/lib.rs` | **Every rule and the renderer, pure**: no I/O, no clock, no threads, no files. The seeded xorshift64* is the only randomness, so a run is a function of `(seed, script)`. |
| `src/main.rs` | The terminal front-end: `stty raw -echo`, a stdin reader thread feeding the loop over `std::sync::mpsc`, the alternate screen with one home+repaint per frame, a fixed-timestep loop that sleeps instead of spinning, the CLI, the best-score file. |
| `tests/rules.rs` | The rules: field and paddles, the angle table, the speed ladder and its cap, points and serve direction, walls and the no-tunnel rule, win-by-2 and the 15 cap, the AI cap over 500 ticks, keys, determinism, the pinned golden end state, rendering. |
| `tests/cli.rs` | The real binary with no pty: `--help`, `--headless`, `--replay`, `--record`, exit codes 0/2/66, `NO_COLOR`, `--mono`, frame geometry, persistence in the data dir and nowhere else. |
| `tests/github_ready.rs` | The tool-directory contract (docs, license, verifier, empty dependencies, pure library, launcher, recipe). |
| `fixtures/win.replay` | A committed 7319-tick winning session, seed 7, 11-7. |
| `launcher/pengy-pong.desktop` | The Desktop menu entry (`Terminal=true`, `Categories=Game;`). |
| `../../buildroot-external/package/pengy-pong/` | The Buildroot recipe skeleton that builds this crate from the repo into `/usr/bin`. |

## Buildroot

`buildroot-external/package/pengy-pong/` carries `Config.in` +
`pengy-pong.mk`: a `local`-source recipe that runs `cargo build --offline
--release` on this directory and installs the binary to
`/usr/bin/pengy-pong` plus the `.desktop` file. It needs no vendored crates and
no download digest, because the crate has **no dependencies** — that is the
point of the zero-dep rule.

Image wiring (the `source` line in `buildroot-external/Config.in`, the layer
`.config`, the menu) is deliberately **not** in this change: it is one shared
file touched by ten games, so it is a single integration pass, not ten.

## Tests

```sh
cd tools/pengy-pong && cargo test
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
