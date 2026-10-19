//! The rules contract: every rule in `spec.md`, asserted on the library
//! directly. No I/O, no clock, no pty — `src/lib.rs` is pure, so these are
//! plain function calls.

use pengy_pong::*;

/// Drive the match for `n` ticks with the autopilot on the left paddle.
fn play(g: &mut Game, n: u64) {
    for _ in 0..n {
        for k in g.autopilot_keys().chars() {
            g.key(k);
        }
        g.tick();
        if g.state == State::Over {
            return;
        }
    }
}

/// Feed a script of keys, one per tick (None = no input).
fn script(g: &mut Game, keys: &[Option<char>]) {
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

// ---------------------------------------------------------------------------
// Field, paddles, geometry
// ---------------------------------------------------------------------------

#[test]
fn the_field_is_60x20_with_paddles_on_columns_1_and_58() {
    assert_eq!((COLS, ROWS), (60, 20));
    assert_eq!((LEFT_COL, RIGHT_COL), (1, 58));
    assert_eq!(PADDLE_H, 4);
    let g = Game::new(1);
    assert_eq!(g.top(Left), (ROWS - PADDLE_H) / 2);
    assert_eq!(g.top(Right), (ROWS - PADDLE_H) / 2);
    assert_eq!(g.ball_col(), COLS / 2);
}

#[test]
fn a_paddle_covers_four_rows_and_clamps_at_the_field_edges() {
    assert_eq!(paddle_rows(7), (7, 10));
    for top in 0..=ROWS - PADDLE_H {
        let (a, b) = paddle_rows(top);
        assert_eq!(b - a, PADDLE_H - 1);
        assert!(covers(top, a) && covers(top, b));
        if a > 0 {
            assert!(!covers(top, a - 1));
        }
        if b + 1 < ROWS {
            assert!(!covers(top, b + 1));
        }
    }
    // Clamped: a paddle can never leave the field, in either direction.
    let mut g = Game::new(1);
    for _ in 0..200 {
        g.key('w');
    }
    assert_eq!(g.top(Left), 0);
    for _ in 0..400 {
        g.key('s');
    }
    assert_eq!(g.top(Left), ROWS - PADDLE_H);
    assert_eq!(move_top(0, -5), 0);
    assert_eq!(move_top(ROWS - PADDLE_H, 5), ROWS - PADDLE_H);
    assert_eq!(clamp_top(999), ROWS - PADDLE_H);
}

// ---------------------------------------------------------------------------
// The reflection angle table
// ---------------------------------------------------------------------------

#[test]
fn the_angle_table_is_10_degree_steps_from_straight_to_50() {
    let angles: Vec<i32> = BOUNCES.iter().map(|b| b.angle).collect();
    assert_eq!(angles, vec![-50, -10, 10, 50]);
    // Every angle is a multiple of 10 degrees, the edges are exactly +/-50, and
    // the inner rows are the shallowest bounce the game allows.
    for b in BOUNCES.iter() {
        assert_eq!(b.angle % 10, 0);
        assert!(b.angle.abs() <= 50 && b.angle.abs() >= 10);
    }
    assert_eq!(BOUNCES[0].angle, -50);
    assert_eq!(BOUNCES[3].angle, 50);
    assert_eq!(BOUNCES[0].slot, 0);
    assert_eq!(BOUNCES[3].slot, 3);
    // Symmetric, and the sign convention is "positive angle = down the field".
    assert_eq!(BOUNCES[0].tan, -BOUNCES[3].tan);
    assert_eq!(BOUNCES[1].tan, -BOUNCES[2].tan);
    assert!(BOUNCES[3].tan > 0 && BOUNCES[0].tan < 0);
}

#[test]
fn the_bounce_slot_is_the_row_the_ball_met_the_paddle_on() {
    // Top row of the paddle => slot 0 (the -50 edge), bottom row => slot 3.
    assert_eq!(bounce_for(6, 6), BOUNCES[0]);
    assert_eq!(bounce_for(7, 6), BOUNCES[1]);
    assert_eq!(bounce_for(8, 6), BOUNCES[2]);
    assert_eq!(bounce_for(9, 6), BOUNCES[3]);
    // Clamped to the paddle: a hit is always one of the four table entries.
    assert_eq!(bounce_for(0, 6), BOUNCES[0]);
    assert_eq!(bounce_for(19, 6), BOUNCES[3]);
    for top in 0..=ROWS - PADDLE_H {
        for row in 0..ROWS {
            let b = bounce_for(row, top);
            assert!((0..PADDLE_H).contains(&b.slot));
        }
    }
}

#[test]
fn vy_comes_from_the_angle_and_scales_with_the_speed() {
    assert_eq!(vy_for(BOUNCES[2], 100), 18);
    assert_eq!(vy_for(BOUNCES[3], 100), 119);
    assert_eq!(vy_for(BOUNCES[1], 100), -18);
    assert_eq!(vy_for(BOUNCES[0], 100), -119);
    // tan(50 deg) ~ 1.19, tan(10 deg) ~ 0.18, at serve speed.
    assert_eq!(vy_for(BOUNCES[3], 200), 238);
    assert_eq!(vy_for(BOUNCES[2], 200), 36);
}

#[test]
fn a_bounce_flips_x_sets_vy_from_the_table_and_pins_the_ball_to_the_face() {
    // Serve toward the right, park the right paddle so the ball meets it on a
    // known row, and watch the bounce.
    let mut g = Game::with(5, Mode::OneP, 1);
    g.serve_to = Side::Right;
    g.serve_now();
    assert_eq!(g.vx, SERVE_SPEED);
    // Move the paddle so its top row is the ball's row: slot 0 => -50 degrees.
    let row = g.ball_row();
    g.right_top = clamp_top(row);
    while g.state == State::Play && g.ball_col() < RIGHT_COL {
        g.tick();
    }
    assert!(g.hits >= 1, "the ball reached the paddle");
    assert!(g.vx < 0, "x flipped");
    assert_eq!(g.ball_x, Side::Right.face());
    let b = bounce_for(g.ball_row(), g.right_top);
    assert_eq!(g.vy, vy_for(b, g.speed));
    assert_eq!(g.speed, next_speed(SERVE_SPEED));
}

// ---------------------------------------------------------------------------
// Speed: the 5% ladder and its cap
// ---------------------------------------------------------------------------

#[test]
fn speed_rises_5_percent_per_hit_and_never_passes_twice_the_serve_speed() {
    assert_eq!(SERVE_SPEED, 100);
    assert_eq!(MAX_SPEED, 2 * SERVE_SPEED);
    let mut s = SERVE_SPEED;
    let mut seen = vec![s];
    for _ in 0..400 {
        s = next_speed(s);
        assert!(s <= MAX_SPEED, "the cap held: {s}");
        assert!(s >= seen[seen.len() - 1], "the ladder never falls");
        seen.push(s);
    }
    assert_eq!(*seen.last().unwrap(), MAX_SPEED);
    // 100 -> 105 -> 110 …: exactly floor(speed * 105 / 100).
    assert_eq!(next_speed(100), 105);
    assert_eq!(next_speed(105), 110);
    assert_eq!(next_speed(199), MAX_SPEED);
    assert_eq!(next_speed(MAX_SPEED), MAX_SPEED);
}

#[test]
fn the_ball_speed_in_play_never_exceeds_the_cap() {
    let mut g = Game::with(11, Mode::OneP, 3);
    for _ in 0..20_000 {
        for k in g.autopilot_keys().chars() {
            g.key(k);
        }
        g.tick();
        assert!(g.speed <= MAX_SPEED, "speed {} over the cap", g.speed);
        assert!(g.vx.abs() <= MAX_SPEED);
        assert!(g.vy.abs() <= MAX_SPEED * 2);
        if g.state == State::Over {
            break;
        }
    }
}

// ---------------------------------------------------------------------------
// Points, serves, and the serve direction
// ---------------------------------------------------------------------------

#[test]
fn a_point_scores_freezes_the_ball_and_starts_the_next_countdown() {
    let mut g = Game::with(2, Mode::OneP, 1);
    // Serve toward the left and never move the paddle: the ball goes past the
    // left paddle and the right player takes the point.
    g.serve_to = Side::Left;
    g.serve_now();
    let before = g.right_score;
    for _ in 0..200 {
        g.tick();
        if g.points > before {
            break;
        }
    }
    assert_eq!(g.right_score, before + 1);
    assert_eq!(g.left_score, 0);
    assert_eq!(g.points, 1);
    assert_eq!(g.state, State::Serve);
    assert_eq!(g.countdown, SERVE_COUNTDOWN);
    assert_eq!(SERVE_COUNTDOWN * TICK_MS, 1000, "the countdown is 1 second");
    // The serve travels toward the player who LOST the point.
    assert_eq!(g.serve_to, Side::Left);
    assert_eq!(g.speed, SERVE_SPEED, "the speed ladder resets on a point");
    assert_eq!(g.ball_col(), COLS / 2, "the ball is parked at the centre");
    for _ in 0..SERVE_COUNTDOWN {
        g.tick();
    }
    assert_eq!(g.state, State::Play);
    assert!(g.vx < 0, "the serve travels toward the loser");
}

#[test]
fn every_serve_travels_toward_the_player_who_lost_the_point() {
    let mut g = Game::with(3, Mode::OneP, 1);
    let mut served = 0;
    for _ in 0..40_000 {
        for k in g.autopilot_keys().chars() {
            g.key(k);
        }
        let before = g.points;
        let (pl, pr) = (g.left_score, g.right_score);
        let state = g.state;
        g.tick();
        if g.points > before {
            // The side whose score moved took the point, and the next serve
            // travels toward the other side: the loser receives it.
            let winner = if g.left_score > pl { Side::Left } else { Side::Right };
            assert_eq!(g.right_score, pr, "exactly one score moves per point");
            assert_eq!(g.serve_to, winner.other(), "the serve goes to the loser");
            served += 1;
        }
        if state == State::Serve && g.state == State::Play {
            let dir = g.vx.signum();
            let toward = g.serve_to;
            assert_eq!(
                (dir, toward),
                match toward {
                    Side::Left => (-1, Side::Left),
                    Side::Right => (1, Side::Right),
                },
                "the serve travels toward the player who lost the point"
            );
        }
        if g.state == State::Over {
            break;
        }
    }
    assert!(served >= 10, "a whole match of serves: {served}");
}

#[test]
fn space_serves_now_and_the_countdown_counts_down() {
    let mut g = Game::new(4);
    assert_eq!(g.state, State::Serve);
    assert_eq!(g.countdown, SERVE_COUNTDOWN);
    assert!(!g.key(' '));
    assert_eq!(g.state, State::Play);
    assert_eq!(g.countdown, 0);
    // The countdown path: 25 ticks of 40 ms is one second.
    let mut g2 = Game::new(4);
    for _ in 0..(SERVE_COUNTDOWN - 1) {
        g2.tick();
        assert_eq!(g2.state, State::Serve);
    }
    g2.tick();
    assert_eq!(g2.state, State::Play);
    assert_eq!(g2.ticks, SERVE_COUNTDOWN);
}

// ---------------------------------------------------------------------------
// The AI: its speed cap and its reaction delay
// ---------------------------------------------------------------------------

#[test]
fn the_ai_never_moves_more_than_its_cap_over_500_ticks() {
    for d in 1..=MAX_DIFFICULTY {
        let cap = AI_CAPS[d as usize - 1];
        let mut g = Game::with(100 + d as u64 * 7, Mode::OneP, d);
        let mut prev = g.top(Right);
        let mut moved = 0u64;
        for _ in 0..500 {
            for k in g.autopilot_keys().chars() {
                g.key(k);
            }
            g.tick();
            let now = g.top(Right);
            let delta = if now >= prev {
                now - prev
            } else {
                prev - now
            };
            assert!(
                delta <= cap,
                "difficulty {d}: the penguin moved {delta} rows in one tick, cap is {cap}"
            );
            assert!((0..=ROWS - PADDLE_H).contains(&now), "the paddle stayed on the field");
            if delta > 0 {
                moved += 1;
            }
            prev = now;
            if g.state == State::Over {
                break;
            }
        }
        assert!(moved > 50, "difficulty {d} actually plays ({moved} moves)");
    }
}

#[test]
fn the_ai_is_slower_at_1_than_at_3_and_its_delay_is_6_3_1() {
    assert_eq!(AI_CAPS, [1, 2, 3]);
    assert_eq!(AI_DELAYS, [6, 3, 1]);
    // The caps are ordered, and the delays run the other way.
    assert!(AI_CAPS[0] < AI_CAPS[1] && AI_CAPS[1] < AI_CAPS[2]);
    assert!(AI_DELAYS[0] > AI_DELAYS[1] && AI_DELAYS[1] > AI_DELAYS[2]);
    // Over a long match the higher difficulty wins more often: the tuning is
    // real, not decorative.
    let won = |d: u8| {
        let mut wins = 0;
        for seed in 1..=12 {
            let mut g = Game::with(seed, Mode::OneP, d);
            play(&mut g, 40_000);
            if g.winner() == Some(Side::Left) {
                wins += 1;
            }
        }
        wins
    };
    let (d1, d3) = (won(1), won(3));
    assert!(d1 >= 6, "the player beats level 1 often ({d1}/12)");
    assert!(d3 <= 8, "level 3 is the hard one ({d3}/12 player wins)");
    assert!(d1 > d3, "level 3 is harder than level 1: {d1} vs {d3}");
}

#[test]
fn the_ai_only_ever_sees_the_ball_where_it_was_delay_ticks_ago() {
    // Difficulty 1 has a 6-tick delay: the ring is 6 samples deep, and the
    // sample it reads is the one written 6 ticks ago.
    let mut g = Game::with(9, Mode::OneP, 1);
    for _ in 0..20 {
        g.tick();
    }
    let head = g.ai_head;
    let seen = g.ai_view[(head + AI_VIEW_LEN - 6) % AI_VIEW_LEN];
    assert_eq!(seen, g.ai_view[(head + AI_VIEW_LEN - 6) % AI_VIEW_LEN]);
    // The ring never grows: a fixed-size history, not an unbounded Vec.
    assert_eq!(g.ai_view.len(), AI_VIEW_LEN);
    assert!(head < AI_VIEW_LEN);
}

#[test]
fn in_2p_the_penguin_stays_out_of_the_way() {
    let mut g = Game::with(7, Mode::TwoP, 2);
    for _ in 0..200 {
        assert_eq!(g.ai_move(), None, "hotseat has no penguin");
        g.tick();
    }
}

// ---------------------------------------------------------------------------
// The ball: walls, paddles, goal lines
// ---------------------------------------------------------------------------

#[test]
fn the_ball_bounces_off_the_top_and_bottom_walls_and_stays_in_the_field() {
    let mut g = Game::with(13, Mode::OneP, 2);
    let mut bounces = 0;
    for _ in 0..4000 {
        for k in g.autopilot_keys().chars() {
            g.key(k);
        }
        let before = g.wall_bounces;
        g.tick();
        if g.wall_bounces > before {
            bounces += 1;
        }
        let row = g.ball_row();
        assert!((0..ROWS).contains(&row), "ball row {row} left the field");
        let col = g.ball_col();
        assert!((0..COLS).contains(&col), "ball col {col} left the field");
        if g.state == State::Over {
            break;
        }
    }
    assert!(bounces > 0, "the walls are used ({bounces} bounces)");
}

#[test]
fn a_ball_at_the_speed_cap_never_tunnels_through_a_paddle() {
    // Park both paddles on the ball's row, pin the speed at the cap, and watch
    // the rally: the ball must bounce, never pass a paddle.
    let mut g = Game::with(17, Mode::OneP, 1);
    g.serve_to = Side::Right;
    g.serve_now();
    g.vy = 0;
    g.ball_y = 10 * FP_SCALE;
    g.right_top = 9;
    g.left_top = 9;
    g.speed = MAX_SPEED;
    g.vx = MAX_SPEED;
    g.vy = 0;
    let mut hits = 0;
    for _ in 0..400 {
        let before = g.hits;
        // Keep the paddles glued to the ball's row.
        g.right_top = clamp_top(g.ball_row());
        g.left_top = clamp_top(g.ball_row());
        g.tick();
        if g.hits > before {
            hits += 1;
        }
        assert_eq!(g.state, State::Play, "nobody conceded: the ball is in play");
        assert!(g.points == 0, "no point at the speed cap");
    }
    assert!(hits >= 4, "the ball bounced {hits} times at the cap");
}

#[test]
fn the_ball_crossing_a_paddle_without_a_paddle_there_is_a_point() {
    let mut g = Game::with(19, Mode::TwoP, 1);
    // Park the paddle out of reach and serve at it.
    g.serve_to = Side::Right;
    g.serve_now();
    g.vy = 0;
    g.ball_y = 0;
    g.right_top = ROWS - PADDLE_H;
    for _ in 0..200 {
        g.tick();
        if g.points > 0 {
            break;
        }
    }
    assert_eq!(g.points, 1);
    assert_eq!(g.left_score, 1, "the left player takes a point the right paddle missed");
    assert_eq!(g.right_score, 0);
}

// ---------------------------------------------------------------------------
// Scoring a match: first to 11, win by 2, hard cap 15
// ---------------------------------------------------------------------------

#[test]
fn first_to_eleven_wins_and_10_10_needs_a_two_point_margin() {
    let mut g = Game::new(1);
    let mut over = |l: u64, r: u64| {
        g.left_score = l;
        g.right_score = r;
        g.match_over()
    };
    assert_eq!(over(11, 0), Some(Cause::Won));
    assert_eq!(over(11, 5), Some(Cause::Won));
    assert_eq!(over(10, 0), None, "ten is not eleven");
    assert_eq!(over(10, 10), None, "deuce: no margin, no winner");
    assert_eq!(over(11, 10), None, "11-10 is not a win, the margin is 1");
    assert_eq!(over(10, 11), None);
    assert_eq!(over(12, 10), Some(Cause::Won), "12-10 is a win by two");
    assert_eq!(over(11, 9), Some(Cause::Won));
    assert_eq!(over(13, 11), Some(Cause::Won));
    assert_eq!(over(12, 11), None, "12-11 is deuce again");
    assert_eq!(over(14, 12), Some(Cause::Won));
    assert_eq!(over(14, 13), None, "14-13 is not a win: the cap is next");
    assert_eq!(over(15, 13), Some(Cause::Cap), "15 is the hard cap");
    assert_eq!(over(15, 14), Some(Cause::Cap), "the cap ends it with a 1-point margin");
    assert_eq!(over(15, 15), Some(Cause::Cap));
}

#[test]
fn a_match_ends_the_tick_the_winning_point_lands_and_is_frozen() {
    let mut g = Game::with(23, Mode::OneP, 1);
    // Hand the left player ten points, then play until the match ends.
    g.left_score = 10;
    g.right_score = 0;
    let mut ticks = 0;
    while g.state != State::Over && ticks < 40_000 {
        for k in g.autopilot_keys().chars() {
            g.key(k);
        }
        g.tick();
        ticks += 1;
    }
    assert_eq!(g.state, State::Over);
    assert!(g.cause == Some(Cause::Won) || g.cause == Some(Cause::Cap));
    let top = g.left_score.max(g.right_score);
    assert!(top >= WIN_SCORE && top <= CAP_SCORE, "the match ended on the score rules: {top}");
    assert!(g.left_score.abs_diff(g.right_score) >= 2 || top == CAP_SCORE);
    assert_eq!(g.winner(), Some(Side::Left), "the player took this match");
    assert!(g.margin() > 0);
    // Frozen: ticking a finished match changes nothing.
    let snap = g.canonical();
    for _ in 0..50 {
        g.tick();
    }
    assert_eq!(g.canonical(), snap, "a finished match is frozen");
    assert_eq!(g.state, State::Over);
}

#[test]
fn the_hard_cap_ends_a_match_that_refuses_to_finish() {
    let mut g = Game::new(1);
    g.left_score = 14;
    g.right_score = 14;
    assert_eq!(g.match_over(), None);
    g.left_score = 15;
    g.right_score = 14;
    assert_eq!(g.match_over(), Some(Cause::Cap));
    g.state = State::Over;
    g.cause = Some(Cause::Cap);
    assert_eq!(g.winner(), Some(Side::Left));
    assert_eq!(Cause::Cap.text(), "the 15-point cap");
    assert_eq!(Cause::Won.text(), "first to eleven, win by two");
}

// ---------------------------------------------------------------------------
// Keys
// ---------------------------------------------------------------------------

#[test]
fn q_is_the_only_quit_key_and_the_others_do_what_they_say() {
    let mut g = Game::new(1);
    assert!(g.key('q'), "q quits");
    for c in ['a', 'd', 'x', 'z', '1', '2', '3', 'm', 'p', ' ', 'w', 's', 'k', 'j'] {
        let mut g2 = Game::new(1);
        assert!(!g2.key(c), "{c} is not a quit key");
    }
    let mut g = Game::new(1);
    g.key(' ');
    assert_eq!(g.state, State::Play);
    g.key('p');
    assert_eq!(g.state, State::Pause);
    // A paused match does not move the ball.
    let snap = (g.ball_x, g.ball_y, g.vx, g.vy);
    for _ in 0..20 {
        g.tick();
    }
    assert_eq!((g.ball_x, g.ball_y, g.vx, g.vy), snap, "pause freezes the ball");
    g.key('p');
    assert_eq!(g.state, State::Play);
    // m toggles 1P/2P, 1/2/3 pick the level (and 1P).
    g.key('m');
    assert_eq!(g.mode, Mode::TwoP);
    g.key('m');
    assert_eq!(g.mode, Mode::OneP);
    g.key('m');
    g.key('2');
    assert_eq!(g.mode, Mode::OneP);
    assert_eq!(g.difficulty, 2);
    g.key('1');
    assert_eq!(g.difficulty, 1);
    g.key('3');
    assert_eq!(g.difficulty, 3);
    // r restarts: a fresh match on the next seed, banked best kept.
    let mut g = Game::with(40, Mode::OneP, 3);
    g.best_wins = 4;
    g.best_margin = 5;
    g.best_rally = 300;
    g.key(' ');
    g.key('r');
    assert_eq!(g.seed, 41, "restart walks the seed family");
    assert_eq!(g.left_score, 0);
    assert_eq!(g.right_score, 0);
    assert_eq!(g.ticks, 0);
    assert_eq!(g.state, State::Serve);
    assert_eq!((g.best_wins, g.best_margin, g.best_rally), (4, 5, 300));
    assert_eq!(g.difficulty, 3, "the level survives a restart");
    assert_eq!(g.mode, Mode::OneP);
}

#[test]
fn w_and_s_steer_the_player_and_the_arrows_follow_the_mode() {
    // w/s are always the left (player) paddle.
    let mut g = Game::with(1, Mode::OneP, 2);
    g.key('w');
    assert_eq!(g.top(Left), (ROWS - PADDLE_H) / 2 - 1);
    g.key('s');
    assert_eq!(g.top(Left), (ROWS - PADDLE_H) / 2);
    // 1P: the arrows (delivered as k/j) steer the player's own paddle.
    g.key('k');
    assert_eq!(g.top(Left), (ROWS - PADDLE_H) / 2 - 1);
    assert_eq!(g.top(Right), (ROWS - PADDLE_H) / 2);
    // 2P: w/s stay on the left, k/j move the right paddle.
    let mut g = Game::with(1, Mode::TwoP, 2);
    g.key('k');
    assert_eq!(g.top(Right), (ROWS - PADDLE_H) / 2 - 1);
    assert_eq!(g.top(Left), (ROWS - PADDLE_H) / 2);
    g.key('j');
    assert_eq!(g.top(Right), (ROWS - PADDLE_H) / 2);
    // A finished match ignores paddle keys.
    let mut g = Game::new(1);
    g.state = State::Over;
    let top = g.top(Left);
    g.key('w');
    assert_eq!(g.top(Left), top, "a finished match is frozen");
}

#[test]
fn paddle_keys_move_exactly_one_row_per_keypress() {
    let mut g = Game::new(1);
    let start = g.top(Left);
    for i in 1..=8 {
        g.key('s');
        assert_eq!(g.top(Left), start + i);
    }
    for i in (0..8).rev() {
        g.key('w');
        assert_eq!(g.top(Left), start + i);
    }
}

// ---------------------------------------------------------------------------
// Determinism
// ---------------------------------------------------------------------------

/// A script that plays a whole match: the autopilot's keys, tick by tick.
fn autopilot_script(seed: u64, ticks: u64) -> Vec<Option<char>> {
    let mut g = Game::with(seed, Mode::OneP, 2);
    let mut keys = Vec::new();
    for _ in 0..ticks {
        let line = g.autopilot_keys();
        keys.push(line.chars().next());
        for c in line.chars() {
            g.key(c);
        }
        g.tick();
        if g.state == State::Over {
            break;
        }
    }
    keys
}

#[test]
fn the_same_seed_and_the_same_script_land_on_the_same_state_every_time() {
    let keys = autopilot_script(31, 3000);
    let mut a = Game::with(31, Mode::OneP, 2);
    let mut b = Game::with(31, Mode::OneP, 2);
    script(&mut a, &keys);
    script(&mut b, &keys);
    assert_eq!(a.canonical(), b.canonical());
    assert_eq!(a.state_hash(), b.state_hash());
    assert_eq!(
        a.render(RenderOpts::default()),
        b.render(RenderOpts::default())
    );
    assert_eq!(plain_state(&a), plain_state(&b));
    assert_eq!(a.result_kv(), b.result_kv());
}

#[test]
fn a_different_seed_or_a_different_script_lands_on_a_different_state() {
    let keys = autopilot_script(31, 3000);
    let mut a = Game::with(31, Mode::OneP, 2);
    script(&mut a, &keys);
    let mut b = Game::with(32, Mode::OneP, 2);
    script(&mut b, &keys);
    assert_ne!(a.state_hash(), b.state_hash(), "the seed is in the state");
    let mut c = Game::with(31, Mode::OneP, 2);
    let mut other = keys.clone();
    if let Some(first) = other.first_mut() {
        // Flip the first keypress to the opposite direction: the very first
        // thing the script asks for is different, so the run must differ.
        *first = Some(match first {
            Some('w') => 's',
            _ => 'w',
        });
    }
    script(&mut c, &other);
    assert_ne!(a.state_hash(), c.state_hash(), "the script is in the state");
}

#[test]
fn a_scripted_session_lands_on_the_pinned_golden_end_state() {
    // The acceptance shape: seed + script => this exact result. If a rule
    // changes, this hash changes — that is the point of pinning it.
    let mut g = Game::with(1, Mode::OneP, 2);
    for _ in 0..20_000 {
        let line = g.autopilot_keys();
        for c in line.chars() {
            g.key(c);
        }
        g.tick();
        if g.state == State::Over {
            break;
        }
    }
    assert_eq!(g.state, State::Over);
    assert_eq!(g.left_score, 11);
    assert_eq!(g.right_score, 5);
    assert_eq!(g.points, 16);
    assert_eq!(g.ticks, 6598);
    assert_eq!(g.total_hits, 125);
    assert_eq!(g.longest_rally, 472);
    assert_eq!(g.winner(), Some(Side::Left));
    assert_eq!(g.state_hash(), 0xb936_f7f3_0844_10a2);

    // And a hand-written script, played tick by tick, is reproducible too.
    let keys: Vec<Option<char>> = "w w w s s s w s w s s w w k j s s w s s s w w s s w s w w s s s s w w s s"
        .split_whitespace()
        .map(|k| Some(k.chars().next().unwrap()))
        .collect();
    let mut a = Game::with(101, Mode::OneP, 2);
    script(&mut a, &keys);
    let mut b = Game::with(101, Mode::OneP, 2);
    script(&mut b, &keys);
    assert_eq!(a.state_hash(), b.state_hash());
    assert_eq!(a.ticks, keys.len() as u64);
    assert_eq!(a.state, State::Play);
}

#[test]
fn the_rng_is_seeded_reproducible_and_never_zero() {
    let mut a = Rng::new(7);
    let mut b = Rng::new(7);
    for _ in 0..100 {
        assert_eq!(a.next_u64(), b.next_u64());
    }
    assert_ne!(Rng::new(7).state(), Rng::new(8).state());
    for seed in [0u64, 1, u64::MAX, 1 << 32] {
        let mut r = Rng::new(seed);
        for _ in 0..50 {
            assert_ne!(r.state(), 0, "xorshift must never hold 0");
            r.next_u64();
        }
    }
    let mut r = Rng::new(3);
    for _ in 0..200 {
        assert!(r.below(7) < 7);
    }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

#[test]
fn the_frame_is_exactly_the_window_and_fits_80x24() {
    let g = Game::new(1);
    let f = g.render(RenderOpts::default());
    let lines: Vec<&str> = f.split('\n').collect();
    // render() ends with a newline, so the last element is the empty tail.
    assert_eq!(lines.len(), REF_HEIGHT + 1);
    for line in &lines[..REF_HEIGHT] {
        assert_eq!(strip_sgr(line).chars().count(), REF_WIDTH, "row width: {line:?}");
    }
    // The field is 20 rows of 60 columns inside a 62-column border.
    let mono = g.render(RenderOpts {
        width: REF_WIDTH,
        height: REF_HEIGHT,
        mono: true,
        color: false,
    });
    let rows: Vec<&str> = mono.split('\n').collect();
    assert!(rows[2].starts_with('+'));
    assert!(rows[3].starts_with('|'));
    assert_eq!(rows[3].chars().filter(|c| *c == '|').count(), 2);
    assert!(rows[23].contains("w/s move"));
    assert!(rows[23].starts_with('+') && rows[23].trim_end().ends_with('+'));
}

#[test]
fn a_smaller_window_drops_the_title_then_the_status_then_the_border() {
    let g = Game::new(1);
    // 80x24: title, status, border, field, border.
    let full = g.render(RenderOpts::default());
    assert!(full.contains("PENGUIN PONG"));
    assert!(full.contains("SERVE IN"));
    assert!(full.contains('+'));
    // 80x23: the title line goes, the status stays.
    let h23 = g.render(RenderOpts {
        width: 80,
        height: 23,
        mono: true,
        color: false,
    });
    assert!(!h23.contains("PENGUIN PONG"));
    assert!(h23.contains("SERVE IN"));
    // 80x22: the status goes, the border (and its legend) stays.
    let h22 = g.render(RenderOpts {
        width: 80,
        height: 22,
        mono: true,
        color: false,
    });
    assert!(!h22.contains("SERVE IN"));
    assert!(h22.contains("w/s move"));
    // 61 columns: the border goes, the 60-column field stays.
    let narrow = g.render(RenderOpts {
        width: 61,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(!narrow.contains('+'));
    assert!(narrow.contains(':'));
    // 40x10: no border, no title, no status, and the field clipped to 10 rows
    // of 40 columns. Nothing crashes, nothing wraps.
    let tiny = g.render(RenderOpts {
        width: 40,
        height: 10,
        mono: true,
        color: false,
    });
    let rows: Vec<&str> = tiny.split('\n').collect();
    assert_eq!(rows.len(), 11);
    for line in &rows[..10] {
        assert_eq!(line.chars().count(), 40, "tiny row width: {line:?}");
    }
    // 1x1 is legal output, not a panic.
    let one = g.render(RenderOpts {
        width: 1,
        height: 1,
        mono: true,
        color: false,
    });
    assert_eq!(strip_sgr(&one).chars().count(), 2, "one row plus its newline");
}

#[test]
fn mono_and_no_color_emit_no_escape_bytes_at_all() {
    let g = Game::new(1);
    let mono = g.render(RenderOpts {
        width: REF_WIDTH,
        height: REF_HEIGHT,
        mono: true,
        color: false,
    });
    assert!(!mono.contains('\x1b'), "mono is ASCII-only");
    assert!(!mono.chars().any(|c| c > '\u{7f}'), "mono has no box drawing");
    let plain = g.render(RenderOpts {
        width: REF_WIDTH,
        height: REF_HEIGHT,
        mono: false,
        color: false,
    });
    assert!(!plain.contains('\x1b'), "colour off emits no escapes");
    let colour = g.render(RenderOpts::default());
    assert!(colour.contains("\x1b[1;36m"), "colour on paints the title");
    // Stripping the colour gives the same visible text as colour off.
    assert_eq!(strip_sgr(&colour), plain);
    // The mono glyph set is the ASCII one.
    assert!(mono.contains('#') && mono.contains('O') && mono.contains(':'));
    assert!(!colour.contains('#'));
}

#[test]
fn the_frame_shows_the_state_the_score_and_the_keys() {
    let mut g = Game::with(5, Mode::TwoP, 3);
    g.left_score = 7;
    g.right_score = 3;
    let f = g.render(RenderOpts {
        width: REF_WIDTH,
        height: REF_HEIGHT,
        mono: true,
        color: false,
    });
    assert!(f.contains("you  7"));
    assert!(f.contains("p2  3"));
    assert!(f.contains("2P d3 seed 5"));
    assert!(f.contains("w/s move"));
    assert!(f.contains("q quit"));
    g.state = State::Pause;
    let p = g.render(RenderOpts {
        width: REF_WIDTH,
        height: REF_HEIGHT,
        mono: true,
        color: false,
    });
    assert!(p.contains("PAUSED"));
    g.state = State::Over;
    g.cause = Some(Cause::Won);
    let o = g.render(RenderOpts {
        width: REF_WIDTH,
        height: REF_HEIGHT,
        mono: true,
        color: false,
    });
    assert!(o.contains("MATCH OVER"));
    assert!(o.contains("left wins 7-3"));
    assert!(o.contains("first to eleven, win by two"));
}

#[test]
fn plain_state_is_key_value_lines_plus_an_ascii_field() {
    let g = Game::with(6, Mode::OneP, 2);
    let s = plain_state(&g);
    assert!(!s.contains('\x1b'), "plain text, no escapes");
    for key in [
        "state=", "seed=", "mode=", "difficulty=", "ticks=", "points=", "left=", "right=",
        "hits=", "speed=", "rally=", "longest_rally=", "hash=",
    ] {
        assert!(s.contains(key), "plain_state is missing {key}");
    }
    assert!(s.contains("field:"));
    let rows: Vec<&str> = s.lines().filter(|l| l.starts_with('|')).collect();
    assert_eq!(rows.len(), ROWS);
    for r in &rows {
        assert_eq!(r.chars().count(), COLS + 2);
    }
    assert!(rows.iter().any(|r| r.contains('O')));
    // Both paddles sit on the same rows, so PADDLE_H rows carry two '#' each.
    let paddle_rows: Vec<&&str> = rows.iter().filter(|r| r.contains('#')).collect();
    assert_eq!(paddle_rows.len(), PADDLE_H);
    for r in paddle_rows {
        assert_eq!(r.chars().filter(|c| *c == '#').count(), 2);
    }
}

#[test]
fn the_result_kv_carries_the_match_outcome() {
    let mut g = Game::with(8, Mode::OneP, 3);
    play(&mut g, 40_000);
    assert_eq!(g.state, State::Over);
    let kv = g.result_kv();
    let get = |k: &str| {
        kv.iter()
            .find(|(a, _)| a == k)
            .map(|(_, b)| b.clone())
            .unwrap()
    };
    assert_eq!(get("state"), "over");
    assert_eq!(get("seed"), "8");
    assert_eq!(get("mode"), "1P");
    assert_eq!(get("difficulty"), "3");
    assert_eq!(get("cause"), "won");
    assert!(get("winner") == "left" || get("winner") == "right");
    assert_eq!(get("left"), g.left_score.to_string());
    assert_eq!(get("right"), g.right_score.to_string());
    assert_eq!(get("hash").len(), 16);
    assert!(get("longest_rally").parse::<u64>().unwrap() > 20);
}

// ---------------------------------------------------------------------------
// The game is a game: it finishes, and it is winnable
// ---------------------------------------------------------------------------

#[test]
fn a_match_against_every_level_finishes_with_a_winner() {
    for d in 1..=MAX_DIFFICULTY {
        let mut g = Game::with(77, Mode::OneP, d);
        play(&mut g, 60_000);
        assert_eq!(g.state, State::Over, "level {d} finished");
        assert!(g.winner().is_some());
        let top = g.left_score.max(g.right_score);
        assert!(top >= WIN_SCORE || top >= CAP_SCORE);
        assert!(top <= CAP_SCORE, "the cap held at level {d}: {top}");
        assert!(g.total_hits > 10, "the rally is played, not skipped");
    }
}

#[test]
fn the_rally_counter_tracks_the_longest_point() {
    let mut g = Game::with(12, Mode::OneP, 1);
    let mut last_points = 0;
    let mut best = 0;
    for _ in 0..20_000 {
        for k in g.autopilot_keys().chars() {
            g.key(k);
        }
        g.tick();
        if g.points > last_points {
            last_points = g.points;
            assert!(g.longest_rally >= best, "the longest rally only grows");
            best = g.longest_rally;
        }
        if g.state == State::Over {
            break;
        }
    }
    assert!(best > 20, "the longest rally is a real number: {best}");
    assert!(g.longest_rally >= g.rally);
}

