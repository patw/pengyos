//! The "Disks" window (libcosmic).
//!
//! Lists the removable drives ([`crate::drives`]) and offers Format…, Write
//! image… and Eject for each. Every UDisks call runs on its own thread and
//! reports back through a channel, because a call can wait on a polkit password
//! prompt for as long as the user takes.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use cosmic::dialog::file_chooser::{self, FileFilter};
use cosmic::iced::futures::channel::mpsc;
use cosmic::iced::{Alignment, Length, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::section;
use cosmic::widget::{self, button, column, container, icon, nav_bar, row, text};
use zbus::blocking::Connection;

use crate::{human, plan_eject, plan_format, udisks, Drive, Fs, APP_ID, IMAGE_EXTENSIONS};

#[derive(Debug, Clone)]
pub enum Message {
    Refresh,
    Drives(Result<Vec<Drive>, String>),
    AskFormat(String),
    FormatFs(usize),
    FormatName(String),
    ConfirmFormat,
    /// Write image… on a drive: use the image already chosen, or ask for one.
    AskWrite(String),
    ImageChosen(String, Option<PathBuf>),
    ConfirmWrite,
    ClearImage,
    Eject(String),
    CloseDialog,
    Cancel,
    Progress(f32),
    /// A job finished: Ok(what to tell the user) or Err(why it failed).
    Done(Result<String, String>),
}

#[derive(Clone, Debug)]
enum Dialog {
    Format { drive: Drive, fs: usize, name: String },
    Write { drive: Drive, image: PathBuf },
}

struct Busy {
    drive: String,
    what: String,
    fraction: Option<f32>,
    cancellable: bool,
}

pub struct App {
    core: cosmic::Core,
    conn: Option<Connection>,
    drives: Vec<Drive>,
    loaded: bool,
    busy: Option<Busy>,
    dialog: Option<Dialog>,
    /// From the command line (Open with…), until it has been written.
    image: Option<PathBuf>,
    error: Option<String>,
    note: Option<String>,
    cancel: Arc<AtomicBool>,
    fs_titles: Vec<String>,
}

fn file_name(p: &Path) -> String {
    p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| p.display().to_string())
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
    fn drive(&self, object: &str) -> Option<Drive> {
        self.drives.iter().find(|d| d.object == object).cloned()
    }

    fn refresh(&self) -> Task<cosmic::Action<Message>> {
        let Some(conn) = self.conn.clone() else { return Task::none() };
        background(move |send| send(Message::Drives(udisks::drives(&conn))))
    }

    fn start(&mut self, drive: &Drive, what: String, cancellable: bool) -> Option<Connection> {
        let conn = self.conn.clone()?;
        self.error = None;
        self.note = None;
        self.dialog = None;
        self.cancel.store(false, Ordering::Relaxed);
        self.busy = Some(Busy { drive: drive.object.clone(), what, fraction: None, cancellable });
        Some(conn)
    }

    fn format(&mut self) -> Task<cosmic::Action<Message>> {
        let Some(Dialog::Format { drive, fs, name }) = self.dialog.clone() else { return Task::none() };
        let fs = Fs::ALL[fs];
        let steps = match plan_format(&drive, fs, &name) {
            Ok(s) => s,
            Err(e) => {
                self.error = Some(e);
                self.dialog = None;
                return Task::none();
            }
        };
        let Some(conn) = self.start(&drive, format!("Formatting {} as {}…", drive.name(), fs.title()), false) else {
            return Task::none();
        };
        let done = format!("{} is formatted as {}.", drive.name(), fs.title());
        background(move |send| send(Message::Done(udisks::run_all(&conn, &steps).map(|()| done))))
    }

    fn write(&mut self) -> Task<cosmic::Action<Message>> {
        let Some(Dialog::Write { drive, image }) = self.dialog.clone() else { return Task::none() };
        let Some(conn) = self.start(&drive, format!("Writing {} to {}…", file_name(&image), drive.name()), true) else {
            return Task::none();
        };
        let cancel = self.cancel.clone();
        self.image = None;
        let done = format!("{} is written to {}. You can eject it now.", file_name(&image), drive.name());
        background(move |send| {
            let r = udisks::write_image(&conn, &drive, &image, &cancel, |done, total| {
                if total > 0 {
                    send(Message::Progress(done as f32 / total as f32));
                }
            });
            send(Message::Done(r.map(|()| done)));
        })
    }

    fn eject(&mut self, drive: Drive) -> Task<cosmic::Action<Message>> {
        let steps = plan_eject(&drive);
        let Some(conn) = self.start(&drive, format!("Ejecting {}…", drive.name()), false) else { return Task::none() };
        let done = format!("{} can be unplugged.", drive.name());
        background(move |send| send(Message::Done(udisks::run_all(&conn, &steps).map(|()| done))))
    }
}

