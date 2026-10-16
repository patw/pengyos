//! The "Disk Usage" window (libcosmic).
//!
//! A start page of places (your home folder and each filesystem, with how full it
//! is); a scan with live counts and Stop; then the answer: a ring chart of the
//! folder in the middle (its contents in the first ring, theirs in the next, …)
//! beside the same contents as a ranked list. Click a folder, in the chart or the
//! list, to go into it; click the centre, or a crumb, to go back up. Read-only:
//! the window can open a folder in Files and nothing else.

use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use cosmic::dialog::file_chooser;
use cosmic::iced::mouse;
use cosmic::iced::widget::canvas::{self, Frame, Geometry, Path as CanvasPath, Stroke, Text};
use cosmic::iced::{alignment, Alignment, Color, Length, Point, Radians, Rectangle, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::section;
use cosmic::widget::{self, button, column, container, icon, nav_bar, row, text};

use crate::{
    colour, count, filesystems, human, mounts, mounts_inside, percent, place_name, real_mount_points, ring_radii, scan,
    segments, space, Kind, Node, Progress, Scan, Segment, Space, APP_ID, CENTRE,
};

#[derive(Clone, Debug)]
pub struct Place {
    pub title: String,
    pub path: PathBuf,
    pub space: Option<Space>,
}

#[derive(Clone, Debug)]
pub enum Message {
    Places(Vec<Place>),
    Scan(PathBuf),
    PickFolder,
    Tick,
    Stop,
    Scanned(Result<Arc<Scan>, String>),
    /// Make the node at this path (from the scan's root) the centre.
    Open(Vec<usize>),
    Hover(Option<Vec<usize>>),
    Home,
    Rescan,
    OpenInFiles,
}

enum Page {
    Start,
    Scanning(PathBuf),
    Done(Arc<Scan>),
}

pub struct App {
    core: cosmic::Core,
    home: PathBuf,
    places: Vec<Place>,
    page: Page,
    progress: Arc<Progress>,
    /// The centre node, as child indices from the scan's root.
    centre: Vec<usize>,
    /// The slice under the pointer, as child indices from the scan's root.
    hover: Option<Vec<usize>>,
    error: Option<String>,
}

fn places(home: &Path) -> Vec<Place> {
    let mut out = vec![Place { title: "Home folder".into(), path: home.to_path_buf(), space: space(home) }];
    for m in filesystems(&mounts()) {
        out.push(Place { title: place_name(&m, home), space: space(&m.point), path: m.point });
    }
    out
}

impl App {
    fn scan(&self) -> Option<&Arc<Scan>> {
        match &self.page {
            Page::Done(s) => Some(s),
            _ => None,
        }
    }

    fn start(&mut self, path: PathBuf) -> Task<cosmic::Action<Message>> {
        self.error = None;
        self.centre.clear();
        self.hover = None;
        self.progress = Arc::new(Progress::default());
        self.page = Page::Scanning(path.clone());
        let progress = self.progress.clone();
        cosmic::task::future(async move {
            let r = std::thread::spawn(move || {
                let skip = mounts_inside(&mounts(), &path);
                scan(&path, &skip, &progress).map(Arc::new)
            })
            .join()
            .unwrap_or_else(|_| Err("The scan crashed.".into()));
            Message::Scanned(r)
        })
    }

    /// The full path on disk of the node at `path` in the scan.
    fn disk_path(&self, s: &Scan, path: &[usize]) -> PathBuf {
        let mut p = s.path.clone();
        let mut n = &s.root;
        for &i in path {
            n = &n.children[i];
            p.push(&n.name);
        }
        p
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

    fn init(core: cosmic::Core, folder: Option<PathBuf>) -> (Self, Task<cosmic::Action<Message>>) {
        let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"));
        let mut app = App {
            core,
            places: Vec::new(),
            home: home.clone(),
            page: Page::Start,
            progress: Arc::new(Progress::default()),
            centre: Vec::new(),
            hover: None,
            error: None,
        };
        app.set_header_title("Disk Usage".into());
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Disk Usage".into(), id),
            None => Task::none(),
        };
        let first = match folder {
            Some(f) => app.start(f.canonicalize().unwrap_or(f)),
            None => Task::none(),
        };
        let load = cosmic::task::future(async move { Message::Places(places(&home)) });
        (app, Task::batch([title, load, first]))
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::Places(p) => self.places = p,
            Message::Scan(p) => return self.start(p),
            Message::PickFolder => {
                let dialog = file_chooser::open::Dialog::new().title("Choose a folder to scan");
                return cosmic::task::future(async move {
                    match dialog.open_folder().await.ok().and_then(|r| r.url().to_file_path().ok()) {
                        Some(p) => Message::Scan(p),
                        None => Message::Tick,
                    }
                });
            }
            Message::Tick => {}
            Message::Stop => self.progress.cancel.store(true, Ordering::Relaxed),
            Message::Scanned(Ok(s)) => self.page = Page::Done(s),
            Message::Scanned(Err(e)) => {
                if e != "Stopped." {
                    self.error = Some(e);
                }
                self.page = Page::Start;
            }
            Message::Open(p) => {
                if self.scan().and_then(|s| s.root.at(&p)).is_some_and(|n| n.kind == Kind::Dir) {
                    self.centre = p;
                    self.hover = None;
                }
            }
            Message::Hover(h) => self.hover = h,
            Message::Home => {
                self.page = Page::Start;
                let home = self.home.clone();
                return cosmic::task::future(async move { Message::Places(places(&home)) });
            }
            Message::Rescan => {
                if let Some(s) = self.scan() {
                    let p = s.path.clone();
                    return self.start(p);
                }
            }
            Message::OpenInFiles => {
                if let Some(s) = self.scan() {
                    let p = self.disk_path(s, &self.centre);
                    let _ = std::process::Command::new("xdg-open").arg(p).spawn();
                }
            }
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let body: Element<'_, Message> = match &self.page {
            Page::Start => self.view_start(),
            Page::Scanning(p) => {
                let items = self.progress.items.load(Ordering::Relaxed);
                let bytes = self.progress.bytes.load(Ordering::Relaxed);
                let cancelling = self.progress.cancel.load(Ordering::Relaxed);
                column::with_children([
                    text::title3(format!("Scanning {}…", p.display())).into(),
                    widget::indeterminate_linear().width(Length::Fill).into(),
                    text::body(format!("{} files and folders, {} so far", count(items), human(bytes))).into(),
                    button::standard(if cancelling { "Stopping…" } else { "Stop" })
                        .on_press_maybe((!cancelling).then_some(Message::Stop))
                        .into(),
                ])
                .spacing(spacing.space_s)
                .max_width(620)
                .into()
            }
            Page::Done(s) => self.view_result(s),
        };
        container(body)
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(spacing.space_m)
            .align_x(alignment::Horizontal::Center)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        match self.page {
            // Live counts while scanning.
            Page::Scanning(_) => cosmic::iced::time::every(Duration::from_millis(250)).map(|_| Message::Tick),
            _ => Subscription::none(),
        }
    }
}

