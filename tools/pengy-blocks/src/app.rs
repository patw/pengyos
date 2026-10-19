//! The Glacier Blocks window (libcosmic).
//!
//! One canvas: the 10x20 well in the middle, the hold slot on its left, the
//! next three pieces and the score on its right. Every rule (gravity, the
//! lock delay, the kicks, the 7-bag) is `pengy_blocks::Game`'s; this file
//! turns keys into the game's key characters and paints its state.

use std::path::PathBuf;
use std::time::Duration;

use cosmic::iced::keyboard::{self, key::Named, Key};
use cosmic::iced::widget::canvas::{self, Frame, Geometry, Path, Stroke, Text};
use cosmic::iced::{alignment, mouse, Alignment, Color, Length, Point, Rectangle, Size, Subscription};
use cosmic::prelude::*;
use cosmic::widget::{button, container, nav_bar};

use crate::{
    cells, landing_row, piece_colour, shape, Game, State, BOARD_ROWS, COLS, HIDDEN_ROWS, ROWS, TICK_MS,
};

use super::{apply_best, bank, Args, Best};

pub const APP_ID: &str = "io.catbee.pengyos.GlacierBlocks";

pub fn run(args: &Args, dir: PathBuf) -> i32 {
    let mut game = Game::new(args.seed);
    let dir = (!args.no_save).then_some(dir);
    if let Some(d) = &dir {
        apply_best(&mut game, &Best::load(d));
    }
    let flags = Flags { game, dir, autopilot: args.autopilot };
    let settings = cosmic::app::Settings::default()
        .size(Size::new(640.0, 760.0))
        .size_limits(cosmic::iced::Limits::NONE.min_width(420.0).min_height(480.0));
    match cosmic::app::run::<App>(settings, flags) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("pengy-blocks: {err}");
            1
        }
    }
}

pub struct Flags {
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
}

#[derive(Clone, Debug)]
pub enum Message {
    /// A key the game understands: `h l j k z space c p`.
    Key(char),
    NewGame,
    Tick,
    Quit,
}

pub struct App {
    core: cosmic::Core,
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
    banked: bool,
}

impl App {
    fn bank(&mut self) {
        if self.banked || self.game.pieces <= 1 {
            return;
        }
        if let Some(d) = &self.dir {
            bank(d, &mut self.game);
        }
        self.banked = true;
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
        let mut app = App { core, game: flags.game, dir: flags.dir, autopilot: flags.autopilot, banked: false };
        app.set_header_title("Glacier Blocks".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Glacier Blocks".into(), id),
            None => Task::none(),
        };
        (app, title)
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Key(c) => {
                if !self.autopilot {
                    self.game.key(c);
                } else if c == 'p' {
                    self.game.key(c);
                }
            }
            Message::NewGame => {
                self.bank();
                self.game.restart();
                self.banked = false;
            }
            Message::Tick => {
                if self.autopilot && self.game.state == State::Play {
                    for c in self.game.autopilot_keys().chars() {
                        self.game.key(c);
                    }
                }
                self.game.tick();
            }
            Message::Quit => {
                self.bank();
                return cosmic::iced::exit();
            }
        }
        if self.game.state == State::Over {
            self.bank();
        }
        Task::none()
    }

    fn on_app_exit(&mut self) -> Option<Message> {
        self.bank();
        None
    }

    fn header_start(&self) -> Vec<Element<'_, Message>> {
        let paused = self.game.state == State::Paused;
        vec![
            button::standard("New Game").on_press(Message::NewGame).into(),
            button::standard(if paused { "Resume" } else { "Pause" })
                .on_press_maybe((self.game.state != State::Over).then_some(Message::Key('p')))
                .into(),
        ]
    }

    fn view(&self) -> Element<'_, Message> {
        container(canvas::Canvas::new(Well { game: &self.game }).width(Length::Fill).height(Length::Fill))
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
            // Held keys repeat (the compositor's key repeat), which is what
            // makes a held arrow slide the piece along the floor.
            match key.as_ref() {
                Key::Named(Named::ArrowLeft) => Some(Message::Key('h')),
                Key::Named(Named::ArrowRight) => Some(Message::Key('l')),
                Key::Named(Named::ArrowDown) => Some(Message::Key('j')),
                Key::Named(Named::ArrowUp) => Some(Message::Key('k')),
                Key::Named(Named::Shift) => Some(Message::Key('c')),
                Key::Named(Named::Escape) | Key::Named(Named::F1) => Some(Message::Key('p')),
                Key::Named(Named::F2) => Some(Message::NewGame),
                Key::Character("n") if modifiers.control() => Some(Message::NewGame),
                Key::Character("q") if modifiers.control() => Some(Message::Quit),
                Key::Character(c) if !modifiers.control() => {
                    let c = c.chars().next()?.to_ascii_lowercase();
                    match c {
                        'a' => Some(Message::Key('h')),
                        'd' => Some(Message::Key('l')),
                        's' => Some(Message::Key('j')),
                        'w' | 'x' => Some(Message::Key('k')),
                        'h' | 'j' | 'k' | 'l' | 'z' | 'c' | 'p' | ' ' => Some(Message::Key(c)),
                        _ => None,
                    }
                }
                _ => None,
            }
        });
        let clock = if self.game.state == State::Play {
            cosmic::iced::time::every(Duration::from_millis(TICK_MS)).map(|_| Message::Tick)
        } else {
            Subscription::none()
        };
        Subscription::batch([keys, clock])
    }
}

