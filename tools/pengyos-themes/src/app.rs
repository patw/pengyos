//! The *Themes* window (libcosmic): Looks, Icons and Colours over the library in
//! this crate. Everything it changes goes through the same functions as the CLI
//! (so the same checks, the same COSMIC keys, the same undo), and COSMIC redraws
//! this window with the new theme like every other app.

use std::path::PathBuf;
use std::time::Duration;

use cosmic::iced::futures::channel::oneshot;
use cosmic::iced::{Alignment, Color, Length, Subscription};
use cosmic::prelude::*;
use cosmic::widget::settings::{item, section};
use cosmic::widget::{self, button, column, container, icon, nav_bar, row, text};

use crate::catalogue::{Catalogue, IconPack, Mode, COSMIC_ICONS};
use crate::colours::{self, Found, Now, Order, Query, Swatch};
use crate::fetch::Curl;
use crate::icons::{self, ThemeInfo};
use crate::looks::{self, Look};
use crate::{desktop, Paths};

pub const APP_ID: &str = "io.catbee.pengyos.Themes";

/// The icons a preview shows, in this order.
const PREVIEW: [&str; 6] =
    ["folder", "user-home", "text-x-generic", "image-x-generic", "audio-x-generic", "application-x-executable"];
const ORDERS: [&str; 3] = ["Most popular", "Newest", "By name"];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Page {
    Looks,
    Icons,
    Colours,
}

#[derive(Debug, Clone)]
pub enum Message {
    ApplyLook(String),
    UseIcons(String),
    InstallPack(String),
    AskRemovePack(String),
    RemovePack(String),
    ApplyColours(String),
    ForgetColours(String),
    ForgetLook(String),
    SearchText(String),
    SearchOrder(usize),
    Search(u32),
    SearchDone(u32, Result<(Vec<Found>, usize), String>),
    /// Background work finished: a note to show, or an error.
    Done(Result<String, String>),
    Undo,
    AskReset,
    Reset,
    AskSave,
    SaveName(String),
    Save,
    CloseDialog,
    Tick,
}

#[derive(Debug, Clone, PartialEq)]
enum Dialog {
    Reset,
    Save,
    RemovePack(String),
}

#[derive(Debug, Clone, PartialEq)]
enum PackState {
    Included,
    Installed(String),
    NotInstalled,
}

struct LookCard {
    look: Look,
    swatch: Option<Swatch>,
    icons: Vec<Option<PathBuf>>,
}

struct ColourCard {
    id: String,
    name: String,
    by: String,
    swatch: Swatch,
    /// In the user's library (so it can be forgotten); else from the catalogue.
    mine: bool,
}

pub struct App {
    core: cosmic::Core,
    nav: nav_bar::Model,
    paths: Paths,
    cat: Option<Catalogue>,
    page: Page,
    // What the desktop shows now.
    icons_now: String,
    mode: Mode,
    colours_now: Now,
    undo: usize,
    // Page contents.
    looks: Vec<LookCard>,
    themes: Vec<(ThemeInfo, Vec<Option<PathBuf>>)>,
    packs: Vec<(IconPack, PackState)>,
    library: Vec<ColourCard>,
    search: String,
    order: usize,
    page_no: u32,
    results: Vec<(Found, Swatch)>,
    more: bool,
    searched: bool,
    // Work and messages.
    busy: Option<String>,
    error: Option<String>,
    note: Option<String>,
    dialog: Option<Dialog>,
    save_name: String,
}

/// Run `f` on a thread and hand its message back to the window.
fn background(f: impl FnOnce() -> Message + Send + 'static) -> Task<cosmic::Action<Message>> {
    let (tx, rx) = oneshot::channel();
    std::thread::spawn(move || {
        let _ = tx.send(f());
    });
    cosmic::task::future(async move { rx.await.unwrap_or(Message::Done(Err("the work stopped unexpectedly".into()))) })
}

