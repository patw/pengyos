//! The Dev boxes and Containers pages (spec.md steps 3 and 4): distrobox, and
//! podman with Quadlet services. Like the VM page, every button is a command
//! (containers.rs says which) and the result shows what ran.

use cosmic::iced::Length;
use cosmic::prelude::*;
use cosmic::widget::settings::{item, section};
use cosmic::widget::{self, button, column, row, text};

use super::{off_thread, App, Message};
use crate::backend::{self, Log};
use crate::containers::{self, BoxAction, Container, ContainerAction, Image, NewBox, RunForm, Service, DISTROS};
use crate::shell_line;

#[derive(Debug, Clone)]
pub enum PodMsg {
    Refresh,
    Loaded(Result<(Vec<Container>, Vec<Image>, Vec<Service>), String>),
    /// A command finished: (what ran, result).
    Done(Log, Result<String, String>),
    // dev boxes
    NewBox,
    BoxName(String),
    BoxDistro(usize),
    BoxImage(String),
    BoxNvidia(bool),
    BoxOwnHome(bool),
    CreateBox,
    BoxAct(String, BoxAction),
    BoxApps(String),
    BoxAppsLoaded(String, Result<Vec<String>, String>),
    BoxExport(String, String),
    // containers
    Container(String, ContainerAction),
    Logs(String, Vec<String>),
    LogsLoaded(String, Result<String, String>),
    KeepPreview(String),
    KeepPreviewed(String, Result<String, String>),
    Keep(String),
    Service(String, &'static str),
    Edit(String),
    RemoveService(String),
    RunForm,
    RunField(u8, String),
    RunService(bool),
    RunGpu(bool),
    Run,
    PullForm,
    PullField(String),
    Pull,
    RemoveImage(String),
    PruneImages,
}

impl From<PodMsg> for Message {
    fn from(m: PodMsg) -> Message {
        Message::Pods(m)
    }
}

pub enum PodDialog {
    NewBox(NewBox),
    Apps { name: String, apps: Option<Result<Vec<String>, String>> },
    Text { title: String, text: String },
    Keep { name: String, file: Option<Result<String, String>> },
    Run(RunForm),
    Pull(String),
    /// Ask first: (title, body, button, what it does).
    Ask(String, String, &'static str, PodMsg),
}

#[derive(Default)]
pub struct Pods {
    pub containers: Vec<Container>,
    pub images: Vec<Image>,
    pub services: Vec<Service>,
    pub error: Option<String>,
    pub busy: Option<String>,
    /// (text, the commands that ran, failed?)
    pub result: Option<(String, Log, bool)>,
    pub dialog: Option<PodDialog>,
    pub loaded: bool,
    pub nvidia: bool,
    pub distro_names: Vec<String>,
}

fn load() -> Task<cosmic::Action<Message>> {
    cosmic::task::future(async move {
        let r = off_thread(|| {
            let c = backend::list_containers()?;
            let i = backend::list_images().unwrap_or_default();
            Ok((c, i, backend::list_services()))
        })
        .await;
        Message::Pods(PodMsg::Loaded(r))
    })
}

/// Run one command off the UI thread; its line goes into the log.
fn run1(argv: Vec<String>) -> Task<cosmic::Action<Message>> {
    cosmic::task::future(async move {
        let line = shell_line(&argv);
        let r = off_thread(move || backend::run(&argv)).await;
        Message::Pods(PodMsg::Done(vec![line], r))
    })
}

impl App {
    pub(super) fn pods_init(&mut self) -> Task<cosmic::Action<Message>> {
        self.pods.nvidia = backend::has_nvidia();
        self.pods.distro_names = DISTROS.iter().map(|d| d.0.to_string()).chain(["Another image…".to_string()]).collect();
        load()
    }

    fn taken(&self) -> Vec<String> {
        self.pods.containers.iter().map(|c| c.name.clone()).chain(self.pods.services.iter().map(|s| s.name.clone())).collect()
    }

    fn start(&mut self, what: &str) {
        self.pods.busy = Some(what.to_string());
        self.pods.result = None;
        self.pods.dialog = None;
    }