// ---------------------------------------------------------------------------
// The well
// ---------------------------------------------------------------------------

struct Well<'a> {
    game: &'a Game,
}

/// The block colours by `piece_colour` index: 1 red, 2 green, 3 amber,
/// 4 blue, 5 violet, 6 ice cyan.
fn block_colour(i: usize) -> Color {
    let (r, g, b) = match i {
        1 => (0xe0, 0x4f, 0x5f),
        2 => (0x4c, 0xc2, 0x7a),
        3 => (0xf2, 0xb3, 0x3d),
        4 => (0x3d, 0x7f, 0xe0),
        5 => (0xa0, 0x6c, 0xe0),
        _ => (0x52, 0xd0, 0xe8),
    };
    Color::from_rgb8(r, g, b)
}

fn shade(c: Color, k: f32) -> Color {
    Color { r: (c.r * k).min(1.0), g: (c.g * k).min(1.0), b: (c.b * k).min(1.0), a: c.a }
}

/// One block: a body, a lit top-left bevel and a shaded bottom-right one, so
/// a stack reads as ice cubes, not as flat squares.
fn block(frame: &mut Frame<cosmic::Renderer>, at: Point, s: f32, colour: Color) {
    let inset = (s * 0.06).max(1.0);
    frame.fill_rectangle(at, Size::new(s, s), shade(colour, 0.72));
    let lit = Path::new(|b| {
        b.move_to(at);
        b.line_to(Point::new(at.x + s, at.y));
        b.line_to(Point::new(at.x + s - inset * 2.0, at.y + inset * 2.0));
        b.line_to(Point::new(at.x + inset * 2.0, at.y + s - inset * 2.0));
        b.line_to(Point::new(at.x, at.y + s));
        b.close();
    });
    frame.fill(&lit, shade(colour, 1.25));
    frame.fill_rectangle(
        Point::new(at.x + inset * 2.0, at.y + inset * 2.0),
        Size::new(s - inset * 4.0, s - inset * 4.0),
        colour,
    );
}

fn label(frame: &mut Frame<cosmic::Renderer>, s: &str, at: Point, px: f32, colour: Color, bold: bool) {
    frame.fill_text(Text {
        content: s.to_string(),
        position: at,
        color: colour,
        size: px.into(),
        font: cosmic::iced::Font {
            weight: if bold { cosmic::iced::font::Weight::Bold } else { cosmic::iced::font::Weight::Normal },
            ..Default::default()
        },
        align_x: Alignment::Start.into(),
        align_y: alignment::Vertical::Top,
        ..Text::default()
    });
}

