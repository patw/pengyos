//! The rules of Frostbite Solitaire, asserted against the pure library.
//!
//! Every test here drives `pengy_solitaire::Game` directly: no terminal, no
//! clock, no keyboard (the committed fixtures are the one thing these tests
//! read). Same seed + same script => the same state hash, which is what
//! `--replay` and the repo gate rely on.
//!
//! Card ids are `suit * 13 + rank`: Spades 0..12, Hearts 13..25, Clubs
//! 26..38, Diamonds 39..51, rank 0 = Ace .. 12 = King. The tests spell cards
//! as `up(H, RANK_KING)` so a wrong suit can never hide in a magic number.

use std::fs;
use std::path::PathBuf;

use pengy_solitaire::*;

const S: usize = 0; // Spades, black
const H: usize = 1; // Hearts, red
const C: usize = 2; // Clubs, black
const D: usize = 3; // Diamonds, red

fn id(suit: usize, rank: usize) -> u8 {
    (suit * 13 + rank) as u8
}

/// A face-up card.
fn up(suit: usize, rank: usize) -> Card {
    Card {
        id: id(suit, rank),
        up: true,
    }
}

/// A face-down card.
fn down(suit: usize, rank: usize) -> Card {
    Card {
        id: id(suit, rank),
        up: false,
    }
}

/// A face-up run: `run(&[(H, 11), (S, 10)])` is Q♥ 10♠.
fn run(cards: &[(usize, usize)]) -> Vec<Card> {
    cards.iter().map(|(s, r)| up(*s, *r)).collect()
}

fn empty() -> Vec<Card> {
    Vec::new()
}

fn empty4() -> [Vec<Card>; 4] {
    [empty(), empty(), empty(), empty()]
}

fn empty7() -> [Vec<Card>; 7] {
    [empty(), empty(), empty(), empty(), empty(), empty(), empty()]
}

/// A game with the piles replaced verbatim — the fixture builder for the
/// legality tests.
fn position(
    tableau: [Vec<Card>; 7],
    foundations: [Vec<Card>; 4],
    waste: Vec<Card>,
    stock: Vec<Card>,
) -> Game {
    let mut g = Game::new(1);
    g.tableau = tableau;
    g.foundations = foundations;
    g.waste = waste;
    g.stock = stock;
    g.message = String::new();
    g
}

/// Every pile on the table, for the "all 52 cards are somewhere" assertions.
fn all_piles(g: &Game) -> Vec<&[Card]> {
    let mut v: Vec<&[Card]> = Vec::with_capacity(13);
    for p in g.tableau.iter() {
        v.push(p);
    }
    for p in g.foundations.iter() {
        v.push(p);
    }
    v.push(&g.stock);
    v.push(&g.waste);
    v
}

fn ids(pile: &[Card]) -> Vec<u8> {
    pile.iter().map(|c| c.id).collect()
}

// ---------------------------------------------------------------------------
// The deck and the deal
// ---------------------------------------------------------------------------

#[test]
fn the_deck_is_52_distinct_cards() {
    let deck = ordered_deck();
    assert_eq!(deck.len(), DECK_SIZE);
    let mut seen = [false; DECK_SIZE];
    for i in deck {
        assert!(!seen[i as usize], "card {i} appears twice");
        seen[i as usize] = true;
    }
    assert!(seen.iter().all(|s| *s), "every card is in the deck");

    // Ranks and suits are the standard ones, and the colours are the standard
    // ones: Hearts and Diamonds red, Spades and Clubs black.
    assert_eq!((suit(0), rank(0)), (S, RANK_ACE));
    assert_eq!((suit(12), rank(12)), (S, RANK_KING));
    assert_eq!((suit(13), rank(13)), (H, RANK_ACE));
    assert_eq!((suit(51), rank(51)), (D, RANK_KING));
    assert_eq!(RANK_CHARS[rank(12)], 'K');
    assert_eq!(RANK_CHARS[rank(9)], 'T');
    assert_eq!(face(id(H, 0), false), "A♥");
    assert_eq!(face(id(H, 0), true), "AH");
    assert_eq!(face(id(D, 12), false), "K♦");
    assert!(is_red(id(H, 0)) && is_red(id(D, 12)));
    assert!(!is_red(id(S, 0)) && !is_red(id(C, 12)));
    assert_eq!(opposite_suits(id(H, 0)), [S, C]);
    assert_eq!(opposite_suits(id(C, 0)), [H, D]);
}

#[test]
fn the_seeded_shuffle_is_a_permutation_and_is_deterministic() {
    for seed in [0u64, 1, 2, 7, 42, 9999, u64::MAX] {
        let mut a = Rng::new(seed);
        let mut b = Rng::new(seed);
        let x = shuffled_deck(&mut a);
        let y = shuffled_deck(&mut b);
        assert_eq!(x, y, "seed {seed} must deal the same deck twice");
        let mut seen = [false; DECK_SIZE];
        for i in x {
            assert!(!seen[i as usize], "seed {seed} dealt {i} twice");
            seen[i as usize] = true;
        }
        assert!(seen.iter().all(|s| *s), "seed {seed} dealt all 52");
    }
    // Different seeds deal different decks: the generator is not a counter.
    let mut a = Rng::new(1);
    let mut b = Rng::new(2);
    assert_ne!(shuffled_deck(&mut a), shuffled_deck(&mut b));
}

