//! The rules, tested through the library: the generator, the field of view,
//! combat, the monsters, the items, the score, and determinism.
//!
//! Everything here drives `pengy_rogue::Game` directly — no terminal, no clock,
//! no files. The whole suite runs in well under a second.

use pengy_rogue::*;

/// A floor with everything on it, for the hand-built scenarios: one open room,
/// no monsters, no items.
fn open_room() -> Game {
    let mut g = Game::new(1);
    g.map = vec![WALL; MAP_W * MAP_H];
    for y in 1..MAP_H - 1 {
        for x in 1..MAP_W - 1 {
            g.map[idx(x, y)] = FLOOR;
        }
    }
    g.map[idx(38, 10)] = STAIRS;
    g.stairs = Pos::new(38, 10);
    g.entry = Pos::new(3, 3);
    g.player = Pos::new(3, 3);
    g.items.clear();
    g.monsters.clear();
    g.refresh_fov();
    g
}

fn put_monster(g: &mut Game, kind: MonsterKind, x: usize, y: usize) {
    g.monsters.push(Monster {
        kind,
        x,
        y,
        hp: kind.hp(),
    });
}

// ---------------------------------------------------------------------------
// Generation
// ---------------------------------------------------------------------------

#[test]
fn one_hundred_seeds_all_dig_a_connected_floor() {
    for seed in 0..100u64 {
        for depth in 1..=FLOORS {
            let mut rng = Rng::new(seed * 31 + depth as u64);
            let floor = gen_floor(&mut rng, depth);
            assert!(
                floor_is_connected(&floor),
                "seed {seed} depth {depth}: the stairs are not reachable from the entry"
            );
            let reach = flood_fill(&floor.map, floor.entry);
            assert!(
                reach[idx(floor.stairs.x, floor.stairs.y)],
                "seed {seed} depth {depth}: flood fill cannot reach the stairs"
            );
            assert_eq!(
                floor.map[idx(floor.stairs.x, floor.stairs.y)],
                STAIRS,
                "seed {seed} depth {depth}: the stairs tile is not a stair"
            );
            assert_ne!(
                (floor.entry.x, floor.entry.y),
                (floor.stairs.x, floor.stairs.y),
                "seed {seed} depth {depth}: the entry is the stairs"
            );
        }
    }
}

#[test]
fn rooms_are_4_to_9_of_the_right_size_and_never_overlap() {
    for seed in 0..100u64 {
        let mut rng = Rng::new(seed);
        let floor = gen_floor(&mut rng, 1 + (seed as usize) % FLOORS);
        assert!(
            floor.rooms.len() >= ROOMS_MIN && floor.rooms.len() <= ROOMS_MAX,
            "seed {seed}: {} rooms",
            floor.rooms.len()
        );
        for r in &floor.rooms {
            assert!(r.w >= ROOM_W_MIN && r.w <= ROOM_W_MAX, "seed {seed}: room {} wide", r.w);
            assert!(r.h >= ROOM_H_MIN && r.h <= ROOM_H_MAX, "seed {seed}: room {} tall", r.h);
            assert!(r.x >= 1 && r.y >= 1);
            assert!(r.x + r.w <= MAP_W - 1, "seed {seed}: room runs off the map");
            assert!(r.y + r.h <= MAP_H - 1, "seed {seed}: room runs off the map");
        }
        for (i, a) in floor.rooms.iter().enumerate() {
            for (j, b) in floor.rooms.iter().enumerate() {
                if i != j {
                    assert!(
                        !a.overlaps(*b),
                        "seed {seed}: rooms {i} and {j} overlap"
                    );
                }
            }
        }
    }
}