fn color(c: [f32; 4]) -> Color {
    Color::from_rgba(c[0], c[1], c[2], c[3])
}

fn filled<'a>(c: [f32; 4], radius: f32) -> cosmic::theme::Container<'a> {
    cosmic::theme::Container::custom(move |_| cosmic::iced::widget::container::Style {
        background: Some(color(c).into()),
        border: cosmic::iced::Border { radius: radius.into(), ..Default::default() },
        ..Default::default()
    })
}

impl App {
    /// The Reset dialog's text, naming the shipped Looks when the catalogue has them.
    fn reset_text(&self) -> String {
        let names = self.cat.as_ref().and_then(|c| {
            Some((c.default_look_name(Mode::Dark)?, c.default_look_name(Mode::Light)?))
        });
        let what = match names {
            Some((d, l)) => format!("the system defaults: {d} in dark mode, {l} in light mode"),
            None => "the system defaults, dark and light".to_string(),
        };
        format!("Icons and colours go back to {what}. Dark or light stays as it is. Undo puts your look back.")
    }

    fn reload_status(&mut self) {
        self.icons_now = desktop::icon_theme(&self.paths);
        self.mode = desktop::mode(&self.paths);
        self.colours_now = colours::now(&self.paths, self.mode);
        self.undo = desktop::undo_depth(&self.paths);
    }

