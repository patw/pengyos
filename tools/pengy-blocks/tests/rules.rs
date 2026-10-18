//! The rules of Glacier Blocks, asserted. Every test drives `src/lib.rs`
//! directly — no terminal, no clock, no files.
//!
//! Coverage follows `spec.md`: the 7-bag invariant over 70 draws, every
//! wall-kick case, line-clear collapse on hand-built boards (including a
//! 4-line clear and a non-adjacent pair), the scoring table, the level
//! thresholds, the lock delay and its reset, the spawn-overlap game over, the
//! key table, the renderer's geometry and degrade rules, and determinism.

use pengy_blocks::*;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn empty() -> [[bool; COLS]; BOARD_ROWS] {
    empty_board()
}

/// Fill `row` from `from` to `to` inclusive.
fn fill(board: &mut [[bool; COLS]; BOARD_ROWS], row: usize, from: usize, to: usize) {
    for c in from..=to {
        board[row][c] = true;
    }
}

/// A game whose current piece is `kind` in `rot` at `(row, col)`, on `board`.
fn placed(board: &[[bool; COLS]; BOARD_ROWS], kind: usize, rot: usize, row: i32, col: i32) -> Game {
    let mut g = Game::new(1);
    g.board = *board;
    g.kind = kind;
    g.rot = rot;
    g.row = row;
    g.col = col;
    g.plan = None;
    g
}

const I: usize = 0;
const J: usize = 1;
const L: usize = 2;
const O: usize = 3;
const S: usize = 4;
const T: usize = 5;
const Z: usize = 6;

// ---------------------------------------------------------------------------
// The 7-bag randomiser
// ---------------------------------------------------------------------------

#[test]
fn bag_gives_every_piece_ten_times_in_seventy_draws() {
    // The headline invariant: 70 draws of a 7-bag are exactly 10 of each.
    let mut bag = Bag::new(20_261_018);
    let mut counts = [0u32; 7];
    for _ in 0..70 {
        counts[bag.take()] += 1;
    }
    assert_eq!(counts, [10, 10, 10, 10, 10, 10, 10], "70 draws = 10 of each");
}

#[test]
fn bag_gives_every_piece_ten_times_in_seventy_draws_from_other_seeds() {
    for seed in [0u64, 1, 7, 42, 99, 1234, u64::MAX] {
        let mut bag = Bag::new(seed);
        let mut counts = [0u32; 7];
        for _ in 0..70 {
            counts[bag.take()] += 1;
        }
        assert_eq!(counts, [10; 7], "seed {seed}");
    }
}

#[test]
fn bag_every_window_of_seven_is_complete() {
    // Not just the totals: every consecutive group of seven draws is the full
    // set, which is the property a player actually feels.
    let mut bag = Bag::new(7);
    let mut draws = Vec::new();
    for _ in 0..70 {
        draws.push(bag.take());
    }
    for start in (0..70).step_by(7) {
        let mut window = draws[start..start + 7].to_vec();
        window.sort_unstable();
        assert_eq!(window, [0, 1, 2, 3, 4, 5, 6], "bag {}", start / 7 + 1);
    }
}

#[test]
fn bag_preview_is_the_next_three_without_consuming_them() {
    let mut bag = Bag::new(3);
    let peek = bag.peek(3);
    let taken: Vec<usize> = (0..3).map(|_| bag.take()).collect();
    assert_eq!(peek, taken, "the preview is what you get");
    assert_eq!(bag.peek(3).len(), 3, "and the preview keeps working");
}

#[test]
fn bag_preview_looks_into_the_next_bag() {
    let mut bag = Bag::new(11);
    // Drain the current bag, then the preview must come out of the next one.
    for _ in 0..7 {
        bag.take();
    }
    let peek = bag.peek(3);
    let taken: Vec<usize> = (0..3).map(|_| bag.take()).collect();
    assert_eq!(peek, taken);
    let mut sorted = peek.clone();
    sorted.sort_unstable();
    assert!(sorted.iter().all(|k| *k < 7), "and they are real pieces");
}

#[test]
fn bag_is_seeded_and_reproducible() {
    let mut a = Bag::new(5);
    let mut b = Bag::new(5);
    let mut c = Bag::new(6);
    for i in 0..20 {
        assert_eq!(a.take(), b.take(), "same seed, same bag (draw {i})");
    }
    let e: Vec<usize> = (0..20).map(|_| c.take()).collect();
    let mut d = Bag::new(5);
    let f: Vec<usize> = (0..20).map(|_| d.take()).collect();
    assert_ne!(e, f, "a different seed is a different bag");
}

// ---------------------------------------------------------------------------
// The seven shapes
// ---------------------------------------------------------------------------

