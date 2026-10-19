//! The Ice Rogue window (libcosmic).
//!
//! The map is a canvas: ice walls, snow floor, the stairs, and every creature
//! and item drawn as a small picture, in the light of the penguin's lamp
//! (what was seen but is out of sight stays, dimmed). The side panel is plain
//! widgets: the numbers, the pack, the last thing that happened, the keys.
//! Every rule is `pengy_rogue::Game`'s; a key is one of its key characters.

use std::path::PathBuf;

use cosmic::iced::keyboard::{self, key::Named, Key};
use cosmic::iced::widget::canvas::{self, Frame, Geometry, Path, Stroke};
use cosmic::iced::{mouse, Alignment, Color, Length, Point, Rectangle, Size, Subscription, Vector};
use cosmic::prelude::*;
use cosmic::widget::{self, button, container, nav_bar, row, text};

use crate::{
    idx, item_name, Game, MonsterKind, State, FLOORS, ITEM_FISH, ITEM_GOLD, ITEM_PARKA, ITEM_SCROLL, MAP_H, MAP_W,
    STAIRS, WALL,
};

use super::{apply_best, bank, start_game, Args, Best};

pub const APP_ID: &str = "io.catbee.pengyos.IceRogue";

pub fn run(args: &Args, dir: PathBuf) -> i32 {
    let mut game = match start_game(args) {
        Ok(g) => g,
        Err(code) => return code,
    };
    let dir = (!args.no_save).then_some(dir);
    if let Some(d) = &dir {
        apply_best(&mut game, &Best::load(d));
    }
    let flags = Flags { game, dir, autopilot: args.autopilot };
    let settings = cosmic::app::Settings::default()
        .size(Size::new(1180.0, 560.0))
        .size_limits(cosmic::iced::Limits::NONE.min_width(720.0).min_height(320.0));
    match cosmic::app::run::<App>(settings, flags) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("pengy-rogue: {err}");
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
    /// One of the game's keys: `hjklyubn . g >`.
    Key(char),
    NewGame,
    Demo,
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
        if self.banked || self.game.turns == 0 {
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
        app.set_header_title("Ice Rogue".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Ice Rogue".into(), id),
            None => Task::none(),
        };
        (app, title)
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Key(c) => {
                self.game.key(c);
            }
            Message::Demo => {
                if self.game.state == State::Play {
                    for c in self.game.autopilot_keys().chars() {
                        self.game.key(c);
                    }
                }
            }
            Message::NewGame => {
                self.bank();
                let mut next = Game::new(self.game.seed.wrapping_add(1));
                let g = &self.game;
                (next.best_score, next.best_floor, next.best_kills, next.best_games, next.best_wins, next.best_seed) =
                    (g.best_score, g.best_floor, g.best_kills, g.best_games, g.best_wins, g.best_seed);
                self.game = next;
                self.banked = false;
            }
            Message::Quit => {
                self.bank();
                return cosmic::iced::exit();
            }
        }
        if self.game.state != State::Play {
            self.bank();
        }
        Task::none()
    }

    fn on_app_exit(&mut self) -> Option<Message> {
        self.bank();
        None
    }

    fn header_start(&self) -> Vec<Element<'_, Message>> {
        vec![button::standard("New Game").on_press(Message::NewGame).into()]
    }

    fn view(&self) -> Element<'_, Message> {
        let g = &self.game;
        let spacing = cosmic::theme::spacing();
        let map = canvas::Canvas::new(Map { game: g }).width(Length::Fill).height(Length::Fill);

        let mut side: Vec<Element<'_, Message>> = Vec::new();
        match g.state {
            State::Dead => {
                side.push(text::title3("You died").into());
                side.push(text::body(format!("On floor {}, with {} kills and {} gold.", g.floor, g.kills, g.gold)).into());
                side.push(button::suggested("New Game").on_press(Message::NewGame).into());
            }
            State::Win => {
                side.push(text::title3("The Thaw is broken").into());
                side.push(text::body("The ice holds. You win.").into());
                side.push(button::suggested("New Game").on_press(Message::NewGame).into());
            }
            State::Play => {
                side.push(text::title4(format!("Floor {} of {}", g.floor, FLOORS)).into());
            }
        }
        let hp = (g.hp.max(0) as f32 / g.max_hp.max(1) as f32).clamp(0.0, 1.0);
        side.push(
            row::with_children([
                text::body(format!("Health {}/{}", g.hp.max(0), g.max_hp)).width(Length::Fixed(110.0)).into(),
                widget::determinate_linear(hp).width(Length::Fill).into(),
            ])
            .spacing(spacing.space_xs)
            .align_y(Alignment::Center)
            .into(),
        );
        side.push(text::body(format!("Attack 1d4+{} · Defence {}", 1 + g.weapon, g.defence)).into());
        side.push(text::body(format!("Gold {} · Kills {} · Score {}", g.gold, g.kills, g.score())).into());
        let pack = if g.inventory.is_empty() {
            "Pack: empty".to_string()
        } else {
            let mut counts: Vec<(char, usize)> = Vec::new();
            for &c in &g.inventory {
                match counts.iter_mut().find(|(k, _)| *k == c) {
                    Some((_, n)) => *n += 1,
                    None => counts.push((c, 1)),
                }
            }
            let parts: Vec<String> = counts.iter().map(|(c, n)| format!("{} ×{}", item_name(*c), n)).collect();
            format!("Pack: {}", parts.join(", "))
        };
        side.push(text::caption(pack).into());
        if !g.message.is_empty() {
            side.push(container(text::body(g.message.clone())).padding(spacing.space_xs).class(cosmic::theme::Container::Card).width(Length::Fill).into());
        }
        side.push(widget::space::vertical().into());
        side.push(
            text::caption(
                "Arrows or hjkl move, yubn (or Home PgUp End PgDn) go diagonally. \
                 Walk into a monster to fight it. G picks up, > goes down the stairs, \
                 . waits a turn. Fish heal, scrolls map the floor, a parka keeps out the cold.",
            )
            .into(),
        );
        side.push(text::caption(format!("Best {} · deepest floor {} · {} won of {}", g.best_score, g.best_floor, g.best_wins, g.best_games)).into());
        let panel = widget::column::with_children(side)
            .spacing(spacing.space_s)
            .padding(spacing.space_s)
            .width(Length::Fixed(320.0))
            .height(Length::Fill);
        container(row::with_children([map.into(), panel.into()])).width(Length::Fill).height(Length::Fill).into()
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
                Key::Named(Named::Home) => Some(Message::Key('y')),
                Key::Named(Named::PageUp) => Some(Message::Key('u')),
                Key::Named(Named::End) => Some(Message::Key('b')),
                Key::Named(Named::PageDown) => Some(Message::Key('n')),
                Key::Named(Named::F2) => Some(Message::NewGame),
                Key::Character("n") if modifiers.control() => Some(Message::NewGame),
                Key::Character("q") if modifiers.control() => Some(Message::Quit),
                Key::Character(c) if !modifiers.control() => {
                    let c = c.chars().next()?;
                    match c {
                        'h' | 'j' | 'k' | 'l' | 'y' | 'u' | 'b' | 'n' | '.' | '>' => Some(Message::Key(c)),
                        'g' | 'G' | ',' => Some(Message::Key('g')),
                        _ => None,
                    }
                }
                _ => None,
            }
        });
        // The demo penguin plays a move every 150 ms.
        let demo = if self.autopilot && self.game.state == State::Play {
            cosmic::iced::time::every(std::time::Duration::from_millis(150)).map(|_| Message::Demo)
        } else {
            Subscription::none()
        };
        Subscription::batch([keys, demo])
    }
}