    pub(super) fn update_pods(&mut self, m: PodMsg) -> Task<cosmic::Action<Message>> {
        let p = &mut self.pods;
        match m {
            PodMsg::Refresh => return load(),
            PodMsg::Loaded(Ok((c, i, s))) => {
                p.containers = c;
                p.images = i;
                p.services = s;
                p.error = None;
                p.loaded = true;
            }
            PodMsg::Loaded(Err(e)) => {
                p.error = Some(e);
                p.loaded = true;
            }
            PodMsg::Done(log, r) => {
                p.busy = None;
                p.result = Some(match r {
                    Ok(t) => (t, log, false),
                    Err(e) => (e, log, true),
                });
                return load();
            }
            PodMsg::NewBox => p.dialog = Some(PodDialog::NewBox(NewBox { distro: 0, ..Default::default() })),
            PodMsg::BoxName(v) => {
                if let Some(PodDialog::NewBox(b)) = &mut p.dialog {
                    b.name = v;
                }
            }
            PodMsg::BoxImage(v) => {
                if let Some(PodDialog::NewBox(b)) = &mut p.dialog {
                    b.image = v;
                }
            }
            PodMsg::BoxDistro(i) => {
                if let Some(PodDialog::NewBox(b)) = &mut p.dialog {
                    b.distro = i;
                }
            }
            PodMsg::BoxNvidia(v) => {
                if let Some(PodDialog::NewBox(b)) = &mut p.dialog {
                    b.nvidia = v;
                }
            }
            PodMsg::BoxOwnHome(v) => {
                if let Some(PodDialog::NewBox(b)) = &mut p.dialog {
                    b.own_home = v;
                }
            }
            PodMsg::CreateBox => {
                let Some(PodDialog::NewBox(b)) = p.dialog.take() else { return Task::none() };
                let argv = b.argv(&backend::home());
                self.start(&format!("Creating {} (it downloads the image the first time: a few minutes)", b.name.trim()));
                return run1(argv);
            }
            PodMsg::BoxAct(name, BoxAction::Delete) if !matches!(p.dialog, Some(PodDialog::Ask(..))) => {
                p.dialog = Some(PodDialog::Ask(
                    format!("Delete the box {name}?"),
                    "What you installed in it goes. Your home folder is not touched (unless it has its own).".into(),
                    "Delete",
                    PodMsg::BoxAct(name, BoxAction::Delete),
                ));
            }
            PodMsg::BoxAct(name, a) => {
                self.start(&format!("{} {name}", if a == BoxAction::Stop { "Stopping" } else { "Deleting" }));
                return run1(a.argv(&name));
            }
            PodMsg::BoxApps(name) => {
                p.dialog = Some(PodDialog::Apps { name: name.clone(), apps: None });
                let argv = containers::box_apps(&name);
                return cosmic::task::future(async move {
                    let r = off_thread(move || backend::run(&argv)).await;
                    Message::Pods(PodMsg::BoxAppsLoaded(name, r.map(|t| containers::parse_box_apps(&t))))
                });
            }
            PodMsg::BoxAppsLoaded(name, r) => {
                if let Some(PodDialog::Apps { name: n, apps }) = &mut p.dialog {
                    if *n == name {
                        *apps = Some(r);
                    }
                }
            }
            PodMsg::BoxExport(name, app) => {
                self.start(&format!("Adding {app} to the launcher"));
                return run1(containers::box_export(&name, &app));
            }
            PodMsg::Container(name, ContainerAction::Remove) if !matches!(p.dialog, Some(PodDialog::Ask(..))) => {
                p.dialog = Some(PodDialog::Ask(
                    format!("Remove the container {name}?"),
                    "It is stopped and deleted. Named volumes are kept; anything else inside it goes.".into(),
                    "Remove",
                    PodMsg::Container(name, ContainerAction::Remove),
                ));
            }
            PodMsg::Container(name, a) => {
                self.start(&format!("{a:?} {name}"));
                return run1(a.argv(&name));
            }
            PodMsg::Logs(title, argv) => {
                p.dialog = Some(PodDialog::Text { title: title.clone(), text: "Loading…".into() });
                return cosmic::task::future(async move {
                    let r = off_thread(move || backend::run(&argv)).await;
                    Message::Pods(PodMsg::LogsLoaded(title, r))
                });
            }
            PodMsg::LogsLoaded(title, r) => {
                if let Some(PodDialog::Text { title: t, text }) = &mut p.dialog {
                    if *t == title {
                        *text = match r {
                            Ok(s) if s.trim().is_empty() => "(nothing logged yet)".into(),
                            Ok(s) => s,
                            Err(e) => e,
                        };
                    }
                }
            }
            PodMsg::KeepPreview(name) => {
                p.dialog = Some(PodDialog::Keep { name: name.clone(), file: None });
                return cosmic::task::future(async move {
                    let n = name.clone();
                    let r = off_thread(move || {
                        let inspect = backend::run(&["podman".into(), "container".into(), "inspect".into(), n])?;
                        let spec0 = containers::QuadletSpec::from_inspect(&inspect, &[])?;
                        let img = backend::run(&["podman".into(), "image".into(), "inspect".into(), spec0.image]).unwrap_or_default();
                        Ok(containers::QuadletSpec::from_inspect(&inspect, &containers::image_env(&img))?.render())
                    })
                    .await;
                    Message::Pods(PodMsg::KeepPreviewed(name, r))
                });
            }
            PodMsg::KeepPreviewed(name, r) => {
                if let Some(PodDialog::Keep { name: n, file }) = &mut p.dialog {
                    if *n == name {
                        *file = Some(r);
                    }
                }
            }
            PodMsg::Keep(name) => {
                self.start(&format!("Making {name} a service"));
                return cosmic::task::future(async move {
                    let (r, log) = off_thread(move || backend::keep_at_boot(&name)).await;
                    Message::Pods(PodMsg::Done(log, r))
                });
            }
            PodMsg::Service(name, verb) => {
                self.start(&format!("{verb} {name}"));
                // Reload first, so an edited file is what starts.
                return cosmic::task::future(async move {
                    let mut log = vec!["systemctl --user daemon-reload".to_string()];
                    let unit = format!("{name}.service");
                    let argv = containers::service_action(verb, &unit);
                    log.push(shell_line(&argv));
                    let r = off_thread(move || {
                        backend::run(&["systemctl".into(), "--user".into(), "daemon-reload".into()])?;
                        backend::run(&argv)
                    })
                    .await;
                    Message::Pods(PodMsg::Done(log, r))
                });
            }
            PodMsg::Edit(path) => {
                if let Err(e) = backend::spawn(&["cosmic-edit".into(), path.clone()]) {
                    p.result = Some((e, vec![format!("cosmic-edit {path}")], true));
                } else {
                    p.result = Some((
                        "Opened in the text editor. After saving, use Restart: Machines reloads the services first.".into(),
                        vec![format!("cosmic-edit {path}")],
                        false,
                    ));
                }
            }
            PodMsg::RemoveService(name) if !matches!(p.dialog, Some(PodDialog::Ask(..))) => {
                p.dialog = Some(PodDialog::Ask(
                    format!("Remove the service {name}?"),
                    "It is stopped and its file deleted. Its data (volumes and folders) is kept.".into(),
                    "Remove",
                    PodMsg::RemoveService(name),
                ));
            }
            PodMsg::RemoveService(name) => {
                let Some(svc) = p.services.iter().find(|s| s.name == name).cloned() else { return Task::none() };
                self.start(&format!("Removing {name}"));
                return cosmic::task::future(async move {
                    let (r, log) = off_thread(move || backend::remove_service(&svc)).await;
                    Message::Pods(PodMsg::Done(log, r))
                });
            }
            PodMsg::RunForm => p.dialog = Some(PodDialog::Run(RunForm { service: true, ..Default::default() })),
            PodMsg::RunField(which, v) => {
                if let Some(PodDialog::Run(f)) = &mut p.dialog {
                    match which {
                        0 => f.image = v,
                        1 => f.name = v,
                        2 => f.ports = v,
                        3 => f.volumes = v,
                        _ => f.env = v,
                    }
                }
            }
            PodMsg::RunService(b) => {
                if let Some(PodDialog::Run(f)) = &mut p.dialog {
                    f.service = b;
                }
            }
            PodMsg::RunGpu(b) => {
                if let Some(PodDialog::Run(f)) = &mut p.dialog {
                    f.gpu = b;
                }
            }
            PodMsg::Run => {
                let Some(PodDialog::Run(f)) = p.dialog.take() else { return Task::none() };
                self.start(&format!("Starting {} (downloading the image first)", f.name.trim()));
                return cosmic::task::future(async move {
                    let (r, log) = off_thread(move || backend::run_container(&f)).await;
                    Message::Pods(PodMsg::Done(log, r))
                });
            }
            PodMsg::PullForm => p.dialog = Some(PodDialog::Pull(String::new())),
            PodMsg::PullField(v) => {
                if let Some(PodDialog::Pull(s)) = &mut p.dialog {
                    *s = v;
                }
            }
            PodMsg::Pull => {
                let Some(PodDialog::Pull(img)) = p.dialog.take() else { return Task::none() };
                self.start(&format!("Downloading {}", img.trim()));
                return run1(vec!["podman".into(), "pull".into(), img.trim().to_string()]);
            }
            PodMsg::RemoveImage(id) if !matches!(p.dialog, Some(PodDialog::Ask(..))) => {
                let name = p.images.iter().find(|i| i.id == id).map(|i| i.name.clone()).unwrap_or_default();
                p.dialog = Some(PodDialog::Ask(format!("Remove the image {name}?"), String::new(), "Remove", PodMsg::RemoveImage(id)));
            }
            PodMsg::RemoveImage(id) => {
                self.start("Removing the image");
                return run1(vec!["podman".into(), "rmi".into(), id]);
            }
            PodMsg::PruneImages if !matches!(p.dialog, Some(PodDialog::Ask(..))) => {
                p.dialog = Some(PodDialog::Ask(
                    "Remove unused images?".into(),
                    "Every image no container uses is deleted; they download again when needed.".into(),
                    "Remove",
                    PodMsg::PruneImages,
                ));
            }
            PodMsg::PruneImages => {
                self.start("Removing unused images");
                return run1(vec!["podman".into(), "image".into(), "prune".into(), "--all".into(), "--force".into()]);
            }
        }
        Task::none()
    }

