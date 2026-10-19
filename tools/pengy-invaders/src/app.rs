//! The Thaw Invaders window (libcosmic).
//!
//! One canvas: the formation marching down, three ice shields, the cannon.
//! Left/right (or A/D) move — held, they keep moving; Space fires, and held,
//! fires again the moment the laser is spent. Every rule is
//! `pengy_invaders::Game`'s; this file paints its grid as pixel art.

use std::collections::HashSet;
use std::path::PathBuf;
use std::time::Duration;

use cosmic::iced::keyboard::{self, key::Named, Key};
use cosmic::iced::widget::canvas::{self, Frame, Geometry, Path, Text};
use cosmic::iced::{alignment, mouse, Alignment, Color, Length, Point, Rectangle, Size, Subscription};
use cosmic::prelude::*;
use cosmic::widget::{button, container, nav_bar};

use crate::{
    alien_left_col, alien_row, Game, State, ALIEN_W, COLS, FORM_COLS, FORM_ROWS, PLAYER_ROW, ROWS,
    SHIELD_H, SHIELD_TOP, SHIELD_W, SHIELD_X, TICK_MS,
};

use super::{bank, new_game, Args, Best};

pub const APP_ID: &str = "io.catbee.pengyos.ThawInvaders";

pub fn run(args: &Args, dir: PathBuf) -> i32 {
    let mut game = new_game(args);
    let dir = (!args.no_save).then_some(dir);
    if let Some(d) = &dir {
        let b = Best::load(d);
        game.best_score = b.score;
        game.best_wave = b.wave;
    }
    let flags = Flags { game, dir, autopilot: args.autopilot };
    let settings = cosmic::app::Settings::default()
        .size(Size::new(820.0, 720.0))
        .size_limits(cosmic::iced::Limits::NONE.min_width(420.0).min_height(380.0));
    match cosmic::app::run::<App>(settings, flags) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("pengy-invaders: {err}");
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
    Left,
    Right,
    Fire,
}

#[derive(Clone, Debug)]
pub enum Message {
    Press(Hold),
    Release(Hold),
    Key(char),
    Tick,
    Quit,
}

pub struct App {
    core: cosmic::Core,
    game: Game,
    dir: Option<PathBuf>,
    autopilot: bool,
    banked: bool,
    held: HashSet<Hold>,
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