impl cosmic::Application for App {
    type Executor = cosmic::executor::Default;
    type Flags = Option<PathBuf>;
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

    fn init(core: cosmic::Core, image: Option<PathBuf>) -> (Self, Task<cosmic::Action<Message>>) {
        let (conn, error) = match udisks::connect() {
            Ok(c) => (Some(c), None),
            Err(e) => (None, Some(e)),
        };
        let mut app = App {
            core,
            conn,
            drives: Vec::new(),
            loaded: false,
            busy: None,
            dialog: None,
            image,
            error,
            note: None,
            cancel: Arc::new(AtomicBool::new(false)),
            fs_titles: Fs::ALL.iter().map(|f| f.title().to_string()).collect(),
        };
        app.set_header_title("Disks".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Disks".into(), id),
            None => Task::none(),
        };
        let refresh = app.refresh();
        (app, Task::batch([title, refresh]))
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Refresh => {
                if self.busy.is_none() {
                    return self.refresh();
                }
            }
            Message::Drives(Ok(d)) => {
                self.drives = d;
                self.loaded = true;
            }
            Message::Drives(Err(e)) => self.error = Some(e),
            Message::AskFormat(obj) => {
                self.error = None;
                self.note = None;
                if let Some(drive) = self.drive(&obj) {
                    self.dialog = Some(Dialog::Format { drive, fs: 0, name: String::new() });
                }
            }
            Message::FormatFs(i) => {
                if let Some(Dialog::Format { fs, .. }) = &mut self.dialog {
                    *fs = i.min(Fs::ALL.len() - 1);
                }
            }
            Message::FormatName(n) => {
                if let Some(Dialog::Format { name, .. }) = &mut self.dialog {
                    *name = n;
                }
            }
            Message::ConfirmFormat => return self.format(),
            Message::AskWrite(obj) => {
                self.error = None;
                self.note = None;
                if let Some(image) = self.image.clone() {
                    return cosmic::task::message(Message::ImageChosen(obj, Some(image)));
                }
                let filter = IMAGE_EXTENSIONS.iter().fold(FileFilter::new("Disk images"), |f, e| f.glob(&format!("*.{e}")));
                let dialog = file_chooser::open::Dialog::new().title("Choose a disk image to write").filter(filter);
                return cosmic::task::future(async move {
                    let path = dialog.open_file().await.ok().and_then(|r| r.url().to_file_path().ok());
                    Message::ImageChosen(obj, path)
                });
            }
            Message::ImageChosen(obj, Some(image)) => {
                if let Some(drive) = self.drive(&obj) {
                    match crate::plan_write(&drive, crate::image_size(&image)) {
                        Ok(_) => self.dialog = Some(Dialog::Write { drive, image }),
                        Err(e) => self.error = Some(e),
                    }
                }
            }
            Message::ImageChosen(_, None) => {}
            Message::ConfirmWrite => return self.write(),
            Message::ClearImage => self.image = None,
            Message::Eject(obj) => {
                if let Some(drive) = self.drive(&obj) {
                    return self.eject(drive);
                }
            }
            Message::CloseDialog => self.dialog = None,
            Message::Cancel => self.cancel.store(true, Ordering::Relaxed),
            Message::Progress(f) => {
                if let Some(b) = &mut self.busy {
                    b.fraction = Some(f.clamp(0.0, 1.0));
                }
            }
            Message::Done(r) => {
                self.busy = None;
                match r {
                    Ok(note) => self.note = Some(note),
                    Err(e) => self.error = Some(e),
                }
                return self.refresh();
            }
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut body: Vec<Element<'_, Message>> = Vec::new();
        if let Some(image) = &self.image {
            body.push(
                row::with_children([
                    text::body(format!("Ready to write {}. Choose a drive below.", file_name(image)))
                        .width(Length::Fill)
                        .into(),
                    button::text("Clear").on_press_maybe(self.busy.is_none().then_some(Message::ClearImage)).into(),
                ])
                .align_y(Alignment::Center)
                .into(),
            );
        }
        if let Some(e) = &self.error {
            body.push(text::body(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        if let Some(n) = &self.note {
            body.push(text::body(n.clone()).into());
        }
        if let Some(b) = &self.busy {
            let bar: Element<'_, Message> = match b.fraction {
                Some(f) => widget::determinate_linear(f).width(Length::Fill).into(),
                None => widget::indeterminate_linear().width(Length::Fill).into(),
            };
            let mut c: Vec<Element<'_, Message>> = vec![text::title4(b.what.clone()).into(), bar];
            if b.cancellable {
                let cancelling = self.cancel.load(Ordering::Relaxed);
                c.push(
                    row::with_children([
                        text::caption("Do not unplug the drive until this is finished.").width(Length::Fill).into(),
                        button::standard(if cancelling { "Stopping…" } else { "Stop" })
                            .on_press_maybe((!cancelling).then_some(Message::Cancel))
                            .into(),
                    ])
                    .spacing(spacing.space_s)
                    .align_y(Alignment::Center)
                    .into(),
                );
            } else {
                c.push(text::caption("Do not unplug the drive until this is finished.").into());
            }
            body.push(container(column::with_children(c).spacing(spacing.space_s)).padding(spacing.space_s).into());
        }
        body.push(self.view_drives());
        let content = column::with_children(body).spacing(spacing.space_m).max_width(620);
        let centered = container(content).width(Length::Fill).align_x(cosmic::iced::alignment::Horizontal::Center);
        container(widget::scrollable(centered)).width(Length::Fill).height(Length::Fill).padding(spacing.space_m).into()
    }

    fn dialog(&self) -> Option<Element<'_, Message>> {
        let spacing = cosmic::theme::spacing();
        let cancel = button::standard("Cancel").on_press(Message::CloseDialog);
        match self.dialog.as_ref()? {
            Dialog::Format { drive, fs, name } => {
                let f = Fs::ALL[*fs];
                let check = f.check_name(name);
                let mut c: Vec<Element<'_, Message>> = vec![
                    text::body("Type").into(),
                    widget::dropdown(&self.fs_titles, Some(*fs), Message::FormatFs).into(),
                    text::caption(f.about()).into(),
                    text::body("Name").into(),
                    widget::text_input("Optional", name.as_str()).on_input(Message::FormatName).into(),
                ];
                match &check {
                    Err(e) => c.push(text::caption(e.clone()).class(cosmic::theme::Text::Accent).into()),
                    Ok(n) if f == Fs::Fat32 && !n.is_empty() && n != name.trim() => {
                        c.push(text::caption(format!("FAT32 names are stored in capitals: {n}")).into())
                    }
                    Ok(_) => {}
                }
                Some(
                    widget::dialog()
                        .title(format!("Format {}?", drive.name()))
                        .body(drive.erase_warning())
                        .control(column::with_children(c).spacing(spacing.space_xxs))
                        .primary_action(
                            button::destructive("Format").on_press_maybe(check.is_ok().then_some(Message::ConfirmFormat)),
                        )
                        .secondary_action(cancel)
                        .into(),
                )
            }
            Dialog::Write { drive, image } => {
                let stored = std::fs::metadata(image).map(|m| m.len()).unwrap_or(0);
                let size = match crate::image_size(image) {
                    Some(n) if n != stored => format!("{}, {} unpacked", human(stored), human(n)),
                    _ => human(stored),
                };
                let body = format!("{}\n\nImage: {} ({size})", drive.erase_warning(), file_name(image));
                Some(
                    widget::dialog()
                        .title(format!("Write the image to {}?", drive.name()))
                        .body(body)
                        .primary_action(button::destructive("Write").on_press(Message::ConfirmWrite))
                        .secondary_action(cancel)
                        .into(),
                )
            }
        }
    }

