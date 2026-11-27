//! The "Machines" window (libcosmic). v1, step 2 of spec.md: virtual machines
//! (pengyos-vm), with the built-in screen and disk snapshots. Every button runs
//! one pengyos-vm command (lib.rs says which), and what it ran is shown with
//! the result, so the window also teaches the command line.

use std::path::PathBuf;
use std::sync::mpsc::Sender;
use std::time::Duration;

use cosmic::dialog::file_chooser::{self, FileFilter};
use cosmic::iced::futures::channel::mpsc as fmpsc;
use cosmic::iced::widget::canvas;
use cosmic::iced::{Alignment, Length, Subscription};
use cosmic::iced::widget::image::Handle;
use cosmic::prelude::*;
use cosmic::widget::settings::{item, section};
use cosmic::widget::{self, button, column, container, nav_bar, row, text};

use crate::backend::{self, Input, ScreenEvent};
use crate::{shell_line, Iso, NewVm, Vm, VmAction};

#[derive(Debug, Clone)]
pub enum Field {
    Name,
    Image,
    Cpus,
    Memory,
    Disk,
    Forwards,
}

#[derive(Debug, Clone)]
pub enum Message {
    Refresh,
    Loaded(Result<(Vec<Vm>, Vec<Iso>), String>),
    Select(String),
    /// Ask first (the dialog), then run.
    Ask(String, VmAction),
    Act(String, VmAction),
    Done(String, Result<String, String>),
    SnapLabel(String),
    Terminal(Vec<String>),
    NewVm,
    NewField(Field, String),
    NewIso(usize),
    NewUseImage(bool),
    NewBridge(bool),
    NewSshKey(bool),
    NewBios(bool),
    PickImage,
    ImagePicked(Option<PathBuf>),
    AddIso,
    IsoPicked(Option<PathBuf>),
    IsoAdded(Result<String, String>),
    Create,
    Created(String, Result<String, String>),
    CloseDialog,
    OpenScreen(String),
    Screen(u64, ScreenEvent),
    ScreenInput(Input),
    ScreenFocus(bool),
    ScreenFit(bool),
    ScreenCtrlAltDel,
    CloseScreen,
}

enum Dialog {
    Confirm { title: String, body: String, vm: String, action: VmAction, destructive: bool },
    NewVm(NewVm),
}

struct Screen {
    vm: String,
    id: u64,
    tx: Option<Sender<Input>>,
    image: Option<Handle>,
    width: u16,
    height: u16,
    status: String,
    fit: bool,
    focused: bool,
}

pub struct App {
    core: cosmic::Core,
    vms: Vec<Vm>,
    isos: Vec<Iso>,
    iso_names: Vec<String>,
    selected: Option<String>,
    /// (VM, what it is doing): one command at a time per window.
    busy: Option<(String, &'static str)>,
    /// The last result: (text, the command that ran, failed?)
    result: Option<(String, String, bool)>,
    load_error: Option<String>,
    snap_label: String,
    dialog: Option<Dialog>,
    creating: Option<String>,
    screen: Option<Screen>,
    screens_opened: u64,
}

/// Run a command off the UI thread.
fn off_thread<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> impl std::future::Future<Output = T> {
    async move { std::thread::spawn(f).join().expect("worker thread") }
}

fn load() -> Task<cosmic::Action<Message>> {
    cosmic::task::future(async move {
        let r = off_thread(|| {
            let vms = backend::list_vms()?;
            let isos = backend::list_isos().unwrap_or_default();
            Ok((vms, isos))
        })
        .await;
        Message::Loaded(r)
    })
}

impl App {
    fn current(&self) -> Option<&Vm> {
        let name = self.selected.as_ref()?;
        self.vms.iter().find(|v| &v.name == name)
    }

    fn run_action(&mut self, vm: String, action: VmAction) -> Task<cosmic::Action<Message>> {
        let argv = action.argv(&vm);
        self.busy = Some((vm.clone(), action.doing()));
        self.result = None;
        if let VmAction::SnapshotCreate(_) = action {
            self.snap_label.clear();
        }
        // Rolling back or switching off ends the screen's connection anyway.
        if matches!(action, VmAction::ForceOff | VmAction::Stop | VmAction::SnapshotRevert(_)) {
            if let Some(s) = &self.screen {
                if s.vm == vm {
                    self.close_screen();
                }
            }
        }
        let line = shell_line(&argv);
        cosmic::task::future(async move {
            let r = off_thread(move || backend::run(&argv)).await;
            Message::Done(line, r)
        })
    }

