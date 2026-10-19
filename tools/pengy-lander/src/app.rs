//! The Ice Lander window (libcosmic).
//!
//! One canvas: the night sky, the ridge, the pads (each marked with what it
//! pays), and the probe. Space (or the up arrow) fires the engine for as long
//! as it is held; left/right (or A/D) turn the probe. The game steps at its
//! own 30 Hz (`pengy_lander::TICK_MS`); the probe is eased between the last
//! two ticks, so it flies smoothly at any frame rate.

use std::collections::HashSet;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use cosmic::iced::keyboard::{self, key::Named, Key};
use cosmic::iced::widget::canvas::{self, Frame, Geometry, Path, Stroke, Text};
use cosmic::iced::{alignment, mouse, Alignment, Color, Length, Point, Radians, Rectangle, Size, Subscription, Vector};
use cosmic::prelude::*;
use cosmic::widget::{button, container, nav_bar};

use crate::{
    mps_text, Body, Game, State, COLS, FUEL_START, MAX_TILT_DEG, MAX_VX, MAX_VY, PSCALE, ROT_PER_TICK, TICK_MS,
    WORLD_H_M,
};

use super::{bank, mirror, Args, Best};

pub const APP_ID: &str = "io.catbee.pengyos.IceLander";

const TICK: Duration = Duration::from_millis(TICK_MS);

pub fn run(args: &Args, dir: PathBuf) -> i32 {
    let mut game = Game::with_body(args.seed, args.body);
    let dir = (!args.no_save).then_some(dir);
    if let Some(d) = &dir {
        mirror(&mut game, &Best::load(d));
    }
    let flags = Flags { game, dir, autopilot: args.autopilot };
    let settings = cosmic::app::Settings::default()
        .size(Size::new(1000.0, 560.0))
        .size_limits(cosmic::iced::Limits::NONE.min_width(480.0).min_height(300.0));
    match cosmic::app::run::<App>(settings, flags) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("pengy-lander: {err}");
            1
        }
    }
}

pub struct Flags {
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Hold {
    Thrust,
    Left,
    Right,
}

#[derive(Clone, Debug)]
pub enum Message {
    Press(Hold),
    Release(Hold),
    Key(char),
    Body(Body),
    Frame(Instant),
    Quit,
}

pub struct App {
    core: cosmic::Core,
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
    banked: bool,
    held: HashSet<Hold>,
    /// The probe one tick ago (x, y in metres, heading in degrees).
    prev: (f32, f32, f32),
    last_tick: Option<Instant>,
    alpha: f32,
    /// The engine fired on the last tick (the game clears its latch).
    burning: bool,
    /// The probe waits, hovering, for the first control of the wave.
    started: bool,
}

fn pose(g: &Game) -> (f32, f32, f32) {
    (g.x as f32 / PSCALE as f32, g.y as f32 / PSCALE as f32, g.heading as f32)
}

impl App {
    fn bank(&mut self) {
        if self.banked || self.game.ticks == 0 {
            return;
        }
        if let Some(d) = &self.dir {
            bank(d, &mut self.game);
        }
        self.banked = true;
    }

