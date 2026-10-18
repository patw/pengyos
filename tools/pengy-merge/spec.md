# pengy-merge — Icefloe 2048 (2048-style sliding floes)

The contract for the PengyOS pack-in 2048. Implementation and tests are the
authority on detail; this file is the authority on *intent*.

## Summary

A field of ice floes numbered 2, 4, 8, …. Slide the field; every floe goes as
far as it can, equal floes fuse into one of double value, the fused value is
scored. Reach 2048. Terminal-first, playable in 80x24, Rust 2021, `std` only,
zero external crates.

## Purpose

PengyOS ships a desktop and a terminal and no games. Every OS that had a
personality shipped a couple of small, complete ones (Windows Solitaire and
Minesweeper, the Atari 2600 cart, the BSD games). This is one of ten: a tiny,
finished game that runs anywhere the OS runs, including over ssh and on Core.

## Deliverable

`tools/pengy-merge/`: a zero-dependency Rust crate (`src/lib.rs` pure rules +
renderer, `src/main.rs` terminal front-end), its docs, its suite, a committed
replay fixture, a `.desktop` launcher, and a Buildroot recipe skeleton at
`buildroot-external/package/pengy-merge/`.

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
| `q`, `Ctrl-C` | Quit, restoring the terminal. |
| anything else | Ignored. `Escape` is **not** a quit key — terminals deliver arrow keys as `ESC [ A`, so a bare `ESC` must be harmless. |

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

### Rendering

`fn render(&Game, RenderOpts) -> String` — a pure function of state plus the
window size and the glyph policy. Exactly `height` rows, every row exactly
`width` visible columns, so one `home` + one write per frame repaints the screen
with no leftovers and no flicker; no per-cell cursor addressing. Colour via SGR
by magnitude (2 dim cyan → 2048 black-on-white), with the fused/dealt floes
bright for 3 ticks; `NO_COLOR`/`--mono` emit **no escape bytes at all**.
Alternate screen `?1049h`/`?1049l`, cursor hidden while playing.

Degrade rules: the score panel and the keys line need 42 columns, the header
needs 30, the field box needs the field width plus two (25 for 4x4, 31 for 5x5);
below that the panel goes, then the header, then the box, and the frame is
clipped from the bottom. Values are right-aligned in 5 columns. The reference
window is 80x24.

## Constraints

- **Rust 2021, `std` only, `[dependencies]` empty. Forever.** No `libc`, no
  `rand`, no `crossterm`, no `clap`, no `serde`. This is what makes the Buildroot
  recipe an offline, reproducible `cargo build --release`.
- Raw keyboard via `stty raw -echo` through `std::process::Command`; restored
  with `stty sane` from a `Drop` guard **and** a panic hook. Stdin is read on a
  worker thread and delivered over `std::sync::mpsc`. Missing `stty`, or stdin
  that is not a tty, degrades cleanly (cooked input, normal screen buffer)
  instead of failing.
- The release profile does **not** set `panic = "abort"`: destructors must run,
  because the terminal is restored by a destructor.
- RNG is a hand-written seeded xorshift64*. No system entropy anywhere. Every
  run takes `--seed N` and is bit-for-bit reproducible.
- **Never requires a TTY.** `--headless`, a non-tty stdin, or `--replay` never
  render and never read keys; they print plain text and exit 0. This is how the
  repo gate and `pengyos-qemu-verify` run the game.
- Fixed timestep, no busy-wait (`recv_timeout` sleeps). `q` and `Ctrl-C` quit.
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
11. **End-to-end, no pty**: `--help` lists every flag and key; `--headless` and
    a piped stdin exit 0 with plain text; the committed fixture replays to
    `max=512 score=7976` with a pinned hash, twice byte-identically;
    `record → replay` round-trips; `NO_COLOR` emits no escapes; `--mono` is
    ASCII-only; usage errors are exit 2 with one line; a missing replay file is
    exit 66; the best score is written under the data dir and nowhere else;
    `--no-save` writes nothing.

**GitHub-ready contract** (asserted by `tests/github_ready.rs`): `README.md`,
`spec.md`, `LICENSE` (MIT, "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`,
`verify.toml`, `Cargo.toml` with an **empty** `[dependencies]`, a **pure**
`src/lib.rs` (no fs/process/thread/time/io), the launcher `.desktop`, and the
recipe pair.

## Acceptance criteria

- `cargo test` green in under 5 s, zero warnings on a clean build.
- `pengy-merge --headless --seed 7 --ticks 4000` exits 0 and prints plain-text
  state; `pengy-merge --replay fixtures/win.replay --seed 7` exits 0, reports
  `max=512`, and is byte-identical run twice.
- Playable in 80x24; an 18x6 window renders a clipped, unwrapped game.
- `q` and `Ctrl-C` leave the terminal exactly as they found it; a panic does too.
- The crate builds with `[dependencies]` empty: `cargo build --release --offline`.
- The recipe skeleton names the crate, pins its version, declares MIT, and
  installs `/usr/bin/pengy-merge`.

## Non-goals

- Image wiring: `buildroot-external/Config.in`, `configs/layers/*.config`,
  `board/desktop/rootfs-overlay`, and the `tools/README.md` index entry. Ten
  games touching three shared files is a conflict generator; one integration
  pass does it.
- Sound, sprites, mouse, touch, gamepad, multiplayer, level editor.
- Online scores, accounts, telemetry, network of any kind.
- Windows below 25x10 being *good*; they must merely not crash.

## Feel

Cold and calm: the field is a box of right-aligned numbers, the floe that just
fused flashes for three ticks, and a mistake costs one keypress — `u` takes the
slide and the floe it dealt straight back.
