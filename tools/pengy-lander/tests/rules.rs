//! The rules of Ice Lander, asserted through the library.
//!
//! Every test here drives `src/lib.rs` directly: no terminal, no clock, no
//! files. The physics is integer fixed point, so every assertion is exact.

use pengy_lander::*;

// ---------------------------------------------------------------------------
// Units and constants
// ---------------------------------------------------------------------------

#[test]
fn the_fixed_point_scales_are_consistent() {
    // The position update is `x += vx` per tick, which is exact only because
    // PSCALE == VSCALE * 30 (dt = 1/30 s).
    assert_eq!(PSCALE, VSCALE * 30);
    // Every per-tick delta is an exact integer in these units: 1/30 s of
    // 1.62 m/s^2 is 1620, of 5.0 is 5000, of 4.0 is 4000, and 1/30 s of a
    // 90 deg/s turn is 3 degrees. No rounding term anywhere.
    assert_eq!(G_MOON * 30 % VSCALE, 1_620 * 30 % VSCALE);
    assert_eq!(G_MOON * 30, 48_600); // 1.62 m/s^2 at 1/30 s, in VSCALE units
    assert_eq!(THRUST_ACC * 30, 120_000);
    assert_eq!(ROT_PER_TICK * 30, 90);
    // 1.62 m/s^2, 5.0 m/s^2, 4.0 m/s^2, in m/s per tick.
    assert_eq!(G_MOON, 1_620);
    assert_eq!(G_ASTEROID, 5_000);
    assert_eq!(THRUST_ACC, 4_000);
    assert_eq!(Body::Moon.gravity(), G_MOON);
    assert_eq!(Body::Asteroid.gravity(), G_ASTEROID);
}

#[test]
fn the_landing_limits_are_the_spec_numbers() {
    assert_eq!(MAX_VX, VSCALE); // 1.0 m/s
    assert_eq!(MAX_VY, 2 * VSCALE); // 2.0 m/s
    assert_eq!(MAX_TILT_DEG, 5);
    assert_eq!(ROT_PER_TICK * 30, 90); // 90 deg/s
    assert_eq!(FUEL_START / 30, FUEL_UNITS); // 100 units
    assert_eq!(FUEL_UNITS, 100);
    assert_eq!(FUEL_BURN * 30, 30); // 1 unit per second of thrust
}

// ---------------------------------------------------------------------------
// Integration: gravity and thrust over N steps, exactly
// ---------------------------------------------------------------------------

#[test]
fn gravity_integrates_exactly_over_n_steps() {
    // Free fall on the Moon from 20 m, no thrust: vy after N ticks is exactly
    // N * G_MOON, and y is the exact sum of the velocities.
    let mut g = Game::new(1);
    g.y = 20 * PSCALE;
    g.vx = 0;
    g.vy = 0;
    g.terrain = Terrain {
        h: vec![0; COLS],
        pads: vec![],
    };
    for n in 1..=60 {
        g.tick();
        assert_eq!(g.vy, -(n as i64) * G_MOON);
        assert_eq!(g.y, 20 * PSCALE - (n as i64) * (n as i64 + 1) / 2 * G_MOON);
    }
}

#[test]
fn thrust_adds_exactly_along_the_heading() {
    // Nose straight up, full thrust: net per tick is THRUST - gravity.
    let mut g = Game::new(1);
    g.y = 20 * PSCALE;
    g.vx = 0;
    g.vy = 0;
    g.heading = 0;
    g.terrain = Terrain {
        h: vec![0; COLS],
        pads: vec![],
    };
    for n in 1..=30 {
        g.key(' ');
        g.tick();
        assert_eq!(g.vy, (n as i64) * (THRUST_ACC - G_MOON));
        assert_eq!(g.fuel, FUEL_START - n as i64);
    }
    // Nose right: all the thrust is horizontal, gravity is untouched.
    let mut g = Game::new(1);
    g.y = 20 * PSCALE;
    g.vx = 0;
    g.vy = 0;
    g.heading = 90;
    g.terrain = Terrain {
        h: vec![0; COLS],
        pads: vec![],
    };
    for n in 1..=10 {
        g.key(' ');
        g.tick();
        assert_eq!(g.vx, (n as i64) * THRUST_ACC);
        assert_eq!(g.vy, -(n as i64) * G_MOON);
    }
}