#[test]
fn the_deal_is_the_seven_pile_klondike_layout() {
    let g = Game::new(7);
    // 1..7 cards per column, the top of each face up, everything under it face
    // down, 24 cards left in the stock.
    for (i, pile) in g.tableau.iter().enumerate() {
        assert_eq!(pile.len(), i + 1, "column {} has {} cards", i + 1, pile.len());
        assert!(pile[pile.len() - 1].up, "column {} top must be face up", i + 1);
        for card in &pile[..pile.len() - 1] {
            assert!(!card.up, "column {} has a face-down card above the top", i + 1);
        }
    }
    assert_eq!(g.stock.len(), 24);
    assert!(g.stock.iter().all(|card| !card.up), "the stock is face down");
    assert!(g.waste.is_empty());
    assert!(g.foundations.iter().all(|f| f.is_empty()));
    assert_eq!(g.foundation_count(), 0);
    assert_eq!(g.score, 0);
    assert_eq!(g.moves, 0);
    assert_eq!(g.state, State::Play);

    // Every card is dealt exactly once.
    let mut seen = [false; DECK_SIZE];
    for pile in all_piles(&g) {
        for card in pile {
            assert!(!seen[card.id as usize], "card {} dealt twice", card.id);
            seen[card.id as usize] = true;
        }
    }
    assert!(seen.iter().all(|s| *s), "all 52 cards are dealt");
}

#[test]
fn the_same_seed_deals_the_same_game() {
    assert_eq!(
        Game::new(42).state_hash(),
        Game::new(42).state_hash(),
        "a seed is a deal"
    );
    assert_ne!(
        Game::new(42).state_hash(),
        Game::new(43).state_hash(),
        "neighbouring seeds deal different games"
    );
}

// ---------------------------------------------------------------------------
// Tableau legality: descending by rank, alternating colour
// ---------------------------------------------------------------------------

#[test]
fn a_tableau_build_accepts_descending_alternating_colour() {
    let g = position(
        [
            run(&[(S, 12)]), // K♠, black
            run(&[(H, 11)]), // Q♥, red
            run(&[(S, 1)]),  // 2♠
            empty(),
            empty(),
            empty(),
            empty(),
        ],
        empty4(),
        empty(),
        vec![down(S, 0)],
    );
    // Q♥ onto K♠: one rank down, opposite colour. Legal.
    assert!(g.can_drop(&[up(H, 11)], Area::Tableau, 0));
    // 2♠ onto K♠: same colour, and nowhere near one rank down. Illegal.
    assert!(!g.can_drop(&[up(S, 1)], Area::Tableau, 0));
    // K♠ onto Q♥: building up. Illegal.
    assert!(!g.can_drop(&[up(S, 12)], Area::Tableau, 1));
    // Same colour, one rank down: Q♣ onto K♠. Illegal.
    assert!(!g.can_drop(&[up(C, 11)], Area::Tableau, 0));
    // A face-down card is never playable.
    assert!(!g.can_drop(&[down(H, 11)], Area::Tableau, 0));
    // The stock and the waste are not destinations.
    assert!(!g.can_drop(&[up(H, 11)], Area::Stock, 0));
    assert!(!g.can_drop(&[up(H, 11)], Area::Waste, 0));
}

#[test]
fn only_a_king_opens_an_empty_column() {
    let g = position(
        [empty(), empty(), empty(), empty(), empty(), empty(), run(&[(S, 0)])],
        empty4(),
        empty(),
        empty(),
    );
    assert!(g.can_drop(&[up(S, 12)], Area::Tableau, 0), "K♠ opens a column");
    assert!(g.can_drop(&[up(D, 12)], Area::Tableau, 0), "K♦ opens a column");
    assert!(!g.can_drop(&[up(S, 11)], Area::Tableau, 0), "Q♠ does not");
    assert!(!g.can_drop(&[up(S, 0)], Area::Tableau, 0), "A♠ does not");
    // A run headed by a King opens a column too.
    assert!(g.can_drop(&run(&[(S, 12), (H, 11)]), Area::Tableau, 0));
    // A run headed by a Queen does not.
    assert!(!g.can_drop(&run(&[(S, 11), (H, 10)]), Area::Tableau, 0));
}

#[test]
fn a_foundation_takes_one_card_of_its_suit_ascending() {
    let g = position(
        empty7(),
        [run(&[(S, 0), (S, 1)]), empty(), empty(), empty()],
        empty(),
        empty(),
    );
    // The Spades foundation holds A♠ 2♠, so it is waiting for the 3♠.
    assert!(g.can_drop(&[up(S, 2)], Area::Foundation, 0), "3♠ is next");
    assert!(!g.can_drop(&[up(S, 3)], Area::Foundation, 0), "4♠ is too high");
    assert!(!g.can_drop(&[up(S, 1)], Area::Foundation, 0), "2♠ is already up");
    assert!(!g.can_drop(&[up(H, 2)], Area::Foundation, 0), "3♥ is the wrong suit");
    // An empty foundation takes only its own Ace.
    assert!(g.can_drop(&[up(H, 0)], Area::Foundation, 1), "A♥ opens Hearts");
    assert!(!g.can_drop(&[up(H, 1)], Area::Foundation, 1), "2♥ needs the Ace up");
    assert!(!g.can_drop(&[up(S, 0)], Area::Foundation, 1), "A♠ is not Hearts");
    // Two cards never go to a foundation at once.
    assert!(!g.can_drop(&run(&[(S, 2), (S, 3)]), Area::Foundation, 0));
}

