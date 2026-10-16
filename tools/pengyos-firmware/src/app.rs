//! The "Firmware" window (libcosmic).
//!
//! It reads what root's daily check saved (`/var/lib/pengyos-firmware/state.json`)
//! and offers the updates fwupd found. Installing is a systemd unit started
//! through `systemctl` (polkit lets administrators do it without a password, see
//! `60-pengyos-firmware.rules`); the unit runs `fwupdmgr` as root, streams
//! `/run/pengyos-firmware/progress.json`, and never restarts the computer by
//! itself. Nothing here writes to hardware without the person pressing the
//! button: firmware updates are irreversible.

use std::path::Path;
use std::process::Command;
use std::time::{Duration, SystemTime};

use cosmic::iced::futures::channel::mpsc;
use cosmic::iced::{Alignment, Length, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::{item, section};
use cosmic::widget::{self, button, column, container, nav_bar, row, text};

use crate::{
    ago, apply_running, human_size, now, start_unit, until, FirmwareState, Prefs, Progress, Running, SNOOZES,
    STATE, UNIT_CHECK,
};

#[derive(Clone, Debug, PartialEq)]
enum Busy {
    Idle,
    Checking,
    Installing,
}

#[derive(Debug, Clone)]
pub enum Message {
    CheckNow,
    /// Install these devices (all of them when the list is empty).
    Update(Vec<String>),
    Snooze(i64),
    ClearSnooze,
    SetNotify(bool),
    Restart,
    Progress(Progress),
    /// A privileged action finished (Err = what to tell the user).
    Done(Result<(), String>),
    Refresh,
}

pub struct App {
    core: cosmic::Core,
    running: Running,
    state: FirmwareState,
    prefs: Prefs,
    busy: Busy,
    progress: Option<Progress>,
    error: Option<String>,
    note: Option<String>,
}

fn reboot() {
    let _ = Command::new("systemctl").arg("reboot").spawn();
}

impl App {
    fn reload(&mut self) {
        self.running = Running::load();
        self.state = FirmwareState::load(Path::new(STATE));
        self.prefs = Prefs::load();
    }

    fn save_prefs(&mut self) {
        if let Err(e) = self.prefs.save() {
            self.error = Some(format!("Could not save your settings: {e}"));
        }
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
    fn install(&mut self, ids: Vec<String>) -> Task<cosmic::Action<Message>> {
        self.busy = Busy::Installing;
        self.error = None;
        self.note = None;
        self.progress = Some(Progress { phase: "start".into(), message: "Starting…".into(), ..Default::default() });
        let unit = crate::update_unit(&ids);
        let (tx, rx) = mpsc::unbounded::<Message>();
        std::thread::spawn(move || {
            let started = SystemTime::now();
            let send = |m: Message| {
                let _ = tx.unbounded_send(m);
            };
            let job = std::thread::spawn(move || start_unit(&unit));
            let fresh = || {
                let meta = std::fs::metadata(crate::PROGRESS).ok()?;
                (meta.modified().ok()? >= started).then(|| std::fs::read_to_string(crate::PROGRESS).ok())?
            };
            while !job.is_finished() {
                if let Some(p) = fresh().and_then(|t| Progress::parse(&t)) {
                    send(Message::Progress(p));
                }
                std::thread::sleep(Duration::from_millis(400));
            }
            let result = job.join().unwrap_or(Err("crashed".into()));
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

impl cosmic::Application for App {
    type Executor = cosmic::executor::Default;
    type Flags = ();
    type Message = Message;
    const APP_ID: &'static str = crate::APP_ID;

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
            state: FirmwareState::default(),
            prefs: Prefs::default(),
            busy: Busy::Idle,
            progress: None,
            error: None,
            note: None,
        };
        app.reload();
        let mut tasks = vec![match app.core.main_window_id() {
            Some(id) => app.set_window_title("Firmware".into(), id),
            None => Task::none(),
        }];
        // An install started earlier (or elsewhere) is still running: follow it.
        if apply_running() {
            tasks.push(app.install(Vec::new()));
        }
        (app, Task::batch(tasks))
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::CheckNow => return self.start(UNIT_CHECK.into(), Busy::Checking),
            Message::Update(ids) => return self.install(ids),
            Message::Snooze(secs) => {
                let key = self.state.key();
                self.prefs.snooze(&key, secs, now());
                self.save_prefs();
            }
            Message::ClearSnooze => {
                self.prefs.clear_snooze();
                self.save_prefs();
            }
            Message::SetNotify(on) => {
                self.prefs.notify = on;
                self.save_prefs();
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
                    Ok(()) if was == Busy::Installing => {
                        self.note = Some("Anything that needs a restart says so above.".into());
                    }
                    Ok(()) => {}
                }
                if was == Busy::Installing && self.error.is_some() {
                    self.progress = None;
                }
            }
            Message::Refresh => {
                if self.busy == Busy::Idle {
                    self.reload();
                    // Started elsewhere (another window, a terminal): follow it.
                    if apply_running() {
                        return self.install(Vec::new());
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
        if let Some(devices) = self.view_devices() {
            body.push(devices);
        }
        body.push(self.view_settings());
        let content = column::with_children(body).spacing(spacing.space_m).max_width(620);
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
            .add(item::builder(checked).description("PengyOS asks fwupd for firmware updates every day.").control(check))
            .into()
    }

    fn view_main(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let t = now();
        let mut c: Vec<Element<'_, Message>> = Vec::new();

        if self.busy == Busy::Installing {
            let p = self.progress.clone().unwrap_or_default();
            c.push(text::title4("Installing firmware").into());
            c.push(widget::determinate_linear(p.fraction).width(Length::Fill).into());
            c.push(text::body(p.message).into());
            c.push(
                text::caption("Do not turn this computer off while it is updating firmware. Some updates apply while it is switched off.").into(),
            );
        } else if !self.state.pending_reboot().is_empty() {
            let names = FirmwareState::names(&self.state.pending_reboot());
            c.push(text::title4("Restart to finish a firmware update").into());
            c.push(text::body(format!("{names} is waiting for a restart to take its new firmware.")).into());
            c.push(row::with_children([button::suggested("Restart now").on_press(Message::Restart).into()]).into());
        } else if !self.state.offers().is_empty() {
            let offers = self.state.offers();
            let n = offers.len();
            c.push(
                text::title4(match n {
                    1 => "A firmware update is available".to_string(),
                    k => format!("{k} firmware updates are available"),
                })
                .into(),
            );
            let idle = self.busy == Busy::Idle;
            for d in offers.iter() {
                c.push(self.view_device(d));
            }
            let ids: Vec<String> = offers.iter().map(|d| d.id.clone()).collect();
            c.push(
                button::suggested(match n {
                    1 => "Update it".to_string(),
                    k => format!("Update all {k}"),
                })
                .on_press_maybe(idle.then_some(Message::Update(ids)))
                .into(),
            );
            c.push(text::caption("Firmware updates are written into the hardware and cannot be undone. The computer restarts only when you tell it to.").into());
            match self.prefs.snoozed(&self.state.key(), t) {
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
            c.push(text::title4("All firmware is up to date").into());
            c.push(
                text::caption(format!(
                    "{} devices on this computer can be updated by fwupd.",
                    self.state.updatable().len()
                ))
                .into(),
            );
            if let Some(n) = &self.note {
                c.push(text::body(n.clone()).into());
            }
        }
        if let Some(e) = &self.state.error {
            c.push(text::caption(format!("The last check failed: {e}")).into());
        }
        container(column::with_children(c).spacing(spacing.space_s))
            .padding(spacing.space_s)
            .into()
    }

    /// One device with an update: what it is, what it goes to, and what it needs.
    fn view_device(&self, d: &crate::Device) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let Some(a) = d.offer() else {
            return text::caption(d.name.clone()).into();
        };
        let mut c: Vec<Element<'_, Message>> = vec![
            row::with_children([
                text::title4(d.name.clone()).into(),
                container(text::body(format!("{} → {}", d.current, a.version)))
                    .width(Length::Fill)
                    .align_x(Alignment::End)
                    .into(),
            ])
            .into(),
        ];
        if let Some(u) = a.urgency_word() {
            c.push(text::body(u).into());
        }
        if a.size > 0 {
            c.push(text::caption(format!("{} to install", human_size(a.size))).into());
        }
        for para in a.notes() {
            c.push(text::body(para).into());
        }
        for w in d.warnings() {
            c.push(text::caption(w).into());
        }
        if !d.update_error.is_empty() {
            c.push(text::caption(format!("Last attempt: {}", d.update_error)).into());
        }
        // Several devices on offer: each one can also be installed alone.
        let update = button::standard("Update just this one").on_press_maybe(
            (self.busy == Busy::Idle && self.state.offers().len() > 1)
                .then_some(Message::Update(vec![d.id.clone()])),
        );
        c.push(update.into());
        container(column::with_children(c).spacing(spacing.space_xs))
            .padding(spacing.space_xs)
            .into()
    }

    /// Every device fwupd can update, with what it is running.
    fn view_devices(&self) -> Option<Element<'_, Message>> {
        let devices = self.state.updatable();
        if devices.is_empty() {
            return None;
        }
        let mut s = section().title("This computer");
        for d in devices {
            let offered = d.offer().map(|a| a.version.clone()).unwrap_or_default();
            let right = if offered.is_empty() {
                d.current.clone()
            } else {
                format!("{} → {}", d.current, offered)
            };
            s = s.add(item::builder(d.name.clone()).control(text::body(right)));
        }
        Some(s.into())
    }

    fn view_settings(&self) -> Element<'_, Message> {
        section()
            .title("Settings")
            .add(
                item::builder("Tell me about firmware updates")
                    .description("Off: no notifications. PengyOS still checks daily, and this app shows what is available.")
                    .control(widget::toggler(self.prefs.notify).on_toggle(Message::SetNotify)),
            )
            .into()
    }
}
