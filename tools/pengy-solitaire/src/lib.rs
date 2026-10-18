//! pengy-solitaire — the pure rules core of Frostbite Solitaire.
//!
//! Klondike solitaire, draw three, unlimited redeals: the Windows 3.0 pack-in
//! cart, rebuilt for the PengyOS terminal.
//!
//! EVERY rule and the renderer live in this file, and this file is **pure**:
//! no files, no keyboard, no screen, no clock, no threads, no process, no
//! system entropy. The only randomness is the seeded xorshift64* below, so a
//! run is a function of `(seed, script)` and a session can be replayed, hashed
//! and asserted. The terminal front-end (`src/main.rs`) is the only thing that
//! touches the outside world.
//!
//! Pile convention, everywhere: a pile is a `Vec<Card>` whose **last** element
//! is the top card — the one that is playable.

use std::fmt::Write as _;

// ---------------------------------------------------------------------------
// Seeded randomness — xorshift64*, hand-written. No `rand`, no system entropy.
// ---------------------------------------------------------------------------

/// A seeded xorshift64* generator. Every run takes `--seed N` and is
/// reproducible bit for bit: the same seed deals the same 52 cards.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rng {
    state: u64,
}

impl Rng {
    /// Seed the generator. The seed is mixed (splitmix-style) so neighbouring
    /// seeds are not correlated, and a zero state is remapped (xorshift must
    /// never hold 0).
    pub fn new(seed: u64) -> Self {
        let mut s = seed.wrapping_add(0x9E37_79B9_7F4A_7C15);
        s ^= s >> 30;
        s = s.wrapping_mul(0xBF58_476D_1CE4_70B9);
        s ^= s >> 27;
        s = s.wrapping_mul(0x94D0_49BB_1331_11EB);
        s ^= s >> 31;
        Self {
            state: if s == 0 { 0x2545_FF49_6413_F8B9 } else { s },
        }
    }

    /// Raw generator state. Part of the game state, so hashes cover it.
    pub fn state(&self) -> u64 {
        self.state
    }

    /// Next 64-bit value.
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545_FF49_6413_F8B9)
    }

    /// Next value in `0..n`. `n` must be > 0.
    pub fn below(&mut self, n: u64) -> u64 {
        assert!(n > 0, "Rng::below(0)");
        self.next_u64() % n
    }
}

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

/// A card is one byte plus its face-up flag: `id = suit * 13 + rank`.
///
/// * suits: 0 Spades, 1 Hearts, 2 Clubs, 3 Diamonds — Hearts and Diamonds are
///   the red suits;
/// * ranks: 0 Ace, 1..10, 11 Jack, 12 Queen, 13... no: 11 J, 12 Q, and the
///   King is 12. `rank` is 0-based, so a foundation holding `n` cards is
///   waiting for rank `n`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Card {
    pub id: u8,
    /// Face up? Only face-up cards are playable, and a face-down tableau card
    /// flips the moment it becomes the top card.
    pub up: bool,
}

pub const DECK_SIZE: usize = 52;
pub const SUITS: usize = 4;
pub const RANKS: usize = 13;
pub const RANK_ACE: usize = 0;
pub const RANK_KING: usize = 12;

/// Rank of a card id (0 = Ace .. 12 = King).
pub const fn rank(id: u8) -> usize {
    (id % 13) as usize
}

/// Suit of a card id (0 Spades, 1 Hearts, 2 Clubs, 3 Diamonds).
pub const fn suit(id: u8) -> usize {
    (id / 13) as usize
}

/// Red suits are Hearts (1) and Diamonds (3).
pub const fn is_red(id: u8) -> bool {
    matches!(id / 13, 1 | 3)
}

pub const SUIT_CHARS: [char; SUITS] = ['♠', '♥', '♣', '♦'];
pub const SUIT_ASCII: [char; SUITS] = ['S', 'H', 'C', 'D'];
pub const SUIT_NAMES: [&str; SUITS] = ["Spades", "Hearts", "Clubs", "Diamonds"];
pub const RANK_CHARS: [char; RANKS] = [
    'A', '2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K',
];

/// The two suits of the opposite colour — the ones a safe auto-collect needs
/// to have out of the way.
pub const fn opposite_suits(id: u8) -> [usize; 2] {
    if is_red(id) {
        [0, 2]
    } else {
        [1, 3]
    }
}

/// The face of a card: `A♠`, or `AS` in mono mode.
pub fn face(id: u8, mono: bool) -> String {
    let r = RANK_CHARS[rank(id)];
    let s = if mono {
        SUIT_ASCII[suit(id)]
    } else {
        SUIT_CHARS[suit(id)]
    };
    format!("{r}{s}")
}

/// A whole deck, ids 0..51.
pub fn ordered_deck() -> [u8; DECK_SIZE] {
    let mut d = [0u8; DECK_SIZE];
    let mut i = 0;
    while i < DECK_SIZE {
        d[i] = i as u8;
        i += 1;
    }
    d
}

/// A shuffled deck: Fisher-Yates over the seeded generator. Deterministic for a
/// given seed, and a permutation of 0..51 for every seed.
pub fn shuffled_deck(rng: &mut Rng) -> [u8; DECK_SIZE] {
    let mut d = ordered_deck();
    let mut i = DECK_SIZE - 1;
    while i > 0 {
        let j = rng.below((i + 1) as u64) as usize;
        d.swap(i, j);
        i -= 1;
    }
    d
}

// ---------------------------------------------------------------------------
// Tuning constants — the difficulty and the score, all in one place
// ---------------------------------------------------------------------------

/// Fixed timestep of the front-end loop, in milliseconds.
pub const TICK_MS: u64 = 60;

/// Cards dealt from the stock to the waste per press. The Windows 3.0 game is
/// draw-three, and draw-three is the hard, thinking version of Klondike.
pub const DRAW_COUNT: usize = 3;

/// Undo depth. Enough to walk a whole auto-collect and a long mistake back.
pub const MAX_HISTORY: usize = 50;

/// Standard Klondike scoring. The score never goes below zero.
pub const SCORE_WASTE_TO_TABLEAU: i64 = 5;
pub const SCORE_TO_FOUNDATION: i64 = 10;
pub const SCORE_FLIP: i64 = 5;
pub const SCORE_FOUNDATION_TO_TABLEAU: i64 = -15;
pub const SCORE_RECYCLE: i64 = 100;

/// The window the layout is designed for, and the degrade thresholds. Below
/// them the decoration goes away in a fixed order: border, score panel, title,
/// key legend. The cards never go away.
pub const REF_WIDTH: usize = 80;
pub const REF_HEIGHT: usize = 24;
pub const MIN_WIDTH: usize = 50;
pub const BORDER_MIN_WIDTH: usize = 78;
pub const BORDER_MIN_HEIGHT: usize = 24;
pub const PANEL_MIN_WIDTH: usize = 66;
pub const TITLE_MIN_HEIGHT: usize = 22;
pub const LEGEND_MIN_HEIGHT: usize = 20;

/// The key legend: one line, printed at the bottom of the frame and quoted by
/// `--help`, so the two can never drift apart.
pub const LEGEND: &str =
    "hjkl/arrows move  space take/drop  d draw  f collect  u undo  n new  q quit";

pub const GAME_NAME: &str = "Frostbite Solitaire";
pub const GAME_SUBTITLE: &str = "Klondike, draw three, unlimited redeals";

// ---------------------------------------------------------------------------
// Piles, cursor, selection, state
// ---------------------------------------------------------------------------

/// The piles on the table. `Foundation(i)` is the foundation of suit `i`,
/// `Tableau(i)` is tableau column `i` (0-based, left to right).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Area {
    Stock,
    Waste,
    Foundation,
    Tableau,
}

impl Area {
    pub fn name(self) -> &'static str {
        match self {
            Area::Stock => "stock",
            Area::Waste => "waste",
            Area::Foundation => "foundation",
            Area::Tableau => "tableau",
        }
    }
}