// ---------------------------------------------------------------------------
// The map
// ---------------------------------------------------------------------------

struct Map<'a> {
    game: &'a Game,
}

impl canvas::Program<Message, cosmic::Theme, cosmic::Renderer> for Map<'_> {
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
        frame.fill_rectangle(Point::ORIGIN, bounds.size(), Color::from_rgb8(0x05, 0x09, 0x10));
        // Tiles are at least 30 px, so the pictures read; when the map does
        // not fit at that size, the view follows the penguin (and stops at the
        // map's edges, so no space is wasted on rock that is not there).
        let fit = ((bounds.width - 16.0) / MAP_W as f32).min((bounds.height - 16.0) / MAP_H as f32);
        let t = fit.max(30.0).floor();
        let camera = |size: f32, tiles: usize, at: usize| -> (f32, f32) {
            let shown = size / t;
            if shown >= tiles as f32 {
                (0.0, (size - t * tiles as f32) / 2.0)
            } else {
                let first = (at as f32 + 0.5 - shown / 2.0).clamp(0.0, tiles as f32 - shown);
                (first, 0.0)
            }
        };
        let (cam_x, ox) = camera(bounds.width, MAP_W, g.player.x);
        let (cam_y, oy) = camera(bounds.height, MAP_H, g.player.y);
        let at = |x: usize, y: usize| Point::new(ox + (x as f32 - cam_x) * t, oy + (y as f32 - cam_y) * t);

        // Tiles: lit ones bright, remembered ones dim, unknown ones dark.
        for y in 0..MAP_H {
            for x in 0..MAP_W {
                let i = idx(x, y);
                if !g.seen[i] {
                    continue;
                }
                let lit = g.visible[i];
                let p = at(x, y);
                match g.map[i] {
                    WALL => {
                        // Only the walls that face a floor are drawn: the rock
                        // beyond them stays dark, which is what makes rooms read.
                        let faces_floor = [(-1i32, 0i32), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)]
                            .iter()
                            .any(|(dx, dy)| {
                                let (nx, ny) = (x as i32 + dx, y as i32 + dy);
                                nx >= 0
                                    && ny >= 0
                                    && (nx as usize) < MAP_W
                                    && (ny as usize) < MAP_H
                                    && g.map[idx(nx as usize, ny as usize)] != WALL
                            });
                        if !faces_floor {
                            continue;
                        }
                        let (top, side) = if lit {
                            (Color::from_rgb8(0x9c, 0xc9, 0xe6), Color::from_rgb8(0x5f, 0x8f, 0xb6))
                        } else {
                            (Color::from_rgb8(0x3a, 0x52, 0x68), Color::from_rgb8(0x2a, 0x3c, 0x4e))
                        };
                        frame.fill_rectangle(p, Size::new(t, t), side);
                        frame.fill_rectangle(p, Size::new(t, t * 0.7), top);
                    }
                    _ => {
                        let snow = if lit { Color::from_rgb8(0xe4, 0xee, 0xf5) } else { Color::from_rgb8(0x2c, 0x36, 0x42) };
                        frame.fill_rectangle(p, Size::new(t, t), snow);
                        if g.map[i] == STAIRS {
                            stairs(&mut frame, p, t, lit);
                        }
                    }
                }
            }
        }
        // A faint grid on the lit floor, so a step reads as a step.
        let grid = Path::new(|b| {
            for y in 0..MAP_H {
                for x in 0..MAP_W {
                    let i = idx(x, y);
                    if g.visible[i] && g.map[i] != WALL {
                        b.rectangle(at(x, y), Size::new(t, t));
                    }
                }
            }
        });
        frame.stroke(&grid, Stroke::default().with_color(Color::from_rgba(0.3, 0.45, 0.6, 0.12)).with_width(1.0));

        // What is in sight: the items, the monsters, the penguin.
        for it in &g.items {
            if g.visible[idx(it.x, it.y)] {
                item(&mut frame, it.glyph, at(it.x, it.y), t);
            }
        }
        for m in &g.monsters {
            if g.visible[idx(m.x, m.y)] {
                let p = at(m.x, m.y);
                monster(&mut frame, m.kind, p, t, g.turns);
                // A wounded monster shows how much it has left.
                let full = m.kind.hp();
                if m.hp < full {
                    let k = m.hp.max(0) as f32 / full as f32;
                    frame.fill_rectangle(Point::new(p.x + t * 0.1, p.y + t * 0.05), Size::new(t * 0.8, t * 0.1), Color::from_rgba(0.0, 0.0, 0.0, 0.5));
                    frame.fill_rectangle(Point::new(p.x + t * 0.1, p.y + t * 0.05), Size::new(t * 0.8 * k, t * 0.1), Color::from_rgb8(0xe0, 0x4f, 0x5f));
                }
            }
        }
        penguin(&mut frame, at(g.player.x, g.player.y), t, g.state == State::Dead);

        if g.state != State::Play {
            let tint = if g.state == State::Win { Color::from_rgba(1.0, 0.85, 0.4, 0.18) } else { Color::from_rgba(0.5, 0.0, 0.05, 0.35) };
            frame.fill_rectangle(Point::ORIGIN, bounds.size(), tint);
        }
        vec![frame.into_geometry()]
    }
}

