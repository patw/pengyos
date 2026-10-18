# pengy-pong — Penguin Pong (Pong, 1972)

The contract for the PengyOS pack-in Pong. Implementation and tests are the
authority on detail; this file is the authority on *intent*.

## Summary

Two paddles, one ball, first to eleven. A 60x20 field, 4-row paddles, a ball
that crosses it one cell per tick at serve speed, a bounce angle from the row the
ball met the paddle on, a speed that climbs 5% per hit to twice the serve speed,
a 1-second countdown between points. Play the penguin at difficulty 1, 2 or 3, or
a friend on the same keyboard. Terminal-first, playable in 80x24, Rust 2021,
`std` only, zero external crates.

## Purpose

PengyOS ships a desktop and a terminal and no games. Every OS that had a
personality shipped a couple of small, complete ones (Windows Solitaire and
Minesweeper, the Atari 2600 cart, the BSD games). This is one of ten: a tiny,
finished game that runs anywhere the OS runs, including over ssh and on Core.

## Deliverable

`tools/pengy-pong/`: a zero-dependency Rust crate (`src/lib.rs` pure rules +
renderer, `src/main.rs` terminal front-end), its docs, its suite, a committed
replay fixture, a `.desktop` launcher, and a Buildroot recipe skeleton at
`buildroot-external/package/pengy-pong/`.

## Interface

### Field and rules

| Quantity | Value |
|---|---|
| Field | 60 columns x 20 rows |
| Paddles | 4 rows tall, 1 column wide, on columns 1 (player) and 58 (penguin / player 2) |
| Ball | 1 cell, position and velocity in fixed-point cells (x100) |
| Tick | 40 ms, fixed timestep |
| Ball motion | 1 cell per tick along the field at serve speed; `vy` changes only on a paddle bounce |
| Serve speed | 100 fixed-point units = 1 cell per tick |
| Speed ladder | `speed = speed * 105 / 100` per paddle hit, capped at 200 (2x serve), reset on the point |
| Serve | Toward the player who lost the point, after a 25-tick (1 s) countdown; `space` serves early |
| Walls | Top and bottom reflect `vy`; the ball is clamped back inside |
| Paddle hit | A **crossing** of the paddle face (no tunnelling at the speed cap) |
| Scoring | The ball passing a paddle is a point for the other side |
| Match | First to 11, win by 2 required at 10-10, hard cap 15 |
| Rally cap | A rally over 900 ticks (36 s) is a point for the player who hit last |

### The reflection angle table

The slot is the paddle row the ball met, 0 = top row .. 3 = bottom row. A 4-row
paddle has no exact centre row, so the two inner rows give the shallowest bounce
the game allows (±10°, the closest thing to "straight" a 4-row paddle has) and
the two edge rows give the full ±50°. Every angle is a multiple of 10 degrees.

| Slot | Angle | tan(angle) x100 | vy at serve speed |
|---|---|---|---|
| 0 (top edge) | -50° | -119 | -119 |
| 1 (inner) | -10° | -18 | -18 |
| 2 (inner) | +10° | +18 | +18 |
| 3 (bottom edge) | +50° | +119 | +119 |

`vy = speed * tan(angle) / 100`, so the angle holds as the speed climbs. A paddle
that is moving when it hits adds **spin** (`SPIN_PER_ROW = 40` per row per tick of
paddle motion), clamped to the steepest angle the table allows at that speed.
Spin is what makes a rally develop: a paddle that only ever centres on the ball
returns the same shallow ball for ever.

### The penguin (1P)

| Difficulty | Speed cap | Reaction delay |
|---|---|---|
| 1 | 1 row per tick | 6 ticks |
| 2 | 2 rows per tick | 3 ticks |
| 3 | 3 rows per tick | 1 tick |

The penguin only ever plays the ball as it was `delay` ticks ago, and its move is
clamped to its cap every tick — it can never exceed it. The autopilot
(`--headless`, `--autopilot`, `--record`) is the same rule aimed at the player's
paddle with a 2-tick delay, a 2-row cap and the intercept (where the ball will
meet the paddle, walls reflected): a strong human, not a god, so the demo path is
beatable by level 3.

### States

`Serve` (the 1-second countdown) → `Play` → `Serve` … → `Over` (the match is
won, or the 15-point cap ended it). `Pause` freezes the ball; `p` toggles it. A
finished match is **frozen**: ticking changes nothing at all, and the frame says
what to do next.

### Keys

| Key | Effect |
|---|---|
| `w` `s` | The player's paddle, up / down. One row per keypress. |
| `k` `j` | Up / down. In 2P the right-hand paddle; in 1P the player's own. The front-end delivers the arrow keys (`ESC [ A/B`) as `k`/`j`. |
| `1` `2` `3` | The penguin's difficulty. Picks 1P. |
| `m` | Toggle 1P / 2P. |
| `space` | Serve now. |
| `p` | Pause / resume. |
| `r` | Restart: a new match on `seed + 1`, banked best kept. |
| `q`, `Ctrl-C` | Quit, restoring the terminal. |
| anything else | Ignored. `Escape` is **not** a quit key — terminals deliver the arrow keys as `ESC [ A`, so a bare `ESC` must be harmless. |

### CLI