/// Where the cursor is. The top row holds the stock, the waste and the four
/// foundations; the grid below holds the seven tableau columns.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Cursor {
    pub area: Area,
    pub index: usize,
}

/// A picked-up run of cards: where it came from and how many cards it took,
/// counted from the top of that pile.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Selection {
    pub area: Area,
    pub index: usize,
    pub count: usize,
}

/// Play, or won. Klondike has no lose state: a game you cannot win is a game
/// you press `n` on.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum State {
    Play,
    Win,
}

/// What a keypress queued for the next tick. Cursor moves apply immediately;
/// everything that changes the board happens on a tick.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Action {
    Draw,
    Collect,
    Undo,
    NewDeal,
    Act,
}

/// One undoable state. `undo` puts it back verbatim, so the state hash after an
/// undo equals the hash before the move.
#[derive(Clone, Debug)]
pub struct Snapshot {
    stock: Vec<Card>,
    waste: Vec<Card>,
    foundations: [Vec<Card>; SUITS],
    tableau: [Vec<Card>; 7],
    score: i64,
    moves: u64,
    redeals: u64,
    flips: u64,
    dead: bool,
    state: State,
    message: String,
    selection: Option<Selection>,
    cursor: Cursor,
}

// ---------------------------------------------------------------------------
// The game
// ---------------------------------------------------------------------------

/// The whole game state. Every rule is a method on this type.
#[derive(Clone, Debug)]
pub struct Game {
    pub seed: u64,
    pub rng: Rng,
    pub stock: Vec<Card>,
    pub waste: Vec<Card>,
    pub foundations: [Vec<Card>; SUITS],
    pub tableau: [Vec<Card>; 7],
    pub cursor: Cursor,
    pub selection: Option<Selection>,
    pub score: i64,
    pub moves: u64,
    pub ticks: u64,
    pub ms: u64,
    pub redeals: u64,
    /// Tableau cards uncovered by the rules (a flip is progress, and the demo
    /// player uses it to notice that a game is dead).
    pub flips: u64,
    /// True when the demo player has proved this deal is finished: a full pass
    /// over the stock uncovered and raised nothing new.
    pub dead: bool,
    flips_at_recycle: u64,
    foundations_at_recycle: usize,
    /// The last move applied, so the demo player never answers a move with its
    /// own reverse.
    last_move: Option<(Area, usize, Area, usize)>,
    pub state: State,
    pub message: String,
    /// The undo stack, oldest first, capped at [`MAX_HISTORY`].
    pub history: Vec<Snapshot>,
    /// The key queued for the next tick.
    pub pending: Option<Action>,
    /// The autopilot's queued keys (the headless demo player).
    plan: Vec<char>,
    plan_cycle: usize,
    /// The persisted record, copied in by the front-end. Pure: the library
    /// never reads a file, the caller does.
    pub best_score: u64,
    pub best_moves: u64,
    pub best_ms: u64,
    pub best_games: u64,
    pub best_wins: u64,
}

impl Default for Game {
    fn default() -> Self {
        Game::new(1)
    }
}

impl Game {
    /// Deal a fresh game from `seed`.
    pub fn new(seed: u64) -> Self {
        let mut g = Game {
            seed,
            rng: Rng::new(seed),
            stock: Vec::new(),
            waste: Vec::new(),
            foundations: [Vec::new(), Vec::new(), Vec::new(), Vec::new()],
            tableau: [
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
            ],
            cursor: Cursor {
                area: Area::Stock,
                index: 0,
            },
            selection: None,
            score: 0,
            moves: 0,
            ticks: 0,
            ms: 0,
            redeals: 0,
            flips: 0,
            dead: false,
            flips_at_recycle: 0,
            foundations_at_recycle: 0,
            last_move: None,
            state: State::Play,
            message: String::from("press d to draw, space to take a card"),
            history: Vec::new(),
            pending: None,
            plan: Vec::new(),
            plan_cycle: 0,
            best_score: 0,
            best_moves: 0,
            best_ms: 0,
            best_games: 0,
            best_wins: 0,
        };
        g.deal();
        g
    }

    /// Deal the standard Klondike layout: seven tableau columns of 1..7 cards,
    /// the top card of each face up and the rest face down, the remaining 24
    /// cards as the face-down stock.
    pub fn deal(&mut self) {
        let deck = shuffled_deck(&mut self.rng);
        self.stock.clear();
        self.waste.clear();
        for f in self.foundations.iter_mut() {
            f.clear();
        }
        for t in self.tableau.iter_mut() {
            t.clear();
        }
        let mut k = 0usize;
        for col in 0..7 {
            for row in 0..=col {
                let card = Card {
                    id: deck[k],
                    up: row == col,
                };
                k += 1;
                self.tableau[col].push(card);
            }
        }
        while k < DECK_SIZE {
            self.stock.push(Card {
                id: deck[k],
                up: false,
            });
            k += 1;
        }
        self.cursor = Cursor {
            area: Area::Stock,
            index: 0,
        };
        self.selection = None;
        self.score = 0;
        self.moves = 0;
        self.ticks = 0;
        self.ms = 0;
        self.redeals = 0;
        self.flips = 0;
        self.dead = false;
        self.flips_at_recycle = 0;
        self.foundations_at_recycle = 0;
        self.last_move = None;
        self.state = State::Play;
        self.history.clear();
        self.pending = None;
        self.plan.clear();
        self.plan_cycle = 0;
        self.message = String::from("new deal");
    }

    /// `n`: a fresh deal. The shuffle stream moves on, so `n` is a new game and
    /// never a repeat of the one you are on.
    pub fn new_deal(&mut self) {
        self.deal();
    }

    // -- pile helpers -------------------------------------------------------

    /// The cards of a pile, top last.
    pub fn pile(&self, area: Area, index: usize) -> &[Card] {
        match area {
            Area::Stock => &self.stock,
            Area::Waste => &self.waste,
            Area::Foundation => &self.foundations[index],
            Area::Tableau => &self.tableau[index],
        }
    }

    /// The top card of a pile, when it has one and it is face up.
    pub fn top(&self, area: Area, index: usize) -> Option<Card> {
        self.pile(area, index).last().copied().filter(|c| c.up)
    }

    /// How many cards are on the foundations. 52 is a win.
    pub fn foundation_count(&self) -> usize {
        self.foundations.iter().map(|f| f.len()).sum()
    }

    // -- scoring ------------------------------------------------------------

    /// Add points, floored at 0: Klondike scoring never goes negative.
    pub fn add_score(&mut self, delta: i64) {
        self.score += delta;
        if self.score < 0 {
            self.score = 0;
        }
    }

    /// The score of a move, by where the cards came from and where they land.
    pub fn score_for(from: Area, to: Area) -> i64 {
        match (from, to) {
            (Area::Waste, Area::Tableau) => SCORE_WASTE_TO_TABLEAU,
            (Area::Waste, Area::Foundation) => SCORE_TO_FOUNDATION,
            (Area::Tableau, Area::Foundation) => SCORE_TO_FOUNDATION,
            (Area::Foundation, Area::Tableau) => SCORE_FOUNDATION_TO_TABLEAU,
            _ => 0,
        }
    }

    // -- legality -----------------------------------------------------------

    /// A run of tableau cards that may be picked up as a unit: every card face
    /// up, descending by exactly one rank, colours strictly alternating.
    pub fn legal_run(cards: &[Card]) -> bool {
        if cards.is_empty() {
            return false;
        }
        for (i, c) in cards.iter().enumerate() {
            if !c.up {
                return false;
            }
            if i > 0 {
                let p = cards[i - 1];
                if rank(c.id) + 1 != rank(p.id) {
                    return false;
                }
                if is_red(c.id) == is_red(p.id) {
                    return false;
                }
            }
        }
        true
    }

