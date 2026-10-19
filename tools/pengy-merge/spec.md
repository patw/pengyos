# pengy-merge — Icefloe 2048 (2048-style sliding floes)

The contract for the PengyOS pack-in 2048. Implementation and tests are the
authority on detail; this file is the authority on *intent*.

## Summary

A field of ice floes numbered 2, 4, 8, …. Slide the field; every floe goes as
far as it can, equal floes fuse into one of double value, the fused value is
scored. Reach 2048. A COSMIC window; the rules are Rust 2021, `std` only.

## Purpose

PengyOS ships a desktop and a terminal and no games. Every OS that had a
personality shipped a couple of small, complete ones (Windows Solitaire and
Minesweeper, the Atari 2600 cart, the BSD games). This is one of ten: a tiny,
finished game in the Desktop's App Library.

## Deliverable

`tools/pengy-merge/`: a Rust crate whose rules (`src/lib.rs`) are `std` only,
a libcosmic window (`src/app.rs`, the `cosmic` feature), a headless harness
(`src/cli.rs`), its docs, its suite, a committed replay fixture; and in the
Desktop overlay its launcher and icon. `scripts/build-cosmic.sh` builds it.

## Interface

### Field and rules

| Quantity | Value |
|---|---|
| Field | 4x4 by default; `--size N` accepts 3..=8 |
| Goal | 2048 by default; `--goal N`, a power of two ≥ 4 |
| Opening | two floes dealt in random empty cells |
| Slide | every floe in every lane moves as far as it goes |
| Fusing | equal neighbours fuse, **from the direction of travel**; a floe fuses **at most once per slide** |
| Score | exactly the sum of the fused values |
| Spawn | after a slide that moved something: one floe in a random empty cell, 2 with 90%, 4 with 10% |
| Tick | 110 ms fixed; the puzzle is turn-based, so the tick drives the loop cadence and the fuse flash only |
| Flash | a fused or dealt floe flashes for 3 ticks (cosmetic, part of the state) |

The slide rule, stated as the tests state it:

| Input | Direction | Result |
|---|---|---|
| `2 2 2 2` | left | `4 4` |
| `4 4 8` | left | `8 8` |
| `2 2 4` | right | `4 4` |
| `2 4 2` | left | `2 4 2` (no chain fuse, nothing moved) |
| `4 4 8 8` | left | `8 16` |
| `8 4 4 8` | right | `8 8 8` |
| `2 2 2` | left | `4 2` (the fused 4 cannot eat the third 2) |

### States

`Playing` → `Won` (the goal floe appeared; slides are frozen, `c` continues) →
`Playing` with `continued` set. `Playing` → `Over` (a slide changed nothing and
no fuse is possible, or the field filled up and nothing can fuse). A finished
run is frozen: ticking changes nothing, and the frame says what to do next.

### Keys

| Key | Effect |
|---|---|
| `h` `j` `k` `l` | Slide left, down, up, right. |
| arrow keys | Same (`ESC [ A/B/C/D`; the front-end hands the loop the letter `A`/`B`/`C`/`D`). |
| `w` `a` `s` | Aliases: up, left, down. |
| `u` | Undo one slide, including the floe it dealt. One step only. |
| `r` | Restart: a new run in the same seed family (`seed + 1`), best score kept. |
| `c` | Keep playing after a win. |
| `q` | Quit (the replay harness stops). |
| anything else | Ignored. |

These are the game's key characters, as a replay file writes them. The window
maps its controls onto them (README, *Play it*).

### CLI

`--seed N` (default 1), `--size N` (default 4), `--goal N` (default 2048),
`--headless`, `--replay FILE`, `--record FILE`, `--ticks N`, `--autopilot`,
`--mono`, `--no-color`, `--print-frame`, `--width N`, `--height N`,
`--data-dir DIR`, `--no-save`, `--help`, `--version`. `NO_COLOR` is honoured.
Exit codes: **0** success, **2** usage error (one diagnostic line), **66**
unreadable replay file.

### Replay format

One line per tick. A line containing a character is that key; a blank line is
no input; a line starting with `#` is a comment and is not a tick. The harness
stops at the end tick (a finished run is frozen) or at EOF, then prints the
final state as `key=value` lines and exits 0:

```
state=over seed=7 size=4 goal=2048 ticks=549 moves=549 fuses=535 score=7976
best=0 max=512 undos=0 hash=9e91c46c6aafb114 cause=stuck
replay=fixtures/win.replay quit=0 stopped_at=eof
```

`hash=` is FNV-1a over the canonical serialization of the whole state: two runs
with the same seed and the same script have the same hash.

### Persistence

`$XDG_DATA_HOME/pengyos/games/pengy-merge/best.txt` (default `~/.local/share/…`),
holding `score=`, `max=`, `moves=`, `seed=`. Tier-1 mutable, under `$HOME`,
never `/usr`, never the repo. A failure to write is ignored: a read-only `$HOME`
must not cost the player a game.

### The text frame (`--print-frame`)

A debugging aid for the headless harness, not the game's display.


`fn render(&Game, RenderOpts) -> String` — a pure function of state plus the
window size and the glyph policy. Exactly `height` rows, every row exactly
`width` visible columns, so one `home` + one write per frame repaints the screen
with no leftovers and no flicker; no per-cell cursor addressing. Colour via SGR
by magnitude (2 dim cyan → 2048 black-on-white), with the fused/dealt floes
bright for 3 ticks; `NO_COLOR`/`--mono` emit **no escape bytes at all**.