    fn close_screen(&mut self) {
        if let Some(s) = self.screen.take() {
            if let Some(tx) = s.tx {
                let _ = tx.send(Input::Close);
            }
        }
    }

    fn open_screen(&mut self, vm: String) -> Task<cosmic::Action<Message>> {
        self.close_screen();
        let Some(v) = self.vms.iter().find(|x| x.name == vm) else { return Task::none() };
        self.screens_opened += 1;
        let id = self.screens_opened;
        let mut s = Screen {
            vm: vm.clone(),
            id,
            tx: None,
            image: None,
            width: 0,
            height: 0,
            status: "Connecting…".into(),
            fit: true,
            focused: false,
        };
        let (etx, erx) = fmpsc::unbounded::<Message>();
        match backend::open_screen(&v.vnc_socket, move |e| {
            let _ = etx.unbounded_send(Message::Screen(id, e));
        }) {
            Ok(tx) => s.tx = Some(tx),
            Err(e) => s.status = e,
        }
        self.screen = Some(s);
        cosmic::task::stream(erx)
    }

    fn send(&self, input: Input) {
        if let Some(tx) = self.screen.as_ref().and_then(|s| s.tx.as_ref()) {
            let _ = tx.send(input);
        }
    }

    fn form(&mut self) -> Option<&mut NewVm> {
        match self.dialog.as_mut() {
            Some(Dialog::NewVm(f)) => Some(f),
            _ => None,
        }
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
        // Dev boxes and containers come next (spec.md, steps 3 and 4); until
        // then there is one page and no sidebar.
        None
    }