    /// May this run be dropped on this pile?
    ///
    /// * foundation: exactly one card, of that foundation's suit, and the next
    ///   rank up (an Ace onto an empty foundation);
    /// * empty tableau column: a King, and only a King;
    /// * non-empty tableau column: the top card is one rank higher and of the
    ///   opposite colour.
    ///
    /// The run being dropped must itself be a legal run. The stock and the
    /// waste are never destinations.
    pub fn can_drop(&self, cards: &[Card], area: Area, index: usize) -> bool {
        if cards.is_empty() || self.state != State::Play {
            return false;
        }
        if !Self::legal_run(cards) {
            return false;
        }
        let card = cards[0];
        match area {
            Area::Foundation => {
                cards.len() == 1
                    && suit(card.id) == index
                    && rank(card.id) == self.foundations[index].len()
            }
            Area::Tableau => match self.tableau[index].last() {
                None => rank(card.id) == RANK_KING,
                Some(top) => {
                    top.up
                        && rank(top.id) == rank(card.id) + 1
                        && is_red(top.id) != is_red(card.id)
                }
            },
            Area::Stock | Area::Waste => false,
        }
    }

    /// The longest movable run sitting on top of a tableau column: the longest
    /// suffix that is a legal run. A column whose face-up cards are not a
    /// sequence (a card came back off a foundation, say) yields just its top
    /// card, which is always movable on its own.
    pub fn movable_run(&self, index: usize) -> Vec<Card> {
        let pile = &self.tableau[index];
        if pile.is_empty() {
            return Vec::new();
        }
        let mut start = pile.len() - 1;
        while start > 0 {
            let above = pile[start];
            let below = pile[start - 1];
            let linked = below.up
                && rank(below.id) == rank(above.id) + 1
                && is_red(below.id) != is_red(above.id);
            if !linked {
                break;
            }
            start -= 1;
        }
        pile[start..].to_vec()
    }

    /// The run the cursor is pointing at: the top card of the waste, the top
    /// card of a foundation, the movable run of a tableau column. The stock is
    /// not a source — you draw from it, you do not pick it up.
    pub fn selectable(&self, area: Area, index: usize) -> Vec<Card> {
        match area {
            Area::Tableau => self.movable_run(index),
            Area::Waste | Area::Foundation => self
                .top(area, index)
                .map(|c| vec![c])
                .unwrap_or_default(),
            Area::Stock => Vec::new(),
        }
    }

    /// Is this card safe to send to its foundation right now?
    ///
    /// The classic rule, so auto-collect can never strand a card you needed on
    /// the tableau: an Ace is always safe; a card of rank `r` is safe only when
    /// both foundations of the *opposite* colour already hold rank `r-1`, so
    /// the card that could have received this one is out of the way. A card
    /// taken off the tableau additionally needs the card beneath it to be of
    /// the opposite colour and one rank higher (or to be nothing), so
    /// auto-collect never breaks a build.
    pub fn safe_to_foundation(&self, card: Card, from_tableau: bool, col: usize) -> bool {
        let r = rank(card.id);
        if self.foundations[suit(card.id)].len() != r {
            return false; // not the next rank up: not a legal foundation move
        }
        if r == RANK_ACE {
            return true;
        }
        for s in opposite_suits(card.id) {
            if self.foundations[s].len() < r {
                return false;
            }
        }
        if from_tableau {
            let pile = &self.tableau[col];
            if pile.len() >= 2 {
                let below = pile[pile.len() - 2];
                if !below.up
                    || rank(below.id) != r + 1
                    || is_red(below.id) == is_red(card.id)
                {
                    return false;
                }
            }
        }
        true
    }

    // -- moves --------------------------------------------------------------

    /// Pick up the run at a pile. Returns the selection, `None` when there is
    /// nothing to take.
    pub fn select(&mut self, area: Area, index: usize) -> Option<Selection> {
        let cards = self.selectable(area, index);
        if cards.is_empty() {
            self.message = match area {
                Area::Stock => String::from("the stock is not a source: press d to draw"),
                _ => format!("{} is empty", area.name()),
            };
            return None;
        }
        let sel = Selection {
            area,
            index,
            count: cards.len(),
        };
        self.selection = Some(sel);
        self.message = format!("took {} ({} card(s))", names(&cards, false), cards.len());
        Some(sel)
    }

    /// Drop the selection on a pile. True when a move happened. An illegal drop
    /// is refused and the cards stay in hand; space on the source puts them
    /// back down.
    pub fn drop(&mut self, sel: Selection, area: Area, index: usize) -> bool {
        if self.state != State::Play {
            return false;
        }
        if sel.area == area && sel.index == index {
            self.selection = None;
            self.message = String::from("put them back");
            return false;
        }
        let cards = self.cards_of(sel);
        if cards.is_empty() {
            self.selection = None;
            self.message = String::from("nothing selected");
            return false;
        }
        if !self.can_drop(&cards, area, index) {
            self.message = String::from("that is not a legal move");
            return false;
        }
        self.apply(sel, area, index, &cards);
        true
    }

    /// The cards a selection refers to, top last.
    pub fn cards_of(&self, sel: Selection) -> Vec<Card> {
        let pile = self.pile(sel.area, sel.index);
        if sel.count == 0 || sel.count > pile.len() {
            return Vec::new();
        }
        pile[pile.len() - sel.count..].to_vec()
    }

    /// Apply a legal move: take the cards off the source, put them face up on
    /// the destination, score it, flip what the source uncovered. This is the
    /// undoable step, so it pushes the state it is leaving behind.
    fn apply(&mut self, sel: Selection, area: Area, index: usize, cards: &[Card]) {
        self.push_history();
        self.apply_no_history(sel, area, index, cards);
    }

    /// The move itself, with no undo level: [`Game::auto_collect`] batches many
    /// of them under one.
    fn apply_no_history(&mut self, sel: Selection, area: Area, index: usize, cards: &[Card]) {
        match sel.area {
            Area::Stock => {}
            Area::Waste => {
                let n = self.waste.len() - sel.count;
                self.waste.truncate(n);
            }
            Area::Foundation => {
                let n = self.foundations[sel.index].len() - sel.count;
                self.foundations[sel.index].truncate(n);
            }
            Area::Tableau => {
                let n = self.tableau[sel.index].len() - sel.count;
                self.tableau[sel.index].truncate(n);
            }
        }
        match area {
            Area::Foundation => {
                for c in cards {
                    self.foundations[index].push(Card {
                        id: c.id,
                        up: true,
                    });
                }
            }
            Area::Tableau => {
                for c in cards {
                    self.tableau[index].push(Card {
                        id: c.id,
                        up: true,
                    });
                }
            }
            _ => {}
        }
        self.add_score(Self::score_for(sel.area, area));
        self.moves += 1;
        self.last_move = Some((sel.area, sel.index, area, index));
        self.selection = None;
        self.flip_top(sel.area, sel.index);
        self.check_win();
        self.message = format!(
            "{}: {} -> {} ({} cards)",
            if self.state == State::Win {
                "won"
            } else {
                "moved"
            },
            sel.area.name(),
            area.name(),
            cards.len()
        );
    }

    /// Auto-flip: a face-down tableau card that has become the top card turns
    /// over for [`SCORE_FLIP`] points. It is not counted as a move — it is what
    /// the rules do for you.
    fn flip_top(&mut self, area: Area, index: usize) {
        if area != Area::Tableau {
            return;
        }
        let n = self.tableau[index].len();
        if n == 0 {
            return;
        }
        if !self.tableau[index][n - 1].up {
            self.tableau[index][n - 1].up = true;
            self.flips += 1;
            self.add_score(SCORE_FLIP);
        }
    }

