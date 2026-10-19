//! The Frostbite Solitaire window (libcosmic).
//!
//! The table is one canvas: the stock and the waste top left, the four
//! foundations top right, the seven tableau columns below. Drag a card (and
//! everything on top of it) to move it; click the stock to draw; double-click
//! a card to send it home. Every rule is `pengy_solitaire::Game`'s: this file
//! only turns the pointer into `select`/`drop`/`draw` calls and paints the
//! piles.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use cosmic::iced::keyboard::{self, key::Named, Key};
use cosmic::iced::widget::canvas::path::{arc::Elliptical, Builder};
use cosmic::iced::widget::canvas::stroke::{LineCap, LineJoin};
use cosmic::iced::widget::canvas::{self, Frame, Geometry, Path, Stroke, Text};
use cosmic::iced::{alignment, mouse, Alignment, Color, Length, Point, Radians, Rectangle, Size, Subscription, Vector};
use cosmic::prelude::*;
use cosmic::widget::{self, button, container, nav_bar, row, text};

use crate::{is_red, rank, suit, Area, Card, Game, Selection, State, RANK_ACE, TICK_MS};

use super::{apply_best, bank, Best};

pub const APP_ID: &str = "io.catbee.pengyos.Solitaire";

/// How long two clicks on the same card may be apart to count as a double-click.
const DOUBLE_CLICK: Duration = Duration::from_millis(400);

/// What `main` hands the window.
pub struct Flags {
    pub game: Game,
    /// Where the record lives; `None` with `--no-save`.
    pub dir: Option<PathBuf>,
    /// Let the demo player play.
    pub autopilot: bool,
}

#[derive(Clone, Debug)]
pub enum Message {
    /// Move `count` cards from the top of a pile onto another pile.
    Move { from: Area, from_index: usize, count: usize, to: Area, to_index: usize },
    /// The stock was clicked: draw three, or recycle the waste.
    Draw,
    /// A double-click: send this pile's top card to its foundation.
    Home(Area, usize),
    Collect,
    Undo,
    NewDeal,
    Tick,
    Quit,
}

pub struct App {
    core: cosmic::Core,
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
    /// The current game is in the record already (won, or abandoned).
    banked: bool,
}

impl App {
    /// Bank the game into the record once: on the win, or when it is given up
    /// (a new deal, the window closing) after at least one move.
    fn bank(&mut self) {
        if self.banked || (self.game.moves == 0 && self.game.state != State::Win) {
            return;
        }
        if let Some(dir) = &self.dir {
            bank(dir, &mut self.game);
        }
        self.banked = true;
    }

    fn after_move(&mut self) {
        if self.game.state == State::Win {
            self.bank();
        }
    }
}

impl cosmic::Application for App {
    type Executor = cosmic::executor::Default;
    type Flags = Flags;
    type Message = Message;
    const APP_ID: &'static str = APP_ID;

    fn core(&self) -> &cosmic::Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut cosmic::Core {
        &mut self.core
    }

    fn nav_model(&self) -> Option<&nav_bar::Model> {
        None
    }

    fn init(core: cosmic::Core, flags: Flags) -> (Self, Task<cosmic::Action<Message>>) {
        let mut game = flags.game;
        if let Some(dir) = &flags.dir {
            apply_best(&mut game, &Best::load(dir));
        }
        game.message.clear();
        let mut app = App { core, game, dir: flags.dir, autopilot: flags.autopilot, banked: false };
        app.set_header_title("Frostbite Solitaire".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Frostbite Solitaire".into(), id),
            None => Task::none(),
        };
        (app, title)
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        let g = &mut self.game;
        match msg {
            Message::Move { from, from_index, count, to, to_index } => {
                if from == to && from_index == to_index {
                    return Task::none();
                }
                let sel = Selection { area: from, index: from_index, count };
                g.drop(sel, to, to_index);
                g.selection = None;
                self.after_move();
            }
            Message::Draw => g.draw(),
            Message::Home(area, index) => {
                if let Some(card) = g.top(area, index) {
                    let sel = Selection { area, index, count: 1 };
                    g.drop(sel, Area::Foundation, suit(card.id));
                    g.selection = None;
                    self.after_move();
                }
            }
            Message::Collect => {
                g.auto_collect();
                self.after_move();
            }
            Message::Undo => {
                g.undo();
            }
            Message::NewDeal => {
                self.bank();
                self.game.new_deal();
                self.game.message.clear();
                self.banked = false;
            }
            Message::Tick => {
                if self.autopilot && g.state == State::Play && !g.dead {
                    for c in g.autopilot_keys().chars() {
                        g.key(c);
                    }
                }
                g.tick();
                self.after_move();
            }
            Message::Quit => {
                self.bank();
                return cosmic::iced::exit();
            }
        }
        Task::none()
    }

    fn on_app_exit(&mut self) -> Option<Message> {
        self.bank();
        None
    }

