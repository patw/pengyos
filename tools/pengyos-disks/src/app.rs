//! The "Disks" window (libcosmic).
//!
//! Drives down the side ([`crate::drives`]); for the chosen one, a bar of its
//! partitions and free space in proportion, and the selected partition's details
//! and actions (mount, format, grow, delete, create in free space), plus the
//! whole-drive actions (new partition table, write an image, and for removable
//! drives format and eject). Every UDisks call runs on its own thread and reports
//! back through a channel, because a call can wait on a polkit password prompt for
//! as long as the user takes.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use cosmic::dialog::file_chooser::{self, FileFilter};
use cosmic::iced::futures::channel::mpsc;
use cosmic::iced::{Alignment, Length, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::{item, section};
use cosmic::widget::{self, button, column, container, icon, nav_bar, row, text};
use zbus::blocking::Connection;

use crate::{
    fs_name, human, parse_size, plan_create, plan_delete, plan_eject, plan_format, plan_format_volume, plan_grow,
    plan_new_table, plan_toggle, udisks, Drive, Fs, Gap, Slot, Step, Volume, APP_ID, IMAGE_EXTENSIONS, MBR_MAX,
};

/// What is selected in the layout bar.
#[derive(Debug, Clone, PartialEq)]
pub enum Sel {
    Part(String),
    Free(Gap),
}

#[derive(Debug, Clone)]
pub enum Message {
    Refresh,
    Drives(Result<Vec<Drive>, String>),
    Select(Sel),
    AskFormatDrive,
    /// Write image…: use the image already chosen, or ask for one.
    AskWrite,
    ImageChosen(String, Option<PathBuf>),
    AskNewTable,
    AskCreate(Gap),
    AskFormatVolume(String),
    AskDelete(String),
    AskGrow(String),
    Toggle(String),
    Eject,
    DialogFs(usize),
    DialogName(String),
    DialogSize(String),
    DialogGpt(usize),
    Confirm,
    ClearImage,
    CloseDialog,
    Cancel,
    Progress(f32),
    /// A job finished: Ok(what to tell the user) or Err(why it failed).
    Done(Result<String, String>),
}

#[derive(Clone, Debug)]
enum Dialog {
    FormatDrive { drive: Drive, fs: usize, name: String },
    Write { drive: Drive, image: PathBuf },
    NewTable { drive: Drive, gpt: bool },
    /// `fs` indexes [`Fs::ALL`]; one past the end is "leave unformatted".
    Create { drive: Drive, gap: Gap, size: String, fs: usize, name: String },
    FormatVolume { drive: Drive, volume: Volume, fs: usize, name: String },
    Delete { drive: Drive, volume: Volume },
    Grow { drive: Drive, volume: Volume },
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
    nav: nav_bar::Model,
    /// (object, name) of each drive in the nav bar, to rebuild it only on change.
    nav_key: Vec<(String, String)>,
    selected: Option<Sel>,
    busy: Option<Busy>,
    dialog: Option<Dialog>,
    /// From the command line (Open with…), until it has been written.
    image: Option<PathBuf>,
    error: Option<String>,
    note: Option<String>,
    cancel: Arc<AtomicBool>,
    drive_fs: Vec<String>,
    all_fs: Vec<String>,
    create_fs: Vec<String>,
    tables: Vec<String>,
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

/// Names the Cosmic icon theme has (it has no plain `drive-harddisk`).
fn drive_icon(d: &Drive) -> &'static str {
    match d.bus.as_str() {
        "sdio" | "mmc" => "media-flash",
        "usb" => "drive-harddisk-usb",
        _ if d.removable => "drive-removable-media",
        _ => "drive-harddisk-system",
    }
}

/// A partition's create/format choice: `Some(fs)`, or `None` for unformatted.
fn create_choice(i: usize) -> Option<Fs> {
    Fs::ALL.get(i).copied()
}

impl App {
    fn drive(&self, object: &str) -> Option<Drive> {
        self.drives.iter().find(|d| d.object == object).cloned()
    }

    /// The drive chosen in the nav bar.
    fn current(&self) -> Option<&Drive> {
        let object = self.nav.active_data::<String>()?;
        self.drives.iter().find(|d| &d.object == object)
    }

    fn refresh(&self) -> Task<cosmic::Action<Message>> {
        let Some(conn) = self.conn.clone() else { return Task::none() };
        background(move |send| send(Message::Drives(udisks::drives(&conn))))
    }

    /// Rebuild the nav bar when drives come or go, keeping the one chosen.
    fn sync_nav(&mut self) {
        let key: Vec<(String, String)> =
            self.drives.iter().map(|d| (d.object.clone(), format!("{} ({})", d.name(), human(d.size)))).collect();
        if key == self.nav_key {
            return;
        }
        let was = self.nav.active_data::<String>().cloned();
        self.nav.clear();
        for d in &self.drives {
            let label = d.name();
            let id = self.nav.insert().text(label).icon(icon::from_name(drive_icon(d)).icon()).data(d.object.clone()).id();
            if was.as_deref() == Some(d.object.as_str()) {
                self.nav.activate(id);
            }
        }
        if self.nav.active_data::<String>().is_none() {
            self.nav.activate_position(0);
            self.selected = None;
        }
        self.nav_key = key;
    }

    /// Drop a selection that no longer exists (a deleted partition, used free space).
    fn check_selection(&mut self) {
        let keep = match (&self.selected, self.current()) {
            (Some(Sel::Part(o)), Some(d)) => d.volume(o).is_some(),
            (Some(Sel::Free(g)), Some(d)) => d.free().contains(g),
            _ => false,
        };
        if !keep {
            self.selected = None;
        }
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

    fn run(&mut self, drive: &Drive, plan: Result<Vec<Step>, String>, what: String, done: String) -> Task<cosmic::Action<Message>> {
        let steps = match plan {
            Ok(s) => s,
            Err(e) => {
                self.error = Some(e);
                self.dialog = None;
                return Task::none();
            }
        };
        let Some(conn) = self.start(drive, what, false) else { return Task::none() };
        background(move |send| send(Message::Done(udisks::run_all(&conn, &steps).map(|()| done))))
    }

    /// Act on the open dialog, planned against the drive as it is now (the dialog
    /// holds the one it was opened on, which a refresh may have changed).
    fn confirm(&mut self) -> Task<cosmic::Action<Message>> {
        let Some(dialog) = self.dialog.clone() else { return Task::none() };
        let fresh = |d: &Drive| self.drive(&d.object).ok_or_else(|| format!("{} is gone.", d.name()));
        match dialog {
            Dialog::FormatDrive { drive, fs, name } => {
                let fs = Fs::DRIVE[fs];
                let plan = fresh(&drive).and_then(|d| plan_format(&d, fs, &name));
                self.run(
                    &drive,
                    plan,
                    format!("Formatting {} as {}…", drive.name(), fs.title()),
                    format!("{} is formatted as {}.", drive.name(), fs.title()),
                )
            }
            Dialog::NewTable { drive, gpt } => {
                let table = if gpt { "gpt" } else { "dos" };
                let plan = fresh(&drive).and_then(|d| plan_new_table(&d, table));
                self.selected = None;
                self.run(
                    &drive,
                    plan,
                    format!("Erasing {}…", drive.name()),
                    format!("{} is empty, with a new {} partition table.", drive.name(), if gpt { "GPT" } else { "MBR" }),
                )
            }
            Dialog::Create { drive, gap, size, fs, name } => {
                let choice = create_choice(fs);
                let plan = fresh(&drive).and_then(|d| {
                    let size = if size.trim().is_empty() { None } else { Some(parse_size(&size)?) };
                    plan_create(&d, gap, size, choice, &name)
                });
                self.selected = None;
                let what = choice.map_or("unformatted".to_string(), |f| f.title().to_string());
                self.run(&drive, plan, format!("Creating a partition on {}…", drive.name()), format!("Created a new {what} partition."))
            }
            Dialog::FormatVolume { drive, volume, fs, name } => {
                let fs = Fs::ALL[fs];
                let plan = fresh(&drive).and_then(|d| {
                    let v = d.volume(&volume.object).cloned().ok_or("That partition is gone.")?;
                    plan_format_volume(&d, &v, fs, &name)
                });
                self.run(
                    &drive,
                    plan,
                    format!("Formatting {} as {}…", volume.device, fs.title()),
                    format!("{} is formatted as {}.", volume.device, fs.title()),
                )
            }
            Dialog::Delete { drive, volume } => {
                let plan = fresh(&drive).and_then(|d| {
                    let v = d.volume(&volume.object).cloned().ok_or("That partition is gone.")?;
                    plan_delete(&d, &v)
                });
                self.selected = None;
                self.run(&drive, plan, format!("Deleting {}…", volume.device), format!("Deleted {}.", volume.device))
            }
            Dialog::Grow { drive, volume } => {
                let plan = fresh(&drive).and_then(|d| {
                    let v = d.volume(&volume.object).cloned().ok_or("That partition is gone.")?;
                    plan_grow(&d, &v)
                });
                self.run(&drive, plan, format!("Growing {}…", volume.device), format!("{} now fills the free space after it.", volume.device))
            }
            Dialog::Write { drive, image } => self.write(drive, image),
        }
    }

    fn write(&mut self, drive: Drive, image: PathBuf) -> Task<cosmic::Action<Message>> {
        let Some(drive) = self.drive(&drive.object) else { return Task::none() };
        let Some(conn) = self.start(&drive, format!("Writing {} to {}…", file_name(&image), drive.name()), true) else {
            return Task::none();
        };
        let cancel = self.cancel.clone();
        self.image = None;
        self.selected = None;
        let done = format!("{} is written to {}.", file_name(&image), drive.name());
        background(move |send| {
            let r = udisks::write_image(&conn, &drive, &image, &cancel, |done, total| {
                if total > 0 {
                    send(Message::Progress(done as f32 / total as f32));
                }
            });
            send(Message::Done(r.map(|()| done)));
        })
    }

    fn open(&mut self, d: Dialog) {
        self.error = None;
        self.note = None;
        self.dialog = Some(d);
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
        Some(&self.nav)
    }

    /// Esc cancels an open dialog (they are modal: nothing else can be clicked).
    fn on_escape(&mut self) -> Task<cosmic::Action<Message>> {
        self.dialog = None;
        Task::none()
    }

    fn on_nav_select(&mut self, id: nav_bar::Id) -> Task<cosmic::Action<Message>> {
        self.nav.activate(id);
        self.selected = None;
        Task::none()
    }

    fn init(core: cosmic::Core, image: Option<PathBuf>) -> (Self, Task<cosmic::Action<Message>>) {
        let (conn, error) = match udisks::connect() {
            Ok(c) => (Some(c), None),
            Err(e) => (None, Some(e)),
        };
        let titles = |fs: &[Fs]| fs.iter().map(|f| f.title().to_string()).collect::<Vec<_>>();
        let mut create_fs = titles(&Fs::ALL);
        create_fs.push("Leave unformatted".into());
        let mut app = App {
            core,
            conn,
            drives: Vec::new(),
            loaded: false,
            nav: nav_bar::Model::default(),
            nav_key: Vec::new(),
            selected: None,
            busy: None,
            dialog: None,
            image,
            error,
            note: None,
            cancel: Arc::new(AtomicBool::new(false)),
            drive_fs: titles(&Fs::DRIVE),
            all_fs: titles(&Fs::ALL),
            create_fs,
            tables: vec!["GPT (recommended)".into(), "MBR (for old computers)".into()],
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
                self.sync_nav();
                self.check_selection();
            }
            Message::Drives(Err(e)) => self.error = Some(e),
            Message::Select(s) => self.selected = Some(s),
            Message::AskFormatDrive => {
                if let Some(drive) = self.current().cloned() {
                    self.open(Dialog::FormatDrive { drive, fs: 0, name: String::new() });
                }
            }
            Message::AskNewTable => {
                if let Some(drive) = self.current().cloned() {
                    let gpt = drive.size > MBR_MAX || drive.table != "dos";
                    self.open(Dialog::NewTable { drive, gpt });
                }
            }
            Message::AskCreate(gap) => {
                if let Some(drive) = self.current().cloned() {
                    self.open(Dialog::Create { drive, gap, size: String::new(), fs: 0, name: String::new() });
                }
            }
            Message::AskFormatVolume(obj) => {
                if let Some(drive) = self.current().cloned() {
                    if let Some(volume) = drive.volume(&obj).cloned() {
                        self.open(Dialog::FormatVolume { drive, volume, fs: 0, name: String::new() });
                    }
                }
            }
            Message::AskDelete(obj) => {
                if let Some(drive) = self.current().cloned() {
                    if let Some(volume) = drive.volume(&obj).cloned() {
                        self.open(Dialog::Delete { drive, volume });
                    }
                }
            }
            Message::AskGrow(obj) => {
                if let Some(drive) = self.current().cloned() {
                    if let Some(volume) = drive.volume(&obj).cloned() {
                        match plan_grow(&drive, &volume) {
                            Ok(_) => self.open(Dialog::Grow { drive, volume }),
                            Err(e) => self.error = Some(e),
                        }
                    }
                }
            }
            Message::Toggle(obj) => {
                let Some(drive) = self.current().cloned() else { return Task::none() };
                let Some(v) = drive.volume(&obj).cloned() else { return Task::none() };
                let (what, done) = match (&*v.fs_type, v.swap_active, v.mount_points.is_empty()) {
                    ("swap", true, _) => ("Turning swap off", "Swap is off"),
                    ("swap", false, _) => ("Turning swap on", "Swap is on"),
                    (_, _, true) => ("Mounting", "Mounted"),
                    _ => ("Unmounting", "Unmounted"),
                };
                let plan = plan_toggle(&drive, &v).map(|s| vec![s]);
                return self.run(&drive, plan, format!("{what} {}…", v.device), format!("{done}: {}.", v.device));
            }
            Message::AskWrite => {
                let Some(drive) = self.current().cloned() else { return Task::none() };
                self.error = None;
                self.note = None;
                let obj = drive.object.clone();
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
                        Ok(_) => self.open(Dialog::Write { drive, image }),
                        Err(e) => self.error = Some(e),
                    }
                }
            }
            Message::ImageChosen(_, None) => {}
            Message::Eject => {
                if let Some(drive) = self.current().cloned() {
                    let plan = Ok(plan_eject(&drive));
                    return self.run(&drive, plan, format!("Ejecting {}…", drive.name()), format!("{} can be unplugged.", drive.name()));
                }
            }
            Message::DialogFs(i) => match &mut self.dialog {
                Some(Dialog::FormatDrive { fs, .. }) => *fs = i.min(Fs::DRIVE.len() - 1),
                Some(Dialog::FormatVolume { fs, .. }) => *fs = i.min(Fs::ALL.len() - 1),
                Some(Dialog::Create { fs, .. }) => *fs = i.min(Fs::ALL.len()),
                _ => {}
            },
            Message::DialogName(n) => match &mut self.dialog {
                Some(Dialog::FormatDrive { name, .. } | Dialog::FormatVolume { name, .. } | Dialog::Create { name, .. }) => {
                    *name = n
                }
                _ => {}
            },
            Message::DialogSize(s) => {
                if let Some(Dialog::Create { size, .. }) = &mut self.dialog {
                    *size = s;
                }
            }
            Message::DialogGpt(i) => {
                if let Some(Dialog::NewTable { gpt, .. }) = &mut self.dialog {
                    *gpt = i == 0;
                }
            }
            Message::Confirm => return self.confirm(),
            Message::ClearImage => self.image = None,
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
                    text::body(format!("Ready to write {}. Choose a drive, then Write image….", file_name(image)))
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
            let warn = text::caption("Do not unplug the drive or turn the computer off until this is finished.");
            if b.cancellable {
                let cancelling = self.cancel.load(Ordering::Relaxed);
                c.push(
                    row::with_children([
                        warn.width(Length::Fill).into(),
                        button::standard(if cancelling { "Stopping…" } else { "Stop" })
                            .on_press_maybe((!cancelling).then_some(Message::Cancel))
                            .into(),
                    ])
                    .spacing(spacing.space_s)
                    .align_y(Alignment::Center)
                    .into(),
                );
            } else {
                c.push(warn.into());
            }
            body.push(container(column::with_children(c).spacing(spacing.space_s)).padding(spacing.space_s).into());
        }
        body.push(self.view_drive());
        let content = column::with_children(body).spacing(spacing.space_m).max_width(760);
        let centered = container(content).width(Length::Fill).align_x(cosmic::iced::alignment::Horizontal::Center);
        container(widget::scrollable(centered)).width(Length::Fill).height(Length::Fill).padding(spacing.space_m).into()
    }

    fn dialog(&self) -> Option<Element<'_, Message>> {
        let spacing = cosmic::theme::spacing();
        let cancel = button::standard("Cancel").on_press(Message::CloseDialog);
        let accent = |s: String| -> Element<'_, Message> { text::caption(s).class(cosmic::theme::Text::Accent).into() };
        let d = match self.dialog.as_ref()? {
            Dialog::FormatDrive { drive, fs, name } => {
                let f = Fs::DRIVE[*fs];
                let check = f.check_name(name);
                let mut c = fs_picker(&self.drive_fs, *fs, f.about(), name);
                if let Some(e) = name_note(f, name, &check) {
                    c.push(if check.is_err() { accent(e) } else { text::caption(e).into() });
                }
                widget::dialog()
                    .title(format!("Format {}?", drive.name()))
                    .body(drive.erase_warning())
                    .control(column::with_children(c).spacing(spacing.space_xxs))
                    .primary_action(button::destructive("Format").on_press_maybe(check.is_ok().then_some(Message::Confirm)))
            }
            Dialog::NewTable { drive, gpt } => {
                let mut c: Vec<Element<'_, Message>> = vec![
                    text::body("Partition table").into(),
                    widget::dropdown(&self.tables, Some(if *gpt { 0 } else { 1 }), Message::DialogGpt).into(),
                ];
                let ok = plan_new_table(drive, if *gpt { "gpt" } else { "dos" });
                if let Err(e) = &ok {
                    c.push(accent(e.clone()));
                }
                widget::dialog()
                    .title(format!("Erase {}?", drive.name()))
                    .body(format!("{}\n\nThe drive will be left empty, ready for new partitions.", drive.erase_warning()))
                    .control(column::with_children(c).spacing(spacing.space_xxs))
                    .primary_action(button::destructive("Erase").on_press_maybe(ok.is_ok().then_some(Message::Confirm)))
            }
            Dialog::Create { drive, gap, size, fs, name } => {
                let choice = create_choice(*fs);
                let mut c: Vec<Element<'_, Message>> = vec![
                    text::body("Size").into(),
                    widget::text_input(format!("All of it ({})", human(gap.size)), size.as_str())
                        .on_input(Message::DialogSize)
                        .into(),
                    text::body("Type").into(),
                    widget::dropdown(&self.create_fs, Some(*fs), Message::DialogFs).into(),
                    text::caption(choice.map_or("An empty partition, to format later.", |f| f.about())).into(),
                ];
                if choice.is_some_and(|f| f != Fs::Swap) {
                    c.push(text::body("Name").into());
                    c.push(widget::text_input("Optional", name.as_str()).on_input(Message::DialogName).into());
                }
                let plan = (if size.trim().is_empty() { Ok(None) } else { parse_size(size).map(Some) })
                    .and_then(|n| plan_create(drive, *gap, n, choice, name));
                match (&plan, choice) {
                    (Err(e), _) => c.push(accent(e.clone())),
                    (Ok(_), Some(f)) => {
                        if let Some(n) = name_note(f, name, &f.check_name(name)) {
                            c.push(text::caption(n).into());
                        }
                    }
                    _ => {}
                }
                widget::dialog()
                    .title("Create a partition")
                    .body(format!("In {} of free space on {}.", human(gap.size), drive.name()))
                    .control(column::with_children(c).spacing(spacing.space_xxs))
                    .primary_action(button::suggested("Create").on_press_maybe(plan.is_ok().then_some(Message::Confirm)))
            }
            Dialog::FormatVolume { drive, volume, fs, name } => {
                let f = Fs::ALL[*fs];
                let ok = plan_format_volume(drive, volume, f, name);
                let mut c = fs_picker(&self.all_fs, *fs, f.about(), name);
                match &ok {
                    Err(e) => c.push(accent(e.clone())),
                    Ok(_) => {
                        if let Some(n) = name_note(f, name, &f.check_name(name)) {
                            c.push(text::caption(n).into());
                        }
                    }
                }
                widget::dialog()
                    .title(format!("Format {}?", volume.title()))
                    .body(format!("Everything on {} will be erased. This cannot be undone.", what(volume)))
                    .control(column::with_children(c).spacing(spacing.space_xxs))
                    .primary_action(button::destructive("Format").on_press_maybe(ok.is_ok().then_some(Message::Confirm)))
            }
            Dialog::Delete { drive, volume } => {
                let inside = if volume.container { " and every partition inside it" } else { "" };
                widget::dialog()
                    .title(format!("Delete {}?", volume.title()))
                    .body(format!(
                        "{} on {}{inside} will be deleted, with everything on it. This cannot be undone.",
                        what(volume),
                        drive.name()
                    ))
                    .primary_action(button::destructive("Delete").on_press(Message::Confirm))
            }
            Dialog::Grow { drive, volume } => {
                let to = drive.room_after(volume).map(|g| g.offset + g.size - volume.offset).unwrap_or(volume.size);
                widget::dialog()
                    .title(format!("Grow {}?", volume.title()))
                    .body(format!(
                        "{} grows from {} to {}, into the free space after it. Its files stay where they are.\n\n\
                         Keep the computer on until it is done: a resize cut short by a power loss can damage the filesystem.",
                        volume.device,
                        human(volume.size),
                        human(to)
                    ))
                    .primary_action(button::suggested("Grow").on_press(Message::Confirm))
            }
            Dialog::Write { drive, image } => {
                let stored = std::fs::metadata(image).map(|m| m.len()).unwrap_or(0);
                let size = match crate::image_size(image) {
                    Some(n) if n != stored => format!("{}, {} unpacked", human(stored), human(n)),
                    _ => human(stored),
                };
                widget::dialog()
                    .title(format!("Write the image to {}?", drive.name()))
                    .body(format!("{}\n\nImage: {} ({size})", drive.erase_warning(), file_name(image)))
                    .primary_action(button::destructive("Write").on_press(Message::Confirm))
            }
        };
        Some(d.secondary_action(cancel).into())
    }

    fn subscription(&self) -> Subscription<Message> {
        // Notice drives being plugged in and out, and changes made elsewhere.
        cosmic::iced::time::every(Duration::from_secs(2)).map(|_| Message::Refresh)
    }
}