    /// `d`: draw [`DRAW_COUNT`] cards from the stock onto the waste, preserving
    /// their order. An empty stock recycles the whole waste back into the stock
    /// (reversed, face down) for the [`SCORE_RECYCLE`] penalty — unlimited
    /// passes, exactly like the Windows game. Recycling needs a non-empty
    /// waste: with both piles empty there is nothing to do.
    pub fn draw(&mut self) {
        if self.state != State::Play {
            self.message = String::from("the game is over: press n for a new deal");
            return;
        }
        if self.stock.is_empty() {
            if self.waste.is_empty() {
                self.message = String::from("stock and waste are both empty");
                return;
            }
            self.push_history();
            self.stock = self
                .waste
                .iter()
                .rev()
                .map(|c| Card {
                    id: c.id,
                    up: false,
                })
                .collect();
            self.waste.clear();
            self.redeals += 1;
            self.moves += 1;
            self.selection = None;
            self.add_score(-SCORE_RECYCLE);
            // A pass over the whole waste that uncovered nothing and raised
            // nothing means this deal is finished: there is no move left that
            // the rules have not already offered.
            let progress = self.flips > self.flips_at_recycle
                || self.foundation_count() > self.foundations_at_recycle;
            self.dead = self.redeals > 1 && !progress;
            self.flips_at_recycle = self.flips;
            self.foundations_at_recycle = self.foundation_count();
            self.message = format!(
                "recycled {} cards (-{}), pass {}",
                self.stock.len(),
                SCORE_RECYCLE,
                self.redeals + 1
            );
            return;
        }
        self.push_history();
        let n = DRAW_COUNT.min(self.stock.len());
        let taken: Vec<Card> = self.stock.split_off(self.stock.len() - n);
        for c in taken {
            self.waste.push(Card {
                id: c.id,
                up: true,
            });
        }
        self.moves += 1;
        self.message = format!(
            "drew {}, {} left in the stock",
            self.waste.len(),
            self.stock.len()
        );
    }

    /// `f`: collect every card that is safe to send to a foundation, applied
    /// repeatedly until nothing more is safe. Idempotent by construction: the
    /// second call finds no safe card, so it changes nothing.
    pub fn auto_collect(&mut self) -> usize {
        if self.state != State::Play {
            return 0;
        }
        // One undo level for the whole batch: `u` after an `f` puts every card
        // back, not just the last one.
        self.push_history();
        let mut n = 0;
        while let Some((sel, suit)) = self.find_safe() {
            let cards = self.cards_of(sel);
            self.apply_no_history(sel, Area::Foundation, suit, &cards);
            n += 1;
            if n > DECK_SIZE {
                break; // a guard against a logic bug, not a rule
            }
        }
        if n == 0 {
            self.pop_history(); // a pass that did nothing is not an undo level
        }
        self.message = if n == 0 {
            String::from("nothing is safe to collect")
        } else {
            format!("collected {} card{} to the foundations", n, if n == 1 { "" } else { "s" })
        };
        n
    }

    /// The first safe card to a foundation: the waste top first, then the
    /// tableau tops, left to right.
    fn find_safe(&self) -> Option<(Selection, usize)> {
        if let Some(top) = self.top(Area::Waste, 0) {
            if self.safe_to_foundation(top, false, 0) {
                return Some((
                    Selection {
                        area: Area::Waste,
                        index: 0,
                        count: 1,
                    },
                    suit(top.id),
                ));
            }
        }
        for col in 0..7 {
            if let Some(top) = self.top(Area::Tableau, col) {
                if self.safe_to_foundation(top, true, col) {
                    return Some((
                        Selection {
                            area: Area::Tableau,
                            index: col,
                            count: 1,
                        },
                        suit(top.id),
                    ));
                }
            }
        }
        None
    }

    // -- undo ---------------------------------------------------------------

    /// Push the current state onto the undo stack, dropping the oldest state
    /// past [`MAX_HISTORY`].
    fn push_history(&mut self) {
        let snap = Snapshot {
            stock: self.stock.clone(),
            waste: self.waste.clone(),
            foundations: self.foundations.clone(),
            tableau: self.tableau.clone(),
            score: self.score,
            moves: self.moves,
            redeals: self.redeals,
            flips: self.flips,
            dead: self.dead,
            state: self.state,
            message: self.message.clone(),
            selection: self.selection,
            cursor: self.cursor,
        };
        self.history.push(snap);
        if self.history.len() > MAX_HISTORY {
            self.history.remove(0);
        }
    }

    /// Drop the top of the undo stack without restoring it (a keypress that
    /// turned out to change nothing must not leave a phantom undo level).
    fn pop_history(&mut self) {
        self.history.pop();
    }

    /// `u`: restore the exact previous state — piles, score, move count,
    /// cursor, selection and message.
    pub fn undo(&mut self) -> bool {
        let Some(s) = self.history.pop() else {
            self.message = String::from("nothing to undo");
            return false;
        };
        self.stock = s.stock;
        self.waste = s.waste;
        self.foundations = s.foundations;
        self.tableau = s.tableau;
        self.score = s.score;
        self.moves = s.moves;
        self.redeals = s.redeals;
        self.flips = s.flips;
        self.dead = s.dead;
        self.state = s.state;
        self.selection = s.selection;
        self.cursor = s.cursor;
        self.pending = None;
        self.message = format!("undid one move (was: {})", s.message);
        true
    }

    // -- win ----------------------------------------------------------------

    /// All 52 cards on the foundations is the only way to win.
    pub fn check_win(&mut self) {
        if self.foundation_count() == DECK_SIZE {
            self.state = State::Win;
        }
    }

    // -- cursor -------------------------------------------------------------

    /// The top row as slots: 0 stock, 1 waste, 2..5 foundations. The tableau
    /// row is its own slot space, 0..6.
    fn slot(area: Area, index: usize) -> usize {
        match area {
            Area::Stock => 0,
            Area::Waste => 1,
            Area::Foundation => 2 + index,
            Area::Tableau => index,
        }
    }

    fn slot_area(slot: usize) -> (Area, usize) {
        match slot {
            0 => (Area::Stock, 0),
            1 => (Area::Waste, 0),
            s => (Area::Foundation, (s - 2).min(3)),
        }
    }

    fn is_top_row(area: Area) -> bool {
        !matches!(area, Area::Tableau)
    }

    /// `h` / `l`: move along the row the cursor is on. Clamped, never wrapping:
    /// a wrap would make a long press of one key overshoot.
    pub fn move_cursor_x(&mut self, dir: i32) {
        let top = Self::is_top_row(self.cursor.area);
        let last = if top { 5 } else { 6 };
        let slot = (Self::slot(self.cursor.area, self.cursor.index) as i32 + dir).clamp(0, last);
        let slot = slot as usize;
        self.cursor = if top {
            let (area, index) = Self::slot_area(slot);
            Cursor { area, index }
        } else {
            Cursor {
                area: Area::Tableau,
                index: slot,
            }
        };
    }

    /// `k`: up to the top row, `j`: down to the tableau column nearest the
    /// current slot.
    pub fn move_cursor_y(&mut self, dir: i32) {
        let top = Self::is_top_row(self.cursor.area);
        match (top, dir) {
            (true, 1) => {
                let slot = Self::slot(self.cursor.area, self.cursor.index).min(6);
                self.cursor = Cursor {
                    area: Area::Tableau,
                    index: slot,
                };
            }
            (false, -1) => {
                let (area, index) = Self::slot_area(self.cursor.index.min(5));
                self.cursor = Cursor { area, index };
            }
            _ => {}
        }
    }

    // -- keys and ticks -----------------------------------------------------

    /// A keypress. Cursor moves apply at once; everything that changes the
    /// board is queued for the next [`Game::tick`], so the game advances on a
    /// fixed timestep. Returns true when the game asked to quit.
    pub fn key(&mut self, c: char) -> bool {
        match c {
            'q' => return true,
            'h' => self.move_cursor_x(-1),
            'l' => self.move_cursor_x(1),
            'k' => self.move_cursor_y(-1),
            'j' => self.move_cursor_y(1),
            'd' => self.pending = Some(Action::Draw),
            'f' => self.pending = Some(Action::Collect),
            'u' => self.pending = Some(Action::Undo),
            'n' => self.pending = Some(Action::NewDeal),
            ' ' | '\n' | '\r' => self.pending = Some(Action::Act),
            _ => {}
        }
        false
    }