#[test]
fn every_room_is_reachable_and_the_contents_stand_on_floor() {
    for seed in 0..100u64 {
        let mut rng = Rng::new(seed ^ 0x5eed);
        let floor = gen_floor(&mut rng, 1 + (seed as usize) % FLOORS);
        let reach = flood_fill(&floor.map, floor.entry);
        for r in &floor.rooms {
            assert!(
                reach[idx(r.cx(), r.cy())],
                "seed {seed}: the middle of a room is cut off"
            );
        }
        for i in &floor.items {
            assert_ne!(floor.map[idx(i.x, i.y)], WALL, "seed {seed}: item in the rock");
            assert!(ITEM_GLYPHS.contains(&i.glyph));
        }
        // One item per tile.
        for (a, i) in floor.items.iter().enumerate() {
            for (b, j) in floor.items.iter().enumerate() {
                if a != b {
                    assert!((i.x, i.y) != (j.x, j.y), "seed {seed}: two items on one tile");
                }
            }
        }
        for m in &floor.monsters {
            assert_ne!(floor.map[idx(m.x, m.y)], WALL, "seed {seed}: monster in the rock");
            assert!((m.x, m.y) != (floor.entry.x, floor.entry.y));
            assert_eq!(m.hp, m.kind.hp(), "seed {seed}: a monster spawns wounded");
        }
        // Monsters do not share a tile with each other, the entry or the stairs.
        for (a, m) in floor.monsters.iter().enumerate() {
            for (b, n) in floor.monsters.iter().enumerate() {
                if a != b {
                    assert!((m.x, m.y) != (n.x, n.y), "seed {seed}: two monsters, one tile");
                }
            }
        }
    }
}

#[test]
fn the_boss_only_spawns_on_floor_five() {
    for seed in 0..100u64 {
        for depth in 1..=FLOORS {
            let mut rng = Rng::new(seed * 7 + depth as u64);
            let floor = gen_floor(&mut rng, depth);
            let bosses = floor
                .monsters
                .iter()
                .filter(|m| m.kind == MonsterKind::Thaw)
                .count();
            if depth == FLOORS {
                assert_eq!(bosses, 1, "seed {seed}: floor 5 must hold The Thaw");
            } else {
                assert_eq!(bosses, 0, "seed {seed}: floor {depth} must not hold a boss");
            }
        }
    }
}

#[test]
fn generation_is_a_function_of_the_seed() {
    for seed in 0..25u64 {
        let a = gen_floor(&mut Rng::new(seed), 3);
        let b = gen_floor(&mut Rng::new(seed), 3);
        assert_eq!(a.map, b.map, "seed {seed}: the same seed dug a different floor");
        assert_eq!(a.rooms, b.rooms);
        assert_eq!(a.monsters.len(), b.monsters.len());
        assert_eq!(a.items.len(), b.items.len());
        let c = gen_floor(&mut Rng::new(seed + 1), 3);
        assert_ne!(a.map, c.map, "seed {seed}: neighbouring seeds dug the same floor");
    }
}

// ---------------------------------------------------------------------------
// Field of view
// ---------------------------------------------------------------------------

#[test]
fn a_wall_blocks_the_view() {
    let mut g = open_room();
    // A wall column at x = 10, with a gap at y = 6.
    for y in 2..12 {
        g.map[idx(10, y)] = WALL;
    }
    g.map[idx(10, 6)] = FLOOR;
    g.player = Pos::new(5, 6);
    g.refresh_fov();
    // Straight down the corridor: seen, through the gap.
    assert!(g.visible[idx(9, 6)]);
    assert!(g.visible[idx(11, 6)]);
    // Behind the wall: not seen, and never marked as seen.
    assert!(!g.visible[idx(11, 3)]);
    assert!(!g.seen[idx(11, 3)]);
    // The wall itself is visible: you always see the rock in front of you.
    assert!(g.visible[idx(10, 5)]);
}

#[test]
fn the_radius_edge_is_in_and_the_next_tile_out() {
    let mut g = open_room();
    // Placed so the whole square fits inside the map.
    g.player = Pos::new(20, 7);
    g.refresh_fov();
    // The radius is a square: the tile exactly 7 away along a corridor is in.
    assert!(g.visible[idx(27, 7)], "7 to the right is in");
    assert!(g.visible[idx(13, 7)], "7 to the left is in");
    assert!(g.visible[idx(20, 14)], "7 down is in");
    assert!(g.visible[idx(20, 0)], "7 up is in");
    // The corner of the square is in.
    assert!(g.visible[idx(27, 14)], "the corner of the square is in");
    // One further out is not.
    assert!(!g.visible[idx(28, 7)], "8 to the right is out");
    assert!(!g.visible[idx(12, 7)], "8 to the left is out");
    assert!(!g.visible[idx(28, 14)], "past the corner is out");
    assert!(!g.visible[idx(20, 13)] == false, "6 down is in");
    // The tile you stand on is always in.
    assert!(g.visible[idx(20, 7)]);
}