#[test]
fn there_are_seven_pieces_of_four_cells() {
    for kind in 0..7 {
        for rot in 0..4 {
            let s = shape(kind, rot);
            assert_eq!(s.len(), 4, "{kind} {rot}");
            // No duplicate cell.
            for i in 0..4 {
                for j in (i + 1)..4 {
                    assert_ne!(s[i], s[j], "{kind} {rot} has a duplicate cell");
                }
            }
        }
    }
    assert_eq!(PIECE_NAMES, ["I", "J", "L", "O", "S", "T", "Z"]);
}

#[test]
fn the_o_does_not_change_when_it_rotates() {
    for rot in 0..4 {
        assert_eq!(shape(O, rot), shape(O, 0));
    }
}

#[test]
fn a_rotation_by_four_is_the_same_shape() {
    for kind in 0..7 {
        for rot in 0..4 {
            assert_eq!(shape(kind, rot), shape(kind, rot + 4));
            assert_eq!(shape(kind, rot), shape(kind, rot + 8));
        }
    }
}

#[test]
fn the_well_is_ten_by_twenty_with_two_hidden_rows() {
    assert_eq!(COLS, 10);
    assert_eq!(ROWS, 20);
    assert_eq!(HIDDEN_ROWS, 2);
    assert_eq!(BOARD_ROWS, 22);
}

// ---------------------------------------------------------------------------
// Fits, walls and the floor
// ---------------------------------------------------------------------------

#[test]
fn a_piece_fits_in_an_empty_well_and_falls_to_the_floor() {
    let b = empty();
    assert!(fits(&b, I, 0, 0, 3));
    assert_eq!(landing_row(&b, I, 0, 0, 3), 20, "a flat I lies in the floor row");
    assert_eq!(landing_row(&b, I, 1, 0, 3), 18);
    assert_eq!(landing_row(&b, O, 0, 0, 3), 19);
}

#[test]
fn walls_and_the_floor_are_solid() {
    let b = empty();
    assert!(!fits(&b, I, 0, 0, -1), "left wall");
    assert!(!fits(&b, I, 0, 0, 7), "right wall: a flat I needs four columns");
    assert!(!fits(&b, I, 0, 21, 3), "below the floor: a flat I needs row 22");
    assert!(fits(&b, I, 0, 19, 6), "flat I in the last row, cols 6..9");
    assert!(!fits(&b, I, 1, 19, 7), "a vertical I needs four rows");
}

#[test]
fn a_piece_does_not_fit_on_a_filled_cell() {
    let mut b = empty();
    fill(&mut b, 21, 0, 9);
    assert!(!fits(&b, O, 0, 19, 3), "the floor row is full");
    assert!(fits(&b, O, 0, 18, 3));
}

// ---------------------------------------------------------------------------
// Wall kicks — every case in the table
// ---------------------------------------------------------------------------

#[test]
fn kick_table_is_in_place_then_plus_one_minus_one_plus_two_minus_two_up() {
    assert_eq!(
        KICKS,
        [(0, 0), (1, 0), (-1, 0), (2, 0), (-2, 0), (0, -1)]
    );
}

#[test]
fn kick_in_place() {
    let g = placed(&empty(), T, 0, 5, 3);
    assert_eq!(g.kick(0, 1), (1, Some((0, 0))));
}

#[test]
fn kick_plus_one_column_against_the_left_wall() {
    // A vertical I against the left wall standing up: the flat rotation needs
    // one column of kick.
    let g = placed(&empty(), I, 3, 5, -1);
    assert_eq!(g.kick(3, 1), (0, Some((1, 0))));
}

#[test]
fn kick_minus_one_column_against_the_right_wall() {
    let g = placed(&empty(), I, 1, 5, 7);
    assert_eq!(g.kick(1, 1), (2, Some((-1, 0))));
}

#[test]
fn kick_plus_two_columns() {
    // A T above a shelf with a three-cell gap at the right: in place, +1, -1
    // and -2 all land on the shelf, and only +2 reaches the gap.
    let mut b = empty();
    fill(&mut b, 16, 1, 4);
    let g = placed(&b, T, 1, 15, 3);
    assert_eq!(g.kick(1, 1), (2, Some((2, 0))));
}

#[test]
fn kick_minus_two_columns() {
    // The mirror: a flat I against the right wall, with column 9 blocked on
    // the row it wants. Every kick to the right is out of bounds, -1 lands on
    // the block, and -2 is the only fit.
    let mut b = empty();
    fill(&mut b, 7, 9, 9);
    let g = placed(&b, I, 1, 5, 7);
    assert_eq!(g.kick(1, 1), (2, Some((-2, 0))));
}