    fn reload(&mut self) {
        self.cat = Catalogue::load(&self.paths.catalogue).ok();
        self.reload_status();
        let cat = self.cat.as_ref();
        self.looks = looks::list(&self.paths, cat)
            .into_iter()
            .map(|look| LookCard {
                swatch: looks::swatch(&self.paths, cat, &look),
                icons: icons::preview(&self.paths, &look.icons, &PREVIEW[..4], 32),
                look,
            })
            .collect();
        self.themes = icons::installed_themes(&self.paths)
            .into_iter()
            .map(|t| {
                let p = icons::preview(&self.paths, &t.dir, &PREVIEW, 32);
                (t, p)
            })
            .collect();
        self.packs = cat
            .map(|c| {
                c.icons
                    .iter()
                    .map(|p| {
                        let state = if icons::included(&self.paths, p) {
                            PackState::Included
                        } else if let Some(m) = icons::installed(&self.paths, p) {
                            PackState::Installed(m.version)
                        } else {
                            PackState::NotInstalled
                        };
                        (p.clone(), state)
                    })
                    .collect()
            })
            .unwrap_or_default();
        let mut lib: Vec<ColourCard> = cat
            .map(|c| {
                c.colours
                    .iter()
                    .filter_map(|x| {
                        let b = colours::parse(&c.colour_text(x).ok()?).ok()?;
                        Some(ColourCard {
                            id: x.id.clone(),
                            name: x.name.clone(),
                            by: x.author.clone(),
                            swatch: colours::swatch(&b),
                            mine: false,
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        lib.extend(colours::library(&self.paths).into_iter().filter_map(|e| {
            let (_, t) = colours::library_get(&self.paths, &e.id)?;
            Some(ColourCard {
                swatch: colours::swatch(&colours::parse(&t).ok()?),
                by: e.author.clone().unwrap_or_else(|| e.source.clone()),
                id: e.id,
                name: e.name,
                mine: true,
            })
        }));
        self.library = lib;
    }

    /// The result of a quick, local change: reload and say how it went.
    fn finish(&mut self, r: crate::Result<String>) {
        match r {
            Ok(note) => {
                self.note = Some(note);
                self.error = None;
            }
            Err(e) => {
                self.error = Some(e.to_string());
                self.note = None;
            }
        }
        self.reload();
    }

    fn look_in_use(&self, l: &Look) -> bool {
        self.icons_now == l.icons
            && self.mode == l.mode
            && match &self.colours_now {
                Now::Applied(a) => a.id == l.colours,
                Now::Default => l.colours == "default",
                Now::Custom => false,
            }
    }

    fn colours_in_use(&self, id: &str) -> bool {
        matches!(&self.colours_now, Now::Applied(a) if a.id == id)
    }

    fn idle(&self) -> bool {
        self.busy.is_none()
    }
}

impl cosmic::Application for App {
    type Executor = cosmic::executor::Default;
    type Flags = ();
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

    fn on_nav_select(&mut self, id: nav_bar::Id) -> Task<cosmic::Action<Message>> {
        self.nav.activate(id);
        if let Some(p) = self.nav.data::<Page>(id) {
            self.page = *p;
        }
        Task::none()
    }

    fn init(core: cosmic::Core, _flags: ()) -> (Self, Task<cosmic::Action<Message>>) {
        let mut nav = nav_bar::Model::default();
        nav.insert()
            .text("Looks")
            .icon(icon::from_name("preferences-desktop-theme-symbolic"))
            .data(Page::Looks)
            .activate();
        nav.insert().text("Icons").icon(icon::from_name("folder-pictures-symbolic")).data(Page::Icons);
        nav.insert().text("Colours").icon(icon::from_name("applications-graphics-symbolic")).data(Page::Colours);
        let paths = Paths::from_env().unwrap_or_else(|_| Paths::under(std::path::Path::new("/nonexistent")));
        let mut app = App {
            core,
            nav,
            paths,
            cat: None,
            page: Page::Looks,
            icons_now: COSMIC_ICONS.into(),
            mode: Mode::Dark,
            colours_now: Now::Default,
            undo: 0,
            looks: vec![],
            themes: vec![],
            packs: vec![],
            library: vec![],
            search: String::new(),
            order: 0,
            page_no: 1,
            results: vec![],
            more: false,
            searched: false,
            busy: None,
            error: None,
            note: None,
            dialog: None,
            save_name: String::new(),
        };
        app.reload();
        if app.cat.is_none() {
            app.error = Some(format!("The theme catalogue ({}) is missing.", app.paths.catalogue.display()));
        }
        let title = match app.core.main_window_id() {
            Some(id) => app.set_window_title("Themes".into(), id),
            None => Task::none(),
        };
        (app, title)
    }

    fn update(&mut self, msg: Message) -> Task<cosmic::Action<Message>> {
        match msg {
            Message::ApplyLook(id) => {
                let (paths, cat) = (self.paths.clone(), self.cat.clone());
                let needs_download = self
                    .looks
                    .iter()
                    .find(|c| c.look.id == id)
                    .is_some_and(|c| c.look.icons != COSMIC_ICONS && icons::find_theme(&self.paths, &c.look.icons).is_none());
                self.busy = Some(if needs_download { "Downloading its icons…".into() } else { "Applying…".into() });
                return background(move || {
                    Message::Done(
                        looks::apply(&paths, cat.as_ref(), &id, &Curl).map(|l| format!("{} applied.", l.name)).map_err(|e| e.0),
                    )
                });
            }
            Message::UseIcons(t) => {
                let r = icons::use_theme(&self.paths, &t).map(|_| format!("Icons: {t}."));
                self.finish(r);
            }
            Message::InstallPack(id) => {
                let (paths, Some(cat)) = (self.paths.clone(), self.cat.clone()) else { return Task::none() };
                let name = cat.pack(&id).map(|p| p.name.clone()).unwrap_or_default();
                self.busy = Some(format!("Downloading {name}…"));
                return background(move || {
                    Message::Done(
                        icons::install(&paths, &cat, &id, &Curl)
                            .map(|_| format!("{name} is installed. Pick it below, or apply a Look that uses it."))
                            .map_err(|e| e.0),
                    )
                });
            }
            Message::AskRemovePack(id) => self.dialog = Some(Dialog::RemovePack(id)),
            Message::RemovePack(id) => {
                self.dialog = None;
                if let Some(cat) = self.cat.clone() {
                    let r = icons::remove(&self.paths, &cat, &id).map(|switched| {
                        if switched {
                            "Removed. It was in use, so the icons are back to the system's.".into()
                        } else {
                            "Removed.".into()
                        }
                    });
                    self.finish(r);
                }
            }
            Message::ApplyColours(id) => {
                let r = colours::apply(&self.paths, self.cat.as_ref(), &id).map(|(e, _)| format!("{} applied.", e.name));
                self.finish(r);
            }
            Message::ForgetColours(id) => {
                let r = colours::forget(&self.paths, &id).map(|e| format!("{} is out of your library.", e.name));
                self.finish(r);
            }
            Message::ForgetLook(id) => {
                let r = looks::forget(&self.paths, &id).map(|l| format!("Forgot {}.", l.name));
                self.finish(r);
            }
            Message::SearchText(t) => self.search = t,
            Message::SearchOrder(i) => {
                self.order = i;
                if self.searched {
                    return self.update(Message::Search(1));
                }
            }
            Message::Search(page) => {
                let q = Query {
                    text: self.search.clone(),
                    order: [Order::Popular, Order::New, Order::Name][self.order.min(2)],
                    color: None,
                    page,
                };
                let paths = self.paths.clone();
                let api = std::env::var("PENGYOS_THEMES_API").unwrap_or_else(|_| colours::API.into());
                self.busy = Some("Asking cosmic-themes.org…".into());
                return background(move || {
                    Message::SearchDone(page, colours::search(&paths, &Curl, &api, &q).map_err(|e| e.0))
                });
            }
            Message::SearchDone(page, r) => {
                self.busy = None;
                self.searched = true;
                match r {
                    Ok((found, dropped)) => {
                        self.more = found.len() + dropped >= colours::PAGE as usize;
                        let found = found.into_iter().filter_map(|f| {
                            let s = colours::swatch(&colours::parse(&f.theme.ron).ok()?);
                            Some((f, s))
                        });
                        if page == 1 {
                            self.results = found.collect();
                        } else {
                            self.results.extend(found);
                        }
                        self.page_no = page;
                        self.error = None;
                    }
                    Err(e) => self.error = Some(e),
                }
            }
            Message::Done(r) => {
                self.busy = None;
                self.finish(r.map_err(crate::Error));
            }
            Message::Undo => {
                let r = desktop::undo(&self.paths).map(|l| match l {
                    Some(label) => format!("Undid: {label}."),
                    None => "Nothing to undo.".into(),
                });
                self.finish(r);
            }
            Message::AskReset => self.dialog = Some(Dialog::Reset),
            Message::Reset => {
                self.dialog = None;
                let r = desktop::reset(&self.paths, self.cat.as_ref())
                    .map(|_| "The default look is back. Undo puts yours back.".into());
                self.finish(r);
            }
            Message::AskSave => {
                self.save_name.clear();
                self.dialog = Some(Dialog::Save);
            }
            Message::SaveName(s) => self.save_name = s,
            Message::Save => {
                self.dialog = None;
                let r = looks::save(&self.paths, self.cat.as_ref(), &self.save_name)
                    .map(|l| format!("Saved {} with your Looks.", l.name));
                self.finish(r);
            }
            Message::CloseDialog => self.dialog = None,
            Message::Tick => {
                if self.idle() {
                    self.reload_status();
                }
            }
        }
        Task::none()
    }

    fn header_end(&self) -> Vec<Element<'_, Message>> {
        vec![
            button::text("Undo")
                .leading_icon(icon::from_name("edit-undo-symbolic"))
                .on_press_maybe((self.idle() && self.undo > 0).then_some(Message::Undo))
                .into(),
            button::text("Reset").on_press_maybe(self.idle().then_some(Message::AskReset)).into(),
        ]
    }

    fn dialog(&self) -> Option<Element<'_, Message>> {
        let d = self.dialog.as_ref()?;
        Some(match d {
            Dialog::Reset => widget::dialog()
                .title("Back to the default look?")
                .body(self.reset_text())
                .primary_action(button::destructive("Reset").on_press(Message::Reset))
                .secondary_action(button::standard("Cancel").on_press(Message::CloseDialog))
                .into(),
            Dialog::Save => widget::dialog()
                .title("Save as a Look")
                .body("Keeps the icons and colours you have now, to come back to in one click.")
                .control(
                    widget::text_input("Name", self.save_name.as_str())
                        .on_input(Message::SaveName)
                        .on_submit(|_| Message::Save),
                )
                .primary_action(
                    button::suggested("Save")
                        .on_press_maybe((!crate::slug(&self.save_name).is_empty()).then_some(Message::Save)),
                )
                .secondary_action(button::standard("Cancel").on_press(Message::CloseDialog))
                .into(),
            Dialog::RemovePack(id) => {
                let name = self.packs.iter().find(|(p, _)| &p.id == id).map(|(p, _)| p.name.clone()).unwrap_or_default();
                widget::dialog()
                    .title(format!("Remove {name}?"))
                    .body("Its icon themes are deleted from your home folder. If you use one now, the icons go back to the system's.")
                    .primary_action(button::destructive("Remove").on_press(Message::RemovePack(id.clone())))
                    .secondary_action(button::standard("Cancel").on_press(Message::CloseDialog))
                    .into()
            }
        })
    }

    fn view(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut body: Vec<Element<'_, Message>> = Vec::new();
        if let Some(b) = &self.busy {
            body.push(
                row![icon::from_name("content-loading-symbolic").size(16), text::body(b.clone())]
                    .spacing(8)
                    .align_y(Alignment::Center)
                    .into(),
            );
        }
        if let Some(e) = &self.error {
            body.push(text::body(e.clone()).class(cosmic::theme::Text::Accent).into());
        } else if let Some(n) = &self.note {
            body.push(text::body(n.clone()).into());
        }
        body.push(match self.page {
            Page::Looks => self.view_looks(),
            Page::Icons => self.view_icons(),
            Page::Colours => self.view_colours(),
        });
        let content = column::with_children(body).spacing(spacing.space_m).max_width(980);
        container(widget::scrollable(container(content).padding(spacing.space_m).width(Length::Fill)))
            .width(Length::Fill)
            .height(Length::Fill)
            .align_x(cosmic::iced::alignment::Horizontal::Center)
            .into()
    }

    fn subscription(&self) -> Subscription<Message> {
        // Settings or the sunset switch may change things while the window is open.
        cosmic::iced::time::every(Duration::from_secs(5)).map(|_| Message::Tick)
    }
}

// ---- pieces ------------------------------------------------------------------------

fn icon_row<'a>(paths: &[Option<PathBuf>], size: u16) -> Element<'a, Message> {
    let spacing = cosmic::theme::spacing();
    row::with_children(
        paths
            .iter()
            .flatten()
            .map(|p| widget::icon(icon::from_path(p.clone())).size(size).into())
            .collect::<Vec<Element<'a, Message>>>(),
    )
    .spacing(spacing.space_xxs)
    .into()
}

/// A small mock window in a theme's colours: background, a card holding the
/// icons, a line of text and the accent.
fn mock<'a>(s: Option<Swatch>, icons: &[Option<PathBuf>], width: f32) -> Element<'a, Message> {
    let s = s.unwrap_or(Swatch { bg: [0.5; 4], card: [0.6; 4], accent: [0.3, 0.6, 0.9, 1.0], text: [0.0, 0.0, 0.0, 1.0], dark: false });
    let text_c = color(s.text);
    let lines = column![
        text::body("Aa").class(cosmic::theme::Text::Color(text_c)),
        container(widget::Space::new().width(Length::Fixed(56.0)).height(Length::Fixed(8.0))).class(filled(s.accent, 4.0)),
    ]
    .spacing(6);
    let card = container(row![icon_row(icons, 28), widget::Space::new().width(Length::Fill), lines].align_y(Alignment::Center))
        .padding(10)
        .width(Length::Fill)
        .class(filled(s.card, 8.0));
    container(card).padding(12).width(Length::Fixed(width)).class(filled(s.bg, 12.0)).into()
}

fn swatch_strip<'a>(s: Swatch) -> Element<'a, Message> {
    let dot = |c: [f32; 4]| -> Element<'a, Message> {
        container(widget::Space::new().width(Length::Fixed(28.0)).height(Length::Fixed(28.0))).class(filled(c, 14.0)).into()
    };
    container(row![dot(s.card), dot(s.accent), dot(s.text)].spacing(6))
        .padding(8)
        .class(filled(s.bg, 10.0))
        .into()
}

fn badge<'a>(label: &'static str) -> Element<'a, Message> {
    text::body(label).class(cosmic::theme::Text::Accent).into()
}

impl App {
    fn view_looks(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let cards: Vec<Element<'_, Message>> = self
            .looks
            .iter()
            .map(|c| {
                let l = &c.look;
                let mut actions: Vec<Element<'_, Message>> = vec![if self.look_in_use(l) {
                    badge("In use")
                } else {
                    button::suggested("Apply").on_press_maybe(self.idle().then(|| Message::ApplyLook(l.id.clone()))).into()
                }];
                if !l.curated {
                    actions.push(button::text("Forget").on_press_maybe(self.idle().then(|| Message::ForgetLook(l.id.clone()))).into());
                }
                let summary = if l.summary.is_empty() {
                    format!("Yours: {} icons, {}.", l.icons, l.mode.as_str())
                } else {
                    l.summary.clone()
                };
                container(
                    column![
                        mock(c.swatch, &c.icons, 268.0),
                        text::heading(l.name.clone()),
                        text::caption(summary).width(Length::Fixed(268.0)),
                        row::with_children(actions).spacing(spacing.space_xs).align_y(Alignment::Center),
                    ]
                    .spacing(spacing.space_xs),
                )
                .padding(spacing.space_s)
                .class(cosmic::theme::Container::Card)
                .into()
            })
            .collect();
        column![
            text::title3("Looks"),
            text::body("Icons and colours that belong together, applied in one step. Undo, at the top, puts back what you had."),
            widget::flex_row(cards).row_spacing(spacing.space_s).column_spacing(spacing.space_s),
            section().add(
                item::builder("Keep what you have now")
                    .description("Saves your current icons and colours as a Look of your own.")
                    .control(button::standard("Save as a Look…").on_press_maybe(self.idle().then_some(Message::AskSave))),
            ),
        ]
        .spacing(spacing.space_m)
        .into()
    }

    fn view_icons(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut installed = section().title("Icon themes");
        for (t, p) in &self.themes {
            let control: Element<'_, Message> = if t.dir == self.icons_now {
                badge("In use")
            } else {
                button::standard("Use").on_press_maybe(self.idle().then(|| Message::UseIcons(t.dir.clone()))).into()
            };
            let follow = self
                .cat
                .as_ref()
                .is_some_and(|c| c.icons.iter().filter_map(|p| p.follow.as_ref()).any(|f| f.dark == t.dir || f.light == t.dir));
            let mut desc = if t.user { "Installed by you".to_string() } else { "Comes with PengyOS".to_string() };
            if follow {
                desc.push_str(". Switches with dark and light");
            }
            installed = installed.add(
                item::builder(t.name.clone())
                    .description(desc)
                    .control(row![icon_row(p, 24), control].spacing(spacing.space_m).align_y(Alignment::Center)),
            );
        }
        let mut more = section().title("More icon themes");
        for (p, state) in &self.packs {
            let control: Element<'_, Message> = match state {
                PackState::Included => text::caption("Comes with PengyOS").into(),
                PackState::Installed(v) if *v != p.version => button::standard(format!("Update to {}", p.version))
                    .on_press_maybe(self.idle().then(|| Message::InstallPack(p.id.clone())))
                    .into(),
                PackState::Installed(_) => {
                    button::destructive("Remove").on_press_maybe(self.idle().then(|| Message::AskRemovePack(p.id.clone()))).into()
                }
                PackState::NotInstalled => button::suggested(format!("Install ({} MB)", p.size.div_ceil(1_000_000)))
                    .on_press_maybe(self.idle().then(|| Message::InstallPack(p.id.clone())))
                    .into(),
            };
            more = more.add(
                item::builder(format!("{} {}", p.name, p.version))
                    .description(format!(
                        "{} {}. {} MB on disk.",
                        p.summary,
                        p.license,
                        p.installed_size.div_ceil(1_000_000)
                    ))
                    .control(control),
            );
        }
        column![
            text::title3("Icons"),
            text::body("Every app and the panel switch at once. Themes you install go in your home folder only."),
            installed,
            more,
        ]
        .spacing(spacing.space_m)
        .into()
    }

    fn colour_card<'a>(&'a self, id: String, name: String, by: String, s: Swatch, forget: bool) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        let mut actions: Vec<Element<'a, Message>> = vec![if self.colours_in_use(&id) {
            badge("In use")
        } else {
            button::suggested("Apply").on_press_maybe(self.idle().then(|| Message::ApplyColours(id.clone()))).into()
        }];
        if forget {
            actions.push(button::text("Forget").on_press_maybe(self.idle().then(|| Message::ForgetColours(id.clone()))).into());
        }
        container(
            column![
                row![swatch_strip(s), column![text::heading(name), text::caption(format!("{} · {}", by, if s.dark { "dark" } else { "light" }))]]
                    .spacing(spacing.space_s)
                    .align_y(Alignment::Center),
                row::with_children(actions).spacing(spacing.space_xs),
            ]
            .spacing(spacing.space_xs),
        )
        .padding(spacing.space_s)
        .width(Length::Fixed(300.0))
        .class(cosmic::theme::Container::Card)
        .into()
    }

    fn view_colours(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mine: Vec<Element<'_, Message>> = self
            .library
            .iter()
            .map(|c| self.colour_card(c.id.clone(), c.name.clone(), c.by.clone(), c.swatch, c.mine))
            .collect();
        let search = row![
            widget::text_input("Search cosmic-themes.org", self.search.as_str())
                .on_input(Message::SearchText)
                .on_submit(|_| Message::Search(1))
                .width(Length::Fill),
            widget::dropdown(&ORDERS[..], Some(self.order), Message::SearchOrder),
            button::suggested("Search").on_press_maybe(self.idle().then_some(Message::Search(1))),
        ]
        .spacing(spacing.space_xs)
        .align_y(Alignment::Center);
        let found: Vec<Element<'_, Message>> = self
            .results
            .iter()
            .map(|(f, s)| self.colour_card(f.id(), f.theme.name.clone(), f.theme.author.clone().unwrap_or_default(), *s, false))
            .collect();
        let mut c: Vec<Element<'_, Message>> = vec![
            text::title3("Colours").into(),
            text::body("Applying one also switches dark or light to match it. Each one you apply is kept here, so it works offline too.").into(),
            widget::flex_row(mine).row_spacing(spacing.space_s).column_spacing(spacing.space_s).into(),
            text::title4("From cosmic-themes.org").into(),
            text::caption("Made and shared by COSMIC users. Nothing is fetched until you search; themes are colours only, nothing in them runs.").into(),
            search.into(),
        ];
        if self.searched && found.is_empty() {
            c.push(text::body("Nothing found.").into());
        }
        c.push(widget::flex_row(found).row_spacing(spacing.space_s).column_spacing(spacing.space_s).into());
        if self.more {
            c.push(button::standard("More").on_press_maybe(self.idle().then_some(Message::Search(self.page_no + 1))).into());
        }
        column::with_children(c).spacing(spacing.space_m).into()
    }
}