#[test]
fn thrust_at_45_degrees_uses_the_table_exactly() {
    let mut g = Game::new(1);
    g.y = 20 * PSCALE;
    g.vx = 0;
    g.vy = 0;
    g.heading = 45;
    g.terrain = Terrain {
        h: vec![0; COLS],
        pads: vec![],
    };
    g.key(' ');
    g.tick();
    // sin 45 = cos 45 = 0.707 in the table.
    assert_eq!(g.vx, THRUST_ACC * 707 / 1000);
    assert_eq!(g.vy, THRUST_ACC * 707 / 1000 - G_MOON);
}

/// A test rig with nothing under the probe: flat ground at 0, no pads, the
/// probe high up. Rotation and integration tests use it so the probe cannot
/// land halfway through a loop.
fn free_space(seed: u64) -> Game {
    let mut g = Game::new(seed);
    g.terrain = Terrain {
        h: vec![0; COLS],
        pads: vec![],
    };
    g.y = 22 * PSCALE;
    g.vx = 0;
    g.vy = 0;
    g
}

#[test]
fn rotation_is_three_degrees_a_tick_and_wraps() {
    let mut g = free_space(1);
    for _ in 0..10 {
        g.key('d');
        g.tick();
    }
    assert_eq!(g.heading, 30);
    for _ in 0..10 {
        g.key('a');
        g.tick();
    }
    assert_eq!(g.heading, 0);
    // 120 ticks of rotation is a full turn, and the heading stays in 0..360.
    for _ in 0..120 {
        g.key('d');
        g.tick();
    }
    assert_eq!(g.heading, 0);
    assert_eq!(tilt_of(357), 3);
    assert_eq!(tilt_of(183), 177);
    assert_eq!(tilt_of(-3), 3);
    assert_eq!(tilt_of(720), 0);
}

// ---------------------------------------------------------------------------
// Fuel
// ---------------------------------------------------------------------------

#[test]
fn fuel_floors_at_zero_and_the_engine_stops() {
    let mut g = Game::new(1);
    g.y = 22 * PSCALE;
    g.fuel = 2 * FUEL_BURN; // two ticks of burn left
    g.terrain = Terrain {
        h: vec![0; COLS],
        pads: vec![],
    };
    g.key(' ');
    g.tick();
    assert_eq!(g.fuel, FUEL_BURN);
    g.key(' ');
    g.tick();
    assert_eq!(g.fuel, 0);
    // Out of fuel: the engine is off and the cause says so. No negative fuel,
    // ever, and no thrust from here on.
    for _ in 0..40 {
        g.key(' ');
        g.tick();
        assert_eq!(g.fuel, 0);
        assert!(!g.engine_on());
        assert_eq!(g.thrust_accel(), (0, 0));
    }
    assert_eq!(g.cause, Some(Cause::OutOfFuel));
    assert_eq!(g.state, State::Flying); // it is falling, not yet wrecked
}

#[test]
fn fuel_burns_one_unit_per_second_of_thrust() {
    let mut g = Game::new(1);
    g.y = 22 * PSCALE;
    g.terrain = Terrain {
        h: vec![0; COLS],
        pads: vec![],
    };
    for _ in 0..30 {
        g.key(' ');
        g.tick();
    }
    assert_eq!(g.fuel_units(), FUEL_UNITS - 1); // one second, one unit
}

// ---------------------------------------------------------------------------
// Landing classification, at the boundaries
// ---------------------------------------------------------------------------