#[test]
fn kick_up_one_row() {
    // A T over a shelf with five blocks: every column kick is blocked, and the
    // only place the rotation fits is one row up.
    let mut b = empty();
    fill(&mut b, 18, 2, 6);
    let g = placed(&b, T, 0, 16, 3);
    assert_eq!(g.kick(0, 1), (1, Some((0, -1))));
}

#[test]
fn a_rotation_with_no_kick_is_refused() {
    let mut b = empty();
    fill(&mut b, 17, 0, 9);
    fill(&mut b, 18, 0, 9);
    fill(&mut b, 16, 0, 9);
    let mut g = placed(&b, I, 0, 15, 0);
    g.rot = 0;
    g.row = 15;
    g.col = 0;
    // A flat I at row 16..16 is boxed in: rotating to vertical needs rows
    // 15..18, all of which are spoken for.
    let before = (g.rot, g.row, g.col);
    assert!(!g.key('x'));
    assert_eq!((g.rot, g.row, g.col), before, "a refused rotation moves nothing");
}

#[test]
fn kicks_are_applied_by_the_rotate_key() {
    let mut g = placed(&empty(), I, 3, 5, -1);
    g.key('x');
    assert_eq!((g.rot, g.col), (0, 0), "the +1 kick moved the anchor");
}

// ---------------------------------------------------------------------------
// Line clears and the collapse
// ---------------------------------------------------------------------------

#[test]
fn a_full_row_clears_and_the_well_collapses() {
    let mut b = empty();
    fill(&mut b, 21, 0, 8);
    b[21][9] = false;
    b[17][0] = true; // a marker above, to watch the collapse
    // Drop a vertical I into column 9: it completes row 21.
    let (after, cleared) = place(&b, I, 1, 18, 7);
    assert_eq!(cleared, 1);
    assert_eq!(after[21].iter().filter(|c| **c).count(), 1, "only the well cell is left in the cleared row");
    assert!(after[18][0], "the marker fell by one row");
    assert!(!after[17][0], "and left its old row");
    // The I's own cells fell with everything else: rows 19, 20, 21 of column 9.
    assert!(after[19][9] && after[20][9] && after[21][9]);
}

#[test]
fn four_lines_clear_at_once() {
    let mut b = empty();
    for r in 18..=21 {
        fill(&mut b, r, 0, 8);
    }
    let (after, cleared) = place(&b, I, 1, 18, 7);
    assert_eq!(cleared, 4, "a tetris");
    assert!(after.iter().all(|r| !r.iter().any(|c| *c)), "the well is empty");
}

#[test]
fn a_non_adjacent_pair_clears_and_collapses_exactly() {
    // Rows 18 and 20 are full except column 9; rows 19 and 21 are partial.
    // A vertical I in the well completes 18 and 20 — two rows that do not
    // touch — and everything above falls by two.
    let mut b = empty();
    fill(&mut b, 18, 0, 8);
    fill(&mut b, 20, 0, 8);
    fill(&mut b, 19, 0, 4);
    fill(&mut b, 21, 5, 5);
    b[17][0] = true;
    let (after, cleared) = place(&b, I, 1, 18, 7);
    assert_eq!(cleared, 2, "rows 18 and 20");
    // The kept rows, top to bottom: 0..17, then the old 19, then the old 21.
    // Rows above the cleared rows fall by the number of rows cleared below
    // them; rows between two clears fall by one; the row below the last clear
    // does not move.
    assert_eq!(
        after[20],
        [true, true, true, true, true, false, false, false, false, true],
        "the old row 19 fell one row (it sits between the two clears)"
    );
    assert_eq!(
        after[21],
        [false, false, false, false, false, true, false, false, false, true],
        "the old row 21 stays: nothing below it was cleared"
    );
    assert_eq!(
        after[19],
        [true, false, false, false, false, false, false, false, false, false],
        "the old row 17 fell two rows"
    );
    assert!(!after[18].iter().any(|c| *c), "the old row 16 was empty");
    assert!(!after[0].iter().any(|c| *c) && !after[1].iter().any(|c| *c));
}

#[test]
fn a_clear_drops_the_score_by_the_table_times_the_level() {
    for (n, points) in [(1, 100u64), (2, 300), (3, 500), (4, 800)] {
        // Build a board with `n` rows full except column 9, level 1.
        let mut g = Game::new(1);
        let top = 21 - (n - 1);
        for r in top..=21 {
            fill(&mut g.board, r, 0, 8);
        }
        g.kind = I;
        g.rot = 1;
        g.row = (top - 3) as i32;
        g.col = 7;
        g.plan = None;
        let before = g.score;
        let rows = landing_row(&g.board, g.kind, g.rot, g.row, g.col) - g.row;
        g.key(' ');
        assert_eq!(
            g.score - before,
            points * 1 + 2 * rows as u64,
            "{n} lines at level 1 (plus the {} rows of hard drop)",
            rows
        );
        assert_eq!(g.lines, n as u64);
        assert_eq!(g.clears[n], 1);
    }
}

