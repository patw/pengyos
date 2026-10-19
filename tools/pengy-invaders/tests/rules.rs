//! The rules contract: every rule in `spec.md`, asserted on the library
//! directly. No I/O, no clock, no pty — `src/lib.rs` is pure, so these are
//! plain function calls.

use pengy_invaders::*;

/// Feed a script of keys, one per tick (None = no input).
fn run_script(g: &mut Game, keys: &[Option<char>]) {
    for k in keys {
        if g.state == State::Over {
            return;
        }
        if let Some(c) = k {
            g.key(*c);
        }
        g.tick();
    }
}

/// Run `n` ticks with the autopilot flying the ship.
fn autopilot(g: &mut Game, n: u64) {
    for _ in 0..n {
        if g.state == State::Over {
            return;
        }
        for k in g.autopilot_keys().chars() {
            g.key(k);
        }
        g.tick();
    }
}

/// A play field with the title screen dismissed.
fn playing(seed: u64) -> Game {
    let mut g = Game::new(seed);
    g.key(' ');
    g
}

/// Kill every alien except the ones in `rows`, so the formation is small and
/// the field is predictable.
fn clear_all_but(g: &mut Game, rows: &[usize]) {
    for ri in 0..FORM_ROWS {
        for fc in 0..FORM_COLS {
            g.aliens[ri][fc] = rows.contains(&ri);
        }
    }
}

/// Leave exactly one alien alive, in the top row of the rightmost formation
/// column: far from the action, and enough aliens that the wave does not
/// advance under the test. (A cleared formation advances the wave on the next
/// tick, which is a rule, not an accident.)
fn one_alien_far(g: &mut Game) {
    clear_all_but(g, &[]);
    g.aliens[0][FORM_COLS - 1] = true;
}

