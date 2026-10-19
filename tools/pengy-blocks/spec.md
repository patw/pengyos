# pengy-blocks — spec

The contract for Glacier Blocks, the PengyOS pack-in Tetris. Rules, keys,
states, the headless harness and the acceptance criteria. `README.md` is the
user's entry point; this file is what the tests enforce.

## Purpose

PengyOS ships ten small, complete Desktop games. This is the falling-
blocks slot, and it has to be the real thing: tuned gravity, real wall kicks, a
lock delay you can play with, a score, a persisted best, a clean quit, no
crashes, no flicker.

## Constraints (non-negotiable)

1. **Rust 2021; the rules are `std` only.** `src/lib.rs` uses no crate and no
   I/O. Without the `cosmic` feature `[dependencies]` is empty, so `cargo test`
   needs no network and no display; a test asserts both.
2. **The window is libcosmic**, behind the `cosmic` feature, at the pin the
   other PengyOS apps use. it ships inside the shared `pengy-games` binary
   (`/usr/bin/pengy-blocks` is a symlink to it); Core and Server do not ship it.
3. **The window paints the game's state**, every tick, on a canvas, and turns
   the keyboard into the game's key characters (see *The window*).
4. **Hand-written seeded RNG** (xorshift64*). Every run takes `--seed N` and is
   reproducible bit for bit.
5. **The text frame is a pure function of state**: `Game::render(&self,
   RenderOpts) -> String`, printed by `--print-frame`. Same state, same bytes.
6. **Never require a display.** With `--headless` or with `--replay`: no
   window, no key reading, plain text out, exit 0. With no harness flag, a build
   without the window says how to get one and exits 2.
7. **Fixed timestep.** The game advances in `TICK_MS` steps on a timer, never
   faster; a finished game stays on its end screen until a new game or quit.
8. **Mutable state lives in one place**:
   `$XDG_DATA_HOME/pengyos/games/pengy-blocks/` (default
   `~/.local/share/...`), Tier-1 under `$HOME`. Never `/usr`, never the repo,
   never anywhere else. A write failure is ignored.
9. No network, no services, no root, no cron, no nested jobs, no process left
   running.

## The game

### Geometry

| | |
|---|---|
| Well | 10 columns x 20 rows visible, plus 2 hidden spawn rows above (`BOARD_ROWS = 22`) |
| Spawn | anchor row 0, anchor column 3 (centres a 3-wide piece) |
| Cell | drawn as two characters; colour is a background fill |
| Ghost | the landing preview of the current piece, drawn under it |

### Pieces

All seven tetrominoes in the standard SRS orientations: rotation 0 is the
spawn orientation, 1 is clockwise from it, 2 = 180, 3 = counter-clockwise. The
O does not change when it rotates.

### The 7-bag

Every piece appears exactly once per bag. Two bags are kept (the current one
and the next) so the next-3 preview can look ahead without consuming
randomness.

### Gravity and level

`level = 1 + lines / 10`. The gravity interval, in milliseconds per row,
indexed by `level - 1`:

```
800, 720, 630, 550, 470, 380, 300, 220, 130, 100, 80
```

Level 11 and above run at 80 ms.

### Rotation and wall kicks

A rotation tries, in order: in place, +1 column, −1 column, +2 columns, −2
columns, up 1 row. The first placement that fits is applied. If none fits, the
rotation is refused and the piece does not move.

### Lock delay

A piece that cannot move down locks after **300 ms**. A successful move or
rotation resets the delay, up to **15** times per piece. A piece that is not
grounded accumulates no delay.

### Scoring

| Event | Points |
|---|---|
| 1 line | 100 x level |
| 2 lines | 300 x level |
| 3 lines | 500 x level |
| 4 lines | 800 x level |
| Soft drop | 1 per row moved by a keypress |
| Hard drop | 2 per row dropped |

The well collapses after a clear: every row above a cleared row falls by
exactly the number of cleared rows below it. Rows between two clears fall by
one; rows below the lowest clear do not move.

### Hold

`c` swaps the current piece with the hold slot. If the slot is empty, the next
piece from the bag is used. One hold per piece: it becomes available again when
the piece locks. The swapped piece spawns fresh (rotation 0, spawn position) and
the timers restart.

### Game over

A spawn that overlaps the well ends the game (`cause = top-out`). A finished
game is frozen: ticking and playing change nothing.

## States

| State | Meaning |
|---|---|
| `Play` | the game runs; keys and ticks apply |
| `Paused` | `p`; nothing ticks, `p` resumes |
| `Over` | `cause = top-out`; frozen, `r` restarts, `q` quits |

## Keys

