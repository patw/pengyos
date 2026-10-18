# pengy-solitaire — spec

**Frostbite Solitaire**: Klondike, draw three, unlimited redeals — the Windows
3.0 pack-in cart, rebuilt for the PengyOS terminal. One of the ten pack-in
games; the slot next to blocks, breakout, floe, icefield, invaders, lander,
merge, pong and rogue.

This file is the contract. The tests in `tests/` enforce it; the README is the
user-facing version of the same rules.

## Purpose

Every OS used to ship a couple of small, complete games that just worked.
PengyOS ships none. The pack-in games are terminal-first, Rust, zero-dependency,
and *good little games*: tuned rules, a score, a persistent record, a clean
quit, no crashes, no flicker.

Solitaire is the one everybody's grandparents played. It is also the pack-in
that most distros forget: it needs no fonts, no GPU, no X, and it proves the
terminal is a game platform.

## Constraints (non-negotiable)

1. **Rust 2021, `std` only, zero external crates.** No libc, no `rand`, no
   crossterm, no clap, no serde. `[dependencies]` stays empty forever;
   `tests/github_ready.rs` asserts it. This is what makes the Buildroot recipe
   an offline, reproducible `cargo build --release`.
2. **The library is pure.** `src/lib.rs` has no files, no keyboard, no screen,
   no clock, no threads, no process, no system entropy. The only randomness is
   the seeded xorshift64*. `tests/github_ready.rs` greps for the forbidden
   imports.
3. **Rendering is a pure function of state**: `frame(&Game) -> String`, one
   string per frame, the front-end writing `\x1b[H` plus the frame. No per-cell
   cursor addressing, so no flicker.
4. **Never require a TTY.** No TTY, `--headless`, or `--replay`: no rendering,
   no key reads, plain text, exit 0. The repo gate and `pengyos-qemu-verify`
   have no pty, so this is how the game is proven.
5. **Determinism.** Every run takes `--seed N`; the same seed and the same
   script produce the same state hash, byte for byte.
6. **The terminal is restored on every exit**, including a panic: a `Drop`
   guard plus a panic hook run `stty sane` and leave the alternate screen. The
   release profile deliberately does **not** set `panic = "abort"`, because
   destructors must run.
7. **Mutable state lives in one place**:
   `$XDG_DATA_HOME/pengyos/games/pengy-solitaire/` (default
   `~/.local/share/...`), under `$HOME`. Never `/usr`, never the repo, never a
   sibling of the binary. A write failure is ignored.
8. **No network, no services, no root, no cron, no daemon, no nested jobs, no
   process left running.**

## The game

### Deck and deal

- 52 cards, `id = suit * 13 + rank`. Suits 0 Spades, 1 Hearts, 2 Clubs,
  3 Diamonds; Hearts and Diamonds are red. Ranks 0 Ace .. 12 King.
- Deal: tableau column `i` (0-based) gets `i + 1` cards, its top card face up
  and the rest face down. The remaining 24 cards are the face-down stock. The
  waste and the foundations start empty.
- The shuffle is Fisher-Yates over the seeded xorshift64*, so a seed is a deal.

### Piles

A pile is a `Vec<Card>` whose **last** element is the top card.

| Pile | Cards | Playable |
|---|---|---|
| stock | face down | `d` deals from its top |
| waste | face up | its top card |
| foundation ×4 | face up, one suit each, ascending from the Ace | its top card |
| tableau ×7 | face up and down | the longest legal face-up run on top |

### Moves

- **Tableau → tableau**: the run being moved must be a legal run (every card
  face up, descending by exactly one rank, colours strictly alternating). It
  lands on a column whose top card is one rank higher and of the opposite
  colour. An empty column accepts a King **only**.
- **→ foundation**: exactly one card, of that foundation's suit, of the rank
  the foundation is waiting for (`foundation.len()`).
- **foundation → tableau**: allowed (it costs 15), and the normal tableau rules
  apply to the landing.
- **waste → tableau / foundation**: the waste's top card, under the same rules.
- The stock and the waste are never destinations.
- A face-down card is never playable. A tableau card flips the moment it becomes
  the top card, for +5, and a flip is not a move.
- An illegal drop is refused and the selection stays in hand; space on the
  source puts the cards back down.

### Draw and redeals

- `d` moves up to 3 cards from the stock top to the waste top, **preserving
  their order**.
- An empty stock recycles the whole waste back into the stock, reversed and face
  down, for −100. Passes are unlimited.
- Recycling needs a non-empty waste: with both piles empty, `d` does nothing and
  costs nothing.

### Auto-collect (`f`)

Collects to the foundations, repeatedly, only cards that are **safe**:

1. the move is legal (the card is the next rank up on its own foundation);
2. an Ace is always safe; a card of rank `r > 0` is safe only when **both**
   foundations of the opposite colour already hold rank `r-1`;
3. a card taken off the tableau additionally needs the card beneath it to be of
   the opposite colour and one rank higher, or to be nothing — auto-collect
   never breaks a build.

Applied repeatedly until nothing more is safe, so **a second `f` changes
nothing** (idempotent). One `f` is one undo level.

### Undo

- Every state-changing action pushes a snapshot; `f` pushes one for the whole
  batch; an action that changes nothing pushes none.
- Depth is capped at 50 (the oldest level is dropped).
- `u` restores the exact previous position: piles, score, moves, redeals, flips,
  cursor, selection. The state hash after an undo equals the hash before the
  move. The elapsed timer is not rewound — it is the wall clock, not the
  position, and it is not in the hash.

### Win

All 52 cards on the foundations. There is no lose state. A won game is frozen:
`d` and `f` do nothing, `n` deals a new game, `q` quits.

### Score

| Event | Points |
|---|---|
| waste → tableau | +5 |
| waste → foundation | +10 |
| tableau → foundation | +10 |
| flip | +5 |
| foundation → tableau | −15 |
| recycle | −100 |

Floored at 0. Moves and elapsed time are tracked alongside.

## Keys

| Key | Action |
|---|---|
| `h` / `Left` | Cursor left, clamped (no wrap). |
| `l` / `Right` | Cursor right, clamped. |
| `k` / `Up` | Cursor up: from the tableau to the top row. |
| `j` / `Down` | Cursor down: from the top row to the nearest tableau column. |
| `space` | Take the run at the cursor; then drop it at the cursor. On the source, put it back. |
| `d` | Draw three, or recycle. |
| `f` | Auto-collect the safe cards. |
| `u` | Undo one level. |
| `n` | New deal. |
| `q` | Quit, restoring the terminal. `Ctrl-C` quits too. |
| anything else | Ignored. `Escape` is not a quit key (terminals send the arrows as `ESC [ A`). |

Cursor moves apply at once; everything that changes the board is queued and
applied on the next tick, so the game advances on a fixed timestep.

## States

| State | Meaning |
|---|---|
| `Play` | The game is live. |
| `Win` | All 52 cards are on the foundations; the board is frozen and the banner is drawn. |

Plus, in the front-end: `quit` (the loop asked to exit) and, for the harness,
`stopped_at` (the tick the session ended on, or `eof`).

## CLI

```
pengy-solitaire [FLAG]...
  --seed N          the deal (default 1)
  --headless        no TTY, no render, no keys: demo player, plain text, exit 0
  --replay FILE     one line of keys per tick; blank = none; '#' = comment; '_' = space
  --state FILE      start from a saved position instead of dealing
  --save FILE       write the final position (the same format --state reads)
  --record FILE     with --headless: write the demo player's script
  --ticks N         ticks for the headless path (default 4000)
  --autopilot       interactive: let the demo player play
  --mono            ASCII-only glyphs, no colour
  --no-color        no ANSI colour (NO_COLOR in the environment implies this)
  --print-frame     with --headless/--replay: also print the final frame
  --width N / --height N   the window for the headless frame (default 80x24)
  --data-dir DIR    where the record lives (default the XDG path)
  --no-save         do not read or write the record
  --help / --version
```

Exit codes: **0** success, **2** usage error, **66** unreadable replay or state
file.

## Rendering

- Reference window **80x24**. Cards are 5x3 boxes in a cascade with a 1-row
  step; the top row holds the stock, the waste and the four foundations, the
  grid holds the seven tableau columns at pitch 7, and the score panel starts at
  column 52.
- Degrade order, in a fixed sequence: **border** (below 78 columns or 24 rows),
  **score panel** (below 66 columns), **title** (below 22 rows), **key legend**
  (below 20 rows). The cards are never dropped; a pile that outgrows the grid
  compresses its cascade and counts the overflow with a `+n` marker.
- Colour is opt-in: red for the red suits, dim for the decoration. `--mono` and
  `NO_COLOR` produce a frame that is pure ASCII.
- One write per frame: `\x1b[H` + the frame, on the alternate screen
  (`?1049h` / `?1049l`), cursor hidden.

## Persistence

`$XDG_DATA_HOME/pengyos/games/pengy-solitaire/best.txt`, key=value:
`score`, `moves`, `ms` (the fastest win), `games`, `wins`, `seed`. Tier-1
mutable, under `$HOME`. Written once per game, on exit. Every failure to write
is ignored.

## Acceptance criteria

Each line is a test in `tests/`.