/// Wipe the shields so a laser has a clear lane to fly.
fn clear_shields(g: &mut Game) {
    for s in 0..SHIELD_COUNT {
        for r in 0..SHIELD_H {
            for c in 0..SHIELD_W {
                g.shields[s][r][c] = false;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Field, geometry, constants
// ---------------------------------------------------------------------------

#[test]
fn the_field_is_60x24_with_the_ship_on_row_23() {
    assert_eq!((COLS, ROWS), (60, 24));
    assert_eq!(PLAYER_ROW, 23);
    assert_eq!((MIN_X, MAX_X), (1, 58));
    let g = Game::new(1);
    assert_eq!(g.player_x, COLS / 2);
    assert_eq!(g.lives, START_LIVES);
    assert_eq!(g.wave, 1);
    assert_eq!(g.alive(), ALIENS);
    assert_eq!(ALIENS, 55);
}

#[test]
fn the_formation_is_11x5_two_cells_wide_spaced_four() {
    assert_eq!((FORM_COLS, FORM_ROWS, ALIEN_W, COL_SPACING), (11, 5, 2, 4));
    assert_eq!(FORM_W, 42);
    let g = Game::new(1);
    assert_eq!(g.formation_x, FORM_X0);
    assert_eq!(g.formation_row, FORM_ROW0);
    // The leftmost alien occupies two cells; the gap between columns is two.
    assert_eq!(alien_left_col(g.formation_x, 0), 9);
    assert_eq!(alien_left_col(g.formation_x, 1), 13);
    assert_eq!(alien_row(g.formation_row, 4), 6);
    // The formation's outermost cells.
    assert_eq!(g.formation_x + FORM_W - 1, 50);
}

#[test]
fn a_screen_column_maps_to_the_formation_column_that_covers_it() {
    // Formation left cell at 9: columns 9-10 are fc 0, 11-12 are the gap,
    // 13-14 are fc 1.
    assert_eq!(formation_col_at(9, 9), Some(0));
    assert_eq!(formation_col_at(9, 10), Some(0));
    assert_eq!(formation_col_at(9, 11), None);
    assert_eq!(formation_col_at(9, 12), None);
    assert_eq!(formation_col_at(9, 13), Some(1));
    assert_eq!(formation_col_at(9, 8), None);
    assert_eq!(formation_col_at(9, 9 + 4 * 10), Some(10));
    assert_eq!(formation_col_at(9, 9 + 4 * 11), None);
}

#[test]
fn shields_are_three_8x4_blocks_between_the_formation_and_the_ship() {
    assert_eq!((SHIELD_COUNT, SHIELD_W, SHIELD_H, SHIELD_TOP), (3, 8, 4, 18));
    let g = Game::new(1);
    assert_eq!(g.shield_cells(), 3 * 8 * 4);
    assert_eq!(shield_cell_at(18, 8), Some((0, 0, 0)));
    assert_eq!(shield_cell_at(21, 15), Some((0, 3, 7)));
    assert_eq!(shield_cell_at(17, 8), None, "above the shields");
    assert_eq!(shield_cell_at(22, 8), None, "below the shields");
    assert_eq!(shield_cell_at(18, 16), None, "between shields");
    assert_eq!(shield_cell_at(21, 51), Some((2, 3, 7)));
    // The shields sit above the ship's row and below the wave-1 formation.
    assert!(SHIELD_TOP > 6 && SHIELD_TOP < PLAYER_ROW);
}

// ---------------------------------------------------------------------------
// Tempo: the step period
// ---------------------------------------------------------------------------

#[test]
fn the_step_period_starts_at_8_and_shrinks_with_kills() {
    assert_eq!(period_for(55, 1), 8);
    assert_eq!(period_for(28, 1), 4);
    assert_eq!(period_for(14, 1), 2);
    assert_eq!(period_for(7, 1), 1);
    // Never below 1, whatever the numbers say.
    assert_eq!(period_for(1, 1), 1);
    assert_eq!(period_for(0, 9), 1);
    // Each wave is faster.
    assert_eq!(period_for(55, 2), 4);
    assert_eq!(period_for(55, 3), 2);
    assert_eq!(period_for(55, 8), 1);
    let mut g = playing(1);
    assert_eq!(g.step_period(), 8);
    g.aliens[4][0] = false;
    g.aliens[4][1] = false;
    assert_eq!(g.alive(), 53);
    assert_eq!(period_for(53, 1), 7, "8 * 53 / 55 = 7");
}

#[test]
fn the_formation_steps_one_column_every_period_ticks() {
    let mut g = playing(1);
    let x0 = g.formation_x;
    assert_eq!(g.step_period(), 8);
    for i in 1..8 {
        g.tick();
        assert_eq!(g.formation_x, x0, "no step before the period is up (tick {i})");
    }
    g.tick();
    assert_eq!(g.formation_x, x0 + 1, "one column on the 8th tick");
    for _ in 0..7 {
        g.tick();
    }
    assert_eq!(g.formation_x, x0 + 1);
    g.tick();
    assert_eq!(g.formation_x, x0 + 2);
}

#[test]
fn the_period_never_drops_below_one_as_the_formation_thins() {
    let mut g = playing(1);
    clear_all_but(&mut g, &[4]);
    assert_eq!(g.alive(), 11);
    assert_eq!(g.step_period(), 1);
    // 11 aliens, 1 column per tick: the row crosses the field in under a
    // second per sweep, and the period stays at the floor.
    for _ in 0..40 {
        g.tick();
    }
    assert!(g.step_period() >= MIN_PERIOD);
    assert_eq!(g.step_period(), 1);
}

// ---------------------------------------------------------------------------
// Reversal and descent, exactly at both edges
// ---------------------------------------------------------------------------

#[test]
fn the_formation_reverses_and_descends_exactly_at_the_right_edge() {
    let mut g = playing(1);
    // Park it one column short of the wall, with the step due.
    g.formation_x = RIGHT_EDGE - FORM_W; // 16, rightmost cell 57
    g.dir = Dir::Right;
    g.step_countdown = 1;
    let row = g.formation_row;
    g.tick();
    assert_eq!(g.formation_x, 17, "clamped so the outermost cell is on 58");
    assert_eq!(g.formation_x + FORM_W - 1, RIGHT_EDGE);
    assert_eq!(g.dir, Dir::Left, "reversed");
    assert_eq!(g.formation_row, row + 1, "descended one row");
    assert_eq!(g.reversals, 1);
    assert_eq!(g.descents, 1);
    // And it walks away from the wall, it does not stick.
    g.step_countdown = 1;
    g.tick();
    assert_eq!(g.formation_x, 16);
    assert_eq!(g.formation_row, row + 1, "no second descent");
    assert_eq!(g.descents, 1);
}

#[test]
fn the_formation_reverses_and_descends_exactly_at_the_left_edge() {
    let mut g = playing(1);
    g.formation_x = LEFT_EDGE; // on the wall, with the step due
    g.dir = Dir::Left;
    g.step_countdown = 1;
    let row = g.formation_row;
    g.tick();
    assert_eq!(g.formation_x, LEFT_EDGE, "clamped onto column 1");
    assert_eq!(g.dir, Dir::Right, "reversed");
    assert_eq!(g.formation_row, row + 1, "descended one row");
    assert_eq!(g.reversals, 1);
    assert_eq!(g.descents, 1);
    g.step_countdown = 1;
    g.tick();
    assert_eq!(g.formation_x, LEFT_EDGE + 1);
    assert_eq!(g.descents, 1, "the descent is per reversal, not per tick");
}

#[test]
fn every_reversal_descends_and_the_two_edges_are_symmetric() {
    let mut g = playing(1);
    // March to the right wall and back; one reversal each way, two descents.
    let row0 = g.formation_row;
    for _ in 0..400 {
        g.tick();
        if g.reversals >= 2 {
            break;
        }
    }
    assert_eq!(g.reversals, 2);
    assert_eq!(g.descents, 2);
    assert_eq!(g.formation_row, row0 + 2);
    // The formation never leaves the field.
    assert!(g.formation_x >= LEFT_EDGE);
    assert!(g.formation_x + FORM_W - 1 <= RIGHT_EDGE);
}

// ---------------------------------------------------------------------------
// The laser
// ---------------------------------------------------------------------------

#[test]
fn the_ship_moves_two_columns_per_keypress_and_clamps() {
    let mut g = playing(1);
    let x = g.player_x;
    g.key('d');
    assert_eq!(g.player_x, x + 2);
    g.key('a');
    assert_eq!(g.player_x, x);
    g.key('h');
    assert_eq!(g.player_x, x - 2, "the arrow keys arrive as h/l");
    g.key('l');
    assert_eq!(g.player_x, x);
    for _ in 0..40 {
        g.key('a');
    }
    assert_eq!(g.player_x, MIN_X);
    for _ in 0..40 {
        g.key('d');
    }
    assert_eq!(g.player_x, MAX_X);
}

#[test]
fn one_laser_is_in_flight_and_the_cooldown_is_two_ticks() {
    let mut g = playing(1);
    clear_shields(&mut g);
    one_alien_far(&mut g);
    g.key(' ');
    assert!(g.laser.is_some());
    assert_eq!(g.cooldown, FIRE_COOLDOWN);
    assert_eq!(g.shots, 1);
    // A second shot while the laser is in flight is ignored.
    g.key(' ');
    assert_eq!(g.shots, 1);
    // And the cooldown gates the shot even once the laser is gone.
    g.tick();
    assert_eq!(g.cooldown, 1);
    g.key(' ');
    assert_eq!(g.shots, 1, "cooldown gates the shot");
    g.tick();
    assert_eq!(g.cooldown, 0);
    let mut ticks = 0;
    while g.laser.is_some() {
        g.tick();
        ticks += 1;
        assert!(ticks < 40, "the laser must leave the field");
    }
    assert_eq!(g.cooldown, 0, "the cooldown expired while the laser flew");
    g.key(' ');
    assert_eq!(g.shots, 2);
    g.key(' ');
    assert_eq!(g.shots, 2, "cooldown gates the shot");
    g.tick();
    assert_eq!(g.cooldown, 1);
    g.tick();
    assert_eq!(g.cooldown, 0);
    g.laser = None;
    g.key(' ');
    assert_eq!(g.shots, 3);
}

#[test]
fn the_laser_travels_two_rows_per_tick_upward() {
    let mut g = playing(1);
    clear_shields(&mut g);
    one_alien_far(&mut g);
    g.player_x = 30;
    g.key(' ');
    assert_eq!(g.laser.map(|l| l.row), Some(PLAYER_ROW as i64));
    g.tick();
    assert_eq!(g.laser.map(|l| l.row), Some(PLAYER_ROW as i64 - 2));
    g.tick();
    assert_eq!(g.laser.map(|l| l.row), Some(PLAYER_ROW as i64 - 4));
    assert_eq!(g.laser.map(|l| l.col), Some(30));
}

#[test]
fn a_laser_kills_the_lowest_alien_in_its_column() {
    let mut g = playing(1);
    // Leave one alien in the bottom row and one in the top row of column 3.
    clear_all_but(&mut g, &[0, 4]);
    let fc = 3;
    let col = alien_left_col(g.formation_x, fc) + 1;
    g.player_x = col;
    g.laser = None;
    g.cooldown = 0;
    let score0 = g.score;
    g.key(' ');
    // The laser climbs from row 23; the bottom alien is on row 6.
    let mut ticks = 0;
    while g.laser.is_some() && ticks < 40 {
        g.tick();
        ticks += 1;
    }
    assert_eq!(g.aliens[4][fc], false, "the bottom alien died");
    assert_eq!(g.aliens[0][fc], true, "the top alien is untouched");
    assert_eq!(g.kills, 1);
    assert_eq!(g.score, score0 + score_for_row(4), "the bottom row scores 10");
}

#[test]
fn a_laser_takes_its_flight_time_and_a_shield_below_the_alien_stops_it() {
    // Column 30 is under formation column 5 *and* inside shield 1: the shot
    // must climb, and the shield is the first thing it meets.
    let mut g = playing(1);
    clear_all_but(&mut g, &[4]);
    g.step_countdown = u64::MAX; // hold the formation still
    let fc = formation_col_at(g.formation_x, 30).expect("col 30 is under an alien");
    g.player_x = 30;
    g.laser = None;
    g.cooldown = 0;
    let cells0 = g.shield_cells();
    g.key(' ');
    g.tick();
    assert!(g.aliens[4][fc], "one tick is not enough to reach row 6");
    let mut ticks = 1;
    while g.laser.is_some() && ticks < 40 {
        g.tick();
        ticks += 1;
    }
    assert!(g.aliens[4][fc], "the alien behind the shield is untouched");
    assert_eq!(g.kills, 0);
    assert_eq!(g.shield_cells(), cells0 - 1, "the shield took the shot");

    // Through the hole the shot just made, it climbs on and kills it, no
    // sooner than the rows it has to cross.
    g.cooldown = 0;
    // Erode the rest of the shield column so the laser has a clear path.
    for sr in 0..4 {
        g.shields[1][sr][4] = false;
    }
    g.key(' ');
    let rise = PLAYER_ROW - alien_row(g.formation_row, 4);
    let mut ticks = 0;
    while g.laser.is_some() && ticks < 40 {
        g.tick();
        ticks += 1;
    }
    assert!(!g.aliens[4][fc], "the open column lets the shot through");
    assert!(ticks >= rise / LASER_SPEED, "the shot flew {ticks} ticks for {rise} rows");
}

#[test]
fn alien_points_are_10_at_the_bottom_and_50_at_the_top() {
    assert_eq!(score_for_row(4), 10);
    assert_eq!(score_for_row(3), 20);
    assert_eq!(score_for_row(2), 30);
    assert_eq!(score_for_row(1), 40);
    assert_eq!(score_for_row(0), 50);
}

#[test]
fn a_laser_that_hits_a_shield_erodes_that_cell_and_dies() {
    let mut g = playing(1);
    // Fire up the middle of shield 1 (columns 26..33, rows 18..21). The first
    // cell the laser reaches is the shield's bottom row.
    g.player_x = 30;
    one_alien_far(&mut g);
    g.laser = None;
    g.cooldown = 0;
    let cells0 = g.shield_cells();
    g.key(' ');
    let mut ticks = 0;
    while g.laser.is_some() && ticks < 40 {
        g.tick();
        ticks += 1;
    }
    assert_eq!(g.shield_cells(), cells0 - 1, "exactly one cell eroded");
    assert_eq!(
        shield_cell_at(21, 30),
        Some((1, 3, 4)),
        "the eroded cell is the one the laser flew into"
    );
    assert_eq!(g.shields[1][3][4], false);
    assert_eq!(g.laser, None, "the laser is consumed by the shield");
}

#[test]
fn a_laser_flies_through_a_hole_in_a_shield() {
    let mut g = playing(1);
    one_alien_far(&mut g);
    g.player_x = 30;
    g.shields[1][3][4] = false; // the cell the laser would fly into
    let cells0 = g.shield_cells();
    g.laser = None;
    g.cooldown = 0;
    g.key(' ');
    let mut ticks = 0;
    while g.laser.is_some() && ticks < 40 {
        g.tick();
        ticks += 1;
    }
    assert_eq!(g.shields[1][3][4], false, "the hole is still a hole");
    assert_eq!(
        g.shields[1][2][4], false,
        "the laser stopped at the next intact cell, one row above the hole"
    );
    assert_eq!(g.shield_cells(), cells0 - 1, "the hole costs nothing");
}

#[test]
fn a_laser_shoots_down_a_bomb() {
    let mut g = playing(1);
    one_alien_far(&mut g);
    clear_shields(&mut g);
    g.player_x = 30;
    g.bombs = vec![Bomb { col: 30, row: 12 }];
    g.laser = None;
    g.cooldown = 0;
    g.key(' ');
    let mut ticks = 0;
    while g.laser.is_some() && ticks < 40 {
        g.tick();
        ticks += 1;
    }
    assert!(g.bombs.is_empty(), "the bomb is gone");
    assert_eq!(g.bombs_shot, 1);
}

// ---------------------------------------------------------------------------
// Bombs
// ---------------------------------------------------------------------------

#[test]
fn bomb_probability_rises_with_the_wave() {
    assert_eq!(bomb_rate_for(1), 20, "0.002");
    assert_eq!(bomb_rate_for(2), 25, "0.0025");
    assert_eq!(bomb_rate_for(5), 40);
    assert_eq!(BOMB_RATE_SCALE, 10_000);
    let mut g = playing(1);
    assert_eq!(g.bomb_rate(), 20);
    g.wave = 4;
    assert_eq!(g.bomb_rate(), 35);
}

#[test]
fn at_most_three_bombs_are_on_screen_and_they_fall_one_row_per_tick() {
    let mut g = playing(1);
    clear_shields(&mut g);
    // A full formation rolling every tick saturates the cap fast.
    let mut peak = 0;
    for _ in 0..400 {
        g.tick();
        peak = peak.max(g.bombs.len());
        assert!(g.bombs.len() <= MAX_BOMBS, "never more than 3 bombs");
    }
    assert_eq!(peak, MAX_BOMBS, "the cap is reachable");
    assert_eq!(g.state, State::Play);
    let before = g.bombs.clone();
    g.tick();
    assert_eq!(g.bombs.len(), before.len());
    for (b, a) in g.bombs.iter().zip(before.iter()) {
        assert_eq!(b.row, a.row + BOMB_SPEED);
    }
}

#[test]
fn a_bomb_that_reaches_the_ship_takes_a_life_and_clears_the_field() {
    let mut g = playing(1);
    one_alien_far(&mut g);
    clear_shields(&mut g);
    g.player_x = 30;
    g.bombs = vec![Bomb { col: 30, row: PLAYER_ROW - 1 }];
    g.lives = START_LIVES;
    g.tick();
    assert_eq!(g.lives, START_LIVES - 1);
    assert_eq!(g.hits_taken, 1);
    assert!(g.bombs.is_empty(), "the bombs clear on a hit");
    assert_eq!(g.laser, None);
    assert_eq!(g.player_x, COLS / 2, "the ship recentres");
    assert_eq!(g.state, State::Play);
}

#[test]
fn a_bomb_that_hits_a_shield_erodes_that_cell() {
    let mut g = playing(1);
    one_alien_far(&mut g);
    let cells0 = g.shield_cells();
    g.bombs = vec![Bomb { col: 30, row: SHIELD_TOP - 1 }];
    g.tick();
    assert_eq!(g.shield_cells(), cells0 - 1);
    assert_eq!(g.shields[1][0][4], false);
    assert!(g.bombs.is_empty(), "the bomb is spent on the shield");
    assert_eq!(g.lives, START_LIVES, "the shield took the hit, not the ship");
}

#[test]
fn a_bomb_that_leaves_the_bottom_of_the_field_is_gone() {
    let mut g = playing(1);
    one_alien_far(&mut g);
    clear_shields(&mut g);
    g.player_x = 1;
    g.bombs = vec![Bomb { col: 55, row: ROWS - 1 }];
    g.tick();
    assert!(g.bombs.is_empty());
    assert_eq!(g.lives, START_LIVES);
}

#[test]
fn three_lives_and_the_third_hit_ends_the_run() {
    let mut g = playing(1);
    one_alien_far(&mut g);
    clear_shields(&mut g);
    assert_eq!(g.lives, 3);
    for i in 1..=3 {
        g.player_x = 30;
        g.bombs = vec![Bomb { col: 30, row: PLAYER_ROW - 1 }];
        g.tick();
        assert_eq!(g.lives, 3 - i);
        if i < 3 {
            assert_eq!(g.state, State::Play, "the run plays on with lives left");
        }
    }
    assert_eq!(g.state, State::Over);
    assert_eq!(g.cause, Some(Cause::Destroyed));
    assert_eq!(g.lives, 0);
}

// ---------------------------------------------------------------------------
// Waves
// ---------------------------------------------------------------------------

#[test]
fn clearing_a_wave_scores_the_bonus_and_starts_the_next_one_lower_and_faster() {
    let mut g = playing(1);
    let row0 = g.formation_row;
    let period0 = g.step_period();
    clear_all_but(&mut g, &[]);
    assert_eq!(g.alive(), 0);
    g.tick();
    assert_eq!(g.wave, 2);
    assert_eq!(g.waves_cleared, 1);
    assert_eq!(g.alive(), ALIENS, "the formation refilled");
    assert_eq!(g.formation_row, row0 + 1, "one row lower");
    assert_eq!(g.formation_x, FORM_X0, "centred again");
    assert_eq!(g.dir, Dir::Right, "marching right again");
    assert!(g.step_period() < period0, "faster");
    assert_eq!(g.step_period(), period_for(ALIENS, 2));
    assert_eq!(g.score, WAVE_BONUS * 1, "the wave-1 bonus");
    assert_eq!(g.bombs.len(), 0, "the field is clear for the new wave");
    assert_eq!(g.state, State::Play);
}

#[test]
fn waves_keep_the_score_and_the_wave_number_across_several_clears() {
    let mut g = playing(1);
    for w in 1..=4 {
        clear_all_but(&mut g, &[]);
        g.tick();
        assert_eq!(g.wave, w + 1);
        assert_eq!(g.score, WAVE_BONUS * (w * (w + 1) / 2) as u64);
        assert_eq!(g.formation_row, FORM_ROW0 + w as usize);
    }
    assert_eq!(g.waves_cleared, 4);
    assert_eq!(g.step_period(), period_for(ALIENS, 5));
}

// ---------------------------------------------------------------------------
// The end of the run
// ---------------------------------------------------------------------------

#[test]
fn the_formation_reaching_the_ship_row_ends_the_run() {
    let mut g = playing(1);
    clear_all_but(&mut g, &[4]);
    g.formation_row = PLAYER_ROW - FORM_ROWS; // bottom row one above the ship
    g.formation_x = RIGHT_EDGE - FORM_W; // the next step is a reversal
    g.dir = Dir::Right;
    g.step_countdown = 1;
    assert!(!g.breached(), "the bottom row is one above the ship");
    g.tick();
    assert!(g.breached());
    assert_eq!(g.state, State::Over);
    assert_eq!(g.cause, Some(Cause::Breach));
}

#[test]
fn a_run_ends_the_moment_the_formation_touches_the_ship_row() {
    let mut g = playing(1);
    // March it down: every reversal descends, so a full sweep is a countdown.
    for _ in 0..4000 {
        g.tick();
        if g.state == State::Over {
            break;
        }
    }
    assert_eq!(g.state, State::Over);
    // A full formation with bombs on it usually takes the ship down first; a
    // run that survives the bombs ends by breach. Both are ends.
    let cause = g.cause.expect("a finished run names its cause");
    assert!(
        cause == Cause::Breach || cause == Cause::Destroyed,
        "unexpected cause {cause:?}"
    );
    if cause == Cause::Breach {
        assert!(g.formation_bottom() >= PLAYER_ROW);
    }
}

#[test]
fn an_alien_touching_a_shield_erodes_it() {
    let mut g = playing(1);
    clear_all_but(&mut g, &[4]);
    // The bottom formation row lands on the shields' bottom row.
    g.formation_row = SHIELD_TOP + SHIELD_H - 1 - (FORM_ROWS - 1);
    let cells0 = g.shield_cells();
    g.step_countdown = 1;
    g.tick();
    assert!(g.shield_cells() < cells0, "the formation ground the shield down");
}

// ---------------------------------------------------------------------------
// Keys, states
// ---------------------------------------------------------------------------

#[test]
fn the_title_screen_starts_on_space_and_ignores_the_game_keys() {
    let mut g = Game::new(1);
    assert_eq!(g.state, State::Ready);
    g.key('d');
    assert_eq!(g.player_x, COLS / 2, "no movement on the title screen");
    g.key(' ');
    assert_eq!(g.state, State::Play);
    g.key('d');
    assert_eq!(g.player_x, COLS / 2 + 2);
}

#[test]
fn pause_freezes_the_run_and_p_resumes() {
    let mut g = playing(1);
    // the pause itself is part of the state, so measure it after pausing
    g.key('p');
    assert_eq!(g.state, State::Pause);
    let before = g.state_hash(); // the pause itself is part of the state
    for _ in 0..20 {
        g.tick();
    }
    assert_eq!(g.state_hash(), before, "a paused run does not advance");
    g.key('d');
    assert_eq!(g.player_x, COLS / 2, "no ship movement while paused");
    g.key('p');
    assert_eq!(g.state, State::Play);
    g.tick();
    assert_ne!(g.state_hash(), before);
}

#[test]
fn q_quits_from_every_state_and_r_restarts_on_the_next_seed() {
    let mut g = playing(1);
    assert!(g.key('q'));
    let mut g2 = playing(1);
    g2.key('p');
    assert!(g2.key('Q'));
    let mut g3 = playing(1);
    g3.key(' ');
    g3.key('d');
    let seed = g3.seed;
    g3.key('r');
    assert_eq!(g3.seed, seed + 1, "the seed family walks on restart");
    assert_eq!(g3.state, State::Play);
    assert_eq!(g3.score, 0);
    assert_eq!(g3.lives, START_LIVES);
    assert_eq!(g3.ticks, 0);
    assert_eq!(g3.wave, 1);
    assert_eq!(g3.alive(), ALIENS);
    assert!(!g3.key('r'), "restart is not a quit");
}

#[test]
fn unknown_keys_are_ignored_and_escape_is_not_a_quit() {
    let mut g = playing(1);
    for c in ['x', 'Z', '1', '\n', '\t', '\x1b', ';', '['] {
        assert!(!g.key(c), "{c:?} must not quit");
    }
    assert_eq!(g.state, State::Play);
}

// ---------------------------------------------------------------------------
// Determinism
// ---------------------------------------------------------------------------

/// A fixed script of keys: a little flight, a lot of firing.
fn demo_script() -> Vec<Option<char>> {
    let mut s = Vec::new();
    for i in 0..900 {
        let k = match i % 11 {
            0 => Some('a'),
            3 => Some('d'),
            5 => Some(' '),
            7 => Some('d'),
            _ => None,
        };
        s.push(k);
    }
    s
}

#[test]
fn the_same_seed_and_the_same_script_produce_the_same_state() {
    let script = demo_script();
    let mut a = playing(11);
    run_script(&mut a, &script);
    let mut b = playing(11);
    run_script(&mut b, &script);
    assert_eq!(a.state_hash(), b.state_hash());
    assert_eq!(a.score, b.score);
    assert_eq!(a.kills, b.kills);
    assert_eq!(a.ticks, b.ticks);
    assert_eq!(plain_state(&a), plain_state(&b));
}

#[test]
fn a_different_seed_produces_a_different_state() {
    let script = demo_script();
    let mut a = playing(11);
    run_script(&mut a, &script);
    let mut b = playing(12);
    run_script(&mut b, &script);
    assert_ne!(a.state_hash(), b.state_hash());
}

#[test]
fn a_different_script_produces_a_different_state_on_the_same_seed() {
    let mut a = playing(11);
    run_script(&mut a, &demo_script());
    let mut b = playing(11);
    let mut s = demo_script();
    s.push(Some('a'));
    run_script(&mut b, &s);
    assert_ne!(a.state_hash(), b.state_hash());
}

#[test]
fn the_autopilot_is_deterministic_and_clears_waves() {
    let mut a = playing(4);
    autopilot(&mut a, 3000);
    let mut b = playing(4);
    autopilot(&mut b, 3000);
    assert_eq!(a.state_hash(), b.state_hash());
    assert!(
        a.waves_cleared >= 1,
        "the autopilot must win something: waves_cleared={}",
        a.waves_cleared
    );
    assert!(a.score > 0);
}

#[test]
fn the_rng_is_a_pure_function_of_the_seed() {
    let mut a = Rng::new(7);
    let mut b = Rng::new(7);
    for _ in 0..50 {
        assert_eq!(a.next_u64(), b.next_u64());
    }
    assert_ne!(Rng::new(7).next_u64(), Rng::new(8).next_u64());
    // xorshift must never sit on 0, whatever the seed.
    for seed in [0u64, 1, u64::MAX] {
        assert_ne!(Rng::new(seed).state(), 0);
    }
    for seed in 0..40u64 {
        let mut r = Rng::new(seed);
        for _ in 0..20 {
            assert!(r.below(10_000) < 10_000);
        }
    }
}

// ---------------------------------------------------------------------------
// Rendering: a pure function of state
// ---------------------------------------------------------------------------

#[test]
fn the_frame_is_a_pure_function_of_state() {
    let g = playing(5);
    assert_eq!(frame(&g), frame(&g));
    let mut h = playing(5);
    run_script(&mut h, &demo_script());
    assert_eq!(frame(&h), frame(&h));
    assert_ne!(frame(&g), frame(&h), "the frame follows the state");
}

/// Every row of a frame is exactly `width` visible columns, and there are
/// exactly `height` rows.
fn assert_window(frame: &str, width: usize, height: usize) {
    let lines: Vec<&str> = frame.split('\n').collect();
    assert_eq!(lines.len(), height + 1, "exactly {height} rows plus the final newline: {lines:?}");
    for (i, line) in lines[..height].iter().enumerate() {
        let visible = strip_sgr(line).chars().count();
        assert_eq!(visible, width, "row {i} is {visible} columns, not {width}");
    }
}

#[test]
fn the_frame_fits_80x24_and_80x28_exactly() {
    let g = playing(5);
    assert_window(&g.render(RenderOpts { width: 80, height: 24, mono: true, color: false }), 80, 24);
    assert_window(&g.render(RenderOpts { width: 80, height: 28, mono: false, color: true }), 80, 28);
    assert_window(&g.render(RenderOpts::default()), 80, 28);
    assert_window(&g.render(RenderOpts { width: 60, height: 24, mono: true, color: false }), 60, 24);
    assert_window(&g.render(RenderOpts { width: 40, height: 12, mono: true, color: false }), 40, 12);
    assert_window(&g.render(RenderOpts { width: 20, height: 5, mono: false, color: true }), 20, 5);
}

#[test]
fn a_small_window_drops_the_border_and_the_score_panel_not_the_field() {
    let g = playing(5);
    let full = g.render(RenderOpts { width: 80, height: 28, mono: true, color: false });
    let small = g.render(RenderOpts { width: 80, height: 24, mono: true, color: false });
    // The full chrome carries the border and the legend.
    assert!(full.contains("+--"), "the border is drawn at 80x28");
    assert!(full.contains("THAW INVADERS"), "the title is drawn at 80x28");
    assert!(full.contains("q quit"), "the legend is drawn at 80x28");
    // At 80x24 the border and the title are gone, the field is not, and the
    // HUD rides on the field's top row.
    assert!(!small.contains("+--"), "the border is dropped at 80x24");
    assert!(!small.contains("THAW INVADERS"), "the title is dropped at 80x24");
    assert!(small.contains("score"), "the score is still on screen at 80x24");
    assert!(small.contains('E'), "the formation is on screen at 80x24");
    assert!(small.contains("-+-"), "the ship is on screen at 80x24");
    // A window shorter than the field clips it from the bottom and keeps the
    // HUD, so the player always sees the score.
    let tiny = g.render(RenderOpts { width: 80, height: 8, mono: true, color: false });
    assert!(tiny.contains("score"));
}

#[test]
fn mono_is_pure_ascii_and_color_is_only_escapes() {
    let g = playing(5);
    let mono = g.render(RenderOpts { width: 80, height: 24, mono: true, color: false });
    assert!(
        mono.chars().all(|c| c.is_ascii()),
        "--mono must be ASCII-only"
    );
    assert!(!mono.contains('\x1b'), "--mono draws no colour");
    let colour = g.render(RenderOpts { width: 80, height: 24, mono: false, color: true });
    assert!(colour.contains('\x1b'), "colour is on by default");
    let plain = g.render(RenderOpts { width: 80, height: 24, mono: false, color: false });
    assert_eq!(strip_sgr(&colour), plain, "colour adds no visible characters");
    // The plain-text dump is ASCII too.
    assert!(plain_state(&g).chars().all(|c| c.is_ascii()));
    assert!(field_text(&g).chars().all(|c| c.is_ascii()));
}

#[test]
fn strip_sgr_removes_only_the_escapes() {
    assert_eq!(strip_sgr("\x1b[31mabc\x1b[0m"), "abc");
    assert_eq!(strip_sgr("no escapes"), "no escapes");
    assert_eq!(strip_sgr(""), "");
    assert_eq!(strip_sgr("\x1b[H"), "");
}

#[test]
fn the_result_key_values_cover_the_contract() {
    let g = playing(5);
    let kv = g.result_kv();
    for key in [
        "state", "seed", "ticks", "wave", "score", "lives", "aliens", "kills", "shots", "bombs",
        "shield_cells", "player_x", "formation_x", "formation_row", "period", "best_score",
        "best_wave", "hash",
    ] {
        assert!(
            kv.iter().any(|(k, _)| k == key),
            "result_kv is missing {key}"
        );
    }
    assert!(kv.iter().all(|(_, v)| v.chars().all(|c| c.is_ascii())));
    let s = plain_state(&g);
    assert!(s.contains("state=play"));
    assert!(s.contains("field:"));
}