    fn step(&mut self) {
        let g = &mut self.game;
        if self.autopilot {
            for k in g.autopilot_keys().chars() {
                g.key(k);
            }
        } else {
            if self.held.contains(&Hold::Thrust) {
                g.key(' ');
            }
            if self.held.contains(&Hold::Left) {
                g.rotate(-ROT_PER_TICK);
            }
            if self.held.contains(&Hold::Right) {
                g.rotate(ROT_PER_TICK);
            }
        }
        self.burning = g.engine_on();
        self.prev = pose(g);
        g.tick();
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
        let prev = pose(&flags.game);
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
            burning: false,
            started: flags.autopilot,
        };
        app.set_header_title("Ice Lander".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Ice Lander".into(), id),
            None => Task::none(),
        };
        (app, title)
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Press(h) => {
                self.held.insert(h);
                if self.game.state == State::Flying {
                    self.started = true;
                }
            }
            Message::Release(h) => {
                self.held.remove(&h);
            }
            Message::Key(c) => {
                let crashed = self.game.state == State::Crashed;
                if c == 'r' || (c == 'n' && crashed) {
                    self.bank();
                    self.banked = false;
                }
                self.game.key(c);
                self.prev = pose(&self.game);
                self.burning = false;
                // A new run or a new wave waits for its first control.
                self.started = self.autopilot;
            }
            Message::Body(b) => {
                if b != self.game.body {
                    self.bank();
                    self.banked = false;
                    let (bs, bw, bseed) = (self.game.best_score, self.game.best_waves, self.game.best_seed);
                    self.game = Game::with_body(self.game.seed.wrapping_add(1), b);
                    (self.game.best_score, self.game.best_waves, self.game.best_seed) = (bs, bw, bseed);
                    self.prev = pose(&self.game);
                    self.started = self.autopilot;
                }
            }
            Message::Frame(now) => {
                let mut last = *self.last_tick.get_or_insert(now);
                let mut steps = 0;
                while now.duration_since(last) >= TICK && steps < 4 {
                    self.step();
                    last += TICK;
                    steps += 1;
                    if self.game.state != State::Flying {
                        break;
                    }
                }
                if now.duration_since(last) >= TICK {
                    last = now;
                }
                self.last_tick = Some(last);
                self.alpha = (now.duration_since(last).as_secs_f32() / TICK.as_secs_f32()).min(1.0);
                if self.game.state == State::Crashed {
                    self.bank();
                }
                if self.autopilot && self.game.state == State::Landed {
                    self.game.key('n');
                    self.prev = pose(&self.game);
                }
            }
            Message::Quit => {
                self.bank();
                return cosmic::iced::exit();
            }
        }
        if self.game.state != State::Flying {
            self.last_tick = None;
            self.alpha = 1.0;
            self.burning = false;
        }
        Task::none()
    }

    fn on_app_exit(&mut self) -> Option<Message> {
        self.bank();
        None
    }

    fn header_start(&self) -> Vec<Element<'_, Message>> {
        let next = if self.game.state == State::Landed { "Next Wave" } else { "New Terrain" };
        vec![
            button::standard("New Game").on_press(Message::Key('r')).into(),
            button::standard(next).on_press(Message::Key('n')).into(),
        ]
    }

    fn header_end(&self) -> Vec<Element<'_, Message>> {
        let pick = |label: &'static str, b: Body| -> Element<'_, Message> {
            if self.game.body == b {
                button::suggested(label).on_press(Message::Body(b)).into()
            } else {
                button::standard(label).on_press(Message::Body(b)).into()
            }
        };
        vec![pick("Moon", Body::Moon), pick("Asteroid", Body::Asteroid)]
    }

    fn view(&self) -> Element<'_, Message> {
        let sky = Sky { game: &self.game, prev: self.prev, alpha: self.alpha, burning: self.burning, waiting: !self.started };
        container(canvas::Canvas::new(sky).width(Length::Fill).height(Length::Fill))
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        fn hold(key: &Key) -> Option<Hold> {
            match key.as_ref() {
                Key::Named(Named::ArrowUp) => Some(Hold::Thrust),
                Key::Named(Named::ArrowLeft) => Some(Hold::Left),
                Key::Named(Named::ArrowRight) => Some(Hold::Right),
                Key::Character(c) => match c.to_ascii_lowercase().as_str() {
                    " " | "w" => Some(Hold::Thrust),
                    "a" => Some(Hold::Left),
                    "d" => Some(Hold::Right),
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
                cosmic::iced::Event::Keyboard(keyboard::Event::KeyReleased { key, .. }) => hold(&key).map(Message::Release),
                cosmic::iced::Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, repeat, .. }) => {
                    if repeat {
                        return None;
                    }
                    if let Some(h) = hold(&key) {
                        return Some(Message::Press(h));
                    }
                    match key.as_ref() {
                        Key::Named(Named::Enter) => Some(Message::Key('n')),
                        Key::Named(Named::F2) => Some(Message::Key('r')),
                        Key::Character("n") if modifiers.control() => Some(Message::Key('r')),
                        Key::Character("q") if modifiers.control() => Some(Message::Quit),
                        Key::Character("n") => Some(Message::Key('n')),
                        _ => None,
                    }
                }
                _ => None,
            }
        });
        let frames = if (self.started && self.game.state == State::Flying) || (self.autopilot && self.game.state == State::Landed) {
            cosmic::iced::window::frames().map(|(_, at)| Message::Frame(at))
        } else {
            Subscription::none()
        };
        Subscription::batch([keys, frames])
    }
}

