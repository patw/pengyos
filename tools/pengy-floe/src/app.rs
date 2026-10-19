//! The Flappy Pengy window (libcosmic).
//!
//! One canvas: a polar night with an aurora, ice ridges drifting by, ice
//! pillars, and the penguin. Space (or W, or the up arrow) flaps. The game
//! steps on its own fixed tick (`pengy_floe::TICK_MS`); the picture is drawn
//! every frame, with the penguin's height eased between the last two ticks and
//! the pillars placed at their fractional scroll, so the motion is smooth.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use cosmic::iced::keyboard::{self, key::Named, Key};
use cosmic::iced::widget::canvas::{self, Frame, Geometry, Path, Stroke, Text};
use cosmic::iced::{alignment, mouse, Alignment, Color, Length, Point, Radians, Rectangle, Size, Subscription, Vector};
use cosmic::prelude::*;
use cosmic::widget::{button, container, nav_bar};

use crate::{Game, State, COLS, PENGUIN_COL, PILLAR_W, ROWS, SPEED_UNIT, TICK_MS};

use super::{Args, Best};

pub const APP_ID: &str = "io.catbee.pengyos.FlappyPengy";

const TICK: Duration = Duration::from_millis(TICK_MS);

pub fn run(args: &Args, dir: PathBuf) -> i32 {
    let mut game = Game::new(args.seed);
    let dir = (!args.no_save).then_some(dir);
    if let Some(d) = &dir {
        game.best = Best::load(d).score;
    }
    let flags = Flags { game, dir, autopilot: args.autopilot };
    let settings = cosmic::app::Settings::default()
        .size(Size::new(880.0, 520.0))
        .size_limits(cosmic::iced::Limits::NONE.min_width(400.0).min_height(260.0));
    match cosmic::app::run::<App>(settings, flags) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("pengy-floe: {err}");
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
    Key(char),
    Frame(Instant),
    Quit,
}

pub struct App {
    core: cosmic::Core,
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
    banked: bool,
    /// The first flap starts the clock: a fresh window waits for the player.
    started: bool,
    /// The penguin's row one tick ago, to ease between.
    prev_y: i32,
    last_tick: Option<Instant>,
    /// How far the current tick has run, 0..1.
    alpha: f32,
}