fn pad() -> Option<u64> {
    Some(2)
}

#[test]
fn landing_is_accepted_just_inside_every_limit() {
    // Just inside: 0.99 m/s sideways, 1.99 m/s down, 3 degrees of tilt.
    let t = classify_touch(pad(), -99 * VSCALE / 100, -199 * VSCALE / 100, 3);
    assert_eq!(t, Touch::Landed(2));
    let t = classify_touch(pad(), 99 * VSCALE / 100, 199 * VSCALE / 100, 3);
    assert_eq!(t, Touch::Landed(2));
    assert_eq!(classify_touch(pad(), 0, 0, 0), Touch::Landed(2));
    assert_eq!(classify_touch(Some(5), 0, 0, 4), Touch::Landed(5));
}

#[test]
fn landing_is_refused_at_and_past_every_limit() {
    // The comparisons are strict: exactly 1.0 m/s is a crash.
    assert_eq!(
        classify_touch(pad(), MAX_VX, 0, 0),
        Touch::Crashed(Cause::TooFastSideways)
    );
    assert_eq!(
        classify_touch(pad(), -MAX_VX, 0, 0),
        Touch::Crashed(Cause::TooFastSideways)
    );
    assert_eq!(
        classify_touch(pad(), 0, -MAX_VY, 0),
        Touch::Crashed(Cause::TooFastDescent)
    );
    assert_eq!(
        classify_touch(pad(), 0, MAX_VY, 0),
        Touch::Crashed(Cause::TooFastDescent)
    );
    assert_eq!(
        classify_touch(pad(), 0, 0, MAX_TILT_DEG),
        Touch::Crashed(Cause::TooTilted)
    );
    assert_eq!(
        classify_touch(pad(), 0, 0, 180),
        Touch::Crashed(Cause::TooTilted)
    );
    // One unit below each limit is a landing.
    assert_eq!(classify_touch(pad(), MAX_VX - 1, 0, 0), Touch::Landed(2));
    assert_eq!(classify_touch(pad(), 0, -MAX_VY + 1, 0), Touch::Landed(2));
    assert_eq!(classify_touch(pad(), 0, 0, MAX_TILT_DEG - 1), Touch::Landed(2));
}

#[test]
fn touchdown_off_a_pad_is_always_a_crash() {
    for vx in [-2 * VSCALE, 0, MAX_VX - 1] {
        for vy in [-3 * VSCALE, 0, MAX_VY - 1] {
            assert_eq!(
                classify_touch(None, vx, vy, 0),
                Touch::Crashed(Cause::OffPad)
            );
        }
    }
}

#[test]
fn the_order_of_blame_is_pad_then_sideways_then_down_then_tilt() {
    assert_eq!(
        classify_touch(None, MAX_VX, MAX_VY, 90),
        Touch::Crashed(Cause::OffPad)
    );
    assert_eq!(
        classify_touch(pad(), MAX_VX, MAX_VY, 90),
        Touch::Crashed(Cause::TooFastSideways)
    );
    assert_eq!(
        classify_touch(pad(), 0, MAX_VY, 90),
        Touch::Crashed(Cause::TooFastDescent)
    );
    assert_eq!(
        classify_touch(pad(), 0, 0, 90),
        Touch::Crashed(Cause::TooTilted)
    );
}

#[test]
fn touchdown_on_a_pad_lands_and_banks_the_wave() {
    let mut g = Game::new(7);
    // Sit the probe on the first pad, creeping down.
    let p = g.terrain.pads[0].clone();
    g.x = (p.centre() as i64) * PSCALE;
    g.y = (g.terrain.at(p.centre()) + 1) * PSCALE;
    g.vx = 0;
    g.vy = -VSCALE / 2; // 0.5 m/s
    g.heading = 0;
    while g.state == State::Flying {
        g.tick();
    }
    assert_eq!(g.state, State::Landed);
    assert_eq!(g.cause, None);
    assert_eq!(g.last_mult, p.mult);
    assert_eq!(g.waves_landed, 1);
    assert_eq!(g.score(), landing_score(p.mult, g.fuel_units(), 0));
    // A landed probe is frozen: n flies the next wave, nothing else moves it.
    let before = g.canonical();
    g.tick();
    g.key(' ');
    g.tick();
    assert_eq!(g.canonical(), before);
}