// ---------------------------------------------------------------------------
// The sky
// ---------------------------------------------------------------------------

struct Sky<'a> {
    game: &'a Game,
    prev: (f32, f32, f32),
    alpha: f32,
    burning: bool,
    /// The wave has not started: a card says how to fly.
    waiting: bool,
}

fn label(frame: &mut Frame<cosmic::Renderer>, s: String, at: Point, px: f32, colour: Color, align: Alignment) {
    frame.fill_text(Text {
        content: s,
        position: at,
        color: colour,
        size: px.into(),
        font: cosmic::iced::Font { weight: cosmic::iced::font::Weight::Bold, ..Default::default() },
        align_x: align.into(),
        align_y: alignment::Vertical::Center,
        ..Text::default()
    });
}

const GOOD: Color = Color { r: 0.45, g: 0.9, b: 0.55, a: 1.0 };
const BAD: Color = Color { r: 1.0, g: 0.45, b: 0.42, a: 1.0 };

impl canvas::Program<Message, cosmic::Theme, cosmic::Renderer> for Sky<'_> {
    type State = ();

    fn draw(
        &self,
        _state: &(),
        renderer: &cosmic::Renderer,
        _theme: &cosmic::Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<Geometry<cosmic::Renderer>> {
        let g = self.game;
        let mut frame = Frame::new(renderer, bounds.size());
        frame.fill_rectangle(Point::ORIGIN, bounds.size(), Color::from_rgb8(0x04, 0x07, 0x12));
        // The world: 60 m across, 24 m up, a metre a unit, y up.
        let m = (bounds.width / COLS as f32).min(bounds.height / WORLD_H_M as f32);
        let (w, h) = (m * COLS as f32, m * WORLD_H_M as f32);
        let ox = (bounds.width - w) / 2.0;
        let oy = (bounds.height - h) / 2.0;
        let at = |x: f32, y: f32| Point::new(ox + x * m, oy + h - y * m);

        for i in 0..90u32 {
            let x = (i.wrapping_mul(2_654_435_761) % 1000) as f32 / 1000.0;
            let y = (i.wrapping_mul(40_503) % 1000) as f32 / 1000.0;
            let r = if i % 7 == 0 { 1.6 } else { 1.0 };
            frame.fill(&Path::circle(Point::new(ox + x * w, oy + y * h), r), Color::from_rgba(1.0, 1.0, 1.0, 0.25 + 0.5 * ((i % 5) as f32 / 5.0)));
        }

        // The ridge: a line through the column centres, filled to the floor.
        let t = &g.terrain;
        let (rock, rim) = match g.body {
            Body::Moon => (Color::from_rgb8(0x3b, 0x4a, 0x5c), Color::from_rgb8(0xc9, 0xe2, 0xf2)),
            Body::Asteroid => (Color::from_rgb8(0x4a, 0x3c, 0x36), Color::from_rgb8(0xe0, 0xc7, 0xb0)),
        };
        let ridge = Path::new(|b| {
            b.move_to(at(0.0, 0.0));
            b.line_to(at(0.0, t.at(0) as f32));
            for c in 0..COLS {
                b.line_to(at(c as f32 + 0.5, t.at(c) as f32));
            }
            b.line_to(at(COLS as f32, t.at(COLS - 1) as f32));
            b.line_to(at(COLS as f32, 0.0));
            b.close();
        });
        frame.fill(&ridge, rock);
        frame.stroke(&ridge, Stroke::default().with_color(rim).with_width(2.0));

        // The pads: flat, lit, marked with what they pay.
        for p in &t.pads {
            let y = t.at(p.start) as f32;
            let colour = match p.mult {
                5 => Color::from_rgb8(0xff, 0xd6, 0x5c),
                2 => Color::from_rgb8(0x8f, 0xe3, 0xf5),
                _ => Color::from_rgb8(0xb8, 0xc7, 0xd6),
            };
            frame.fill_rectangle(at(p.start as f32, y + 0.25), Size::new((p.end - p.start + 1) as f32 * m, m * 0.35), colour);
            label(&mut frame, format!("×{}", p.mult), at((p.start + p.end + 1) as f32 / 2.0, y - 1.0), m * 0.8, colour, Alignment::Center);
        }

        // The probe, eased between ticks.
        let (cx, cy, ch) = pose(g);
        let a = self.alpha;
        let ease = |p: f32, c: f32| p + (c - p) * a;
        let mut dh = ch - self.prev.2;
        if dh > 180.0 {
            dh -= 360.0;
        } else if dh < -180.0 {
            dh += 360.0;
        }
        let (x, y, heading) = if g.state == State::Flying {
            (ease(self.prev.0, cx), ease(self.prev.1, cy), self.prev.2 + dh * a)
        } else {
            (cx, cy, ch)
        };
        let crashed = g.state == State::Crashed;
        frame.with_save(|f| {
            // Origin at the feet, rotated about the probe's middle.
            let feet = at(x + 0.5, y);
            f.translate(Vector::new(feet.x, feet.y - m * 0.9));
            f.rotate(Radians(heading.to_radians()));
            probe(f, m, self.burning && !crashed, g.ticks, crashed);
        });

        // The instruments.
        let vx_ok = g.vx.abs() < MAX_VX;
        let vy_ok = g.vy.abs() < MAX_VY;
        let tilt_ok = g.tilt() < MAX_TILT_DEG;
        let white = Color::from_rgba(1.0, 1.0, 1.0, 0.92);
        let px = m * 0.62;
        let rows: [(String, Color); 5] = [
            (format!("ALT  {} m", g.altitude().max(0)), white),
            (format!("H    {} m/s", mps_text(g.vx)), if vx_ok { GOOD } else { BAD }),
            (format!("V    {} m/s", mps_text(g.vy)), if vy_ok { GOOD } else { BAD }),
            (format!("TILT {}°", g.tilt()), if tilt_ok { GOOD } else { BAD }),
            (format!("FUEL {}", g.fuel_units()), white),
        ];
        for (i, (s, c)) in rows.iter().enumerate() {
            label(&mut frame, s.clone(), Point::new(ox + m * 0.8, oy + m * (1.0 + i as f32 * 0.95)), px, *c, Alignment::Start);
        }
        let fuel = g.fuel as f32 / FUEL_START as f32;
        frame.fill_rectangle(Point::new(ox + m * 0.8, oy + m * 5.4), Size::new(m * 7.0, m * 0.3), Color::from_rgba(1.0, 1.0, 1.0, 0.15));
        frame.fill_rectangle(Point::new(ox + m * 0.8, oy + m * 5.4), Size::new(m * 7.0 * fuel, m * 0.3), if fuel > 0.2 { GOOD } else { BAD });
        label(&mut frame, format!("SCORE {}", g.total), Point::new(ox + w - m * 0.8, oy + m * 1.0), px, white, Alignment::End);
        label(&mut frame, format!("WAVE {}", g.wave), Point::new(ox + w - m * 0.8, oy + m * 1.95), px, white, Alignment::End);
        label(&mut frame, format!("BEST {}", g.best_score.max(g.total)), Point::new(ox + w - m * 0.8, oy + m * 2.9), px, white, Alignment::End);

        let banner = match g.state {
            State::Landed => Some((
                format!("Landed on ×{} · +{}", g.last_mult, g.last_points),
                "Next Wave: N or Enter".to_string(),
            )),
            State::Crashed => Some((
                format!("Crashed — {}", g.cause.map(|c| c.text()).unwrap_or("crashed")),
                format!("Score {} · New Game: Ctrl+N", g.total),
            )),
            State::Flying if self.waiting => Some((
                if g.wave <= 1 { "Ice Lander".to_string() } else { format!("Wave {}", g.wave) },
                "Hold Space to burn · ← → to turn · land slow and level on a pad".to_string(),
            )),
            State::Flying => None,
        };
        if let Some((title, hint)) = banner {
            let bw = (w * 0.62).min(m * 40.0);
            let top = oy + h * 0.32;
            let card = Path::rounded_rectangle(Point::new(ox + (w - bw) / 2.0, top), Size::new(bw, m * 3.6), (m * 0.5).into());
            frame.fill(&card, Color::from_rgba(0.02, 0.05, 0.1, 0.86));
            label(&mut frame, title, Point::new(ox + w / 2.0, top + m * 1.2), m * 1.0, Color::WHITE, Alignment::Center);
            label(&mut frame, hint, Point::new(ox + w / 2.0, top + m * 2.5), m * 0.55, Color::from_rgba(1.0, 1.0, 1.0, 0.8), Alignment::Center);
        }
        vec![frame.into_geometry()]
    }
}

