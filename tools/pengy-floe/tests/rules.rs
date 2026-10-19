//! Rules tests: every contract line in spec.md "Rules to test", driven through
//! the library the player actually drives. No I/O, no sleeps, no randomness
//! beyond the seeded generator — the whole suite runs in well under a second.

use pengy_floe::*;

/// A script is one key per tick; `None` is "no input".
fn run(seed: u64, script: &[Option<char>]) -> Game {
    let mut g = Game::new(seed);
    for &k in script {
        if let Some(c) = k {
            if g.key(c) {
                break;
            }
        }
        g.tick();
        if g.state == State::Dead {
            break;
        }
    }
    g
}

fn autopilot(seed: u64, ticks: u64) -> Game {
    let mut g = Game::new(seed);
    for _ in 0..ticks {
        if let Some(k) = g.autopilot_key() {
            g.key(k);
        }
        g.tick();
        if g.state == State::Dead {
            break;
        }
    }
    g
}

// ---------------------------------------------------------------------------
// Gravity integration
// ---------------------------------------------------------------------------

#[test]
fn gravity_integrates_exactly_over_n_ticks() {
    // No flap: vy goes 1, 2, 3, 3, … and y is the running sum from START_ROW.
    let mut g = Game::new(1);
    assert_eq!(g.y, START_ROW);
    assert_eq!(g.vy, 0);
    let expected_y = [(11i32, 1i32), (13, 2), (16, 3), (19, 3)];
    for (i, (y, vy)) in expected_y.iter().enumerate() {
        g.tick();
        assert_eq!(g.vy, *vy, "vy after tick {}", i + 1);
        assert_eq!(g.y, *y, "y after tick {}", i + 1);
    }
    // The 4th tick lands on the floor row: that is death, not a bounce.
    assert_eq!(g.state, State::Dead);
    assert_eq!(g.cause, Some(Cause::Floor));
    assert_eq!(g.y, FLOOR_ROW);
}

#[test]
fn gravity_is_one_per_tick_and_clamped_to_three() {
    let mut g = Game::new(1);
    for _ in 0..40 {
        g.tick();
        assert!(g.vy <= VY_MAX, "vy never exceeds the clamp");
        if g.state == State::Dead {
            break;
        }
    }
    // A flap sets vy; the next tick adds exactly GRAVITY.
    let mut g = Game::new(1);
    g.flap();
    assert_eq!(g.vy, FLAP_VY);
    g.tick();
    assert_eq!(g.vy, FLAP_VY + GRAVITY);
    assert_eq!(g.y, START_ROW + FLAP_VY + GRAVITY);
}

#[test]
fn flap_sets_vy_and_never_stacks() {
    let mut g = Game::new(1);
    for _ in 0..5 {
        g.flap();
    }
    // Five flaps in one tick are one flap: vy is *set*, never summed.
    assert_eq!(g.vy, FLAP_VY);
    assert_eq!(g.flaps, 5);
    g.tick();
    assert_eq!(g.vy, -1);

    // Flapping while paused or dead does nothing.
    let mut p = Game::new(1);
    p.toggle_pause();
    p.flap();
    assert_eq!(p.vy, 0);
    assert_eq!(p.state, State::Paused);
}

// ---------------------------------------------------------------------------
// Gap generation: an impossible gap is a bug
// ---------------------------------------------------------------------------

#[test]
fn generated_gaps_are_always_in_bounds_with_clearance() {
    for seed in 0..400u64 {
        let g = autopilot(seed, 400);
        for p in &g.pillars {
            let h = p.gap_height();
            assert!(h >= GAP_MIN, "seed {seed}: gap {h} below the minimum");
            assert!(h <= GAP_MAX, "seed {seed}: gap {h} above the maximum");
            assert!(
                p.gap_top >= CEILING_ROW + CLEARANCE,
                "seed {seed}: gap_top {} leaves no ceiling clearance",
                p.gap_top
            );
            assert!(
                p.gap_bottom <= FLOOR_ROW - CLEARANCE,
                "seed {seed}: gap_bottom {} leaves no floor clearance",
                p.gap_bottom
            );
            assert!(p.gap_top >= 0 && p.gap_bottom < ROWS as i32);
        }
    }
}

