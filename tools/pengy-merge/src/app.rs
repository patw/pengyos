//! The Icefloe 2048 window (libcosmic).
//!
//! One canvas: the field of floes, coloured from pale frost (2) to deep sea
//! ice (2048 and up). The arrow keys (or WASD, or hjkl) slide; a floe that
//! just fused or landed glows for a moment. Every rule is `pengy_merge::Game`'s.

use std::path::PathBuf;
use std::time::Duration;

use cosmic::iced::keyboard::{self, key::Named, Key};
use cosmic::iced::widget::canvas::{self, Frame, Geometry, Path, Stroke, Text};
use cosmic::iced::{alignment, mouse, Alignment, Color, Length, Point, Rectangle, Size, Subscription};
use cosmic::prelude::*;
use cosmic::widget::{self, button, container, nav_bar, row, text};

use crate::{Game, State, FLASH_TICKS, TICK_MS};

use super::{Args, Best};

pub const APP_ID: &str = "io.catbee.pengyos.Icefloe";

pub fn run(args: &Args, dir: PathBuf) -> i32 {
    let mut game = Game::with(args.seed, args.size, args.goal);
    let dir = (!args.no_save).then_some(dir);
    if let Some(d) = &dir {
        game.best = Best::load(d).score;
    }
    let flags = Flags { game, dir, autopilot: args.autopilot };
    let settings = cosmic::app::Settings::default()
        .size(Size::new(560.0, 680.0))
        .size_limits(cosmic::iced::Limits::NONE.min_width(360.0).min_height(420.0));
    match cosmic::app::run::<App>(settings, flags) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("pengy-merge: {err}");
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
    /// A key the game understands (`h j k l`, `u`, `c`).
    Key(char),
    NewGame,
    Undo,
    KeepGoing,
    Tick,
    Quit,
}

pub struct App {
    core: cosmic::Core,
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
    /// This run is in the record already.
    banked: bool,
}

impl App {
    fn bank(&mut self) {
        if self.banked || self.game.moves == 0 {
            return;
        }
        if let Some(d) = &self.dir {
            let b = Best::save(d, &self.game);
            self.game.best = b.score;
        }
        self.banked = true;
    }

    /// The record is written when the run ends; an undo out of the end puts
    /// the run back in play, and it is banked again only when it ends again.
    fn settle(&mut self) {
        match self.game.state {
            State::Over => self.bank(),
            _ => self.banked = false,
        }
        if self.game.score > self.game.best {
            self.game.best = self.game.score;
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
        let mut app = App { core, game: flags.game, dir: flags.dir, autopilot: flags.autopilot, banked: false };
        app.set_header_title("Icefloe 2048".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Icefloe 2048".into(), id),
            None => Task::none(),
        };
        (app, title)
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Key(c) => {
                self.game.key(c);
            }
            Message::NewGame => {
                self.bank();
                self.game.restart();
                self.banked = false;
            }
            Message::Undo => {
                self.game.undo();
            }
            Message::KeepGoing => {
                self.game.key('c');
            }
            Message::Tick => {
                if self.autopilot && self.game.state == State::Playing {
                    if let Some(k) = self.game.autopilot_key() {
                        self.game.key(k);
                    }
                }
                self.game.tick();
            }
            Message::Quit => {
                self.bank();
                return cosmic::iced::exit();
            }
        }
        self.settle();
        Task::none()
    }

    fn on_app_exit(&mut self) -> Option<Message> {
        self.bank();
        None
    }

    fn header_start(&self) -> Vec<Element<'_, Message>> {
        vec![
            button::standard("New Game").on_press(Message::NewGame).into(),
            button::standard("Undo")
                .on_press_maybe(self.game.history.is_some().then_some(Message::Undo))
                .into(),
        ]
    }

    fn view(&self) -> Element<'_, Message> {
        let g = &self.game;
        let spacing = cosmic::theme::spacing();
        let bar = row::with_children([
            text::body(format!("Score {}", g.score)).into(),
            text::body(format!("Best {}", g.best.max(g.score))).into(),
            text::body(format!("Moves {}", g.moves)).into(),
            widget::space::horizontal().into(),
            text::caption("Arrow keys slide · Ctrl+Z undo").into(),
        ])
        .spacing(spacing.space_m)
        .align_y(Alignment::Center)
        .padding([spacing.space_xxs, spacing.space_s]);
        let mut body: Vec<Element<'_, Message>> =
            vec![canvas::Canvas::new(Field { game: g }).width(Length::Fill).height(Length::Fill).into()];
        if g.state == State::Won {
            body.push(
                row::with_children([
                    widget::space::horizontal().into(),
                    button::suggested("Keep Going").on_press(Message::KeepGoing).into(),
                    button::standard("New Game").on_press(Message::NewGame).into(),
                    widget::space::horizontal().into(),
                ])
                .spacing(spacing.space_s)
                .padding(spacing.space_xs)
                .into(),
            );
        }
        body.push(bar.into());
        container(widget::column::with_children(body)).width(Length::Fill).height(Length::Fill).into()
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
                Key::Named(Named::ArrowLeft) => Some(Message::Key('h')),
                Key::Named(Named::ArrowDown) => Some(Message::Key('j')),
                Key::Named(Named::ArrowUp) => Some(Message::Key('k')),
                Key::Named(Named::ArrowRight) => Some(Message::Key('l')),
                Key::Character("z") if modifiers.control() => Some(Message::Undo),
                Key::Character("n") if modifiers.control() => Some(Message::NewGame),
                Key::Character("q") if modifiers.control() => Some(Message::Quit),
                Key::Named(Named::F2) => Some(Message::NewGame),
                Key::Character(c) if !modifiers.control() => {
                    let c = c.chars().next()?.to_ascii_lowercase();
                    "hjklwasduc".contains(c).then_some(Message::Key(c))
                }
                _ => None,
            }
        });
        // The flash fades, and the demo plays, on the game's own tick.
        let busy = self.autopilot || self.game.flash.iter().any(|f| *f > 0);
        let clock = if busy {
            cosmic::iced::time::every(Duration::from_millis(TICK_MS)).map(|_| Message::Tick)
        } else {
            Subscription::none()
        };
        Subscription::batch([keys, clock])
    }
}