impl App {
    fn bank(&mut self) {
        if self.banked || self.game.ticks == 0 {
            return;
        }
        if let Some(d) = &self.dir {
            Best::save(d, &self.game);
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
        let prev_y = flags.game.y;
        let mut app = App {
            core,
            game: flags.game,
            dir: flags.dir,
            autopilot: flags.autopilot,
            banked: false,
            started: flags.autopilot,
            prev_y,
            last_tick: None,
            alpha: 0.0,
        };
        app.set_header_title("Flappy Pengy".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Flappy Pengy".into(), id),
            None => Task::none(),
        };
        (app, title)
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Key(c) if !self.started && c != 'r' => {
                // The first flap starts the run; nothing else does.
                if c == ' ' {
                    self.started = true;
                    self.game.key(c);
                }
            }
            Message::Key(c) => {
                let was_dead = self.game.state == State::Dead;
                if c == 'r' {
                    self.bank();
                    self.banked = false;
                    self.started = self.autopilot;
                }
                self.game.key(c);
                if was_dead && self.game.state == State::Playing {
                    // A flap on the death screen is a new run.
                    self.banked = false;
                    self.prev_y = self.game.y;
                    self.last_tick = None;
                }
                if self.game.state != State::Playing {
                    self.last_tick = None;
                }
            }
            Message::Frame(now) => {
                if self.game.state != State::Playing {
                    self.last_tick = None;
                    return Task::none();
                }
                let mut last = *self.last_tick.get_or_insert(now);
                // A fixed timestep: as many ticks as the wall clock owes, and
                // never a burst after the window was hidden for a while.
                let mut steps = 0;
                while now.duration_since(last) >= TICK && steps < 4 {
                    if self.autopilot {
                        if let Some(k) = self.game.autopilot_key() {
                            self.game.key(k);
                        }
                    }
                    self.prev_y = self.game.y;
                    self.game.tick();
                    last += TICK;
                    steps += 1;
                    if self.game.state != State::Playing {
                        break;
                    }
                }
                if now.duration_since(last) >= TICK {
                    last = now;
                }
                self.last_tick = Some(last);
                self.alpha = (now.duration_since(last).as_secs_f32() / TICK.as_secs_f32()).min(1.0);
                if self.game.state == State::Dead {
                    self.bank();
                    self.alpha = 1.0;
                }
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
        let label = match self.game.state {
            State::Paused => "Resume",
            _ => "Pause",
        };
        vec![
            button::standard("New Game").on_press(Message::Key('r')).into(),
            button::standard(label)
                .on_press_maybe((self.game.state != State::Dead).then_some(Message::Key('p')))
                .into(),
        ]
    }

    fn view(&self) -> Element<'_, Message> {
        let sky = Sky { game: &self.game, prev_y: self.prev_y, alpha: self.alpha, waiting: !self.started };
        container(canvas::Canvas::new(sky).width(Length::Fill).height(Length::Fill))
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        let keys = cosmic::iced::event::listen_with(|event, status, _| {
            let cosmic::iced::Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, repeat, .. }) = event
            else {
                return None;
            };
            if status == cosmic::iced::event::Status::Captured {
                return None;
            }
            match key.as_ref() {
                // One press, one flap: a held key must not machine-gun.
                _ if repeat => None,
                Key::Named(Named::ArrowUp) => Some(Message::Key(' ')),
                Key::Named(Named::Escape) => Some(Message::Key('p')),
                Key::Named(Named::F2) => Some(Message::Key('r')),
                Key::Character("n") if modifiers.control() => Some(Message::Key('r')),
                Key::Character("q") if modifiers.control() => Some(Message::Quit),
                Key::Character(c) if !modifiers.control() => match c.chars().next()?.to_ascii_lowercase() {
                    ' ' | 'w' | 'k' => Some(Message::Key(' ')),
                    'p' => Some(Message::Key('p')),
                    _ => None,
                },
                _ => None,
            }
        });
        // Clicking the sky flaps too.
        let clicks = cosmic::iced::event::listen_with(|event, status, _| match event {
            cosmic::iced::Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left))
                if status == cosmic::iced::event::Status::Ignored =>
            {
                Some(Message::Key(' '))
            }
            _ => None,
        });
        let frames = if self.started && self.game.state == State::Playing {
            cosmic::iced::window::frames().map(|(_, at)| Message::Frame(at))
        } else {
            Subscription::none()
        };
        Subscription::batch([keys, clicks, frames])
    }
}

// ---------------------------------------------------------------------------
// The sky
// ---------------------------------------------------------------------------

struct Sky<'a> {
    game: &'a Game,
    prev_y: i32,
    alpha: f32,
    /// No flap yet: the title card is up.
    waiting: bool,
}

fn mix(a: Color, b: Color, t: f32) -> Color {
    Color { r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t, a: a.a + (b.a - a.a) * t }
}