#[test]
fn a_run_is_a_legal_unit_only_when_it_is_ordered_and_alternating() {
    // 9♠ 8♥ 7♣: descending, alternating. Legal.
    assert!(Game::legal_run(&run(&[(S, 8), (H, 7), (C, 6)])));
    // 9♠ 8♣: same colour. Illegal.
    assert!(!Game::legal_run(&run(&[(S, 8), (C, 7)])));
    // 9♠ 7♥: a rank missing. Illegal.
    assert!(!Game::legal_run(&run(&[(S, 8), (H, 6)])));
    // 9♠ 8♥ 7♥: the last step repeats a colour. Illegal.
    assert!(!Game::legal_run(&run(&[(S, 8), (H, 7), (H, 6)])));
    // A face-down card inside the run. Illegal.
    assert!(!Game::legal_run(&[up(S, 8), down(H, 7), up(C, 6)]));
    assert!(!Game::legal_run(&[]), "an empty run is not a run");
    // A single card is always a legal run.
    assert!(Game::legal_run(&[up(S, 8)]));
}

#[test]
fn the_movable_run_is_the_longest_legal_suffix_of_a_column() {
    let g = position(
        [
            vec![down(S, 0), up(S, 8), up(H, 7), up(C, 6)], // -A♠ 9♠ 8♥ 7♣
            vec![down(S, 0), up(S, 8), up(C, 6)],           // -A♠ 9♠ 7♣ (broken)
            vec![down(S, 0), up(S, 12), up(H, 11), up(S, 8)], // -A♠ K♠ Q♥ 9♠ (broken)
            empty(),
            empty(),
            empty(),
            run(&[(H, 7)]),
        ],
        empty4(),
        empty(),
        empty(),
    );
    assert_eq!(g.movable_run(0).len(), 3, "the whole alternating run");
    assert_eq!(g.movable_run(1).len(), 1, "a broken build yields only its top");
    assert_eq!(g.movable_run(2).len(), 1, "the run must be contiguous");
    assert!(g.movable_run(3).is_empty(), "an empty column has nothing to take");
    assert_eq!(g.movable_run(6).len(), 1);
    // A face-down top card is not a legal run: the rules flip it first.
    let mut h = Game::new(1);
    h.tableau[0] = vec![down(S, 0)];
    assert_eq!(h.movable_run(0).len(), 1);
    assert!(
        !Game::legal_run(&h.movable_run(0)),
        "a face-down card is not playable"
    );
}

#[test]
fn a_multi_card_run_moves_as_a_unit_and_keeps_its_order() {
    let mut g = position(
        [
            vec![down(S, 0), up(S, 8), up(H, 7), up(C, 6)], // -A♠ 9♠ 8♥ 7♣
            run(&[(S, 9)]),                                 // 10♠
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
        ],
        empty4(),
        empty(),
        empty(),
    );
    // 9♠ 8♥ 7♣ does not fit on 10♠: same colour.
    let sel = g.select(Area::Tableau, 0).unwrap();
    assert_eq!(sel.count, 3, "the whole run is picked up");
    assert!(!g.drop(sel, Area::Tableau, 1));
    // A 10♥ on top is one rank above the run head and the other colour: it lands.
    g.tableau[1].push(up(H, 9));
    let sel = g.select(Area::Tableau, 0).unwrap();
    assert!(g.drop(sel, Area::Tableau, 1));
    assert_eq!(
        ids(&g.tableau[1]),
        vec![id(S, 9), id(H, 9), id(S, 8), id(H, 7), id(C, 6)],
        "the run keeps its order and lands on top"
    );
    assert_eq!(g.tableau[0].len(), 1, "only the face-down Ace is left behind");
    assert!(g.tableau[0][0].up, "the uncovered card flipped");
    assert_eq!(g.score, SCORE_FLIP, "the flip scored, the build is free");
    assert_eq!(g.moves, 1);
}

#[test]
fn an_illegal_drop_is_refused_and_the_cards_stay_in_hand() {
    let mut g = position(
        [
            run(&[(S, 12)]),
            run(&[(S, 0)]),
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
        ],
        empty4(),
        empty(),
        empty(),
    );
    let sel = g.select(Area::Tableau, 0).unwrap(); // K♠
    assert!(!g.drop(sel, Area::Tableau, 1), "K♠ onto A♠ is illegal");
    assert_eq!(g.selection, Some(sel), "the selection survives a refused drop");
    assert_eq!(g.tableau[0].len(), 1);
    assert_eq!(g.moves, 0);
    // Space on the source puts them back down.
    assert!(!g.drop(sel, Area::Tableau, 0));
    assert_eq!(g.selection, None);
}

// ---------------------------------------------------------------------------
// The stock, the waste, and unlimited redeals
// ---------------------------------------------------------------------------

#[test]
fn drawing_deals_three_cards_in_order() {
    let mut g = Game::new(1);
    let stock = g.stock.clone();
    let top3: Vec<u8> = stock[stock.len() - DRAW_COUNT..]
        .iter()
        .map(|c| c.id)
        .collect();
    g.draw();
    assert_eq!(g.waste.len(), DRAW_COUNT);
    assert!(g.waste.iter().all(|c| c.up), "the waste is face up");
    assert_eq!(
        ids(&g.waste),
        top3,
        "the order of the stock is preserved on the way to the waste"
    );
    assert_eq!(g.stock.len(), 24 - DRAW_COUNT);
    assert_eq!(g.moves, 1);
    assert_eq!(g.redeals, 0);
}