    fn header_start(&self) -> Vec<Element<'_, Message>> {
        vec![
            button::standard("New Game").on_press(Message::NewDeal).into(),
            button::standard("Undo")
                .on_press_maybe((!self.game.history.is_empty()).then_some(Message::Undo))
                .into(),
            button::standard("Collect")
                .on_press_maybe((self.game.state == State::Play).then_some(Message::Collect))
                .into(),
        ]
    }

    fn view(&self) -> Element<'_, Message> {
        let g = &self.game;
        let spacing = cosmic::theme::spacing();
        let secs = g.ms / 1000;
        let mut status: Vec<Element<'_, Message>> = vec![
            text::body(format!("Score {}", g.score)).into(),
            text::body(format!("Moves {}", g.moves)).into(),
            text::body(format!("Time {}:{:02}", secs / 60, secs % 60)).into(),
        ];
        if g.redeals > 0 {
            status.push(text::body(format!("Pass {}", g.redeals + 1)).into());
        }
        if g.best_score > 0 || g.best_games > 0 {
            status.push(
                text::caption(format!("Best {} · {} won of {}", g.best_score, g.best_wins, g.best_games)).into(),
            );
        }
        let message = if g.state == State::Win {
            String::new()
        } else if g.dead {
            "No more moves this deal: try Undo, or a New Game.".to_string()
        } else if g.message.starts_with("that is not") {
            "That card does not go there.".to_string()
        } else {
            String::new()
        };
        let bar = row::with_children([
            row::with_children(status).spacing(spacing.space_m).align_y(Alignment::Center).into(),
            widget::space::horizontal().into(),
            text::caption(message).into(),
        ])
        .align_y(Alignment::Center)
        .padding([spacing.space_xxs, spacing.space_s]);

        let table = canvas::Canvas::new(Table { game: g }).width(Length::Fill).height(Length::Fill);
        container(widget::column::with_children([table.into(), bar.into()]))
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        let keys = cosmic::iced::event::listen_with(|event, status, _| {
            let cosmic::iced::Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, .. }) = event else {
                return None;
            };
            if status == cosmic::iced::event::Status::Captured {
                return None;
            }
            match key.as_ref() {
                Key::Character("z") if modifiers.control() => Some(Message::Undo),
                Key::Character("n") if modifiers.control() => Some(Message::NewDeal),
                Key::Character("q") if modifiers.control() => Some(Message::Quit),
                Key::Named(Named::F2) => Some(Message::NewDeal),
                Key::Character("f") => Some(Message::Collect),
                Key::Character("d" | " ") => Some(Message::Draw),
                _ => None,
            }
        });
        // The clock runs while there is a game to play.
        let clock = if self.game.state == State::Play {
            cosmic::iced::time::every(Duration::from_millis(TICK_MS)).map(|_| Message::Tick)
        } else {
            Subscription::none()
        };
        Subscription::batch([keys, clock])
    }
}

// ---------------------------------------------------------------------------
// The table
// ---------------------------------------------------------------------------

struct Table<'a> {
    game: &'a Game,
}

/// A card or pile under the pointer.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Hit {
    area: Area,
    index: usize,
    /// The card's position in its pile, `None` on an empty pile.
    card: Option<usize>,
}

/// A drag in progress.
#[derive(Clone, Copy, Debug)]
struct Drag {
    area: Area,
    index: usize,
    count: usize,
    /// Pointer minus the top-left of the first dragged card.
    grab: Vector,
    at: Point,
    moved: bool,
}

#[derive(Default)]
struct TableState {
    drag: Option<Drag>,
    last_click: Option<(Instant, Area, usize)>,
}

/// Where everything is, for one size of the canvas.
struct Layout {
    cw: f32,
    ch: f32,
    gap: f32,
    left: f32,
    top: f32,
    tableau_top: f32,
    bottom: f32,
}

/// The fan of a tableau column, in card heights: a face-down card shows a
/// sliver, a face-up one its whole corner index.
const FAN_DOWN: f32 = 0.10;
const FAN_UP: f32 = 0.30;

impl Layout {
    fn new(size: Size, g: &Game) -> Layout {
        // Seven columns and eight gaps of 12% of a card across. Down: the top
        // row, then the longest column at its full fan. The cards shrink to
        // fit a long column, the way Aisleriot's do, instead of squeezing the
        // fan until the indices overlap; only a column too long for the
        // smallest card squeezes.
        let fan = (0..7)
            .map(|i| {
                let p = &g.tableau[i];
                p.iter().take(p.len().saturating_sub(1)).map(|c| if c.up { FAN_UP } else { FAN_DOWN }).sum::<f32>()
            })
            .fold(0.0f32, f32::max)
            .max(FAN_UP * 2.0);
        let by_width = size.width / 7.96;
        let by_height = size.height / (1.4 * (2.0 + fan) + 0.12 * 4.5);
        let cw = by_width.min(by_height).clamp(30.0, 150.0);
        let ch = cw * 1.4;
        let gap = cw * 0.12;
        let used = 7.0 * cw + 6.0 * gap;
        let left = ((size.width - used) / 2.0).max(gap);
        let top = gap * 1.5;
        Layout { cw, ch, gap, left, top, tableau_top: top + ch + gap * 2.0, bottom: size.height - gap }
    }