// ---------------------------------------------------------------------------
// The field
// ---------------------------------------------------------------------------

struct Field<'a> {
    game: &'a Game,
}

/// Floe colours, from 2 up: pale frost through glacier blue to deep sea ice,
/// then aurora for the floes past 2048.
fn floe_colour(value: u32) -> (Color, Color) {
    let k = value.max(2).trailing_zeros() as usize; // 2 -> 1, 2048 -> 11
    const FILL: [(u8, u8, u8); 14] = [
        (0xee, 0xf4, 0xfa), // 2
        (0xdc, 0xeb, 0xf7), // 4
        (0xb9, 0xdc, 0xf2), // 8
        (0x92, 0xcb, 0xec), // 16
        (0x6c, 0xb5, 0xe3), // 32
        (0x48, 0x9d, 0xd8), // 64
        (0x2f, 0x84, 0xc8), // 128
        (0x22, 0x6c, 0xb3), // 256
        (0x1b, 0x57, 0x9c), // 512
        (0x17, 0x45, 0x84), // 1024
        (0x14, 0x34, 0x6c), // 2048
        (0x5b, 0x3f, 0xa8), // 4096
        (0x2a, 0x9d, 0x8f), // 8192
        (0xc2, 0x4d, 0x8a), // 16384+
    ];
    let (r, g, b) = FILL[(k - 1).min(FILL.len() - 1)];
    let ink = if k <= 3 { Color::from_rgb8(0x1d, 0x3b, 0x58) } else { Color::WHITE };
    (Color::from_rgb8(r, g, b), ink)
}

impl canvas::Program<Message, cosmic::Theme, cosmic::Renderer> for Field<'_> {
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
        let n = g.size;
        let mut frame = Frame::new(renderer, bounds.size());
        let dark = theme.cosmic().is_dark;
        let page = if dark { Color::from_rgb8(0x10, 0x1a, 0x24) } else { Color::from_rgb8(0xe8, 0xef, 0xf5) };
        frame.fill_rectangle(Point::ORIGIN, bounds.size(), page);

        // The field: a square of sea with n x n wells for the floes.
        let side = (bounds.width.min(bounds.height) - 24.0).max(40.0);
        let origin = Point::new((bounds.width - side) / 2.0, (bounds.height - side) / 2.0);
        let gap = side * 0.11 / (n as f32 + 1.0);
        let cell = (side - gap * (n as f32 + 1.0)) / n as f32;
        let sea = Path::rounded_rectangle(origin, Size::new(side, side), (side * 0.03).into());
        frame.fill(&sea, Color::from_rgb8(0x1e, 0x3a, 0x52));

        for r in 0..n {
            for c in 0..n {
                let at = Point::new(
                    origin.x + gap + c as f32 * (cell + gap),
                    origin.y + gap + r as f32 * (cell + gap),
                );
                let well = Path::rounded_rectangle(at, Size::new(cell, cell), (cell * 0.08).into());
                frame.fill(&well, Color::from_rgba(1.0, 1.0, 1.0, 0.08));
                let v = g.at(r, c);
                if v == 0 {
                    continue;
                }
                let (fill, ink) = floe_colour(v);
                // A fresh fuse or spawn grows out of its well and glows.
                let glow = g.flash[r * n + c] as f32 / FLASH_TICKS as f32;
                let grow = cell * 0.05 * glow;
                let floe = Path::rounded_rectangle(
                    Point::new(at.x - grow, at.y - grow),
                    Size::new(cell + 2.0 * grow, cell + 2.0 * grow),
                    (cell * 0.08).into(),
                );
                frame.fill(&floe, fill);
                if glow > 0.0 {
                    frame.stroke(
                        &floe,
                        Stroke::default().with_color(Color::from_rgba(1.0, 1.0, 1.0, 0.8 * glow)).with_width(3.0),
                    );
                }
                let digits = v.to_string();
                let px = cell * match digits.len() {
                    1 | 2 => 0.44,
                    3 => 0.36,
                    4 => 0.28,
                    _ => 0.22,
                };
                frame.fill_text(Text {
                    content: digits,
                    position: Point::new(at.x + cell / 2.0, at.y + cell / 2.0),
                    color: ink,
                    size: px.into(),
                    font: cosmic::iced::Font { weight: cosmic::iced::font::Weight::Bold, ..Default::default() },
                    align_x: Alignment::Center.into(),
                    align_y: alignment::Vertical::Center,
                    ..Text::default()
                });
            }
        }

        let banner = match g.state {
            State::Won => Some((format!("{} reached!", g.goal), "Keep going, or start a new game")),
            State::Over => Some(("No moves left".to_string(), "Undo (Ctrl+Z), or a New Game (Ctrl+N)")),
            State::Playing => None,
        };
        if let Some((title, hint)) = banner {
            frame.fill(&sea, Color::from_rgba(0.06, 0.1, 0.16, 0.62));
            let c = Point::new(origin.x + side / 2.0, origin.y + side / 2.0);
            for (line, dy, px) in [(title, -18.0, side * 0.09), (hint.to_string(), side * 0.07, side * 0.04)] {
                frame.fill_text(Text {
                    content: line,
                    position: Point::new(c.x, c.y + dy),
                    color: Color::WHITE,
                    size: px.max(13.0).into(),
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