#[test]
fn the_last_draw_takes_what_is_left_and_an_empty_stock_recycles_the_waste() {
    let mut g = Game::new(1);
    g.stock = vec![down(S, 1), down(S, 2)];
    g.waste = empty();
    g.draw();
    assert_eq!(g.waste.len(), 2, "draw takes what is there, not three");
    assert!(g.stock.is_empty());
    g.draw();
    assert_eq!(g.stock.len(), 2, "the waste went back into the stock");
    assert!(g.waste.is_empty());
    assert!(g.stock.iter().all(|c| !c.up), "the recycled stock is face down");
    assert_eq!(
        ids(&g.stock),
        vec![id(S, 2), id(S, 1)],
        "recycling turns the pile over: the waste top becomes the bottom"
    );
    assert_eq!(g.redeals, 1);
    assert_eq!(g.score, 0, "the recycle penalty is floored at zero");
}

#[test]
fn recycling_needs_a_waste_and_costs_a_hundred() {
    let mut g = Game::new(1);
    g.stock = empty();
    g.waste = empty();
    g.score = 500;
    g.draw();
    assert!(g.stock.is_empty() && g.waste.is_empty(), "nothing to recycle");
    assert_eq!(g.redeals, 0, "and no pass was counted");
    assert_eq!(g.score, 500, "and no penalty was taken");

    g.waste = vec![up(S, 3), up(S, 4)];
    g.draw();
    assert_eq!(g.redeals, 1);
    assert_eq!(g.score, 400, "recycling costs 100");
    assert_eq!(g.stock.len(), 2);
}

#[test]
fn passes_are_unlimited() {
    let mut g = Game::new(1);
    for _ in 0..6 {
        while !g.stock.is_empty() {
            g.draw();
        }
        g.draw(); // recycle
    }
    assert!(g.redeals >= 6, "the stock keeps coming back: {} passes", g.redeals);
    assert_eq!(g.score, 0, "and the score floors at zero");
    assert_eq!(g.state, State::Play, "a pass is not an end");
}

// ---------------------------------------------------------------------------
// Auto-flip, auto-collect, and the score
// ---------------------------------------------------------------------------

#[test]
fn a_card_flips_the_moment_it_is_uncovered() {
    let mut g = position(
        [
            vec![down(S, 0), up(S, 1)], // -A♠ 2♠
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
        ],
        [run(&[(S, 0)]), empty(), empty(), empty()],
        empty(),
        empty(),
    );
    let sel = g.select(Area::Tableau, 0).unwrap();
    assert_eq!(sel.count, 1);
    assert!(g.drop(sel, Area::Foundation, 0), "2♠ onto A♠");
    assert!(g.tableau[0][0].up, "the Ace flipped the moment it was uncovered");
    assert_eq!(g.score, SCORE_TO_FOUNDATION + SCORE_FLIP);
    assert_eq!(g.moves, 1, "a flip is not a move");

    // Nothing uncovered above the top card: the buried Ace stays buried, and
    // the card that *does* become the top flips.
    let mut h = position(
        [
            vec![down(S, 0), down(H, 0), up(S, 1)], // -A♠ -2♥ 2♠
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
        ],
        [run(&[(S, 0)]), empty(), empty(), empty()],
        empty(),
        empty(),
    );
    let sel = h.select(Area::Tableau, 0).unwrap();
    assert!(h.drop(sel, Area::Foundation, 0));
    assert!(h.tableau[0][1].up, "the new top card flipped");
    assert!(!h.tableau[0][0].up, "the buried Ace stays buried");
    assert_eq!(h.score, SCORE_TO_FOUNDATION + SCORE_FLIP, "raise plus flip");
}

#[test]
fn auto_collect_only_takes_safe_cards_and_is_idempotent() {
    // The four aces are on the table: all of them are safe, so `f` takes them.
    // The 2♠ is the next rank with both opposite-colour aces up, and the 2♦
    // likewise — but each of them is sitting on a face-down card, so taking
    // either would break a column the rules do not let auto-collect break.
    let mut g = position(
        [
            run(&[(S, 0)]),
            run(&[(H, 0)]),
            run(&[(C, 0)]),
            run(&[(D, 0)]),
            vec![down(H, 1), up(S, 1)], // -2♥ 2♠
            vec![down(C, 2), up(D, 1)], // -3♣ 2♦
            empty(),
        ],
        empty4(),
        empty(),
        empty(),
    );
    let n = g.auto_collect();
    assert_eq!(n, 4, "the four aces; the 2♠ and the 2♦ are refused");
    assert_eq!(g.foundation_count(), 4);
    assert_eq!(g.score, 4 * SCORE_TO_FOUNDATION);
    assert_eq!(g.auto_collect(), 0, "a second pass collects nothing");
    let hash = g.state_hash();
    assert_eq!(g.auto_collect(), 0, "and a third");
    assert_eq!(g.state_hash(), hash, "auto-collect is idempotent");
}

#[test]
fn auto_collect_never_breaks_a_build() {
    // 8♥ on top of 9♠: the Hearts foundation is waiting for the 8♥, both
    // opposite colours are at the same depth, and the card beneath is a legal
    // build, so it is safe. The 9♠ left behind is not the next rank, so the
    // pass stops there.
    let seven = |suit: usize| -> Vec<(usize, usize)> { (0..7).map(|r| (suit, r)).collect() };
    let mut g = position(
        [
            run(&[(S, 8), (H, 7)]),
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
        ],
        [
            run(&seven(S)),
            run(&seven(H)),
            run(&seven(C)),
            run(&seven(D)),
        ],
        empty(),
        empty(),
    );
    let n = g.auto_collect();
    assert_eq!(n, 1, "the 8♥ is safe, the 9♠ is not the next rank");
    assert_eq!(ids(&g.tableau[0]), vec![id(S, 8)]);
    assert_eq!(g.foundations[H].len(), 8);
    assert_eq!(g.auto_collect(), 0);
}