#[test]
fn the_line_score_is_multiplied_by_the_level() {
    let mut g = Game::new(1);
    // 60 lines in the book, so the level is 7 and stays 7 after four more.
    g.lines = 60;
    g.level = level_for(g.lines);
    assert_eq!(g.level, 7);
    for r in 18..=21 {
        fill(&mut g.board, r, 0, 8);
    }
    g.kind = I;
    g.rot = 1;
    g.row = 15;
    g.col = 7;
    g.plan = None;
    let before = g.score;
    let rows = landing_row(&g.board, g.kind, g.rot, g.row, g.col) - g.row;
    g.key(' ');
    assert_eq!(
        g.score - before,
        800 * 7 + 2 * rows as u64,
        "a tetris at level 7, plus the hard drop"
    );
    assert_eq!(g.lines, 64);
    assert_eq!(g.level, 7, "and the level follows the lines");
}

// ---------------------------------------------------------------------------
// Levels and gravity
// ---------------------------------------------------------------------------

#[test]
fn the_gravity_ladder_is_the_tuned_table() {
    assert_eq!(
        GRAVITY_MS,
        [800, 720, 630, 550, 470, 380, 300, 220, 130, 100, 80]
    );
    for level in 1..=11 {
        assert_eq!(gravity_ms(level), GRAVITY_MS[level - 1]);
    }
    assert_eq!(gravity_ms(12), 80, "level 11 and above run at 80 ms");
    assert_eq!(gravity_ms(99), 80);
}

#[test]
fn level_is_one_plus_a_level_per_ten_lines() {
    assert_eq!(level_for(0), 1);
    assert_eq!(level_for(9), 1);
    assert_eq!(level_for(10), 2);
    assert_eq!(level_for(19), 2);
    assert_eq!(level_for(20), 3);
    assert_eq!(level_for(100), 11);
    assert_eq!(gravity_ms(level_for(100)), 80);
}

#[test]
fn gravity_steps_the_piece_down_at_the_level_interval() {
    let mut g = Game::new(1);
    let row0 = g.row;
    // Level 1 is 800 ms = 40 ticks.
    for _ in 0..39 {
        g.tick();
    }
    assert_eq!(g.row, row0, "no step before the interval");
    g.tick();
    assert_eq!(g.row, row0 + 1, "one step at the interval");
}

#[test]
fn a_higher_level_falls_faster() {
    let mut g = Game::new(1);
    g.level = 11;
    let row0 = g.row;
    for _ in 0..4 {
        g.tick();
    }
    assert_eq!(g.row, row0 + 1, "80 ms is four ticks");
}

// ---------------------------------------------------------------------------
// The lock delay
// ---------------------------------------------------------------------------

#[test]
fn a_ground_piece_locks_after_the_lock_delay() {
    let mut b = empty();
    // A shelf under the piece — deliberately not a full row, which would clear
    // on the lock and move the cells we are asserting about.
    fill(&mut b, 20, 4, 5);
    let mut g = placed(&b, O, 0, 17, 3);
    assert!(g.grounded());
    let pieces = g.pieces;
    for _ in 0..14 {
        g.tick(); // 280 ms
    }
    assert_eq!(g.pieces, pieces, "not yet: 280 ms is under 300 ms");
    g.tick();
    assert_eq!(g.pieces, pieces + 1, "locked at 300 ms");
    assert!(g.board[19][4] && g.board[19][5], "the piece is in the well");
}

#[test]
fn a_successful_move_resets_the_lock_delay() {
    let mut b = empty();
    fill(&mut b, 20, 4, 6);
    let mut g = placed(&b, O, 0, 17, 4);
    for _ in 0..10 {
        g.tick(); // 200 ms of the 300
    }
    assert!(g.grounded());
    g.key('h'); // a successful slide
    assert_eq!(g.lock_resets, 1);
    for _ in 0..10 {
        g.tick(); // 200 ms more: 400 ms in total
    }
    assert_eq!(g.pieces, 1, "the delay restarted, so it has not locked");
    for _ in 0..5 {
        g.tick(); // 300 ms after the reset
    }
    assert_eq!(g.pieces, 2, "and it locks 300 ms after the reset");
}

#[test]
fn the_lock_delay_reset_is_capped() {
    let mut b = empty();
    fill(&mut b, 20, 3, 7);
    let mut g = placed(&b, O, 0, 17, 5);
    let pieces = g.pieces;
    // Wiggle it for ever: 20 slides of 280 ms each is 5.6 seconds of delay.
    for i in 0..20 {
        for _ in 0..14 {
            g.tick();
        }
        g.key(if i % 2 == 0 { 'h' } else { 'l' });
    }
    assert_eq!(g.pieces, pieces + 1, "the reset budget ran out and it locked");
    assert!(g.lock_resets <= MAX_LOCK_RESETS);
}

