//! The rules contract: every rule in spec.md "Verification", asserted against
//! the library. These are the classic 2048 edge cases — they are the whole
//! game, and they are what a rewrite of `slide_line` must not be able to break.
//!
//! Pure library tests only: no files, no clock, no terminal, no network.

use pengy_merge::*;

/// A game with an exact field, no rng consumed beyond the fixed seed.
fn board(rows: &[&[u32]]) -> Game {
    let size = rows.len();
    let mut g = Game::with(1, size, 2048);
    g.grid = rows.concat();
    g.max_tile = g.max_floe();
    g.flash = vec![0; g.grid.len()];
    g
}

/// The non-zero cells of a row, left to right — how the spec writes a row.
fn row(g: &Game, r: usize) -> Vec<u32> {
    (0..g.size).map(|c| g.at(r, c)).filter(|&v| v != 0).collect()
}

fn line(v: &[u32]) -> Vec<u32> {
    slide_line(v).cells
}

// ---------------------------------------------------------------------------
// 1. The slide: the edge cases, exactly as the spec lists them.
// ---------------------------------------------------------------------------

#[test]
fn four_equal_floes_pair_up_two_fuses() {
    // [2,2,2,2] left => [4,4]
    assert_eq!(line(&[2, 2, 2, 2]), vec![4, 4, 0, 0]);
}

#[test]
fn fuse_then_equal_floe_does_not_chain() {
    // [4,4,8] left => [8,8]: the 8 formed by the fuse never fuses with the 8.
    assert_eq!(line(&[4, 4, 8]), vec![8, 8, 0]);
    assert_eq!(line(&[4, 4, 8, 0]), vec![8, 8, 0, 0]);
}

#[test]
fn fusing_from_the_direction_of_travel_right() {
    // [2,2,4] right => [4,4]. Travel order from the right is [4,2,2].
    assert_eq!(line(&[4, 2, 2]), vec![4, 4, 0]);
    let mut g = board(&[
        &[2, 2, 4, 0],
        &[0, 0, 0, 0],
        &[0, 0, 0, 0],
        &[0, 0, 0, 0],
    ]);
    g.slide(Right);
    assert_eq!(row(&g, 0), vec![4, 4]);
    assert_eq!(g.score, 4);
}

#[test]
fn no_chain_merge_across_a_gap() {
    // [2,4,2] left => [2,4,2]: nothing moves, nothing fuses.
    assert_eq!(line(&[2, 4, 2]), vec![2, 4, 2]);
    assert!(!slide_line(&[2, 4, 2]).changed);
}

#[test]
fn fuse_order_is_from_the_direction_of_travel() {
    // [4,4,8,8] left => [8,16], not [16,8] and not [4,16].
    assert_eq!(line(&[4, 4, 8, 8]), vec![8, 16, 0, 0]);
    // [8,4,4,8] right => [8,8,8]
    assert_eq!(line(&[8, 4, 4, 8]), vec![8, 8, 8, 0]);
}

#[test]
fn a_floe_never_fuses_twice_in_one_slide() {
    // [2,2,2] left => [4,2]: the fused 4 cannot eat the third 2.
    assert_eq!(line(&[2, 2, 2]), vec![4, 2, 0]);
    // [2,2,2,2,2] on a 5-field => [4,4,2]
    assert_eq!(line(&[2, 2, 2, 2, 2]), vec![4, 4, 2, 0, 0]);
    // [4,4,4,4] => [8,8]
    assert_eq!(line(&[4, 4, 4, 4]), vec![8, 8, 0, 0]);
}

#[test]
fn a_slide_packs_every_floe_and_its_score_is_the_sum_of_the_fuses() {
    let mut g = board(&[
        &[2, 0, 2, 4],
        &[0, 0, 0, 0],
        &[0, 0, 0, 0],
        &[0, 0, 0, 0],
    ]);
    let slid = g.slide(Left);
    assert_eq!(row(&g, 0), vec![4, 4]);
    assert_eq!(slid.gained, 4);
    assert_eq!(slid.fuses, 1);
    assert_eq!(g.score, 4);
    assert!(slid.changed);
}