`--seed N` (default 1), `--mode 1p|2p`, `--difficulty 1..=3` (default 2),
`--headless`, `--replay FILE`, `--record FILE`, `--ticks N`, `--autopilot`,
`--mono`, `--no-color`, `--print-frame`, `--width N`, `--height N`,
`--data-dir DIR`, `--no-save`, `--help`, `--version`. `NO_COLOR` is honoured.
Exit codes: **0** success, **2** usage error (one diagnostic line), **66**
unreadable replay file.

### Replay format

One line per tick. A line with characters is those keypresses, applied in order
(a held key autorepeats, so a line may carry more than one); a blank line is no
input; a line starting with `#` is a comment and is not a tick. The harness stops
at the match-over tick (a finished match is frozen) or at EOF, then prints the
final state as `key=value` lines and exits 0:

```
state=over seed=7 mode=1P difficulty=2 ticks=7319 points=18 left=11 right=7
hits=139 speed=100 rally=435 longest_rally=628 best_wins=1 best_margin=4
best_rally=628 hash=070441adffab99fc cause=won winner=left
replay=fixtures/win.replay quit=0 stopped_at=eof
```

`hash=` is FNV-1a over the canonical serialization of the whole state: two runs
with the same seed and the same script have the same hash.

### Persistence

`$XDG_DATA_HOME/pengyos/games/pengy-pong/best.txt` (default `~/.local/share/…`),
holding `wins=`, `margin=`, `rally=`, `seed=`. Tier-1 mutable, under `$HOME`,
never `/usr`, never the repo. A failure to write is ignored: a read-only `$HOME`
must not cost the player a game.

### Rendering

`Game::render(&Game, RenderOpts) -> String` — a pure function of state plus the
window size and the glyph policy. Exactly `height` rows, every row exactly
`width` visible columns, so one `home` + one write per frame repaints the screen
with no leftovers and no flicker; no per-cell cursor addressing. Colour via SGR
(paddles, ball, borders), off under `NO_COLOR` and `--mono`; `--mono` is the
ASCII glyph set (`#` paddles, `O` ball, `+ - |` borders, `:` net).

Degrade rules: the title/scoreboard line needs 68 columns and 24 rows; the
border — which carries the key legend — needs 62 columns and 22 rows; the status
line needs 23 rows. Below that the title goes, then the status, then the border,
and a short window clips the field from the bottom. The 60-column field itself is
never dropped. 80x24 shows title, status, bordered field and legend exactly.

### Terminal

`stty raw -echo` via `std::process::Command`, the alternate screen (`?1049h` /
`?1049l`), the cursor hidden, one `home` + one write per frame. Restored by a
`Drop` guard **and** a panic hook, so `q`, `Ctrl-C`, a normal exit and a panic all
leave the terminal exactly as they found it. Keys arrive on a worker thread over
`std::sync::mpsc`; the loop sleeps to its deadline, so it is a fixed timestep with
no busy-wait. If `stty` is missing, or stdin is not a tty, the game degrades
instead of failing.

## Constraints

- Rust 2021, `std` only, **zero external crates**: no libc, no rand, no crossterm,
  no clap, no serde. `[dependencies]` is empty and stays empty — that is what
  makes the Buildroot recipe an offline, reproducible `cargo build --release`.
- `src/lib.rs` is **pure**: no I/O, no clock, no threads, no files, no system
  entropy. The RNG is a hand-written seeded xorshift64*, so every run takes
  `--seed N` and is reproducible bit for bit.
- Never require a TTY: `--headless`, `--replay`, and any run with a non-tty stdin
  never render and never read keys.
- No network, no services, no root, no cron, no nested jobs, nothing left running.
- Writes only under `$XDG_DATA_HOME/pengyos/games/pengy-pong/`.

## Acceptance criteria

- `cargo test` green in well under 5 s, zero warnings on a clean build.
- `pengy-pong --headless --seed 7 --ticks 60000 --no-save` exits 0, prints
  plain-text state, and reports `winner=left`.
- `pengy-pong --replay fixtures/win.replay --seed 7 --mode 1p --difficulty 2`
  exits 0, reports `left=11`, `cause=won`, `hash=070441adffab99fc`, and is
  byte-identical run twice.
- The AI never moves more than its cap in a tick, over 500 ticks, at every level.
- The ball speed never exceeds twice the serve speed, and never tunnels a paddle.
- A match against every level finishes with a winner; level 1 is easier than
  level 3; no match stalls.
- Playable in 80x24; a 40x10 window renders a clipped, unwrapped game; 1x1 does
  not crash.
- `q` and `Ctrl-C` leave the terminal exactly as they found it; a panic does too.
- The crate builds with `[dependencies]` empty: `cargo build --release --offline`.
- The recipe skeleton names the crate, pins its version, declares MIT, and
  installs `/usr/bin/pengy-pong`.

## Non-goals

- Image wiring: `buildroot-external/Config.in`, `configs/layers/*.config`,
  `board/desktop/rootfs-overlay`, and the `tools/README.md` index entry. Ten
  games touching three shared files is a conflict generator; one integration pass
  does it.
- Sound, sprites, mouse, touch, gamepad, network play, ladders, accounts.
- Online scores, telemetry, network of any kind.
- Windows below 25x10 being *good*; they must merely not crash.

## Feel

Loud and clean: a black field, a bright ball, the net down the middle, the key
legend built into the bottom border, and a scoreboard that never moves. The
rally is the whole game — the speed climbs, the angles steepen, and the point
ends when somebody's paddle is not there.
