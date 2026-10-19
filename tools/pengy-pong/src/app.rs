//! The Penguin Pong window (libcosmic).
//!
//! One canvas: an ice rink, a puck, two paddles. W/S move the left paddle (the
//! arrow keys too, in one-player); in two-player the arrow keys move the
//! right one. Keys are held, not tapped: a held key moves its paddle a row a
//! tick, the way a joystick did. The game steps on its own tick
//! (`pengy_pong::TICK_MS`); the puck and the paddles are eased between the
//! last two ticks, so the motion is smooth at any frame rate.

use std::collections::HashSet;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use cosmic::iced::keyboard::{self, key::Named, Key};
use cosmic::iced::widget::canvas::{self, Frame, Geometry, Path, Stroke, Text};
use cosmic::iced::{alignment, mouse, Alignment, Color, Length, Point, Rectangle, Size, Subscription};
use cosmic::prelude::*;
use cosmic::widget::{button, container, nav_bar};

use crate::{Game, Mode, Side, State, COLS, FP_SCALE, LEFT_COL, PADDLE_H, RIGHT_COL, ROWS, TICK_MS};

use super::{bank, Args, Best};

pub const APP_ID: &str = "io.catbee.pengyos.PenguinPong";

const TICK: Duration = Duration::from_millis(TICK_MS);

pub fn run(args: &Args, dir: PathBuf) -> i32 {
    let mut game = Game::with(args.seed, args.mode, args.difficulty);
    let dir = (!args.no_save).then_some(dir);
    if let Some(d) = &dir {
        let b = Best::load(d);
        game.best_wins = b.wins;
        game.best_margin = b.margin;
        game.best_rally = b.rally;
    }
    let flags = Flags { game, dir, autopilot: args.autopilot };
    let settings = cosmic::app::Settings::default()
        .size(Size::new(1000.0, 480.0))
        .size_limits(cosmic::iced::Limits::NONE.min_width(480.0).min_height(260.0));
    match cosmic::app::run::<App>(settings, flags) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("pengy-pong: {err}");
            1
        }
    }
}

pub struct Flags {
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
}

/// A paddle control: which paddle, which way.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Pad {
    LeftUp,
    LeftDown,
    ArrowUp,
    ArrowDown,
}

#[derive(Clone, Debug)]
pub enum Message {
    Press(Pad),
    Release(Pad),
    /// A game key: space serves, `p` pauses, `r` restarts, `m` and `1 2 3`.
    Key(char),
    Frame(Instant),
    Quit,
}

/// Where the puck and the paddles were one tick ago.
#[derive(Clone, Copy, Default)]
struct Pose {
    x: f32,
    y: f32,
    left: f32,
    right: f32,
}

impl Pose {
    fn of(g: &Game) -> Pose {
        Pose {
            x: g.ball_x as f32 / FP_SCALE as f32,
            y: g.ball_y as f32 / FP_SCALE as f32,
            left: g.left_top as f32,
            right: g.right_top as f32,
        }
    }
}

pub struct App {
    core: cosmic::Core,
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
    banked: bool,
    held: HashSet<Pad>,
    prev: Pose,
    last_tick: Option<Instant>,
    alpha: f32,
}

impl App {
    fn bank(&mut self) {
        if self.banked || self.game.points == 0 {
            return;
        }
        if let Some(d) = &self.dir {
            bank(d, &mut self.game);
        }
        self.banked = true;
    }

    /// The paddle a control moves in the current mode, and which way.
    fn steer(&self, pad: Pad) -> (Side, i32) {
        let arrows = if self.game.mode == Mode::TwoP { Side::Right } else { Side::Left };
        match pad {
            Pad::LeftUp => (Side::Left, -1),
            Pad::LeftDown => (Side::Left, 1),
            Pad::ArrowUp => (arrows, -1),
            Pad::ArrowDown => (arrows, 1),
        }
    }