impl App {
    fn view_start(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut out: Vec<Element<'_, Message>> = vec![
            text::title3("What is taking the space?").into(),
            text::body("Choose a place to scan. Nothing is changed or deleted.").into(),
        ];
        if let Some(e) = &self.error {
            out.push(text::body(e.clone()).class(cosmic::theme::Text::Accent).into());
        }
        let mut s = section().title("Places");
        for p in &self.places {
            let detail = match p.space {
                Some(sp) if sp.total > 0 => {
                    format!("{} used of {} · {} free · {}", human(sp.used), human(sp.total), human(sp.free), p.path.display())
                }
                _ => p.path.display().to_string(),
            };
            let mut info: Vec<Element<'_, Message>> = vec![text::heading(p.title.clone()).into(), text::caption(detail).into()];
            if let Some(sp) = p.space.filter(|sp| sp.total > 0) {
                info.push(widget::determinate_linear(sp.used as f32 / sp.total as f32).width(Length::Fill).into());
            }
            let name = if p.title == "Home folder" { "user-home" } else { "drive-harddisk-system" };
            s = s.add(
                row::with_children([
                    icon::from_name(name).size(32).into(),
                    column::with_children(info).spacing(spacing.space_xxs).width(Length::Fill).into(),
                    button::suggested("Scan").on_press(Message::Scan(p.path.clone())).into(),
                ])
                .spacing(spacing.space_s)
                .align_y(Alignment::Center),
            );
        }
        out.push(s.into());
        out.push(button::standard("Scan a folder…").on_press(Message::PickFolder).into());
        let col = column::with_children(out).spacing(spacing.space_m).max_width(720);
        widget::scrollable(container(col).width(Length::Fill).align_x(alignment::Horizontal::Center)).into()
    }

    fn view_result<'a>(&'a self, s: &'a Scan) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        let Some(centre) = s.root.at(&self.centre) else { return text::body("…").into() };

        // Crumbs: the scanned folder, then each folder down to the centre.
        let mut crumbs: Vec<Element<'_, Message>> =
            vec![button::text(s.path.display().to_string()).on_press(Message::Open(Vec::new())).into()];
        let mut n = &s.root;
        for (k, &i) in self.centre.iter().enumerate() {
            n = &n.children[i];
            crumbs.push(text::body("›").into());
            crumbs.push(button::text(n.name.clone()).on_press(Message::Open(self.centre[..=k].to_vec())).into());
        }
        let top = row::with_children([
            button::standard("Places").on_press(Message::Home).into(),
            widget::scrollable::horizontal(row::with_children(crumbs).align_y(Alignment::Center)).width(Length::Fill).into(),
            button::standard("Open in Files").on_press(Message::OpenInFiles).into(),
            button::standard("Scan again").on_press(Message::Rescan).into(),
        ])
        .spacing(spacing.space_xs)
        .align_y(Alignment::Center);

        let hovered = self
            .hover
            .as_ref()
            .and_then(|h| h.strip_prefix(self.centre.as_slice()).map(|rel| rel.to_vec()))
            .filter(|rel| !rel.is_empty());
        let chart = canvas::Canvas::new(Chart {
            centre,
            centre_path: self.centre.clone(),
            segments: segments(centre),
            hover: hovered.clone(),
        })
        .width(Length::FillPortion(3))
        .height(Length::Fill);

        // The same contents, ranked.
        let mut list: Vec<Element<'_, Message>> = Vec::new();
        for (i, c) in centre.children.iter().enumerate() {
            let mut path = self.centre.clone();
            path.push(i);
            let is_hover = hovered.as_ref().is_some_and(|h| h.first() == Some(&i));
            let name = match c.kind {
                Kind::Dir => format!("{}/", c.name),
                _ => c.name.clone(),
            };
            let line = column::with_children([
                row::with_children([
                    (if is_hover { text::heading(name) } else { text::body(name) }).width(Length::Fill).into(),
                    text::body(human(c.size)).into(),
                ])
                .spacing(spacing.space_xs)
                .into(),
                row::with_children([
                    widget::determinate_linear(if centre.size > 0 { c.size as f32 / centre.size as f32 } else { 0.0 })
                        .width(Length::Fill)
                        .into(),
                    text::caption(percent(c.size, centre.size)).width(Length::Fixed(56.0)).align_x(alignment::Horizontal::Right).into(),
                ])
                .spacing(spacing.space_xs)
                .align_y(Alignment::Center)
                .into(),
            ])
            .spacing(2);
            let b = button::custom(line).class(cosmic::theme::Button::ListItem([8.0; 4])).width(Length::Fill);
            list.push(if c.kind == Kind::Dir { b.on_press(Message::Open(path)).into() } else { b.into() });
        }
        if list.is_empty() {
            list.push(text::body("This folder is empty.").into());
        }

        let mut notes: Vec<String> = Vec::new();
        // Name only the left-out filesystems that hold files, not /proc and friends.
        let real = real_mount_points(&mounts());
        let m: Vec<String> = s.mounts.iter().filter(|p| real.contains(*p)).map(|p| p.display().to_string()).collect();
        if !m.is_empty() {
            notes.push(format!("Not included, they are other drives or filesystems: {}.", m.join(", ")));
        }
        if !s.snapshots.is_empty() {
            notes.push("Snapshots are not counted: they share their space with the files they copy.".into());
        }
        if s.unreadable_count > 0 {
            notes.push(format!(
                "{} folder(s) could not be read (no permission), e.g. {}.",
                s.unreadable_count,
                s.unreadable[0].display()
            ));
        }

        let side = column::with_children([
            text::title4(format!("{} · {}", centre.name, human(centre.size))).into(),
            text::caption(format!("{} files and folders", count(centre.items))).into(),
            // Right padding keeps the sizes clear of the scrollbar.
            widget::scrollable(column::with_children(list).spacing(2).padding([0, spacing.space_s, 0, 0]))
                .height(Length::Fill)
                .into(),
        ])
        .spacing(spacing.space_xs)
        .width(Length::FillPortion(2));

        let mut out: Vec<Element<'_, Message>> = vec![
            top.into(),
            row::with_children([chart.into(), side.into()]).spacing(spacing.space_m).height(Length::Fill).into(),
        ];
        out.extend(notes.into_iter().map(|n| text::caption(n).into()));
        column::with_children(out).spacing(spacing.space_s).into()
    }
}

