//! The "Printers" window (libcosmic).
//!
//! Two pages. The main one lists the printers (status, supplies, Print test
//! page, Pause/Resume, Make default, Remove…), the print queue (Cancel,
//! Hold/Resume) and the last few finished documents. "Add printer…" opens the
//! second: the printers CUPS finds on USB and the network, or an address to
//! type, then a name, a location and a driver (driverless when the printer
//! supports it).
//!
//! Every CUPS request runs on its own thread and reports through a channel: a
//! search for printers waits on mDNS for seconds, and the driver list is
//! thousands of entries. The main page re-reads CUPS every two seconds, so
//! jobs move through the queue on screen.

use std::time::Duration;

use cosmic::iced::futures::channel::mpsc;
use cosmic::iced::{Alignment, Length, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::section;
use cosmic::widget::{self, button, column, container, icon, row, text};

use crate::cups::Cups;
use crate::{Device, Driver, Found, Job, Printer, Snapshot, APP_ID};

/// What the window was asked to do at startup.
#[derive(Clone, Debug, Default)]
pub struct Flags {
    /// Open on the Add page (`--add`, the launcher's "Add a printer" action).
    pub add: bool,
}

/// How long CUPS' backends look for printers (the network ones wait on mDNS).
const SEARCH_SECONDS: i32 = 8;

#[derive(Debug, Clone)]
pub enum Message {
    Refresh,
    Loaded(Result<Snapshot, String>),
    TestPage(String),
    Pause(String),
    Resume(String),
    MakeDefault(String),
    AskRemove(String),
    ConfirmRemove,
    CloseDialog,
    CancelJob(i32),
    HoldJob(i32),
    ReleaseJob(i32),
    CancelAll,
    /// An action finished: Ok(what to tell the user) or Err(why it failed).
    Done(Result<String, String>),
    OpenAdd,
    CloseAdd,
    Search,
    Devices(Result<Vec<Device>, String>),
    Drivers(Result<Vec<Driver>, String>),
    Pick(usize),
    /// Back from setting up a printer to the list of found ones.
    Unpick,
    Address(String),
    UseAddress,
    Name(String),
    Location(String),
    DriverPick(usize),
    DriverSearch(String),
    ConfirmAdd,
    Added(Result<String, String>),
}

/// The Add page.
#[derive(Default)]
struct Adding {
    searching: bool,
    found: Vec<Found>,
    devices: Vec<Device>,
    /// Every driver CUPS has; loaded once per visit, in the background.
    drivers: Option<Vec<Driver>>,
    drivers_error: Option<String>,
    error: Option<String>,
    address: String,
    address_error: Option<String>,
    picked: Option<Device>,
    name: String,
    location: String,
    choices: Vec<Driver>,
    /// How many of `choices` were matched to the printer (the rest come
    /// from the driver search).
    matched: usize,
    choice_titles: Vec<String>,
    choice: usize,
    search: String,
    working: bool,
}

pub struct App {
    core: cosmic::Core,
    cups: Cups,
    snap: Snapshot,
    loaded: bool,
    refreshing: bool,
    busy: bool,
    error: Option<String>,
    note: Option<String>,
    remove: Option<String>,
    adding: Option<Adding>,
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

impl App {
    fn printer(&self, queue: &str) -> Option<&Printer> {
        self.snap.printers.iter().find(|p| p.name == queue)
    }

    fn title_of(&self, queue: &str) -> String {
        self.printer(queue).map(Printer::title).unwrap_or_else(|| queue.replace('_', " "))
    }

    fn refresh(&mut self) -> Task<cosmic::Action<Message>> {
        if self.refreshing {
            return Task::none();
        }
        self.refreshing = true;
        let cups = self.cups.clone();
        background(move |send| send(Message::Loaded(cups.snapshot())))
    }

    /// Run one CUPS action in the background; `done` is what to say when it works.
    fn act(
        &mut self,
        done: String,
        job: impl FnOnce(&Cups) -> Result<(), String> + Send + 'static,
    ) -> Task<cosmic::Action<Message>> {
        self.busy = true;
        self.error = None;
        self.note = None;
        let cups = self.cups.clone();
        background(move |send| send(Message::Done(job(&cups).map(|()| done))))
    }

    fn open_add(&mut self) -> Task<cosmic::Action<Message>> {
        self.error = None;
        self.note = None;
        self.adding = Some(Adding::default());
        let cups = self.cups.clone();
        let drivers = background(move |send| send(Message::Drivers(cups.drivers())));
        Task::batch([self.search(), drivers])
    }

    fn search(&mut self) -> Task<cosmic::Action<Message>> {
        let Some(a) = self.adding.as_mut() else { return Task::none() };
        a.searching = true;
        a.error = None;
        let cups = self.cups.clone();
        background(move |send| send(Message::Devices(cups.devices(SEARCH_SECONDS))))
    }

    fn taken(&self) -> Vec<String> {
        self.snap.printers.iter().map(|p| p.name.clone()).collect()
    }

    /// Pick a printer to set up (found, or typed) and prefill its name.
    fn pick(&mut self, device: Device) {
        let Some(a) = self.adding.as_mut() else { return };
        a.name = device.title();
        a.location = device.location.clone();
        a.picked = Some(device);
        a.error = None;
        a.choice = 0;
        a.search.clear();
        recompute_choices(a);
    }

    fn add(&mut self) -> Task<cosmic::Action<Message>> {
        let taken = self.taken();
        let no_default = !self.snap.printers.iter().any(|p| p.is_default);
        let Some(a) = self.adding.as_mut() else { return Task::none() };
        let (Some(device), Some(driver)) = (a.picked.clone(), a.choices.get(a.choice).cloned()) else {
            return Task::none();
        };
        let info = a.name.trim().to_string();
        let queue = crate::queue_name(&info, &taken);
        let location = a.location.trim().to_string();
        a.working = true;
        a.error = None;
        let cups = self.cups.clone();
        background(move |send| {
            let r = cups.add(&queue, &device.uri, &driver.name, &info, &location).map(|()| {
                // The first printer becomes the default, so apps that print
                // straight to "the default" find it.
                if no_default && cups.set_default(&queue).is_ok() {
                    format!("{info} is ready, and is the default printer. Print a test page to check it.")
                } else {
                    format!("{info} is ready. Print a test page to check it.")
                }
            });
            send(Message::Added(r));
        })
    }
}

/// The driver dropdown: what suits the picked printer, plus search hits.
fn recompute_choices(a: &mut Adding) {
    let Some(device) = a.picked.as_ref() else {
        a.choices.clear();
        a.choice_titles.clear();
        a.matched = 0;
        return;
    };
    let all = a.drivers.as_deref().unwrap_or(&[]);
    let mut choices = crate::drivers_for(device, all);
    // A typed IPP address: driverless is the one driver that fits any IPP
    // Everywhere printer, and cups-filters makes it by asking the printer.
    if crate::speaks_ipp(&device.uri) && !choices.iter().any(Driver::is_driverless) {
        choices.insert(0, Driver::driverless_for(&device.uri));
    }
    a.matched = choices.len();
    for d in crate::search_drivers(all, &a.search, 40) {
        if !choices.iter().any(|c| c.name == d.name) {
            choices.push(d);
        }
    }
    let keep = a.choices.get(a.choice).map(|d| d.name.clone());
    a.choice = keep.and_then(|k| choices.iter().position(|c| c.name == k)).unwrap_or(0);
    a.choice_titles = choices.iter().map(Driver::title).collect();
    a.choices = choices;
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
            cups: Cups::new(),
            snap: Snapshot::default(),
            loaded: false,
            refreshing: false,
            busy: false,
            error: None,
            note: None,
            remove: None,
            adding: None,
        };
        app.set_header_title("Printers".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Printers".into(), id),
            None => Task::none(),
        };
        let refresh = app.refresh();
        let add = if flags.add { app.open_add() } else { Task::none() };
        (app, Task::batch([title, refresh, add]))
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Refresh => return self.refresh(),
            Message::Loaded(r) => {
                self.refreshing = false;
                self.loaded = true;
                match r {
                    Ok(s) => {
                        self.snap = s;
                        // A refresh that works clears a "cannot reach CUPS".
                        if self.error.as_deref().is_some_and(|e| e.starts_with("Cannot reach")) {
                            self.error = None;
                        }
                    }
                    Err(e) => self.error = Some(e),
                }
            }
            Message::TestPage(q) => {
                let title = self.title_of(&q);
                self.busy = true;
                self.error = None;
                self.note = None;
                let cups = self.cups.clone();
                return background(move |send| {
                    let r = cups.test_page(&q).map(|id| {
                        format!("Test page sent to {title} (job {id}). It is in the queue below until it has printed.")
                    });
                    send(Message::Done(r));
                });
            }
            Message::Pause(q) => {
                let t = format!("{} is paused: documents wait in the queue until you resume it.", self.title_of(&q));
                return self.act(t, move |c| c.pause(&q));
            }
            Message::Resume(q) => {
                let t = format!("{} is printing again.", self.title_of(&q));
                return self.act(t, move |c| c.resume(&q));
            }
            Message::MakeDefault(q) => {
                let t = format!("{} is the default printer.", self.title_of(&q));
                return self.act(t, move |c| c.set_default(&q));
            }
            Message::AskRemove(q) => self.remove = Some(q),
            Message::ConfirmRemove => {
                if let Some(q) = self.remove.take() {
                    let t = format!("{} is removed.", self.title_of(&q));
                    return self.act(t, move |c| c.delete(&q));
                }
            }
            Message::CloseDialog => self.remove = None,
            Message::CancelJob(id) => return self.act("Cancelled.".into(), move |c| c.cancel(id)),
            Message::HoldJob(id) => {
                return self.act("On hold: it stays in the queue until you resume it.".into(), move |c| c.hold(id))
            }
            Message::ReleaseJob(id) => return self.act("Released: it prints in turn.".into(), move |c| c.release(id)),
            Message::CancelAll => {
                let ids: Vec<i32> = self.snap.queue.iter().map(|j| j.id).collect();
                let n = ids.len();
                return self.act(format!("Cancelled {n} documents."), move |c| {
                    ids.iter().try_for_each(|id| c.cancel(*id))
                });
            }
            Message::Done(r) => {
                self.busy = false;
                match r {
                    Ok(n) => self.note = Some(n),
                    Err(e) => self.error = Some(e),
                }
                return self.refresh();
            }
            Message::OpenAdd => return self.open_add(),
            Message::CloseAdd => self.adding = None,
            Message::Search => return self.search(),
            Message::Devices(r) => {
                let printers = self.snap.printers.clone();
                if let Some(a) = self.adding.as_mut() {
                    a.searching = false;
                    match r {
                        Ok(d) => {
                            a.found = crate::found_printers(&d, &printers);
                            a.devices = d;
                        }
                        Err(e) => a.error = Some(e),
                    }
                }
            }
            Message::Drivers(r) => {
                if let Some(a) = self.adding.as_mut() {
                    match r {
                        Ok(d) => a.drivers = Some(d),
                        Err(e) => {
                            a.drivers = Some(Vec::new());
                            a.drivers_error = Some(e);
                        }
                    }
                    recompute_choices(a);
                }
            }
            Message::Pick(i) => {
                if let Some(d) = self.adding.as_ref().and_then(|a| a.found.get(i)).map(|f| f.device.clone()) {
                    self.pick(d);
                }
            }
            Message::Unpick => {
                if let Some(a) = self.adding.as_mut() {
                    a.picked = None;
                    a.error = None;
                    recompute_choices(a);
                }
            }
            Message::Address(s) => {
                if let Some(a) = self.adding.as_mut() {
                    a.address = s;
                    a.address_error = None;
                }
            }
            Message::UseAddress => {
                let Some(a) = self.adding.as_mut() else { return Task::none() };
                match crate::uri_from_address(&a.address) {
                    Ok(uri) => {
                        let host = uri.split("://").nth(1).and_then(|r| r.split('/').next()).unwrap_or("").to_string();
                        self.pick(Device {
                            uri,
                            class: "network".into(),
                            info: host.clone(),
                            make_model: format!("Printer at {host}"),
                            ..Default::default()
                        });
                    }
                    Err(e) => a.address_error = Some(e),
                }
            }
            Message::Name(s) => {
                if let Some(a) = self.adding.as_mut() {
                    a.name = s;
                }
            }
            Message::Location(s) => {
                if let Some(a) = self.adding.as_mut() {
                    a.location = s;
                }
            }
            Message::DriverPick(i) => {
                if let Some(a) = self.adding.as_mut() {
                    a.choice = i.min(a.choices.len().saturating_sub(1));
                }
            }
            Message::DriverSearch(s) => {
                if let Some(a) = self.adding.as_mut() {
                    a.search = s;
                    recompute_choices(a);
                }
            }
            Message::ConfirmAdd => return self.add(),
            Message::Added(Ok(note)) => {
                self.adding = None;
                self.error = None;
                self.note = Some(note);
                return self.refresh();
            }
            Message::Added(Err(e)) => {
                if let Some(a) = self.adding.as_mut() {
                    a.working = false;
                    a.error = Some(e);
                }
            }
        }
        Task::none()
    }

    fn subscription(&self) -> Subscription<Message> {
        // Jobs move through the queue and printers change state on their own.
        cosmic::iced::time::every(Duration::from_secs(2)).map(|_| Message::Refresh)
    }

    fn dialog(&self) -> Option<Element<'_, Message>> {
        let q = self.remove.as_ref()?;
        let p = self.printer(q);
        let title = self.title_of(q);
        let waiting = self.snap.queue.iter().filter(|j| &j.printer == q).count();
        let mut body = format!("{title} will no longer be offered in print dialogs.");
        if waiting > 0 {
            body.push_str(&format!(
                " {waiting} {} waiting for it will be cancelled.",
                if waiting == 1 { "document" } else { "documents" }
            ));
        }
        if p.is_some_and(Printer::automatic) {
            body.push_str(" It was added automatically, and comes back while it is on your network.");
        }
        Some(
            widget::dialog()
                .title(format!("Remove {title}?"))
                .body(body)
                .primary_action(button::destructive("Remove").on_press(Message::ConfirmRemove))
                .secondary_action(button::standard("Cancel").on_press(Message::CloseDialog))
                .into(),
        )
    }

    fn view(&self) -> Element<'_, Message> {
        if let Some(a) = &self.adding {
            return self.page(self.view_add(a));
        }
        let spacing = cosmic::theme::spacing();
        let mut body: Vec<Element<'_, Message>> = Vec::new();
        body.push(
            row::with_children([
                text::body("Printers you can use from any app.").width(Length::Fill).into(),
                button::suggested("Add printer…")
                    .leading_icon(icon::from_name("list-add-symbolic").size(16))
                    .on_press(Message::OpenAdd)
                    .into(),
            ])
            .spacing(spacing.space_s)
            .align_y(Alignment::Center)
            .into(),
        );
        if let Some(e) = &self.error {
            body.push(text::body(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        if let Some(n) = &self.note {
            body.push(text::body(n.clone()).into());
        }
        body.push(self.view_printers());
        if !self.snap.printers.is_empty() || !self.snap.queue.is_empty() {
            body.push(self.view_queue());
        }
        if !self.snap.history.is_empty() {
            body.push(self.view_history());
        }
        self.page(body)
    }
}

impl App {
    fn page<'a>(&self, body: Vec<Element<'a, Message>>) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        let content = column::with_children(body).spacing(spacing.space_m).max_width(680);
        // The right padding keeps the scrollbar off the buttons at the edge.
        let centered = container(content)
            .width(Length::Fill)
            .padding([0, spacing.space_m, 0, 0])
            .align_x(cosmic::iced::alignment::Horizontal::Center);
        container(widget::scrollable(centered))
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(spacing.space_m)
            .into()
    }

    fn view_printers(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        if self.snap.printers.is_empty() {
            let msg = if self.loaded {
                "No printers yet. Printers on your network that support driverless printing \
                 (IPP Everywhere, AirPrint) appear here by themselves; for any other, choose \
                 Add printer…"
            } else {
                "Looking for printers…"
            };
            return container(text::body(msg)).padding(spacing.space_m).into();
        }
        let idle = !self.busy;
        let mut s = section().title("Printers");
        for p in &self.snap.printers {
            let mut lines: Vec<Element<'_, Message>> = Vec::new();
            let mut head = vec![text::heading(p.title()).into()];
            if p.is_default {
                head.push(text::caption("Default").class(cosmic::theme::Text::Accent).into());
            }
            lines.push(row::with_children(head).spacing(spacing.space_xs).align_y(Alignment::Center).into());
            let mut what = vec![p.model(), p.connection().to_string()];
            if !p.location.trim().is_empty() {
                what.push(p.location.trim().to_string());
            }
            what.retain(|w| !w.is_empty());
            lines.push(text::caption(what.join(" · ")).into());
            lines.push(text::body(p.status()).into());
            if let Some(sup) = p.supplies() {
                lines.push(text::caption(sup).into());
            }
            if p.automatic() {
                lines.push(text::caption("Added automatically: it is on your network.").into());
            }
            let q = p.name.clone();
            let mut buttons: Vec<Element<'_, Message>> = vec![button::standard("Print test page")
                .on_press_maybe(idle.then(|| Message::TestPage(q.clone())))
                .into()];
            buttons.push(if p.paused() {
                button::standard("Resume").on_press_maybe(idle.then(|| Message::Resume(q.clone()))).into()
            } else {
                button::standard("Pause").on_press_maybe(idle.then(|| Message::Pause(q.clone()))).into()
            });
            if !p.is_default {
                buttons.push(
                    button::standard("Make default").on_press_maybe(idle.then(|| Message::MakeDefault(q.clone()))).into(),
                );
            }
            buttons.push(button::destructive("Remove…").on_press_maybe(idle.then(|| Message::AskRemove(q.clone()))).into());
            lines.push(widget::flex_row(buttons).spacing(spacing.space_xs).into());
            let icon_name = match (p.connection(), p.state) {
                (_, crate::PrinterState::Printing) => "printer-printing",
                (_, crate::PrinterState::Stopped) if !p.reasons.iter().any(|r| r == "paused") => "printer-error",
                ("Network", _) => "printer-network",
                _ => "printer",
            };
            s = s.add(
                row::with_children([
                    icon::from_name(icon_name).size(40).into(),
                    column::with_children(lines).spacing(spacing.space_xxs).width(Length::Fill).into(),
                ])
                .spacing(spacing.space_s)
                .align_y(Alignment::Start),
            );
        }
        s.into()
    }

    fn job_line(&self, j: &Job) -> String {
        let mut parts = vec![self.title_of(&j.printer)];
        if !j.user.is_empty() {
            parts.push(j.user.clone());
        }
        parts.push(crate::size_text(j.kbytes));
        parts.push(crate::ago(crate::now(), j.created));
        parts.retain(|p| !p.is_empty());
        parts.join(" · ")
    }

    fn view_queue(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let idle = !self.busy;
        let mut s = section().title("Print queue");
        if self.snap.queue.is_empty() {
            return s.add(text::body("Nothing is waiting to print.")).into();
        }
        for j in &self.snap.queue {
            let p = self.printer(&j.printer);
            let id = j.id;
            let hold: Element<'_, Message> = if j.held() {
                button::standard("Resume").on_press_maybe(idle.then_some(Message::ReleaseJob(id))).into()
            } else {
                button::standard("Hold")
                    .on_press_maybe((idle && j.state == crate::JobState::Pending).then_some(Message::HoldJob(id)))
                    .into()
            };
            let info = column::with_children([
                text::heading(j.title()).into(),
                text::caption(self.job_line(j)).into(),
                text::body(j.status(p)).into(),
            ])
            .spacing(spacing.space_xxs)
            .width(Length::Fill);
            s = s.add(
                row::with_children([
                    info.into(),
                    hold,
                    button::destructive("Cancel").on_press_maybe(idle.then_some(Message::CancelJob(id))).into(),
                ])
                .spacing(spacing.space_xs)
                .align_y(Alignment::Center),
            );
        }
        if self.snap.queue.len() > 1 {
            s = s.add(
                row::with_children([
                    cosmic::iced::widget::space::horizontal().into(),
                    button::destructive("Cancel all").on_press_maybe(idle.then_some(Message::CancelAll)).into(),
                ])
                .width(Length::Fill),
            );
        }
        s.into()
    }

    fn view_history(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut s = section().title("Recently printed");
        for j in &self.snap.history {
            let line = format!("{} · {}", self.job_line(j), j.status(None));
            s = s.add(
                column::with_children([text::body(j.title()).into(), text::caption(line).into()])
                    .spacing(spacing.space_xxs)
                    .width(Length::Fill),
            );
        }
        s.into()
    }

    /// The Add page: first choose a printer, then set it up. Setting up is its
    /// own step so its form is in view at once (below the list it sat under
    /// the fold of the default window, and picking seemed to do nothing).
    fn view_add<'a>(&'a self, a: &'a Adding) -> Vec<Element<'a, Message>> {
        let spacing = cosmic::theme::spacing();
        let mut body: Vec<Element<'a, Message>> = Vec::new();
        let (back, heading) = match &a.picked {
            Some(_) => (Message::Unpick, "Set up the printer"),
            None => (Message::CloseAdd, "Add a printer"),
        };
        body.push(
            row::with_children([
                button::standard("Back")
                    .leading_icon(icon::from_name("go-previous-symbolic").size(16))
                    .on_press_maybe((!a.working).then_some(back))
                    .into(),
                text::title4(heading).into(),
            ])
            .spacing(spacing.space_s)
            .align_y(Alignment::Center)
            .into(),
        );
        if let Some(e) = &a.error {
            body.push(text::body(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        if let Some(dev) = &a.picked {
            body.push(self.view_setup(a, dev));
            return body;
        }

        // --- found printers ---------------------------------------------------
        let mut found = section().title("Printers found on USB and your network");
        if a.searching {
            found = found.add(
                column::with_children([
                    text::body("Looking for printers…").into(),
                    widget::indeterminate_linear().width(Length::Fill).into(),
                ])
                .spacing(spacing.space_xs),
            );
        } else if a.found.is_empty() {
            found = found.add(text::body(
                "None found. Check that the printer is switched on and connected (USB, or the \
                 same network as this computer), then search again — or type its address below.",
            ));
        }
        for (i, f) in a.found.iter().enumerate() {
            let mut desc = vec![f.connection().to_string()];
            if f.device.driverless() {
                desc.push("driverless".into());
            }
            if let Some(name) = &f.added_as {
                desc.push(format!("already added as {name}"));
            }
            let icon_name = if f.connection() == "Network" { "printer-network" } else { "printer" };
            let item = row::with_children([
                icon::from_name(icon_name).size(32).into(),
                column::with_children([text::heading(f.device.title()).into(), text::caption(desc.join(" · ")).into()])
                    .spacing(spacing.space_xxs)
                    .width(Length::Fill)
                    .into(),
            ])
            .spacing(spacing.space_s)
            .align_y(Alignment::Center);
            found = found.add(
                widget::list::button(item).on_press_maybe((!a.working).then_some(Message::Pick(i))),
            );
        }
        found = found.add(
            row::with_children([
                cosmic::iced::widget::space::horizontal().into(),
                button::standard("Search again")
                    .leading_icon(icon::from_name("view-refresh-symbolic").size(16))
                    .on_press_maybe((!a.searching && !a.working).then_some(Message::Search))
                    .into(),
            ])
            .width(Length::Fill),
        );
        body.push(found.into());

        // --- by address -----------------------------------------------------------
        let mut by_address = vec![
            row::with_children([
                widget::text_input("192.168.1.20, printer.local or ipp://…", a.address.as_str())
                    .on_input(Message::Address)
                    .on_submit(|_| Message::UseAddress)
                    .width(Length::Fill)
                    .into(),
                button::standard("Use this address")
                    .on_press_maybe((!a.working && !a.address.trim().is_empty()).then_some(Message::UseAddress))
                    .into(),
            ])
            .spacing(spacing.space_xs)
            .align_y(Alignment::Center)
            .into(),
            text::caption("For a printer that is not found: on another network, or one that does not announce itself.")
                .into(),
        ];
        if let Some(e) = &a.address_error {
            by_address.push(text::caption(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        body.push(
            section()
                .title("Or type its address")
                .add(column::with_children(by_address).spacing(spacing.space_xxs))
                .into(),
        );
        body
    }

    fn view_setup<'a>(&'a self, a: &'a Adding, dev: &'a Device) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        let mut s = section().title(dev.title());
        let mut how = vec![crate::connection_of(&dev.uri).to_string()];
        if dev.driverless() {
            how.push("driverless".into());
        }
        how.push(crate::percent_decode(&dev.uri));
        s = s.add(text::caption(how.join(" · ")));
        s = s.add(widget::settings::item(
            "Name",
            widget::text_input("Shown in print dialogs", a.name.as_str())
                .on_input(Message::Name)
                .width(Length::Fixed(320.0)),
        ));
        s = s.add(widget::settings::item(
            "Location",
            widget::text_input("Optional, e.g. Office", a.location.as_str())
                .on_input(Message::Location)
                .width(Length::Fixed(320.0)),
        ));

        let mut driver: Vec<Element<'a, Message>> = Vec::new();
        if a.choices.is_empty() {
            let msg = if a.drivers.is_none() {
                "Loading the driver list…"
            } else {
                "No driver matches this printer. Search for its make and model below (a \
                 similar model of the same make often works)."
            };
            driver.push(text::body(msg).into());
        } else {
            driver.push(widget::dropdown(&a.choice_titles, Some(a.choice), Message::DriverPick).into());
            let picked = &a.choices[a.choice];
            let about = if picked.is_driverless() {
                "Recommended: CUPS builds the driver from what the printer reports about itself, \
                 so nothing model-specific is needed."
            } else if a.choice == 0 && a.matched > 0 {
                "The best match for this printer."
            } else if a.choice < a.matched {
                "Also made for this printer."
            } else {
                "Found by your search: check that it is for your model."
            };
            driver.push(text::caption(about).into());
        }
        if let Some(e) = &a.drivers_error {
            driver.push(text::caption(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        driver.push(
            widget::search_input("Find another driver, e.g. HP LaserJet 1020", a.search.as_str())
                .on_input(Message::DriverSearch)
                .on_clear(Message::DriverSearch(String::new()))
                .into(),
        );
        if a.drivers.is_none() && !a.search.trim().is_empty() {
            driver.push(text::caption("Still loading the driver list…").into());
        }
        s = s.add(
            column::with_children([text::body("Driver").into(), column::with_children(driver).spacing(spacing.space_xs).into()])
                .spacing(spacing.space_xs),
        );

        let check = if a.name.trim().is_empty() { Err("Give the printer a name.".to_string()) } else { Ok(()) };
        let mut foot: Vec<Element<'a, Message>> = Vec::new();
        if let Err(e) = &check {
            foot.push(text::caption(e.clone()).class(cosmic::theme::Text::Accent).width(Length::Fill).into());
        } else if a.working {
            foot.push(
                column::with_children([
                    text::caption("Adding… (a driverless printer is asked about itself; this can take a moment)").into(),
                    widget::indeterminate_linear().width(Length::Fill).into(),
                ])
                .spacing(spacing.space_xxs)
                .width(Length::Fill)
                .into(),
            );
        } else {
            foot.push(cosmic::iced::widget::space::horizontal().into());
        }
        let can = check.is_ok() && !a.working && !a.choices.is_empty();
        foot.push(button::suggested("Add printer").on_press_maybe(can.then_some(Message::ConfirmAdd)).into());
        s = s.add(row::with_children(foot).spacing(spacing.space_s).align_y(Alignment::Center).width(Length::Fill));
        s.into()
    }
}