    fn step(&mut self) {
        if self.autopilot && self.game.state == State::Play {
            for k in self.game.autopilot_keys().chars() {
                self.game.key(k);
            }
        }
        // Held keys move their paddle a row this tick, before the puck moves,
        // so the paddle's motion counts as spin on a hit this tick.
        if matches!(self.game.state, State::Play | State::Serve) {
            let moves: Vec<(Side, i32)> = self.held.iter().map(|p| self.steer(*p)).collect();
            for (side, d) in moves {
                self.game.set_top(side, d);
            }
        }
        self.prev = Pose::of(&self.game);
        self.game.tick();
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
        let prev = Pose::of(&flags.game);
        let mut app = App {
            core,
            game: flags.game,
            dir: flags.dir,
            autopilot: flags.autopilot,
            banked: false,
            held: HashSet::new(),
            prev,
            last_tick: None,
            alpha: 0.0,
        };
        app.set_header_title("Penguin Pong".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Penguin Pong".into(), id),
            None => Task::none(),
        };
        (app, title)
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Press(p) => {
                if self.held.insert(p) && self.game.state != State::Over && self.game.state != State::Pause {
                    // The first row moves at once: a tap is a nudge.
                    let (side, d) = self.steer(p);
                    self.game.set_top(side, d);
                }
            }
            Message::Release(p) => {
                self.held.remove(&p);
            }
            Message::Key(c) => {
                if c == 'r' {
                    self.bank();
                    self.banked = false;
                }
                self.game.key(c);
                self.prev = Pose::of(&self.game);
            }
            Message::Frame(now) => {
                let mut last = *self.last_tick.get_or_insert(now);
                let mut steps = 0;
                while now.duration_since(last) >= TICK && steps < 4 {
                    self.step();
                    last += TICK;
                    steps += 1;
                }
                if now.duration_since(last) >= TICK {
                    last = now;
                }
                self.last_tick = Some(last);
                self.alpha = (now.duration_since(last).as_secs_f32() / TICK.as_secs_f32()).min(1.0);
                if self.game.state == State::Over {
                    self.bank();
                }
            }
            Message::Quit => {
                self.bank();
                return cosmic::iced::exit();
            }
        }
        if self.game.state != State::Play && self.game.state != State::Serve {
            self.last_tick = None;
        }
        Task::none()
    }

    fn on_app_exit(&mut self) -> Option<Message> {
        self.bank();
        None
    }

    fn header_start(&self) -> Vec<Element<'_, Message>> {
        let paused = self.game.state == State::Pause;
        vec![
            button::standard("New Match").on_press(Message::Key('r')).into(),
            button::standard(if paused { "Resume" } else { "Pause" })
                .on_press_maybe(matches!(self.game.state, State::Play | State::Pause).then_some(Message::Key('p')))
                .into(),
        ]
    }

    fn header_end(&self) -> Vec<Element<'_, Message>> {
        let g = &self.game;
        let pick = |label: &'static str, on: bool, key: char| -> Element<'_, Message> {
            if on {
                button::suggested(label).on_press(Message::Key(key)).into()
            } else {
                button::standard(label).on_press(Message::Key(key)).into()
            }
        };
        let one = g.mode == Mode::OneP;
        vec![
            pick("Easy", one && g.difficulty == 1, '1'),
            pick("Medium", one && g.difficulty == 2, '2'),
            pick("Hard", one && g.difficulty == 3, '3'),
            pick("Two Players", !one, 'm'),
        ]
    }

    fn view(&self) -> Element<'_, Message> {
        let rink = Rink { game: &self.game, prev: self.prev, alpha: self.alpha };
        container(canvas::Canvas::new(rink).width(Length::Fill).height(Length::Fill))
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        fn pad(key: &Key) -> Option<Pad> {
            match key.as_ref() {
                Key::Named(Named::ArrowUp) => Some(Pad::ArrowUp),
                Key::Named(Named::ArrowDown) => Some(Pad::ArrowDown),
                Key::Character(c) => match c.to_ascii_lowercase().as_str() {
                    "w" => Some(Pad::LeftUp),
                    "s" => Some(Pad::LeftDown),
                    _ => None,
                },
                _ => None,
            }
        }
        let keys = cosmic::iced::event::listen_with(|event, status, _| {
            if status == cosmic::iced::event::Status::Captured {
                return None;
            }
            match event {
                cosmic::iced::Event::Keyboard(keyboard::Event::KeyReleased { key, .. }) => pad(&key).map(Message::Release),
                cosmic::iced::Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, repeat, .. }) => {
                    if let Some(p) = pad(&key) {
                        return (!repeat).then_some(Message::Press(p));
                    }
                    if repeat {
                        return None;
                    }
                    match key.as_ref() {
                        Key::Named(Named::Escape) => Some(Message::Key('p')),
                        Key::Named(Named::F2) => Some(Message::Key('r')),
                        Key::Character("n") if modifiers.control() => Some(Message::Key('r')),
                        Key::Character("q") if modifiers.control() => Some(Message::Quit),
                        Key::Character(c) if !modifiers.control() => {
                            let c = c.chars().next()?.to_ascii_lowercase();
                            " p123".contains(c).then_some(Message::Key(c))
                        }
                        _ => None,
                    }
                }
                _ => None,
            }
        });
        let frames = if matches!(self.game.state, State::Play | State::Serve) {
            cosmic::iced::window::frames().map(|(_, at)| Message::Frame(at))
        } else {
            Subscription::none()
        };
        Subscription::batch([keys, frames])
    }
}