fn text(frame: &mut Frame<cosmic::Renderer>, s: String, at: Point, px: f32, colour: Color) {
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
        // One cell of the game's grid, and the field centred in the window.
        let cell = (bounds.width / COLS as f32).min(bounds.height / ROWS as f32);
        let (w, h) = (cell * COLS as f32, cell * ROWS as f32);
        let ox = (bounds.width - w) / 2.0;
        let oy = (bounds.height - h) / 2.0;
        let scroll = g.ticks as f32 + self.alpha;

        // The night: bands from deep blue at the top to teal at the horizon.
        frame.fill_rectangle(Point::ORIGIN, bounds.size(), Color::from_rgb8(0x07, 0x10, 0x24));
        let (top, low) = (Color::from_rgb8(0x07, 0x10, 0x24), Color::from_rgb8(0x14, 0x4a, 0x63));
        let bands = 24;
        for i in 0..bands {
            let t = i as f32 / (bands - 1) as f32;
            frame.fill_rectangle(
                Point::new(ox, oy + h * i as f32 / bands as f32),
                Size::new(w, h / bands as f32 + 1.0),
                mix(top, low, t),
            );
        }
        // Stars, fixed to the field.
        for i in 0..40u32 {
            let x = (i.wrapping_mul(2_654_435_761) % 1000) as f32 / 1000.0;
            let y = (i.wrapping_mul(40_503) % 1000) as f32 / 1000.0 * 0.55;
            let twinkle = 0.35 + 0.35 * ((scroll * 0.07 + i as f32).sin() * 0.5 + 0.5);
            frame.fill(
                &Path::circle(Point::new(ox + x * w, oy + y * h), (cell * 0.06).max(1.0)),
                Color::from_rgba(1.0, 1.0, 1.0, twinkle),
            );
        }
        // The aurora: two soft ribbons that sway.
        for (k, colour) in [(0.0f32, Color::from_rgba(0.35, 0.95, 0.65, 0.16)), (1.7, Color::from_rgba(0.45, 0.6, 1.0, 0.12))] {
            let ribbon = Path::new(|b| {
                let steps = 48;
                for s in 0..=steps {
                    let x = ox + w * s as f32 / steps as f32;
                    let y = oy + h * (0.22 + 0.06 * ((s as f32 * 0.25 + scroll * 0.03 + k).sin()));
                    if s == 0 {
                        b.move_to(Point::new(x, y));
                    } else {
                        b.line_to(Point::new(x, y));
                    }
                }
                for s in (0..=steps).rev() {
                    let x = ox + w * s as f32 / steps as f32;
                    let y = oy + h * (0.34 + 0.05 * ((s as f32 * 0.31 + scroll * 0.025 + k).cos()));
                    b.line_to(Point::new(x, y));
                }
                b.close();
            });
            frame.fill(&ribbon, colour);
        }
        // Far ridges, drifting at a fifth of the pillars' speed.
        let ridge = Path::new(|b| {
            let span = w * 0.25;
            let shift = (scroll * cell * 0.08) % span;
            b.move_to(Point::new(ox, oy + h));
            let mut x = ox - shift;
            let mut up = true;
            b.line_to(Point::new(ox, oy + h * 0.78));
            while x < ox + w + span {
                let peak = if up { 0.62 } else { 0.8 };
                b.line_to(Point::new(x.clamp(ox, ox + w), oy + h * peak));
                x += span / 2.0;
                up = !up;
            }
            b.line_to(Point::new(ox + w, oy + h));
            b.close();
        });
        frame.fill(&ridge, Color::from_rgb8(0x1c, 0x3d, 0x55));

        // The pillars, at their fractional scroll position.
        let frac = g.sub as f32 / SPEED_UNIT as f32;
        let ice = Color::from_rgb8(0xbf, 0xe6, 0xf6);
        let ice_dark = Color::from_rgb8(0x7f, 0xbe, 0xdc);
        for p in &g.pillars {
            let x = ox + (p.x as f32 - frac) * cell;
            let pw = PILLAR_W as f32 * cell;
            if x > ox + w || x + pw < ox {
                continue;
            }
            for (y0, y1, cap_at) in [(0.0, p.gap_top as f32 * cell, p.gap_top as f32 * cell), ((p.gap_bottom + 1) as f32 * cell, h, (p.gap_bottom + 1) as f32 * cell)] {
                if y1 <= y0 {
                    continue;
                }
                frame.fill_rectangle(Point::new(x, oy + y0), Size::new(pw, y1 - y0), ice);
                frame.fill_rectangle(Point::new(x + pw * 0.62, oy + y0), Size::new(pw * 0.38, y1 - y0), ice_dark);
                frame.fill_rectangle(Point::new(x + pw * 0.14, oy + y0), Size::new(pw * 0.12, y1 - y0), Color::from_rgba(1.0, 1.0, 1.0, 0.6));
                // A lip at the gap's edge.
                let lip = if cap_at <= y0 + 0.5 { oy + y0 } else { oy + cap_at - cell * 0.45 };
                let cap = Path::rounded_rectangle(Point::new(x - cell * 0.2, lip), Size::new(pw + cell * 0.4, cell * 0.45), (cell * 0.12).into());
                frame.fill(&cap, Color::from_rgb8(0xea, 0xf7, 0xfd));
                frame.stroke(&cap, Stroke::default().with_color(ice_dark).with_width(1.0));
            }
        }

        // Snow on the floor.
        frame.fill_rectangle(Point::new(ox, oy + h - cell * 0.6), Size::new(w, cell * 0.6), Color::from_rgb8(0xee, 0xf6, 0xfb));

        // The penguin: eased between the last two ticks, tilted by its speed.
        let y = self.prev_y as f32 + (g.y - self.prev_y) as f32 * self.alpha;
        let centre = Point::new(ox + (PENGUIN_COL as f32 + 1.0) * cell, oy + (y + 0.5) * cell);
        let tilt = if g.state == State::Dead { 1.2 } else { (g.vy as f32 * 0.22).clamp(-0.5, 0.9) };
        frame.with_save(|f| {
            f.translate(Vector::new(centre.x, centre.y));
            f.rotate(Radians(tilt));
            penguin(f, cell, g.ticks, g.state == State::Dead);
        });

        // The score, big, at the top.
        text(&mut frame, g.score.to_string(), Point::new(ox + w / 2.0, oy + cell * 2.0), cell * 1.8, Color::from_rgba(1.0, 1.0, 1.0, 0.9));

        let banner = match g.state {
            State::Dead => Some((
                format!("{} — score {}", g.cause.map(|c| c.label()).unwrap_or("splash"), g.score),
                format!("Best {}{} · Space to fly again", g.best.max(g.score), g.medal().map(|m| format!(" · {m} medal")).unwrap_or_default()),
            )),
            State::Paused => Some(("Paused".to_string(), "Esc to resume".to_string())),
            State::Playing if self.waiting => Some(("Flappy Pengy".to_string(), "Space, W, ↑ or a click to flap".to_string())),
            State::Playing => None,
        };
        if let Some((title, hint)) = banner {
            let bw = (w * 0.7).min(560.0);
            let at = Point::new(ox + (w - bw) / 2.0, oy + h * 0.36);
            let card = Path::rounded_rectangle(at, Size::new(bw, cell * 4.0), (cell * 0.5).into());
            frame.fill(&card, Color::from_rgba(0.02, 0.06, 0.12, 0.78));
            text(&mut frame, title, Point::new(ox + w / 2.0, at.y + cell * 1.4), cell * 0.95, Color::WHITE);
            text(&mut frame, hint, Point::new(ox + w / 2.0, at.y + cell * 2.8), cell * 0.6, Color::from_rgba(1.0, 1.0, 1.0, 0.8));
        }
        vec![frame.into_geometry()]
    }
}