#[test]
fn touchdown_on_rock_crashes_the_run() {
    let mut g = Game::new(7);
    // Find a column that is not a pad.
    let col = (0..COLS).find(|c| g.terrain.pad_at(*c).is_none()).unwrap();
    g.x = (col as i64) * PSCALE;
    g.y = (g.terrain.at(col) + 1) * PSCALE;
    g.vx = 0;
    g.vy = -VSCALE / 2;
    while g.state == State::Flying {
        g.tick();
    }
    assert_eq!(g.state, State::Crashed);
    assert_eq!(g.cause, Some(Cause::OffPad));
    assert!(g.state.is_over());
    assert_eq!(g.score(), 0);
}

// ---------------------------------------------------------------------------
// Score
// ---------------------------------------------------------------------------

#[test]
fn score_is_monotonic_in_fuel_and_multiplier() {
    let mut prev = 0u64;
    for fuel in 0..=100 {
        let s = landing_score(1, fuel, 0);
        assert!(s > prev, "more fuel must pay more");
        prev = s;
    }
    let mut prev = 0u64;
    for mult in MULTS {
        let s = landing_score(mult, 50, 0);
        assert!(s > prev, "a richer pad must pay more");
        prev = s;
    }
    // 100 x multiplier + 10 per unit of fuel + 50 per wave banked.
    assert_eq!(landing_score(1, 0, 0), 100);
    assert_eq!(landing_score(5, 0, 0), 500);
    assert_eq!(landing_score(5, 10, 0), 600);
    assert_eq!(landing_score(5, 10, 3), 750);
    assert_eq!(landing_score(1, 100, 0), 1100);
    // No negative fuel, ever.
    assert_eq!(landing_score(1, -7, 0), 100);
}

#[test]
fn a_landing_banks_and_a_new_wave_starts_fresh() {
    let mut g = Game::new(11);
    let p = g.terrain.pads[0].clone();
    g.x = (p.centre() as i64) * PSCALE;
    g.y = (g.terrain.at(p.centre()) + 1) * PSCALE;
    g.vy = -VSCALE / 2;
    while g.state == State::Flying {
        g.tick();
    }
    let banked = g.score();
    assert!(banked > 0);
    g.key('n');
    assert_eq!(g.state, State::Flying);
    assert_eq!(g.wave, 2);
    assert_eq!(g.score(), banked); // banked score is never lost
    assert_eq!(g.fuel_units(), FUEL_UNITS); // and the tank is full again
    assert_eq!(g.last_points, 0);
    // The wave bonus pays for the fresh terrain.
    assert_eq!(
        landing_score(1, 0, 1) - landing_score(1, 0, 0),
        WAVE_BONUS
    );
}

// ---------------------------------------------------------------------------
// Terrain generator
// ---------------------------------------------------------------------------