Degrade rules: the score panel and the keys line need 42 columns, the header
needs 30, the field box needs the field width plus two (25 for 4x4, 31 for 5x5);
below that the panel goes, then the header, then the box, and the frame is
clipped from the bottom. Values are right-aligned in 5 columns. The reference
window is 80x24.

### The window

One canvas: the field as a square of sea with n x n wells, the floes coloured
from pale frost (2) to deep sea ice (2048) and aurora past it, the value in
bold. A floe that just fused or landed grows out of its well and glows for its
`FLASH_TICKS`. The arrows (or WASD, or hjkl) slide; Ctrl+Z undoes. Reaching the
goal offers Keep Going and New Game; a jammed field says so and offers Undo.
The header has New Game and Undo; the status line shows score, best, moves.

## Constraints

- **Rust 2021; the rules are `std` only.** `src/lib.rs` uses no crate and no
  I/O, and without the `cosmic` feature `[dependencies]` is empty, so
  `cargo test` needs no network and no display.
- **The window is libcosmic**, behind the `cosmic` feature, at the pin the
  other PengyOS apps use; shipped on the Desktop only, inside the shared `pengy-games` binary.
- RNG is a hand-written seeded xorshift64*. No system entropy anywhere. Every
  run takes `--seed N` and is bit-for-bit reproducible.
- **Never requires a display.** `--headless` and `--replay` never open a
  window and never read keys; they print plain text and exit 0. This is how
  the repo gate and `pengyos-qemu-verify` run the game. With no harness
  flag, a build without the window says how to get one and exits 2.
- Fixed timestep: the game steps on its own tick, never faster. A finished
  game stays on its end screen until a new game or quit, and is banked into
  the record once.
- No network, no services, no root, no cron, no nested jobs, no process left
  running.

## Verification (tests are first-class)

`cargo test` green, fast (< 5 s). The rules list, each one tested:

1. **The slide edge cases** — the table above, exactly, plus "a floe never
   fuses twice in one slide".
2. **Score equals the sum of the fused values**, and a slide that moves nothing
   scores nothing, spawns nothing and changes nothing.
3. **A spawn lands only on an empty cell** — swept over 400 seeds, every other
   cell verified unchanged; a full field deals nothing.
4. **Spawn probability is 90/10** — 4000 deals counted.
5. **Game-over detection** — a full field with no adjacent equal pair has no
   legal slide; the run that fills the last gap jams and ends.
6. **Winning** — the goal floe wins, freezes the field, `c` continues, a second
   goal does not re-win, `--goal` moves the target.
7. **Undo** — restores the field, score, move count and generator state, clears
   a game over, and is one step only.
8. **Determinism** — same seed + same script ⇒ same canonical string, hash and
   frame; a different seed ⇒ a different one.
9. **A scripted input sequence through the library lands on the golden end
   state** (seed 11, 28 keys, score 92, 25 moves, 16 fuses, pinned hash).
10. **Rendering** — frame geometry, the degrade rules, mono/NO_COLOR emitting no
    escapes, right-aligned 5-column values, the status lines.
11. **End-to-end, no display**: `--help` lists every flag and key; `--headless` exits 0 with plain text, and with no harness flag a build
   without the window exits 2; the committed fixture replays to
    `max=512 score=7976` with a pinned hash, twice byte-identically;
    `record → replay` round-trips; `NO_COLOR` emits no escapes; `--mono` is
    ASCII-only; usage errors are exit 2 with one line; a missing replay file is
    exit 66; the best score is written under the data dir and nowhere else;
    `--no-save` writes nothing.

**GitHub-ready contract** (asserted by `tests/github_ready.rs`): `README.md`,
`spec.md`, `LICENSE` (MIT, "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`,
`verify.toml`, `Cargo.toml` with no dependencies without `cosmic` and libcosmic
at the shared pin, a pure `src/lib.rs`, the launcher and icon in the Desktop
overlay under the app id, and the `build-cosmic.sh` and post-build wiring.

## Acceptance criteria

- `cargo test` green in under 5 s, zero warnings on a clean build.
- `pengy-merge --headless --seed 7 --ticks 4000` exits 0 and prints plain-text
  state; `pengy-merge --replay fixtures/win.replay --seed 7` exits 0, reports
  `max=512`, and is byte-identical run twice.
- The text frame fits 80x24; a 18x6 one is clipped, unwrapped.
- A finished game stays on its end screen until a new game or quit, and is
  banked into the record once.
- Without `cosmic` the crate builds with `[dependencies]` empty: `cargo build --release --offline`.
- It ships inside `pengy-games` (built by `scripts/build-cosmic.sh`), with
  `/usr/bin/pengy-merge` a symlink to it; the launcher and icon are in the Desktop overlay.

## Non-goals

- Sound, sprites, mouse, touch, gamepad, multiplayer, level editor.
- Online scores, accounts, telemetry, network of any kind.
- Windows below 25x10 being *good*; they must merely not crash.

## Feel

Cold and calm: the field is a box of right-aligned numbers, the floe that just
fused flashes for three ticks, and a mistake costs one keypress — `u` takes the
slide and the floe it dealt straight back.