#[test]
fn auto_collect_refuses_a_card_that_could_strand_a_build() {
    // The 2♠ is the next rank, and the opposite-colour aces are up, so the
    // count says safe. It is sitting on the 3♠ — the same colour, so that is
    // not a legal build, and taking the 2♠ would leave a broken column behind.
    // Auto-collect leaves it alone.
    let mut g = position(
        [
            run(&[(S, 2), (S, 1)]), // 3♠ 2♠
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
        ],
        [
            run(&[(S, 0)]),
            run(&[(H, 0)]),
            run(&[(C, 0)]),
            run(&[(D, 0)]),
        ],
        empty(),
        empty(),
    );
    assert_eq!(g.auto_collect(), 0, "the build is worth more than 10 points");
    assert_eq!(g.tableau[0].len(), 2);
}

#[test]
fn the_score_table_is_the_standard_klondike_one() {
    assert_eq!(Game::score_for(Area::Waste, Area::Tableau), 5);
    assert_eq!(Game::score_for(Area::Tableau, Area::Foundation), 10);
    assert_eq!(Game::score_for(Area::Waste, Area::Foundation), 10);
    assert_eq!(Game::score_for(Area::Foundation, Area::Tableau), -15);
    assert_eq!(Game::score_for(Area::Tableau, Area::Tableau), 0);
    assert_eq!(SCORE_FLIP, 5);
    assert_eq!(SCORE_RECYCLE, 100);

    // A card back off a foundation costs 15, and the score never goes negative.
    let full: Vec<(usize, usize)> = (0..=12).map(|r| (S, r)).collect();
    let mut g = position(
        empty7(),
        [run(&full), empty(), empty(), empty()],
        empty(),
        empty(),
    );
    g.score = 10;
    let sel = g.select(Area::Foundation, 0).unwrap(); // K♠
    assert_eq!(sel.count, 1);
    assert!(g.drop(sel, Area::Tableau, 0), "a King may come back down");
    assert_eq!(g.score, 0, "10 - 15 floors at zero");
    assert_eq!(g.tableau[0].len(), 1);

    g.score = 100;
    let sel = g.select(Area::Tableau, 0).unwrap();
    assert!(g.drop(sel, Area::Foundation, 0), "and it can go back up");
    assert_eq!(g.score, 110);

    // A waste card onto the tableau is 5.
    let mut h = position(
        [
            run(&[(S, 12)]),
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
            empty(),
        ],
        empty4(),
        run(&[(H, 11)]),
        empty(),
    );
    let sel = h.select(Area::Waste, 0).unwrap();
    assert!(h.drop(sel, Area::Tableau, 0));
    assert_eq!(h.score, SCORE_WASTE_TO_TABLEAU);
}

// ---------------------------------------------------------------------------
// Undo
// ---------------------------------------------------------------------------

#[test]
fn undo_restores_the_exact_previous_state() {
    let mut g = Game::new(7);
    let before = g.state_hash();
    g.key('d');
    g.tick();
    let drawn = g.state_hash();
    assert_ne!(before, drawn);

    g.key('u');
    g.tick();
    assert_eq!(g.state_hash(), before, "undo restores the state hash exactly");

    // A deeper stack: play a mixed opening, then undo every level in order.
    // A keypress that changes nothing (an `f` with nothing safe) is not an undo
    // level, so the stack walks the *distinct* states, not the keypresses.
    let mut h = Game::new(7);
    let start = h.state_hash();
    let mut hashes = vec![start];
    for keys in ["d", "f", "d", "d", "f", "d"] {
        h.key(keys.chars().next().unwrap());
        h.tick();
        let x = h.state_hash();
        if hashes.last() != Some(&x) {
            hashes.push(x);
        }
    }
    // The last recorded hash is where we already are, so the walk starts one
    // level back.
    for expected in hashes.iter().rev().skip(1) {
        h.key('u');
        h.tick();
        assert_eq!(h.state_hash(), *expected, "each undo level is exact");
    }
    assert_eq!(h.state_hash(), start);
    assert!(!h.undo(), "an empty undo stack says so and changes nothing");
}

#[test]
fn a_keypress_that_changes_nothing_adds_no_undo_level() {
    let mut g = Game::new(7);
    g.foundations = [empty(), empty(), empty(), empty()];
    g.tableau = [
        vec![down(S, 0), up(S, 5)],
        vec![down(H, 0), up(H, 5)],
        vec![down(C, 0), up(C, 5)],
        vec![down(D, 0), up(D, 5)],
        vec![down(S, 1), up(S, 2)],
        vec![down(H, 1), up(H, 2)],
        vec![down(C, 1), up(C, 2)],
    ];
    let levels = g.history.len();
    assert_eq!(g.auto_collect(), 0, "nothing is safe: no aces are up");
    assert_eq!(
        g.history.len(),
        levels,
        "and the pass that did nothing is not an undo level"
    );
    assert!(!g.undo(), "so there is nothing to undo");
    // A refused drop is not an undo level either.
    let sel = g.select(Area::Tableau, 0).unwrap();
    assert!(!g.drop(sel, Area::Foundation, 1), "6♠ is not Hearts");
    assert_eq!(g.history.len(), levels);
}

#[test]
fn undo_restores_the_score_and_the_move_count_too() {
    let mut g = Game::new(7);
    let (score, moves, redeals) = (g.score, g.moves, g.redeals);
    while !g.stock.is_empty() {
        g.draw();
    }
    g.draw(); // recycle: -100, floored at 0
    assert!(g.score == 0 && g.redeals == 1 && g.moves > 0);
    while g.undo() {}
    assert_eq!((g.score, g.moves, g.redeals), (score, moves, redeals));
}