fn ellipse(c: Point, rx: f32, ry: f32) -> Path {
    Path::new(|b| {
        b.ellipse(cosmic::iced::widget::canvas::path::arc::Elliptical {
            center: c,
            radii: Vector::new(rx, ry),
            rotation: cosmic::iced::Radians(0.0),
            start_angle: cosmic::iced::Radians(0.0),
            end_angle: cosmic::iced::Radians(std::f32::consts::TAU),
        })
    })
}

fn stairs(frame: &mut Frame<cosmic::Renderer>, p: Point, t: f32, lit: bool) {
    let c = if lit { Color::from_rgb8(0x4a, 0x5d, 0x70) } else { Color::from_rgb8(0x1c, 0x24, 0x2e) };
    for k in 0..3 {
        let k = k as f32;
        frame.fill_rectangle(Point::new(p.x + t * (0.15 + 0.2 * k), p.y + t * (0.2 + 0.2 * k)), Size::new(t * (0.7 - 0.2 * k), t * 0.18), c);
    }
}

fn penguin(frame: &mut Frame<cosmic::Renderer>, p: Point, t: f32, dead: bool) {
    let c = Point::new(p.x + t / 2.0, p.y + t / 2.0);
    let navy = Color::from_rgb8(0x1b, 0x24, 0x33);
    frame.fill(&ellipse(c, t * 0.34, t * 0.44), navy);
    frame.fill(&ellipse(Point::new(c.x, c.y + t * 0.07), t * 0.22, t * 0.32), Color::from_rgb8(0xf6, 0xf8, 0xfb));
    frame.fill(&Path::circle(Point::new(c.x - t * 0.1, c.y - t * 0.22), t * 0.06), Color::WHITE);
    frame.fill(&Path::circle(Point::new(c.x + t * 0.1, c.y - t * 0.22), t * 0.06), Color::WHITE);
    if !dead {
        frame.fill(&Path::circle(Point::new(c.x - t * 0.1, c.y - t * 0.22), t * 0.03), navy);
        frame.fill(&Path::circle(Point::new(c.x + t * 0.1, c.y - t * 0.22), t * 0.03), navy);
    }
    let beak = Path::new(|b| {
        b.move_to(Point::new(c.x - t * 0.07, c.y - t * 0.13));
        b.line_to(Point::new(c.x + t * 0.07, c.y - t * 0.13));
        b.line_to(Point::new(c.x, c.y - t * 0.02));
        b.close();
    });
    frame.fill(&beak, Color::from_rgb8(0xf4, 0x9a, 0x2a));
    // Feet.
    for dx in [-0.13, 0.13] {
        frame.fill(&ellipse(Point::new(c.x + t * dx, c.y + t * 0.42), t * 0.09, t * 0.04), Color::from_rgb8(0xf4, 0x9a, 0x2a));
    }
}

