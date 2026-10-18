# Thaw Invaders — the contract

The contract for `tools/pengy-invaders`. The README is the user's entry point;
this file is what the code must do, and what the tests prove.

## The game

Something is melting the ice from above. You have one laser. The 1978 arcade
original (Space Invaders) on a terminal field, in the PengyOS pack-in games slot
next to pengy-pong, floe, merge, icefield, breakout, lander, blocks, solitaire
and rogue.

## Field and geometry

| Name | Value |
|------|-------|
| Field | 60 columns x 24 rows |
| Ship | row 23, 3 columns wide (centre `x`, covers `x-1 .. x+1`), `x` in 1..58 |
| Formation | 11 columns x 5 rows, alien 2 cells wide, column pitch 4, width 42, starts centred at column 9, top row 2 |
| Walls | the formation reverses when its outermost cell reaches column 1 (left) or 58 (right) |
| Shields | 3 blocks of 8x4 cells, left columns 8, 26, 44, top row 18 |
| Reference window | 80x24 playable; 80x28 is the full chrome |

## States

`Ready` (the title screen: the field is drawn, the formation marches, nothing
can hurt you) → `Play` → `Pause` (a freeze, not a mode) → `Over`.

`Over` names its cause: `destroyed` (the three lives are gone) or `breach` (the
formation reached the ship's row). A finished run is frozen: only `r` (restart)
and `q` (quit) do anything.

## Rules

1. **Tempo.** A fixed timestep of 40 ms per tick. The loop sleeps; it never
   busy-waits.
2. **The formation steps one column every N ticks**, where
   `N = 8 * alive / 55 / wave` in integer arithmetic, floored at 1. A full
   formation on wave 1 steps every 8 ticks; 11 aliens step every tick.
3. **Reversal.** Touching a wall reverses the direction *and* descends the
   formation one row. The clamps are exact: the outermost cell lands on column
   58 (right) or column 1 (left) and never past them. One descent per reversal.
4. **The ship** moves 2 columns per keypress, clamped to 1..58.
5. **The laser.** One laser in flight, 2 ticks of cooldown after a shot. It
   travels 2 rows per tick upward, checking every cell it crosses. A hit kills
   the **lowest** living alien in that formation column. A laser that hits an
   intact shield cell erodes it and dies; a laser that flies into a hole in a
   shield passes through. A laser that hits a bomb destroys it. A laser that
   leaves the top of the field is gone.
6. **Bombs.** Each living alien rolls once per tick, in row-major order, at
   probability `0.002 + 0.0005 * (wave - 1)`. At most 3 bombs on screen. A bomb
   falls 1 row per tick. A bomb on the ship's row inside the ship's columns
   takes a life, clears the bombs, clears the laser and recentres the ship. A
   bomb that hits an intact shield cell erodes it and is spent. A bomb that
   leaves the bottom of the field is gone.
7. **Shields** are 96 cells; any hit erodes the cell it lands on, and an alien
   that touches a shield grinds away every cell it touches.
8. **Lives.** 3. The third hit ends the run.
9. **Waves.** A cleared formation scores `100 * wave`, then the formation
   refills one row lower, centred, marching right, with the faster step period,
   and the field is cleared of bombs and the laser.
10. **Score.** An alien scores by its formation row: bottom 10, then 20, 30,
    40, top 50. A cleared wave scores `100 * wave`.
11. **The breach.** When the formation's bottom row reaches row 23 the run
    ends, cause `breach`.
12. **Order inside a tick** is fixed and is part of the contract: cooldown,
    laser, formation, bombs, wave.

## Keys

| Key | Meaning |
|-----|---------|
| `a`, `A`, `h` | Move left 2 columns (`h` is how the front-end delivers the left arrow) |
| `d`, `D`, `l` | Move right 2 columns (`l` is the right arrow) |
| `space` | Fire |
| `p`, `P` | Pause / resume |
| `r`, `R` | Restart: the next seed in the seed family, best kept |
| `q`, `Q` | Quit. Ctrl-C quits too |
| other | Ignored. A bare Escape is not a quit key, because terminals send the arrows as `ESC [ C` |

## Randomness and determinism

The only source of randomness is a hand-written seeded xorshift64* whose state
is part of the game state. Every run takes `--seed N`. A run is a pure function
of `(seed, script)`: same seed + same input script ⇒ the same end state, bit for
bit, and the end state prints as a 16-hex-digit hash.

## Rendering

`render(&Game, RenderOpts) -> String` (and `frame(&Game)` at the reference
window) is a **pure function of state**: same state, same bytes. The front-end
writes it as one `home` plus one write per frame — no per-cell cursor
addressing, so there is nothing to flicker.

Degrade rules, in order, as the window shrinks:

* `height >= 28` and `width >= 62`: title line, border, 24 field rows, legend.
* `height >= 26`: title line, 24 field rows, legend (no border).
* `height >= 25`: 24 field rows, legend; the HUD rides on the field's top row.
* `height == 24`: the field is the whole screen; the HUD rides on the field's
  top row (row 0, which is always empty because the formation starts on row 2).
* `height < 24`: the field is clipped from the bottom, and the HUD stays.

The 60-column field is never dropped. Every frame is exactly `height` rows of
exactly `width` visible columns; colour adds no visible characters.

`--mono` draws with pure ASCII (no box-drawing, no blocks, no hearts).
`NO_COLOR` in the environment suppresses every escape without a flag.

## Persistence

The best score, the wave it reached and the seed it happened on live in
`$XDG_DATA_HOME/pengyos/games/pengy-invaders/best.txt`, defaulting to
`~/.local/share/pengyos/games/pengy-invaders/best.txt` — Tier-1 mutable state
under `$HOME`. The game writes nothing anywhere else, ever: not `/usr`, not the
repo, not next to the binary. `--data-dir DIR` relocates it; `--no-save` skips
reading and writing entirely. Every persistence failure is ignored: a read-only
`$HOME` must never cost the player a game.

## CLI

`--seed N`, `--headless`, `--replay FILE`, `--record FILE`, `--ticks N`,
`--wave N`, `--autopilot`, `--mono`, `--no-color`, `--print-frame`,
`--width N`, `--height N`, `--data-dir DIR`, `--no-save`, `--help`, `--version`.

* `--help` lists every flag and every key, and quotes the tuning constants from
  the library so the text cannot drift from the rules.
* Exit codes: `0` success (including every headless/replay run), `2` usage
  error, `66` an unreadable replay file.

### The headless path (the no-pty contract)

If stdin is not a terminal, or `--headless` is passed: never touch the
terminal, never read keys, run the built-in autopilot for `--ticks` ticks,
print the plain-text state (`key=value` lines, then the field as ASCII rows)
and exit 0. The repo gate and `pengyos-qemu-verify` have no pty; this is how
the game is proven to work.

### The replay harness

`--replay FILE` plays a whole session headlessly: **one line of input per
tick**, a blank line is no input, a `#` line is a comment and is not a tick.
`f` is the fire key (the space character is the fire key on the keyboard, so a
line of spaces would be indistinguishable from "no input"; the file format
spells fire `f` and ignores spaces). The session stops at the game-over tick
or at EOF. It prints the final state and score as `key=value` lines and exits
0. `--record FILE` writes the autopilot's input in the same format, which is
how the committed fixture was made.

## Constraints (non-negotiable)

* Rust 2021, **std only, zero external crates**: no libc, no rand, no
  crossterm, no clap, no serde. That is what keeps the Buildroot recipe an
  offline, reproducible `cargo build --release`.
* Raw keyboard via `stty raw -echo` through `std::process::Command`; restored
  with `stty sane` in a `Drop` guard **and** a panic hook. Stdin is read on a
  worker thread and delivered over `std::sync::mpsc`. If `stty` is missing, or
  stdin is not a tty, the game degrades and keeps working.
* Drawing is ANSI escapes on stdout: the alternate screen (`?1049h` /
  `?1049l`), hide/show cursor, one `home` plus one repaint per frame.
* Never require a TTY (see the headless path above).
* Fixed timestep, no busy-wait. `q` and Ctrl-C quit cleanly and restore the
  terminal.
* No network, no services, no root, no cron, no nested jobs, no process left
  running.

## Acceptance criteria

Each line is checked by a test in `tests/`.

| # | Criterion | Test |
|---|----------|------|
| 1 | `cargo test` is green, in under 5 s, with no terminal | the suite itself |
| 2 | The field is 60x24, the formation 11x5, 2 cells wide, pitch 4, 3 shields 8x4, 3 lives | `the_field_is_60x24_with_the_ship_on_row_23`, `the_formation_is_11x5_two_cells_wide_spaced_four`, `shields_are_three_8x4_blocks_between_the_formation_and_the_ship` |
| 3 | The formation reverses and descends exactly at both edges | `the_formation_reverses_and_descends_exactly_at_the_right_edge`, `..._left_edge`, `every_reversal_descends_and_the_two_edges_are_symmetric` |
| 4 | N starts at 8, shrinks with kills, never below 1 | `the_step_period_starts_at_8_and_shrinks_with_kills`, `the_period_never_drops_below_one_as_the_formation_thins`, `the_formation_steps_one_column_every_period_ticks` |
| 5 | A laser kills the lowest alien in its column, scoring by row | `a_laser_kills_the_lowest_alien_in_its_column`, `alien_points_are_10_at_the_bottom_and_50_at_the_top` |
| 6 | One laser in flight, 2-tick cooldown, 2 rows per tick | `one_laser_is_in_flight_and_the_cooldown_is_two_ticks`, `the_laser_travels_two_rows_per_tick_upward` |
| 7 | Bomb/ship and laser/shield collisions, shield erosion, holes pass | `a_bomb_that_reaches_the_ship_takes_a_life_and_clears_the_field`, `a_bomb_that_hits_a_shield_erodes_that_cell`, `a_laser_that_hits_a_shield_erodes_that_cell_and_dies`, `a_laser_flies_through_a_hole_in_a_shield`, `an_alien_touching_a_shield_erodes_it` |
| 8 | Bomb rate rises by wave, at most 3 bombs, 1 row per tick | `bomb_probability_rises_with_the_wave`, `at_most_three_bombs_are_on_screen_and_they_fall_one_row_per_tick` |
| 9 | Three lives, and the third hit ends the run | `three_lives_and_the_third_hit_ends_the_run` |
| 10 | Wave advance: lower, faster, bonus, refilled | `clearing_a_wave_scores_the_bonus_and_starts_the_next_one_lower_and_faster`, `waves_keep_the_score_and_the_wave_number_across_several_clears` |
| 11 | The run ends when the formation reaches the ship's row | `the_formation_reaching_the_ship_row_ends_the_run`, `a_run_ends_the_moment_the_formation_touches_the_ship_row` |
| 12 | Determinism: same seed + same script = same state hash | `the_same_seed_and_the_same_script_produce_the_same_state`, `a_different_seed_produces_a_different_state`, `a_scripted_session_ends_in_the_same_state_every_time`, `a_replay_is_deterministic_and_the_seed_is_the_harnesss_choice` |
| 13 | Rendering is a pure function of state and fits the window | `the_frame_is_a_pure_function_of_state`, `the_frame_fits_80x24_and_80x28_exactly`, `a_small_window_drops_the_border_and_the_score_panel_not_the_field`, `mono_is_pure_ascii_and_color_is_only_escapes` |
| 14 | No TTY: headless prints plain text and exits 0 | `headless_needs_no_terminal_and_exits_zero`, `a_piped_stdin_takes_the_headless_path` |
| 15 | `--replay` on a committed fixture wins something, exit 0 | `the_committed_replay_fixture_wins_something`, `the_replay_fixture_is_a_playable_session` |
| 16 | `--help` lists every flag and key; `NO_COLOR` and `--mono` are honoured | `help_lists_every_flag_and_every_key`, `no_color_is_honoured_without_a_flag`, `print_frame_draws_the_field_and_mono_is_ascii_only` |
| 17 | The high score persists under `$XDG_DATA_HOME` and nowhere else | `the_high_score_persists_in_the_data_dir_and_only_there`, `the_record_never_goes_downhill`, `the_default_data_dir_is_the_xdg_games_path`, `no_save_touches_nothing` |
| 18 | Zero dependencies, std only, the launcher and the recipe skeleton exist | `the_crate_is_rust_2021_with_zero_dependencies`, `the_launcher_is_a_game_entry_that_runs_in_a_terminal`, `the_buildroot_recipe_builds_the_crate_from_this_repo_into_usr_bin`, `the_recipe_does_not_wire_the_image` |

## Out of scope

Image wiring — the `buildroot-external/Config.in` source line, the
`configs/layers/*.config` selections, and the `board/desktop/rootfs-overlay`
entries — is a separate integration job. This tool ships the recipe skeleton
only, and its tests assert that the skeleton does not touch those files.