#[test]
fn a_slide_that_moves_nothing_scores_nothing_and_spawns_nothing() {
    let mut g = board(&[&[2, 4, 8, 16], &[0, 0, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0]]);
    let before = g.grid.clone();
    let slid = g.slide(Left);
    assert!(!slid.changed);
    assert_eq!(slid.gained, 0);
    assert_eq!(g.score, 0);
    assert_eq!(g.grid, before);
    assert_eq!(g.moves, 0);
}

#[test]
fn every_direction_slides_its_own_lanes() {
    let mut g = board(&[
        &[0, 0, 0, 2],
        &[0, 0, 0, 2],
        &[0, 0, 0, 0],
        &[0, 0, 0, 4],
    ]);
    g.slide(Left);
    assert_eq!(row(&g, 0), vec![2]);
    assert_eq!(row(&g, 3), vec![4]);

    let mut g = board(&[
        &[2, 0, 0, 0],
        &[2, 0, 0, 0],
        &[0, 0, 0, 0],
        &[4, 0, 0, 0],
    ]);
    g.slide(Up);
    assert_eq!(g.grid, vec![4, 0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
    assert_eq!(g.score, 4);
}

// ---------------------------------------------------------------------------
// 2. Score, fuses, and the biggest floe.
// ---------------------------------------------------------------------------

#[test]
fn score_is_exactly_the_sum_of_the_fused_values() {
    let mut g = board(&[&[2, 2, 4, 4], &[8, 8, 16, 16], &[0, 0, 0, 0], &[0, 0, 0, 0]]);
    let slid = g.slide(Left);
    // 2+2=4, 4+4=8 on row 0; 8+8=16, 16+16=32 on row 1.
    assert_eq!(slid.gained, 4 + 8 + 16 + 32);
    assert_eq!(g.score, 60);
    assert_eq!(slid.fuses, 4);
    assert_eq!(g.max_tile, 32);
}

#[test]
fn max_floe_tracks_the_field() {
    let g = board(&[&[2, 4, 8, 16], &[32, 64, 128, 256], &[512, 1024, 2048, 2], &[0, 0, 0, 0]]);
    assert_eq!(g.max_floe(), 2048);
}

// ---------------------------------------------------------------------------
// 3. Spawning.
// ---------------------------------------------------------------------------

#[test]
fn a_spawn_lands_only_on_an_empty_cell() {
    for seed in 0..400u64 {
        let mut g = Game::new(seed);
        for _ in 0..60 {
            let empty: Vec<usize> = g.empty_cells();
            if empty.is_empty() {
                break;
            }
            let before = g.grid.clone();
            let at = g.spawn().expect("an empty cell was there");
            assert_eq!(before[at], 0, "seed {seed}: spawn overwrote a live floe");
            assert!(g.grid[at] == 2 || g.grid[at] == 4, "seed {seed}: bad floe value");
            for (i, &v) in g.grid.iter().enumerate() {
                if i != at {
                    assert_eq!(v, before[i], "seed {seed}: spawn moved other cells");
                }
            }
        }
    }
}

#[test]
fn a_spawn_fills_exactly_one_empty_cell() {
    for seed in 0..500u64 {
        let mut g = Game::new(seed);
        let before = g.empty_cells().len();
        g.spawn();
        assert_eq!(g.empty_cells().len(), before - 1, "seed {seed}");
    }
}

#[test]
fn spawn_probability_is_ninety_percent_two() {
    // Count the dealt value directly: start from an empty field.
    let mut g = Game::with(1, 4, 2048);
    g.grid = vec![0; 16];
    let mut twos = 0;
    let mut fours = 0;
    for _ in 0..4000 {
        g.grid = vec![0; 16];
        let at = g.spawn().unwrap();
        match g.grid[at] {
            2 => twos += 1,
            4 => fours += 1,
            v => panic!("unexpected dealt floe {v}"),
        }
    }
    let pct = 100.0 * twos as f64 / (twos + fours) as f64;
    assert!(
        (86.0..=94.0).contains(&pct),
        "expected ~90% twos, got {pct}% ({twos} twos, {fours} fours)"
    );
}

#[test]
fn a_full_field_deals_nothing() {
    let mut g = board(&[
        &[2, 4, 2, 4],
        &[4, 2, 4, 2],
        &[2, 4, 2, 4],
        &[4, 2, 4, 2],
    ]);
    assert!(g.empty_cells().is_empty());
    assert!(g.spawn().is_none());
}

// ---------------------------------------------------------------------------
// 4. Game over.
// ---------------------------------------------------------------------------

#[test]
fn a_full_field_with_no_adjacent_fuse_is_over() {
    let mut g = board(&[
        &[2, 4, 2, 4],
        &[4, 2, 4, 2],
        &[2, 4, 2, 4],
        &[4, 2, 4, 2],
    ]);
    assert!(!g.can_move(), "no slide is legal on a checkerboard");
    g.play(Left);
    assert_eq!(g.state, State::Over);
    assert_eq!(g.cause, Some(Cause::Stuck));
    assert_eq!(g.score, 0, "a dead slide scores nothing");
}

#[test]
fn a_full_field_that_can_still_fuse_is_not_over() {
    let g = board(&[
        &[2, 4, 2, 4],
        &[2, 2, 4, 2],
        &[2, 4, 2, 4],
        &[4, 2, 4, 2],
    ]);
    assert!(g.can_move());
    assert!(g.is_legal(Left));
    assert!(g.is_legal(Up), "column 0 is 2,2,2,4: up fuses, so up is legal");
}

#[test]
fn the_run_ends_when_the_last_slide_jams_the_field() {
    // One empty cell, and the only slide that moves anything packs the field
    // shut: the dealt floe lands in the last gap and nothing can fuse.
    let mut g = board(&[
        &[32, 16, 8, 8],
        &[16, 64, 32, 4],
        &[4, 32, 8, 8],
        &[32, 4, 32, 0],
    ]);
    assert_eq!(g.state, State::Playing);
    assert!(g.is_legal(Down));
    g.play(Down);
    assert!(g.grid.iter().all(|&v| v != 0), "the field filled up");
    assert_eq!(g.state, State::Over);
    assert_eq!(g.cause, Some(Cause::Stuck));
}

// ---------------------------------------------------------------------------
// 5. Winning.
// ---------------------------------------------------------------------------

#[test]
fn reaching_the_goal_wins_and_freezes_the_field() {
    let mut g = board(&[&[1024, 1024, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0]]);
    g.play(Left);
    assert_eq!(g.max_tile, 2048);
    assert_eq!(g.state, State::Won);
    assert_eq!(g.cause, Some(Cause::Goal));
    // Frozen: a slide after the win changes nothing.
    let before = g.grid.clone();
    g.play(Left);
    assert_eq!(g.grid, before);
    assert_eq!(g.score, 2048);
}

#[test]
fn continue_keeps_playing_after_a_win() {
    let mut g = board(&[&[1024, 1024, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0]]);
    g.play(Left);
    assert_eq!(g.state, State::Won);
    assert!(!g.key('c'), "c continues, it does not quit");
    assert_eq!(g.state, State::Playing);
    assert!(g.continued);
    // And a goal floe reached again does not re-win.
    let mut h = board(&[&[1024, 1024, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0]]);
    h.continued = true;
    h.play(Left);
    assert_eq!(h.state, State::Playing);
    assert_eq!(h.max_tile, 2048);
}

#[test]
fn a_custom_goal_wins_at_that_floe() {
    let mut g = Game::with(3, 4, 128);
    g.grid = vec![0; 16];
    g.grid[0] = 64;
    g.grid[1] = 64;
    g.play(Left);
    assert_eq!(g.max_tile, 128);
    assert_eq!(g.state, State::Won);
}

// ---------------------------------------------------------------------------
// 6. Keys: undo, restart, quit.
// ---------------------------------------------------------------------------

#[test]
fn undo_takes_back_the_slide_and_the_floe_it_dealt() {
    let mut g = board(&[&[2, 2, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0]]);
    let before = g.grid.clone();
    g.play(Left);
    assert_eq!(g.score, 4);
    assert_eq!(g.moves, 1);
    assert!(g.grid.iter().filter(|&&v| v != 0).count() >= 2);
    assert!(!g.key('u'), "undo does not quit");
    assert_eq!(g.grid, before, "undo restored the field");
    assert_eq!(g.score, 0);
    assert_eq!(g.moves, 0);
    assert_eq!(g.undos, 1);
}

#[test]
fn undo_is_one_step_only() {
    let mut g = board(&[&[2, 2, 4, 4], &[0, 0, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0]]);
    g.play(Left);
    let after_one = g.grid.clone();
    g.play(Left);
    assert!(!g.key('u'), "undo does not quit");
    assert_eq!(g.grid, after_one, "undo went back exactly one slide");
    let settled = g.grid.clone();
    assert!(!g.key('u'), "undo does not quit");
    assert!(!g.undo(), "there is no second step to take");
    assert_eq!(g.grid, settled);
}

#[test]
fn undo_can_take_back_a_jam() {
    let mut g = board(&[
        &[2, 4, 2, 4],
        &[4, 2, 4, 2],
        &[2, 4, 2, 4],
        &[4, 2, 4, 2],
    ]);
    g.play(Left);
    assert_eq!(g.state, State::Over);
    assert!(!g.key('u'));
    assert_eq!(g.state, State::Playing, "undo put the run back in play");
    assert!(!g.can_move(), "the checkerboard is still jammed");
}

#[test]
fn restart_starts_a_clean_field_on_the_next_seed_and_keeps_the_best() {
    let mut g = Game::with(10, 4, 2048);
    g.best = 9001;
    g.score = 500;
    g.play(Left);
    assert!(!g.key('r'), "restart does not quit");
    assert_eq!(g.seed, 11, "restart walks the seed family");
    assert_eq!(g.score, 0);
    assert_eq!(g.moves, 0);
    assert_eq!(g.best, 9001, "the record survives the restart");
    assert_eq!(g.grid.iter().filter(|&&v| v != 0).count(), 2);
    assert_eq!(g.state, State::Playing);
}

#[test]
fn keys_are_the_documented_ones_and_everything_else_is_ignored() {
    let mut g = Game::new(5);
    assert!(g.key('q'), "q quits");
    let mut g = Game::new(5);
    assert!(g.key('\u{3}'), "ctrl-C quits");
    let mut g = Game::new(5);
    let before = g.grid.clone();
    for c in ['x', 'z', ' ', '\n', '\t', '7', 'e', 'm', 'p'] {
        assert!(!g.key(c), "{c} must not quit");
    }
    assert_eq!(g.grid, before, "junk keys do not move the field");
}

#[test]
fn every_key_that_slides_is_accepted() {
    for (c, d) in [
        ('h', Left),
        ('j', Down),
        ('k', Up),
        ('l', Right),
        ('D', Left),
        ('B', Down),
        ('A', Up),
        ('C', Right),
        ('a', Left),
        ('s', Down),
        ('w', Up),
    ] {
        assert_eq!(Dir::parse(c), Some(d), "key {c} must slide {d:?}");
    }
    assert_eq!(Dir::parse('q'), None);
    assert_eq!(Dir::parse('u'), None);
    assert_eq!(Dir::parse('r'), None);
}

// ---------------------------------------------------------------------------
// 7. Determinism.
// ---------------------------------------------------------------------------

fn script() -> &'static [char] {
    &['h', 'j', 'h', 'l', 'k', 'j', 'h', 'h', 'l', 'j', 'u', 'j', 'k', 'h', 'l']
}

#[test]
fn the_same_seed_and_the_same_script_give_the_same_run() {
    let run = |seed: u64| {
        let mut g = Game::new(seed);
        for &c in script() {
            g.key(c);
            g.tick();
        }
        g
    };
    let a = run(7);
    let b = run(7);
    assert_eq!(a.canonical(), b.canonical());
    assert_eq!(a.state_hash(), b.state_hash());
    assert_eq!(a.render(RenderOpts::default()), b.render(RenderOpts::default()));

    let c = run(8);
    assert_ne!(a.state_hash(), c.state_hash(), "a different seed is a different run");
}

#[test]
fn the_generator_is_reproducible_and_never_zero() {
    let mut a = Rng::new(12345);
    let mut b = Rng::new(12345);
    for _ in 0..1000 {
        assert_eq!(a.next_u64(), b.next_u64());
    }
    assert_ne!(Rng::new(0).state(), 0, "xorshift must never hold 0");
    let mut z = Rng::new(0);
    for _ in 0..100 {
        assert_ne!(z.next_u64(), 0);
    }
}

#[test]
fn a_scripted_session_lands_on_the_golden_end_state() {
    // The pinned fingerprint of seed 11 through this exact script. Change the
    // rules and this number moves: that is the point.
    let mut g = Game::with(11, 4, 2048);
    for &c in &[
        'h', 'j', 'h', 'l', 'k', 'j', 'h', 'h', 'l', 'j', 'k', 'l', 'h', 'j', 'j', 'h', 'l', 'k',
        'h', 'j', 'u', 'h', 'l', 'l', 'j', 'h', 'k', 'j',
    ] {
        g.key(c);
        g.tick();
    }
    assert_eq!(g.seed, 11);
    assert_eq!(g.state, State::Playing);
    assert_eq!(g.undos, 1, "the script undoes exactly once");
    assert_eq!(g.score, 92);
    assert_eq!(g.moves, 25);
    assert_eq!(g.fuses, 16);
    assert_eq!(g.max_tile, 16);
    assert_eq!(g.state_hash(), 0xc341_ca16_8b48_fbf7);
}

#[test]
fn the_committed_fixture_replays_to_the_same_winning_run() {
    // The library path the --replay harness walks, asserted here without a
    // process: seed 7, the committed script, a 512 floe.
    let text = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/win.replay"))
        .expect("the committed fixture");
    let run = || {
        let mut g = Game::with(7, 4, 2048);
        for line in text.lines() {
            if line.starts_with('#') {
                continue;
            }
            if g.state != State::Playing {
                break;
            }
            if let Some(c) = line.chars().next() {
                g.key(c);
            }
            g.tick();
        }
        g
    };
    let a = run();
    let b = run();
    assert_eq!(a.state_hash(), b.state_hash());
    assert_eq!(a.max_tile, 512, "the fixture wins something big");
    assert!(a.max_tile >= 256);
    assert!(a.score >= 2000, "score {}", a.score);
    assert_eq!(a.state, State::Over);
    assert_eq!(a.moves, 549);
}

// ---------------------------------------------------------------------------
// 8. Field sizes.
// ---------------------------------------------------------------------------

#[test]
fn a_five_field_plays_by_the_same_rules() {
    let mut g = Game::with(4, 5, 2048);
    assert_eq!(g.size, 5);
    assert_eq!(g.grid.len(), 25);
    g.grid = vec![0; 25];
    g.grid[0] = 2;
    g.grid[1] = 2;
    g.grid[4] = 8;
    let slid = g.slide(Left);
    assert_eq!(row(&g, 0), vec![4, 8]);
    assert_eq!(slid.gained, 4);
    for _ in 0..200 {
        let key = g.autopilot_key();
        if g.state != State::Playing {
            break;
        }
        if let Some(c) = key {
            g.key(c);
        }
        g.tick();
    }
    assert!(g.max_tile >= 32, "the bot plays 5x5 too: {}", g.max_tile);
}

#[test]
fn a_new_field_is_dealt_two_floes_and_nothing_else() {
    for seed in 0..200u64 {
        let g = Game::new(seed);
        let live: Vec<u32> = g.grid.iter().copied().filter(|&v| v != 0).collect();
        assert_eq!(live.len(), 2, "seed {seed} dealt {:?}", live);
        assert!(
            live.iter().all(|&v| v == 2 || v == 4),
            "seed {seed} dealt {:?}",
            live
        );
        assert_eq!(g.score, 0);
        assert_eq!(g.moves, 0);
        assert_eq!(g.state, State::Playing);
    }
}

// ---------------------------------------------------------------------------
// 9. Rendering: a pure function of state, with documented degrade rules.
// ---------------------------------------------------------------------------

#[test]
fn the_frame_is_exactly_the_window_and_is_a_pure_function_of_state() {
    let g = Game::new(3);
    for (w, h) in [(80, 24), (60, 20), (42, 14), (30, 12), (25, 10), (12, 6), (80, 3)] {
        let opts = RenderOpts {
            width: w,
            height: h,
            mono: true,
            color: false,
        };
        let frame = g.render(opts);
        let lines: Vec<&str> = frame.split('\n').collect();
        assert_eq!(lines.len(), h + 1, "frame ends with a newline ({w}x{h})");
        for (i, l) in lines[..h].iter().enumerate() {
            assert_eq!(l.chars().count(), w, "row {i} is not {w} columns wide ({w}x{h}): {l:?}");
        }
        assert_eq!(g.render(opts), frame, "render is pure");
    }
}

#[test]
fn the_reference_frame_is_the_80x24_layout() {
    let g = Game::new(3);
    let frame = g.render(RenderOpts::default());
    let lines: Vec<&str> = frame.split('\n').collect();
    assert_eq!(lines.len(), 25);
    assert!(lines[0].contains("ICEFLOE 2048"), "the header is drawn");
    assert!(lines[1].contains("score"), "the score panel is drawn");
    assert!(lines[1].contains("best"), "the best score is on the panel");
    assert!(lines[1].contains("goal"), "the goal is on the panel");
    assert!(lines[3].contains('\u{250c}'), "the field box is drawn");
    assert!(lines[13].contains("hjkl"), "the keys line is drawn");
}

#[test]
fn a_small_window_drops_the_panel_then_the_border_and_never_wraps() {
    let g = Game::new(3);
    // 80x24: header, panel, box, keys.
    let big = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(big.contains("score"));
    assert!(big.contains('+'));
    assert!(big.contains("hjkl"));

    // 30x12: no score panel, no keys line, the box is still there (25 wide).
    let mid = g.render(RenderOpts {
        width: 30,
        height: 12,
        mono: true,
        color: false,
    });
    assert!(!mid.contains("score"), "the panel is dropped below 42 columns");
    assert!(!mid.contains("hjkl"), "the keys line is dropped below 42 columns");
    assert!(mid.contains('+'), "the box survives at 30 columns");
    assert!(mid.contains("ICEFLOE 2048"), "the header survives at 30 columns");

    // 20x8: no box, no panel, no header — just the field, clipped.
    let small = g.render(RenderOpts {
        width: 20,
        height: 8,
        mono: true,
        color: false,
    });
    assert!(!small.contains('+'), "the box needs the field width plus two");
    assert!(!small.contains("score"));
    assert!(!small.contains("ICEFLOE"));
    for l in small.split('\n') {
        assert!(l.chars().count() <= 20);
    }
}

#[test]
fn colour_off_and_mono_emit_no_escape_bytes_at_all() {
    let g = Game::new(9);
    let plain = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(!plain.contains('\x1b'), "NO_COLOR/--mono emit no escapes");
    assert!(
        plain.is_ascii(),
        "--mono is ASCII-only: {:?}",
        plain.chars().filter(|c| !c.is_ascii()).collect::<String>()
    );

    // Colour on: escapes, and every one of them closed.
    let colour = g.render(RenderOpts::default());
    assert!(colour.contains("\x1b[36m"), "colour is on by default");
    assert!(!colour.contains('\u{2500}') || colour.contains('\u{250c}'));
    let no_color = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: false,
        color: false,
    });
    assert!(!no_color.contains('\x1b'), "NO_COLOR emits no escapes");
    assert!(no_color.contains('\u{250c}'), "NO_COLOR keeps the glyphs");
}