#[test]
fn explored_tiles_stay_drawn_and_unexplored_tiles_are_blank() {
    let mut g = open_room();
    g.player = Pos::new(5, 6);
    g.refresh_fov();
    let far = idx(30, 6);
    assert!(!g.seen[far], "the far tile is out of the view to start with");
    let frame = render(&g, RenderOpts::default());
    assert!(frame.contains('@'));
    // Walk toward it: the tile behind us stays on screen, dim, and the mono
    // frame draws the explored floor as a plain '.'.
    for _ in 0..12 {
        g.key('l');
    }
    assert!(g.seen[idx(5, 6)], "the tile we left is still explored");
    assert!(!g.visible[idx(5, 6)], "and it is no longer in sight");
    let mono = render(&g, RenderOpts { mono: true, ..Default::default() });
    assert!(mono.contains('.'), "explored floor draws as '.' in mono");
    assert!(!mono.contains('\x1b'), "mono emits no escape codes");
    let colour = render(&g, RenderOpts { color: true, ..Default::default() });
    assert!(colour.contains("\x1b[2m"), "explored floor draws dim in colour");
}

// ---------------------------------------------------------------------------
// Combat
// ---------------------------------------------------------------------------

#[test]
fn combat_is_deterministic_for_a_fixed_seed() {
    let script = "jjllkk..nnbb";
    let run = |seed: u64| {
        let mut g = Game::new(seed);
        for c in script.chars() {
            g.key(c);
        }
        g.state_hash()
    };
    for seed in 0..20u64 {
        assert_eq!(run(seed), run(seed), "seed {seed}: the same script diverged");
    }
    // And a different script is a different run.
    let mut a = Game::new(9);
    let mut b = Game::new(9);
    for c in "jjll".chars() {
        a.key(c);
    }
    for c in "jjlk".chars() {
        b.key(c);
    }
    assert_ne!(a.state_hash(), b.state_hash());
}

#[test]
fn the_attack_roll_is_the_documented_formula() {
    // 1d4 + 1 + weapon - defence, minimum 0, over the whole die.
    let mut g = Game::new(3);
    g.weapon = 0;
    let mut seen = [0; 4];
    for _ in 0..400 {
        let roll = g.player_attack_roll(0);
        assert!((2..=5).contains(&roll), "roll {roll} is not 1d4+1");
        seen[(roll - 2) as usize] += 1;
    }
    assert!(seen.iter().all(|n| *n > 0), "the die never landed on a face");
    // Defence comes straight off the roll, and the floor of the roll is 0.
    let mut g = Game::new(3);
    g.weapon = 0;
    assert!((0..=1).contains(&g.player_attack_roll(5)));
    g.weapon = 10;
    assert!((11..=15).contains(&g.player_attack_roll(4)));
    assert_eq!(g.player_attack_roll(99), 0, "the roll never goes negative");
}

#[test]
fn a_monster_hits_the_penguin_and_the_penguin_can_die() {
    let mut g = open_room();
    put_monster(&mut g, MonsterKind::Rat, 4, 3);
    let hp = g.hp;
    g.key('.'); // the rat is adjacent: it hits
    assert_eq!(g.hp, hp - 1, "a rat does 1 damage");
    assert_eq!(g.message, "rat hits you for 1.");
    // A parka soaks the rat completely, and a wolf still lands 2.
    g.defence = 1;
    g.key('.');
    assert_eq!(g.hp, hp - 1, "1 damage minus 1 defence is 0");
    g.monsters.clear();
    put_monster(&mut g, MonsterKind::Wolf, 4, 3);
    let before = g.hp;
    g.key('.');
    assert_eq!(g.hp, before - 2, "a wolf does 3 minus the parka");
    // Permadeath: 0 hp is dead, and dead is final.
    g.hp = 1;
    g.key('.');
    assert_eq!(g.state, State::Dead);
    assert_eq!(g.hp, 0);
    let before = g.canonical();
    for c in "jjllgg>".chars() {
        g.key(c);
    }
    assert_eq!(g.canonical(), before, "a dead penguin does not move");
    assert!(g.key('q'), "q still quits a dead run");
}

