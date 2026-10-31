//! The "Remote Desktop" window (libcosmic).
//!
//! An on/off switch, where other computers connect and as whom, the RDP
//! password, and "ask me again". Every change is a `pengyos-remote …` run off
//! the UI thread, as the person; it needs no administrator.

use std::time::Duration;

use cosmic::iced::{Length, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::{item, section};
use cosmic::widget::{self, button, column, container, nav_bar, row, text};

use crate::{forget_argv, off_argv, on_argv, password_argv, password_problem, run, status, Status};

#[derive(Debug, Clone)]
pub enum Message {
    Refresh,
    Loaded(Result<Status, String>),
    Toggle(bool),
    SetPassword(String),
    SetPasswordAgain(String),
    SavePassword,
    Forget,
    /// A change finished: Ok(what to say) or Err(why not).
    Done(Result<String, String>),
}

pub struct App {
    core: cosmic::Core,
    state: Option<Status>,
    password: String,
    password_again: String,
    busy: bool,
    error: Option<String>,
    note: Option<String>,
}

impl App {
    fn load(&self) -> Task<cosmic::Action<Message>> {
        cosmic::task::future(async move {
            let r = std::thread::spawn(status).join().unwrap_or(Err("crashed".into()));
            Message::Loaded(r)
        })
    }

    /// Run a pengyos-remote command line off the UI thread.
    fn change(&mut self, argv: Vec<String>, stdin: Option<String>, ok: String) -> Task<cosmic::Action<Message>> {
        self.busy = true;
        self.error = None;
        self.note = None;
        cosmic::task::future(async move {
            let r = std::thread::spawn(move || run(&argv, stdin.as_deref()).map(|_| ok))
                .join()
                .unwrap_or(Err("crashed".into()));
            Message::Done(r)
        })
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
            state: None,
            password: String::new(),
            password_again: String::new(),
            busy: false,
            error: None,
            note: None,
        };
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Remote Desktop".into(), id),
            None => Task::none(),
        };
        let load = app.load();
        (app, Task::batch([title, load]))
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Refresh => {
                if !self.busy {
                    return self.load();
                }
            }
            Message::Loaded(Ok(s)) => self.state = Some(s),
            Message::Loaded(Err(e)) => self.error = Some(e),
            Message::Toggle(true) => {
                let first = !self.state.as_ref().is_some_and(|s| s.consent_saved);
                let ok = if first {
                    "Remote desktop is on. The first time someone connects, this screen asks you to allow it."
                } else {
                    "Remote desktop is on."
                };
                return self.change(on_argv(), None, ok.into());
            }
            Message::Toggle(false) => {
                return self.change(off_argv(), None, "Remote desktop is off. Nobody can connect.".into());
            }
            Message::SetPassword(p) => self.password = p,
            Message::SetPasswordAgain(p) => self.password_again = p,
            Message::SavePassword => {
                if let Some(p) = password_problem(&self.password, &self.password_again) {
                    self.error = Some(p);
                    return Task::none();
                }
                let pw = format!("{}\n", self.password);
                self.password.clear();
                self.password_again.clear();
                let ok = "The RDP password is set. Use it, with your user name, from the other computer.";
                return self.change(password_argv(), Some(pw), ok.into());
            }
            Message::Forget => {
                return self.change(forget_argv(), None, "The next connection asks you again.".into());
            }
            Message::Done(r) => {
                self.busy = false;
                match r {
                    Ok(said) => self.note = Some(said),
                    Err(e) => self.error = Some(e),
                }
                return self.load();
            }
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut body: Vec<Element<'_, Message>> = Vec::new();
        if let Some(e) = &self.error {
            body.push(text::body(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        if let Some(n) = &self.note {
            body.push(text::body(n.clone()).into());
        }
        body.push(self.view_switch());
        if let Some(s) = &self.state {
            body.push(self.view_connect(s));
        }
        body.push(self.view_password());
        let content = column::with_children(body).spacing(spacing.space_m).max_width(560);
        container(widget::scrollable(content))
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(spacing.space_m)
            .align_x(cosmic::iced::alignment::Horizontal::Center)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        // A change from a terminal (pengyos-remote on) shows up here too.
        cosmic::iced::time::every(Duration::from_secs(5)).map(|_| Message::Refresh)
    }
}

impl App {
    fn view_switch(&self) -> Element<'_, Message> {
        let (title, about, on, can_turn_on) = match &self.state {
            None => ("Checking…".to_string(), String::new(), false, false),
            Some(s) => {
                let about = if !s.password_set {
                    "Set an RDP password below first.".to_string()
                } else if s.enabled {
                    "Other computers on your network can see this screen and use your mouse and keyboard, \
                     after giving your user name and the RDP password."
                        .to_string()
                } else {
                    "Turn it on to use this desktop from another computer: Remote Desktop Connection on \
                     Windows, Windows App on a Mac, Remmina on Linux."
                        .to_string()
                };
                (s.headline(), about, s.enabled, s.password_set)
            }
        };
        let toggle = widget::toggler(on);
        let toggle = if !self.busy && (on || can_turn_on) { toggle.on_toggle(Message::Toggle) } else { toggle };
        section().add(item::builder(title).description(about).control(toggle)).into()
    }

    fn view_connect<'a>(&'a self, s: &'a Status) -> Element<'a, Message> {
        let mut sec = section().title("Connecting");
        sec = sec.add(item("Computer", text::body(s.connect_to().join("\n"))));
        sec = sec.add(item("User name", text::body(s.user.clone())));
        if let Some(fp) = &s.fingerprint {
            sec = sec.add(text::caption(format!(
                "The other computer asks you to trust this one's certificate once. It should show SHA-256 {fp}."
            )));
        }
        let ask: Element<'_, Message> = if s.consent_saved {
            item::builder("Permission")
                .description("You allowed sharing this screen. Connections no longer ask.")
                .control(button::standard("Ask again").on_press_maybe((!self.busy).then_some(Message::Forget)))
            .into()
        } else {
            text::caption("The first connection asks on this screen whether to allow it.").into()
        };
        sec = sec.add(ask);
        if s.enabled && !s.running() && !s.log.is_empty() {
            sec = sec.add(text::caption(format!("Last messages:\n{}", s.log.join("\n"))));
        }
        sec.into()
    }

    fn view_password(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let set = self.state.as_ref().is_some_and(|s| s.password_set);
        let state = if set {
            "A password is set. Type a new one here to change it."
        } else {
            "Not set yet: nobody can connect until you set one."
        };
        let fields = column::with_children([
            text::body(state).into(),
            text::caption("It is not your login password: it only opens remote desktop, so it can be one you are happy to type on another computer.").into(),
            widget::secure_input("New RDP password", self.password.as_str(), None, true)
                .on_input(Message::SetPassword)
                .into(),
            widget::secure_input("Again", self.password_again.as_str(), None, true)
                .on_input(Message::SetPasswordAgain)
                .on_submit(|_| Message::SavePassword)
                .into(),
            row::with_children([button::standard("Set password")
                .on_press_maybe((!self.busy && !self.password.is_empty()).then_some(Message::SavePassword))
                .into()])
            .into(),
        ])
        .spacing(spacing.space_xs);
        column::with_children([
            text::title4("RDP password").into(),
            container(fields).padding(spacing.space_s).into(),
        ])
        .spacing(spacing.space_xs)
        .into()
    }
}