#[test]
fn a_floating_piece_does_not_accumulate_the_lock_delay() {
    let mut g = Game::new(1);
    for _ in 0..40 {
        g.tick();
    }
    assert!(!g.grounded());
    assert_eq!(g.lock_acc, 0);
    assert_eq!(g.pieces, 1);
}

// ---------------------------------------------------------------------------
// Drop scoring
// ---------------------------------------------------------------------------

#[test]
fn soft_drop_scores_one_point_per_row() {
    let mut g = Game::new(1);
    let row0 = g.row;
    for _ in 0..5 {
        g.key('j');
    }
    assert_eq!(g.row, row0 + 5);
    assert_eq!(g.score, 5);
    assert_eq!(g.soft_points, 5);
    assert_eq!(g.hard_points, 0);
}

#[test]
fn hard_drop_scores_two_points_per_row_and_locks_at_once() {
    let mut g = Game::new(1);
    let row0 = g.row;
    let pieces = g.pieces;
    g.key(' ');
    let rows = landing_row(&empty(), g.kind, g.rot, row0, g.col) - row0;
    assert_eq!(g.pieces, pieces + 1, "the next piece spawned");
    assert_eq!(g.hard_points, rows as u64 * 2);
    assert_eq!(g.soft_points, 0);
}

#[test]
fn a_soft_drop_onto_the_floor_starts_the_lock_delay() {
    let mut b = empty();
    fill(&mut b, 20, 4, 5);
    let mut g = placed(&b, O, 0, 16, 3);
    g.key('j');
    assert!(g.grounded());
    assert_eq!(g.soft_points, 1);
    for _ in 0..15 {
        g.tick();
    }
    assert_eq!(g.pieces, 2, "locked 300 ms after the soft drop");
}

// ---------------------------------------------------------------------------
// Hold
// ---------------------------------------------------------------------------

#[test]
fn hold_swaps_the_piece_and_the_slot() {
    let mut g = Game::new(1);
    let first = g.kind;
    let next = g.preview()[0];
    g.key('c');
    assert_eq!(g.held, Some(first), "the piece you were holding is in the slot");
    assert_ne!(g.kind, first);
    assert_eq!(g.kind, next, "and you play the piece that was coming next");
    assert_eq!(g.row, SPAWN_ROW, "the swapped piece spawns fresh");
}

#[test]
fn hold_is_once_per_piece() {
    let mut g = Game::new(1);
    g.key('c');
    let after = g.kind;
    g.key('c');
    assert_eq!(g.kind, after, "the second hold of a piece does nothing");
    g.key(' '); // lock it
    assert!(g.can_hold, "a new piece may hold again");
}

#[test]
fn hold_resets_the_plan_and_the_timers() {
    let mut g = Game::new(1);
    for _ in 0..50 {
        g.tick();
    }
    g.key('c');
    assert_eq!(g.gravity_acc, 0);
    assert_eq!(g.lock_acc, 0);
    assert!(g.plan.is_none(), "the autopilot re-plans for the held piece");
}

// ---------------------------------------------------------------------------
// Game over
// ---------------------------------------------------------------------------

#[test]
fn a_spawn_that_overlaps_the_well_ends_the_game() {
    let mut b = empty();
    // The spawn area is the top of the well: the hidden rows over columns 3..5.
    fill(&mut b, 0, 3, 5);
    fill(&mut b, 1, 3, 5);
    let mut g = placed(&b, O, 0, 17, 3);
    fill(&mut g.board, 20, 4, 5);
    assert_eq!(g.state, State::Play);
    for _ in 0..15 {
        g.tick();
    }
    assert_eq!(g.state, State::Over, "the well is full to the top");
    assert_eq!(g.cause, Some(Cause::TopOut));
    assert_eq!(g.cause.unwrap().name(), "top-out");
}

#[test]
fn a_finished_game_is_frozen() {
    let mut g = Game::new(1);
    fill(&mut g.board, 0, 3, 5);
    fill(&mut g.board, 1, 3, 5);
    g.kind = O;
    g.rot = 0;
    g.row = SPAWN_ROW;
    g.col = SPAWN_COL;
    g.state = State::Over;
    g.cause = Some(Cause::TopOut);
    let before = g.canonical();
    for _ in 0..50 {
        g.tick();
    }
    g.key('h');
    g.key(' ');
    g.key('c');
    assert_eq!(g.canonical(), before, "nothing moves after the end");
}

