//! The "Gamepads" window (libcosmic).
//!
//! Lists the controllers the system reports, and drives a live picture of the
//! selected one: buttons light up, sticks follow, triggers and pedals fill, the
//! D-pad points. Every evdev read runs on its own thread and reports through a
//! channel (a read blocks until something happens), and the device list is
//! re-scanned every two seconds so plugging a pad in is noticed.
//!
//! The picture's *geometry* is pure and tested in `layout.rs`; this file is the
//! drawing and the wiring.

use std::collections::BTreeSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, RwLock};
use std::time::Duration;

use cosmic::iced::alignment::{Horizontal, Vertical};
use cosmic::iced::futures::channel::mpsc;
use cosmic::iced::widget::canvas::{self, Canvas, Frame, Path, Program};
use cosmic::iced::{Color, Length, Pixels, Point, Rectangle, Size, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::section;
use cosmic::widget::{self, button, column, container, icon, row, text};

use crate::layout::{self, Layout, Piece};
use crate::raw::{self, Device};
use crate::{
    axis_name, button_name, hat_name, Caps, Kind, Pad, PadState, Rumble, APP_ID,
};

/// What the window was asked to do at startup.
#[derive(Clone, Debug, Default)]
pub struct Flags {
    /// Drive the panel with a synthetic pad: the app's own end-to-end test.
    pub selftest: bool,
    /// Open with this device selected.
    pub device: Option<std::path::PathBuf>,
}

#[derive(Debug, Clone)]
pub enum Message {
    Select(String),
    Refresh,
    Pads(Result<Vec<Pad>, String>),
    Events(Result<Vec<crate::Ev>, String>),
    Ranges(Result<Vec<(u16, crate::AxisInfo)>, String>),
    Rumble,
    Strength(i32),
    Rumbled(Result<String, String>),
    /// Hand the events the reader thread collected to the panel.
    Drain(Vec<crate::Ev>),
    ClearLog,
    Reset,
    Tick,
}

pub struct App {
    core: cosmic::Core,
    pads: Vec<Pad>,
    loaded: bool,
    selected: Option<String>,
    state: PadState,
    ranges: Vec<(u16, crate::AxisInfo)>,
    rx: Option<mpsc::UnboundedReceiver<crate::Ev>>,
    /// The device `--selftest`/the command line asked to open, applied when
    /// the first enumeration lands.
    startup_device: Option<std::path::PathBuf>,
    log: Vec<String>,
    error: Option<String>,
    rumble_note: Option<String>,
    rumbling: Arc<AtomicBool>,
    strength: i32,
    generation: u64,
    selftest: bool,
    selftest_step: u64,
}

/// Run `job` on its own thread; it sends its messages, the last being the answer.
fn background(job: impl FnOnce(&dyn Fn(Message)) + Send + 'static) -> Task<cosmic::Action<Message>> {
    let (tx, rx) = mpsc::unbounded::<Message>();
    std::thread::spawn(move || {
        let send = |m: Message| {
            let _ = tx.unbounded_send(m);
        };
        job(&send);
    });
    cosmic::task::stream(rx)
}

fn pad_by_path(pads: &[Pad], path: &str) -> Option<Pad> {
    pads.iter().find(|p| p.path == path).cloned()
}

fn first_controller(pads: &[Pad]) -> Option<String> {
    pads.iter()
        .find(|p| p.kind.is_controller() && p.testable())
        .or_else(|| pads.iter().find(|p| p.testable()))
        .map(|p| p.path.clone())
}

/// The device to open at startup: the one named on the command line (a
/// `.desktop` file can pass a file, and a terminal user types a node), else
/// the first controller.
fn start_device(pads: &[Pad], wanted: Option<&std::path::Path>) -> Option<String> {
    wanted
        .and_then(|w| pads.iter().find(|p| std::path::Path::new(&p.path) == w))
        .map(|p| p.path.clone())
        .or_else(|| first_controller(pads))
}

/// The synthetic pad `--selftest` draws: a complete modern gamepad, so the
/// panel can be exercised (and screenshotted) with no hardware attached.
fn selftest_pad() -> Pad {
    let mut caps = Caps::default();
    caps.ev = [crate::EV_KEY, crate::EV_ABS, crate::EV_MSC].into_iter().collect();
    caps.keys = [
        crate::BTN_A,
        crate::BTN_B,
        crate::BTN_X,
        crate::BTN_Y,
        crate::BTN_TL,
        crate::BTN_TR,
        crate::BTN_SELECT,
        crate::BTN_START,
        crate::BTN_MODE,
        crate::BTN_THUMBL,
        crate::BTN_THUMBR,
    ]
    .into_iter()
    .collect();
    caps.abs = [
        crate::ABS_X,
        crate::ABS_Y,
        crate::ABS_RX,
        crate::ABS_RY,
        crate::ABS_Z,
        crate::ABS_RZ,
        crate::ABS_HAT0X,
        crate::ABS_HAT0Y,
    ]
    .into_iter()
    .collect();
    caps.ff = [0x50].into_iter().collect();
    crate::DeviceInfo {
        path: "/dev/input/selftest".into(),
        sys: "input-selftest".into(),
        name: "Synthetic test pad".into(),
        phys: "usb-selftest/input0".into(),
        modalias: String::new(),
        caps,
    }
    .pad()
}

/// One step of the synthetic pad's animation: press something, move something.
fn selftest_events(step: u64) -> Vec<crate::Ev> {
    use crate::Ev;
    let t = step as f32;
    let wave = (t / 6.0).sin();
    match step % 12 {
        0 => vec![Ev::Key { code: crate::BTN_A, pressed: true }],
        1 => vec![Ev::Key { code: crate::BTN_A, pressed: false }, Ev::Key { code: crate::BTN_B, pressed: true }],
        2 => vec![Ev::Key { code: crate::BTN_B, pressed: false }, Ev::Key { code: crate::BTN_X, pressed: true }],
        3 => vec![Ev::Key { code: crate::BTN_X, pressed: false }, Ev::Key { code: crate::BTN_Y, pressed: true }],
        4 => vec![Ev::Key { code: crate::BTN_Y, pressed: false }, Ev::Abs { code: crate::ABS_X, value: (wave * 30000.0) as i32 }],
        5 => vec![Ev::Abs { code: crate::ABS_Y, value: (wave * -20000.0) as i32 }],
        6 => vec![Ev::Abs { code: crate::ABS_RX, value: (wave * 32000.0) as i32 }],
        7 => vec![Ev::Abs { code: crate::ABS_RY, value: (wave * 12000.0) as i32 }],
        8 => vec![Ev::Abs { code: crate::ABS_Z, value: ((wave +1.0) * 127.0) as i32 }],
        9 => vec![Ev::Abs { code: crate::ABS_RZ, value: ((1.0 - wave) * 127.0) as i32 }],
        10 => vec![
            Ev::Abs { code: crate::ABS_HAT0X, value: -1 },
            Ev::Abs { code: crate::ABS_HAT0Y, value: -1 },
        ],
        _ => vec![
            Ev::Abs { code: crate::ABS_HAT0X, value: 0 },
            Ev::Abs { code: crate::ABS_HAT0Y, value: 0 },
            Ev::Key { code: crate::BTN_START, pressed: true },
        ],
    }
}

/// The node the reader thread should have open, and the channel it reports on.
///
/// The reader is one thread per selected device (an evdev read blocks until
/// something happens, so it cannot run on the UI thread). The app hands it the
/// node through this lock and drains its channel on a 40 ms timer: a test panel
/// that lags a frame is invisible, and the app never blocks.
static READER_PATH: RwLock<String> = RwLock::new(String::new());
static READER_TX: RwLock<Option<mpsc::UnboundedSender<crate::Ev>>> = RwLock::new(None);

fn set_reader(path: Option<&str>, tx: Option<mpsc::UnboundedSender<crate::Ev>>) {
    if let Ok(mut p) = READER_PATH.write() {
        *p = path.unwrap_or_default().to_string();
    }
    if let Ok(mut t) = READER_TX.write() {
        *t = tx;
    }
}

fn reader_endpoint() -> Option<mpsc::UnboundedSender<crate::Ev>> {
    READER_TX.read().ok().and_then(|t| t.clone())
}

/// Spawn the reader for one device. It exits when the device goes away, when it
/// cannot be opened, or when the app drops the channel (a new selection).
fn spawn_reader(path: String) {
    std::thread::spawn(move || {
        let Some(pad) = raw::pads().ok().and_then(|pads| pad_by_path(&pads, &path)) else {
            return;
        };
        let Ok(mut device) = Device::open(&pad) else { return };
        let tx = reader_endpoint();
        loop {
            match device.read(Duration::from_millis(60)) {
                Ok(events) => {
                    if events.is_empty() {
                        continue;
                    }
                    let Some(tx) = tx.as_ref() else { return };
                    for ev in events {
                        if tx.unbounded_send(ev).is_err() {
                            return;
                        }
                    }
                }
                Err(e) => {
                    // ENODEV: unplugged. The app notices the device vanished on
                    // its next list refresh, so the thread just stops.
                    if Pad::is_gone_message(&e) {
                        return;
                    }
                }
            }
        }
    });
}

impl App {
    fn selected_pad(&self) -> Option<Pad> {
        self.selected.as_deref().and_then(|p| pad_by_path(&self.pads, p))
    }

    fn refresh(&self) -> Task<cosmic::Action<Message>> {
        background(|send| send(Message::Pads(raw::pads())))
    }

    fn read_ranges(&self, pad: Pad) -> Task<cosmic::Action<Message>> {
        background(move |send| match Device::open(&pad) {
            Ok(device) => {
                let ranges = pad
                    .caps
                    .shown_axes()
                    .into_iter()
                    .map(|code| (code, device.axis_info(code)))
                    .collect();
                send(Message::Ranges(Ok(ranges)));
            }
            Err(e) => send(Message::Ranges(Err(crate::explain_open_error(&e)))),
        })
    }

    fn select(&mut self, path: &str) -> Task<cosmic::Action<Message>> {
        let Some(pad) = pad_by_path(&self.pads, path) else { return Task::none() };
        self.generation += 1;
        self.selected = Some(path.to_string());
        self.state = PadState::new(&pad);
        self.ranges = Vec::new();
        self.log.clear();
        self.error = None;
        self.rumble_note = None;
        if self.selftest {
            set_reader(None, None);
            return Task::none();
        }
        // Drop the old reader (its channel closes), then start the new one.
        let (tx, rx) = mpsc::unbounded::<crate::Ev>();
        set_reader(Some(path), Some(tx));
        self.rx = Some(rx);
        spawn_reader(path.to_string());
        self.read_ranges(pad)
    }

    fn rumble(&self) -> Task<cosmic::Action<Message>> {
        let Some(pad) = self.selected_pad() else { return Task::none() };
        // strength is 0..100 percent of the kernel's 0..65535 magnitude.
        let level = crate::rumble_level(self.strength * 655);
        let effect = Rumble { strong: level, weak: level };
        self.rumbling.store(true, Ordering::Relaxed);
        background(move |send| {
            let msg = raw::rumble(&pad, effect.strong, effect.weak, Duration::from_millis(400));
            send(Message::Rumbled(match msg {
                Ok(()) => Ok(format!("{} rumbled.", pad.name)),
                Err(e) => Err(e),
            }));
        })
    }

    fn push_log(&mut self, ev: crate::Ev) {
        let line = self.state.describe(ev);
        self.log.insert(0, line);
        self.log.truncate(12);
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

    fn nav_model(&self) -> Option<&cosmic::widget::nav_bar::Model> {
        None
    }

    fn init(core: cosmic::Core, flags: Flags) -> (Self, Task<cosmic::Action<Message>>) {
        let mut app = App {
            core,
            pads: Vec::new(),
            loaded: false,
            selected: None,
            state: PadState::default(),
            ranges: Vec::new(),
            rx: None,
            startup_device: None,
            log: Vec::new(),
            error: None,
            rumble_note: None,
            rumbling: Arc::new(AtomicBool::new(false)),
            strength: 60,
            generation: 0,
            selftest: flags.selftest,
            selftest_step: 0,
        };
        app.set_header_title("Gamepads".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Gamepads".into(), id),
            None => Task::none(),
        };
        if app.selftest {
            let pad = selftest_pad();
            app.pads = vec![pad.clone()];
            app.loaded = true;
            app.selected = Some(pad.path.clone());
            app.state = PadState::new(&pad);
            app.ranges = pad
                .caps
                .shown_axes()
                .into_iter()
                .map(|code| {
                    (
                        code,
                        crate::AxisInfo {
                            min: if crate::is_signed_axis(code) { -32768 } else { 0 },
                            max: if crate::is_signed_axis(code) { 32767 } else { 255 },
                            ..Default::default()
                        },
                    )
                })
                .collect();
            return (app, title);
        }
        // A device named on the command line is opened as soon as the list
        // arrives; `select` needs the list, so the request is carried.
        app.startup_device = flags.device.clone();
        let refresh = app.refresh();
        (app, Task::batch([title, refresh]))
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Refresh => {
                // --selftest owns its device list: the synthetic pad is not a
                // node on the system, so a rescan would erase it.
                if self.selftest {
                    return Task::none();
                }
                return self.refresh();
            }
            Message::Pads(Ok(pads)) => {
                self.pads = pads;
                self.loaded = true;
                // Keep the selection when the device is still there; when it
                // is gone (unplugged) fall to the first controller, and when
                // nothing is left, clear the panel.
                let wanted = self.startup_device.take();
                let live = self
                    .selected
                    .as_deref()
                    .is_some_and(|p| pad_by_path(&self.pads, p).is_some());
                if !live {
                    match start_device(&self.pads, wanted.as_deref()) {
                        Some(path) => return self.select(&path),
                        None => {
                            self.selected = None;
                            self.state = PadState::default();
                        }
                    }
                }
            }
            Message::Pads(Err(e)) => self.error = Some(e),
            Message::Select(path) => return self.select(&path),
            Message::Drain(_) => {
                // Hand everything the reader thread collected to the panel.
                // Drain into a local first: the reader's channel is borrowed
                // from `self`, and applying an event mutates `self`.
                let mut events: Vec<crate::Ev> = Vec::new();
                if let Some(rx) = self.rx.as_mut() {
                    while let Ok(ev) = rx.try_recv() {
                        events.push(ev);
                    }
                }
                for ev in events {
                    if self.state.push(ev) {
                        self.push_log(ev);
                    }
                }
            }
            // The reader thread hands events through the channel (Drain);
            // Events only carries the failure that ends a reader.
            Message::Events(Ok(_)) => {}
            Message::Events(Err(e)) => {
                // The pad was unplugged: drop it from the list and say so.
                if Pad::is_gone_message(&e) {
                    let gone = self.selected.clone();
                    self.pads.retain(|p| Some(&p.path) != gone.as_ref());
                    self.selected = None;
                    set_reader(None, None);
                    self.error = Some(format!("{e}. Plug it back in and it will reappear."));
                } else {
                    self.error = Some(crate::explain_open_error(&e));
                }
            }
            Message::Ranges(Ok(ranges)) => self.ranges = ranges,
            Message::Ranges(Err(e)) => self.error = Some(e),
            Message::Rumble => return self.rumble(),
            Message::Strength(v) => self.strength = v,
            Message::Rumbled(r) => {
                self.rumbling.store(false, Ordering::Relaxed);
                match r {
                    Ok(note) => self.rumble_note = Some(note),
                    Err(e) => self.rumble_note = Some(e),
                }
            }
            Message::ClearLog => self.log.clear(),
            Message::Reset => {
                if let Some(pad) = self.selected_pad() {
                    self.state = PadState::new(&pad);
                    self.log.clear();
                }
            }
            Message::Tick => {
                if self.selftest {
                    self.selftest_step += 1;
                    for ev in selftest_events(self.selftest_step) {
                        if self.state.push(ev) {
                            self.push_log(ev);
                        }
                    }
                }
            }
        }
        Task::none()
    }

    fn subscription(&self) -> Subscription<Message> {
        // 40 ms: the panel follows the pad within a couple of frames, and the
        // UI thread never blocks on a device read. A tick drains the reader's
        // channel, or — in --selftest — advances the synthetic pad.
        // `Subscription::map` takes a non-capturing closure, so the tick is a
        // plain message and `update` decides what a tick means.
        let drain = cosmic::iced::time::every(Duration::from_millis(40)).map(|_| Message::Tick);
        // Notice pads being plugged in and out.
        let rescan = cosmic::iced::time::every(Duration::from_secs(2)).map(|_| Message::Refresh);
        Subscription::batch([drain, rescan])
    }

    fn view(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut body: Vec<Element<'_, Message>> = Vec::new();

        if let Some(e) = &self.error {
            body.push(text::body(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        body.push(self.view_pads());

        let Some(pad) = self.selected_pad() else {
            let msg = if self.loaded {
                "No game controllers. Plug one in — the list refreshes by itself."
            } else {
                "Looking for controllers…"
            };
            body.push(container(text::body(msg)).padding(spacing.space_m).into());
            return self.scroll(body);
        };

        body.push(self.view_canvas(&pad));
        if let Some(extra) = self.view_extra(&pad) {
            body.push(extra);
        }
        body.push(self.view_rumble(&pad));
        body.push(self.view_log());

        self.scroll(body)
    }
}

impl App {
    fn scroll<'a>(&self, body: Vec<Element<'a, Message>>) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        let content = column::with_children(body).spacing(spacing.space_m).max_width(860);
        let centered = container(content).width(Length::Fill).align_x(Horizontal::Center);
        container(widget::scrollable(centered))
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(spacing.space_m)
            .into()
    }

    fn view_pads(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        if self.pads.is_empty() {
            let msg = if self.loaded { "No input devices found." } else { "Looking…" };
            return container(text::body(msg)).padding(spacing.space_m).into();
        }
        let (controllers, other) = crate::split_pads(self.pads.clone());
        let mut s = section().title("Game controllers");
        if controllers.is_empty() {
            s = s.add(text::caption("Nothing that answers as a game controller."));
        }
        for p in &controllers {
            s = s.add(self.pad_row(p));
        }
        let mut out: Vec<Element<'_, Message>> = vec![s.into()];
        if !other.is_empty() {
            let mut s = section().title("Other input devices");
            for p in other.iter().take(12) {
                s = s.add(self.pad_row(p));
            }
            out.push(s.into());
        }
        column::with_children(out).spacing(spacing.space_m).into()
    }

    fn pad_row(&self, p: &Pad) -> widget::list::ListButton<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let selected = self.selected.as_deref() == Some(p.path.as_str());
        let mut desc = p.summary();
        if let Some(ids) = p.vid_pid() {
            desc.push_str(&format!(" · {ids}"));
        }
        let info = column::with_children([
            text::heading(p.name.clone()).into(),
            text::caption(desc).into(),
        ])
        .spacing(spacing.space_xxs)
        .width(Length::Fill);
        let row: Element<'_, Message> = row::with_children([
            icon::from_name(p.kind.icon()).size(36).into(),
            info.into(),
        ])
        .spacing(spacing.space_s)
        .align_y(cosmic::iced::Alignment::Center)
        .into();
        widget::list::button(row)
            .on_press(Message::Select(p.path.clone()))
            .selected(selected)
    }

    fn view_canvas(&self, pad: &Pad) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let layout = layout::layout(&pad.caps, pad.kind);
        // The canvas must be pinned to COSMIC's Theme/Renderer: iced's own
        // defaults would give this widget a different Element type than every
        // other widget in the window.
        let canvas = Canvas::<PadCanvas, Message, cosmic::Theme, cosmic::Renderer>::new(PadCanvas {
            layout,
            state: self.state.clone(),
            ranges: self.ranges.clone(),
            name: pad.name.clone(),
            kind: pad.kind,
            palette: palette(),
        })
        .width(Length::Fill)
        .height(Pixels(360.0));
        column::with_children([
            text::title4(pad.name.clone()).into(),
            text::caption(pad.summary()).into(),
            canvas.into(),
        ])
        .spacing(spacing.space_xxs)
        .into()
    }

    /// The controls the picture has no room for: extra axes as bars, switches,
    /// relative axes, and every button by name.
    fn view_extra(&self, pad: &Pad) -> Option<Element<'_, Message>> {
        let spacing = cosmic::theme::spacing();
        let layout = layout::layout(&pad.caps, pad.kind);
        let shown: BTreeSet<u16> = layout
            .elements
            .iter()
            .filter_map(|e| match e {
                Piece::Button { code, .. } => Some(*code),
                Piece::Bar { code, .. } => Some(*code),
                Piece::Stick { x, y: _, .. } => Some(*x),
                Piece::Hat { x, y: _, .. } => Some(*x),
                Piece::Label { .. } => None,
            })
            .collect();
        let mut rows: Vec<Element<'_, Message>> = Vec::new();

        for code in pad.caps.shown_axes() {
            if shown.contains(&code) {
                continue;
            }
            let info = self.axis_info(code);
            let fraction = self.state.norm(code, info);
            rows.push(
                row::with_children([
                    text::caption(axis_name(code).to_string()).width(Length::Fill).into(),
                    text::caption(format!("{}", self.state.axis(code).unwrap_or(0)))
                        .width(Length::Fill)
                        .into(),
                    widget::determinate_linear(fraction).width(Length::Fill).into(),
                ])
                .spacing(spacing.space_s).into(),
            );
        }
        for (code, on) in &self.state.switches {
            rows.push(
                row::with_children([
                    text::caption(format!("switch {code}")).width(Length::Fill).into(),
                    text::caption(if *on { "on" } else { "off" }).into(),
                ])
                .spacing(spacing.space_s).into(),
            );
        }
        for (code, value) in &self.state.rel {
            rows.push(
                row::with_children([
                    text::caption(format!("relative {}", axis_name(*code))).width(Length::Fill).into(),
                    text::caption(format!("{value}")).into(),
                ])
                .spacing(spacing.space_s).into(),
            );
        }
        let pressed: Vec<String> = self
            .state
            .buttons
            .iter()
            .filter(|(_, on)| *on)
            .map(|(code, _)| button_name(*code).to_string())
            .collect();
        rows.push(
            row::with_children([
                text::caption("Pressed now").width(Length::Fill).into(),
                text::caption(if pressed.is_empty() { "nothing".into() } else { pressed.join(", ") })
                    .width(Length::Fill)
                    .into(),
            ])
            .spacing(spacing.space_s).into(),
        );
        let hats: Vec<String> = self
            .state
            .hats
            .iter()
            .filter(|(_, _, dir)| *dir >= 0)
            .map(|(_, _, dir)| hat_name(*dir).to_string())
            .collect();
        if !hats.is_empty() {
            rows.push(
                row::with_children([
                    text::caption("D-pad").width(Length::Fill).into(),
                    text::caption(hats.join(", ")).width(Length::Fill).into(),
                ])
                .spacing(spacing.space_s).into(),
            );
        }
        if rows.is_empty() {
            return None;
        }
        Some(
            section()
                .title("Live values")
                .add(column::with_children(rows).spacing(spacing.space_xxs))
                .into(),
        )
    }

    fn view_rumble(&self, pad: &Pad) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let has_ff = pad.caps.ff.contains(&0x50);
        let rumbling = self.rumbling.load(Ordering::Relaxed);
        let strength_row: Element<'_, Message> = row::with_children([
            text::caption("Strength").width(Length::Fill).into(),
            widget::slider(0..=100, self.strength, Message::Strength).width(Length::Fill).into(),
            button::standard(if rumbling { "Rumbling…" } else { "Rumble" })
                .on_press_maybe((!rumbling && has_ff).then_some(Message::Rumble))
                .into(),
        ])
        .spacing(spacing.space_s)
        .into();
        let mut rows: Vec<Element<'_, Message>> = vec![strength_row];
        if let Some(note) = &self.rumble_note {
            rows.push(text::caption(note.clone()).into());
        }
        if !has_ff {
            rows.push(text::caption(
                "This device reports no rumble motors, so the button is inert.",
            ).into());
        }
        section()
            .title("Rumble")
            .add(column::with_children(rows).spacing(spacing.space_xxs))
            .into()
    }

    fn view_log(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut rows: Vec<Element<'_, Message>> = Vec::new();
        if self.log.is_empty() {
            rows.push(
                text::body("Press a button or move a stick on the selected device; every event lands here.")
                    .into(),
            );
        } else {
            for line in self.log.iter() {
                rows.push(widget::selectable_text(line.as_str()).into());
            }
        }
        let header_row: Element<'_, Message> = row::with_children([
            text::title4("Events").width(Length::Fill).into(),
            button::text("Clear")
                .on_press_maybe((!self.log.is_empty()).then_some(Message::ClearLog))
                .into(),
            button::text("Reset state").on_press(Message::Reset).into(),
        ])
        .spacing(spacing.space_s)
        .width(Length::Fill)
        .into();
        section()
            .title("Events")
            .header(header_row)
            .add(column::with_children(rows).spacing(spacing.space_xxs))
            .into()
    }

    fn axis_info(&self, code: u16) -> crate::AxisInfo {
        self.ranges
            .iter()
            .find(|(c, _)| *c == code)
            .map(|(_, i)| *i)
            .unwrap_or_default()
    }
}