#[test]
fn terrain_always_has_pads_of_a_legal_width_over_200_seeds() {
    for seed in 0..200u64 {
        let mut rng = Rng::new(seed);
        let t = generate_terrain(&mut rng);
        assert!(
            t.pads.len() >= MIN_PADS && t.pads.len() <= MAX_PADS,
            "seed {seed}: {} pads",
            t.pads.len()
        );
        for p in &t.pads {
            assert!(
                p.width() >= PAD_MIN_W && p.width() <= PAD_MAX_W,
                "seed {seed}: pad {}..{} is {} wide",
                p.start,
                p.end,
                p.width()
            );
            assert!(p.end < COLS, "seed {seed}: pad runs off the field");
            assert!(MULTS.contains(&p.mult), "seed {seed}: multiplier {}", p.mult);
            // A pad is flat: one height across its whole width.
            let h = t.at(p.start);
            for c in p.start..=p.end {
                assert_eq!(t.at(c), h, "seed {seed}: pad {} is not flat", p.start);
            }
        }
        // Pads do not overlap, and they are left to right.
        for w in t.pads.windows(2) {
            assert!(w[0].end < w[1].start, "seed {seed}: pads overlap");
        }
        // The ridge is ground, not spikes.
        assert!(t.max_h() <= MAX_RIDGE_M, "seed {seed}: ridge {}", t.max_h());
        assert!(t.h.iter().all(|h| *h >= MIN_RIDGE_M));
        // The ridge walks one metre at a time; the only steps are the pad
        // benches, which are cut 2 metres down by construction.
        for c in 0..COLS - 1 {
            if t.pad_at(c).is_some() || t.pad_at(c + 1).is_some() {
                continue; // the pad benches are the only steps
            }
            assert!(
                (t.at(c) - t.at(c + 1)).abs() <= 1,
                "seed {seed}: a cliff in the ridge at column {c}"
            );
        }
        // There is always room to fly: the ridge tops out below the spawn.
        assert!(t.max_h() < SPAWN_Y_M);
    }
}

#[test]
fn the_spawn_is_always_clear_of_terrain_and_above_the_ridge() {
    for seed in 0..200u64 {
        let mut rng = Rng::new(seed);
        let t = generate_terrain(&mut rng);
        let (x, y, vx, vy, heading) = spawn_point(&mut rng, &t);
        assert!(y / PSCALE > t.max_h(), "seed {seed}: spawned inside a ridge");
        assert!(y / PSCALE >= SPAWN_Y_M);
        assert!(x / PSCALE >= 0 && x / PSCALE < COLS as i64);
        assert_eq!(heading, 0);
        assert_eq!(vy, 0);
        assert!(vx.abs() <= VSCALE * 3 / 2, "seed {seed}: spawned at {}", vx);
        // And it is not over a pad wall: the ground under the spawn is lower
        // than the probe by at least the ridge margin.
        let col = (x / PSCALE) as usize;
        assert!(y / PSCALE - t.at(col) >= SPAWN_Y_M - MAX_RIDGE_M);
    }
}

#[test]
fn the_same_seed_always_builds_the_same_ground() {
    for seed in 0..40u64 {
        let a = Game::new(seed);
        let b = Game::new(seed);
        assert_eq!(a.terrain, b.terrain, "seed {seed} built two ridges");
        assert_eq!(a.x, b.x);
        assert_eq!(a.y, b.y);
        assert_eq!(a.vx, b.vx);
    }
}

#[test]
fn every_terrain_has_a_pad_the_autopilot_can_score_on() {
    for seed in 0..200u64 {
        let g = Game::new(seed);
        let p = g.terrain.best_pad(g.x / PSCALE);
        assert!(p.is_some(), "seed {seed}: no pad to aim at");
    }
}

// ---------------------------------------------------------------------------
// Keys
// ---------------------------------------------------------------------------

#[test]
fn keys_do_what_the_help_text_says() {
    let mut g = Game::new(3);
    assert!(!g.key('a'));
    assert_eq!(g.heading, 357);
    assert!(!g.key('d'));
    assert!(!g.key('d'));
    assert_eq!(g.heading, 3);
    g.key(' ');
    assert!(g.engine_on());
    // q is the only key that asks the front-end to quit.
    assert!(g.key('q'));
    // Junk is ignored, and Escape is inert (terminals send arrows as ESC [ A).
    let mut h = Game::new(3);
    let before = h.canonical();
    for c in ['\x1b', 'x', 'Z', '\n', '\t', '1', '-', '=', 'j', 'k'] {
        assert!(!h.key(c), "key {:?} must be inert", c);
    }
    assert_eq!(h.canonical(), before);
}