#[test]
fn gap_height_shrinks_from_eight_to_five_by_score_25() {
    assert_eq!(Game::gap_height_for(0), 8);
    assert_eq!(Game::gap_height_for(24), 6); // 8 - 3*24/25
    assert_eq!(Game::gap_height_for(25), GAP_MIN);
    assert_eq!(Game::gap_height_for(1000), GAP_MIN);
    // Monotone: the gap never widens as the score climbs.
    let mut prev = Game::gap_height_for(0);
    for s in 0..60 {
        let h = Game::gap_height_for(s);
        assert!(h <= prev && h >= GAP_MIN && h <= GAP_MAX);
        prev = h;
    }
}

// ---------------------------------------------------------------------------
// Scoring
// ---------------------------------------------------------------------------

#[test]
fn score_increments_exactly_once_per_pillar() {
    // Short run, nothing culled: every point is exactly one scored pillar.
    let mut g = Game::new(2);
    for _ in 0..60 {
        if let Some(k) = g.autopilot_key() {
            g.key(k);
        }
        g.tick();
    }
    let passed = g.pillars.iter().filter(|p| p.right() < PENGUIN_COL as i32).count();
    assert_eq!(g.score as usize, passed, "one point per pillar past the penguin");
    assert_eq!(
        g.pillars.iter().filter(|p| p.scored).count(),
        g.score as usize,
        "the scored flag is the point"
    );

    // Long run: at most one point per tick, and a pillar that has scored never
    // scores again (its flag stays set while it is live).
    let mut g = Game::new(2);
    let mut prev: Vec<(i32, i32, i32, bool)> = Vec::new();
    let mut last = 0i32;
    for _ in 0..4000 {
        if let Some(k) = g.autopilot_key() {
            g.key(k);
        }
        g.tick();
        assert!(g.score - last <= 1, "never more than one point in a tick");
        last = g.score;
        let now: Vec<(i32, i32, i32, bool)> = g
            .pillars
            .iter()
            .map(|p| (p.x, p.gap_top, p.gap_bottom, p.scored))
            .collect();
        for (x, top, bottom, scored) in &now {
            // The same pillar one tick later sits `cols` columns to the left.
            if let Some((_, _, _, was)) = prev
                .iter()
                .find(|(px, pt, pb, _)| *pt == *top && *pb == *bottom && *px >= *x && *px - *x <= 1)
            {
                assert!(!(*was && !scored), "a scored pillar never un-scores");
            }
        }
        prev = now;
        if g.state == State::Dead {
            break;
        }
    }
    assert!(g.score > 10, "the autopilot passes pillars");
}

#[test]
fn pillars_spawn_every_12_columns() {
    let g = autopilot(5, 600);
    assert!(g.pillars.len() >= 2);
    for pair in g.pillars.windows(2) {
        // Spawn columns are 12 apart; live pillars have travelled the same
        // distance, so the spacing is preserved exactly.
        assert_eq!(pair[1].x - pair[0].x, SPACING_COLS);
    }
}

#[test]
fn speed_ramps_from_one_column_per_two_ticks_to_one_per_tick() {
    assert_eq!(Game::speed_rate_for(0), 30); // 30/60 = 1 column per 2 ticks
    assert_eq!(Game::speed_rate_for(15), 45);
    assert_eq!(Game::speed_rate_for(SPEED_FULL_SCORE), SPEED_UNIT);
    assert_eq!(Game::speed_rate_for(999), SPEED_UNIT);

    // At score 0 the world moves 1 column every 2 ticks, exactly.
    let mut g = Game::new(1);
    let x0 = g.pillars[0].x;
    g.tick();
    assert_eq!(g.pillars[0].x, x0, "tick 1 of 2 does not move");
    g.tick();
    assert_eq!(g.pillars[0].x, x0 - 1, "tick 2 of 2 moves one column");

    // At full speed it moves 1 column per tick, and never more.
    let mut g = Game::new(1);
    g.score = SPEED_FULL_SCORE;
    let x0 = g.pillars[0].x;
    for i in 1..=5 {
        g.flap(); // stay alive: this test is about the world, not the penguin
        g.tick();
        assert_eq!(g.state, State::Playing);
        assert_eq!(g.pillars[0].x, x0 - i, "full speed is exactly 1 column/tick");
    }
}