fn monster(frame: &mut Frame<cosmic::Renderer>, kind: MonsterKind, p: Point, t: f32, turns: u64) {
    let c = Point::new(p.x + t / 2.0, p.y + t / 2.0);
    match kind {
        MonsterKind::Rat => {
            let grey = Color::from_rgb8(0x8a, 0x84, 0x7e);
            let tail = Path::new(|b| {
                b.move_to(Point::new(c.x + t * 0.25, c.y + t * 0.12));
                b.quadratic_curve_to(Point::new(c.x + t * 0.5, c.y + t * 0.3), Point::new(c.x + t * 0.42, c.y - t * 0.1));
            });
            frame.stroke(&tail, Stroke::default().with_color(Color::from_rgb8(0xd9, 0x9a, 0xa6)).with_width(t * 0.05));
            frame.fill(&ellipse(Point::new(c.x + t * 0.05, c.y + t * 0.1), t * 0.28, t * 0.2), grey);
            frame.fill(&Path::circle(Point::new(c.x - t * 0.22, c.y), t * 0.15), grey);
            frame.fill(&Path::circle(Point::new(c.x - t * 0.2, c.y - t * 0.15), t * 0.07), Color::from_rgb8(0xd9, 0x9a, 0xa6));
            frame.fill(&Path::circle(Point::new(c.x - t * 0.28, c.y - t * 0.02), t * 0.035), Color::BLACK);
        }
        MonsterKind::IceCrab => {
            let red = Color::from_rgb8(0xe0, 0x5a, 0x4a);
            let legs = Path::new(|b| {
                for k in 0..3 {
                    let y = c.y + t * (0.02 + 0.1 * k as f32);
                    b.move_to(Point::new(c.x - t * 0.2, y));
                    b.line_to(Point::new(c.x - t * 0.42, y + t * 0.12));
                    b.move_to(Point::new(c.x + t * 0.2, y));
                    b.line_to(Point::new(c.x + t * 0.42, y + t * 0.12));
                }
            });
            frame.stroke(&legs, Stroke::default().with_color(red).with_width(t * 0.05));
            frame.fill(&ellipse(Point::new(c.x, c.y + t * 0.08), t * 0.28, t * 0.18), red);
            frame.fill(&Path::circle(Point::new(c.x - t * 0.3, c.y - t * 0.2), t * 0.11), red);
            frame.fill(&Path::circle(Point::new(c.x + t * 0.3, c.y - t * 0.2), t * 0.11), red);
            for dx in [-0.08, 0.08] {
                frame.fill(&Path::circle(Point::new(c.x + t * dx, c.y - t * 0.06), t * 0.04), Color::BLACK);
            }
        }
        MonsterKind::Wolf => {
            let grey = Color::from_rgb8(0x6d, 0x76, 0x82);
            let head = Path::new(|b| {
                b.move_to(Point::new(c.x - t * 0.36, c.y - t * 0.38));
                b.line_to(Point::new(c.x - t * 0.16, c.y - t * 0.16));
                b.line_to(Point::new(c.x + t * 0.16, c.y - t * 0.16));
                b.line_to(Point::new(c.x + t * 0.36, c.y - t * 0.38));
                b.line_to(Point::new(c.x + t * 0.34, c.y + t * 0.02));
                b.line_to(Point::new(c.x, c.y + t * 0.42));
                b.line_to(Point::new(c.x - t * 0.34, c.y + t * 0.02));
                b.close();
            });
            frame.fill(&head, grey);
            frame.fill(&ellipse(Point::new(c.x, c.y + t * 0.18), t * 0.14, t * 0.16), Color::from_rgb8(0xc8, 0xcf, 0xd8));
            for dx in [-0.14, 0.14] {
                frame.fill(&Path::circle(Point::new(c.x + t * dx, c.y - t * 0.03), t * 0.05), Color::from_rgb8(0xff, 0xd6, 0x5c));
            }
            frame.fill(&Path::circle(Point::new(c.x, c.y + t * 0.3), t * 0.05), Color::BLACK);
        }
        MonsterKind::Thaw => {
            // The Thaw: a sun that pulses with the turns.
            let pulse = 1.0 + 0.08 * ((turns % 4) as f32 - 1.5).abs();
            let rays = Path::new(|b| {
                for k in 0..8 {
                    let a = k as f32 * std::f32::consts::TAU / 8.0;
                    b.move_to(Point::new(c.x + a.cos() * t * 0.3, c.y + a.sin() * t * 0.3));
                    b.line_to(Point::new(c.x + a.cos() * t * 0.48 * pulse, c.y + a.sin() * t * 0.48 * pulse));
                }
            });
            frame.stroke(&rays, Stroke::default().with_color(Color::from_rgb8(0xff, 0x9a, 0x3d)).with_width(t * 0.08));
            frame.fill(&Path::circle(c, t * 0.3), Color::from_rgb8(0xff, 0xc1, 0x3d));
            frame.fill(&Path::circle(c, t * 0.18), Color::from_rgb8(0xff, 0x78, 0x2d));
        }
    }
}