#[test]
fn the_undo_stack_is_capped_at_fifty_levels() {
    let mut g = Game::new(7);
    for _ in 0..80 {
        g.draw();
        if g.stock.is_empty() {
            g.draw(); // recycle
        }
    }
    assert_eq!(MAX_HISTORY, 50);
    assert!(
        g.history.len() <= MAX_HISTORY,
        "the undo stack is capped: {} levels",
        g.history.len()
    );
}

// ---------------------------------------------------------------------------
// Cursor, keys, ticks
// ---------------------------------------------------------------------------

#[test]
fn the_cursor_walks_the_table_and_clamps() {
    let mut g = Game::new(1);
    assert_eq!((g.cursor.area, g.cursor.index), (Area::Stock, 0));
    g.key('h');
    assert_eq!(
        (g.cursor.area, g.cursor.index),
        (Area::Stock, 0),
        "clamped at the left edge"
    );
    for _ in 0..10 {
        g.key('l');
    }
    assert_eq!(
        (g.cursor.area, g.cursor.index),
        (Area::Foundation, 3),
        "clamped at the right edge of the top row"
    );
    g.key('j');
    assert_eq!((g.cursor.area, g.cursor.index), (Area::Tableau, 5));
    for _ in 0..10 {
        g.key('h');
    }
    assert_eq!((g.cursor.area, g.cursor.index), (Area::Tableau, 0));
    g.key('k');
    assert_eq!((g.cursor.area, g.cursor.index), (Area::Stock, 0));
    g.key('k');
    assert_eq!(
        (g.cursor.area, g.cursor.index),
        (Area::Stock, 0),
        "clamped at the top"
    );
    g.key('j');
    g.key('l');
    assert_eq!((g.cursor.area, g.cursor.index), (Area::Tableau, 1));
}

#[test]
fn a_tick_moves_the_clock_and_applies_the_queued_key() {
    let mut g = Game::new(1);
    assert_eq!(g.ticks, 0);
    assert_eq!(g.ms, 0);
    g.key('d');
    assert!(g.waste.is_empty(), "the key is queued, not applied");
    g.tick();
    assert_eq!(g.ticks, 1);
    assert_eq!(g.ms, TICK_MS);
    assert_eq!(g.waste.len(), DRAW_COUNT, "the queued key acted on the tick");
    assert_eq!(g.pending, None, "and was consumed");
    g.tick();
    assert_eq!(g.waste.len(), DRAW_COUNT, "an idle tick draws nothing");
    assert_eq!(g.ticks, 2);
}

#[test]
fn q_is_the_only_quit_key_and_the_rest_are_ignored() {
    let mut g = Game::new(1);
    for c in ['a', 's', 'w', 'x', 'z', 'p', 'r', '1', ' ', '\t', '\x1b', '\x03'] {
        assert!(!g.key(c), "{c:?} must not quit");
    }
    assert!(g.key('q'));
}

#[test]
fn n_deals_a_new_game_and_keeps_the_record() {
    let mut g = Game::new(1);
    g.best_score = 999;
    g.best_games = 3;
    g.draw();
    g.tick();
    assert_eq!(g.moves, 1);
    g.key('n');
    g.tick();
    assert_eq!(g.moves, 0);
    assert_eq!(g.ticks, 0);
    assert_eq!(g.score, 0);
    assert!(g.history.is_empty(), "a new deal has no undo history");
    assert_eq!(g.best_score, 999, "the record is not part of the deal");
    assert_eq!(g.best_games, 3);
    assert_eq!(g.stock.len(), 24);
    assert_eq!(g.state, State::Play);
}

// ---------------------------------------------------------------------------
// Winning
// ---------------------------------------------------------------------------

#[test]
fn the_game_is_won_when_the_foundations_hold_all_fifty_two_cards() {
    let mut g = Game::new(1);
    // 51 cards on the foundations, the King of Diamonds on the waste.
    let mut f: [Vec<Card>; 4] = empty4();
    for suit in [S, H, C] {
        for rank in 0..13 {
            f[suit].push(up(suit, rank));
        }
    }
    for rank in 0..12 {
        f[D].push(up(D, rank));
    }
    g.foundations = f;
    g.waste = vec![up(D, 12)];
    g.stock = empty();
    g.tableau = empty7();
    assert_eq!(g.foundation_count(), 51);
    assert_eq!(g.state, State::Play);
    let sel = g.select(Area::Waste, 0).unwrap();
    assert!(g.drop(sel, Area::Foundation, D));
    assert_eq!(g.foundation_count(), DECK_SIZE);
    assert_eq!(g.state, State::Win);

    // A won game is frozen: no draws, no moves, nothing on the table.
    let (stock, waste, found) = (g.stock.len(), g.waste.len(), g.foundation_count());
    g.key('d');
    g.tick();
    g.key('f');
    g.tick();
    assert_eq!(g.state, State::Win);
    assert_eq!((g.stock.len(), g.waste.len(), g.foundation_count()), (stock, waste, found));
    assert!(!g.can_drop(&[up(D, 11)], Area::Foundation, D));
}

// ---------------------------------------------------------------------------
// Rendering is a pure function of state
// ---------------------------------------------------------------------------