// ---------------------------------------------------------------------------
// Collisions
// ---------------------------------------------------------------------------

#[test]
fn ceiling_and_floor_kill_at_the_boundary_cells_only() {
    // y = 2 with a flap (vy = -2) integrates to 2 + (-2 + 1) = 1, then the
    // next tick lands on the ceiling row: death. Gravity is applied before
    // integration, so a single -1 is cancelled by it.
    let mut g = Game::new(1);
    g.y = 2;
    g.vy = -2;
    g.tick();
    assert_eq!(g.state, State::Playing, "row 1 is one row from the ceiling");
    g.flap();
    g.tick();
    assert_eq!(g.y, CEILING_ROW);
    assert_eq!(g.state, State::Dead);
    assert_eq!(g.cause, Some(Cause::Ceiling));

    // y = 3 with a flap integrates to 2: alive, two rows from the ceiling.
    let mut g = Game::new(1);
    g.y = 3;
    g.vy = -2;
    g.tick();
    assert_eq!(g.y, 2);
    assert_eq!(g.state, State::Playing);

    // y = 18 with vy = 1 lands on the floor row: death.
    let mut g = Game::new(1);
    g.y = 18;
    g.vy = 1;
    g.tick();
    assert_eq!(g.y, FLOOR_ROW);
    assert_eq!(g.state, State::Dead);
    assert_eq!(g.cause, Some(Cause::Floor));

    // y = 16 with vy = 1 integrates to 18: alive, one row from the floor.
    let mut g = Game::new(1);
    g.y = 16;
    g.vy = 1;
    g.tick();
    assert_eq!(g.y, 18);
    assert_eq!(g.state, State::Playing);
}

/// A game with one pillar parked at `x`, gap `top..=bottom`, penguin on `y`.
fn with_pillar(x: i32, top: i32, bottom: i32, y: i32) -> Game {
    let mut g = Game::new(1);
    g.pillars.clear();
    g.pillars.push(Pillar {
        x,
        gap_top: top,
        gap_bottom: bottom,
        scored: false,
    });
    g.y = y;
    g.vy = 0;
    g
}

#[test]
fn pillar_collision_is_aabb_against_pillar_cells() {
    // Straddling the penguin's columns: the gap rows are safe, the ice is not.
    let g = with_pillar(8, 6, 13, 10);
    assert!(!g.hit_pillar(), "inside the gap is the hole");
    assert!(!with_pillar(8, 6, 13, 6).hit_pillar(), "the gap's top cell is passable");
    assert!(!with_pillar(8, 6, 13, 13).hit_pillar(), "the gap's bottom cell is passable");
    assert!(with_pillar(8, 6, 13, 5).hit_pillar(), "one row above the gap is solid ice");
    assert!(with_pillar(8, 6, 13, 14).hit_pillar(), "one row below the gap is solid ice");
    // A pillar that does not straddle the hitbox never scores a hit.
    assert!(!with_pillar(20, 6, 13, 5).hit_pillar(), "a pillar far away is not a wall");
}

#[test]
fn pillar_collision_covers_both_columns_of_the_hitbox() {
    // Pillar covering columns 7..8: overlaps the hitbox's left column.
    assert!(with_pillar(7, 6, 13, 5).hit_pillar());
    // Pillar covering columns 9..10: overlaps the hitbox's right column.
    assert!(with_pillar(9, 6, 13, 5).hit_pillar());
    // Pillar covering 10..11: clear of the hitbox — the forgiving hitbox.
    assert!(
        !with_pillar(10, 6, 13, 5).hit_pillar(),
        "the sprite overlaps a column the hitbox does not"
    );
    // Pillar covering 6..7: clear of the hitbox on the left.
    assert!(!with_pillar(6, 6, 13, 5).hit_pillar());
}

