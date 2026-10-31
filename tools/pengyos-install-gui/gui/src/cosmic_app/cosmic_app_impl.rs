//! Implementation of the libcosmic app shell.
//!
//! Build-gated behind `cosmic`; see the module docs in [`crate::cosmic_app`].
//!
//! The shell renders the pure [`Model`] and forwards widget events to it through
//! the shared [`crate::router`]; everything disk-related goes through the
//! `pengyos-install` engine (the same one the TUI runs):
//!
//! * the Disk screen lists `pengyos-install disks --json` (the disk the live
//!   system runs from is shown but cannot be picked);
//! * the install runs `pengyos-install image … --progress json --password-stdin`
//!   and streams its progress lines into the Progress screen.
//!
//! The model's Layout and Network steps are passed through without a screen:
//! the only layout the engine performs is "erase the whole disk" (chosen with
//! the disk and spelled out on the Summary screen), and Wi-Fi is configured from
//! the panel after the install.

use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Command, Stdio};

use cosmic::iced::futures::channel::mpsc;
use cosmic::iced::{Alignment, Length, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::{item, section};
use cosmic::widget::{self, button, column, container, nav_bar, radio, row, text};

use pengyos_install_model::{Layout, Model, Step};

use crate::restart::Restart;
use crate::router::{Message as WizardMessage, MessageRouter};

/// The unique COSMIC application id.
const APP_ID: &str = "io.catbee.pengyos.Installer";

/// Flags handed to the application by the runtime at startup.
#[derive(Clone, Debug)]
pub struct Flags {
    /// The install engine to run (`pengyos-install` on `PATH` by default).
    pub engine: String,
}

impl Default for Flags {
    fn default() -> Self {
        Flags { engine: "pengyos-install".into() }
    }
}

/// A disk as reported by `pengyos-install disks --json`.
#[derive(Clone, Debug)]
pub struct DiskChoice {
    pub path: String,
    pub label: String,
    pub installable: bool,
    pub note: String,
}

/// Ask the engine which disks exist.
pub fn load_disks(engine: &str) -> Result<Vec<DiskChoice>, String> {
    let out = Command::new(engine)
        .args(["disks", "--json"])
        .output()
        .map_err(|e| format!("cannot run {engine}: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    let list: serde_json::Value =
        serde_json::from_slice(&out.stdout).map_err(|e| format!("bad disk list: {e}"))?;
    let mut disks = Vec::new();
    for d in list.as_array().into_iter().flatten() {
        let s = |k: &str| d.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
        let b = |k: &str| d.get(k).and_then(|v| v.as_bool()).unwrap_or(false);
        let mounted = d.get("mounted").and_then(|v| v.as_array()).map_or(0, |a| a.len());
        let model = s("model");
        let mut label = format!("{}  —  {}", s("size_label"), s("path"));
        if !model.is_empty() {
            label = format!("{model}  ·  {label}");
        }
        let (installable, note) = if b("is_boot") {
            (false, "this is the USB stick PengyOS is running from".to_string())
        } else if mounted > 0 {
            (false, "in use (has mounted filesystems)".to_string())
        } else {
            (true, if b("removable") { "removable".to_string() } else { String::new() })
        };
        disks.push(DiskChoice { path: s("path"), label, installable, note });
    }
    Ok(disks)
}

/// Where the install is.
#[derive(Clone, Debug, PartialEq)]
enum Run {
    NotStarted,
    Running,
    Done,
    Failed(String),
}

/// Messages emitted by the application and its widgets.
#[derive(Debug, Clone)]
pub enum Message {
    /// A wizard event, routed to the pure [`Model`].
    Wizard(WizardMessage),
    /// The password-confirmation field changed.
    SetConfirm(String),
    /// "Log in automatically" switched (off by default).
    SetAutologin(bool),
    /// The assistant's base URL field changed.
    SetPengyBaseUrl(String),
    /// The assistant's API key field changed.
    SetPengyApiKey(String),
    /// The assistant's model field changed.
    SetPengyModel(String),
    /// Re-scan the disks (e.g. after plugging one in).
    RefreshDisks,
    /// A progress line from the engine.
    Progress(f32, String),
    /// The engine exited.
    Finished(Result<(), String>),
    /// Restart the machine after a successful install.
    Reboot,
    /// "Try PengyOS": close the installer and use the live desktop.
    TryLive,
    /// "Install PengyOS": start the wizard.
    StartInstall,
}

/// The COSMIC application model.
pub struct AppModel {
    core: cosmic::Core,
    model: Model,
    flags: Flags,
    disks: Result<Vec<DiskChoice>, String>,
    password: String,
    confirm: String,
    /// Start straight into the desktop at boot, skipping the login screen.
    autologin: bool,
    /// The assistant's endpoint (the Pengy screen). All three are optional: an
    /// empty base URL means the screen was skipped and Pengy keeps the defaults
    /// the image ships. The key is held here (like the password) and reaches
    /// the engine on stdin, never on argv.
    pengy_base_url: String,
    pengy_api_key: String,
    pengy_model: String,
    progress: f32,
    status: String,
    run: Run,
    /// Set up at start, while the stick (and so `systemctl`) is still there.
    restart: Restart,
}

fn valid_hostname(h: &str) -> bool {
    !h.is_empty()
        && h.len() <= 63
        && !h.starts_with('-')
        && !h.ends_with('-')
        && h.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
}

fn valid_username(u: &str) -> bool {
    let mut chars = u.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_lowercase() || c == '_')
        && u.len() <= 32
        && u != "root"
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

impl AppModel {
    fn step(&self) -> Step {
        self.model.step()
    }

    /// Why Continue is disabled on the current screen (None = enabled).
    fn blocker(&self) -> Option<String> {
        match self.step() {
            Step::Disk if self.model.selected_disk().is_none() => Some("Choose a disk.".into()),
            Step::User => {
                let host = self.model.hostname().unwrap_or("");
                let user = self.model.username().unwrap_or("");
                if !valid_hostname(host) {
                    Some("Computer name: letters, digits and '-' only.".into())
                } else if !valid_username(user) || user.len() < 3 {
                    Some("Username: at least 3 lowercase letters/digits, starting with a letter.".into())
                } else if self.password.len() < 6 {
                    Some("Password: at least 6 characters.".into())
                } else if self.password.contains(':') {
                    Some("The password cannot contain ':'.".into())
                } else if self.password != self.confirm {
                    Some("The passwords do not match.".into())
                } else {
                    None
                }
            }
            Step::Pengy => {
                let url = self.pengy_base_url.trim();
                if url.is_empty() {
                    None // the screen is skippable: an empty answer is the skip answer
                } else if !(url.starts_with("http://") || url.starts_with("https://"))
                    || url.chars().any(|c| c.is_whitespace())
                {
                    Some("Base URL: an http:// or https:// URL, no spaces.".into())
                } else if self.pengy_model.chars().any(|c| c.is_whitespace()) {
                    Some("Model: one name, no spaces.".into())
                } else {
                    None
                }
            }
            _ => None,
        }
    }

    fn selected_disk(&self) -> Option<&DiskChoice> {
        let path = self.model.selected_disk()?;
        self.disks.as_ref().ok()?.iter().find(|d| d.path == path)
    }

    /// Advance, passing through the screen-less Layout and Network steps.
    fn advance(&mut self) -> Task<cosmic::Action<Message>> {
        if self.blocker().is_some() || !self.model.route(WizardMessage::Continue) {
            return Task::none();
        }
        while matches!(self.step(), Step::Layout | Step::Network) {
            if !self.model.route(WizardMessage::Continue) {
                break;
            }
        }
        if self.step() == Step::Progress {
            return self.start_install();
        }
        Task::none()
    }

    fn go_back(&mut self) {
        self.model.route(WizardMessage::Back);
        while matches!(self.step(), Step::Layout | Step::Network) {
            self.model.route(WizardMessage::Back);
        }
    }

    /// Run `pengyos-install image` and stream its JSON progress lines back.
    fn start_install(&mut self) -> Task<cosmic::Action<Message>> {
        let (Some(disk), Some(host), Some(user)) =
            (self.model.selected_disk(), self.model.hostname(), self.model.username())
        else {
            return Task::none();
        };
        let mut args: Vec<String> = [
            "image", "--target", disk, "--hostname", host, "--user", user,
            "--yes", "--password-stdin", "--progress", "json",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        if self.autologin {
            args.push("--autologin".into());
        }
        // The assistant's endpoint, only when the screen was filled in. The key
        // rides on stdin (line 2, after the password), never on argv.
        let base_url = self.pengy_base_url.trim().to_string();
        let api_key = self.pengy_api_key.trim().to_string();
        let pengy_model = self.pengy_model.trim().to_string();
        if !base_url.is_empty() {
            args.push("--pengy-base-url".into());
            args.push(base_url.clone());
            if !api_key.is_empty() {
                args.push("--pengy-api-key-stdin".into());
            }
            if !pengy_model.is_empty() {
                args.push("--pengy-model".into());
                args.push(pengy_model.clone());
            }
        }
        let engine = self.flags.engine.clone();
        let password = self.password.clone();
        self.run = Run::Running;
        self.progress = 0.0;
        self.status = "Starting…".into();

        let (tx, rx) = mpsc::unbounded::<Message>();
        std::thread::spawn(move || {
            let send = |m: Message| {
                let _ = tx.unbounded_send(m);
            };
            let mut child = match Command::new(&engine)
                .args(&args)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
            {
                Ok(c) => c,
                Err(e) => return send(Message::Finished(Err(format!("cannot run {engine}: {e}")))),
            };
            if let Some(mut stdin) = child.stdin.take() {
                let _ = writeln!(stdin, "{password}");
                // Line 2 is the Pengy API key, only when one was typed.
                if !api_key.is_empty() {
                    let _ = writeln!(stdin, "{api_key}");
                }
            }
            let mut last_error = None;
            if let Some(stdout) = child.stdout.take() {
                for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                    let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
                    let msg = v.get("message").and_then(|m| m.as_str()).unwrap_or("").to_string();
                    if v.get("phase").and_then(|p| p.as_str()) == Some("error") {
                        last_error = Some(msg);
                        continue;
                    }
                    let fraction = v.get("fraction").and_then(|f| f.as_f64()).unwrap_or(0.0) as f32;
                    let phase = v.get("phase").and_then(|p| p.as_str()).unwrap_or("");
                    send(Message::Progress(overall(phase, fraction), msg));
                }
            }
            let mut stderr = String::new();
            if let Some(mut e) = child.stderr.take() {
                let _ = e.read_to_string(&mut stderr);
            }
            let result = match child.wait() {
                Ok(s) if s.success() => Ok(()),
                Ok(_) | Err(_) => Err(last_error.unwrap_or_else(|| {
                    stderr.trim().lines().last().unwrap_or("the installer failed").to_string()
                })),
            };
            send(Message::Finished(result));
        });
        cosmic::task::stream(rx)
    }
}

/// Map an engine phase + phase-fraction onto one 0..1 bar.
fn overall(phase: &str, fraction: f32) -> f32 {
    // Same split as pengyos_install.image.PHASE_SPAN.
    let (start, span) = match phase {
        "verify" => (0.0, 0.10),
        "partition" => (0.10, 0.02),
        "write" => (0.12, 0.73),
        "root" => (0.85, 0.05),
        "boot" => (0.90, 0.03),
        "provision" => (0.93, 0.07),
        "done" => (1.0, 0.0),
        _ => (0.0, 0.0),
    };
    start + span * fraction.clamp(0.0, 1.0)
}

impl cosmic::Application for AppModel {
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

    fn init(core: cosmic::Core, flags: Self::Flags) -> (Self, Task<cosmic::Action<Self::Message>>) {
        let disks = load_disks(&flags.engine);
        let mut model = Model::new();
        model.set_hostname("pengyos");
        let mut app = AppModel {
            core,
            model,
            flags,
            disks,
            password: String::new(),
            pengy_base_url: String::new(),
            pengy_api_key: String::new(),
            pengy_model: String::new(),
            confirm: String::new(),
            autologin: false,
            progress: 0.0,
            status: String::new(),
            run: Run::NotStarted,
            restart: Restart::live(),
        };
        let task = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Install PengyOS".to_string(), id),
            None => Task::none(),
        };
        (app, task)
    }

    fn on_close_requested(&self, _id: cosmic::iced::window::Id) -> Option<Self::Message> {
        None
    }

    fn update(&mut self, msg: Self::Message) -> Task<cosmic::Action<Self::Message>> {
        match msg {
            Message::Wizard(WizardMessage::Continue) => return self.advance(),
            Message::Wizard(WizardMessage::Back) => self.go_back(),
            Message::Wizard(WizardMessage::SelectDisk(disk)) => {
                self.model.route(WizardMessage::SelectDisk(disk));
                // The engine's only layout: erase the whole disk.
                self.model.route(WizardMessage::SelectLayout(Layout::Wipe));
            }
            Message::Wizard(WizardMessage::SetPassword(p)) => {
                self.model.route(WizardMessage::SetPassword(p.clone()));
                self.password = p;
            }
            Message::Wizard(other) => {
                self.model.route(other);
            }
            Message::SetConfirm(c) => self.confirm = c,
            Message::SetAutologin(on) => self.autologin = on,
            // The Pengy screen: keep the text locally (the key is a secret and
            // the engine reads it from stdin) and mirror it into the model so
            // the plan and the Summary screen agree with what is on screen.
            Message::SetPengyBaseUrl(url) => {
                self.pengy_base_url = url.clone();
                self.model.route(WizardMessage::SetPengyBaseUrl(url));
            }
            Message::SetPengyApiKey(key) => {
                self.pengy_api_key = key.clone();
                self.model.route(WizardMessage::SetPengyApiKey(key));
            }
            Message::SetPengyModel(model) => {
                self.pengy_model = model.clone();
                self.model.route(WizardMessage::SetPengyModel(model));
            }
            Message::RefreshDisks => self.disks = load_disks(&self.flags.engine),
            Message::Progress(fraction, status) => {
                self.progress = self.progress.max(fraction);
                if !status.is_empty() {
                    self.status = status;
                }
            }
            Message::Finished(Ok(())) => {
                self.run = Run::Done;
                self.progress = 1.0;
            }
            Message::Finished(Err(e)) => self.run = Run::Failed(e),
            Message::Reboot => self.restart.now(),
            // The live desktop stays; the dock / App Library reopen the installer.
            Message::TryLive => std::process::exit(0),
            Message::StartInstall => {
                // The erase is confirmed on the Summary screen ("Erase disk and
                // install"); choosing Install here is the Welcome step's answer.
                self.model.route(WizardMessage::AcceptTerms(true));
                return self.advance();
            }
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Self::Message> {
        let spacing = cosmic::theme::spacing();
        let (title, body): (&str, Element<'_, Message>) = match self.step() {
            Step::Welcome => ("Welcome to PengyOS", self.view_welcome()),
            Step::Disk => ("Choose a disk", self.view_disk()),
            Step::User => ("Your computer and account", self.view_user()),
            Step::Pengy => ("The assistant", self.view_pengy()),
            Step::Summary => ("Ready to install", self.view_summary()),
            Step::Progress => match self.run {
                Run::Done => ("PengyOS is installed", self.view_progress()),
                Run::Failed(_) => ("The install failed", self.view_progress()),
                _ => ("Installing PengyOS", self.view_progress()),
            },
            Step::Layout | Step::Network => ("", column::with_children(Vec::new()).into()),
        };
        let screens = [
            Step::Welcome,
            Step::Disk,
            Step::User,
            Step::Pengy,
            Step::Summary,
            Step::Progress,
        ];
        let index = screens.iter().position(|s| *s == self.step()).unwrap_or(0) + 1;
        // The body scrolls so the footer (Back / Continue) is always on screen,
        // whatever size the compositor gives the window.
        let content = column::with_children([
            text::caption(format!("Step {index} of {}", screens.len())).into(),
            text::title3(title).into(),
            widget::scrollable(body).height(Length::Fill).into(),
            self.view_footer(),
        ])
        .spacing(spacing.space_s)
        .max_width(640);
        container(content)
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(spacing.space_m)
            .align_x(cosmic::iced::alignment::Horizontal::Center)
            .into()
    }

    fn subscription(&self) -> Subscription<Self::Message> {
        Subscription::none()
    }
}

impl AppModel {
    fn view_footer(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut buttons: Vec<Element<'_, Message>> = Vec::new();
        let hint = if matches!(self.step(), Step::Progress) { None } else { self.blocker() };
        buttons.push(
            text::caption(hint.unwrap_or_default())
                .width(Length::Fill)
                .into(),
        );
        match (self.step(), &self.run) {
            (Step::Progress, Run::Done) => {
                buttons.push(button::suggested("Restart now").on_press(Message::Reboot).into());
            }
            (Step::Progress, Run::Failed(_)) => {
                buttons.push(
                    button::standard("Back").on_press(Message::Wizard(WizardMessage::Back)).into(),
                );
            }
            (Step::Progress, _) | (Step::Welcome, _) => {}
            (step, _) => {
                if step != Step::Welcome {
                    buttons.push(
                        button::standard("Back").on_press(Message::Wizard(WizardMessage::Back)).into(),
                    );
                }
                let enabled = self.blocker().is_none();
                let next = if step == Step::Summary {
                    button::destructive("Erase disk and install")
                } else {
                    button::suggested("Continue")
                };
                buttons.push(
                    next.on_press_maybe(enabled.then_some(Message::Wizard(WizardMessage::Continue)))
                        .into(),
                );
            }
        }
        row::with_children(buttons)
            .spacing(spacing.space_s)
            .align_y(Alignment::Center)
            .into()
    }

    fn view_welcome(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let buttons = row::with_children([
            button::standard("Try PengyOS").on_press(Message::TryLive).into(),
            button::suggested("Install PengyOS").on_press(Message::StartInstall).into(),
        ])
        .spacing(spacing.space_s);
        column::with_children([
            text::body(
                "PengyOS is running from the USB stick. Try it first — nothing on this \
                 computer changes, and Settings (display scaling, accessibility) works as \
                 usual — or install it onto a disk in this computer.",
            )
            .into(),
            text::caption(
                "You can start the installer later from the dock or the App Library.",
            )
            .into(),
            buttons.into(),
        ])
        .spacing(spacing.space_m)
        .into()
    }

    fn view_disk(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let selected = self.model.selected_disk();
        let mut sec = section();
        match &self.disks {
            Err(e) => {
                sec = sec.add(item::builder("Could not list the disks").control(text::body(e.clone())));
            }
            Ok(disks) if disks.iter().all(|d| !d.installable) => {
                sec = sec.add(text::body(
                    "No disk to install to was found. Connect or enable one, then refresh.",
                ));
            }
            Ok(_) => {}
        }
        if let Ok(disks) = &self.disks {
            for (index, d) in disks.iter().enumerate() {
                let label: Element<'_, Message> = column::with_children([
                    text::body(d.label.clone()).into(),
                    text::caption(d.note.clone()).into(),
                ])
                .into();
                if d.installable {
                    let path = d.path.clone();
                    let chosen = (selected == Some(d.path.as_str())).then_some(index);
                    sec = sec.add(radio(label, index, chosen, move |_| {
                        Message::Wizard(WizardMessage::SelectDisk(path.clone()))
                    }));
                } else {
                    sec = sec.add(container(label).padding([0, 0, 0, spacing.space_l]));
                }
            }
        }
        column::with_children([
            sec.into(),
            button::text("Refresh").on_press(Message::RefreshDisks).into(),
        ])
        .spacing(spacing.space_s)
        .into()
    }

    fn view_user(&self) -> Element<'_, Message> {
        let host = self.model.hostname().unwrap_or("").to_string();
        let user = self.model.username().unwrap_or("").to_string();
        section()
            .add(item::builder("Computer name").control(
                widget::text_input("pengyos", host)
                    .on_input(|s| Message::Wizard(WizardMessage::SetHostname(s))),
            ))
            .add(item::builder("Username").control(
                widget::text_input("e.g. alex", user)
                    .on_input(|s| Message::Wizard(WizardMessage::SetUsername(s))),
            ))
            .add(item::builder("Password").description("Also becomes the root password.").control(
                widget::secure_input("", self.password.as_str(), None, true)
                    .on_input(|s| Message::Wizard(WizardMessage::SetPassword(s))),
            ))
            .add(item::builder("Repeat password").control(
                widget::secure_input("", self.confirm.as_str(), None, true).on_input(Message::SetConfirm),
            ))
            .add(
                item::builder("Log in automatically")
                    .description(
                        "Skip the login screen at startup, and the lock screen after \
                         sleep. Anyone who can switch this computer on gets your desktop.",
                    )
                    // A plain switch as the control, not `.toggler()`: that
                    // wraps the row in a list button around the switch, and a
                    // click on the switch left the row button holding the
                    // pointer -- the next click (Continue) was swallowed and
                    // had to be made twice.
                    .control(widget::toggler(self.autologin).on_toggle(Message::SetAutologin)),
            )
            .into()
    }

    fn view_pengy(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        column::with_children([
            section()
                .add(item::builder("Base URL").description(
                    "Any OpenAI-compatible endpoint: Ollama (http://127.0.0.1:11434/v1),                      llama.cpp, LM Studio, OpenRouter, OpenAI.",
                ).control(
                    widget::text_input("http://127.0.0.1:11434/v1", self.pengy_base_url.as_str())
                        .on_input(Message::SetPengyBaseUrl),
                ))
                .add(item::builder("API key").description(
                    "Local endpoints need none.",
                ).control(
                    widget::secure_input("", self.pengy_api_key.as_str(), None, true)
                        .on_input(Message::SetPengyApiKey),
                ))
                .add(item::builder("Model").description(
                    "The name the endpoint knows, e.g. qwen3:8b. Leave it empty to                      choose later.",
                ).control(
                    widget::text_input("qwen3:8b", self.pengy_model.as_str())
                        .on_input(Message::SetPengyModel),
                ))
                .into(),
            text::caption(
                "Skip this screen and Pengy keeps its own defaults: the local                  endpoint, no key, no model. You can set all of it later in Pengy's                  Settings, and your skills live in ~/skills.",
            )
            .into(),
        ])
        .spacing(spacing.space_m)
        .into()
    }

    fn view_summary(&self) -> Element<'_, Message> {
        let disk = self
            .selected_disk()
            .map(|d| d.label.clone())
            .unwrap_or_else(|| self.model.selected_disk().unwrap_or("").to_string());
        column::with_children([
            section()
                .add(item::builder("Disk").control(text::body(disk)))
                .add(item::builder("Computer name").control(text::body(
                    self.model.hostname().unwrap_or("").to_string(),
                )))
                .add(item::builder("Username").control(text::body(
                    self.model.username().unwrap_or("").to_string(),
                )))
                .add(item::builder("Login").control(text::body(if self.autologin {
                    "Automatic"
                } else {
                    "Password required"
                })))
                .add(item::builder("Pengy").control(text::body(if self.pengy_base_url.trim().is_empty() {
                    "Not configured — Pengy keeps its defaults".to_string()
                } else {
                    let mut line = self.pengy_base_url.trim().to_string();
                    if !self.pengy_model.trim().is_empty() {
                        line.push_str(&format!("  model {}", self.pengy_model.trim()));
                    }
                    line
                })))
                .into(),
            text::body(
                "Everything on this disk will be erased and replaced with PengyOS. \
                 This cannot be undone.",
            )
            .into(),
        ])
        .spacing(cosmic::theme::spacing().space_m)
        .into()
    }

    fn view_progress(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let (message, bar_visible) = match &self.run {
            Run::Done => (
                "Remove the USB stick, then restart to start PengyOS.".to_string(),
                true,
            ),
            Run::Failed(e) => (
                format!("{e}\n\nThe chosen disk may no longer be usable until PengyOS is installed on it."),
                false,
            ),
            _ => (format!("{}  —  do not turn off the computer.", self.status), true),
        };
        let mut children: Vec<Element<'_, Message>> = Vec::new();
        if bar_visible {
            children.push(widget::determinate_linear(self.progress).width(Length::Fill).into());
        }
        children.push(text::body(message).into());
        column::with_children(children).spacing(spacing.space_m).into()
    }
}