/// The picture: the layout, painted with the live state.
/// The colours the panel paints with, taken from the active COSMIC theme.
#[derive(Clone, Copy, Debug)]
pub struct Palette {
    pub base: Color,
    pub accent: Color,
    pub on_bg: Color,
}

/// The colours of the theme the app is drawing under.
fn palette() -> Palette {
    let active = cosmic::theme::active();
    let cosmic_theme = active.cosmic();
    Palette {
        base: cosmic_theme.bg_component_color().into(),
        accent: cosmic_theme.accent_color().into(),
        on_bg: cosmic_theme.on_bg_color().into(),
    }
}

struct PadCanvas {
    layout: Layout,
    state: PadState,
    ranges: Vec<(u16, crate::AxisInfo)>,
    name: String,
    kind: Kind,
    palette: Palette,
}

impl PadCanvas {
    fn info(&self, code: u16) -> crate::AxisInfo {
        self.ranges
            .iter()
            .find(|(c, _)| *c == code)
            .map(|(_, i)| *i)
            .unwrap_or_default()
    }
}

impl<Message> Program<Message, cosmic::Theme> for PadCanvas {
    type State = ();

    fn draw(
        &self,
        _state: &(),
        renderer: &cosmic::Renderer,
        _theme: &cosmic::Theme,
        bounds: Rectangle,
        _cursor: cosmic::iced::mouse::Cursor,
    ) -> Vec<canvas::Geometry> {
        let mut frame = Frame::new(renderer, bounds.size());
        let w = bounds.size().width;
        let h = bounds.size().height;
        // The canvas program receives iced's own Theme enum, which carries no
        // COSMIC palette; the colours are taken from the active COSMIC theme in
        // `view_canvas` and carried in.
        let (base, accent, on_bg) = (self.palette.base, self.palette.accent, self.palette.on_bg);

        // The body the controls sit on.
        let (bx, by) = layout::scale(40.0, 130.0, w, h);
        let (bw, bh) = (
            layout::scale_len(920.0, w, h),
            layout::scale_len(400.0, w, h),
        );
        frame.fill_rectangle(
            Point::new(bx, by),
            Size::new(bw, bh),
            base,
        );

        for element in &self.layout.elements {
            match element {
                Piece::Button { code, x, y, r, label } => {
                    let (cx, cy) = layout::scale(*x, *y, w, h);
                    let rr = layout::scale_len(*r, w, h);
                    let pressed = self.state.pressed(*code);
                    let path = Path::rounded_rectangle(
                        Point::new(cx - rr, cy - rr),
                        Size::new(rr * 2.0, rr * 2.0),
                        rr.into(),
                    );
                    frame.fill(&path, if pressed { accent } else { base });
                    frame.stroke(
                        &path,
                        canvas::Stroke::default()
                            .with_color(on_bg)
                            .with_width(1.0),
                    );
                    frame.fill_text(canvas::Text {
                        content: label.clone(),
                        position: Point::new(cx, cy),
                        max_width: rr * 2.4,
                        color: (if pressed { on_bg } else { on_bg }),
                        size: (rr * 0.7).into(),
                        line_height: Default::default(),
                        font: Default::default(),
                        align_x: Horizontal::Center.into(),
                        align_y: Vertical::Center,
                        shaping: Default::default(),
                    });
                }
                Piece::Stick { x, y, cx, cy, r } => {
                    let (cx, cy) = layout::scale(*cx, *cy, w, h);
                    let rr = layout::scale_len(*r, w, h);
                    let ring = Path::circle(Point::new(cx, cy), rr);
                    frame.fill(&ring, base);
                    frame.stroke(
                        &ring,
                        canvas::Stroke::default().with_color(on_bg).with_width(1.0),
                    );
                    let nx = self.state.norm(*x, self.info(*x));
                    let ny = self.state.norm(*y, self.info(*y));
                    let moved = nx.abs() > 0.02 || ny.abs() > 0.02;
                    let knob = Path::circle(Point::new(cx + nx * rr * 0.6, cy + ny * rr * 0.6), rr * 0.42);
                    frame.fill(
                        &knob,
                        if moved { accent } else { on_bg },
                    );
                }
                Piece::Hat { x, y, cx, cy, arm, len } => {
                    let (cx, cy) = layout::scale(*cx, *cy, w, h);
                    let a = layout::scale_len(*arm, w, h);
                    let l = layout::scale_len(*len, w, h);
                    let dir = self
                        .state
                        .hats
                        .iter()
                        .find(|(hx, hy, _)| *hx == *x && *hy == *y)
                        .map(|(_, _, d)| *d)
                        .unwrap_or(-1);
                    let colour = if dir == -1 { base } else { accent };
                    frame.fill_rectangle(Point::new(cx - a / 2.0, cy - l / 2.0), Size::new(a, l), colour);
                    frame.fill_rectangle(Point::new(cx - l / 2.0, cy - a / 2.0), Size::new(l, a), colour);
                }
                Piece::Bar { code, x, y, w: bw, h: bh, label, label_y } => {
                    let (bx, by) = layout::scale(*x, *y, w, h);
                    let (lx, ly) = layout::scale(*x, *label_y, w, h);
                    let bw = layout::scale_len(*bw, w, h);
                    let bh = layout::scale_len(*bh, w, h);
                    let fraction = self.state.norm(*code, self.info(*code));
                    frame.fill_rectangle(Point::new(bx, by), Size::new(bw, bh), base);
                    frame.fill_rectangle(
                        Point::new(bx, by),
                        Size::new(bw * fraction, bh),
                        if fraction > 0.02 { accent } else { base },
                    );
                    frame.stroke_rectangle(
                        Point::new(bx, by),
                        Size::new(bw, bh),
                        canvas::Stroke::default().with_color(on_bg).with_width(1.0),
                    );
                    frame.fill_text(canvas::Text {
                        content: format!("{} {}", label, self.state.axis(*code).unwrap_or(0)),
                        position: Point::new(lx, ly),
                        max_width: bw * 2.0,
                        color: (on_bg),
                        size: layout::scale_len(18.0, w, h).into(),
                        line_height: Default::default(),
                        font: Default::default(),
                        align_x: Horizontal::Left.into(),
                        align_y: Vertical::Center,
                        shaping: Default::default(),
                    });
                }
                Piece::Label { x, y, text, size } => {
                    let (x, y) = layout::scale(*x, *y, w, h);
                    frame.fill_text(canvas::Text {
                        content: text.clone(),
                        position: Point::new(x, y),
                        max_width: w,
                        color: (on_bg),
                        size: (*size).into(),
                        line_height: Default::default(),
                        font: Default::default(),
                        align_x: Horizontal::Center.into(),
                        align_y: Vertical::Center,
                        shaping: Default::default(),
                    });
                }
            }
        }

        // The stick values, printed where the sticks are.
        for (code, x, y) in &self.layout.readout {
            let (x, y) = layout::scale(*x, *y, w, h);
            frame.fill_text(canvas::Text {
                content: format!("{} {}", axis_name(*code), self.state.axis(*code).unwrap_or(0)),
                position: Point::new(x, y),
                max_width: layout::scale_len(240.0, w, h),
                color: (on_bg),
                size: layout::scale_len(19.0, w, h).into(),
                line_height: Default::default(),
                font: Default::default(),
                align_x: Horizontal::Center.into(),
                align_y: Vertical::Center,
                shaping: Default::default(),
            });
        }

        // The kind, so a screenshot says what the device was.
        let (x, y) = layout::scale(500.0, 24.0, w, h);
        frame.fill_text(canvas::Text {
            content: format!("{} · {}", self.kind.title(), self.name),
            position: Point::new(x, y),
            max_width: w,
            color: (on_bg),
            size: layout::scale_len(20.0, w, h).into(),
            line_height: Default::default(),
            font: Default::default(),
            align_x: Horizontal::Center.into(),
            align_y: Vertical::Center,
            shaping: Default::default(),
        });

        vec![frame.into_geometry()]
    }
}