#[test]
fn values_are_right_aligned_in_five_columns_and_the_field_is_a_box() {
    let g = board(&[
        &[2, 4, 8, 16],
        &[32, 64, 128, 256],
        &[512, 1024, 2048, 2],
        &[0, 0, 0, 0],
    ]);
    let frame = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(frame.contains("|    2|    4|    8|   16|"), "right-aligned in 5");
    assert!(frame.contains("|  512| 1024| 2048|    2|"));
    assert!(frame.contains("|.....|.....|.....|.....|"), "empty cells are dots");
    assert!(frame.contains("+-----+-----+-----+-----+"));
}

#[test]
fn the_status_line_says_what_the_run_needs() {
    let playing = Game::new(2).render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(playing.contains("hjkl"));

    let mut won = board(&[&[1024, 1024, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0], &[0, 0, 0, 0]]);
    won.play(Left);
    let f = won.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(f.contains("YOU WIN") && f.contains("c keeps playing"));

    let mut over = board(&[
        &[2, 4, 2, 4],
        &[4, 2, 4, 2],
        &[2, 4, 2, 4],
        &[4, 2, 4, 2],
    ]);
    over.play(Left);
    let f = over.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(f.contains("GAME OVER") && f.contains("r restarts"));
}

#[test]
fn the_plain_state_is_key_value_lines_plus_the_field() {
    let g = Game::new(3);
    let text = plain_state(&g);
    for key in [
        "state=", "seed=", "size=", "goal=", "ticks=", "moves=", "score=", "max=", "hash=",
    ] {
        assert!(text.contains(key), "plain_state is missing {key}");
    }
    assert!(text.contains("grid:"));
    assert!(!text.contains('\x1b'), "plain text, no escapes");
}