#[test]
fn walking_into_a_monster_hits_it_and_killing_the_boss_wins() {
    let mut g = open_room();
    put_monster(&mut g, MonsterKind::Rat, 4, 3);
    g.weapon = 5; // a guaranteed kill on a 2 hp rat
    g.key('l');
    assert_eq!(g.kills, 1, "the rat is gone");
    assert!(g.monsters.is_empty());
    assert_eq!(g.message, "You kill rat!");

    // The boss: 20 hp, and killing it is the win condition.
    let mut g = open_room();
    g.floor = FLOORS;
    put_monster(&mut g, MonsterKind::Thaw, 4, 3);
    g.weapon = 20;
    g.key('l');
    assert_eq!(g.state, State::Win, "The Thaw breaking ends the run");
    assert_eq!(g.kills, 1);
    assert!(g.message.contains("win"));
}

#[test]
fn a_monster_in_sight_walks_toward_the_penguin() {
    let mut g = open_room();
    // A wolf 6 tiles away, in the open, inside the radius.
    put_monster(&mut g, MonsterKind::Wolf, 20, 6);
    g.player = Pos::new(14, 6);
    g.refresh_fov();
    let start: i32 = (20i32 - 14).abs();
    let mut dist = start;
    for _ in 0..5 {
        // The penguin waits; the wolf closes the distance every turn.
        g.key('.');
        let m = g.monsters[0];
        let d = (m.x as i32 - g.player.x as i32).abs() + (m.y as i32 - g.player.y as i32).abs();
        assert!(d < dist, "the wolf did not close: {d} after {start}");
        dist = d;
    }
    assert_eq!(dist, 1, "and it stops at arm's length, where it hits");
}

#[test]
fn a_monster_out_of_sight_does_not_hunt() {
    let mut g = open_room();
    // A rat on the far side of the map, well outside the radius.
    put_monster(&mut g, MonsterKind::Rat, 38, 11);
    g.player = Pos::new(3, 3);
    g.refresh_fov();
    assert!(!monster_sees_player(&g.map, 38, 11, 3, 3));
    let mut moved = 0;
    let mut closing = 0;
    let mut last = Pos::new(38, 11);
    let mut last_d: i32 = (38i32 - 3).abs() + (11i32 - 3).abs();
    for _ in 0..60 {
        g.key('.');
        let m = g.monsters[0];
        let d = (m.x as i32 - g.player.x as i32).abs() + (m.y as i32 - g.player.y as i32).abs();
        if (m.x, m.y) != (last.x, last.y) {
            moved += 1;
        }
        if d < last_d {
            closing += 1;
        }
        last = Pos::new(m.x, m.y);
        last_d = d;
    }
    assert!(moved > 0, "a wandering monster moves");
    assert!(
        closing * 3 < 60,
        "a monster out of sight wanders, it does not hunt ({closing}/60 closing)"
    );
}

// ---------------------------------------------------------------------------
// Items, the stairs, the score
// ---------------------------------------------------------------------------

#[test]
fn items_apply_on_pickup_exactly_as_documented() {
    let mut g = open_room();
    g.hp = 6;
    g.items.push(Item {
        x: 3,
        y: 3,
        glyph: ITEM_FISH,
        value: 0,
    });
    g.key('g');
    assert_eq!(g.hp, 9, "a fish heals 3");
    assert_eq!(g.inventory, vec![ITEM_FISH]);
    // A fish at full health is not a surplus.
    g.items.push(Item {
        x: 3,
        y: 3,
        glyph: ITEM_FISH,
        value: 0,
    });
    g.key('g');
    assert_eq!(g.hp, g.max_hp, "and never overfills");

    g.items.push(Item {
        x: 3,
        y: 3,
        glyph: ITEM_PARKA,
        value: 0,
    });
    g.key('g');
    assert_eq!(g.defence, START_DEFENCE + 1, "a parka is +1 defence");

    g.items.push(Item {
        x: 3,
        y: 3,
        glyph: ITEM_GOLD,
        value: 4,
    });
    g.key('g');
    assert_eq!(g.gold, 4, "gold scores");

    // The scroll maps the floor: every tile becomes explored.
    let far = idx(50, 13);
    assert!(!g.seen[far]);
    g.items.push(Item {
        x: 3,
        y: 3,
        glyph: ITEM_SCROLL,
        value: 0,
    });
    g.key('g');
    assert!(g.seen[far], "the scroll maps the whole floor");
    assert!(g.message.contains("scroll"));

    // Nothing underfoot is a message, not a turn.
    let turns = g.turns;
    g.key('g');
    assert_eq!(g.message, "There is nothing here.");
    assert_eq!(g.turns, turns, "and it costs nothing");
}