/// The "Type" dropdown, its explanation and the "Name" field.
fn fs_picker<'a>(titles: &'a [String], fs: usize, about: &'static str, name: &'a str) -> Vec<Element<'a, Message>> {
    vec![
        text::body("Type").into(),
        widget::dropdown(titles, Some(fs), Message::DialogFs).into(),
        text::caption(about).into(),
        text::body("Name").into(),
        widget::text_input("Optional", name).on_input(Message::DialogName).into(),
    ]
}

/// "media (/dev/vda1: ext4, 21.5 GB)"
fn what(v: &Volume) -> String {
    let kind = if v.container { "extended partition".to_string() } else { fs_name(&v.fs_type).to_string() };
    format!("{} ({}: {kind}, {})", v.title(), v.device, human(v.size))
}

/// The name's error, or FAT32's "stored in capitals" note.
fn name_note(f: Fs, name: &str, check: &Result<String, String>) -> Option<String> {
    match check {
        Err(e) => Some(e.clone()),
        Ok(n) if f == Fs::Fat32 && !n.is_empty() && n != name.trim() => Some(format!("FAT32 names are stored in capitals: {n}")),
        Ok(_) => None,
    }
}

impl App {
    fn view_drive(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let Some(d) = self.current() else {
            let msg = if self.loaded || self.conn.is_none() { "No drives found." } else { "Looking for drives…" };
            return container(text::body(msg)).padding(spacing.space_m).into();
        };
        let idle = self.busy.is_none();
        let working = self.busy.as_ref().is_some_and(|b| b.drive == d.object);
        let lock = d.lock();
        let free_ok = idle && lock.is_none();

        let mut head = vec![
            text::title3(d.name()).into(),
            text::caption(format!("{} · {} · {} · {}", d.kind(), human(d.size), d.table_name(), d.device)).into(),
        ];
        if let Some(l) = &lock {
            head.push(text::caption(l.text()).into());
        }
        let mut actions: Vec<Element<'_, Message>> = Vec::new();
        if d.removable {
            actions.push(button::standard("Format…").on_press_maybe(free_ok.then_some(Message::AskFormatDrive)).into());
        }
        actions.push(button::standard("New partition table…").on_press_maybe(free_ok.then_some(Message::AskNewTable)).into());
        actions.push(button::standard("Write image…").on_press_maybe(free_ok.then_some(Message::AskWrite)).into());
        if d.removable && (d.can_power_off || d.ejectable) {
            actions.push(button::standard("Eject").on_press_maybe(free_ok.then_some(Message::Eject)).into());
        }
        if !working {
            head.push(row::with_children(actions).spacing(spacing.space_xs).into());
        }
        let mut out: Vec<Element<'_, Message>> = vec![
            row::with_children([
                icon::from_name(drive_icon(d)).size(48).into(),
                column::with_children(head).spacing(spacing.space_xxs).width(Length::Fill).into(),
            ])
            .spacing(spacing.space_s)
            .into(),
        ];