#[test]
fn r_restarts_on_the_next_seed_and_keeps_the_record() {
    let mut g = Game::new(5);
    g.best_score = 4321;
    g.best_waves = 3;
    g.best_seed = 5;
    let seed = g.seed;
    g.restart();
    assert_eq!(g.seed, seed + 1);
    assert_eq!(g.score(), 0);
    assert_eq!(g.wave, 1);
    assert_eq!(g.state, State::Flying);
    assert_eq!(g.best_score, 4321);
    assert_eq!(g.best_waves, 3);
    assert_eq!(g.best_seed, 5);
}

#[test]
fn n_from_the_air_abandons_the_wave_and_keeps_the_score() {
    let mut g = Game::new(9);
    g.total = 700;
    g.waves_landed = 2;
    g.key('n');
    assert_eq!(g.wave, 2);
    assert_eq!(g.score(), 700);
    assert_eq!(g.state, State::Flying);
    assert_eq!(g.fuel_units(), FUEL_UNITS);
}

#[test]
fn a_crashed_probe_is_frozen() {
    let mut g = Game::new(13);
    g.state = State::Crashed;
    g.cause = Some(Cause::TooFastDescent);
    let before = g.canonical();
    for c in [' ', 'a', 'd'] {
        g.key(c);
        g.tick();
    }
    assert_eq!(g.canonical(), before);
    // n and r are the way back out.
    g.key('n');
    assert_eq!(g.state, State::Flying);
    assert_eq!(g.cause, None);
}

// ---------------------------------------------------------------------------
// Determinism
// ---------------------------------------------------------------------------

/// Drive a scripted session: one line of keys per tick, blank line = no input.
fn scripted(seed: u64, script: &[&str]) -> Game {
    let mut g = Game::new(seed);
    for line in script {
        if g.state.is_over() {
            break;
        }
        for c in line.chars() {
            g.key(c);
        }
        g.tick();
    }
    g
}

#[test]
fn the_same_seed_and_script_always_end_in_the_same_state() {
    let script = [
        " ", " ", " ", "d", " ", " ", "a", "", "", " ", " ", " ", "n", " ", " ",
        "d", "d", " ", " ", " ", " ", "a", "a", "", " ", " ", "n", " ", " ", " ",
    ];
    let a = scripted(17, &script);
    let b = scripted(17, &script);
    assert_eq!(a.canonical(), b.canonical());
    assert_eq!(a.state_hash(), b.state_hash());
    // A different seed, same script: a different run.
    let c = scripted(18, &script);
    assert_ne!(c.state_hash(), a.state_hash());
    // One tick of thrust more, same seed: a different run.
    let mut d = script.to_vec();
    d.push(" ");
    let d = scripted(17, &d);
    assert_ne!(d.state_hash(), a.state_hash());
}

#[test]
fn the_autopilot_lands_on_the_pad_it_aims_at() {
    // The demo path is the one --headless runs: it has to land, not crash.
    let mut landed = 0;
    for seed in 1..25u64 {
        let mut g = Game::new(seed);
        for _ in 0..2000 {
            for k in g.autopilot_keys().chars() {
                g.key(k);
            }
            if g.state == State::Landed {
                landed += 1;
                g.key('n');
            }
            g.tick();
            if g.state == State::Crashed {
                break;
            }
        }
    }
    assert!(
        landed >= 20,
        "the autopilot landed {landed} times in 24 runs"
    );
}

#[test]
fn the_autopilot_is_a_pure_function_of_state() {
    let g = Game::new(20);
    assert_eq!(g.autopilot_keys(), g.autopilot_keys());
    let mut h = Game::new(20);
    let a = h.autopilot_keys();
    h.tick();
    let b = h.autopilot_keys();
    assert!(!a.is_empty());
    // Only the keys the game accepts come out of it.
    for c in (a + &b).chars() {
        assert!(matches!(c, 'a' | 'd' | ' '), "autopilot emitted {c:?}");
    }
}