    fn pods_result(&self) -> Vec<Element<'_, Message>> {
        let mut c: Vec<Element<'_, Message>> = Vec::new();
        if let Some(b) = &self.pods.busy {
            c.push(text::body(format!("{b}…")).into());
        } else if let Some((t, log, bad)) = &self.pods.result {
            let t = text::body(t.clone());
            c.push(if *bad { t.class(cosmic::theme::Text::Accent).into() } else { t.into() });
            for l in log {
                c.push(text::caption(format!("ran: {l}")).font(cosmic::font::mono()).into());
            }
        }
        if let Some(e) = &self.pods.error {
            c.push(text::body(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        c
    }

    pub(super) fn view_boxes(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let idle = self.pods.busy.is_none();
        let mut c: Vec<Element<'_, Message>> = vec![
            text::title3("Dev boxes").into(),
            text::body("A whole Linux distribution in a container that shares your home folder: install anything with its package manager (apt, dnf, pacman) without changing the system.").into(),
            row::with_children([button::suggested("New box…").on_press_maybe(idle.then_some(PodMsg::NewBox.into())).into()]).into(),
        ];
        c.extend(self.pods_result());
        let boxes: Vec<&Container> = self.pods.containers.iter().filter(|x| x.distrobox).collect();
        if boxes.is_empty() && self.pods.loaded {
            c.push(text::body("No boxes yet.").into());
        }
        let mut s = section();
        for b in boxes {
            let n = b.name.clone();
            let mut ctl: Vec<Element<'_, Message>> = vec![
                button::suggested("Open").on_press(Message::Terminal(containers::box_enter(&n))).into(),
                button::standard("Apps…").on_press_maybe(idle.then(|| PodMsg::BoxApps(n.clone()).into())).into(),
                button::standard("Upgrade").on_press(Message::Terminal(containers::box_upgrade(&n))).into(),
            ];
            if b.running() {
                ctl.push(button::standard("Stop").on_press_maybe(idle.then(|| PodMsg::BoxAct(n.clone(), BoxAction::Stop).into())).into());
            }
            ctl.push(button::text("Delete").on_press_maybe(idle.then(|| PodMsg::BoxAct(n.clone(), BoxAction::Delete).into())).into());
            s = s.add(
                item::builder(b.name.clone())
                    .description(format!("{} · {}", short_image(&b.image), b.status))
                    .control(row::with_children(ctl).spacing(spacing.space_xxs)),
            );
        }
        c.push(s.into());
        widget::scrollable(column::with_children(c).spacing(spacing.space_m).max_width(900)).height(Length::Fill).into()
    }

