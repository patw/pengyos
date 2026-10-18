# pengy-lander — spec

The contract for Ice Lander, the PengyOS pack-in lander. This file is the
contract: what the game must do, what it must never do, and how each is proved.
The README is the user's entry point; this is the thing the tests are written
against.

## What it is

A terminal Lunar Lander. A ridge of ice broken by flat landing pads, a probe
with an engine and 100 units of fuel, and a landing that only counts when the
probe arrives slow, upright and on a pad. Waves: land, get a fresh ridge, fly
again; crash and the run is over with the reason on screen.

PengyOS pack-in game 7/10. Rust 2021, `std` only, **zero external crates**.

## Constraints (non-negotiable)

1. **Zero dependencies.** `[dependencies]` is empty and stays empty. No libc,
   no `rand`, no crossterm, no clap, no serde. That is what makes the Buildroot
   recipe an offline, reproducible `cargo build --release`.
2. **The library is pure.** `src/lib.rs` performs no I/O, reads no clock, spawns
   no thread, opens no file, and takes no entropy from the system. A run is a
   function of `(seed, script)`. `src/main.rs` owns the clock, the keyboard, the
   screen and the high-score file, and is the only file that touches them.
3. **Integer fixed point only.** Positions are metres x900 000 (`PSCALE`),
   velocities m/s x30 000 (`VSCALE`), and `PSCALE == VSCALE * 30` so the
   position update `x += vx` per 1/30 s tick is exact. Trigonometry is a
   120-entry `(sin, cos) x1000` table at 3-degree steps. No floating point, no
   `libm`, so a run is bit-for-bit reproducible across machines.
4. **Seeded RNG.** xorshift64*, seeded from `--seed N`. No system entropy.
5. **Keyboard.** `stty raw -echo` via `std::process::Command`; stdin is read on
   a worker thread and delivered to the loop over `std::sync::mpsc`. Restored
   with `stty sane` from a `Drop` guard *and* a panic hook. If `stty` is missing
   or stdin is not a tty, the game degrades instead of failing.
6. **Screen.** ANSI escapes on stdout: alternate screen (`?1049h` / `?1049l`),
   cursor hidden/shown, **one home (`\x1b[H`) plus one full repaint per frame**.
   No per-cell cursor addressing. Every row is padded to the window width, so
   no erase-to-end is needed and a frame contains no escape bytes at all when
   colour is off.
7. **Never require a TTY.** With no TTY, with `--headless`, or with `--replay`,
   the game does not render, does not read keys, prints plain text and exits 0.
   The repo gate and `pengyos-qemu-verify` have no pty: this is how the game is
   proven to work.
8. **Fixed timestep, no busy-wait.** 1/30 s per tick, driven by
   `recv_timeout` against a deadline. `q` and `Ctrl-C` quit cleanly and restore
   the terminal.
9. **Mutable state lives in one place.** `$XDG_DATA_HOME/pengyos/games/
   pengy-lander/` (default `~/.local/share/...`), under `$HOME` — Tier-1. Never
   `/usr`, never the repo, never anywhere else. A write failure is ignored.
10. **No network, no services, no root, no cron, no daemon, no nested jobs.**
11. **`--help` lists every flag and every key.** `NO_COLOR` is honoured.
    `--mono` draws ASCII-only glyphs.
12. **Playable at 80x24**, degrading gracefully in smaller windows.

## The rules

### Physics (per 1/30 s tick, metres and m/s)

| Quantity | Value | Fixed point per tick |
|---|---|---|
| Gravity, Moon (`--body moon`, default) | 1.62 m/s^2 | 1620 |
| Gravity, asteroid (`--body asteroid`) | 5.0 m/s^2 | 5000 |
| Thrust (while fuel remains) | 4.0 m/s^2 | 4000 along the nose |
| Rotation (`a` / `d`) | 90 deg/s | 3 deg |
| Fuel | 100 units | 1 unit per second of thrust (1 per tick, x30 scale) |

Integration order per tick: thrust along the heading, then gravity, then
position (`x += vx`, `y += vy`). The heading is always a multiple of 3 degrees,
which is why the sine table has exactly 120 entries.

Thrust is a **latch**: a `space` keypress arms the engine for the tick that
consumes it and the tick disarms it. A held key autorepeats, so a held space
keeps the engine burning; in a replay file, one line containing a space is
exactly one tick of thrust.