#[test]
fn collision_kills_on_the_tick_that_touches() {
    let mut g = with_pillar(8, 6, 13, 14);
    g.tick();
    assert_eq!(g.state, State::Dead);
    assert_eq!(g.cause, Some(Cause::Pillar));
    // A dead run is frozen: ticking changes nothing.
    let before = g.clone();
    g.tick();
    g.tick();
    assert_eq!(g.canonical(), before.canonical());
}

// ---------------------------------------------------------------------------
// States, keys, medals
// ---------------------------------------------------------------------------

#[test]
fn pause_freezes_the_world_and_resume_continues_it() {
    let mut g = Game::new(1);
    g.key('p');
    assert_eq!(g.state, State::Paused);
    let before = g.canonical();
    for _ in 0..20 {
        g.tick();
        g.key(' ');
    }
    assert_eq!(g.canonical(), before, "a paused run does not advance");
    g.key('p');
    assert_eq!(g.state, State::Playing);
    g.tick();
    assert_ne!(g.canonical(), before);
    assert_eq!(g.ticks, 1);
}

#[test]
fn restart_is_instant_keeps_the_best_and_advances_the_seed_family() {
    let mut g = Game::new(10);
    g.score = 42;
    g.best = 42;
    g.ticks = 999;
    g.key('r');
    assert_eq!(g.seed, 11, "restart moves to the next seed in the family");
    assert_eq!(g.score, 0);
    assert_eq!(g.ticks, 0);
    assert_eq!(g.best, 42, "the best score survives the restart");
    assert_eq!(g.state, State::Playing);
    assert_eq!(g.y, START_ROW);
    assert_eq!(g.vy, 0);
    // A flap on the death screen restarts instantly: death costs two seconds.
    let mut d = Game::new(10);
    d.y = 18;
    d.vy = 1;
    d.tick();
    assert_eq!(d.state, State::Dead);
    d.key(' ');
    assert_eq!(d.state, State::Playing);
    assert_eq!(d.score, 0);
    // A dead run never unpauses.
    let mut d = Game::new(10);
    d.y = 18;
    d.vy = 1;
    d.tick();
    d.key('p');
    assert_eq!(d.state, State::Dead);
}

#[test]
fn quit_keys_are_q_and_ctrl_c() {
    let mut g = Game::new(1);
    assert!(!g.key(' '));
    assert!(!g.key('p'));
    assert!(!g.key('r'));
    assert!(g.key('q'));
    let mut g = Game::new(1);
    assert!(g.key('\u{3}'));
}

#[test]
fn medals_are_10_25_50_100() {
    assert_eq!(Game::medal_for(9), None);
    assert_eq!(Game::medal_for(MEDAL_ICE), Some("ice"));
    assert_eq!(Game::medal_for(MEDAL_BRONZE - 1), Some("ice"));
    assert_eq!(Game::medal_for(MEDAL_BRONZE), Some("bronze"));
    assert_eq!(Game::medal_for(MEDAL_SILVER), Some("silver"));
    assert_eq!(Game::medal_for(MEDAL_GOLD), Some("gold"));
    assert_eq!(Game::medal_for(10_000), Some("gold"));
    let mut g = Game::new(1);
    assert_eq!(g.medal(), None);
    g.score = 25;
    assert_eq!(g.medal(), Some("bronze"));
}

// ---------------------------------------------------------------------------
// Determinism
// ---------------------------------------------------------------------------