    pub(super) fn view_containers(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let idle = self.pods.busy.is_none();
        let mut c: Vec<Element<'_, Message>> = vec![
            text::title3("Containers").into(),
            text::body("Services keep a container running (and start it with this computer); plain containers run until you stop them.").into(),
            row::with_children([
                button::suggested("Run a container…").on_press_maybe(idle.then_some(PodMsg::RunForm.into())).into(),
                button::standard("Download an image…").on_press_maybe(idle.then_some(PodMsg::PullForm.into())).into(),
            ])
            .spacing(spacing.space_xs)
            .into(),
        ];
        c.extend(self.pods_result());

        // Services
        let mut s = section().title("Services");
        if self.pods.services.is_empty() {
            s = s.add(text::caption("None yet. Run a container as a service, or keep one running at boot."));
        }
        for svc in &self.pods.services {
            let n = svc.name.clone();
            let on = svc.state == "active" || svc.state == "activating";
            let mut ctl: Vec<Element<'_, Message>> = Vec::new();
            if !svc.system {
                ctl.push(if on {
                    button::standard("Stop").on_press_maybe(idle.then(|| PodMsg::Service(n.clone(), "stop").into())).into()
                } else {
                    button::standard("Start").on_press_maybe(idle.then(|| PodMsg::Service(n.clone(), "start").into())).into()
                });
                ctl.push(button::standard("Restart").on_press_maybe(idle.then(|| PodMsg::Service(n.clone(), "restart").into())).into());
            }
            let logs = if svc.system {
                vec!["journalctl".into(), "-u".into(), format!("{n}.service"), "-n".into(), "300".into(), "--no-pager".into()]
            } else {
                containers::service_logs(&format!("{n}.service"))
            };
            ctl.push(button::standard("Logs").on_press(Message::from(PodMsg::Logs(format!("{n} (service)"), logs))).into());
            if !svc.system {
                ctl.push(button::standard("Edit").on_press(Message::from(PodMsg::Edit(svc.path.clone()))).into());
                ctl.push(button::text("Remove").on_press_maybe(idle.then(|| PodMsg::RemoveService(n.clone()).into())).into());
            }
            let when = match (svc.system, svc.autostart, backend::lingering()) {
                (true, true, _) => "a system service, starts at boot",
                (true, false, _) => "a system service",
                (false, true, true) => "starts with this computer",
                (false, true, false) => "starts when you log in",
                (false, false, _) => "started by hand",
            };
            let mut desc = format!("{} · {} · {when}", short_image(&svc.image), state_word(&svc.state));
            if !svc.ports.is_empty() {
                desc += &format!(" · ports {}", svc.ports.join(", "));
            }
            s = s.add(item::builder(svc.name.clone()).description(desc).control(row::with_children(ctl).spacing(spacing.space_xxs)));
        }
        c.push(s.into());