    fn col_x(&self, col: usize) -> f32 {
        self.left + col as f32 * (self.cw + self.gap)
    }

    /// The top-row slot of a pile: stock 0, waste 1, foundations 3..=6.
    fn top_slot(area: Area, index: usize) -> usize {
        match area {
            Area::Stock => 0,
            Area::Waste => 1,
            Area::Foundation => 3 + index,
            Area::Tableau => unreachable!(),
        }
    }

    fn slot_rect(&self, area: Area, index: usize) -> Rectangle {
        match area {
            Area::Tableau => Rectangle::new(Point::new(self.col_x(index), self.tableau_top), Size::new(self.cw, self.ch)),
            _ => Rectangle::new(
                Point::new(self.col_x(Self::top_slot(area, index)), self.top),
                Size::new(self.cw, self.ch),
            ),
        }
    }

    /// The fan offsets of a tableau column, squeezed to fit the canvas.
    fn offsets(&self, pile: &[Card]) -> Vec<f32> {
        let (mut down, mut up) = (self.ch * FAN_DOWN, self.ch * FAN_UP);
        let need: f32 = pile.iter().take(pile.len().saturating_sub(1)).map(|c| if c.up { up } else { down }).sum();
        let room = self.bottom - self.tableau_top - self.ch;
        if need > room && need > 0.0 {
            let k = (room / need).max(0.2);
            down *= k;
            up *= k;
        }
        let mut y = self.tableau_top;
        pile.iter()
            .map(|c| {
                let at = y;
                y += if c.up { up } else { down };
                at
            })
            .collect()
    }

    /// How far each of the waste's top three cards is fanned to the right.
    fn waste_fan(&self, len: usize) -> Vec<(usize, f32)> {
        let shown = len.min(3);
        (0..shown).map(|k| (len - shown + k, k as f32 * self.cw * 0.22)).collect()
    }

    fn hit(&self, g: &Game, p: Point) -> Option<Hit> {
        for i in 0..7 {
            let pile = &g.tableau[i];
            let ys = self.offsets(pile);
            let x = self.col_x(i);
            if p.x < x || p.x > x + self.cw {
                continue;
            }
            for k in (0..pile.len()).rev() {
                if p.y >= ys[k] && p.y <= ys[k] + self.ch {
                    return Some(Hit { area: Area::Tableau, index: i, card: Some(k) });
                }
            }
            if self.slot_rect(Area::Tableau, i).contains(p) {
                return Some(Hit { area: Area::Tableau, index: i, card: None });
            }
        }
        if self.slot_rect(Area::Stock, 0).contains(p) {
            return Some(Hit { area: Area::Stock, index: 0, card: g.stock.len().checked_sub(1) });
        }
        if let Some(&(k, dx)) = self.waste_fan(g.waste.len()).last() {
            let r = self.slot_rect(Area::Waste, 0);
            if Rectangle::new(Point::new(r.x + dx, r.y), r.size()).contains(p) {
                return Some(Hit { area: Area::Waste, index: 0, card: Some(k) });
            }
        }
        for f in 0..4 {
            if self.slot_rect(Area::Foundation, f).contains(p) {
                return Some(Hit { area: Area::Foundation, index: f, card: g.foundations[f].len().checked_sub(1) });
            }
        }
        None
    }

    /// Where a dragged card would land: the pile under its middle.
    fn target(&self, centre: Point) -> Option<(Area, usize)> {
        let col = ((centre.x - self.left + self.gap / 2.0) / (self.cw + self.gap)).floor();
        if !(0.0..7.0).contains(&col) {
            return None;
        }
        let col = col as usize;
        if centre.y >= self.tableau_top - self.gap {
            Some((Area::Tableau, col))
        } else if col >= 3 {
            Some((Area::Foundation, col - 3))
        } else {
            None
        }
    }

    /// The top-left of every card of a pile, as drawn.
    fn positions(&self, g: &Game, area: Area, index: usize) -> Vec<Point> {
        match area {
            Area::Tableau => {
                let x = self.col_x(index);
                self.offsets(&g.tableau[index]).into_iter().map(|y| Point::new(x, y)).collect()
            }
            Area::Waste => {
                let r = self.slot_rect(Area::Waste, 0);
                let fan = self.waste_fan(g.waste.len());
                (0..g.waste.len())
                    .map(|k| {
                        let dx = fan.iter().find(|(i, _)| *i == k).map(|(_, d)| *d).unwrap_or(0.0);
                        Point::new(r.x + dx, r.y)
                    })
                    .collect()
            }
            _ => {
                let r = self.slot_rect(area, index);
                vec![r.position(); g.pile(area, index).len()]
            }
        }
    }
}