#[test]
fn the_frame_is_deterministic_and_fits_the_reference_window() {
    let g = Game::new(7);
    let a = frame(&g);
    let b = frame(&g);
    assert_eq!(a, b, "the same state draws the same frame");
    let lines: Vec<&str> = a.lines().collect();
    assert_eq!(lines.len(), REF_HEIGHT, "the reference window is 24 rows");
    for line in &lines {
        assert_eq!(line.chars().count(), REF_WIDTH, "every row is 80 columns");
    }
    assert!(a.contains("Frostbite Solitaire"));
    assert!(a.contains("SCORE"));
    assert!(a.contains("waste"), "the piles are labelled");
    assert!(a.contains('♠') && a.contains('♥'), "the suits are drawn");
    assert!(a.contains("┌───┐"), "cards are boxes");
    assert!(a.contains('>'), "the cursor is drawn");
    assert!(a.contains(LEGEND), "the keys are on the screen");
    assert!(a.contains("┌──"), "80x24 has a border");
}

#[test]
fn mono_mode_is_pure_ascii_and_colour_is_opt_in() {
    let g = Game::new(7);
    let mono = g.render(RenderOpts {
        mono: true,
        ..RenderOpts::default()
    });
    assert!(mono.is_ascii(), "mono draws with plain ASCII only");
    assert!(mono.contains("+---+"));
    assert!(!mono.contains('♠'));
    assert!(mono.contains("AS") || mono.contains("KS"));
    let colour = g.render(RenderOpts {
        color: true,
        ..RenderOpts::default()
    });
    assert!(colour.contains("\x1b[31m"), "the red suits are red");
    assert!(colour.contains("\x1b[2m"), "the decoration is dimmed");
    assert!(
        !g.render(RenderOpts::default()).contains('\x1b'),
        "no colour means no escapes"
    );
}

#[test]
fn a_smaller_window_drops_the_decoration_not_the_cards() {
    let g = Game::new(7);
    let full = g.render(RenderOpts::default());
    assert!(full.contains("┌──"), "80x24 has a border");
    assert!(full.contains("SCORE"), "and the score panel");

    // No border below 78 columns; the cards and the panel stay.
    let narrow = g.render(RenderOpts {
        width: 70,
        ..RenderOpts::default()
    });
    assert!(!narrow.starts_with('┌'), "the border went");
    assert!(narrow.contains("SCORE"), "the panel stayed");
    assert!(narrow.contains("┌───┐"), "the cards stayed");

    // Below 66 columns the panel goes.
    let panel = g.render(RenderOpts {
        width: 60,
        ..RenderOpts::default()
    });
    assert!(!panel.contains("SCORE"), "the score panel went");
    assert!(panel.contains("┌───┐"), "the cards stayed");

    // Below 22 rows the title goes, below 20 the key legend.
    let short = g.render(RenderOpts {
        height: 18,
        ..RenderOpts::default()
    });
    assert!(!short.contains("Frostbite Solitaire"), "the title went");
    assert!(!short.contains(LEGEND), "the legend went");
    assert!(short.contains("┌───┐"), "the cards stayed");
    assert_eq!(short.lines().count(), 18);

    // A tiny window still draws the piles, and never panics.
    let tiny = g.render(RenderOpts {
        width: 20,
        height: 8,
        ..RenderOpts::default()
    });
    assert_eq!(tiny.lines().count(), 8);
    assert!(tiny.contains("┌───┐"));
}

#[test]
fn a_deep_pile_compresses_its_cascade_instead_of_running_off_screen() {
    let mut g = Game::new(1);
    // One column with 19 cards: more than the grid can show at a 1-row cascade.
    g.tableau[0] = (0..19u8).map(|i| Card { id: i, up: i == 18 }).collect();
    let f = g.render(RenderOpts::default());
    assert_eq!(f.lines().count(), REF_HEIGHT, "nothing overflows the window");
    assert!(f.contains("+8"), "the cards that do not fit are counted with a +n marker");
    assert!(cascade_step(1, 17) >= 1);
    assert!(cascade_step(19, 17) >= 1, "the cascade never shrinks below one row");
    assert!(cascade_step(3, 17) <= 8, "and it stretches to fit");
}

// ---------------------------------------------------------------------------
// The scripted session: same seed, same script, same hash
// ---------------------------------------------------------------------------

/// Drive the library through a script the way `--replay` does: one line of keys
/// per tick, a blank line is no input, a `#` line is not a tick, `_` is the
/// space key.
fn drive(g: &mut Game, script: &str) {
    for line in script.lines() {
        if line.starts_with('#') {
            continue;
        }
        for ch in line
            .trim_end()
            .chars()
            .filter(|c| *c != ' ')
            .map(|c| if c == '_' { ' ' } else { c })
        {
            if g.key(ch) {
                return;
            }
        }
        g.tick();
    }
}

#[test]
fn a_scripted_session_is_deterministic() {
    let script = "\
# a short opening: draw, collect, walk to the waste, take it, drop it, undo
d
f
d
d
l
_
j
_
f
u
d
d
f
";
    let mut a = Game::new(21);
    let mut b = Game::new(21);
    let mut c = Game::new(22);
    drive(&mut a, script);
    drive(&mut b, script);
    drive(&mut c, script);
    assert_eq!(
        a.state_hash(),
        b.state_hash(),
        "same seed, same script, same state"
    );
    assert_ne!(a.state_hash(), c.state_hash(), "a different seed is a different game");
    assert_eq!(
        a.ticks,
        script.lines().filter(|l| !l.starts_with('#')).count() as u64,
        "one tick per script line"
    );
    assert_eq!(a.ms, a.ticks * TICK_MS, "the timer is the tick count");

    // The end state is a real, reachable position: 52 distinct cards, and no
    // face-down card sitting on top of a tableau column.
    let mut seen = [false; DECK_SIZE];
    for pile in all_piles(&a) {
        for card in pile {
            assert!(!seen[card.id as usize], "card {} is in two piles", card.id);
            seen[card.id as usize] = true;
        }
    }
    assert!(seen.iter().all(|s| *s), "every card is on the table");
    for pile in a.tableau.iter() {
        if let Some(top) = pile.last() {
            assert!(top.up, "a face-down top card is not a position");
        }
    }
}