        // Plain containers
        let plain: Vec<&Container> = self.pods.containers.iter().filter(|x| !x.distrobox && x.unit.is_none()).collect();
        let mut s = section().title("Other containers");
        if plain.is_empty() {
            s = s.add(text::caption("None."));
        }
        for ct in plain {
            let n = ct.name.clone();
            let mut ctl: Vec<Element<'_, Message>> = Vec::new();
            if ct.running() {
                ctl.push(button::standard("Stop").on_press_maybe(idle.then(|| PodMsg::Container(n.clone(), ContainerAction::Stop).into())).into());
                ctl.push(button::standard("Shell").on_press(Message::Terminal(containers::container_shell(&n))).into());
            } else {
                ctl.push(button::standard("Start").on_press_maybe(idle.then(|| PodMsg::Container(n.clone(), ContainerAction::Start).into())).into());
            }
            ctl.push(button::standard("Logs").on_press(Message::from(PodMsg::Logs(n.clone(), containers::container_logs(&n)))).into());
            ctl.push(button::standard("Keep running at boot…").on_press_maybe(idle.then(|| PodMsg::KeepPreview(n.clone()).into())).into());
            ctl.push(button::text("Remove").on_press_maybe(idle.then(|| PodMsg::Container(n.clone(), ContainerAction::Remove).into())).into());
            let mut desc = format!("{} · {}", short_image(&ct.image), ct.status);
            if !ct.ports.is_empty() {
                desc += &format!(" · ports {}", ct.ports.join(", "));
            }
            s = s.add(item::builder(ct.name.clone()).description(desc).control(row::with_children(ctl).spacing(spacing.space_xxs)));
        }
        c.push(s.into());