/// The ring chart: `centre` in the middle, its contents around it.
struct Chart<'a> {
    centre: &'a Node,
    centre_path: Vec<usize>,
    segments: Vec<Segment>,
    /// The hovered slice, relative to the centre.
    hover: Option<Vec<usize>>,
}

impl Chart<'_> {
    fn geometry(bounds: Rectangle) -> (Point, f32) {
        let c = Point::new(bounds.width / 2.0, bounds.height / 2.0);
        (c, (bounds.width.min(bounds.height) / 2.0 - 8.0).max(10.0))
    }

    fn under(&self, bounds: Rectangle, cursor: mouse::Cursor) -> Option<Option<usize>> {
        let p = cursor.position_in(bounds)?;
        let (c, r) = Self::geometry(bounds);
        crate::hit(&self.segments, (p.x - c.x) as f64, (p.y - c.y) as f64, r as f64)
    }

    fn full(&self, rel: &[usize]) -> Vec<usize> {
        let mut p = self.centre_path.clone();
        p.extend_from_slice(rel);
        p
    }
}

/// Angles: our turns start at twelve o'clock; iced's radians at three.
fn angle(turn: f64) -> Radians {
    Radians((turn * std::f64::consts::TAU - std::f64::consts::FRAC_PI_2) as f32)
}