    fn init(core: cosmic::Core, _flags: ()) -> (Self, Task<cosmic::Action<Message>>) {
        let mut app = App {
            core,
            vms: Vec::new(),
            isos: Vec::new(),
            iso_names: Vec::new(),
            selected: None,
            busy: None,
            result: None,
            load_error: None,
            snap_label: String::new(),
            dialog: None,
            creating: None,
            screen: None,
            screens_opened: 0,
        };
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Machines".into(), id),
            None => Task::none(),
        };
        (app, Task::batch([title, load()]))
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Refresh => return load(),
            Message::Loaded(Ok((vms, isos))) => {
                self.load_error = None;
                self.vms = vms;
                self.iso_names = isos.iter().map(|i| format!("{} ({})", i.name, i.size)).collect();
                self.isos = isos;
                if self.current().is_none() {
                    self.selected = self.vms.first().map(|v| v.name.clone());
                }
                // The VM stopped under the screen: say so rather than show a frozen picture.
                if let Some(s) = &mut self.screen {
                    if !self.vms.iter().any(|v| v.name == s.vm && v.running()) {
                        s.status = "The VM is not running.".into();
                    }
                }
            }
            Message::Loaded(Err(e)) => self.load_error = Some(e),
            Message::Select(name) => {
                if self.screen.as_ref().is_some_and(|s| s.vm != name) {
                    self.close_screen();
                }
                self.selected = Some(name);
                self.result = None;
            }
            Message::Ask(vm, action) => {
                let (title, body, destructive) = match &action {
                    VmAction::ForceOff => (
                        format!("Switch {vm} off?"),
                        "Like pulling the plug: anything not saved in it is lost.".to_string(),
                        true,
                    ),
                    VmAction::Delete => (
                        format!("Delete {vm}?"),
                        "Its disk and every snapshot are deleted. This cannot be undone.".to_string(),
                        true,
                    ),
                    VmAction::SnapshotRevert(s) => {
                        let v = self.vms.iter().find(|x| x.name == vm);
                        let when = v
                            .and_then(|v| v.snapshots.iter().find(|x| &x.name == s))
                            .map(|x| x.created.clone())
                            .unwrap_or_default();
                        let running = v.is_some_and(Vm::running);
                        (
                            format!("Roll {vm} back to {s}?"),
                            format!(
                                "Every change to its disk since {when} is lost.{}",
                                if running { " It is switched off and started again." } else { "" }
                            ),
                            true,
                        )
                    }
                    VmAction::SnapshotDelete(s) => (format!("Delete snapshot {s}?"), String::new(), false),
                    _ => return self.run_action(vm, action),
                };
                self.dialog = Some(Dialog::Confirm { title, body, vm, action, destructive });
            }
            Message::Act(vm, action) => {
                self.dialog = None;
                return self.run_action(vm, action);
            }
            Message::Done(line, r) => {
                self.busy = None;
                self.result = Some(match r {
                    Ok(out) => (out, line, false),
                    Err(e) => (e, line, true),
                });
                return load();
            }
            Message::SnapLabel(s) => self.snap_label = s,
            Message::Terminal(argv) => {
                if let Err(e) = backend::spawn(&crate::in_terminal(&argv)) {
                    self.result = Some((e, shell_line(&argv), true));
                }
            }
            Message::NewVm => {
                let mut f = NewVm::default();
                f.iso = self.isos.first().map(|i| i.name.clone()).unwrap_or_default();
                self.dialog = Some(Dialog::NewVm(f));
            }
            Message::NewField(field, v) => {
                if let Some(f) = self.form() {
                    match field {
                        Field::Name => f.name = v,
                        Field::Image => f.image = v,
                        Field::Cpus => f.cpus = v,
                        Field::Memory => f.memory = v,
                        Field::Disk => f.disk = v,
                        Field::Forwards => f.forwards = v,
                    }
                }
            }
            Message::NewIso(i) => {
                let name = self.isos.get(i).map(|x| x.name.clone());
                if let (Some(f), Some(n)) = (self.form(), name) {
                    f.iso = n;
                }
            }
            Message::NewUseImage(b) => {
                if let Some(f) = self.form() {
                    f.use_image = b;
                }
            }
            Message::NewBridge(b) => {
                if let Some(f) = self.form() {
                    f.bridge = b;
                }
            }
            Message::NewSshKey(b) => {
                if let Some(f) = self.form() {
                    f.ssh_key = b;
                }
            }
            Message::NewBios(b) => {
                if let Some(f) = self.form() {
                    f.bios = b;
                }
            }
            Message::PickImage => {
                let filter = ["qcow2", "img", "raw", "xz", "gz"]
                    .iter()
                    .fold(FileFilter::new("Disk images"), |f, e| f.glob(&format!("*.{e}")));
                let dialog = file_chooser::open::Dialog::new().title("Choose a disk image").filter(filter);
                return cosmic::task::future(async move {
                    Message::ImagePicked(dialog.open_file().await.ok().and_then(|r| r.url().to_file_path().ok()))
                });
            }
            Message::ImagePicked(Some(p)) => {
                if let Some(f) = self.form() {
                    f.image = p.display().to_string();
                }
            }
            Message::ImagePicked(None) | Message::IsoPicked(None) => {}
            Message::AddIso => {
                let filter = FileFilter::new("ISO images").glob("*.iso").glob("*.ISO");
                let dialog = file_chooser::open::Dialog::new().title("Add an installer ISO to the library").filter(filter);
                return cosmic::task::future(async move {
                    Message::IsoPicked(dialog.open_file().await.ok().and_then(|r| r.url().to_file_path().ok()))
                });
            }
            Message::IsoPicked(Some(p)) => {
                self.busy = Some((String::new(), "Adding the ISO"));
                let argv: Vec<String> = vec![crate::VM.into(), "iso".into(), "add".into(), p.display().to_string()];
                return cosmic::task::future(async move {
                    Message::IsoAdded(off_thread(move || backend::run(&argv)).await)
                });
            }
            Message::IsoAdded(r) => {
                self.busy = None;
                match r {
                    Ok(out) => {
                        // "added NAME (SIZE) to the ISO library": choose it in the form.
                        let name = out.strip_prefix("added ").and_then(|s| s.split(' ').next()).map(String::from);
                        if let (Some(f), Some(n)) = (self.form(), name) {
                            f.iso = n;
                        }
                        return load();
                    }
                    Err(e) => self.result = Some((e, "pengyos-vm iso add".into(), true)),
                }
            }
            Message::Create => {
                let Some(Dialog::NewVm(f)) = self.dialog.take() else { return Task::none() };
                let argv = f.argv();
                let line = shell_line(&argv);
                self.creating = Some(f.name.trim().to_string());
                self.result = None;
                return cosmic::task::future(async move {
                    Message::Created(line, off_thread(move || backend::run(&argv)).await)
                });
            }
            Message::Created(line, r) => {
                let name = self.creating.take();
                if r.is_ok() {
                    self.selected = name;
                }
                self.result = Some(match r {
                    Ok(out) => (out, line, false),
                    Err(e) => (e, line, true),
                });
                return load();
            }
            Message::CloseDialog => self.dialog = None,
            Message::OpenScreen(vm) => return self.open_screen(vm),
            Message::Screen(id, ev) => {
                let Some(s) = self.screen.as_mut().filter(|s| s.id == id) else { return Task::none() };
                match ev {
                    ScreenEvent::Ready { width, height, .. } => {
                        s.width = width;
                        s.height = height;
                        s.status = "Click the screen to type into the VM.".into();
                    }
                    ScreenEvent::Frame { width, height, rgba } => {
                        s.width = width;
                        s.height = height;
                        s.image = Some(Handle::from_rgba(width as u32, height as u32, rgba));
                    }
                    ScreenEvent::Bell => {}
                    ScreenEvent::Closed(why) => {
                        s.tx = None;
                        s.status = why.unwrap_or_else(|| "The screen closed (the VM stopped or restarted).".into());
                    }
                }
            }
            Message::ScreenInput(input) => {
                // A wheel notch is a press and a release.
                if let Input::Pointer { buttons, x, y } = input {
                    self.send(input.clone());
                    if buttons & 0x18 != 0 {
                        self.send(Input::Pointer { buttons: buttons & !0x18, x, y });
                    }
                } else {
                    self.send(input);
                }
            }
            Message::ScreenFocus(f) => {
                if let Some(s) = &mut self.screen {
                    s.focused = f;
                    if s.tx.is_some() {
                        s.status = if f {
                            "The VM has the keyboard: click outside the screen to give it back.".into()
                        } else {
                            "Click the screen to type into the VM.".into()
                        };
                    }
                }
            }
            Message::ScreenFit(f) => {
                if let Some(s) = &mut self.screen {
                    s.fit = f;
                }
            }
            Message::ScreenCtrlAltDel => {
                for (down, keysym, scancode) in crate::keymap::ctrl_alt_del() {
                    self.send(Input::Key { down, keysym, scancode });
                }
            }
            Message::CloseScreen => self.close_screen(),
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let body: Element<'_, Message> = match (&self.screen, self.current()) {
            (Some(s), Some(v)) if s.vm == v.name => self.view_screen(s),
            (_, Some(v)) => widget::scrollable(self.view_vm(v)).height(Length::Fill).into(),
            (_, None) => self.view_empty(),
        };
        row::with_children([self.view_list(), container(body).width(Length::Fill).height(Length::Fill).into()])
            .spacing(spacing.space_m)
            .padding(spacing.space_s)
            .into()
    }

    fn dialog(&self) -> Option<Element<'_, Message>> {
        let spacing = cosmic::theme::spacing();
        let cancel = button::standard("Cancel").on_press(Message::CloseDialog);
        let d = match self.dialog.as_ref()? {
            Dialog::Confirm { title, body, vm, action, destructive } => {
                let go = Message::Act(vm.clone(), action.clone());
                let label = match action {
                    VmAction::SnapshotRevert(_) => "Roll back",
                    VmAction::ForceOff => "Switch off",
                    _ => "Delete",
                };
                let primary = if *destructive { button::destructive(label) } else { button::suggested(label) };
                let mut d = widget::dialog().title(title.clone()).primary_action(primary.on_press(go));
                if !body.is_empty() {
                    d = d.body(body.clone());
                }
                d
            }
            Dialog::NewVm(f) => {
                let accent = |s: String| -> Element<'_, Message> { text::caption(s).class(cosmic::theme::Text::Accent).into() };
                let field = |label: &'static str, hint: &'static str, value: &str, which: Field| -> Element<'_, Message> {
                    column::with_children([
                        text::body(label).into(),
                        widget::text_input(hint, value.to_string())
                            .on_input(move |s| Message::NewField(which.clone(), s))
                            .into(),
                    ])
                    .spacing(spacing.space_xxxs)
                    .into()
                };
                let mut c: Vec<Element<'_, Message>> = vec![field("Name", "debian", &f.name, Field::Name)];
                c.push(
                    section()
                        .add(item::builder("Boot a disk image instead of an installer").control(
                            widget::toggler(f.use_image).on_toggle(Message::NewUseImage),
                        ))
                        .into(),
                );
                if f.use_image {
                    c.push(field("Disk image: a file, or an http(s) URL", "https://…/debian-13-genericcloud-amd64.qcow2", &f.image, Field::Image));
                    c.push(button::standard("Choose a file…").on_press(Message::PickImage).into());
                } else {
                    c.push(text::body("Installer ISO").into());
                    let sel = self.isos.iter().position(|i| i.name == f.iso);
                    let mut r: Vec<Element<'_, Message>> = Vec::new();
                    if !self.iso_names.is_empty() {
                        r.push(widget::dropdown(&self.iso_names, sel, Message::NewIso).into());
                    } else {
                        r.push(text::caption("The ISO library is empty.").into());
                    }
                    let adding = self.busy.as_ref().is_some_and(|b| b.1 == "Adding the ISO");
                    r.push(
                        button::standard(if adding { "Adding…" } else { "Add an ISO…" })
                            .on_press_maybe((!adding).then_some(Message::AddIso))
                            .into(),
                    );
                    c.push(row::with_children(r).spacing(spacing.space_s).align_y(Alignment::Center).into());
                }
                c.push(
                    row::with_children([
                        field("CPUs", "2", &f.cpus, Field::Cpus),
                        field("Memory", "2G", &f.memory, Field::Memory),
                        field("Disk", "20G", &f.disk, Field::Disk),
                    ])
                    .spacing(spacing.space_s)
                    .into(),
                );
                c.push(field("Port forwards (NAT), e.g. 8080:80", "", &f.forwards, Field::Forwards));
                c.push(
                    section()
                        .add(
                            item::builder("Bridged network")
                                .description("Its own address on your LAN (wired only; `pengyos-vm bridge enable` first)")
                                .control(widget::toggler(f.bridge).on_toggle(Message::NewBridge)),
                        )
                        .add(
                            item::builder("Give it my SSH key")
                                .description("For systemd guests, like PengyOS Core and cloud images")
                                .control(widget::toggler(f.ssh_key).on_toggle(Message::NewSshKey)),
                        )
                        .add(
                            item::builder("Legacy BIOS")
                                .description("Only for an installer that will not boot with UEFI")
                                .control(widget::toggler(f.bios).on_toggle(Message::NewBios)),
                        )
                        .into(),
                );
                let problem = f.problem();
                match &problem {
                    Some(p) => c.push(accent(p.clone())),
                    None => c.push(text::caption(shell_line(&f.argv())).font(cosmic::font::mono()).into()),
                }
                widget::dialog()
                    .title("New virtual machine")
                    .control(widget::scrollable(column::with_children(c).spacing(spacing.space_xs)).height(Length::Fixed(480.0)))
                    .primary_action(button::suggested("Create").on_press_maybe(problem.is_none().then_some(Message::Create)))
            }
        };
        Some(d.secondary_action(cancel).into())
    }

    fn subscription(&self) -> Subscription<Message> {
        // State changes made elsewhere (a terminal, autostart, a guest that shuts down).
        cosmic::iced::time::every(Duration::from_secs(3)).map(|_| Message::Refresh)
    }
}