/// A piece drawn on its own, centred in a box (the hold slot, the preview).
fn mini_piece(frame: &mut Frame<cosmic::Renderer>, kind: usize, centre: Point, s: f32, dim: bool) {
    let cells = shape(kind, 0);
    let (mut r0, mut r1, mut c0, mut c1) = (i32::MAX, i32::MIN, i32::MAX, i32::MIN);
    for (r, c) in cells {
        r0 = r0.min(r);
        r1 = r1.max(r);
        c0 = c0.min(c);
        c1 = c1.max(c);
    }
    let (w, h) = ((c1 - c0 + 1) as f32 * s, (r1 - r0 + 1) as f32 * s);
    let mut colour = block_colour(piece_colour(kind));
    if dim {
        colour = Color { a: 0.4, ..colour };
    }
    for (r, c) in cells {
        let at = Point::new(centre.x - w / 2.0 + (c - c0) as f32 * s, centre.y - h / 2.0 + (r - r0) as f32 * s);
        block(frame, at, s, colour);
    }
}

impl canvas::Program<Message, cosmic::Theme, cosmic::Renderer> for Well<'_> {
    type State = ();

    fn draw(
        &self,
        _state: &(),
        renderer: &cosmic::Renderer,
        theme: &cosmic::Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<Geometry<cosmic::Renderer>> {
        let g = self.game;
        let mut frame = Frame::new(renderer, bounds.size());
        let dark = theme.cosmic().is_dark;
        let page = if dark { Color::from_rgb8(0x0e, 0x17, 0x22) } else { Color::from_rgb8(0xe6, 0xee, 0xf5) };
        let ink = if dark { Color::from_rgb8(0xe8, 0xf0, 0xf8) } else { Color::from_rgb8(0x1d, 0x2b, 0x3a) };
        let soft = Color { a: 0.65, ..ink };
        frame.fill_rectangle(Point::ORIGIN, bounds.size(), page);

        // The well is 10 x 20 cells with two cells of headroom above it for
        // the spawning piece; side panels are 5 cells each.
        let s = ((bounds.height - 24.0) / (ROWS as f32 + 2.5))
            .min((bounds.width - 24.0) / (COLS as f32 + 11.0))
            .floor()
            .max(8.0);
        let well_w = COLS as f32 * s;
        let well_h = ROWS as f32 * s;
        let left = (bounds.width - well_w) / 2.0;
        let top = (bounds.height - well_h) / 2.0 + s * 0.75;
        let well = Rectangle::new(Point::new(left, top), Size::new(well_w, well_h));

        // The well: deep ice, a faint grid, a frame.
        frame.fill_rectangle(well.position(), well.size(), Color::from_rgb8(0x12, 0x24, 0x38));
        let grid = Path::new(|b| {
            for c in 1..COLS {
                b.move_to(Point::new(left + c as f32 * s, top));
                b.line_to(Point::new(left + c as f32 * s, top + well_h));
            }
            for r in 1..ROWS {
                b.move_to(Point::new(left, top + r as f32 * s));
                b.line_to(Point::new(left + well_w, top + r as f32 * s));
            }
        });
        frame.stroke(&grid, Stroke::default().with_color(Color::from_rgba(1.0, 1.0, 1.0, 0.05)).with_width(1.0));

        let cell_at = |r: i32, c: i32| Point::new(left + c as f32 * s, top + (r - HIDDEN_ROWS as i32) as f32 * s);

        // The stack.
        for r in HIDDEN_ROWS..BOARD_ROWS {
            for c in 0..COLS {
                if g.board[r][c] {
                    block(&mut frame, cell_at(r as i32, c as i32), s, block_colour(g.colour[r][c] as usize));
                }
            }
        }

        if g.state != State::Over {
            let colour = block_colour(piece_colour(g.kind));
            // The ghost: where a hard drop lands.
            let land = landing_row(&g.board, g.kind, g.rot, g.row, g.col);
            for (r, c) in cells(g.kind, g.rot, land, g.col) {
                if r >= HIDDEN_ROWS as i32 {
                    let p = cell_at(r, c);
                    let ghost = Path::rectangle(Point::new(p.x + 1.5, p.y + 1.5), Size::new(s - 3.0, s - 3.0));
                    frame.fill(&ghost, Color { a: 0.12, ..colour });
                    frame.stroke(&ghost, Stroke::default().with_color(Color { a: 0.55, ..colour }).with_width(1.5));
                }
            }
            // The piece, including the part still above the rim while it spawns.
            for (r, c) in g.cur_cells() {
                if r >= HIDDEN_ROWS as i32 - 2 {
                    block(&mut frame, cell_at(r, c), s, colour);
                }
            }
        }
        frame.stroke(
            &Path::rectangle(Point::new(left - 1.5, top - 1.5), Size::new(well_w + 3.0, well_h + 3.0)),
            Stroke::default().with_color(Color::from_rgb8(0x7f, 0xb2, 0xdd)).with_width(3.0),
        );

        // Left: hold. Right: next, then the numbers.
        let panel = s * 4.4;
        let lx = left - s * 0.8 - panel;
        let rx = left + well_w + s * 0.8;
        label(&mut frame, "HOLD", Point::new(lx, top), s * 0.6, soft, true);
        let hold_box = Rectangle::new(Point::new(lx, top + s), Size::new(panel, s * 3.2));
        frame.fill(&Path::rounded_rectangle(hold_box.position(), hold_box.size(), (s * 0.3).into()), Color::from_rgba(0.0, 0.0, 0.0, 0.12));
        if let Some(k) = g.held {
            mini_piece(&mut frame, k, hold_box.center(), s * 0.8, !g.can_hold);
        }
        label(&mut frame, "NEXT", Point::new(rx, top), s * 0.6, soft, true);
        let next_box = Rectangle::new(Point::new(rx, top + s), Size::new(panel, s * 8.4));
        frame.fill(&Path::rounded_rectangle(next_box.position(), next_box.size(), (s * 0.3).into()), Color::from_rgba(0.0, 0.0, 0.0, 0.12));
        for (i, k) in g.preview().into_iter().enumerate() {
            let scale = if i == 0 { 0.8 } else { 0.62 };
            mini_piece(&mut frame, k, Point::new(next_box.center().x, top + s * (2.6 + i as f32 * 2.7)), s * scale, false);
        }
        let mut y = top + s * 10.2;
        for (name, value) in [
            ("SCORE", g.score.to_string()),
            ("LINES", g.lines.to_string()),
            ("LEVEL", g.level.to_string()),
            ("BEST", g.best_score.max(g.score).to_string()),
        ] {
            label(&mut frame, name, Point::new(rx, y), s * 0.5, soft, true);
            label(&mut frame, &value, Point::new(rx, y + s * 0.6), s * 0.95, ink, true);
            y += s * 2.1;
        }
        let keys = "← → move\n↑ / X rotate\nZ rotate back\n↓ soft drop\nSpace hard drop\nShift / C hold\nEsc pause";
        label(&mut frame, keys, Point::new(lx, top + s * 5.0), s * 0.5, soft, false);

        // Pause and game over cover the well.
        let banner = match g.state {
            State::Paused => Some(("Paused", "Esc to resume")),
            State::Over => Some(("Game over", "New Game: Ctrl+N")),
            State::Play => None,
        };
        if let Some((title, hint)) = banner {
            frame.fill_rectangle(well.position(), well.size(), Color::from_rgba(0.04, 0.08, 0.13, 0.72));
            for (line, dy, px) in [(title, -s * 0.8, s * 1.3), (hint, s * 0.9, s * 0.6)] {
                frame.fill_text(Text {
                    content: line.to_string(),
                    position: Point::new(well.center_x(), well.center_y() + dy),
                    color: Color::WHITE,
                    size: px.into(),
                    font: cosmic::iced::Font { weight: cosmic::iced::font::Weight::Bold, ..Default::default() },
                    align_x: Alignment::Center.into(),
                    align_y: alignment::Vertical::Center,
                    ..Text::default()
                });
            }
        }
        vec![frame.into_geometry()]
    }
}