#[test]
fn the_stairs_go_down_and_the_fifth_floor_is_the_last() {
    let mut g = open_room();
    // Standing next to the stairs is not enough: you have to be on them.
    g.key('>');
    assert_eq!(g.floor, 1);
    assert_eq!(g.message, "There are no stairs here.");

    g.player = g.stairs;
    g.refresh_fov();
    for depth in 2..=FLOORS {
        g.player = g.stairs;
        g.key('>');
        assert_eq!(g.floor, depth, "the stairs go down");
        assert_eq!(g.deepest, depth);
        assert_eq!(g.player, g.entry, "and you arrive at the entry");
        assert_ne!(g.map[idx(g.player.x, g.player.y)], WALL);
    }
    assert_eq!(g.floor, FLOORS);
    assert!(
        g.monsters.iter().any(|m| m.kind == MonsterKind::Thaw),
        "floor 5 holds the boss"
    );
    g.player = g.stairs;
    g.key('>');
    assert_eq!(g.floor, FLOORS, "and there is no floor 6");
    assert!(g.message.contains("Thaw"));
}

#[test]
fn a_descent_keeps_the_penguin_and_the_score() {
    let mut g = open_room();
    g.gold = 7;
    g.kills = 3;
    g.hp = 5;
    g.defence = 2;
    g.turns = 40;
    g.player = g.stairs;
    g.key('>');
    assert_eq!(g.floor, 2);
    assert_eq!(g.gold, 7, "the gold comes down with you");
    assert_eq!(g.kills, 3);
    assert_eq!(g.hp, 5);
    assert_eq!(g.defence, 2);
    assert_eq!(g.turns, 41, "and the descent is a turn");
    assert_eq!(g.score(), 2 * 100 + 7 + 3, "score = floor*100 + gold + kills");
}

#[test]
fn the_score_formula_is_floor_times_100_plus_gold_plus_kills() {
    let mut g = Game::new(11);
    assert_eq!(g.score(), 1 * 100 + 0 + 0);
    g.floor = 4;
    g.gold = 12;
    g.kills = 5;
    assert_eq!(g.score(), 4 * 100 + 12 + 5);
    assert_eq!(SCORE_PER_FLOOR, 100);
    assert_eq!(SCORE_PER_KILL, 1);
}

#[test]
fn every_player_action_costs_exactly_one_turn() {
    let mut g = open_room();
    let free = |g: &mut Game, c: char| {
        let t = g.turns;
        g.key(c);
        g.turns == t
    };
    assert!(free(&mut g, 'i'), "the inventory costs no turn");
    let mut g = open_room();
    g.player = Pos::new(1, 1); // the rock is at x = 0
    g.key('h');
    assert_eq!(g.player, Pos::new(1, 1), "walking into the rock does not move you");
    assert_eq!(g.turns, 0);
    assert_eq!(g.message, "You hit the wall.");
    g.key('z');
    assert_eq!(g.turns, 0, "an unknown key is ignored");
    let t = g.turns;
    g.key('j');
    assert_eq!(g.turns, t + 1, "a step is a turn");
    let t = g.turns;
    g.key('.');
    assert_eq!(g.turns, t + 1, "so is a wait");
    let t = g.turns;
    g.key('i');
    assert_eq!(g.turns, t, "and the inventory is not");
}

#[test]
fn the_eight_movement_keys_are_the_documented_ones() {
    for (c, (dx, dy)) in [
        ('h', (-1, 0)),
        ('j', (0, 1)),
        ('k', (0, -1)),
        ('l', (1, 0)),
        ('y', (-1, -1)),
        ('u', (1, -1)),
        ('b', (-1, 1)),
        ('n', (1, 1)),
    ] {
        let i = Game::dir_of(c).expect("a movement key");
        assert_eq!(DIRS[i], (dx, dy), "{c} is not the documented direction");
        assert_eq!(Game::dir_key(i), c);
        let mut g = open_room();
        g.player = Pos::new(10, 6);
        g.refresh_fov();
        g.key(c);
        assert_eq!(
            (g.player.x as i32, g.player.y as i32),
            (10 + dx, 6 + dy),
            "{c} did not move that way"
        );
    }
}

// ---------------------------------------------------------------------------
// Saved positions
// ---------------------------------------------------------------------------