impl canvas::Program<Message, cosmic::Theme, cosmic::Renderer> for Table<'_> {
    type State = TableState;

    fn update(
        &self,
        state: &mut TableState,
        event: &canvas::Event,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> Option<canvas::Action<Message>> {
        let canvas::Event::Mouse(e) = event else { return None };
        let g = self.game;
        let lay = Layout::new(bounds.size(), g);
        match e {
            mouse::Event::ButtonPressed(mouse::Button::Left) => {
                let p = cursor.position_in(bounds)?;
                let hit = lay.hit(g, p)?;
                if g.state != State::Play {
                    return None;
                }
                if hit.area == Area::Stock {
                    return Some(canvas::Action::publish(Message::Draw).and_capture());
                }
                let k = hit.card?;
                let pile = g.pile(hit.area, hit.index);
                let now = Instant::now();
                let top = k + 1 == pile.len();
                if top {
                    if let Some((t, a, i)) = state.last_click {
                        if a == hit.area && i == hit.index && now.duration_since(t) < DOUBLE_CLICK {
                            state.last_click = None;
                            state.drag = None;
                            return Some(canvas::Action::publish(Message::Home(hit.area, hit.index)).and_capture());
                        }
                    }
                }
                state.last_click = Some((now, hit.area, hit.index));
                // A face-up card that heads a legal run (or any top card) can be picked up.
                let run = &pile[k..];
                let movable = match hit.area {
                    Area::Tableau => Game::legal_run(run),
                    _ => top && pile[k].up,
                };
                if !movable {
                    return None;
                }
                let origin = lay.positions(g, hit.area, hit.index)[k];
                state.drag = Some(Drag {
                    area: hit.area,
                    index: hit.index,
                    count: run.len(),
                    grab: p - origin,
                    at: p,
                    moved: false,
                });
                Some(canvas::Action::request_redraw().and_capture())
            }
            mouse::Event::CursorMoved { .. } => {
                let d = state.drag.as_mut()?;
                let p = cursor.position_in(bounds).unwrap_or(d.at);
                if (p.x - d.at.x).abs() + (p.y - d.at.y).abs() > 3.0 {
                    d.moved = true;
                }
                if d.moved {
                    d.at = p;
                }
                Some(canvas::Action::request_redraw())
            }
            mouse::Event::ButtonReleased(mouse::Button::Left) => {
                let d = state.drag.take()?;
                if !d.moved {
                    return Some(canvas::Action::request_redraw());
                }
                let top_left = d.at - d.grab;
                let centre = Point::new(top_left.x + lay.cw / 2.0, top_left.y + lay.ch / 3.0);
                match lay.target(centre) {
                    Some((to, to_index)) => Some(
                        canvas::Action::publish(Message::Move {
                            from: d.area,
                            from_index: d.index,
                            count: d.count,
                            to,
                            to_index,
                        })
                        .and_capture(),
                    ),
                    None => Some(canvas::Action::request_redraw()),
                }
            }
            _ => None,
        }
    }

    fn draw(
        &self,
        state: &TableState,
        renderer: &cosmic::Renderer,
        theme: &cosmic::Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<Geometry<cosmic::Renderer>> {
        let g = self.game;
        let lay = Layout::new(bounds.size(), g);
        let mut frame = Frame::new(renderer, bounds.size());
        let dark = theme.cosmic().is_dark;
        let felt = if dark { rgb(0x15, 0x4a, 0x2c) } else { rgb(0x1f, 0x6e, 0x3e) };
        frame.fill_rectangle(Point::ORIGIN, bounds.size(), felt);

        let paint = Paint::new(&lay);
        // Which cards are in the hand, and so drawn last, under the pointer.
        let lifted = state.drag.filter(|d| d.moved);
        let skip = |area: Area, index: usize, k: usize, len: usize| {
            lifted.is_some_and(|d| d.area == area && d.index == index && k + d.count >= len)
        };

        // The top row.
        let stock = lay.slot_rect(Area::Stock, 0);
        if g.stock.is_empty() {
            paint.slot(&mut frame, stock.position());
            paint.recycle(&mut frame, stock, !g.waste.is_empty());
        } else {
            // A little depth: one back per eight cards left.
            let layers = g.stock.len().div_ceil(8).min(3);
            for l in 0..layers {
                let d = (layers - 1 - l) as f32 * 2.0;
                paint.back(&mut frame, Point::new(stock.x + d, stock.y - d));
            }
        }
        let waste = lay.slot_rect(Area::Waste, 0);
        paint.slot(&mut frame, waste.position());
        let wpos = lay.positions(g, Area::Waste, 0);
        for (k, _) in lay.waste_fan(g.waste.len()) {
            if !skip(Area::Waste, 0, k, g.waste.len()) {
                paint.face(&mut frame, wpos[k], g.waste[k]);
            }
        }
        for f in 0..4 {
            let r = lay.slot_rect(Area::Foundation, f);
            paint.slot(&mut frame, r.position());
            paint.pip(&mut frame, f, Point::new(r.x + lay.cw / 2.0, r.y + lay.ch / 2.0), lay.cw * 0.42, Color::from_rgba(1.0, 1.0, 1.0, 0.22));
            let pile = &g.foundations[f];
            // The card under the top one shows while the top one is dragged.
            for k in pile.len().saturating_sub(2)..pile.len() {
                if !skip(Area::Foundation, f, k, pile.len()) {
                    paint.face(&mut frame, r.position(), pile[k]);
                }
            }
        }

        // The tableau.
        for i in 0..7 {
            let pile = &g.tableau[i];
            paint.slot(&mut frame, lay.slot_rect(Area::Tableau, i).position());
            let pos = lay.positions(g, Area::Tableau, i);
            for (k, c) in pile.iter().enumerate() {
                if skip(Area::Tableau, i, k, pile.len()) {
                    continue;
                }
                if c.up {
                    paint.face(&mut frame, pos[k], *c);
                } else {
                    paint.back(&mut frame, pos[k]);
                }
            }
        }

        // The hand.
        if let Some(d) = lifted {
            let pile = g.pile(d.area, d.index);
            let first = pile.len() - d.count;
            let step = match d.area {
                Area::Tableau => {
                    let pos = lay.positions(g, d.area, d.index);
                    if d.count > 1 { pos[first + 1].y - pos[first].y } else { 0.0 }
                }
                _ => 0.0,
            };
            let origin = d.at - d.grab;
            for (n, c) in pile[first..].iter().enumerate() {
                let at = Point::new(origin.x, origin.y + n as f32 * step);
                paint.shadow(&mut frame, at, 6.0);
                paint.face(&mut frame, at, *c);
            }
        }

        if g.state == State::Win {
            paint.banner(&mut frame, bounds.size(), g);
        }
        vec![frame.into_geometry()]
    }

    fn mouse_interaction(&self, state: &TableState, bounds: Rectangle, cursor: mouse::Cursor) -> mouse::Interaction {
        if state.drag.is_some_and(|d| d.moved) {
            return mouse::Interaction::Grabbing;
        }
        let Some(p) = cursor.position_in(bounds) else { return mouse::Interaction::default() };
        let g = self.game;
        match Layout::new(bounds.size(), g).hit(g, p) {
            Some(Hit { area: Area::Stock, .. }) if g.state == State::Play => mouse::Interaction::Pointer,
            Some(Hit { area, index, card: Some(k) }) if g.state == State::Play => {
                let pile = g.pile(area, index);
                let ok = match area {
                    Area::Tableau => Game::legal_run(&pile[k..]),
                    _ => k + 1 == pile.len(),
                };
                if ok { mouse::Interaction::Grab } else { mouse::Interaction::default() }
            }
            _ => mouse::Interaction::default(),
        }
    }
}

// ---------------------------------------------------------------------------
// Painting cards
// ---------------------------------------------------------------------------

fn rgb(r: u8, g: u8, b: u8) -> Color {
    Color::from_rgb8(r, g, b)
}

const RED: Color = Color { r: 0.78, g: 0.13, b: 0.16, a: 1.0 };
const BLACK: Color = Color { r: 0.11, g: 0.11, b: 0.13, a: 1.0 };
const PAPER: Color = Color { r: 0.99, g: 0.99, b: 0.98, a: 1.0 };
const FROST: Color = Color { r: 0.88, g: 0.93, b: 0.98, a: 1.0 };

struct Paint {
    cw: f32,
    ch: f32,
    radius: f32,
}

impl Paint {
    fn new(lay: &Layout) -> Paint {
        Paint { cw: lay.cw, ch: lay.ch, radius: lay.cw * 0.08 }
    }

    fn card_path(&self, at: Point) -> Path {
        Path::rounded_rectangle(at, Size::new(self.cw, self.ch), self.radius.into())
    }

    fn slot(&self, frame: &mut Frame<cosmic::Renderer>, at: Point) {
        let p = self.card_path(at);
        frame.fill(&p, Color::from_rgba(0.0, 0.0, 0.0, 0.12));
        frame.stroke(&p, Stroke::default().with_color(Color::from_rgba(1.0, 1.0, 1.0, 0.28)).with_width(1.5));
    }

    fn shadow(&self, frame: &mut Frame<cosmic::Renderer>, at: Point, lift: f32) {
        let p = Path::rounded_rectangle(
            Point::new(at.x + lift * 0.4, at.y + lift),
            Size::new(self.cw, self.ch),
            self.radius.into(),
        );
        frame.fill(&p, Color::from_rgba(0.0, 0.0, 0.0, 0.25));
    }

    /// The pattern on the back: a frost-blue card with a lattice of small
    /// diamonds inside a white rule.
    fn back(&self, frame: &mut Frame<cosmic::Renderer>, at: Point) {
        self.shadow(frame, at, 1.5);
        let p = self.card_path(at);
        frame.fill(&p, rgb(0x2b, 0x5c, 0x9a));
        let inset = self.cw * 0.08;
        let inner = Path::rounded_rectangle(
            Point::new(at.x + inset, at.y + inset),
            Size::new(self.cw - 2.0 * inset, self.ch - 2.0 * inset),
            (self.radius * 0.6).into(),
        );
        frame.fill(&inner, rgb(0x34, 0x6d, 0xb3));
        let step = self.cw * 0.16;
        let s = step * 0.32;
        let (cols, rows) = (((self.cw - 2.0 * inset) / step) as i32, ((self.ch - 2.0 * inset) / step) as i32);
        let ox = at.x + inset + (self.cw - 2.0 * inset - (cols - 1) as f32 * step) / 2.0;
        let oy = at.y + inset + (self.ch - 2.0 * inset - (rows - 1) as f32 * step) / 2.0;
        let lattice = Path::new(|b| {
            for r in 0..rows {
                for c in 0..cols {
                    let (x, y) = (ox + c as f32 * step, oy + r as f32 * step);
                    b.move_to(Point::new(x, y - s));
                    b.line_to(Point::new(x + s, y));
                    b.line_to(Point::new(x, y + s));
                    b.line_to(Point::new(x - s, y));
                    b.close();
                }
            }
        });
        frame.fill(&lattice, Color::from_rgba(1.0, 1.0, 1.0, 0.22));
        frame.stroke(&inner, Stroke::default().with_color(Color::from_rgba(1.0, 1.0, 1.0, 0.85)).with_width(1.2));
        frame.stroke(&p, Stroke::default().with_color(Color::from_rgba(0.0, 0.0, 0.0, 0.35)).with_width(1.0));
    }

    fn face(&self, frame: &mut Frame<cosmic::Renderer>, at: Point, card: Card) {
        self.shadow(frame, at, 1.5);
        let p = self.card_path(at);
        frame.fill(&p, PAPER);
        frame.stroke(&p, Stroke::default().with_color(Color::from_rgba(0.0, 0.0, 0.0, 0.30)).with_width(1.0));
        let s = suit(card.id);
        let r = rank(card.id);
        let colour = if is_red(card.id) { RED } else { BLACK };
        let label = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"][r];

        // The index in the top-left corner: the rank, the suit under it. It is
        // what shows on a card fanned under another, so it is the big one.
        let corner = self.ch * 0.14;
        let cx = at.x + self.cw * 0.16;
        self.label(frame, label, Point::new(cx, at.y + self.ch * 0.035), corner, colour);
        self.pip(frame, s, Point::new(cx, at.y + self.ch * 0.225), self.ch * 0.075, colour);
        // The same index, small, at the top right, so a waste fan reads too.
        self.pip(frame, s, Point::new(at.x + self.cw * 0.84, at.y + self.ch * 0.09), self.ch * 0.09, colour);

        // The middle: one big pip, or a frosted panel with the letter for a
        // court card.
        let mid = Point::new(at.x + self.cw * 0.55, at.y + self.ch * 0.62);
        if r >= 10 {
            let w = self.cw * 0.56;
            let h = self.ch * 0.50;
            let panel = Path::rounded_rectangle(Point::new(mid.x - w / 2.0, mid.y - h / 2.0), Size::new(w, h), (self.radius * 0.6).into());
            frame.fill(&panel, FROST);
            frame.stroke(&panel, Stroke::default().with_color(Color { a: 0.5, ..colour }).with_width(1.0));
            let size = h * 0.52;
            self.label(frame, label, Point::new(mid.x, mid.y - h * 0.06 - size / 2.0), size, colour);
            self.pip(frame, s, Point::new(mid.x, mid.y + h * 0.33), h * 0.18, colour);
        } else {
            let size = if r == RANK_ACE { self.cw * 0.58 } else { self.cw * 0.44 };
            self.pip(frame, s, mid, size, colour);
        }
    }

    /// A rank, centred on `top.x`, `size` tall, in the stroke font below.
    /// Canvas text is always painted above every shape in the canvas, so a
    /// card's letters would show through the card covering it; strokes are
    /// painted in order, like the cards.
    fn label(&self, frame: &mut Frame<cosmic::Renderer>, label: &str, top: Point, size: f32, colour: Color) {
        let advance = size * 0.62;
        let gap = size * 0.12;
        let n = label.chars().count() as f32;
        let width = n * advance + (n - 1.0) * gap;
        let path = Path::new(|b| {
            for (i, c) in label.chars().enumerate() {
                let x = top.x - width / 2.0 + i as f32 * (advance + gap);
                glyph(b, c, Point::new(x, top.y), size);
            }
        });
        frame.stroke(
            &path,
            Stroke {
                width: size * 0.14,
                line_cap: LineCap::Round,
                line_join: LineJoin::Round,
                ..Stroke::default().with_color(colour)
            },
        );
    }

    /// A suit symbol, drawn as a path so it never depends on a font having
    /// the glyph. `size` is its height. Suits: 0 ♠, 1 ♥, 2 ♣, 3 ♦.
    fn pip(&self, frame: &mut Frame<cosmic::Renderer>, s: usize, c: Point, size: f32, colour: Color) {
        let path = match s {
            1 => Path::new(|b| heart(b, c, size, false)),
            3 => Path::new(|b| {
                let (w, h) = (size * 0.38, size * 0.5);
                b.move_to(Point::new(c.x, c.y - h));
                b.line_to(Point::new(c.x + w, c.y));
                b.line_to(Point::new(c.x, c.y + h));
                b.line_to(Point::new(c.x - w, c.y));
                b.close();
            }),
            0 => Path::new(|b| {
                heart(b, Point::new(c.x, c.y - size * 0.06), size * 0.9, true);
                stem(b, c, size);
            }),
            _ => Path::new(|b| {
                let r = size * 0.21;
                b.circle(Point::new(c.x, c.y - size * 0.24), r);
                b.circle(Point::new(c.x - size * 0.22, c.y + size * 0.04), r);
                b.circle(Point::new(c.x + size * 0.22, c.y + size * 0.04), r);
                b.circle(Point::new(c.x, c.y - size * 0.02), r * 0.7);
                stem(b, c, size);
            }),
        };
        frame.fill(&path, colour);
    }

    /// The empty stock: a ring to click, when there is a waste to recycle.
    fn recycle(&self, frame: &mut Frame<cosmic::Renderer>, r: Rectangle, live: bool) {
        let c = Point::new(r.x + r.width / 2.0, r.y + r.height / 2.0);
        let ring = Path::circle(c, self.cw * 0.25);
        let a = if live { 0.55 } else { 0.18 };
        frame.stroke(&ring, Stroke::default().with_color(Color::from_rgba(1.0, 1.0, 1.0, a)).with_width(self.cw * 0.06));
    }

    fn banner(&self, frame: &mut Frame<cosmic::Renderer>, size: Size, g: &Game) {
        let (w, h) = ((size.width * 0.6).min(520.0), 150.0);
        let at = Point::new((size.width - w) / 2.0, (size.height - h) / 2.0);
        let p = Path::rounded_rectangle(at, Size::new(w, h), 16.0.into());
        frame.fill(&p, Color::from_rgba(0.05, 0.12, 0.2, 0.88));
        frame.stroke(&p, Stroke::default().with_color(Color::from_rgba(1.0, 1.0, 1.0, 0.5)).with_width(1.5));
        let secs = g.ms / 1000;
        for (line, dy, px) in [
            ("You won!".to_string(), 46.0, 36.0),
            (format!("Score {} · {} moves · {}:{:02}", g.score, g.moves, secs / 60, secs % 60), 92.0, 18.0),
            ("New Game (Ctrl+N) deals again".to_string(), 122.0, 14.0),
        ] {
            frame.fill_text(Text {
                content: line,
                position: Point::new(size.width / 2.0, at.y + dy),
                color: Color::WHITE,
                size: px.into(),
                align_x: Alignment::Center.into(),
                align_y: alignment::Vertical::Center,
                ..Text::default()
            });
        }
    }
}

/// A heart of height `size` centred on `c`; `flip` turns it upside down (the
/// body of a spade).
fn heart(b: &mut Builder, c: Point, size: f32, flip: bool) {
    let s = size;
    let y = |dy: f32| if flip { c.y - dy * s } else { c.y + dy * s };
    let x = |dx: f32| c.x + dx * s;
    b.move_to(Point::new(x(0.0), y(0.46)));
    b.bezier_curve_to(Point::new(x(-0.08), y(0.34)), Point::new(x(-0.5), y(0.12)), Point::new(x(-0.5), y(-0.14)));
    b.bezier_curve_to(Point::new(x(-0.5), y(-0.42)), Point::new(x(-0.14), y(-0.52)), Point::new(x(0.0), y(-0.26)));
    b.bezier_curve_to(Point::new(x(0.14), y(-0.52)), Point::new(x(0.5), y(-0.42)), Point::new(x(0.5), y(-0.14)));
    b.bezier_curve_to(Point::new(x(0.5), y(0.12)), Point::new(x(0.08), y(0.34)), Point::new(x(0.0), y(0.46)));
    b.close();
}

/// The flared stem under a spade or a club.
fn stem(b: &mut Builder, c: Point, size: f32) {
    b.move_to(Point::new(c.x, c.y + size * 0.05));
    b.line_to(Point::new(c.x + size * 0.17, c.y + size * 0.5));
    b.line_to(Point::new(c.x - size * 0.17, c.y + size * 0.5));
    b.close();
}

/// One character of the stroke font: `A 2-9 0 1 J Q K`, in a box `size` tall
/// and about 0.6 `size` wide, top-left at `o`. Built to be stroked with round
/// caps at 15% of the height.
fn glyph(b: &mut Builder, c: char, o: Point, size: f32) {
    let p = |x: f32, y: f32| Point::new(o.x + x * size, o.y + y * size);
    let ellipse = |b: &mut Builder, cx: f32, cy: f32, rx: f32, ry: f32| {
        b.move_to(p(cx + rx, cy));
        b.ellipse(Elliptical {
            center: p(cx, cy),
            radii: Vector::new(rx * size, ry * size),
            rotation: Radians(0.0),
            start_angle: Radians(0.0),
            end_angle: Radians(std::f32::consts::TAU),
        });
    };
    match c {
        'A' => {
            b.move_to(p(0.0, 1.0));
            b.line_to(p(0.31, 0.0));
            b.line_to(p(0.62, 1.0));
            b.move_to(p(0.12, 0.64));
            b.line_to(p(0.50, 0.64));
        }
        '1' => {
            b.move_to(p(0.10, 0.20));
            b.line_to(p(0.34, 0.0));
            b.line_to(p(0.34, 1.0));
        }
        '2' => {
            b.move_to(p(0.04, 0.24));
            b.bezier_curve_to(p(0.08, -0.06), p(0.60, -0.08), p(0.58, 0.28));
            b.bezier_curve_to(p(0.56, 0.52), p(0.20, 0.70), p(0.02, 1.0));
            b.line_to(p(0.62, 1.0));
        }
        '3' => {
            b.move_to(p(0.04, 0.12));
            b.bezier_curve_to(p(0.22, -0.06), p(0.58, -0.02), p(0.56, 0.24));
            b.bezier_curve_to(p(0.55, 0.42), p(0.36, 0.47), p(0.22, 0.47));
            b.move_to(p(0.22, 0.47));
            b.bezier_curve_to(p(0.48, 0.47), p(0.62, 0.58), p(0.60, 0.74));
            b.bezier_curve_to(p(0.58, 1.04), p(0.16, 1.06), p(0.02, 0.88));
        }
        '4' => {
            b.move_to(p(0.46, 1.0));
            b.line_to(p(0.46, 0.0));
            b.line_to(p(0.0, 0.70));
            b.line_to(p(0.64, 0.70));
        }
        '5' => {
            b.move_to(p(0.56, 0.0));
            b.line_to(p(0.10, 0.0));
            b.line_to(p(0.05, 0.46));
            b.bezier_curve_to(p(0.30, 0.32), p(0.62, 0.40), p(0.61, 0.68));
            b.bezier_curve_to(p(0.60, 1.02), p(0.18, 1.06), p(0.02, 0.88));
        }
        '6' => {
            b.move_to(p(0.54, 0.06));
            b.bezier_curve_to(p(0.22, -0.08), p(0.02, 0.24), p(0.03, 0.66));
            ellipse(b, 0.32, 0.70, 0.29, 0.30);
        }
        '7' => {
            b.move_to(p(0.0, 0.0));
            b.line_to(p(0.62, 0.0));
            b.line_to(p(0.20, 1.0));
        }
        '8' => {
            ellipse(b, 0.31, 0.25, 0.24, 0.24);
            ellipse(b, 0.31, 0.73, 0.29, 0.27);
        }
        '9' => {
            ellipse(b, 0.30, 0.31, 0.28, 0.30);
            b.move_to(p(0.58, 0.32));
            b.bezier_curve_to(p(0.60, 0.80), p(0.36, 1.06), p(0.06, 0.94));
        }
        '0' => ellipse(b, 0.31, 0.50, 0.29, 0.50),
        'J' => {
            b.move_to(p(0.26, 0.0));
            b.line_to(p(0.58, 0.0));
            b.move_to(p(0.58, 0.0));
            b.line_to(p(0.58, 0.68));
            b.bezier_curve_to(p(0.58, 1.06), p(0.06, 1.08), p(0.0, 0.72));
        }
        'Q' => {
            ellipse(b, 0.31, 0.48, 0.31, 0.48);
            b.move_to(p(0.38, 0.70));
            b.line_to(p(0.64, 1.02));
        }
        'K' => {
            b.move_to(p(0.02, 0.0));
            b.line_to(p(0.02, 1.0));
            b.move_to(p(0.60, 0.0));
            b.line_to(p(0.02, 0.62));
            b.move_to(p(0.22, 0.42));
            b.line_to(p(0.62, 1.0));
        }
        _ => {}
    }
}