    /// One tick of the fixed timestep: the clock moves, the queued key acts.
    pub fn tick(&mut self) {
        self.ticks += 1;
        self.ms += TICK_MS;
        match self.pending.take() {
            Some(Action::Draw) => self.draw(),
            Some(Action::Collect) => {
                self.auto_collect();
            }
            Some(Action::Undo) => {
                self.undo();
            }
            Some(Action::NewDeal) => self.new_deal(),
            Some(Action::Act) => {
                let cur = self.cursor;
                match self.selection {
                    Some(sel) => {
                        self.drop(sel, cur.area, cur.index);
                    }
                    None => {
                        self.select(cur.area, cur.index);
                    }
                }
            }
            None => {}
        }
    }

    // -- fingerprints -------------------------------------------------------

    /// The whole state as one canonical string. Two runs with the same seed and
    /// the same inputs produce the same string, byte for byte. The elapsed
    /// timer is deliberately *not* in here: it is the wall clock, not the
    /// position, and `undo` rewinds a move without rewinding the clock.
    pub fn canonical(&self) -> String {
        let mut s = String::new();
        let _ = write!(
            s,
            "seed={seed}|rng={rng:016x}",
            seed = self.seed,
            rng = self.rng.state()
        );
        for (name, pile) in [
            ("stock", &self.stock),
            ("waste", &self.waste),
            ("f0", &self.foundations[0]),
            ("f1", &self.foundations[1]),
            ("f2", &self.foundations[2]),
            ("f3", &self.foundations[3]),
            ("t0", &self.tableau[0]),
            ("t1", &self.tableau[1]),
            ("t2", &self.tableau[2]),
            ("t3", &self.tableau[3]),
            ("t4", &self.tableau[4]),
            ("t5", &self.tableau[5]),
            ("t6", &self.tableau[6]),
        ] {
            let _ = write!(s, "|{name}={}", pile_text(pile));
        }
        let _ = write!(
            s,
            "|score={score}|moves={moves}|redeals={redeals}|flips={flips}|dead={dead}|state={state:?}|cursor={area}:{index}|sel={sel:?}",
            score = self.score,
            moves = self.moves,
            redeals = self.redeals,
            flips = self.flips,
            dead = self.dead,
            state = self.state,
            area = self.cursor.area.name(),
            index = self.cursor.index,
            sel = self.selection,
        );
        s
    }

    /// FNV-1a 64 over [`Game::canonical`] — the cheap "same run?" fingerprint.
    pub fn state_hash(&self) -> u64 {
        let mut h: u64 = 0xcbf2_9ce4_8422_2325;
        for b in self.canonical().as_bytes() {
            h ^= *b as u64;
            h = h.wrapping_mul(0x1000_0000_01b3);
        }
        h
    }

    /// The game as `key=value` pairs — what `--replay` prints on exit 0, and
    /// what the acceptance harness greps.
    pub fn result_kv(&self) -> Vec<(String, String)> {
        let mut kv = vec![
            ("state".into(), format!("{:?}", self.state).to_lowercase()),
            ("seed".into(), self.seed.to_string()),
            ("ticks".into(), self.ticks.to_string()),
            ("ms".into(), self.ms.to_string()),
            ("time".into(), clock(self.ms)),
            ("score".into(), self.score.to_string()),
            ("moves".into(), self.moves.to_string()),
            ("redeals".into(), self.redeals.to_string()),
            ("flips".into(), self.flips.to_string()),
            ("foundations".into(), self.foundation_count().to_string()),
            ("stock".into(), self.stock.len().to_string()),
            ("waste".into(), self.waste.len().to_string()),
            ("best_score".into(), self.best_score.to_string()),
            ("best_games".into(), self.best_games.to_string()),
            ("best_wins".into(), self.best_wins.to_string()),
            ("hash".into(), format!("{:016x}", self.state_hash())),
        ];
        kv.push(("message".into(), self.message.clone()));
        kv
    }

    // -- the autopilot (the headless demo player) ---------------------------

    /// The demo player's next keypresses, one tick at a time: collect what is
    /// safe, play the waste onto the tableau, uncover a tableau card, and draw
    /// when there is nothing better. A greedy player, not a champion — its job
    /// is to prove the game runs with no pty, and to make fixtures.
    pub fn autopilot_keys(&mut self) -> String {
        if self.state != State::Play || self.dead {
            return String::new();
        }
        if !self.plan.is_empty() {
            return self.plan.remove(0).to_string();
        }
        self.plan_cycle += 1;
        let keys = match self.plan_cycle % 4 {
            1 => String::from("f"),
            2 => self
                .plan_risky_raise()
                .or_else(|| self.plan_tableau_build())
                .unwrap_or_else(|| String::from("f")),
            3 => self
                .plan_tableau_build()
                .or_else(|| self.plan_waste_play())
                .unwrap_or_else(|| String::from("f")),
            _ => String::from("d"),
        };
        let mut it = keys.chars();
        let first = it.next().unwrap_or('f');
        self.plan = it.collect();
        first.to_string()
    }

    /// Play the waste's top card onto a tableau column that accepts it. Moving
    /// it onto an empty column is a wash, so only built columns count.
    fn plan_waste_play(&self) -> Option<String> {
        let top = self.top(Area::Waste, 0)?;
        let cards = vec![top];
        for dst in 0..7 {
            if self.tableau[dst].is_empty() {
                continue;
            }
            if self.can_drop(&cards, Area::Tableau, dst) {
                return Some(self.plan_move(Area::Waste, 0, Area::Tableau, dst));
            }
        }
        None
    }

    /// Send an Ace, a Two or a Three up to its foundation when the strict
    /// safety rule refused it. This is the classic opening heuristic — aces and
    /// twos out early — and it is a *risk*: it can strand a card the tableau
    /// needed. `f` never takes that risk; only the demo player does.
    fn plan_risky_raise(&self) -> Option<String> {
        if let Some(c) = self.top(Area::Waste, 0) {
            if self.risky_raise_ok(c, false, 0) {
                return Some(self.plan_move(Area::Waste, 0, Area::Foundation, suit(c.id)));
            }
        }
        for col in 0..7 {
            if let Some(c) = self.top(Area::Tableau, col) {
                // A single top card only: a run cannot go to a foundation.
                if self.movable_run(col).len() == 1 && self.risky_raise_ok(c, true, col) {
                    return Some(self.plan_move(Area::Tableau, col, Area::Foundation, suit(c.id)));
                }
            }
        }
        None
    }

    /// The demo player's relaxed safety test: rank 0..2, the next rank up on
    /// its own foundation, and the *same-colour* foundation already holding
    /// rank `r-1` (so the card that could have received this one is one rank
    /// ahead, not both opposite colours as the strict rule demands).
    fn risky_raise_ok(&self, card: Card, from_tableau: bool, col: usize) -> bool {
        let r = rank(card.id);
        if r > 2 || self.foundations[suit(card.id)].len() != r {
            return false;
        }
        if r > 0 && self.foundations[suit(card.id) ^ 2].len() < r {
            return false;
        }
        if from_tableau {
            let pile = &self.tableau[col];
            if pile.len() >= 2 {
                let below = pile[pile.len() - 2];
                if !below.up || rank(below.id) != r + 1 || is_red(below.id) == is_red(card.id) {
                    return false;
                }
            }
        }
        true
    }

    /// Build a tableau run onto another column. The point of the move is to
    /// uncover a face-down card, so a move that does that is taken at once;
    /// otherwise take a build that leaves something behind (moving a whole
    /// face-up pile from one column to another is a wash, and a loop waiting to
    /// happen).
    fn plan_tableau_build(&self) -> Option<String> {
        let mut fallback: Option<(usize, usize)> = None;
        for src in 0..7 {
            let pile = &self.tableau[src];
            let run = self.movable_run(src);
            if run.is_empty() || run.len() == pile.len() {
                continue; // nothing left behind: a wash
            }
            let uncovers = !pile[pile.len() - run.len() - 1].up;
            for dst in 0..7 {
                if dst == src || !self.can_drop(&run, Area::Tableau, dst) {
                    continue;
                }
                if uncovers {
                    return Some(self.plan_move(Area::Tableau, src, Area::Tableau, dst));
                }
                if fallback.is_none()
                    && !self.tableau[dst].is_empty()
                    && self.last_move != Some((Area::Tableau, dst, Area::Tableau, src))
                {
                    fallback = Some((src, dst));
                }
            }
        }
        fallback.map(|(src, dst)| self.plan_move(Area::Tableau, src, Area::Tableau, dst))
    }