#[test]
fn a_saved_position_round_trips() {
    let mut g = Game::new(5);
    for c in "jjjlll..gg".chars() {
        g.key(c);
    }
    let text = g.state_text();
    let back = parse_state(&text).expect("the position reads back");
    assert_eq!(back.map, g.map, "the map survives");
    assert_eq!(back.monsters, g.monsters, "and the monsters, wounded and all");
    assert_eq!(back.items, g.items);
    assert_eq!(back.player, g.player);
    assert_eq!(back.floor, g.floor);
    assert_eq!(back.hp, g.hp);
    assert_eq!(back.gold, g.gold);
    assert_eq!(back.kills, g.kills);
    assert_eq!(back.turns, g.turns);
    assert_eq!(back.state, g.state);
    assert_eq!(back.stairs, g.stairs);
    assert_eq!(back.entry, g.entry);
    assert_eq!(back.rng.state(), g.rng.state(), "and the die is where it left off");
}

#[test]
fn a_broken_position_is_an_error_not_a_panic() {
    assert!(parse_state("").is_none());
    assert!(parse_state("floor=9\nmap=\n").is_none());
    assert!(parse_state("floor=1\nplayer=99,99\n").is_none());
    assert!(parse_state("floor=1\nstairs=2,2\nmap=\n").is_none());
    // A map with a character that is not terrain.
    let text = Game::new(1).state_text().replacen('.', "x", 1);
    assert!(parse_state(&text).is_none(), "a map row of junk is not a position");
}

// ---------------------------------------------------------------------------
// The frame and the fingerprints
// ---------------------------------------------------------------------------

#[test]
fn the_frame_is_a_pure_function_of_state() {
    let g = Game::new(17);
    let a = frame(&g);
    let b = frame(&g);
    assert_eq!(a, b, "the same state draws the same frame");
    let lines: Vec<&str> = a.lines().collect();
    assert_eq!(lines.len(), REF_HEIGHT, "the reference frame is 24 rows");
    for line in &lines {
        assert!(visible_len(line) <= REF_WIDTH, "a row overflowed 80 columns");
    }
    assert!(a.contains('@'), "the penguin is on the frame");
    assert!(a.contains(&format!("{}/{}", 1, FLOORS)), "the panel says the floor");
    assert!(a.contains(LEGEND), "and the keys");
    // The frame is a function of state, so a state change changes it.
    let mut moved = Game::new(17);
    moved.key('j');
    assert_ne!(frame(&moved), a);
}

#[test]
fn a_small_window_drops_the_panel_then_the_border_and_keeps_the_map() {
    let g = Game::new(23);
    let full = render(&g, RenderOpts::default());
    assert!(full.contains("score"), "80x24 has the score panel");
    assert!(full.contains('┌'), "and the border");

    let narrow = render(
        &g,
        RenderOpts {
            width: 60,
            height: REF_HEIGHT,
            ..Default::default()
        },
    );
    assert!(!narrow.contains("best floor"), "a narrow window drops the panel");
    assert!(narrow.contains('┌'), "and keeps the border");
    assert!(narrow.contains('@'), "and keeps the map");

    let tiny = render(
        &g,
        RenderOpts {
            width: 30,
            height: 10,
            ..Default::default()
        },
    );
    assert!(!tiny.contains('┌'), "a tiny window drops the border");
    assert!(!tiny.contains(LEGEND), "and the legend");
    assert!(tiny.contains('#') || tiny.contains('.'), "and keeps the map");
    assert_eq!(tiny.lines().count(), 10, "and never draws more rows than asked");
    for line in tiny.lines() {
        assert!(visible_len(line) <= 30, "and never draws wider than asked");
    }
}

#[test]
fn the_plain_state_is_key_value_lines_a_gate_can_grep() {
    let g = Game::new(31);
    let text = plain_state(&g);
    assert!(!text.contains('\x1b'), "no escape codes in the headless output");
    for key in ["state", "floor", "score", "kills", "gold", "hp", "turns", "hash"] {
        assert!(
            text.lines().any(|l| l.starts_with(&format!("{key}="))),
            "plain_state is missing {key}="
        );
    }
    assert!(text.lines().all(|l| l.contains('=')), "every line is a key=value");
    assert_eq!(text.lines().count(), g.result_kv().len());
}