#[test]
fn the_asteroid_is_a_suicide_run_by_construction() {
    // 5.0 m/s^2 against a 4.0 m/s^2 engine: the nose can never hold the probe
    // up, so the descent always accelerates. Documented, not a bug.
    assert!(Body::Asteroid.gravity() > THRUST_ACC);
    assert!(Body::Moon.gravity() < THRUST_ACC);
    let mut g = Game::with_body(1, Body::Asteroid);
    g.y = 22 * PSCALE;
    g.vy = 0;
    g.terrain = Terrain {
        h: vec![0; COLS],
        pads: vec![],
    };
    for _ in 0..30 {
        g.key(' ');
        g.tick();
        assert!(g.vy < 0, "the probe must keep falling");
    }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

#[test]
fn the_frame_is_a_pure_function_of_state() {
    let g = Game::new(4);
    assert_eq!(frame(&g), frame(&g));
    assert_eq!(g.render(RenderOpts::default()), frame(&g));
}

#[test]
fn the_frame_is_the_window_it_was_asked_for() {
    for (w, h) in [(80, 24), (100, 30), (62, 20), (40, 12), (20, 8), (10, 4)] {
        let opts = RenderOpts {
            width: w,
            height: h,
            mono: true,
            color: false,
        };
        let f = Game::new(6).render(opts);
        let rows: Vec<&str> = f.split("\r\n").collect();
        assert_eq!(rows.len(), h, "window {w}x{h} drew {} rows", rows.len());
        for row in &rows {
            assert_eq!(
                visible_len(row),
                w,
                "window {w}x{h}: row {:?} is {} cells",
                row,
                visible_len(row)
            );
        }
    }
}

#[test]
fn mono_output_is_pure_ascii_and_colour_output_is_not() {
    let g = Game::new(6);
    let mono = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(
        mono.is_ascii(),
        "--mono must draw in ASCII only: {:?}",
        mono.chars().filter(|c| !c.is_ascii()).take(4).collect::<String>()
    );
    assert!(!mono.contains('\x1b'), "no colour means no escape bytes");
    let colour = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: false,
        color: true,
    });
    assert!(colour.contains("\x1b[36m"), "the HUD is coloured");
    assert!(!colour.is_ascii(), "the fancy glyphs are drawn");
    // Colour off means the same glyphs and no escapes at all.
    let plain = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: false,
        color: false,
    });
    assert!(!plain.contains('\x1b'), "colour off emits no escape bytes");
}