    fn subscription(&self) -> Subscription<Message> {
        // Notice drives being plugged in and out.
        cosmic::iced::time::every(Duration::from_secs(2)).map(|_| Message::Refresh)
    }
}

impl App {
    fn view_drives(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        if self.drives.is_empty() {
            let msg = if self.loaded || self.conn.is_none() {
                "No removable drives. Plug in a USB stick or a memory card."
            } else {
                "Looking for drives…"
            };
            return container(text::body(msg)).padding(spacing.space_m).into();
        }
        let idle = self.busy.is_none();
        let mut s = section().title("Removable drives");
        for d in &self.drives {
            let lock = d.lock();
            let can = idle && lock.is_none();
            let mut desc = d.summary();
            if let Some(l) = &lock {
                desc.push('\n');
                desc.push_str(&l.text());
            }
            let icon_name = if matches!(d.bus.as_str(), "sdio" | "mmc") { "media-flash" } else { "drive-removable-media" };
            let working = self.busy.as_ref().is_some_and(|b| b.drive == d.object);
            let buttons = row::with_children([
                button::standard("Format…").on_press_maybe(can.then(|| Message::AskFormat(d.object.clone()))).into(),
                button::standard("Write image…").on_press_maybe(can.then(|| Message::AskWrite(d.object.clone()))).into(),
                button::standard("Eject")
                    .on_press_maybe(can.then(|| Message::Eject(d.object.clone())))
                    .into(),
            ])
            .spacing(spacing.space_xs);
            let mut info = vec![text::heading(d.name()).into(), text::caption(desc).into()];
            if working {
                info.push(text::caption("Working…").into());
            } else if lock.is_none() {
                info.push(buttons.into());
            }
            let info = column::with_children(info)
            .spacing(spacing.space_xxs)
            .width(Length::Fill);
            s = s.add(
                row::with_children([icon::from_name(icon_name).size(40).into(), info.into()])
                    .spacing(spacing.space_s)
                    .align_y(Alignment::Start),
            );
        }
        s.into()
    }
}