Fuel floors at 0 and the engine stops: no negative fuel, no thrust after the
tank is dry, and `cause=outoffuel` says so. Running out is not itself a crash —
the probe still has to arrive.

The world is 60 m wide and 24 m tall. The side walls reflect; the ceiling
clamps the upward velocity.

### Terrain

A 60 x 24 scaled side view of a ridge: one column per metre, the view at 3/4
scale of the 24-metre world.

- The ridge walks one metre per column (slope never exceeds 1 m/col), between
  2 and 12 metres.
- **2 to 4 pads**, each **6 to 12 columns wide**, flat, cut into the ridge,
  never overlapping, left to right.
- Multipliers **1x, 2x and 5x**: exactly one 5x pad, one 2x pad (on a
  different pad), the rest 1x.
- The spawn is at 22 m, heading 0, vy 0, vx within +/-1.5 m/s, at a random
  column. Since the ridge never exceeds 12 m, **the spawn is always clear of
  terrain and above the highest ridge**.

### Landing

Touchdown is a **landing** only when all four hold:

- the feet are on a pad,
- `|vx| < 1.0 m/s`,
- `|vy| < 2.0 m/s`,
- `|tilt| < 5 degrees` (tilt is the angle from straight up, 0..180).

Every comparison is **strict**: exactly 1.0 m/s is a crash. The order of blame
is pad, then sideways speed, then descent, then tilt. A crash ends the run and
names the reason: `offpad`, `toofastsideways`, `toofastdescent`, `tootilted`,
`outoffuel`.

### Score

```
landing = 100 * pad_multiplier + 10 * fuel_units_left + 50 * waves_already_banked
run score = sum of the landings of its waves
```

The wave bonus is the fresh-terrain bonus: wave 1 pays none, wave 2 pays 50,
wave 3 pays 100, and so on. A landing is banked immediately; `n` flies the next
wave on new terrain with a full tank; `r` restarts the run on the next seed of
the seed family and keeps the persisted record.

### Keys

| Key | Effect |
|---|---|
| `space` | Thrust for this tick (latch; autorepeat makes a held key a burn). |
| `a` / `d` | Rotate -/+ 3 degrees (90 deg/s). Arrow keys are delivered as these. |
| `n` | New terrain: the next wave. From a pad it banks the landing; from the air it abandons the wave. |
| `r` | Restart the run on seed+1. The high score is kept. |
| `q` | Quit. `Ctrl-C` quits. Both restore the terminal. |
| anything else | Ignored, including `Escape` (terminals deliver arrows as `ESC [ A`). |

### HUD

Altitude above the ground under the probe, vx, vy (m/s, 2 dp, signed), fuel,
tilt in degrees, the thrust flame, wave, state, score and best. The field draws
the ridge, the pads (a distinct glyph), the probe (a glyph that shows its
heading) and the flame under the nose.

## CLI

```
pengy-lander [FLAG]...
  --seed N              seed (default 1)
  --body moon|asteroid  gravity (default moon)
  --headless            no TTY, no rendering, no keys: autopilot, plain text, exit 0
  --replay FILE         one line of input per tick; blank = no input; '#' = comment
  --record FILE         with --headless: write the autopilot's script in replay format
  --ticks N             ticks for the headless path (default 3000)
  --autopilot           interactive demo: the autopilot flies
  --mono                ASCII-only glyphs, no colour
  --no-color            no ANSI colour (NO_COLOR in the environment implies it)
  --print-frame         with --headless/--replay: print the final frame
  --width N / --height N window for the headless frame (default 80x24)
  --data-dir DIR        high-score directory (default $XDG_DATA_HOME/pengyos/games/pengy-lander)
  --no-save             read and write nothing
  --help / --version
```

Exit codes: **0** success, **2** usage error, **66** unreadable replay file.

`--replay` prints the final state as `key=value` lines on exit 0: `state`,
`seed`, `body`, `ticks`, `wave`, `waves_landed`, `score`, `last_points`,
`last_mult`, `fuel`, `x`, `y`, `alt`, `vx`, `vy`, `tilt`, `heading`, `pads`,
`best_score`, `best_waves`, `best_seed`, `hash`, and `cause` when there is one.
Plus `replay=`, `quit=` and `stopped_at=`. `hash` is FNV-1a 64 over the
canonical state, terrain, pads and generator included.

## Rendering and window degradation