#[test]
fn the_headless_demo_player_plays_a_whole_game_without_a_terminal() {
    let mut g = Game::new(21);
    for _ in 0..1500 {
        for k in g.autopilot_keys().chars() {
            g.key(k);
        }
        g.tick();
        if g.state == State::Win {
            break;
        }
    }
    assert!(
        g.foundation_count() > 0,
        "the demo player raised {} cards",
        g.foundation_count()
    );
    assert!(g.moves > 10, "and it played {} moves", g.moves);
    assert!(g.ticks <= 1500);
    // The demo player is a beginner: it recycles, and Klondike floors at zero.
    assert!(g.score >= 0);
    assert_eq!(g.stock.len() + g.waste.len() + g.foundation_count() + g.tableau.iter().map(|p| p.len()).sum::<usize>(), DECK_SIZE);
}

// ---------------------------------------------------------------------------
// The committed fixtures
// ---------------------------------------------------------------------------

fn fixture(name: &str) -> String {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures")
        .join(name);
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()))
}

#[test]
fn the_near_win_fixture_is_a_legal_position_one_move_from_winning() {
    let mut g = parse_state(&fixture("near-win.state")).expect("the fixture parses");
    assert_eq!(g.foundation_count(), 51, "one card short of a win");
    assert_eq!(g.state, State::Play);
    assert_eq!(g.waste.len(), 1);
    assert_eq!(rank(g.waste[0].id), RANK_KING);
    assert_eq!(suit(g.waste[0].id), D, "the King of Diamonds");
    assert!(g.stock.is_empty());
    assert!(g.tableau.iter().all(|p| p.is_empty()));
    // And it is exactly one legal move from winning.
    let sel = g.select(Area::Waste, 0).unwrap();
    assert_eq!(sel.count, 1);
    assert!(g.can_drop(&[g.waste[0]], Area::Foundation, D));
    assert_eq!(g.foundation_count(), 51);
}

#[test]
fn the_win_fixture_wins_the_near_win_deal() {
    let mut g = parse_state(&fixture("near-win.state")).expect("the fixture parses");
    let score = g.score;
    drive(&mut g, &fixture("win.replay"));
    assert_eq!(g.state, State::Win, "the replay wins");
    assert_eq!(g.foundation_count(), DECK_SIZE);
    assert_eq!(g.score, score + SCORE_TO_FOUNDATION);
    assert_eq!(g.moves, 121);
    assert!(g.waste.is_empty());
    // The winning position is the same every time it is replayed.
    let mut h = parse_state(&fixture("near-win.state")).unwrap();
    drive(&mut h, &fixture("win.replay"));
    assert_eq!(h.state_hash(), g.state_hash(), "the fixture is reproducible");
}

#[test]
fn the_state_file_round_trips() {
    let g = Game::new(7);
    let text = g.state_text();
    let back = parse_state(&text).expect("a dealt game parses back");
    assert_eq!(back.tableau, g.tableau);
    assert_eq!(back.stock, g.stock);
    assert_eq!(back.waste, g.waste);
    assert_eq!(back.foundations, g.foundations);
    assert_eq!(back.seed, g.seed);
}

#[test]
fn a_broken_state_file_is_refused_not_applied() {
    let ok = "foundations=||||\nstock=\nwaste=1\n";
    // A duplicate card.
    assert!(parse_state(&format!("tableau=0,0||||||\n{ok}")).is_none());
    // A missing card.
    assert!(parse_state(&format!("tableau=0||||||\n{ok}")).is_none());
    // A foundation out of order.
    assert!(parse_state(&format!(
        "tableau=1,2,3,4,5,6,7,8,9,10,11,12||||||\nfoundations=0||||\nstock=\nwaste=13"
    ))
    .is_none());
    // A face-down top card is not a position the rules can be in.
    assert!(parse_state(&format!("tableau=-0||||||\n{ok}")).is_none());
    // A card id off the deck.
    assert!(parse_state(&format!("tableau=52||||||\n{ok}")).is_none());
    // Not a key=value line at all.
    assert!(parse_state("nonsense").is_none());
    // An unknown key.
    assert!(parse_state("tableau=||||||\nfoundations=||||\nstock=\nwaste=1\nbogus=1").is_none());
}

// ---------------------------------------------------------------------------
// The plain-text state dump
// ---------------------------------------------------------------------------

#[test]
fn the_plain_state_dump_is_the_same_facts_as_the_frame() {
    let g = Game::new(7);
    let s = plain_state(&g);
    assert!(s.contains("state=play"));
    assert!(s.contains("score=0"));
    assert!(s.contains("stock=24"));
    assert!(s.contains("hash="));
    assert!(s.contains("foundation[Spades]="));
    assert!(s.contains("tableau[1]="));
    assert!(!s.contains('\x1b'), "no escapes in the plain dump");
    for (k, v) in g.result_kv() {
        assert!(s.contains(&format!("{k}={v}")), "the dump carries {k}");
    }
    assert_eq!(clock(0), "0:00");
    assert_eq!(clock(65_000), "1:05");
}
