//! The "File Sharing" window (libcosmic).
//!
//! It shows what is shared and how other computers open it, shares a folder,
//! stops sharing one, and sets the person's share password. Every change is a
//! `pkexec /usr/bin/pengyos-shares …` run off the UI thread; administrators
//! (wheel) need no password for it (`60-pengyos-shares.rules`).

use std::path::PathBuf;
use std::time::Duration;

use cosmic::dialog::file_chooser;
use cosmic::iced::{Alignment, Length, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::{item, section};
use cosmic::widget::{self, button, column, container, nav_bar, row, text};

use crate::{
    add_argv, addresses, current_user, describe, is_admin, name_problem, passwd_argv, password_problem,
    path_problem, remove_argv, run, status, suggest_name, State,
};

/// The Share-a-folder page: a new share, or `editing` an existing one.
#[derive(Clone, Debug, Default)]
struct Form {
    path: String,
    name: String,
    /// The name was typed, so picking another folder no longer replaces it.
    name_typed: bool,
    read_only: bool,
    editing: Option<String>,
}

#[derive(Debug, Clone)]
pub enum Message {
    Refresh,
    Loaded(Result<State, String>),
    NewShare,
    Edit(String),
    Back,
    PickFolder,
    Picked(Option<PathBuf>),
    SetPath(String),
    SetName(String),
    SetReadOnly(bool),
    Save,
    AskRemove(String),
    ConfirmRemove,
    CloseDialog,
    SetPassword(String),
    SetPasswordAgain(String),
    SavePassword,
    /// A change finished: Ok(what to say) or Err(why not).
    Done(Result<String, String>),
}

pub struct App {
    core: cosmic::Core,
    admin: bool,
    me: String,
    state: Option<State>,
    form: Option<Form>,
    remove: Option<String>,
    password: String,
    password_again: String,
    busy: bool,
    error: Option<String>,
    note: Option<String>,
}

impl App {
    fn load(&self) -> Task<cosmic::Action<Message>> {
        let admin = self.admin;
        cosmic::task::future(async move {
            let r = std::thread::spawn(move || status(admin)).join().unwrap_or(Err("crashed".into()));
            Message::Loaded(r)
        })
    }

    /// Run a pengyos-shares command line off the UI thread.
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

    fn shares(&self) -> &[crate::Share] {
        self.state.as_ref().map(|s| s.shares.as_slice()).unwrap_or(&[])
    }

    fn host(&self) -> String {
        self.state.as_ref().map(|s| s.host.clone()).filter(|h| !h.is_empty()).unwrap_or_else(|| "this-computer".into())
    }

    /// What is wrong with the form, if anything (the Share button waits for it).
    fn form_problem(&self, f: &Form) -> Option<String> {
        path_problem(&f.path).or_else(|| name_problem(&f.name, self.shares())).or_else(|| {
            (f.editing.is_none() && self.shares().iter().any(|s| s.name == f.name))
                .then(|| format!("{} is already a share name.", f.name))
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
            admin: is_admin(),
            me: current_user(),
            state: None,
            form: None,
            remove: None,
            password: String::new(),
            password_again: String::new(),
            busy: false,
            error: None,
            note: None,
        };
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("File Sharing".into(), id),
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
            Message::NewShare => {
                self.error = None;
                self.form = Some(Form::default());
            }
            Message::Edit(name) => {
                if let Some(s) = self.shares().iter().find(|s| s.name == name) {
                    self.form = Some(Form {
                        path: s.path.clone(),
                        name: s.name.clone(),
                        name_typed: true,
                        read_only: s.read_only,
                        editing: Some(s.name.clone()),
                    });
                }
            }
            Message::Back => self.form = None,
            Message::PickFolder => {
                let dialog = file_chooser::open::Dialog::new().title("Choose a folder to share");
                return cosmic::task::future(async move {
                    Message::Picked(dialog.open_folder().await.ok().and_then(|r| r.url().to_file_path().ok()))
                });
            }
            Message::Picked(Some(p)) => return self.update(Message::SetPath(p.to_string_lossy().into_owned())),
            Message::Picked(None) => {}
            Message::SetPath(p) => {
                if let Some(f) = self.form.as_mut() {
                    if !f.name_typed && f.editing.is_none() {
                        f.name = suggest_name(&p);
                    }
                    f.path = p;
                }
            }
            Message::SetName(n) => {
                if let Some(f) = self.form.as_mut() {
                    f.name = n;
                    f.name_typed = true;
                }
            }
            Message::SetReadOnly(on) => {
                if let Some(f) = self.form.as_mut() {
                    f.read_only = on;
                }
            }
            Message::Save => {
                let Some(f) = self.form.clone() else { return Task::none() };
                if let Some(p) = self.form_problem(&f) {
                    self.error = Some(p);
                    return Task::none();
                }
                // Changing a share keeps who may use it; a new one is for you.
                let users = f
                    .editing
                    .as_deref()
                    .and_then(|n| self.shares().iter().find(|s| s.name == n))
                    .map(|s| s.users.clone())
                    .filter(|u| !u.is_empty())
                    .unwrap_or_else(|| vec![self.me.clone()]);
                let (win, _) = addresses(&self.host(), &f.name);
                let ok = if f.editing.is_some() {
                    format!("Saved {}.", f.name)
                } else {
                    format!("Shared {} as {win}.", f.path)
                };
                return self.change(add_argv(&f.name, &f.path, f.read_only, &users), None, ok);
            }
            Message::AskRemove(name) => self.remove = Some(name),
            Message::CloseDialog => self.remove = None,
            Message::ConfirmRemove => {
                if let Some(name) = self.remove.take() {
                    let ok = format!("Stopped sharing {name}. The folder and its files are untouched.");
                    return self.change(remove_argv(&name), None, ok);
                }
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
                let ok = "Your share password is set. Use it, with your user name, from other computers.".to_string();
                return self.change(passwd_argv(&self.me), Some(pw), ok);
            }
            Message::Done(r) => {
                self.busy = false;
                match r {
                    Ok(said) => {
                        self.note = Some(said);
                        self.form = None;
                    }
                    Err(e) => self.error = Some(e),
                }
                return self.load();
            }
        }
        Task::none()
    }

    fn dialog(&self) -> Option<Element<'_, Message>> {
        let name = self.remove.as_ref()?;
        let path = self.shares().iter().find(|s| &s.name == name).map(|s| s.path.clone()).unwrap_or_default();
        Some(
            widget::dialog()
                .title(format!("Stop sharing {name}?"))
                .body(format!(
                    "Other computers will no longer reach {path}. The folder and its files stay as they are."
                ))
                .primary_action(button::destructive("Stop sharing").on_press(Message::ConfirmRemove))
                .secondary_action(button::standard("Cancel").on_press(Message::CloseDialog))
                .into(),
        )
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
        match &self.form {
            Some(f) => body.push(self.view_form(f)),
            None => {
                body.push(self.view_status());
                body.push(self.view_shares());
                body.push(self.view_password());
            }
        }
        let content = column::with_children(body).spacing(spacing.space_m).max_width(600);
        container(widget::scrollable(content))
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(spacing.space_m)
            .align_x(cosmic::iced::alignment::Horizontal::Center)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        // A share added from a terminal shows up here too.
        cosmic::iced::time::every(Duration::from_secs(10)).map(|_| Message::Refresh)
    }
}