// ---------------------------------------------------------------------------
// The rink
// ---------------------------------------------------------------------------

struct Rink<'a> {
    game: &'a Game,
    prev: Pose,
    alpha: f32,
}

fn label(frame: &mut Frame<cosmic::Renderer>, s: String, at: Point, px: f32, colour: Color) {
    frame.fill_text(Text {
        content: s,
        position: at,
        color: colour,
        size: px.into(),
        font: cosmic::iced::Font { weight: cosmic::iced::font::Weight::Bold, ..Default::default() },
        align_x: Alignment::Center.into(),
        align_y: alignment::Vertical::Center,
        ..Text::default()
    });
}

impl canvas::Program<Message, cosmic::Theme, cosmic::Renderer> for Rink<'_> {
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
        frame.fill_rectangle(
            Point::ORIGIN,
            bounds.size(),
            if dark { Color::from_rgb8(0x0e, 0x17, 0x22) } else { Color::from_rgb8(0xd9, 0xe4, 0xee) },
        );
        let cell = ((bounds.width - 16.0) / COLS as f32).min((bounds.height - 16.0) / ROWS as f32);
        let (w, h) = (cell * COLS as f32, cell * ROWS as f32);
        let ox = (bounds.width - w) / 2.0;
        let oy = (bounds.height - h) / 2.0;
        let at = |x: f32, y: f32| Point::new(ox + x * cell, oy + y * cell);

        // The ice: boards, a centre line and circle, blue lines.
        let rink = Path::rounded_rectangle(at(0.0, 0.0), Size::new(w, h), (cell * 2.5).into());
        frame.fill(&rink, Color::from_rgb8(0xf2, 0xf8, 0xfc));
        let red = Color::from_rgba8(0xd6, 0x3a, 0x45, 0.55);
        let blue = Color::from_rgba8(0x3a, 0x6e, 0xc8, 0.45);
        frame.fill_rectangle(at(COLS as f32 / 2.0 - 0.15, 0.0), Size::new(cell * 0.3, h), red);
        frame.stroke(&Path::circle(at(COLS as f32 / 2.0, ROWS as f32 / 2.0), cell * 3.5), Stroke::default().with_color(blue).with_width(cell * 0.2));
        for x in [COLS as f32 * 0.3, COLS as f32 * 0.7] {
            frame.fill_rectangle(at(x - 0.25, 0.0), Size::new(cell * 0.5, h), blue);
        }
        frame.stroke(&rink, Stroke::default().with_color(Color::from_rgb8(0x6b, 0x8a, 0xa8)).with_width(cell * 0.35));

        // The score, under everything that moves.
        let ink = Color::from_rgba8(0x1d, 0x2b, 0x3a, 0.28);
        label(&mut frame, g.left_score.to_string(), at(COLS as f32 / 2.0 - 6.0, 3.5), cell * 4.0, ink);
        label(&mut frame, g.right_score.to_string(), at(COLS as f32 / 2.0 + 6.0, 3.5), cell * 4.0, ink);

        // The paddles: the player in blue, the penguin (or player two) in navy.
        let a = self.alpha;
        let ease = |from: f32, to: f32| if (to - from).abs() > 3.0 { to } else { from + (to - from) * a };
        for (col, top, colour) in [
            (LEFT_COL, ease(self.prev.left, g.left_top as f32), Color::from_rgb8(0x2f, 0x7f, 0xd8)),
            (RIGHT_COL, ease(self.prev.right, g.right_top as f32), Color::from_rgb8(0x1b, 0x24, 0x33)),
        ] {
            let p = Path::rounded_rectangle(at(col as f32 + 0.1, top), Size::new(cell * 0.8, cell * PADDLE_H as f32), (cell * 0.4).into());
            frame.fill(&p, colour);
        }

        // The puck, eased between ticks unless it was reset for a serve.
        let (px, py) = (g.ball_x as f32 / FP_SCALE as f32, g.ball_y as f32 / FP_SCALE as f32);
        let (bx, by) = if (px - self.prev.x).abs() > 3.0 || (py - self.prev.y).abs() > 3.0 {
            (px, py)
        } else {
            (self.prev.x + (px - self.prev.x) * a, self.prev.y + (py - self.prev.y) * a)
        };
        if g.state != State::Over {
            let c = at(bx + 0.5, by + 0.5);
            frame.fill(&Path::circle(Point::new(c.x + cell * 0.12, c.y + cell * 0.18), cell * 0.55), Color::from_rgba(0.0, 0.0, 0.0, 0.18));
            frame.fill(&Path::circle(c, cell * 0.55), Color::from_rgb8(0x17, 0x1c, 0x24));
            frame.stroke(&Path::circle(c, cell * 0.4), Stroke::default().with_color(Color::from_rgba(1.0, 1.0, 1.0, 0.15)).with_width(1.0));
        }

        let who = |s: Side| match (s, g.mode) {
            (Side::Left, Mode::OneP) => "You",
            (Side::Right, Mode::OneP) => "The penguin",
            (Side::Left, Mode::TwoP) => "Left",
            (Side::Right, Mode::TwoP) => "Right",
        };
        let banner = match g.state {
            State::Over => {
                let title = match g.winner() {
                    Some(Side::Left) if g.mode == Mode::OneP => "You win!".to_string(),
                    Some(s) => format!("{} wins", who(s)),
                    None => "A draw".to_string(),
                };
                Some((title, format!("{}–{} · New Match: Ctrl+N", g.left_score, g.right_score)))
            }
            State::Pause => Some(("Paused".to_string(), "Esc to resume".to_string())),
            State::Serve if g.points == 0 && g.ticks < 2 => Some((
                "Penguin Pong".to_string(),
                if g.mode == Mode::OneP { "W/S or ↑/↓ to move · first to 11".to_string() } else { "Left: W/S · Right: ↑/↓ · first to 11".to_string() },
            )),
            _ => None,
        };
        if let Some((title, hint)) = banner {
            let bw = (w * 0.5).max(cell * 22.0).min(w);
            let card = Path::rounded_rectangle(at(COLS as f32 / 2.0 - bw / cell / 2.0, ROWS as f32 / 2.0 - 2.6), Size::new(bw, cell * 5.2), (cell).into());
            frame.fill(&card, Color::from_rgba(0.04, 0.08, 0.14, 0.84));
            label(&mut frame, title, at(COLS as f32 / 2.0, ROWS as f32 / 2.0 - 0.9), cell * 1.7, Color::WHITE);
            label(&mut frame, hint, at(COLS as f32 / 2.0, ROWS as f32 / 2.0 + 1.3), cell * 0.85, Color::from_rgba(1.0, 1.0, 1.0, 0.8));
        }
        vec![frame.into_geometry()]
    }
}