        // Images
        let mut s = section().title("Images");
        for img in &self.pods.images {
            let used = match img.containers {
                0 => "unused".to_string(),
                1 => "used by 1 container".to_string(),
                n => format!("used by {n} containers"),
            };
            s = s.add(
                item::builder(img.name.clone())
                    .description(format!("{} · {used}", containers::human(img.size)))
                    .control(button::text("Remove").on_press_maybe((idle && img.containers == 0).then(|| PodMsg::RemoveImage(img.id.clone()).into()))),
            );
        }
        if self.pods.images.iter().any(|i| i.containers == 0) {
            s = s.add(row::with_children([button::standard("Remove unused images").on_press_maybe(idle.then_some(PodMsg::PruneImages.into())).into()]));
        }
        c.push(s.into());
        widget::scrollable(column::with_children(c).spacing(spacing.space_m).max_width(1000)).height(Length::Fill).into()
    }

    pub(super) fn pods_dialog(&self) -> Option<Element<'_, Message>> {
        let spacing = cosmic::theme::spacing();
        let cancel = button::standard("Cancel").on_press(Message::CloseDialog);
        let accent = |s: String| -> Element<'_, Message> { text::caption(s).class(cosmic::theme::Text::Accent).into() };
        let d = match self.pods.dialog.as_ref()? {
            PodDialog::Ask(title, body, label, go) => {
                let mut d = widget::dialog().title(title.clone()).primary_action(button::destructive(*label).on_press(go.clone().into()));
                if !body.is_empty() {
                    d = d.body(body.clone());
                }
                d
            }
            PodDialog::NewBox(b) => {
                let mut c: Vec<Element<'_, Message>> = vec![
                    text::body("Name").into(),
                    widget::text_input("dev", b.name.as_str()).on_input(|s| PodMsg::BoxName(s).into()).into(),
                    text::body("Distribution").into(),
                    widget::dropdown(&self.pods.distro_names, Some(b.distro), |i| PodMsg::BoxDistro(i).into()).into(),
                ];
                if b.distro >= DISTROS.len() {
                    c.push(widget::text_input("docker.io/library/alpine:latest", b.image.as_str()).on_input(|s| PodMsg::BoxImage(s).into()).into());
                }
                let mut opts = section().add(
                    item::builder("Its own home folder")
                        .description("In ~/.local/share/boxes, so its settings and dotfiles stay apart from yours")
                        .control(widget::toggler(b.own_home).on_toggle(|v| PodMsg::BoxOwnHome(v).into())),
                );
                if self.pods.nvidia {
                    opts = opts.add(
                        item::builder("NVIDIA GPU")
                            .description("The driver and CUDA libraries from this computer, inside the box")
                            .control(widget::toggler(b.nvidia).on_toggle(|v| PodMsg::BoxNvidia(v).into())),
                    );
                }
                c.push(opts.into());
                let problem = b.problem(&self.taken());
                match &problem {
                    Some(p) => c.push(accent(p.clone())),
                    None => c.push(text::caption(shell_line(&b.argv(&backend::home()))).font(cosmic::font::mono()).into()),
                }
                widget::dialog()
                    .title("New dev box")
                    .control(column::with_children(c).spacing(spacing.space_xs))
                    .primary_action(button::suggested("Create").on_press_maybe(problem.is_none().then_some(PodMsg::CreateBox.into())))
            }
            PodDialog::Apps { name, apps } => {
                let body: Element<'_, Message> = match apps {
                    None => text::body(format!("Looking in {name} (starting it if it is stopped)…")).into(),
                    Some(Err(e)) => accent(e.clone()),
                    Some(Ok(list)) if list.is_empty() => {
                        text::body("It has no desktop apps yet: install one in it first (for example `sudo apt install gedit`).").into()
                    }
                    Some(Ok(list)) => {
                        let mut s = section();
                        for a in list {
                            s = s.add(
                                item::builder(a.clone())
                                    .control(button::standard("Add to launcher").on_press(PodMsg::BoxExport(name.clone(), a.clone()).into())),
                            );
                        }
                        widget::scrollable(s).height(Length::Fixed(360.0)).into()
                    }
                };
                widget::dialog()
                    .title(format!("Apps in {name}"))
                    .body("Adding one puts it in the App Library like any other app; it runs inside the box.")
                    .control(body)
            }
            PodDialog::Text { title, text: t } => widget::dialog().title(title.clone()).control(
                widget::scrollable(text::caption(t.clone()).font(cosmic::font::mono())).height(Length::Fixed(420.0)).width(Length::Fill),
            ),
            PodDialog::Keep { name, file } => {
                let (ctl, ok): (Element<'_, Message>, bool) = match file {
                    None => (text::body("Reading the container…").into(), false),
                    Some(Err(e)) => (accent(e.clone()), false),
                    Some(Ok(f)) => (
                        column::with_children([
                            text::body(format!("This file goes in {}:", containers::quadlet_dir("~"))).into(),
                            widget::scrollable(text::caption(f.clone()).font(cosmic::font::mono())).height(Length::Fixed(300.0)).into(),
                        ])
                        .spacing(spacing.space_xs)
                        .into(),
                        true,
                    ),
                };
                widget::dialog()
                    .title(format!("Keep {name} running at boot?"))
                    .body(format!("{name} becomes a service with the same image, ports, folders and environment. It is stopped, the service starts it again, and the old container is removed only once the service runs."))
                    .control(ctl)
                    .primary_action(button::suggested("Make it a service").on_press_maybe(ok.then(|| PodMsg::Keep(name.clone()).into())))
            }
            PodDialog::Run(f) => {
                let field = |label: &'static str, hint: &'static str, v: &str, which: u8| -> Element<'_, Message> {
                    column::with_children([
                        text::body(label).into(),
                        widget::text_input(hint, v.to_string()).on_input(move |s| PodMsg::RunField(which, s).into()).into(),
                    ])
                    .spacing(spacing.space_xxxs)
                    .into()
                };
                let mut c: Vec<Element<'_, Message>> = vec![
                    field("Image", "docker.io/jellyfin/jellyfin:latest", &f.image, 0),
                    field("Name", "jellyfin", &f.name, 1),
                    field("Ports (this computer's, then the container's), comma between", "8096:8096", &f.ports, 2),
                    field("Folders (this computer's, then the container's), comma between", "/home/you/media:/media", &f.volumes, 3),
                    field("Environment, KEY=value (one per line)", "TZ=America/Toronto", &f.env, 4),
                ];
                let mut opts = section().add(
                    item::builder("As a service")
                        .description("Restarted if it stops, and started with this computer (a Quadlet file you can edit)")
                        .control(widget::toggler(f.service).on_toggle(|v| PodMsg::RunService(v).into())),
                );
                if self.pods.nvidia {
                    opts = opts.add(item::builder("NVIDIA GPU").control(widget::toggler(f.gpu).on_toggle(|v| PodMsg::RunGpu(v).into())));
                }
                c.push(opts.into());
                let problem = f.problem(&self.taken());
                match &problem {
                    Some(p) => c.push(accent(p.clone())),
                    None if !f.service => c.push(text::caption(shell_line(&f.run_argv())).font(cosmic::font::mono()).into()),
                    None => c.push(text::caption(format!("Writes {}/{}", containers::quadlet_dir("~"), f.spec().file_name())).font(cosmic::font::mono()).into()),
                }
                widget::dialog()
                    .title("Run a container")
                    .control(widget::scrollable(column::with_children(c).spacing(spacing.space_xs)).height(Length::Fixed(480.0)))
                    .primary_action(button::suggested("Run").on_press_maybe(problem.is_none().then_some(PodMsg::Run.into())))
            }
            PodDialog::Pull(img) => widget::dialog()
                .title("Download an image")
                .control(
                    widget::text_input("docker.io/library/nginx:latest", img.as_str())
                        .on_input(|s| PodMsg::PullField(s).into())
                        .on_submit(|_| PodMsg::Pull.into()),
                )
                .primary_action(button::suggested("Download").on_press_maybe((!img.trim().is_empty()).then_some(PodMsg::Pull.into()))),
        };
        Some(d.secondary_action(cancel).into())
    }
}

fn short_image(i: &str) -> String {
    i.trim_start_matches("docker.io/").trim_start_matches("library/").to_string()
}

fn state_word(s: &str) -> &str {
    match s {
        "active" => "running",
        "inactive" => "stopped",
        "activating" => "starting",
        "deactivating" => "stopping",
        "failed" => "failed",
        other => other,
    }
}