#[test]
fn pause_freezes_the_game_and_p_resumes_it() {
    let mut g = Game::new(1);
    g.key('p');
    assert_eq!(g.state, State::Paused);
    let before = g.canonical();
    for _ in 0..100 {
        g.tick();
    }
    assert_eq!(g.canonical(), before, "a paused game does not run");
    g.key('p');
    assert_eq!(g.state, State::Play);
    g.tick();
    assert_ne!(g.canonical(), before);
}

// ---------------------------------------------------------------------------
// The key table
// ---------------------------------------------------------------------------

#[test]
fn the_keys_move_rotate_drop_hold_pause_restart_and_quit() {
    let mut g = Game::new(1);
    let (row, col) = (g.row, g.col);
    g.key('h');
    assert_eq!(g.col, col - 1);
    g.key('l');
    g.key('l');
    assert_eq!(g.col, col + 1, "h/l move, and a at/d work as aliases");
    g.key('a');
    assert_eq!(g.col, col);
    g.key('d');
    assert_eq!(g.col, col + 1);
    g.key('j');
    assert_eq!(g.row, row + 1, "j soft drops");
    assert_eq!(g.rot, 0);
    g.key('k');
    assert_eq!(g.rot, 1, "k rotates clockwise");
    g.key('z');
    assert_eq!(g.rot, 0, "z rotates counter-clockwise");
    g.key('x');
    assert_eq!(g.rot, 1, "x rotates clockwise");
    let pieces = g.pieces;
    g.key(' ');
    assert_eq!(g.pieces, pieces + 1, "space hard drops");
    assert!(g.key('q'), "q is the key that quits");
}

#[test]
fn unknown_keys_are_ignored() {
    let mut g = Game::new(1);
    let before = g.canonical();
    for c in "0123456789-=_+[]\\;',./~!@#$%^&*()".chars() {
        assert!(!g.key(c));
    }
    assert_eq!(g.canonical(), before);
}

#[test]
fn restart_starts_a_fresh_game_on_the_next_seed_and_keeps_the_record() {
    let mut g = Game::new(7);
    g.score = 1234;
    g.best_score = 9999;
    g.key('r');
    assert_eq!(g.seed, 8, "the seed family steps forward");
    assert_eq!(g.score, 0);
    assert_eq!(g.lines, 0);
    assert_eq!(g.level, 1);
    assert_eq!(g.best_score, 9999, "the persisted record survives a restart");
    assert_eq!(g.state, State::Play);
}

// ---------------------------------------------------------------------------
// The preview
// ---------------------------------------------------------------------------

#[test]
fn the_preview_shows_the_next_three_pieces() {
    let g = Game::new(21);
    assert_eq!(g.preview().len(), 3);
    assert_eq!(preview_names(&g.preview()).split(' ').count(), 3);
    // The preview is the bag's own look-ahead.
    assert_eq!(g.preview(), g.bag.peek(3));
}

// ---------------------------------------------------------------------------
// Determinism
// ---------------------------------------------------------------------------

/// A scripted session: one key per tick, a space character is the hard drop.
fn run_script(seed: u64, script: &str, ticks: u64) -> Game {
    let mut g = Game::new(seed);
    let keys: Vec<char> = script.chars().collect();
    for t in 0..ticks {
        if g.state != State::Play {
            break;
        }
        if let Some(&c) = keys.get((t as usize) % keys.len().max(1)) {
            g.key(c);
        }
        g.tick();
    }
    g
}

#[test]
fn the_same_seed_and_the_same_script_always_give_the_same_state() {
    let script = "hlhxz jjj  hh l  kk  ll  zz  jj jj  cc  jjj  hh  kk  ";
    let a = run_script(101, script, 900);
    let b = run_script(101, script, 900);
    assert_eq!(a.state_hash(), b.state_hash());
    assert_eq!(a.canonical(), b.canonical());
    assert_eq!(a.score, b.score);
    assert_eq!(a.lines, b.lines);
    assert_eq!(plain_state(&a), plain_state(&b));
}

#[test]
fn a_different_seed_gives_a_different_game() {
    let script = "hlhxz jjj  hh l  kk  ll  zz  jj jj  cc  jjj  hh  kk  ";
    let a = run_script(101, script, 900);
    let b = run_script(102, script, 900);
    assert_ne!(a.state_hash(), b.state_hash());
}

#[test]
fn a_scripted_session_reaches_a_pinned_end_state() {
    // The golden run: seed 42, the autopilot's own script, 4000 ticks. If this
    // hash moves, a rule changed and the change is deliberate and documented.
    let mut g = Game::new(42);
    for _ in 0..4000 {
        if g.state != State::Play {
            break;
        }
        let keys = g.autopilot_keys();
        for c in keys.chars() {
            g.key(c);
        }
        g.tick();
    }
    assert_eq!(g.state, State::Over);
    assert_eq!(g.score, 174_528);
    assert_eq!(g.lines, 168);
    assert_eq!(g.level, 17);
    assert_eq!(g.pieces, 464);
    assert_eq!(format!("{:016x}", g.state_hash()), "53cbffd1a21e3566");
}