fn state_word(v: &Vm) -> String {
    match v.state.as_str() {
        "running" => "Running".into(),
        "stopped" => "Off".into(),
        "starting" => "Starting".into(),
        "stopping" => "Shutting down".into(),
        "failed" => "Failed to run".into(),
        s => s.to_string(),
    }
}

impl App {
    fn view_list(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut c: Vec<Element<'_, Message>> = vec![text::title4("Virtual machines").into()];
        for v in &self.vms {
            let selected = self.selected.as_deref() == Some(v.name.as_str());
            let label = column::with_children([text::body(v.name.clone()).into(), text::caption(state_word(v)).into()]).spacing(2);
            c.push(
                button::custom(container(label).padding(spacing.space_xxs))
                    .class(cosmic::theme::Button::ListItem([6.0; 4]))
                    .selected(selected)
                    .width(Length::Fill)
                    .on_press(Message::Select(v.name.clone()))
                    .into(),
            );
        }
        if self.vms.is_empty() && self.load_error.is_none() {
            c.push(text::caption("No virtual machines yet.").into());
        }
        if let Some(e) = &self.load_error {
            c.push(text::caption(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        let creating = self.creating.as_ref().map(|n| format!("Creating {n}…"));
        c.push(
            button::suggested(creating.unwrap_or_else(|| "New VM…".into()))
                .on_press_maybe(self.creating.is_none().then_some(Message::NewVm))
                .into(),
        );
        container(widget::scrollable(column::with_children(c).spacing(spacing.space_xs)))
            .width(Length::Fixed(240.0))
            .height(Length::Fill)
            .into()
    }

    fn view_empty(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut c: Vec<Element<'_, Message>> = vec![
            text::title3("Run other operating systems in a window").into(),
            text::body("A virtual machine boots an installer ISO or a ready-made disk image, with its own disk, memory and network. Make one with New VM.").into(),
        ];
        if let Some((t, line, bad)) = &self.result {
            c.push(self.view_result(t, line, *bad));
        }
        container(column::with_children(c).spacing(spacing.space_s).max_width(560)).padding(spacing.space_l).into()
    }

    fn view_result<'a>(&self, t: &'a str, line: &'a str, bad: bool) -> Element<'a, Message> {
        let t = text::body(t.to_string());
        column::with_children([
            if bad { t.class(cosmic::theme::Text::Accent).into() } else { t.into() },
            text::caption(format!("ran: {line}")).font(cosmic::font::mono()).into(),
        ])
        .spacing(2)
        .into()
    }

    fn view_vm<'a>(&'a self, v: &'a Vm) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        let busy = self.busy.as_ref().filter(|b| b.0 == v.name).map(|b| b.1);
        let idle = self.busy.is_none();
        let act = |label: &'static str, a: VmAction| -> Element<'a, Message> {
            button::standard(label).on_press_maybe(idle.then(|| Message::Ask(v.name.clone(), a))).into()
        };
        let on = v.running();
        let mut buttons: Vec<Element<'a, Message>> = Vec::new();
        if on {
            buttons.push(
                button::suggested("Screen")
                    .on_press_maybe(v.has_screen().then(|| Message::OpenScreen(v.name.clone())))
                    .into(),
            );
            buttons.push(act("Shut down", VmAction::Stop));
            buttons.push(
                button::destructive("Force off")
                    .on_press_maybe(idle.then(|| Message::Ask(v.name.clone(), VmAction::ForceOff)))
                    .into(),
            );
            if let Some(_ssh) = &v.ssh {
                let argv = vec![crate::VM.to_string(), "ssh".into(), v.name.clone()];
                buttons.push(button::standard("SSH in a terminal").on_press(Message::Terminal(argv)).into());
            }
        } else {
            buttons.push(
                button::suggested("Start")
                    .on_press_maybe(idle.then(|| Message::Ask(v.name.clone(), VmAction::Start)))
                    .into(),
            );
        }
        if v.iso.is_some() {
            buttons.push(act("Eject ISO", VmAction::Eject));
        }
        if !on {
            buttons.push(
                button::destructive("Delete")
                    .on_press_maybe(idle.then(|| Message::Ask(v.name.clone(), VmAction::Delete)))
                    .into(),
            );
        }

        let mut c: Vec<Element<'a, Message>> = vec![
            row::with_children([text::title3(v.name.clone()).into(), text::body(state_word(v)).into()])
                .spacing(spacing.space_s)
                .align_y(Alignment::Center)
                .into(),
            row::with_children(buttons).spacing(spacing.space_xs).into(),
        ];
        if let Some(b) = busy {
            c.push(text::body(format!("{b}…")).into());
        } else if let Some((t, line, bad)) = &self.result {
            c.push(self.view_result(t, line, *bad));
        }
        if on && !v.has_screen() {
            c.push(text::caption("This VM was started by an older pengyos-vm: restart it to get its screen here.").into());
        }

        let mut info = section().title("Settings");
        info = info.add(item::builder("Processors and memory").control(text::body(format!("{} CPUs, {}", v.cpus, v.memory))));
        let net = match (v.net.as_str(), v.offline) {
            (_, true) => "Offline (a throwaway run)".to_string(),
            ("bridge", _) => "Bridged: its own address on your LAN".to_string(),
            _ => "NAT: shares this computer's connection".to_string(),
        };
        info = info.add(item::builder("Network").control(text::body(net)));
        if let Some(ssh) = &v.ssh {
            info = info.add(item::builder("SSH").control(text::body(ssh.clone()).font(cosmic::font::mono())));
        }
        if !v.forwards.is_empty() {
            info = info.add(item::builder("Port forwards").control(text::body(v.forwards.join(", ")).font(cosmic::font::mono())));
        }
        if let Some(iso) = &v.iso {
            let short = iso.rsplit('/').next().unwrap_or(iso).to_string();
            info = info.add(item::builder("Installer ISO").description("Eject it once the install is done").control(text::body(short)));
        }
        info = info.add(item::builder("Firmware").control(text::body(if v.firmware == "bios" { "Legacy BIOS" } else { "UEFI" })));
        info = info.add(
            item::builder("Start when this computer starts").control(
                widget::toggler(v.autostart).on_toggle_maybe(idle.then(|| {
                    let name = v.name.clone();
                    move |b| Message::Act(name.clone(), VmAction::Autostart(b))
                })),
            ),
        );
        c.push(info.into());

        let mut snaps = section()
            .title("Snapshots")
            .add(text::caption("A snapshot saves the disk, not what is running: take one before trying something, and roll back if it goes wrong."));
        for s in v.snapshots.iter().rev() {
            let title = if s.label.is_empty() { s.name.clone() } else { format!("{} · {}", s.name, s.label) };
            let desc = format!("{}{}", s.created, if s.taken == "running" { ", while it ran" } else { "" });
            let controls = row::with_children([
                button::standard("Roll back")
                    .on_press_maybe(idle.then(|| Message::Ask(v.name.clone(), VmAction::SnapshotRevert(s.name.clone()))))
                    .into(),
                button::text("Delete")
                    .on_press_maybe(idle.then(|| Message::Ask(v.name.clone(), VmAction::SnapshotDelete(s.name.clone()))))
                    .into(),
            ])
            .spacing(spacing.space_xxs);
            snaps = snaps.add(item::builder(title).description(desc).control(controls));
        }
        let take = row::with_children([
            widget::text_input("Label, e.g. before the upgrade", self.snap_label.as_str())
                .on_input(Message::SnapLabel)
                .on_submit(move |_| Message::Ask(v.name.clone(), VmAction::SnapshotCreate(self.snap_label.clone())))
                .width(Length::Fill)
                .into(),
            button::standard("Take snapshot")
                .on_press_maybe(idle.then(|| Message::Ask(v.name.clone(), VmAction::SnapshotCreate(self.snap_label.clone()))))
                .into(),
        ])
        .spacing(spacing.space_xs)
        .align_y(Alignment::Center);
        snaps = snaps.add(take);
        c.push(snaps.into());

        column::with_children(c).spacing(spacing.space_m).max_width(760).into()
    }

    fn view_screen<'a>(&'a self, s: &'a Screen) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        let bar = row::with_children([
            button::standard("Back").on_press(Message::CloseScreen).into(),
            text::title4(s.vm.clone()).into(),
            container(text::caption(s.status.clone())).width(Length::Fill).into(),
            button::standard("Ctrl+Alt+Del").on_press_maybe(s.tx.is_some().then_some(Message::ScreenCtrlAltDel)).into(),
            text::body("Fit to window").into(),
            widget::toggler(s.fit).on_toggle(Message::ScreenFit).into(),
            button::standard("Reconnect").on_press(Message::OpenScreen(s.vm.clone())).into(),
        ])
        .spacing(spacing.space_s)
        .align_y(Alignment::Center);
        let view = crate::screen::View {
            image: s.image.as_ref(),
            width: s.width,
            height: s.height,
            fit: s.fit,
            on_input: Message::ScreenInput,
            on_focus: Message::ScreenFocus,
        };
        column::with_children([
            bar.into(),
            canvas::Canvas::new(view).width(Length::Fill).height(Length::Fill).into(),
        ])
        .spacing(spacing.space_xs)
        .into()
    }
}