impl App {
    fn view_status(&self) -> Element<'_, Message> {
        let (title, about) = match &self.state {
            None => ("Checking…".to_string(), String::new()),
            Some(s) if s.active => (
                "Sharing is on".to_string(),
                format!(
                    "Computers on your home or office network reach the folders below at \\\\{} (Windows) or smb://{} (Mac, Linux). Nothing is reachable from the internet.",
                    self.host(),
                    self.host()
                ),
            ),
            Some(_) => (
                "Sharing is off".to_string(),
                "Share a folder and it turns on by itself; stop sharing the last one and it turns off.".to_string(),
            ),
        };
        let add = button::suggested("Share a folder…").on_press_maybe((self.admin && !self.busy).then_some(Message::NewShare));
        let mut s = section().add(item::builder(title).description(about).control(add));
        if !self.admin {
            s = s.add(text::body(
                "Only administrators can share folders. Ask someone in the wheel group, or have them add you to it.",
            ));
        }
        s.into()
    }

    fn view_shares(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let shares = self.shares();
        if shares.is_empty() {
            return section().title("Shared folders").add(text::body("Nothing is shared.")).into();
        }
        let mut s = section().title("Shared folders");
        let idle = self.admin && !self.busy;
        for sh in shares {
            let (win, url) = addresses(&self.host(), &sh.name);
            let mut lines = vec![sh.path.clone(), describe(sh), format!("{win}    {url}")];
            if !sh.comment.is_empty() {
                lines.insert(0, sh.comment.clone());
            }
            let buttons = row::with_children([
                button::standard("Change…").on_press_maybe(idle.then(|| Message::Edit(sh.name.clone()))).into(),
                button::destructive("Stop sharing")
                    .on_press_maybe(idle.then(|| Message::AskRemove(sh.name.clone())))
                    .into(),
            ])
            .spacing(spacing.space_xs)
            .align_y(Alignment::Center);
            s = s.add(item::builder(sh.name.clone()).description(lines.join("\n")).control(buttons));
        }
        s.into()
    }

    fn view_password(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let state = match self.state.as_ref().and_then(|s| s.has_password(&self.me)) {
            Some(true) => "You have a share password. Set a new one here to change it.".to_string(),
            Some(false) => format!(
                "Not set yet: other computers cannot open your shares until you set one. They log in as {} with it.",
                self.me
            ),
            None => "Other computers log in with your user name and this password.".to_string(),
        };
        let fields = column::with_children([
            text::body(state).into(),
            text::caption("It is not your login password: it only opens shared folders, so it can be one you are happy to type on another computer.").into(),
            widget::secure_input("New share password", self.password.as_str(), None, true)
                .on_input(Message::SetPassword)
                .into(),
            widget::secure_input("Again", self.password_again.as_str(), None, true)
                .on_input(Message::SetPasswordAgain)
                .on_submit(|_| Message::SavePassword)
                .into(),
            row::with_children([button::standard("Set password")
                .on_press_maybe((self.admin && !self.busy && !self.password.is_empty()).then_some(Message::SavePassword))
                .into()])
            .into(),
        ])
        .spacing(spacing.space_xs);
        column::with_children([
            text::title4("Your share password").into(),
            container(fields).padding(spacing.space_s).into(),
        ])
        .spacing(spacing.space_xs)
        .into()
    }

    fn view_form<'a>(&'a self, f: &'a Form) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        let editing = f.editing.is_some();
        let folder = row::with_children([
            widget::text_input("/home/you/Music", f.path.as_str())
                .on_input(Message::SetPath)
                .width(Length::Fill)
                .into(),
            button::standard("Choose…").on_press_maybe((!editing).then_some(Message::PickFolder)).into(),
        ])
        .spacing(spacing.space_xs)
        .align_y(Alignment::Center);
        let name: Element<'_, Message> = if editing {
            text::body(f.name.clone()).into()
        } else {
            widget::text_input("Music", f.name.as_str()).on_input(Message::SetName).into()
        };
        let (win, url) = addresses(&self.host(), if f.name.is_empty() { "NAME" } else { &f.name });
        let problem = self.form_problem(f);
        let mut c: Vec<Element<'_, Message>> = vec![
            text::title3(if editing { format!("Change {}", f.name) } else { "Share a folder".into() }).into(),
            text::body("Folder").into(),
            folder.into(),
            text::body("Name on the network").into(),
            name,
            text::caption(format!("Other computers open it at {win} or {url}.")).into(),
            section()
                .add(
                    item::builder("Read-only")
                        .description("Others can open and copy the files, but not change or delete them.")
                        .control(widget::toggler(f.read_only).on_toggle(Message::SetReadOnly)),
                )
                .into(),
        ];
        if let Some(p) = &problem {
            c.push(text::caption(p.clone()).into());
        }
        c.push(
            row::with_children([
                button::standard("Cancel").on_press(Message::Back).into(),
                button::suggested(if editing { "Save" } else { "Share" })
                    .on_press_maybe((problem.is_none() && !self.busy).then_some(Message::Save))
                    .into(),
            ])
            .spacing(spacing.space_s)
            .into(),
        );
        column::with_children(c).spacing(spacing.space_s).into()
    }
}