    /// The keys that move a run from one pile to another: walk to the source,
    /// take, walk to the destination, drop. One key per tick.
    fn plan_move(&self, from: Area, from_index: usize, to: Area, to_index: usize) -> String {
        let mut keys = self.nav_keys(from, from_index);
        keys.push(' ');
        // The second leg starts from the source, not from where the cursor was.
        keys.push_str(&self.nav_keys_from(
            Cursor {
                area: from,
                index: from_index,
            },
            to,
            to_index,
        ));
        keys.push(' ');
        keys
    }

    /// The keys that walk the cursor from where it is to a pile.
    fn nav_keys(&self, area: Area, index: usize) -> String {
        self.nav_keys_from(self.cursor, area, index)
    }

    /// The keys that walk a cursor from `from` to a pile: one vertical move to
    /// change rows, then the horizontal moves to the target slot. The cursor is
    /// clamped, never wrapping, so this is always the shortest path.
    fn nav_keys_from(&self, from: Cursor, area: Area, index: usize) -> String {
        let mut keys = String::new();
        let mut slot = Self::slot(from.area, from.index);
        if Self::is_top_row(from.area) && !Self::is_top_row(area) {
            keys.push('j');
            slot = slot.min(6);
        } else if !Self::is_top_row(from.area) && Self::is_top_row(area) {
            keys.push('k');
            slot = from.index.min(5);
        }
        let target = Self::slot(area, index);
        while slot < target {
            keys.push('l');
            slot += 1;
        }
        while slot > target {
            keys.push('h');
            slot -= 1;
        }
        keys
    }

    // -- the state file -----------------------------------------------------

    /// The whole game as a text file: the fixture format, the save format, and
    /// what `--state` loads. `-N` means card N is face down.
    pub fn state_text(&self) -> String {
        let mut s = String::new();
        s.push_str("# pengy-solitaire state (Frostbite Solitaire, Klondike draw 3)\n");
        s.push_str("# Load with: pengy-solitaire --state <this file> --replay <script> --seed N\n");
        s.push_str("# Cards are ids 0..51 (suit*13+rank); a leading '-' is face down.\n");
        s.push_str("# Piles are bottom..top, separated by '|'; the last card is the top.\n");
        let _ = writeln!(s, "seed={}", self.seed);
        let _ = writeln!(s, "score={}", self.score);
        let _ = writeln!(s, "moves={}", self.moves);
        let _ = writeln!(s, "ticks={}", self.ticks);
        let _ = writeln!(s, "ms={}", self.ms);
        let _ = writeln!(s, "redeals={}", self.redeals);
        let _ = writeln!(s, "state={}", format!("{:?}", self.state).to_lowercase());
        let _ = writeln!(s, "stock={}", pile_text(&self.stock));
        let _ = writeln!(s, "waste={}", pile_text(&self.waste));
        let _ = writeln!(
            s,
            "foundations={}",
            self.foundations
                .iter()
                .map(|p| pile_text(p))
                .collect::<Vec<String>>()
                .join("|")
        );
        let _ = writeln!(
            s,
            "tableau={}",
            self.tableau
                .iter()
                .map(|p| pile_text(p))
                .collect::<Vec<String>>()
                .join("|")
        );
        s
    }
}

/// A pile as fixture text: `12,-3,47` (top last).
fn pile_text(pile: &[Card]) -> String {
    pile.iter()
        .map(|c| format!("{}{}", if c.up { "" } else { "-" }, c.id))
        .collect::<Vec<String>>()
        .join(",")
}

/// Parse a state file. Strict: 52 distinct ids, foundations ascending by suit
/// from the Ace, tableau tops face up, stock face down, waste face up. `None` on
/// anything else, so a broken fixture is a clean usage error and never a panic.
pub fn parse_state(text: &str) -> Option<Game> {
    let mut seed = 1u64;
    let mut score = 0i64;
    let mut moves = 0u64;
    let mut ticks = 0u64;
    let mut ms = 0u64;
    let mut redeals = 0u64;
    let mut won = false;
    let mut stock = String::new();
    let mut waste = String::new();
    let mut foundations = String::new();
    let mut tableau = String::new();

    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (k, v) = match line.split_once('=') {
            Some(kv) => kv,
            None => return None,
        };
        let v = v.trim();
        match k.trim() {
            "seed" => seed = v.parse().ok()?,
            "score" => score = v.parse().ok()?,
            "moves" => moves = v.parse().ok()?,
            "ticks" => ticks = v.parse().ok()?,
            "ms" => ms = v.parse().ok()?,
            "redeals" => redeals = v.parse().ok()?,
            "state" => won = v == "win",
            "stock" => stock = v.to_string(),
            "waste" => waste = v.to_string(),
            "foundations" => foundations = v.to_string(),
            "tableau" => tableau = v.to_string(),
            _ => return None,
        }
    }

    fn parse_pile(s: &str) -> Option<Vec<Card>> {
        let mut out = Vec::new();
        for tok in s.split(',') {
            let tok = tok.trim();
            if tok.is_empty() {
                continue;
            }
            let (down, digits) = match tok.strip_prefix('-') {
                Some(rest) => (true, rest),
                None => (false, tok),
            };
            let id: u8 = digits.parse().ok()?;
            if id as usize >= DECK_SIZE {
                return None;
            }
            out.push(Card {
                id,
                up: !down,
            });
        }
        Some(out)
    }

    let stock = parse_pile(&stock)?;
    let waste = parse_pile(&waste)?;
    let f: Vec<Vec<Card>> = foundations
        .split('|')
        .map(|p| parse_pile(p))
        .collect::<Option<Vec<Vec<Card>>>>()?;
    let t: Vec<Vec<Card>> = tableau
        .split('|')
        .map(|p| parse_pile(p))
        .collect::<Option<Vec<Vec<Card>>>>()?;
    if f.len() != SUITS || t.len() != 7 {
        return None;
    }

    // Every card exactly once.
    let mut seen = [false; DECK_SIZE];
    for pile in std::iter::once(&stock)
        .chain(std::iter::once(&waste))
        .chain(f.iter())
        .chain(t.iter())
    {
        for c in pile {
            if seen[c.id as usize] {
                return None;
            }
            seen[c.id as usize] = true;
        }
    }
    if !seen.iter().all(|s| *s) {
        return None;
    }

    // Foundations: one suit each, ascending from the Ace, all face up.
    for (s, pile) in f.iter().enumerate() {
        for (i, c) in pile.iter().enumerate() {
            if !c.up || suit(c.id) != s || rank(c.id) != i {
                return None;
            }
        }
    }
    // A face-down top card is not a position the game can be in: the rules flip
    // it the moment it is uncovered.
    for pile in t.iter() {
        if pile.last().is_some_and(|c| !c.up) {
            return None;
        }
    }
    // The stock is face down, the waste face up.
    if stock.iter().any(|c| c.up) || waste.iter().any(|c| !c.up) {
        return None;
    }

    let mut g = Game::new(seed);
    g.stock = stock;
    g.waste = waste;
    g.foundations = [f[0].clone(), f[1].clone(), f[2].clone(), f[3].clone()];
    let mut tab: [Vec<Card>; 7] = [
        Vec::new(),
        Vec::new(),
        Vec::new(),
        Vec::new(),
        Vec::new(),
        Vec::new(),
        Vec::new(),
    ];
    for (i, p) in t.iter().enumerate() {
        tab[i] = p.clone();
    }
    g.tableau = tab;
    g.score = score.max(0);
    g.moves = moves;
    g.ticks = ticks;
    g.ms = ms;
    g.redeals = redeals;
    g.state = if won { State::Win } else { State::Play };
    g.message = String::from("state loaded from file");
    g.selection = None;
    g.cursor = Cursor {
        area: Area::Stock,
        index: 0,
    };
    g.history.clear();
    g.pending = None;
    g.plan.clear();
    Some(g)
}