/// The penguin, drawn about the origin, a little under two cells wide.
fn penguin(f: &mut Frame<cosmic::Renderer>, cell: f32, ticks: u64, dead: bool) {
    let s = cell;
    let ellipse = |cx: f32, cy: f32, rx: f32, ry: f32| {
        Path::new(|b| {
            b.ellipse(cosmic::iced::widget::canvas::path::arc::Elliptical {
                center: Point::new(cx, cy),
                radii: Vector::new(rx, ry),
                rotation: Radians(0.0),
                start_angle: Radians(0.0),
                end_angle: Radians(std::f32::consts::TAU),
            })
        })
    };
    let navy = Color::from_rgb8(0x1b, 0x24, 0x33);
    // Body, belly.
    f.fill(&ellipse(0.0, 0.0, s * 0.95, s * 0.72), navy);
    f.fill(&ellipse(s * 0.18, s * 0.12, s * 0.62, s * 0.52), Color::from_rgb8(0xf6, 0xf8, 0xfb));
    // The wing beats while alive.
    let beat = if dead { 0.0 } else { ((ticks % 4) as f32 - 1.5) * 0.12 };
    let wing = Path::new(|b| {
        b.move_to(Point::new(-s * 0.35, -s * 0.05));
        b.line_to(Point::new(-s * 0.95, s * (0.15 + beat)));
        b.line_to(Point::new(-s * 0.25, s * 0.35));
        b.close();
    });
    f.fill(&wing, Color::from_rgb8(0x2a, 0x36, 0x4a));
    // Beak and eye.
    let beak = Path::new(|b| {
        b.move_to(Point::new(s * 0.78, -s * 0.18));
        b.line_to(Point::new(s * 1.28, -s * 0.06));
        b.line_to(Point::new(s * 0.78, s * 0.08));
        b.close();
    });
    f.fill(&beak, Color::from_rgb8(0xf4, 0x9a, 0x2a));
    f.fill(&Path::circle(Point::new(s * 0.48, -s * 0.3), s * 0.17), Color::WHITE);
    if dead {
        let x = Path::new(|b| {
            b.move_to(Point::new(s * 0.38, -s * 0.4));
            b.line_to(Point::new(s * 0.58, -s * 0.2));
            b.move_to(Point::new(s * 0.58, -s * 0.4));
            b.line_to(Point::new(s * 0.38, -s * 0.2));
        });
        f.stroke(&x, Stroke::default().with_color(navy).with_width((s * 0.07).max(1.0)));
    } else {
        f.fill(&Path::circle(Point::new(s * 0.53, -s * 0.3), s * 0.08), navy);
    }
}