fn item(frame: &mut Frame<cosmic::Renderer>, glyph: char, p: Point, t: f32) {
    let c = Point::new(p.x + t / 2.0, p.y + t / 2.0);
    match glyph {
        ITEM_FISH => {
            let blue = Color::from_rgb8(0x3d, 0x8b, 0xd8);
            let tail = Path::new(|b| {
                b.move_to(Point::new(c.x + t * 0.18, c.y));
                b.line_to(Point::new(c.x + t * 0.4, c.y - t * 0.16));
                b.line_to(Point::new(c.x + t * 0.4, c.y + t * 0.16));
                b.close();
            });
            frame.fill(&tail, blue);
            frame.fill(&ellipse(Point::new(c.x - t * 0.05, c.y), t * 0.27, t * 0.15), blue);
            frame.fill(&Path::circle(Point::new(c.x - t * 0.18, c.y - t * 0.03), t * 0.03), Color::WHITE);
        }
        ITEM_SCROLL => {
            frame.fill_rectangle(Point::new(c.x - t * 0.22, c.y - t * 0.25), Size::new(t * 0.44, t * 0.5), Color::from_rgb8(0xf1, 0xe2, 0xb8));
            for dy in [-0.27, 0.27] {
                frame.fill(&ellipse(Point::new(c.x, c.y + t * dy), t * 0.27, t * 0.06), Color::from_rgb8(0xc9, 0xa8, 0x6a));
            }
            let lines = Path::new(|b| {
                for k in 0..3 {
                    let y = c.y - t * 0.1 + k as f32 * t * 0.1;
                    b.move_to(Point::new(c.x - t * 0.14, y));
                    b.line_to(Point::new(c.x + t * 0.14, y));
                }
            });
            frame.stroke(&lines, Stroke::default().with_color(Color::from_rgb8(0x8a, 0x6e, 0x3f)).with_width(1.0));
        }
        ITEM_GOLD => {
            for (dx, dy) in [(-0.1f32, 0.08f32), (0.1, 0.08), (0.0, -0.08)] {
                let cc = Point::new(c.x + t * dx, c.y + t * dy);
                frame.fill(&Path::circle(cc, t * 0.15), Color::from_rgb8(0xf2, 0xc2, 0x3d));
                frame.stroke(&Path::circle(cc, t * 0.1), Stroke::default().with_color(Color::from_rgb8(0xb8, 0x86, 0x1b)).with_width(1.0));
            }
        }
        ITEM_PARKA => {
            let coat = Path::new(|b| {
                b.move_to(Point::new(c.x - t * 0.12, c.y - t * 0.3));
                b.line_to(Point::new(c.x + t * 0.12, c.y - t * 0.3));
                b.line_to(Point::new(c.x + t * 0.38, c.y - t * 0.1));
                b.line_to(Point::new(c.x + t * 0.3, c.y + t * 0.02));
                b.line_to(Point::new(c.x + t * 0.2, c.y - t * 0.05));
                b.line_to(Point::new(c.x + t * 0.24, c.y + t * 0.34));
                b.line_to(Point::new(c.x - t * 0.24, c.y + t * 0.34));
                b.line_to(Point::new(c.x - t * 0.2, c.y - t * 0.05));
                b.line_to(Point::new(c.x - t * 0.3, c.y + t * 0.02));
                b.line_to(Point::new(c.x - t * 0.38, c.y - t * 0.1));
                b.close();
            });
            frame.fill(&coat, Color::from_rgb8(0xd6, 0x3a, 0x45));
            frame.fill(&ellipse(Point::new(c.x, c.y - t * 0.3), t * 0.14, t * 0.06), Color::from_rgb8(0xf6, 0xf0, 0xe6));
        }
        _ => {}
    }
}