#[test]
fn the_state_hash_covers_the_whole_state() {
    let a = Game::new(1);
    let mut b = Game::new(1);
    b.score += 1;
    assert_ne!(a.state_hash(), b.state_hash());
    let mut c = Game::new(1);
    c.row += 1;
    assert_ne!(a.state_hash(), c.state_hash());
    let mut d = Game::new(1);
    d.bag.rng = Rng::new(9);
    assert_ne!(a.state_hash(), d.state_hash(), "the generator state counts");
}

#[test]
fn the_seeded_generator_is_reproducible_and_never_zero() {
    let mut a = Rng::new(0);
    let mut b = Rng::new(0);
    for _ in 0..100 {
        assert_eq!(a.next_u64(), b.next_u64());
        assert_ne!(a.state(), 0, "xorshift must never hold 0");
    }
    let mut c = Rng::new(7);
    for _ in 0..50 {
        assert!(c.below(7) < 7);
    }
}

// ---------------------------------------------------------------------------
// The headless report
// ---------------------------------------------------------------------------

#[test]
fn the_plain_state_is_key_value_lines_and_the_well() {
    let g = Game::new(1);
    let text = plain_state(&g);
    for key in [
        "state=", "seed=", "score=", "lines=", "level=", "pieces=", "hash=", "next=", "well:",
    ] {
        assert!(text.contains(key), "plain_state is missing {key}");
    }
    assert!(!text.contains('\x1b'), "no escapes in the plain report");
    assert_eq!(text.lines().filter(|l| l.starts_with('|')).count(), ROWS);
}

#[test]
fn result_kv_carries_the_acceptance_fields() {
    let g = Game::new(1);
    let kv = g.result_kv();
    let keys: Vec<&str> = kv.iter().map(|(k, _)| k.as_str()).collect();
    for k in ["state", "seed", "score", "lines", "level", "pieces", "hash"] {
        assert!(keys.contains(&k), "{k} is reported");
    }
}

// ---------------------------------------------------------------------------
// Rendering — a pure function of state
// ---------------------------------------------------------------------------

#[test]
fn the_frame_is_a_pure_function_of_state() {
    let g = Game::new(1);
    assert_eq!(g.render(RenderOpts::default()), g.render(RenderOpts::default()));
    let opts = RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    };
    assert_eq!(g.render(opts), g.render(opts));
}

#[test]
fn the_frame_fits_80x24_and_draws_the_panels() {
    let g = Game::new(1);
    let frame = g.render(RenderOpts::default());
    let lines: Vec<&str> = frame.lines().collect();
    assert_eq!(lines.len(), REF_HEIGHT, "the reference window is 24 rows");
    for line in &lines {
        assert_eq!(pad_to(line, 80).chars().count() - line.chars().count() + line.chars().count(), line.chars().count());
    }
    assert!(frame.contains("HOLD"), "the hold panel");
    assert!(frame.contains("NEXT"), "the next-3 panel");
    assert!(frame.contains("score"), "the score panel");
    assert!(frame.contains("GLACIER BLOCKS"), "the title");
    assert!(frame.contains(LEGEND), "the key legend");
    assert!(frame.contains('│'), "the border");
}

#[test]
fn every_frame_line_is_exactly_the_window_width() {
    for (w, h) in [(80usize, 24usize), (60, 24), (40, 24), (24, 24), (20, 20), (10, 10), (1, 1)] {
        let g = Game::new(3);
        let frame = g.render(RenderOpts {
            width: w,
            height: h,
            mono: true,
            color: false,
        });
        for line in frame.lines() {
            assert_eq!(line.chars().count(), w, "width {w}: {line:?}");
        }
        assert!(
            frame.lines().count() <= h,
            "height {h} overflowed with {} lines",
            frame.lines().count()
        );
    }
}

#[test]
fn a_narrow_window_drops_the_side_panels() {
    let g = Game::new(1);
    let wide = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: false,
        color: false,
    });
    let mid = g.render(RenderOpts {
        width: 30,
        height: 24,
        mono: false,
        color: false,
    });
    assert!(wide.contains("NEXT"));
    assert!(!mid.contains("NEXT"), "the panels go first");
    assert!(!mid.contains("HOLD"));
    assert!(mid.contains('│'), "the well keeps its border at 30 columns");
}

