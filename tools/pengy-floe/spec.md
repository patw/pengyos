# pengy-floe — Flappy Pengy (flappy-bird style, one-button reflex)

The contract for the PengyOS pack-in flapper. Implementation and tests are the
authority on detail; this file is the authority on *intent*.

## Summary

A penguin flaps through gaps in drifting ice pillars. One button, cruel physics,
a score, a persisted best, medals, and a two-second restart. Terminal-first,
80x24, Rust 2021, `std` only, zero external crates.

## Purpose

PengyOS ships a desktop and a terminal and no games. Every OS that had a
personality shipped a couple of small, complete ones (Windows Solitaire and
Minesweeper, the Atari 2600 cart, the BSD games). This is one of ten: a tiny,
finished game that runs anywhere the OS runs, including over ssh and on Core.

## Deliverable

`tools/pengy-floe/`: a zero-dependency Rust crate (`src/lib.rs` pure rules +
`src/main.rs` terminal front-end), its docs, its suite, a committed replay
fixture, a `.desktop` launcher, and a Buildroot recipe skeleton at
`buildroot-external/package/pengy-floe/`.

## Interface

### Playfield and physics

| Quantity | Value |
|---|---|
| Playfield | 40 columns x 20 rows |
| Tick | 80 ms, fixed |
| Penguin | collision box 2 columns wide x 1 row tall, at column 8; sprite 4 columns wide |
| Gravity | `vy += 1` per tick, clamped to `vy <= 3` |
| Flap | sets `vy = -2`; never stacks |
| Integration | `y += vy`, clamped to rows `0..=19`; touching row 0 (ceiling) or row 19 (floor) is death |
| Pillars | 2 columns wide, spawned every 12 columns at column 40 |
| Carrying speed | 1 column per 2 ticks at score 0, ramping linearly to 1 column per tick at score 30, then flat |
| Gap | 8 rows at score 0, shrinking linearly to 5 at score 25, never below 5 |
| Gap placement | `gap_top` drawn from `2 ..= ROWS - 2 - gap_height`, so **at least 2 rows of clearance above and below, always** |
| Scoring | +1 per pillar whose trailing edge passes the penguin, exactly once per pillar |
| Collision | AABB against pillar cells only; the gap is the hole |
| Medals | 10 ice, 25 bronze, 50 silver, 100 gold |

Speed is carried in integer units of 1/60 column per tick (30 at score 0, 60 at
score 30) so the world moves in whole columns and the run stays reproducible.

### States

`Playing` → `Paused` (toggle) → `Dead` (ceiling, floor, or pillar). A dead run is
frozen: ticking changes nothing, and the frame shows score, best, cause and medal.

### Keys

| Key | Effect |
|---|---|
| `space`, `up`, `w`, `k` | Flap. On a dead run: restart instantly. |
| `p` | Pause / resume. A dead run never unpauses. |
| `r` | Restart: a new run in the same seed family (`seed + 1`), best score kept. |
| `q`, `Escape`, `Ctrl-C` | Quit, restoring the terminal. |
| anything else | Ignored. |

### CLI

`--seed N` (default 1), `--headless`, `--replay FILE`, `--record FILE`,
`--ticks N`, `--autopilot`, `--mono`, `--no-color`, `--print-frame`,
`--width N`, `--height N`, `--data-dir DIR`, `--no-save`, `--help`, `--version`.
`NO_COLOR` is honoured. Exit codes: **0** success, **2** usage error (one
diagnostic line), **66** unreadable replay file.

### Replay format

One line per tick. A line containing a character is that key; a blank line is no
input; a line starting with `#` is a comment and is not a tick. The harness stops
at the death tick (a dead run is frozen) or at EOF, then prints the final state as
`key=value` lines and exits 0:

```
state=dead seed=2 ticks=1039 score=70 best=70 y=10 vy=0 flaps=373
hash=5270b19a8f034ede cause=pillar medal=silver replay=… quit=0 stopped_at=eof
```

`hash=` is FNV-1a over the canonical serialization of the whole state: two runs
with the same seed and the same script have the same hash.

### Persistence

`$XDG_DATA_HOME/pengyos/games/pengy-floe/best.txt` (default
`~/.local/share/…`), holding `score=`, `ticks=`, `seed=`. Tier-1 mutable, under
`$HOME`, never `/usr`, never the repo. A failure to write is ignored: a read-only
`$HOME` must not cost the player a game.

### Rendering