    fn apply(&mut self, h: Hold) {
        match h {
            Hold::Left => self.game.key('a'),
            Hold::Right => self.game.key('d'),
            Hold::Fire => self.game.key(' '),
        };
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
        let mut app = App {
            core,
            game: flags.game,
            dir: flags.dir,
            autopilot: flags.autopilot,
            banked: false,
            held: HashSet::new(),
        };
        if app.autopilot {
            app.game.start();
        }
        app.set_header_title("Thaw Invaders".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Thaw Invaders".into(), id),
            None => Task::none(),
        };
        (app, title)
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Press(h) => {
                if self.held.insert(h) {
                    self.apply(h);
                }
            }
            Message::Release(h) => {
                self.held.remove(&h);
            }
            Message::Key(c) => {
                if c == 'r' {
                    self.bank();
                    self.banked = false;
                }
                self.game.key(c);
            }
            Message::Tick => {
                if self.autopilot && self.game.state == State::Play {
                    for k in self.game.autopilot_keys().chars() {
                        self.game.key(k);
                    }
                } else if self.game.state == State::Play {
                    // Held keys: the ship slides a step every other tick, and
                    // the gun fires again as soon as it may.
                    let slide = self.game.ticks % 2 == 0;
                    let held: Vec<Hold> = self.held.iter().copied().collect();
                    for h in held {
                        if h == Hold::Fire || slide {
                            self.apply(h);
                        }
                    }
                }
                self.game.tick();
                if self.game.state == State::Over {
                    self.bank();
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
        let paused = self.game.state == State::Pause;
        vec![
            button::standard("New Game").on_press(Message::Key('r')).into(),
            button::standard(if paused { "Resume" } else { "Pause" })
                .on_press_maybe(matches!(self.game.state, State::Play | State::Pause).then_some(Message::Key('p')))
                .into(),
        ]
    }

    fn view(&self) -> Element<'_, Message> {
        container(canvas::Canvas::new(Field { game: &self.game }).width(Length::Fill).height(Length::Fill))
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        fn hold(key: &Key) -> Option<Hold> {
            match key.as_ref() {
                Key::Named(Named::ArrowLeft) => Some(Hold::Left),
                Key::Named(Named::ArrowRight) => Some(Hold::Right),
                Key::Named(Named::ArrowUp) => Some(Hold::Fire),
                Key::Character(c) => match c.to_ascii_lowercase().as_str() {
                    "a" => Some(Hold::Left),
                    "d" => Some(Hold::Right),
                    " " => Some(Hold::Fire),
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
                        Key::Named(Named::Escape) => Some(Message::Key('p')),
                        Key::Named(Named::F2) => Some(Message::Key('r')),
                        Key::Character("n") if modifiers.control() => Some(Message::Key('r')),
                        Key::Character("q") if modifiers.control() => Some(Message::Quit),
                        Key::Character("p") => Some(Message::Key('p')),
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
// The field
// ---------------------------------------------------------------------------

struct Field<'a> {
    game: &'a Game,
}

/// The three invaders, two frames each, as pixel rows ('#' is lit). They are
/// drawn into a 2 x 1 cell box, which is why the field's cells are taller
/// than they are wide.
const SQUID: [[&str; 8]; 2] = [
    ["...##...", "..####..", ".######.", "##.##.##", "########", "..#..#..", ".#.##.#.", "#.#..#.#"],
    ["...##...", "..####..", ".######.", "##.##.##", "########", ".#.##.#.", "#......#", ".#....#."],
];
const CRAB: [[&str; 8]; 2] = [
    ["..#.....#..", "...#...#...", "..#######..", ".##.###.##.", "###########", "#.#######.#", "#.#.....#.#", "...##.##..."],
    ["..#.....#..", "#..#...#..#", "#.#######.#", "###.###.###", "###########", ".#########.", "..#.....#..", ".#.......#."],
];
const OCTOPUS: [[&str; 8]; 2] = [
    ["....####....", ".##########.", "############", "###..##..###", "############", "...##..##...", "..##.##.##..", "##........##"],
    ["....####....", ".##########.", "############", "###..##..###", "############", "..###..###..", ".##..##..##.", "..##....##.."],
];

fn sprite(frame: &mut Frame<cosmic::Renderer>, rows: &[&str; 8], at: Point, w: f32, h: f32, colour: Color) {
    let cols = rows[0].len();
    let (pw, ph) = (w / cols as f32, h / 8.0);
    let path = Path::new(|b| {
        for (r, line) in rows.iter().enumerate() {
            for (c, ch) in line.chars().enumerate() {
                if ch == '#' {
                    b.rectangle(Point::new(at.x + c as f32 * pw, at.y + r as f32 * ph), Size::new(pw + 0.3, ph + 0.3));
                }
            }
        }
    });
    frame.fill(&path, colour);
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

impl canvas::Program<Message, cosmic::Theme, cosmic::Renderer> for Field<'_> {
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
        frame.fill_rectangle(Point::ORIGIN, bounds.size(), Color::from_rgb8(0x06, 0x0b, 0x16));
        // Cells are 1 wide by 1.45 tall, so a 2 x 1 cell invader has the
        // classic 11:8 shape. Two rows of headroom hold the score line.
        let cx = ((bounds.width - 16.0) / COLS as f32).min((bounds.height - 16.0) / ((ROWS as f32 + 2.0) * 1.45));
        let cy = cx * 1.45;
        let (w, h) = (cx * COLS as f32, cy * ROWS as f32);
        let ox = (bounds.width - w) / 2.0;
        let oy = (bounds.height - h) / 2.0 + cy;
        let at = |col: f32, row: f32| Point::new(ox + col * cx, oy + row * cy);

        // Stars, and the ice the ship stands on.
        for i in 0..60u32 {
            let x = (i.wrapping_mul(2_654_435_761) % 1000) as f32 / 1000.0;
            let y = (i.wrapping_mul(40_503) % 1000) as f32 / 1000.0;
            frame.fill_rectangle(Point::new(ox + x * w, oy + y * h * 0.85), Size::new(1.5, 1.5), Color::from_rgba(1.0, 1.0, 1.0, 0.35));
        }
        frame.fill_rectangle(at(0.0, ROWS as f32), Size::new(w, cy * 0.25), Color::from_rgb8(0x9f, 0xd8, 0xf0));

        // The formation: two frames, swapped on every step it takes.
        let step = (g.formation_x + g.descents as usize) % 2;
        for ri in 0..FORM_ROWS {
            let (rows, colour) = match ri {
                0 => (&SQUID[step], Color::from_rgb8(0xff, 0x6f, 0x91)),
                1 | 2 => (&CRAB[step], Color::from_rgb8(0xff, 0xa2, 0x4c)),
                _ => (&OCTOPUS[step], Color::from_rgb8(0xff, 0xd6, 0x5c)),
            };
            for fc in 0..FORM_COLS {
                if !g.aliens[ri][fc] {
                    continue;
                }
                let col = alien_left_col(g.formation_x, fc) as f32;
                let row = alien_row(g.formation_row, ri) as f32;
                let sw = ALIEN_W as f32 * cx * 0.92;
                sprite(&mut frame, rows, Point::new(ox + col * cx + (ALIEN_W as f32 * cx - sw) / 2.0, oy + row * cy + cy * 0.12), sw, cy * 0.76, colour);
            }
        }

        // The shields: one ice block per cell still standing.
        let ice = Path::new(|b| {
            for (s, shield) in g.shields.iter().enumerate() {
                for r in 0..SHIELD_H {
                    for c in 0..SHIELD_W {
                        if shield[r][c] {
                            b.rectangle(at((SHIELD_X[s] + c) as f32, (SHIELD_TOP + r) as f32), Size::new(cx + 0.3, cy + 0.3));
                        }
                    }
                }
            }
        });
        frame.fill(&ice, Color::from_rgb8(0x8f, 0xe3, 0xf5));

        // Bombs: little falling flames.
        for b in &g.bombs {
            let p = at(b.col as f32 + 0.5, b.row as f32);
            let flame = Path::new(|f| {
                f.move_to(Point::new(p.x, p.y));
                f.line_to(Point::new(p.x + cx * 0.35, p.y + cy * 0.5));
                f.line_to(Point::new(p.x, p.y + cy));
                f.line_to(Point::new(p.x - cx * 0.35, p.y + cy * 0.5));
                f.close();
            });
            frame.fill(&flame, Color::from_rgb8(0xff, 0x8a, 0x3d));
        }
        // The laser: a frozen bolt.
        if let Some(l) = g.laser {
            frame.fill_rectangle(at(l.col as f32 + 0.35, l.row as f32), Size::new(cx * 0.3, cy * 0.9), Color::from_rgb8(0xd8, 0xf6, 0xff));
        }

        // The cannon: an ice-blue turret three cells wide.
        if g.state != State::Over {
            let x = g.player_x as f32;
            let base = Path::new(|b| {
                b.rectangle(at(x - 1.5, PLAYER_ROW as f32 + 0.45), Size::new(cx * 3.0, cy * 0.55));
                b.rectangle(at(x - 1.1, PLAYER_ROW as f32 + 0.25), Size::new(cx * 2.2, cy * 0.25));
                b.rectangle(at(x - 0.2, PLAYER_ROW as f32 - 0.1), Size::new(cx * 0.4, cy * 0.4));
            });
            frame.fill(&base, Color::from_rgb8(0x4f, 0xc3, 0xf7));
        }

        // The score line.
        let white = Color::from_rgba(1.0, 1.0, 1.0, 0.9);
        label(&mut frame, format!("SCORE {}", g.score), at(0.0, -0.6), cy * 0.6, white, Alignment::Start);
        label(&mut frame, format!("WAVE {}", g.wave), at(COLS as f32 / 2.0, -0.6), cy * 0.6, white, Alignment::Center);
        label(&mut frame, format!("BEST {}", g.best_score.max(g.score)), at(COLS as f32 - 9.0, -0.6), cy * 0.6, white, Alignment::End);
        for i in 0..g.lives {
            let x = COLS as f32 - 7.0 + i as f32 * 2.4;
            frame.fill_rectangle(at(x, -0.85), Size::new(cx * 1.8, cy * 0.4), Color::from_rgb8(0x4f, 0xc3, 0xf7));
        }

        let banner = match g.state {
            State::Ready => Some(("THAW INVADERS".to_string(), "Space to start · ← → move · Space fires".to_string())),
            State::Pause => Some(("PAUSED".to_string(), "Esc to resume".to_string())),
            State::Over => Some((
                "GAME OVER".to_string(),
                format!("Score {} · wave {} · New Game: Ctrl+N", g.score, g.wave),
            )),
            State::Play => None,
        };
        if let Some((title, hint)) = banner {
            let card = Path::rounded_rectangle(at(COLS as f32 * 0.15, ROWS as f32 * 0.38), Size::new(w * 0.7, cy * 4.0), 12.0.into());
            frame.fill(&card, Color::from_rgba(0.02, 0.05, 0.1, 0.88));
            label(&mut frame, title, at(COLS as f32 / 2.0, ROWS as f32 * 0.38 + 1.3), cy * 1.1, Color::from_rgb8(0xff, 0xd6, 0x5c), Alignment::Center);
            label(&mut frame, hint, at(COLS as f32 / 2.0, ROWS as f32 * 0.38 + 2.8), cy * 0.5, white, Alignment::Center);
        }
        vec![frame.into_geometry()]
    }
}