impl canvas::Program<Message, cosmic::Theme, cosmic::Renderer> for Chart<'_> {
    type State = ();

    fn update(
        &self,
        _state: &mut (),
        event: &canvas::Event,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> Option<canvas::Action<Message>> {
        let canvas::Event::Mouse(e) = event else { return None };
        match e {
            mouse::Event::CursorMoved { .. } | mouse::Event::CursorLeft => {
                let now = match self.under(bounds, cursor) {
                    Some(Some(i)) => Some(self.full(&self.segments[i].path)),
                    _ => None,
                };
                let was = self.hover.as_ref().map(|h| self.full(h));
                (now != was).then(|| canvas::Action::publish(Message::Hover(now)))
            }
            mouse::Event::ButtonPressed(mouse::Button::Left) => match self.under(bounds, cursor)? {
                // The centre: one folder up.
                None if !self.centre_path.is_empty() => {
                    let up = self.centre_path[..self.centre_path.len() - 1].to_vec();
                    Some(canvas::Action::publish(Message::Open(up)).and_capture())
                }
                Some(i) if self.segments[i].kind == Kind::Dir => {
                    Some(canvas::Action::publish(Message::Open(self.full(&self.segments[i].path))).and_capture())
                }
                _ => None,
            },
            _ => None,
        }
    }

    fn draw(
        &self,
        _state: &(),
        renderer: &cosmic::Renderer,
        theme: &cosmic::Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<Geometry<cosmic::Renderer>> {
        let mut frame = Frame::new(renderer, bounds.size());
        let (c, r) = Self::geometry(bounds);
        let t = theme.cosmic();
        let bg: Color = t.bg_color().into();
        let fg: Color = t.on_bg_color().into();

        for s in &self.segments {
            let (i, o) = ring_radii(s.depth);
            let (ri, ro) = (r * i as f32, r * o as f32);
            // The slice as a polygon: out along the outer edge, back along the inner
            // one (a point every degree or so is smooth at any size we draw).
            let steps = ((s.end - s.start) * 360.0).ceil().max(2.0) as usize;
            let at = |radius: f32, turn: f64| {
                let a = angle(turn).0;
                Point::new(c.x + radius * a.cos(), c.y + radius * a.sin())
            };
            let path = CanvasPath::new(|b| {
                b.move_to(at(ro, s.start));
                for k in 1..=steps {
                    b.line_to(at(ro, s.start + (s.end - s.start) * k as f64 / steps as f64));
                }
                for k in (0..=steps).rev() {
                    b.line_to(at(ri, s.start + (s.end - s.start) * k as f64 / steps as f64));
                }
                b.close();
            });
            let mut rgb = colour(s.path[0], s.depth, s.kind);
            let hovered = self.hover.as_ref().is_some_and(|h| h.as_slice() == s.path.as_slice());
            if hovered {
                rgb = rgb.map(|x| x * 0.8);
            }
            frame.fill(&path, Color::from_rgb(rgb[0], rgb[1], rgb[2]));
            frame.stroke(&path, Stroke::default().with_color(bg).with_width(1.5));
        }

        // The centre: the folder itself; click to go up.
        let inner = CanvasPath::circle(c, r * CENTRE as f32 - 2.0);
        frame.fill(&inner, Color { a: 0.08, ..fg });
        let (title, size) = match self.hover.as_ref().and_then(|h| self.centre.at(h)) {
            Some(n) => (n.name.clone(), format!("{} · {}", human(n.size), percent(n.size, self.centre.size))),
            None => (self.centre.name.clone(), human(self.centre.size)),
        };
        let width = 2.0 * r * CENTRE as f32 - 16.0;
        for (s, dy, px) in [(title, -10.0, 15.0), (size, 12.0, 13.0)] {
            frame.fill_text(Text {
                content: s,
                position: Point::new(c.x, c.y + dy),
                max_width: width,
                color: fg,
                size: px.into(),
                align_x: Alignment::Center.into(),
                align_y: alignment::Vertical::Center,
                ..Text::default()
            });
        }
        if self.segments.is_empty() {
            frame.fill_text(Text {
                content: "Empty".into(),
                position: Point::new(c.x, c.y + r * 0.6),
                color: fg,
                align_x: Alignment::Center.into(),
                align_y: alignment::Vertical::Center,
                ..Text::default()
            });
        }
        vec![frame.into_geometry()]
    }

    fn mouse_interaction(&self, _state: &(), bounds: Rectangle, cursor: mouse::Cursor) -> mouse::Interaction {
        match self.under(bounds, cursor) {
            Some(Some(i)) if self.segments[i].kind == Kind::Dir => mouse::Interaction::Pointer,
            Some(None) if !self.centre_path.is_empty() => mouse::Interaction::Pointer,
            _ => mouse::Interaction::default(),
        }
    }
}