`fn render(&Game, RenderOpts) -> String` — a pure function of state plus the
window size and the glyph policy. One `home` + one frame write per tick; no
per-cell cursor addressing. Colour via SGR; `NO_COLOR`/`--mono` emit **no escape
bytes at all**. Alternate screen `?1049h`/`?1049l`, cursor hidden while playing.

Degrade rules: `width >= 42` draws the box border, narrower drops it;
`height >= 24` adds the keys and status lines, `>= 22` the bottom border, `>= 21`
the header, and below 21 the playfield is clipped from the bottom. Every row is
clipped to the window width. The reference window is 80x24 and the full frame is
exactly 24 rows.

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
- RNG is a hand-written seeded xorshift64*. No system entropy anywhere. Every run
  takes `--seed N` and is bit-for-bit reproducible.
- **Never requires a TTY.** `--headless`, a non-tty stdin, or `--replay` never
  render and never read keys; they print plain text and exit 0. This is how the
  repo gate and `pengyos-qemu-verify` run the game.
- Fixed timestep, no busy-wait (`recv_timeout` sleeps). `q` and `Ctrl-C` quit.
- No network, no services, no root, no cron, no nested jobs, no process left
  running.

## Verification (tests are first-class)

`cargo test` green, fast (< 5 s). The rules list, each one tested:

1. **Gravity integration over N ticks** — exact `vy`/`y` sequence from a cold
   start, including the clamp at 3 and the floor death it produces.
2. **Flap clamps `vy`** — five flaps in one tick are one flap; `vy` is set, never
   summed; flapping while paused/dead does nothing.
3. **A generated gap is always in bounds** with ≥ 2 rows of clearance above and
   below and ≥ 5 rows tall — swept over 400 seeds.
4. **Score increments exactly once per pillar** — one point per pillar past the
   penguin, never more than one per tick, a scored pillar never un-scores.
5. **Ceiling / floor / pillar collisions at the boundary cells** — the row above
   the gap kills, the gap's own top row passes, the pillar covering the hitbox's
   right column kills, the one a column further right does not.
6. **Identical seed + identical script ⇒ identical state** (canonical string,
   hash, and frame), and a different seed/script ⇒ a different one.
7. **A scripted input sequence through the library lands on the golden end
   state** (hash, ticks, flaps, score, seed after the restart).
8. Speed ramp (1 column per 2 ticks → 1 per tick), spawn spacing (12 columns),
   pause freezes the world, restart keeps the best and advances the seed, medals
   at 10/25/50/100, frame purity and the degrade rules.
9. **End-to-end, no pty**: `--help` lists every flag and key; `--headless` and a
   piped stdin exit 0 with plain text; the committed fixture replays to
   `score=70 medal=silver` with a pinned hash; `record → replay` round-trips;
   `NO_COLOR` emits no escapes; `--mono` is ASCII-only; usage errors are exit 2
   with one line; a missing replay file is exit 66; the high score is written
   under the data dir and nowhere else; `--no-save` writes nothing.

**GitHub-ready contract** (asserted by `tests/github_ready.rs`): `README.md`,
`spec.md`, `LICENSE` (MIT, "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`,
`verify.toml`, `Cargo.toml` with an **empty** `[dependencies]`, the launcher
`.desktop`, and the recipe pair.

## Acceptance criteria

- `cargo test` green in under 5 s, zero warnings on a clean build.
- `pengy-floe --headless --seed 2 --ticks 4000` exits 0 and prints plain-text
  state; `pengy-floe --replay fixtures/win.replay --seed 2` exits 0 and reports
  a medal-winning score, twice in a row, byte-identical.
- Playable in 80x24; a 30x12 window renders a clipped, unwrapped game.
- `q` and `Ctrl-C` leave the terminal exactly as they found it; a panic does too.
- The crate builds with `[dependencies]` empty: `cargo build --release --offline`.
- The recipe skeleton names the crate, pins its version, declares MIT, and
  installs `/usr/bin/pengy-floe`.

## Non-goals

- Image wiring: `buildroot-external/Config.in`, `configs/layers/*.config`,
  `board/desktop/rootfs-overlay`, and the `tools/README.md` index entry. Ten
  games touching three shared files is a conflict generator; one integration
  pass does it.
- Sound, sprites, mouse, touch, gamepad, multiplayer, level editor.
- Online scores, accounts, telemetry, network of any kind.
- Window sizes below 40x20 being *good*; they must merely not crash.

## Feel

Tight and slightly forgiving: the hitbox is narrower than the sprite, the gap
never becomes impossible, and death costs two seconds — the death screen paints
immediately, `space` restarts on the spot, and the run is live again on the next
tick.