        out.push(self.view_bar(d, d.layout()));
        if let Some(ext) = d.partitions().find(|v| v.container) {
            let inner: Vec<Slot> = d
                .volumes
                .iter()
                .enumerate()
                .filter(|(_, v)| v.contained && v.number > 0)
                .map(|(i, _)| Slot::Part(i))
                .collect();
            if !inner.is_empty() {
                out.push(text::caption(format!("Inside the extended partition {}:", ext.device)).into());
                out.push(self.view_bar(d, inner));
            }
        }
        if d.table.is_empty() {
            let whole = d.volumes.iter().find(|v| v.number == 0 && v.backing.is_empty());
            out.push(
                text::body(match whole {
                    Some(v) => format!("No partitions: the whole drive is {}.", v.describe()),
                    None => "No partition table. Use New partition table… to start partitioning it.".to_string(),
                })
                .into(),
            );
        }
        if let Some(details) = self.view_selected(d, idle && !working) {
            out.push(details);
        }
        column::with_children(out).spacing(spacing.space_m).into()
    }

    /// Partitions and free space, side by side in proportion (with a floor, so a
    /// 1 MB partition can still be clicked).
    fn view_bar(&self, d: &Drive, slots: Vec<Slot>) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let size_of = |s: &Slot| match s {
            Slot::Part(i) => d.volumes[*i].size,
            Slot::Free(g) => g.size,
        };
        let total: u64 = slots.iter().map(size_of).sum::<u64>().max(1);
        let mut cells: Vec<Element<'_, Message>> = Vec::new();
        for s in &slots {
            let share = (size_of(s) as u128 * 1000 / total as u128) as u16;
            let (title, sub, sel) = match s {
                Slot::Part(i) => {
                    let v = &d.volumes[*i];
                    let fs = if v.container { "extended".to_string() } else { fs_name(&v.fs_type).to_string() };
                    (v.title(), format!("{} · {fs}", human(v.size)), Sel::Part(v.object.clone()))
                }
                Slot::Free(g) => ("Free space".to_string(), human(g.size), Sel::Free(*g)),
            };
            let selected = self.selected.as_ref() == Some(&sel);
            // Square-ish cells read as one bar; free space is drawn hollow.
            let class = match (s, selected) {
                (Slot::Free(_), false) => cosmic::theme::Button::Text,
                _ => cosmic::theme::Button::ListItem([6.0; 4]),
            };
            let label = column::with_children([text::body(title).into(), text::caption(sub).into()]).spacing(2);
            let cell = button::custom(container(label).padding(spacing.space_xxs).clip(true))
                .class(class)
                .selected(selected)
                .width(Length::Fill)
                .height(Length::Fixed(64.0))
                .on_press(Message::Select(sel));
            // Partitions sit on a card so the bar reads as one drive; free space
            // stays hollow.
            let cell: Element<'_, Message> = match s {
                Slot::Part(_) => container(cell).class(cosmic::theme::Container::Card).into(),
                Slot::Free(_) => container(cell)
                    .class(cosmic::theme::Container::custom(|t| {
                        let c = t.cosmic();
                        cosmic::iced::widget::container::Style {
                            border: cosmic::iced::Border {
                                color: c.bg_divider().into(),
                                width: 1.0,
                                radius: c.corner_radii.radius_s.into(),
                            },
                            ..Default::default()
                        }
                    }))
                    .into(),
            };
            cells.push(container(cell).width(Length::FillPortion(share.max(80))).into());
        }
        row::with_children(cells).spacing(2).into()
    }

    fn view_selected(&self, d: &Drive, can: bool) -> Option<Element<'_, Message>> {
        let spacing = cosmic::theme::spacing();
        match self.selected.as_ref()? {
            Sel::Free(g) => {
                let ok = can && !d.read_only;
                let s = section()
                    .title("Free space")
                    .add(item("Size", text::body(human(g.size))))
                    .add(item("Starts at", text::body(human(g.offset))))
                    .add(
                        row::with_children([
                            button::suggested("Create partition…").on_press_maybe(ok.then_some(Message::AskCreate(*g))).into(),
                        ]),
                    );
                Some(s.into())
            }
            Sel::Part(o) => {
                let v = d.volume(o)?;
                let lock = d.volume_lock(v);
                let mut s = section()
                    .title(format!("{} · {}", v.title(), v.device))
                    .add(item("Size", text::body(human(v.size))))
                    .add(item("Contents", text::body(if v.container {
                        "Extended partition (holds other partitions)".to_string()
                    } else {
                        fs_name(&v.fs_type).to_string()
                    })));
                if !v.label.is_empty() {
                    s = s.add(item("Name", text::body(v.label.clone())));
                }
                if !v.part_name.is_empty() && v.part_name != v.label {
                    s = s.add(item("Partition name", text::body(v.part_name.clone())));
                }
                let inside = d.inside(v);
                let mounts: Vec<String> =
                    v.mount_points.iter().chain(inside.iter().flat_map(|c| c.mount_points.iter())).cloned().collect();
                if !mounts.is_empty() {
                    s = s.add(item("Mounted at", text::body(mounts.join(", "))));
                }
                if v.fs_type == "swap" {
                    s = s.add(item("Swap", text::body(if v.swap_active { "On" } else { "Off" })));
                }
                if let Some(l) = &lock {
                    s = s.add(item("Locked", text::body(l.text())));
                }
                let mut buttons: Vec<Element<'_, Message>> = Vec::new();
                if let Ok(step) = plan_toggle(d, v) {
                    let label = match step {
                        Step::Mount { .. } => "Mount",
                        Step::Unmount { .. } => "Unmount",
                        Step::SwapOn { .. } => "Turn swap on",
                        _ => "Turn swap off",
                    };
                    buttons.push(button::standard(label).on_press_maybe(can.then(|| Message::Toggle(v.object.clone()))).into());
                }
                let free = can && lock.is_none();
                if v.number > 0 {
                    if !v.container {
                        buttons.push(
                            button::standard("Format…")
                                .on_press_maybe(free.then(|| Message::AskFormatVolume(v.object.clone())))
                                .into(),
                        );
                    }
                    if d.room_after(v).is_some() && crate::grow_rule(&v.fs_type) != crate::Grow::No {
                        buttons.push(
                            button::standard("Grow…").on_press_maybe(free.then(|| Message::AskGrow(v.object.clone()))).into(),
                        );
                    }
                    buttons.push(
                        button::destructive("Delete…").on_press_maybe(free.then(|| Message::AskDelete(v.object.clone()))).into(),
                    );
                }
                if !buttons.is_empty() {
                    s = s.add(row::with_children(buttons).spacing(spacing.space_xs));
                }
                Some(s.into())
            }
        }
    }
}