#[test]
fn a_very_narrow_window_drops_the_border_and_the_title() {
    let mut g = Game::new(1);
    g.row = 10; // a piece the player can see
    let narrow = g.render(RenderOpts {
        width: 12,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(!narrow.contains('│'), "the border goes with the width");
    assert!(!narrow.contains("GLACIER BLOCKS"), "so does the title");
    assert!(narrow.contains("[]"), "the well is still drawn");
}

#[test]
fn a_short_window_clips_the_well_from_the_bottom() {
    let mut g = Game::new(1);
    g.row = 5; // a piece the player can see
    let short = g.render(RenderOpts {
        width: 80,
        height: 10,
        mono: true,
        color: false,
    });
    assert_eq!(short.lines().count(), 10);
    assert!(short.contains("[]"), "the well is drawn");
    assert!(!short.contains(LEGEND), "the legend is given up first");
}

#[test]
fn mono_draws_ascii_only() {
    let g = Game::new(1);
    let frame = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: true,
    });
    for c in ['│', '─', '┌', '┐', '└', '┘', '█', '░'] {
        assert!(!frame.contains(c), "mono must not draw {c}");
    }
    assert!(!frame.contains('\x1b'), "mono is colour-free");
    assert!(frame.contains('+') && frame.contains('-'));
}

#[test]
fn no_colour_means_no_escapes() {
    let mut g = Game::new(1);
    g.row = 10; // a piece the player can see, so colour has something to fill
    let frame = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: false,
        color: false,
    });
    assert!(!frame.contains('\x1b'));
    let colour = g.render(RenderOpts::default());
    assert!(colour.contains("\x1b[4"), "colour mode uses background fills");
}

#[test]
fn the_ghost_shows_where_the_piece_lands() {
    let g = Game::new(1);
    let frame = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(frame.contains(".."), "the landing preview is drawn");
}

#[test]
fn the_end_of_the_game_is_said_on_the_screen() {
    let mut g = Game::new(1);
    g.state = State::Over;
    g.cause = Some(Cause::TopOut);
    let frame = g.render(RenderOpts::default());
    assert!(frame.contains("GAME OVER"));
    assert!(frame.contains("top-out"));
    g.state = State::Paused;
    assert!(g.render(RenderOpts::default()).contains("PAUSED"));
}

#[test]
fn padding_counts_the_characters_the_terminal_draws() {
    assert_eq!(pad_to("abc", 6), "abc   ");
    assert_eq!(pad_to("\x1b[41m  \x1b[0m", 4), "\x1b[41m  \x1b[0m  ");
    assert_eq!(pad_to("abc", 2), "ab", "a long line is clipped, never scrolled");
}

// ---------------------------------------------------------------------------
// The autopilot (the demo / headless player)
// ---------------------------------------------------------------------------

#[test]
fn the_autopilot_plays_a_real_game() {
    let mut g = Game::new(7);
    for _ in 0..4000 {
        if g.state != State::Play {
            break;
        }
        for c in g.autopilot_keys().chars() {
            g.key(c);
        }
        g.tick();
    }
    assert_eq!(g.state, State::Over);
    assert!(g.pieces > 40, "the demo player survives the pack-in length");
    assert!(g.lines >= 10, "and clears lines: {}", g.lines);
    assert!(g.level >= 2, "and levels up: {}", g.level);
}

#[test]
fn the_autopilot_is_a_pure_function_of_state() {
    let mut a = Game::new(5);
    let mut b = Game::new(5);
    for _ in 0..200 {
        assert_eq!(a.autopilot_keys(), b.autopilot_keys());
        let k = a.autopilot_keys();
        for c in k.chars() {
            a.key(c);
            b.key(c);
        }
        a.tick();
        b.tick();
    }
    assert_eq!(a.state_hash(), b.state_hash());
}

#[test]
fn the_board_score_and_the_placement_score_are_pure_and_ordered() {
    let b = empty();
    assert_eq!(board_score(&b), 0, "an empty well is the zero of the scale");
    let mut filled = empty();
    fill(&mut filled, 21, 0, 9);
    assert!(board_score(&filled) < board_score(&b), "a full row is not free");
    // A placement that clears a line beats one that does not.
    // A placement that clears a line beats one that does not.
    let mut b2 = empty();
    fill(&mut b2, 21, 0, 8);
    let clear = evaluate(&b2, I, 1, 18, 7);
    let quiet = evaluate(&b2, O, 0, 19, 3);
    assert!(clear > quiet, "the line clear dominates: {clear} vs {quiet}");
}

#[test]
fn place_collapses_the_rows_it_completes() {
    let mut b = empty();
    fill(&mut b, 21, 0, 8);
    let (after, cleared) = place(&b, O, 0, 19, 4);
    assert_eq!(cleared, 0, "row 21 is one cell short, so nothing clears");
    assert!(after[21][4] && after[21][5]);
    assert_eq!(after[21].iter().filter(|c| **c).count(), 9);
}