#[test]
fn a_scripted_session_is_bit_for_bit_reproducible() {
    // The acceptance shape: same seed, same script, same hash.
    let script = "jjjjllllnnnnbbbb..gg>jjjlllkkk";
    let run = |seed: u64| {
        let mut g = Game::new(seed);
        for line in script.chars() {
            g.key(line);
            g.tick();
        }
        (g.state_hash(), g.score(), g.floor, g.turns)
    };
    for seed in [1, 2, 3, 7, 11, 42, 1000] {
        let a = run(seed);
        let b = run(seed);
        assert_eq!(a, b, "seed {seed} diverged");
    }
    assert_ne!(run(1).0, run(2).0, "different seeds are different runs");
}

#[test]
fn the_demo_penguin_plays_the_game_without_a_terminal() {
    // The headless path: the autopilot drives the library with no pty, and it
    // makes progress: turns pass, monsters die, the score moves.
    let mut g = Game::new(21);
    for _ in 0..600 {
        if g.state != State::Play {
            break;
        }
        let keys = g.autopilot_keys();
        assert!(
            keys.chars().all(|c| matches!(c, 'h' | 'j' | 'k' | 'l' | 'y' | 'u' | 'b' | 'n' | '.' | 'g' | '>')),
            "the demo player only presses keys the game accepts"
        );
        for c in keys.chars() {
            g.key(c);
        }
        g.tick();
    }
    assert!(g.turns > 100, "the demo penguin took its turns");
    assert!(g.kills > 0, "and it fought its way down: {} kills", g.kills);
    assert!(g.score() >= 100);
    assert!(g.seen.iter().filter(|b| **b).count() > 100, "and it explored");
}

#[test]
fn the_tuning_the_docs_quote_is_the_tuning_the_code_uses() {
    assert_eq!(START_HP, 12);
    assert_eq!(START_MAX_HP, 12);
    assert_eq!(START_WEAPON, 0);
    assert_eq!(START_DEFENCE, 0);
    assert_eq!(ATTACK_DIE_FACES, 4);
    assert_eq!(ATTACK_DIE_BONUS, 1);
    assert_eq!(FISH_HEAL, 3);
    assert_eq!(PARKA_DEFENCE, 1);
    assert_eq!(FOV_RADIUS, 7);
    assert_eq!(FLOORS, 5);
    assert_eq!(MAP_W, 54);
    assert_eq!(MAP_H, 15);
    assert_eq!(REF_WIDTH, 80);
    assert_eq!(REF_HEIGHT, 24);
    assert_eq!(MonsterKind::Rat.hp(), 2);
    assert_eq!(MonsterKind::Rat.damage(), 1);
    assert_eq!(MonsterKind::IceCrab.hp(), 4);
    assert_eq!(MonsterKind::IceCrab.damage(), 2);
    assert_eq!(MonsterKind::Wolf.hp(), 6);
    assert_eq!(MonsterKind::Wolf.damage(), 3);
    assert_eq!(MonsterKind::Thaw.hp(), 20);
    assert_eq!(MonsterKind::Thaw.damage(), 4);
    assert_eq!(GOLD_MIN, 1);
    assert_eq!(GOLD_MAX, 5);
    assert_eq!(ROOMS_MIN, 4);
    assert_eq!(ROOMS_MAX, 9);
    assert_eq!(ROOM_W_MIN, 4);
    assert_eq!(ROOM_W_MAX, 10);
    assert_eq!(ROOM_H_MIN, 4);
    assert_eq!(ROOM_H_MAX, 6);
    assert_eq!(MonsterKind::from_glyph('T'), Some(MonsterKind::Thaw));
    assert_eq!(MonsterKind::from_glyph('c'), Some(MonsterKind::IceCrab));
    assert_eq!(MonsterKind::from_glyph('Z'), None);
}

#[test]
fn the_rng_is_a_total_deterministic_stream() {
    let mut a = Rng::new(7);
    let mut b = Rng::new(7);
    for _ in 0..64 {
        assert_eq!(a.next_u64(), b.next_u64());
    }
    // A zero seed is remapped: xorshift must never sit on 0.
    let mut z = Rng::new(0);
    let v = z.next_u64();
    assert_ne!(z.state(), 0);
    assert_ne!(v, 0);
    // `below` stays in range.
    let mut r = Rng::new(99);
    for _ in 0..200 {
        assert!(r.below(9) < 9);
        assert!((4..=9).contains(&r.range(4, 9)));
    }
}