/// Card names for messages: `A♠ K♥ 7♣`.
pub fn names(cards: &[Card], mono: bool) -> String {
    cards
        .iter()
        .map(|c| face(c.id, mono))
        .collect::<Vec<String>>()
        .join(" ")
}

/// Milliseconds as `m:ss`.
pub fn clock(ms: u64) -> String {
    let secs = ms / 1000;
    format!("{}:{:02}", secs / 60, secs % 60)
}

// ---------------------------------------------------------------------------
// Rendering — a pure function of state
// ---------------------------------------------------------------------------

/// How to draw. The front-end fills this in from the window size, `--mono` and
/// `NO_COLOR`; the headless paths use `--width`/`--height`.
#[derive(Clone, Copy, Debug)]
pub struct RenderOpts {
    pub width: usize,
    pub height: usize,
    /// ASCII-only glyphs, no colour: for a terminal whose locale cannot render
    /// the box drawing and the suit characters.
    pub mono: bool,
    /// Emit ANSI colour (red for the red suits, dim for the decoration).
    pub color: bool,
}

impl Default for RenderOpts {
    fn default() -> Self {
        Self {
            width: REF_WIDTH,
            height: REF_HEIGHT,
            mono: false,
            color: false,
        }
    }
}

/// Card geometry.
pub const CARD_W: usize = 5;
pub const CARD_H: usize = 3;
/// Column pitch: a card plus the gap that makes a cascade readable.
pub const PITCH: usize = 7;
/// Left margin, and the column the score panel starts in.
pub const MARGIN: usize = 2;
pub const PANEL_X: usize = 52;

/// The x of tableau column `i`.
pub fn col_x(i: usize) -> usize {
    MARGIN + i * PITCH
}

/// The x of the top-row piles: stock, waste, then the four foundations.
pub fn top_x(area: Area, index: usize) -> usize {
    match area {
        Area::Stock => MARGIN,
        Area::Waste => MARGIN + PITCH,
        Area::Foundation => MARGIN + 22 + index * PITCH,
        Area::Tableau => col_x(index),
    }
}

/// The row the top-row piles start on, and the row the tableau starts on.
pub const TOP_ROW: usize = 2;
pub const GRID_ROW: usize = 7;

/// The frame: the whole screen as one string. The front-end writes `\x1b[H`
/// plus this string per frame, and that is the whole repaint — no per-cell
/// cursor addressing, so no flicker.
pub fn frame(g: &Game) -> String {
    render(g, RenderOpts::default())
}

/// The frame at a given size. Degrades in a fixed order when the window is
/// smaller than the reference: border, score panel, title, key legend. The
/// cards are always drawn.
pub fn render(g: &Game, o: RenderOpts) -> String {
    let w = o.width.max(20);
    let h = o.height.max(8);
    let border = w >= BORDER_MIN_WIDTH && h >= BORDER_MIN_HEIGHT;
    let panel = w >= PANEL_MIN_WIDTH;
    let title = h >= TITLE_MIN_HEIGHT;
    let legend = h >= LEGEND_MIN_HEIGHT;

    let mut lines: Vec<String> = (0..h).map(|_| String::new()).collect();
    // The border owns the outer ring of the window, so every content row moves
    // down by one when it is drawn.
    let off = usize::from(border);
    let title_row = off;
    let top_row = TOP_ROW + off;
    let grid_row = GRID_ROW + off;
    let reserve = if legend { 2 } else { 0 };

    if title {
        // An ASCII title: the frame must stay plain ASCII in --mono.
        lines[title_row] = format!(" {GAME_NAME} - {GAME_SUBTITLE}");
    }

    // -- the top row: stock, waste, foundations
    for (area, index) in [
        (Area::Stock, 0),
        (Area::Waste, 0),
        (Area::Foundation, 0),
        (Area::Foundation, 1),
        (Area::Foundation, 2),
        (Area::Foundation, 3),
    ] {
        let x = top_x(area, index);
        for r in 0..CARD_H {
            let row = top_row + r;
            if row < h {
                lines[row] = splice(&lines[row], x, &slot_row(g, area, index, r, o));
            }
        }
    }
    // Labels under the top row: how many cards are left, and which suit each
    // foundation is.
    if top_row + CARD_H < h {
        let mut label = String::new();
        label = splice(&label, top_x(Area::Stock, 0), "stock");
        label = splice(&label, top_x(Area::Waste, 0), "waste");
        for i in 0..SUITS {
            label = splice(&label, top_x(Area::Foundation, i), SUIT_NAMES[i]);
        }
        lines[top_row + CARD_H] = label;
    }

    // -- the tableau grid
    let grid_rows = (h - off)
        .saturating_sub(grid_row + reserve)
        .max(CARD_H);
    for col in 0..7 {
        let x = col_x(col);
        let pile = &g.tableau[col];
        let step = cascade_step(pile.len(), grid_rows);
        let mut hidden = 0usize;
        for (i, card) in pile.iter().enumerate() {
            let row = grid_row + i * step;
            if row + CARD_H > grid_row + grid_rows {
                hidden = pile.len() - i;
                break;
            }
            for r in 0..CARD_H {
                if row + r < h {
                    lines[row + r] = splice(&lines[row + r], x, &card_row(card, r, o));
                }
            }
        }
        if hidden > 0 {
            let at = (grid_row + grid_rows).saturating_sub(1);
            if at < h {
                lines[at] = splice(&lines[at], x, &format!("+{hidden}"));
            }
        }
    }

    // -- the score panel, to the right of the grid
    if panel {
        for (i, text) in panel_lines(g).iter().enumerate() {
            let at = grid_row + i;
            if at < h.saturating_sub(off + reserve) {
                lines[at] = splice(&lines[at], PANEL_X, text);
            }
        }
    }

    // -- the cursor and what is in hand
    let (cx, cy) = mark_of(g.cursor.area, g.cursor.index, g, top_row, grid_row, grid_rows);
    if cy < h {
        lines[cy] = splice(&lines[cy], cx, if g.selection.is_some() { "*" } else { ">" });
    }
    if let Some(sel) = g.selection {
        let (sx, sy) = mark_of(sel.area, sel.index, g, top_row, grid_row, grid_rows);
        if sy < h {
            lines[sy] = splice(&lines[sy], sx, "*");
        }
    }

    // -- the win banner, over the grid
    if g.state == State::Win {
        for (i, text) in win_banner(g).iter().enumerate() {
            let at = grid_row + 1 + i;
            if at < h.saturating_sub(off) {
                lines[at] = truncate(text, w.saturating_sub(2 * off));
            }
        }
    }

    // -- the status line and the key legend
    if legend {
        if h > off + 1 {
            lines[h - 2 - off] = format!(" {}", g.message);
        }
        lines[h - 1 - off] = format!(" {LEGEND}");
    }

    // -- the border, last, so it clips whatever it overlaps
    let lines = if border {
        borderize(&lines, w, h, o.mono)
    } else {
        lines
    };

    let mut out = String::with_capacity(w * h + h);
    for line in lines.iter() {
        let _ = writeln!(out, "{}", truncate(&pad(line, w), w));
    }
    if o.color {
        out = colorize(&out);
    }
    out
}

/// The frame, with the default 80x24 options.
impl Game {
    pub fn render(&self, o: RenderOpts) -> String {
        render(self, o)
    }
}

/// How many rows a cascade of `n` cards can use. A pile that outgrows the grid
/// compresses its cascade, and the cards that still do not fit are counted in a
/// `+n` marker at the bottom of the column.
pub fn cascade_step(n: usize, rows: usize) -> usize {
    if n <= 1 {
        return 1;
    }
    let room = rows.saturating_sub(CARD_H);
    (room / (n - 1)).max(1)
}

/// One row of one card.
pub fn card_row(card: &Card, row: usize, o: RenderOpts) -> String {
    if !card.up {
        return back_box(row, o.mono);
    }
    let f = face(card.id, o.mono);
    let (l, r) = if o.mono { ('|', '|') } else { ('│', '│') };
    match row {
        0 => box_top(o.mono),
        1 => format!("{l} {f} {r}"),
        _ => box_bottom(o.mono),
    }
}