/// A scripted session: the autopilot's own flap cadence at seed 7, with a
/// pause, a resume, a restart and an ignored key injected at fixed ticks.
/// It is a pure function of the seed, so the harness can replay it.
fn script() -> Vec<Option<char>> {
    let mut g = Game::new(7);
    let mut s: Vec<Option<char>> = Vec::new();
    for t in 0..400usize {
        let k = match t {
            20 => Some('p'),
            25 => Some('p'),
            34 => Some('r'),
            47 => Some('z'),
            _ => g.autopilot_key(),
        };
        s.push(k);
        if let Some(c) = k {
            g.key(c);
        }
        g.tick();
    }
    s
}

#[test]
fn same_seed_and_script_give_the_identical_state() {
    let s = script();
    let a = run(7, &s);
    let b = run(7, &s);
    assert_eq!(a.canonical(), b.canonical());
    assert_eq!(a.state_hash(), b.state_hash());
    assert_eq!(a.frame(), b.frame());

    // And three more times, byte for byte.
    for _ in 0..3 {
        let c = run(7, &s);
        assert_eq!(c.state_hash(), a.state_hash());
    }
}

#[test]
fn a_different_seed_gives_a_different_state() {
    let s = script();
    assert_ne!(run(7, &s).state_hash(), run(8, &s).state_hash());
}

#[test]
fn a_different_script_gives_a_different_state() {
    // Two extra flaps late in the session. A penguin that flaps is a
    // different penguin: same seed, different world. (Late, on purpose: the
    // script restarts at tick 34, which wipes the pre-restart state, so an
    // early edit would be erased by the restart.)
    let base = script();
    let mut extra = script();
    extra[100] = Some(' ');
    extra[101] = Some(' ');
    assert_ne!(run(7, &base).state_hash(), run(7, &extra).state_hash());
    // And the same script twice is the same run, byte for byte.
    assert_eq!(run(7, &base).state_hash(), run(7, &script()).state_hash());
}

#[test]
fn scripted_session_lands_on_the_expected_end_state() {
    // The golden end state of `script()` at seed 7. If any rule, constant or
    // integration order changes, this fails: the acceptance harness depends on
    // a run being reproducible, and this test is what pins it.
    let g = run(7, &script());
    assert_eq!(g.state_hash(), 0xb56e_65d6_bbb7_8dfb);
    assert_eq!(g.ticks, 366, "400 ticks minus the 34 the restart rolled back");
    assert_eq!(g.flaps, 129);
    assert_eq!(g.score, 16);
    assert_eq!(g.medal(), Some("ice"));
    assert_eq!(g.state, State::Playing);
    assert_eq!(g.seed, 8, "the restart advanced the seed family by one");
    assert_eq!(g.best, 16);
    assert_eq!(g.canonical(), run(7, &script()).canonical());
}