Designed for 80x24: title, a 60-column bordered field, an 18-column instrument
panel beside it, the score strip, the key legend. In a smaller window, in
order: the panel moves above the field, then the title goes, then the border,
then the legend, then the field is clipped **from the top** (the ground is
never clipped). A frame is always exactly `height` rows of exactly `width`
visible cells.

## Acceptance criteria

Each line is proved by a named test.

| # | Criterion | Test |
|---|---|---|
| 1 | `cargo test` is green, in under 5 s | the suite itself |
| 2 | Gravity and thrust integrate exactly over N steps, at both bodies, in both axes | `gravity_integrates_exactly_over_n_steps`, `thrust_adds_exactly_along_the_heading`, `thrust_at_45_degrees_uses_the_table_exactly` |
| 3 | Fuel floors at 0, the engine stops, and the burn is 1 unit per second | `fuel_floors_at_zero_and_the_engine_stops`, `fuel_burns_one_unit_per_second_of_thrust` |
| 4 | Landing classification at the boundaries: just inside and just past each limit, in the order of blame | `landing_is_accepted_just_inside_every_limit`, `landing_is_refused_at_and_past_every_limit`, `touchdown_off_a_pad_is_always_a_crash`, `the_order_of_blame_is_pad_then_sideways_then_down_then_tilt` |
| 5 | The terrain generator always yields 2-4 pads of width >= 6 with a clear spawn above the ridge, over 200 seeds | `terrain_always_has_pads_of_a_legal_width_over_200_seeds`, `the_spawn_is_always_clear_of_terrain_and_above_the_ridge` |
| 6 | Score is monotonic in fuel and in multiplier, and the wave bonus pays for fresh terrain | `score_is_monotonic_in_fuel_and_multiplier`, `a_landing_banks_and_a_new_wave_starts_fresh` |
| 7 | Determinism: same seed + same script => same state hash; one extra tick is a different hash | `the_same_seed_and_script_always_end_in_the_same_state`, `headless_is_bit_for_bit_reproducible`, `record_then_replay_round_trips` |
| 8 | A committed `.replay` fixture wins something | `the_committed_fixture_replays_to_a_landed_run` (seed 20, 6 waves, 8390 points, hash `22392a0ed4250697`) |
| 9 | No TTY: `--headless`, piped stdin and `--replay` never touch the terminal, print plain text, exit 0 | `headless_needs_no_terminal_and_exits_0`, `piped_stdin_takes_the_headless_path` |
| 10 | Exit codes 0 / 2 / 66 | `a_bad_flag_is_a_usage_error_and_exits_2`, `an_unreadable_replay_exits_66` |
| 11 | `--help` lists every flag and every key; `NO_COLOR` and `--mono` are honoured | `help_lists_every_flag_and_every_key`, `no_color_and_mono_are_honoured` |
| 12 | Mono output is pure ASCII; colour-off output contains no escape bytes | `mono_output_is_pure_ascii_and_colour_output_is_not` |
| 13 | A frame is exactly the window asked for, at 80x24 and smaller | `the_frame_is_the_window_it_was_asked_for`, `the_frame_is_the_window_that_was_asked_for` |
| 14 | A small window drops the panel, title, border and legend in order, and never the ground | `a_small_window_drops_the_panel_then_the_border_then_the_title` |
| 15 | The high score is written under `$XDG_DATA_HOME`/`$HOME` and nowhere else; `--no-save` writes nothing | `the_high_score_lives_in_the_data_dir_and_nowhere_else`, `the_default_data_dir_is_under_the_home_xdg_tree`, `no_save_touches_nothing` |
| 16 | The library is pure and the crate has zero dependencies | `the_crate_has_no_dependencies_and_the_library_is_pure` |
| 17 | The autopilot lands (the demo path is a real player, not a demo) | `the_autopilot_lands_on_the_pad_it_aims_at` |
| 18 | The asteroid's unwinnability is a stated property, not a surprise | `the_asteroid_is_a_suicide_run_by_construction` |
| 19 | The tool directory carries its docs, license, changelog, verifier, launcher and recipe; the image wiring is left to the integration pass | `tests/github_ready.rs` |

## Out of scope for this change

Image wiring: the `source` line in `buildroot-external/Config.in`, the layer
`.config` entries, and the menu. Ten games touching those three shared files is
a conflict generator, so it is one integration pass, not ten.
`tests/github_ready.rs` asserts that this change does not touch them.