| Key | Action |
|---|---|
| `h` `a` Left | move one column left |
| `l` `d` Right | move one column right |
| `j` Down | soft drop one row, 1 point |
| `k` `x` Up | rotate clockwise (with kicks) |
| `z` | rotate counter-clockwise (with kicks) |
| `space` | hard drop, 2 points per row, lock now |
| `c` | hold |
| `p` | pause / resume |
| `r` | restart on the next seed in the family; the record is kept |
| `q` | quit (the replay harness stops) |
| anything else | ignored |

These are the game's key characters, as a replay file writes them. The window
maps its controls onto them: the arrows move and rotate, space hard-drops,
shift or `c` holds, Esc pauses, Ctrl+N starts a new game, Ctrl+Q quits. A held
arrow repeats at the compositor's key-repeat rate.

## The window

One canvas: the 10x20 well with a faint grid, the stack as bevelled blocks
coloured by piece, the ghost of a hard drop, and the falling piece — drawn
above the rim while it is still in the two hidden spawn rows, so a new piece is
never invisible. The hold slot is on the left with the controls under it; the
next three pieces, the score, lines, level and best are on the right. Pause and
game over cover the well with a banner. The header has New Game and
Pause/Resume. The best score is banked once per game, on the game over (or
when the game is given up for a new one, or the window closes).

## The text frame (`--print-frame`)

A debugging aid for the headless harness, not the game's display. Reference
window **80x24**: title line, the well with its border (20 rows x 10
cells at two characters per cell), the side panels (hold, next-3, score/level/
lines/best), and the key legend.

Degrade order in a smaller window: **side panels** (below 40 columns, or below
14 rows), **title** (below 40 columns, or below 23 rows), **legend** (below 24
rows), **border** (below 22 columns). A short window clips the well from the
bottom. Every drawn line is exactly the window width — long lines are clipped,
never scrolled.

`--mono` draws ASCII only (`+ - | [ ]`), no colour. `NO_COLOR`, and `--no-color`,
emit no escapes. A headless frame is plain text: no escapes at all.

## The headless contract

* `--headless`: the built-in player plays, with no window,
  the plain-text state is printed, exit 0.
* `--replay FILE`: one line of input per tick, blank = no input, `#` = comment,
  `_` = the hard drop. Stops at the game-over tick or EOF, prints `key=value`
  lines, exits 0.
* `--record FILE`: with `--headless` and `--seed`, write the session in the
  `--replay` format.
* Exit codes: **0** success, **2** usage error, **66** unreadable replay file.

## Acceptance criteria

1. `cargo test` is green in this directory, in under 5 seconds.
2. The 7-bag invariant holds over 70 draws, from several seeds: exactly 10 of
   each piece, and every consecutive group of seven is the full set.
3. Every wall-kick case is asserted against a hand-built board: in place, +1,
   −1, +2, −2, up 1, and the refused rotation.
4. Line-clear collapse is asserted on hand-built boards: a single row, a
   4-line clear, and a **non-adjacent pair** (rows 18 and 20 with rows 19 and
   21 between and below).
5. The scoring table is asserted for 1/2/3/4 lines and for a level multiplier.
6. The level thresholds and the gravity ladder are asserted, including the
   level-11 floor.
7. The lock delay is asserted: locks at 300 ms, resets on a successful move,
   the reset budget is capped at 15.
8. A spawn that overlaps the well ends the game with `cause = top-out`, and a
   finished game is frozen.
9. Same seed + same script => the same state hash; a different seed => a
   different one. The golden run (seed 42, 4000 ticks) is pinned: score 174528,
   168 lines, level 17, 464 pieces, hash `53cbffd1a21e3566`.
10. `pengy-blocks --replay fixtures/win.replay --seed 42` exits 0 and prints
    that same result.
11. The headless harness needs no display and exits 0; with no harness flag a
    build without the window exits 2 and says how to get one; `--help` lists
    every flag and control; usage errors exit 2; a missing replay file exits 66.
12. The frame fits 80x24, degrades in the documented order, and every line is
    exactly the window width.
13. The best score is written under `$XDG_DATA_HOME` (default
    `$HOME/.local/share`), and nowhere else.
14. Without `cosmic`, `[dependencies]` is empty; `src/lib.rs` touches no I/O;
    libcosmic is pinned where pengyos-usage pins it; the launcher and the icon
    are in the Desktop overlay under the app id; `pengy-games` links the crate with its window, `scripts/build-cosmic.sh`
    builds that, and the Desktop post-build links `/usr/bin/pengy-blocks` to it.

## Non-goals

No music, no sound, no themes, no mouse play, no network, no high-score server, no
multiplayer, no rotation system beyond the kicks above, no ghost-toggle, no
config file. Ten wide, twenty tall, seven shapes, no mercy.