| # | Criterion | Test |
|---|---|---|
| 1 | A 52-card deck, no duplicates, standard ranks/suits/colours | `the_deck_is_52_distinct_cards` |
| 2 | The seeded shuffle is a permutation and is deterministic; neighbouring seeds differ | `the_seeded_shuffle_is_a_permutation_and_is_deterministic`, `the_same_seed_deals_the_same_game` |
| 3 | The deal is the 1..7 Klondike layout, 24 in the stock, every card once | `the_deal_is_the_seven_pile_klondike_layout` |
| 4 | Tableau legality: descending + alternating colour accepted; same colour, wrong rank, face-down, non-destinations rejected | `a_tableau_build_accepts_descending_alternating_colour` |
| 5 | Only a King opens an empty column | `only_a_king_opens_an_empty_column` |
| 6 | Foundation: one card, right suit, next rank; aces only on empty; never two at once | `a_foundation_takes_one_card_of_its_suit_ascending` |
| 7 | A run is a legal unit only when ordered and alternating; the movable run is the longest legal suffix | `a_run_is_a_legal_unit_only_when_it_is_ordered_and_alternating`, `the_movable_run_is_the_longest_legal_suffix_of_a_column` |
| 8 | A multi-card run moves as a unit and keeps its order | `a_multi_card_run_moves_as_a_unit_and_keeps_its_order` |
| 9 | An illegal drop is refused, the selection survives, and space on the source puts it back | `an_illegal_drop_is_refused_and_the_cards_stay_in_hand` |
| 10 | Draw three preserves order; the last draw takes what is left | `drawing_deals_three_cards_in_order`, `the_last_draw_takes_what_is_left_and_an_empty_stock_recycles_the_waste` |
| 11 | Recycle only when the stock is empty **and** the waste is not; costs 100; unlimited passes | `recycling_needs_a_waste_and_costs_a_hundred`, `passes_are_unlimited` |
| 12 | Auto-flip on uncover, +5, not a move | `a_card_flips_the_moment_it_is_uncovered` |
| 13 | Auto-collect takes only safe cards, never breaks a build, and is idempotent | `auto_collect_only_takes_safe_cards_and_is_idempotent`, `auto_collect_never_breaks_a_build`, `auto_collect_refuses_a_card_that_could_strand_a_build` |
| 14 | The score table is the standard Klondike one, floored at 0 | `the_score_table_is_the_standard_klondike_one` |
| 15 | Undo restores an identical state hash, level by level, capped at 50; a no-op adds no level | `undo_restores_the_exact_previous_state`, `undo_restores_the_score_and_the_move_count_too`, `the_undo_stack_is_capped_at_fifty_levels`, `a_keypress_that_changes_nothing_adds_no_undo_level` |
| 16 | Cursor walks and clamps; a tick moves the clock and applies the queued key; `q` is the only quit key; `n` resets the deal and keeps the record | `the_cursor_walks_the_table_and_clamps`, `a_tick_moves_the_clock_and_applies_the_queued_key`, `q_is_the_only_quit_key_and_the_rest_are_ignored`, `n_deals_a_new_game_and_keeps_the_record` |
| 17 | Win at 52, and a won game is frozen | `the_game_is_won_when_the_foundations_hold_all_fifty_two_cards` |
| 18 | The frame is deterministic, 80x24, and degrades in the fixed order without dropping the cards; mono is ASCII; colour is opt-in | `the_frame_is_deterministic_and_fits_the_reference_window`, `a_smaller_window_drops_the_decoration_not_the_cards`, `mono_mode_is_pure_ascii_and_colour_is_opt_in`, `a_deep_pile_compresses_its_cascade_instead_of_running_off_screen` |
| 19 | A scripted session is deterministic: same seed + same script ⇒ same state hash | `a_scripted_session_is_deterministic` |
| 20 | A committed fixture deal is one move from winning, and a committed replay wins it | `the_near_win_fixture_is_a_legal_position_one_move_from_winning`, `the_win_fixture_wins_the_near_win_deal`, `the_fixtures_are_a_legal_near_win_deal_and_a_script_that_wins_it` |
| 21 | The game runs with no pty and exits 0; the flags, exit codes and the record file behave | `tests/cli.rs` |
| 22 | The tool-directory contract: docs, license, changelog, verifier, empty dependencies, pure library, launcher, recipe, no image wiring | `tests/github_ready.rs` |

## Non-goals

- Drawing mode, mouse, animation, sound, theming, network play.
- A solvable-deal generator, a solver, a hint engine, a difficulty ladder.
- Draw-one / draw-two variants, Freecell, Spider, Pyramid, TriPeaks — separate
  games, separate tools.
- Image wiring (`buildroot-external/Config.in`, the layer `.config`s, the
  rootfs overlay): one integration pass for all ten games, not ten edits.
- Saving a game in progress for later (the format exists; wiring a menu for it
  does not).