/// One row of a top-row slot: the stock's back, the waste's top card, a
/// foundation's top card, or the slot waiting for one.
fn slot_row(g: &Game, area: Area, index: usize, row: usize, o: RenderOpts) -> String {
    match area {
        Area::Stock => {
            if g.stock.is_empty() {
                empty_box(row, o.mono)
            } else {
                back_box(row, o.mono)
            }
        }
        Area::Waste => match g.top(area, index) {
            Some(c) => card_row(&c, row, o),
            None => empty_box(row, o.mono),
        },
        Area::Foundation => match g.top(area, index) {
            Some(c) => card_row(&c, row, o),
            None => foundation_slot(row, index, o.mono),
        },
        Area::Tableau => empty_box(row, o.mono),
    }
}

/// The box a card sits in: `┌───┐` / `+---+`.
pub fn box_top(mono: bool) -> String {
    if mono {
        String::from("+---+")
    } else {
        String::from("┌───┐")
    }
}

pub fn box_bottom(mono: bool) -> String {
    if mono {
        String::from("+---+")
    } else {
        String::from("└───┘")
    }
}

/// A face-down card: the stock's back.
pub fn back_box(row: usize, mono: bool) -> String {
    let (l, r) = if mono { ('|', '|') } else { ('│', '│') };
    match row {
        0 => box_top(mono),
        1 => format!("{l}### {r}"),
        _ => box_bottom(mono),
    }
}

/// An empty slot: the outline of where a pile may go.
pub fn empty_box(row: usize, mono: bool) -> String {
    let (l, r) = if mono { ('|', '|') } else { ('│', '│') };
    match row {
        0 => box_top(mono),
        1 => format!("{l}   {r}"),
        _ => box_bottom(mono),
    }
}

/// An empty foundation: it is waiting for the Ace of its own suit.
pub fn foundation_slot(row: usize, suit: usize, mono: bool) -> String {
    let (l, r) = if mono { ('|', '|') } else { ('│', '│') };
    match row {
        0 => box_top(mono),
        1 => {
            let s = if mono {
                SUIT_ASCII[suit]
            } else {
                SUIT_CHARS[suit]
            };
            format!("{l} {s} {r}")
        }
        _ => box_bottom(mono),
    }
}

/// The right-hand panel: score, moves, time, the record, the pile counts, the
/// keys. Dropped whole when the window is narrower than [`PANEL_MIN_WIDTH`].
pub fn panel_lines(g: &Game) -> Vec<String> {
    vec![
        format!("SCORE {:>6}", g.score),
        format!("MOVES {:>6}", g.moves),
        format!("TIME {:>7}", clock(g.ms)),
        format!("BEST {:>6}", g.best_score),
        format!("WINS {:>6}", g.best_wins),
        format!("GAMES {:>6}", g.best_games),
        String::new(),
        format!("STOCK {:>5}", g.stock.len()),
        format!("WASTE {:>5}", g.waste.len()),
        format!("UP {:>7}", g.foundation_count()),
        format!("REDEAL {:>4}", g.redeals),
        String::new(),
        format!("UNDO {:>6}", g.history.len()),
        format!(
            "TAKE {:>6}",
            g.selection
                .map(|s| format!("{}#{}", s.area.name(), s.count))
                .unwrap_or_else(|| "-".into())
        ),
        String::new(),
        String::from("d draw  f collect"),
        String::from("u undo  n new deal"),
        String::from("q quit"),
    ]
}

/// The win banner: what you did, and the record.
pub fn win_banner(g: &Game) -> Vec<String> {
    vec![
        String::from("*** YOU WIN ***"),
        format!(
            "score {}  moves {}  time {}",
            g.score,
            g.moves,
            clock(g.ms)
        ),
        format!(
            "best {}  redeals {}",
            g.best_score.max(g.score as u64),
            g.redeals
        ),
        String::from("n for a new deal, q to quit"),
    ]
}

/// Where a pile's marker goes: the column to the left of the pile, on the row
/// of that pile's top card.
fn mark_of(area: Area, index: usize, g: &Game, top_row: usize, grid_row: usize, grid_rows: usize) -> (usize, usize) {
    if matches!(area, Area::Tableau) {
        let n = g.tableau[index].len();
        let step = cascade_step(n, grid_rows);
        let row = if n == 0 {
            grid_row
        } else {
            grid_row + (n - 1).saturating_mul(step)
        };
        (col_x(index).saturating_sub(1), row.saturating_add(1))
    } else {
        (top_x(area, index).saturating_sub(1), top_row + 1)
    }
}

/// Pad a line to the window width.
fn pad(s: &str, w: usize) -> String {
    let mut out = s.to_string();
    while out.chars().count() < w {
        out.push(' ');
    }
    out
}

/// Cut a line to the window width.
fn truncate(s: &str, w: usize) -> String {
    if s.chars().count() <= w {
        s.to_string()
    } else {
        let mut t: String = s.chars().take(w.saturating_sub(1)).collect();
        t.push('.'); // ASCII: the frame must stay plain text in --mono
        t
    }
}

/// Write `text` into `line` at column `x`, padding with spaces as needed.
fn splice(line: &str, x: usize, text: &str) -> String {
    let mut chars: Vec<char> = line.chars().collect();
    while chars.len() < x + text.chars().count() {
        chars.push(' ');
    }
    for (i, c) in text.chars().enumerate() {
        let at = x + i;
        if at < chars.len() {
            chars[at] = c;
        }
    }
    chars.into_iter().collect()
}

/// The border, drawn over the finished frame. The outer ring of the window is
/// the border, so every content row moves down by one.
fn borderize(lines: &[String], w: usize, h: usize, mono: bool) -> Vec<String> {
    let (tl, tr, bl, br, v) = if mono {
        ('+', '+', '+', '+', '|')
    } else {
        ('┌', '┐', '└', '┘', '│')
    };
    let inner = w.saturating_sub(2);
    let mut out = Vec::with_capacity(h);
    out.push(format!("{tl}{}{tr}", "-".repeat(inner)));
    for i in 1..h.saturating_sub(1) {
        let content = lines.get(i).cloned().unwrap_or_default();
        out.push(format!("{v}{}{v}", truncate(&pad(&content, inner), inner)));
    }
    out.push(format!("{bl}{}{br}", "-".repeat(inner)));
    out.truncate(h);
    out
}

/// Colour the finished frame: red for the red suits, dim for the decoration.
/// A pure string transform, so the colour choice is testable.
pub fn colorize(s: &str) -> String {
    const RED: &str = "\x1b[31m";
    const DIM: &str = "\x1b[2m";
    const RESET: &str = "\x1b[0m";
    let mut out = String::with_capacity(s.len() + 128);
    for c in s.chars() {
        match c {
            '♥' | '♦' => {
                out.push_str(RED);
                out.push(c);
                out.push_str(RESET);
            }
            '┌' | '┐' | '└' | '┘' | '│' | '─' => {
                out.push_str(DIM);
                out.push(c);
                out.push_str(RESET);
            }
            _ => out.push(c),
        }
    }
    out
}

/// The plain-text state dump: what `--headless` prints, and what the harness
/// greps. No escapes, no layout — the same facts as the frame, in a stable
/// order.
pub fn plain_state(g: &Game) -> String {
    let mut s = String::new();
    for (k, v) in g.result_kv() {
        let _ = writeln!(s, "{k}={v}");
    }
    for suit in 0..SUITS {
        let _ = writeln!(
            s,
            "foundation[{}]={}",
            SUIT_NAMES[suit],
            pile_text(&g.foundations[suit])
        );
    }
    for col in 0..7 {
        let _ = writeln!(s, "tableau[{}]={}", col + 1, pile_text(&g.tableau[col]));
    }
    let _ = writeln!(s, "stock={}", pile_text(&g.stock));
    let _ = writeln!(s, "waste={}", pile_text(&g.waste));
    s
}