#[test]
fn autopilot_runs_are_reproducible_and_win_a_medal() {
    let a = autopilot(2, 4000);
    let b = autopilot(2, 4000);
    assert_eq!(a.state_hash(), b.state_hash());
    assert_eq!(a.score, b.score);
    assert_eq!(a.score, 70, "the committed fixture's score");
    assert_eq!(a.medal(), Some("silver"));
    assert_eq!(a.state, State::Dead);
    assert_eq!(a.cause, Some(Cause::Pillar));
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/// A rendered row with every ANSI escape removed: the text on screen.
fn plain(line: &str) -> String {
    let mut out = String::new();
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            if chars.next() == Some('[') {
                for c in chars.by_ref() {
                    if c.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// Width of a rendered row on screen: ANSI escapes occupy no cells.
fn visible(line: &str) -> usize {
    let mut n = 0;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            // Skip CSI: ESC [ params final byte.
            if chars.next() == Some('[') {
                for c in chars.by_ref() {
                    if c.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
        } else {
            n += 1;
        }
    }
    n
}

#[test]
fn frame_is_a_pure_function_of_state() {
    let g = autopilot(2, 40);
    assert_eq!(g.frame(), g.render(RenderOpts::default()));
    assert_eq!(g.frame(), g.frame());
    // Colour off emits no escape bytes at all.
    let plain = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(!plain.contains('\x1b'), "NO_COLOR / --mono emits no escapes");
    assert!(plain.is_ascii());
    // Colour on emits escapes, and the same state emits the same ones.
    let color = g.render(RenderOpts {
        color: true,
        ..RenderOpts::default()
    });
    assert!(color.contains("\x1b["));
    assert_eq!(color, g.render(RenderOpts::default()));
}

#[test]
fn frame_fits_80x24_and_degrades_in_smaller_windows() {
    let g = autopilot(2, 40);
    let full = g.render(RenderOpts::default());
    let lines: Vec<&str> = full.lines().collect();
    assert_eq!(lines.len(), 24, "the reference frame is 24 rows");
    assert!(
        full.lines().all(|l| visible(l) <= 80),
        "no row is wider than the window"
    );
    // The bordered playfield rows fill the window: 80 cells, box walls included.
    for l in &lines[1..=20] {
        assert_eq!(visible(l), 80);
        let t = plain(l);
        assert!(t.starts_with('\u{2502}') && t.ends_with('\u{2502}'), "row: {t}");
    }
    assert_eq!(visible(lines[0]), 80, "the header is the box top");

    // 23 rows: the status line goes, the playfield stays whole.
    let l23 = g.render(RenderOpts {
        height: 23,
        ..RenderOpts::default()
    });
    assert_eq!(l23.lines().count(), 23);
    assert!(!l23.contains("score 0   best"));

    // 21 rows: header + playfield + bottom border, no chrome.
    let l21 = g.render(RenderOpts {
        height: 21,
        ..RenderOpts::default()
    });
    assert_eq!(l21.lines().count(), 21);

    // 18 rows: the chrome is gone and the playfield is clipped, not crashed.
    let l18 = g.render(RenderOpts {
        height: 18,
        ..RenderOpts::default()
    });
    assert_eq!(l18.lines().count(), 18);

    // Narrow: the border is dropped and nothing exceeds the width.
    let narrow = g.render(RenderOpts {
        width: 30,
        height: 24,
        ..RenderOpts::default()
    });
    assert!(narrow.lines().all(|l| visible(l) <= 30));
    assert!(!narrow.lines().any(|l| l.starts_with('\u{2502}')));

    // Absurdly small windows must not panic.
    for (w, h) in [(1, 1), (2, 1), (5, 3), (41, 20), (80, 1), (1, 24)] {
        let f = g.render(RenderOpts {
            width: w,
            height: h,
            mono: true,
            color: false,
        });
        assert!(f.lines().count() <= h, "{w}x{h} overflowed");
    }
}

#[test]
fn playfield_projection_shows_the_penguin_pillars_and_ice() {
    let g = with_pillar(8, 6, 13, 10);
    let row = playfield_row(&g, 10);
    assert_eq!(row.chars().count(), COLS);
    assert!(row.contains('@'), "the penguin is drawn on its row");
    assert!(playfield_row(&g, 0).chars().all(|c| c == '-'), "the ceiling row is ice");
    assert!(playfield_row(&g, 19).chars().all(|c| c == '-'), "the floor row is ice");
    // A pillar row is solid ice above the gap.
    let top = playfield_row(&g, 3);
    assert_eq!(&top[8..10], "##");
    // The gap row is empty at the pillar's columns.
    let gap = playfield_row(&g, 12);
    assert_eq!(&gap[8..10], "  ");
}

#[test]
fn result_kv_carries_the_contract_keys() {
    let g = autopilot(2, 4000);
    let kv = g.result_kv();
    let keys: Vec<&str> = kv.iter().map(|(k, _)| k.as_str()).collect();
    for k in ["state", "seed", "ticks", "score", "best", "hash", "cause", "medal"] {
        assert!(keys.contains(&k), "missing {k} in {:?}", keys);
    }
    let text = plain_state(&g);
    assert!(text.contains("score=70\n"));
    assert!(text.contains("medal=silver\n"));
    assert!(!text.contains('\x1b'));
}