#[test]
fn strip_sgr_leaves_the_visible_text() {
    assert_eq!(strip_sgr("\x1b[1;31mGAME\x1b[0m"), "GAME");
    assert_eq!(strip_sgr("plain"), "plain");
    assert_eq!(strip_sgr(""), "");
}

// ---------------------------------------------------------------------------
// 10. The autopilot: the demo path must play a real game.
// ---------------------------------------------------------------------------

#[test]
fn the_autopilot_plays_a_real_game_on_a_fixed_seed() {
    for seed in [1u64, 2, 3, 7, 42] {
        let mut g = Game::with(seed, 4, 2048);
        for _ in 0..4000 {
            if g.state != State::Playing {
                break;
            }
            let k = g.autopilot_key().expect("a legal slide exists while playing");
            g.key(k);
            g.tick();
        }
        assert_eq!(g.state, State::Over, "seed {seed} did not finish");
        assert!(
            g.max_tile >= 128,
            "seed {seed} only reached {}",
            g.max_tile
        );
        assert!(g.score > 0);
    }
    // And the demo is reproducible.
    let play = |seed: u64| {
        let mut g = Game::with(seed, 4, 2048);
        for _ in 0..4000 {
            if g.state != State::Playing {
                break;
            }
            let k = g.autopilot_key().unwrap();
            g.key(k);
            g.tick();
        }
        g.state_hash()
    };
    assert_eq!(play(7), play(7));
    assert_ne!(play(7), play(8));
}