/// The probe, about its middle, heading up the screen: a cabin, a deck, two
/// legs, an engine bell, and a flame while it burns.
fn probe(f: &mut Frame<cosmic::Renderer>, m: f32, burning: bool, ticks: u64, crashed: bool) {
    let hull = if crashed { Color::from_rgb8(0x8a, 0x5a, 0x50) } else { Color::from_rgb8(0xdd, 0xe8, 0xf0) };
    let gold = Color::from_rgb8(0xd9, 0xa4, 0x41);
    if burning {
        let flicker = 0.8 + 0.25 * ((ticks % 3) as f32);
        let flame = Path::new(|b| {
            b.move_to(Point::new(-m * 0.28, m * 0.55));
            b.line_to(Point::new(0.0, m * (0.55 + 1.1 * flicker)));
            b.line_to(Point::new(m * 0.28, m * 0.55));
            b.close();
        });
        f.fill(&flame, Color::from_rgba(1.0, 0.62, 0.2, 0.9));
        let core = Path::new(|b| {
            b.move_to(Point::new(-m * 0.14, m * 0.55));
            b.line_to(Point::new(0.0, m * (0.55 + 0.6 * flicker)));
            b.line_to(Point::new(m * 0.14, m * 0.55));
            b.close();
        });
        f.fill(&core, Color::from_rgb8(0xff, 0xf2, 0xb0));
    }
    // Legs and feet.
    let legs = Path::new(|b| {
        b.move_to(Point::new(-m * 0.45, m * 0.25));
        b.line_to(Point::new(-m * 0.75, m * 0.9));
        b.move_to(Point::new(m * 0.45, m * 0.25));
        b.line_to(Point::new(m * 0.75, m * 0.9));
        b.move_to(Point::new(-m * 0.95, m * 0.9));
        b.line_to(Point::new(-m * 0.55, m * 0.9));
        b.move_to(Point::new(m * 0.55, m * 0.9));
        b.line_to(Point::new(m * 0.95, m * 0.9));
    });
    f.stroke(&legs, Stroke::default().with_color(hull).with_width((m * 0.1).max(1.5)));
    // Engine bell, deck, cabin, window.
    let bell = Path::new(|b| {
        b.move_to(Point::new(-m * 0.18, m * 0.3));
        b.line_to(Point::new(m * 0.18, m * 0.3));
        b.line_to(Point::new(m * 0.28, m * 0.58));
        b.line_to(Point::new(-m * 0.28, m * 0.58));
        b.close();
    });
    f.fill(&bell, Color::from_rgb8(0x6b, 0x7a, 0x8c));
    f.fill(&Path::rectangle(Point::new(-m * 0.6, m * 0.05), Size::new(m * 1.2, m * 0.32)), gold);
    f.fill(&Path::rounded_rectangle(Point::new(-m * 0.45, -m * 0.65), Size::new(m * 0.9, m * 0.72), (m * 0.25).into()), hull);
    f.fill(&Path::circle(Point::new(0.0, -m * 0.32), m * 0.17), Color::from_rgb8(0x2f, 0x7f, 0xd8));
}
