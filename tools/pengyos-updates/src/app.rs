//! The "PengyOS Updates" window (libcosmic).
//!
//! Everything privileged is a systemd unit started through `systemctl` (polkit
//! lets administrators do it without a password, see 60-pengyos-update.rules):
//! check now, install, go back, switch channel. Installing streams
//! `/run/pengyos-update/progress.json` into a progress bar.

use std::path::Path;
use std::process::Command;
use std::time::{Duration, SystemTime};

use cosmic::iced::futures::channel::mpsc;
use cosmic::iced::{Alignment, Length, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::{item, section};
use cosmic::widget::{self, button, column, container, nav_bar, row, text};

use crate::{
    ago, channel_unit, now, start_unit, until, Prefs, Progress, Running, SystemState, APP_ID, PROGRESS,
    SNOOZES, STATE, UNIT_APPLY, UNIT_CHECK, UNIT_ROLLBACK,
};

#[derive(Clone, Debug, PartialEq)]
enum Busy {
    Idle,
    Checking,
    Installing,
    Other(String),
}

#[derive(Debug, Clone)]
pub enum Message {
    CheckNow,
    /// Install, then restart when it's done (`true`) or leave that to the user.
    Update { restart: bool },
    /// While installing: don't restart at the end after all.
    KeepWorking,
    Snooze(i64),
    ClearSnooze,
    SetNotify(bool),
    SetTesting(bool),
    Rollback,
    Restart,
    Progress(Progress),
    /// A privileged action finished (Err = what to tell the user).
    Done(Result<(), String>),
    Refresh,
}

pub struct App {
    core: cosmic::Core,
    running: Running,
    state: SystemState,
    prefs: Prefs,
    busy: Busy,
    progress: Option<Progress>,
    error: Option<String>,
    note: Option<String>,
    /// Restart as soon as the install in progress finishes.
    restart_after: bool,
}

fn reboot() {
    let _ = Command::new("systemctl").arg("reboot").spawn();
}

impl App {
    fn reload(&mut self) {
        self.running = Running::load();
        self.state = SystemState::load(Path::new(STATE));
        self.prefs = Prefs::load();
    }

    fn save_prefs(&mut self) {
        if let Err(e) = self.prefs.save() {
            self.error = Some(format!("Could not save your settings: {e}"));
        }
    }

    fn name(&self) -> String {
        if self.running.name.is_empty() { "PengyOS".into() } else { self.running.name.clone() }
    }

    /// Run `systemctl start UNIT` off the UI thread.
    fn start(&mut self, unit: String, busy: Busy) -> Task<cosmic::Action<Message>> {
        self.busy = busy;
        self.error = None;
        cosmic::task::future(async move {
            let r = std::thread::spawn(move || start_unit(&unit)).join().unwrap_or(Err("crashed".into()));
            Message::Done(r)
        })
    }

    /// Start the install and stream its progress file until it finishes.
    fn install(&mut self) -> Task<cosmic::Action<Message>> {
        self.busy = Busy::Installing;
        self.error = None;
        self.note = None;
        self.progress = Some(Progress { phase: "start".into(), message: "Starting…".into(), ..Default::default() });
        let (tx, rx) = mpsc::unbounded::<Message>();
        std::thread::spawn(move || {
            let started = SystemTime::now();
            let send = |m: Message| {
                let _ = tx.unbounded_send(m);
            };
            let unit = std::thread::spawn(|| start_unit(UNIT_APPLY));
            let fresh = || {
                let meta = std::fs::metadata(PROGRESS).ok()?;
                (meta.modified().ok()? >= started).then(|| std::fs::read_to_string(PROGRESS).ok())?
            };
            while !unit.is_finished() {
                if let Some(p) = fresh().and_then(|t| Progress::parse(&t)) {
                    send(Message::Progress(p));
                }
                std::thread::sleep(Duration::from_millis(400));
            }
            let result = unit.join().unwrap_or(Err("crashed".into()));
            let last = fresh().and_then(|t| Progress::parse(&t));
            match (&result, last) {
                (_, Some(p)) if p.phase == "error" => send(Message::Done(Err(p.message))),
                (Ok(()), Some(p)) => {
                    send(Message::Progress(p));
                    send(Message::Done(Ok(())));
                }
                _ => send(Message::Done(result)),
            }
        });
        cosmic::task::stream(rx)
    }
}

fn apply_running() -> bool {
    Command::new("systemctl")
        .args(["is-active", "--quiet", UNIT_APPLY])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

impl cosmic::Application for App {
    type Executor = cosmic::executor::Default;
    type Flags = ();
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

    fn init(core: cosmic::Core, _flags: ()) -> (Self, Task<cosmic::Action<Message>>) {
        let mut app = App {
            core,
            running: Running::default(),
            state: SystemState::default(),
            prefs: Prefs::default(),
            busy: Busy::Idle,
            progress: None,
            error: None,
            note: None,
            restart_after: false,
        };
        app.reload();
        let mut tasks = vec![match app.core.main_window_id() {
            Some(id) => app.set_window_title("PengyOS Updates".into(), id),
            None => Task::none(),
        }];
        // An install started earlier (or elsewhere) is still running: follow it.
        if apply_running() {
            tasks.push(app.install());
        }
        (app, Task::batch(tasks))
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::CheckNow => return self.start(UNIT_CHECK.into(), Busy::Checking),
            Message::Update { restart } => {
                self.restart_after = restart;
                return self.install();
            }
            Message::KeepWorking => self.restart_after = false,
            Message::Snooze(secs) => {
                if let Some(v) = self.state.offer(&self.running.version).map(|a| a.version.clone()) {
                    self.prefs.snooze(&v, secs, now());
                    self.save_prefs();
                }
            }
            Message::ClearSnooze => {
                self.prefs.clear_snooze();
                self.save_prefs();
            }
            Message::SetNotify(on) => {
                self.prefs.notify = on;
                self.save_prefs();
            }
            Message::SetTesting(on) => {
                let ch = if on { "testing" } else { "stable" };
                return self.start(channel_unit(ch), Busy::Other(format!("Switching to {ch}…")));
            }
            Message::Rollback => {
                return self.start(UNIT_ROLLBACK.into(), Busy::Other("Going back…".into()));
            }
            Message::Restart => reboot(),
            Message::Progress(p) => self.progress = Some(p),
            Message::Done(r) => {
                let was = std::mem::replace(&mut self.busy, Busy::Idle);
                self.reload();
                match r {
                    Err(e) => self.error = Some(e),
                    Ok(()) if was == Busy::Checking && self.state.error.is_some() => {
                        self.error = self.state.error.clone().map(|e| format!("Could not check: {e}"));
                    }
                    Ok(()) if matches!(was, Busy::Other(ref s) if s.starts_with("Going back")) => {
                        self.note = Some("Restart to go back to the previous version.".into());
                    }
                    Ok(()) => {}
                }
                if was == Busy::Installing && self.error.is_some() {
                    self.progress = None;
                    self.restart_after = false;
                }
                if was == Busy::Installing && self.error.is_none() && self.restart_after {
                    reboot();
                }
            }
            Message::Refresh => {
                if self.busy == Busy::Idle {
                    self.reload();
                    // Started elsewhere (another window, a terminal): follow it.
                    if apply_running() {
                        return self.install();
                    }
                }
            }
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut body: Vec<Element<'_, Message>> = vec![self.view_status()];
        if let Some(e) = &self.error {
            body.push(text::body(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        body.push(self.view_main());
        body.push(self.view_settings());
        if let Some(adv) = self.view_previous() {
            body.push(adv);
        }
        let content = column::with_children(body).spacing(spacing.space_m).max_width(560);
        container(widget::scrollable(content))
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(spacing.space_m)
            .align_x(cosmic::iced::alignment::Horizontal::Center)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        // Pick up the daily check's answer while the window is open.
        cosmic::iced::time::every(Duration::from_secs(10)).map(|_| Message::Refresh)
    }
}

impl App {
    fn view_status(&self) -> Element<'_, Message> {
        let t = now();
        let checked = match (self.busy == Busy::Checking, self.state.checked) {
            (true, _) => "Checking…".to_string(),
            (false, Some(c)) => format!("Last checked {}", ago(c, t)),
            (false, None) => "Not checked yet".to_string(),
        };
        let check = button::standard("Check now")
            .on_press_maybe((self.busy == Busy::Idle).then_some(Message::CheckNow));
        section()
            .add(item::builder("This computer").control(text::body(format!("{} {}", self.name(), self.running.version))))
            .add(
                item::builder(checked)
                    .description("PengyOS checks for updates every day.")
                    .control(check),
            )
            .into()
    }

    fn view_main(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let t = now();
        let running = self.running.version.as_str();
        let mut c: Vec<Element<'_, Message>> = Vec::new();

        if self.busy == Busy::Installing {
            let p = self.progress.clone().unwrap_or_default();
            c.push(text::title4("Installing the update").into());
            c.push(widget::determinate_linear(p.fraction).width(Length::Fill).into());
            c.push(text::body(p.message).into());
            if self.restart_after {
                c.push(
                    row::with_children([
                        text::caption("The computer restarts by itself when the update is installed.").into(),
                        button::text("Don't restart").on_press(Message::KeepWorking).into(),
                    ])
                    .spacing(spacing.space_s)
                    .align_y(Alignment::Center)
                    .into(),
                );
            } else {
                c.push(text::caption("You can keep working. Nothing changes until you restart.").into());
            }
        } else if let Some(v) = self.state.pending_restart(running) {
            c.push(text::title4(format!("{} {v} is ready", self.name())).into());
            c.push(text::body("Restart to start using it. Your files, apps and settings stay as they are; if the new version does not start, the computer goes back to this one by itself.").into());
            c.push(
                row::with_children([
                    button::suggested("Restart now").on_press(Message::Restart).into(),
                ])
                .into(),
            );
            if let Some(n) = &self.note {
                c.push(text::caption(n.clone()).into());
            }
        } else if let Some(a) = self.state.offer(running) {
            c.push(text::title4(format!("{} {} is available", self.name(), a.version)).into());
            for para in a.notes.split("\n\n").filter(|p| !p.trim().is_empty()) {
                c.push(text::body(para.trim().to_string()).into());
            }
            let idle = self.busy == Busy::Idle;
            c.push(
                row::with_children([
                    button::suggested("Update and restart")
                        .on_press_maybe(idle.then_some(Message::Update { restart: true }))
                        .into(),
                    button::standard("Update, restart later")
                        .on_press_maybe(idle.then_some(Message::Update { restart: false }))
                        .into(),
                ])
                .spacing(spacing.space_s)
                .into(),
            );
            c.push(text::caption("It downloads and installs in the background, and takes effect when the computer restarts. Your files, apps and settings stay as they are.").into());
            match self.prefs.snoozed(&a.version, t) {
                Some(when) => c.push(
                    row::with_children([
                        text::body(format!("Reminders paused until {}.", until(when, t))).into(),
                        button::text("Remind me again").on_press(Message::ClearSnooze).into(),
                    ])
                    .spacing(spacing.space_s)
                    .align_y(Alignment::Center)
                    .into(),
                ),
                None => {
                    c.push(text::body("Not now? Remind me:").into());
                    let r: Vec<Element<'_, Message>> = SNOOZES
                        .iter()
                        .map(|(label, secs)| button::standard(*label).on_press(Message::Snooze(*secs)).into())
                        .collect();
                    c.push(row::with_children(r).spacing(spacing.space_xs).into());
                }
            }
        } else {
            c.push(text::title4(format!("{} is up to date", self.name())).into());
            if let Some(n) = &self.note {
                c.push(text::body(n.clone()).into());
            }
        }
        if let Busy::Other(what) = &self.busy {
            c.push(text::caption(what.clone()).into());
        }
        container(column::with_children(c).spacing(spacing.space_s))
            .padding(spacing.space_s)
            .into()
    }

    fn view_settings(&self) -> Element<'_, Message> {
        let testing = self.state.channel == "testing";
        let idle = self.busy == Busy::Idle;
        let mut t = widget::toggler(testing);
        if idle {
            t = t.on_toggle(Message::SetTesting);
        }
        section()
            .title("Settings")
            .add(
                item::builder("Tell me about updates")
                    .description("Off: no notifications. PengyOS still checks daily, and this app shows what is available.")
                    .control(widget::toggler(self.prefs.notify).on_toggle(Message::SetNotify)),
            )
            .add(
                item::builder("Get testing releases")
                    .description("New versions a few days early, before they reach everyone.")
                    .control(t),
            )
            .into()
    }

    fn view_previous(&self) -> Option<Element<'_, Message>> {
        let other = self.state.other.as_deref()?;
        if other == self.running.version || self.state.pending_restart(&self.running.version).is_some() {
            return None;
        }
        let back = button::standard("Go back")
            .on_press_maybe((self.busy == Busy::Idle).then_some(Message::Rollback));
        Some(
            section()
                .title("Previous version")
                .add(
                    item::builder(format!("{} {other}", self.name()))
                        .description("Still installed. Going back takes effect when you restart.")
                        .control(back),
                )
                .into(),
        )
    }
}