#[test]
fn a_small_window_drops_the_panel_then_the_border_then_the_title() {
    let g = Game::new(6);
    let full = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(full.contains("ALT"), "the panel is there at 80x24");
    assert!(full.contains('+'), "and the border is");
    assert!(full.contains("Ice Lander"), "and the title is");

    // Narrow: the panel moves above the field, the border goes.
    let narrow = g.render(RenderOpts {
        width: 50,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(narrow.contains("ALT"), "the instruments survive");
    assert!(
        !narrow.contains("Ice Lander"),
        "the title is the first thing dropped"
    );

    // Tiny: the legend goes, the field is clipped, the ground survives.
    let tiny = g.render(RenderOpts {
        width: 12,
        height: 6,
        mono: true,
        color: false,
    });
    assert!(!tiny.contains("space thrust"), "the legend is dropped");
    assert!(tiny.contains('='), "a pad is still drawn");
    assert_eq!(tiny.split("\r\n").count(), 6);
}

#[test]
fn the_hud_shows_every_number_the_player_needs() {
    let g = Game::new(6);
    let f = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    for want in ["ALT", "VX", "VY", "FUEL", "TILT", "THRUST", "score", "wave"] {
        assert!(f.contains(want), "the HUD is missing {want}");
    }
    assert!(f.contains("space thrust"), "the key legend is on screen");
    assert!(f.contains('O') || f.contains('^'), "the probe is drawn");
}

#[test]
fn the_probe_and_the_flame_are_drawn_where_the_probe_is() {
    let mut g = Game::new(6);
    g.y = 12 * PSCALE;
    g.x = 30 * PSCALE;
    g.heading = 0;
    g.key(' ');
    let f = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    let rows: Vec<String> = f.split("\r\n").map(|s| s.trim_end().to_string()).collect();
    let row = rows
        .iter()
        .position(|r| r.contains('^'))
        .expect("no probe drawn");
    // Title line, top border, then the field rows.
    assert_eq!(row, probe_row(g.y) + 2, "the probe is drawn in the wrong row");
    assert_eq!(
        rows[row].chars().position(|c| c == '^').unwrap(),
        g.col() + 1,
        "the probe is drawn in the wrong column"
    );
    // The flame is under the nose while the engine burns.
    assert!(
        rows[row + 1].contains('*'),
        "no flame under a burning engine"
    );
}

#[test]
fn the_end_screen_says_what_happened() {
    let mut g = Game::new(6);
    g.state = State::Crashed;
    g.cause = Some(Cause::TooFastDescent);
    let f = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(f.contains("CRASHED: too fast down"), "the reason is on screen");
    g.state = State::Landed;
    g.last_points = 640;
    let f = g.render(RenderOpts {
        width: 80,
        height: 24,
        mono: true,
        color: false,
    });
    assert!(f.contains("LANDED +640"), "and what it paid");
}

#[test]
fn plain_state_is_key_value_lines_and_an_ascii_field() {
    let s = plain_state(&Game::new(6));
    assert!(s.contains("state=flying"));
    assert!(s.contains("seed=6"));
    assert!(s.contains("hash="));
    assert!(s.contains("field:"));
    for line in s.lines().filter(|l| l.starts_with('|')) {
        assert!(line.is_ascii());
        assert_eq!(line.chars().count(), COLS + 2);
    }
}

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------

#[test]
fn result_kv_carries_the_numbers_the_harness_greps() {
    let kv = Game::new(6).result_kv();
    let keys: Vec<&str> = kv.iter().map(|(k, _)| k.as_str()).collect();
    for want in [
        "state",
        "seed",
        "body",
        "ticks",
        "wave",
        "waves_landed",
        "score",
        "fuel",
        "tilt",
        "hash",
    ] {
        assert!(keys.contains(&want), "no {want} in result_kv");
    }
}

#[test]
fn the_state_hash_covers_the_ground_and_the_generator() {
    let mut a = Game::new(6);
    let h = a.state_hash();
    a.terrain.h[0] += 1;
    assert_ne!(a.state_hash(), h, "the terrain is part of the state");
    let mut b = Game::new(6);
    let h2 = b.state_hash();
    b.rng = Rng::new(99);
    assert_ne!(b.state_hash(), h2, "the generator is part of the state");
}

#[test]
fn mps_text_is_a_signed_two_dp_speed() {
    assert_eq!(mps_text(0), "+0.00");
    assert_eq!(mps_text(VSCALE), "+1.00");
    assert_eq!(mps_text(-VSCALE), "-1.00");
    assert_eq!(mps_text(VSCALE / 2), "+0.50");
    assert_eq!(mps_text(-MAX_VY), "-2.00");
}

#[test]
fn new_terrain_after_a_crash_is_a_new_run_not_a_second_life() {
    let mut g = Game::new(5);
    g.total = 750;
    g.waves_landed = 3;
    g.state = State::Crashed;
    g.key('n');
    assert_eq!(g.state, State::Flying);
    assert_eq!(g.total, 0, "the crashed run's score does not carry on");
    assert_eq!(g.waves_landed, 0);
    assert_eq!(g.seed, 6, "it is the next run of the seed family, as `r` is");
}
