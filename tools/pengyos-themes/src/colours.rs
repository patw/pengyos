//! Colour themes: COSMIC `ThemeBuilder`s in RON, from cosmic-themes.org, a file,
//! or the catalogue; and the user's library of the ones they applied.
//!
//! A colour theme is data. It is parsed with libcosmic's own types and refused
//! unless it parses, after a size cap; nothing in it is ever executed.
//!
//! cosmic-themes.org's API (`/api/themes/`) lists themes with their RON inline:
//! `search`, `order=popular|new|name`, `color=#rrggbb`, `limit`, `offset`. It
//! has no single-theme endpoint, so `search` keeps its last answer
//! (`site-cache.json`) and `apply <site id>` takes the theme from there.

use std::fs;
use std::path::{Path, PathBuf};

use cosmic_theme::ThemeBuilder;
use serde::{Deserialize, Serialize};

use crate::catalogue::{Catalogue, Mode};
use crate::fetch::{query_escape, Fetch};
use crate::{desktop, err, io_at, slug, valid_id, write_atomic, Paths, Result};

/// The largest colour theme accepted (the biggest on cosmic-themes.org is ~8 KB).
pub const MAX_RON: u64 = 64 * 1024;
/// The largest search answer accepted.
pub const MAX_ANSWER: u64 = 4 * 1024 * 1024;
pub const API: &str = "https://cosmic-themes.org/api";
pub const API_TIMEOUT_S: u64 = 20;
pub const PAGE: u32 = 20;

/// Parse a colour theme. Refused unless it is a `ThemeBuilder`.
pub fn parse(text: &str) -> Result<ThemeBuilder> {
    if text.len() as u64 > MAX_RON {
        return Err(err!("refusing the colour theme: larger than {} KB", MAX_RON / 1024));
    }
    ron::from_str::<ThemeBuilder>(text).map_err(|e| err!("not a COSMIC colour theme: {e}"))
}

/// The palette name a theme carries (shown when it has no other name).
pub fn palette_name(b: &ThemeBuilder) -> String {
    use cosmic_theme::CosmicPalette::*;
    match &b.palette {
        Dark(p) | Light(p) | HighContrastDark(p) | HighContrastLight(p) => p.name.clone(),
    }
}

/// The colours a preview needs, as RGBA, from the theme a builder builds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Swatch {
    pub bg: [f32; 4],
    pub card: [f32; 4],
    pub accent: [f32; 4],
    pub text: [f32; 4],
    pub dark: bool,
}

pub fn swatch(b: &ThemeBuilder) -> Swatch {
    let t = b.clone().build();
    let c = |x: cosmic_theme::palette::Srgba| [x.red, x.green, x.blue, x.alpha];
    Swatch {
        bg: c(t.bg_color()),
        card: c(t.primary_container_color()),
        accent: c(t.accent_color()),
        text: c(t.on_bg_color()),
        dark: b.palette.is_dark(),
    }
}

// ---- cosmic-themes.org ----------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Order {
    Popular,
    New,
    Name,
}

impl Order {
    pub fn parse(s: &str) -> Option<Order> {
        match s {
            "popular" => Some(Order::Popular),
            "new" => Some(Order::New),
            "name" => Some(Order::Name),
            _ => None,
        }
    }
    fn as_str(self) -> &'static str {
        match self {
            Order::Popular => "popular",
            Order::New => "new",
            Order::Name => "name",
        }
    }
}

#[derive(Debug, Clone)]
pub struct Query {
    pub text: String,
    pub order: Order,
    /// `rrggbb`, without the `#`.
    pub color: Option<String>,
    /// 1-based.
    pub page: u32,
}

impl Default for Query {
    fn default() -> Self {
        Query { text: String::new(), order: Order::Popular, color: None, page: 1 }
    }
}

/// `#rrggbb` or `rrggbb` -> `rrggbb`.
pub fn parse_hex(s: &str) -> Option<String> {
    let h = s.strip_prefix('#').unwrap_or(s);
    (h.len() == 6 && h.bytes().all(|b| b.is_ascii_hexdigit())).then(|| h.to_ascii_lowercase())
}

pub fn search_url(api: &str, q: &Query) -> String {
    let mut u = format!(
        "{}/themes/?order={}&limit={PAGE}&offset={}",
        api.trim_end_matches('/'),
        q.order.as_str(),
        (q.page.max(1) - 1) * PAGE
    );
    if !q.text.trim().is_empty() {
        u.push_str(&format!("&search={}", query_escape(q.text.trim())));
    }
    if let Some(c) = &q.color {
        u.push_str(&format!("&color={}", query_escape(&format!("#{c}"))));
    }
    u
}

/// One theme as the site lists it.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SiteTheme {
    pub id: u64,
    pub name: String,
    #[serde(default)]
    pub author: Option<String>,
    #[serde(default)]
    pub link: Option<String>,
    #[serde(default)]
    pub downloads: Option<u64>,
    pub ron: String,
}

/// A search hit that parses, with its mode.
#[derive(Debug, Clone)]
pub struct Found {
    pub theme: SiteTheme,
    pub mode: Mode,
}

impl Found {
    pub fn id(&self) -> String {
        format!("site-{}", self.theme.id)
    }
}

/// Search the site. Themes that do not parse as this COSMIC's colour themes are
/// left out (the number left out is returned too).
pub fn search(paths: &Paths, fetch: &dyn Fetch, api: &str, q: &Query) -> Result<(Vec<Found>, usize)> {
    let body = fetch.get(&search_url(api, q), MAX_ANSWER, API_TIMEOUT_S)?;
    let list: Vec<SiteTheme> = serde_json::from_slice(&body)
        .map_err(|e| err!("cosmic-themes.org gave an answer this tool does not understand: {e}"))?;
    let mut found = Vec::new();
    let mut dropped = 0;
    for t in list {
        match parse(&t.ron) {
            Ok(b) => found.push(Found { mode: Mode::from_dark(b.palette.is_dark()), theme: t }),
            Err(_) => dropped += 1,
        }
    }
    // Remember the answer, so `apply site-N` works without asking again.
    let mut cache = site_cache(paths);
    for f in &found {
        cache.retain(|t| t.id != f.theme.id);
        cache.push(f.theme.clone());
    }
    let keep = cache.len().saturating_sub(200);
    cache.drain(..keep);
    let _ = write_atomic(
        &paths.state().join("site-cache.json"),
        serde_json::to_string(&cache).unwrap_or_default().as_bytes(),
    );
    Ok((found, dropped))
}

fn site_cache(paths: &Paths) -> Vec<SiteTheme> {
    fs::read(paths.state().join("site-cache.json"))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

// ---- the library ------------------------------------------------------------------

/// A colour theme the user applied or saved, kept so it can be applied again
/// offline and outlives the site.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Entry {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub author: Option<String>,
    /// Where it came from: a cosmic-themes.org link, a file path, "look", "catalogue".
    pub source: String,
    pub mode: Mode,
}

fn library_dir(paths: &Paths) -> PathBuf {
    paths.state().join("colours")
}

pub fn library(paths: &Paths) -> Vec<Entry> {
    let mut v: Vec<Entry> = fs::read_dir(library_dir(paths))
        .map(|rd| {
            rd.flatten()
                .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
                .filter_map(|e| serde_json::from_slice(&fs::read(e.path()).ok()?).ok())
                .collect()
        })
        .unwrap_or_default();
    v.sort_by_key(|e: &Entry| e.name.to_lowercase());
    v
}

pub fn library_get(paths: &Paths, id: &str) -> Option<(Entry, String)> {
    if !valid_id(id) {
        return None;
    }
    let d = library_dir(paths);
    let e: Entry = serde_json::from_slice(&fs::read(d.join(format!("{id}.json"))).ok()?).ok()?;
    let text = fs::read_to_string(d.join(format!("{id}.ron"))).ok()?;
    Some((e, text))
}

pub fn library_add(paths: &Paths, e: &Entry, text: &str) -> Result<()> {
    if !valid_id(&e.id) {
        return Err(err!("{:?} is not a usable id", e.id));
    }
    let d = library_dir(paths);
    write_atomic(&d.join(format!("{}.ron", e.id)), text.as_bytes())?;
    write_atomic(&d.join(format!("{}.json", e.id)), serde_json::to_string_pretty(e).unwrap().as_bytes())
}

/// Forget a library entry. The desktop keeps its colours; only the copy goes.
pub fn forget(paths: &Paths, id: &str) -> Result<Entry> {
    let (e, _) = library_get(paths, id).ok_or_else(|| err!("{id} is not in your colour library"))?;
    let d = library_dir(paths);
    for ext in ["ron", "json"] {
        let p = d.join(format!("{id}.{ext}"));
        io_at(fs::remove_file(&p), "cannot remove", &p)?;
    }
    Ok(e)
}

// ---- apply ------------------------------------------------------------------------

/// What `apply` was asked for, resolved to a theme.
#[derive(Debug, Clone)]
pub struct Resolved {
    pub entry: Entry,
    pub text: String,
    /// Keep a copy in the library (site and file themes).
    pub keep: bool,
}

/// `arg` is a file (has a `/` or ends in `.ron`), a catalogue colour id, a
/// library id, or a cosmic-themes.org id from the last search (`site-25` or `25`).
pub fn resolve(paths: &Paths, cat: Option<&Catalogue>, arg: &str) -> Result<Resolved> {
    if arg.contains('/') || arg.ends_with(".ron") {
        let p = Path::new(arg);
        let meta = io_at(fs::metadata(p), "cannot read", p)?;
        if meta.len() > MAX_RON {
            return Err(err!("refusing {arg}: larger than {} KB", MAX_RON / 1024));
        }
        let text = io_at(fs::read_to_string(p), "cannot read", p)?;
        let b = parse(&text)?;
        let stem = p.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        let name = if stem.is_empty() { palette_name(&b) } else { stem };
        let id = format!("file-{}", slug(&name));
        let id = if id == "file-" { "file-theme".to_string() } else { id };
        let source = fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf()).display().to_string();
        let mode = Mode::from_dark(b.palette.is_dark());
        return Ok(Resolved { entry: Entry { id, name, author: None, source, mode }, text, keep: true });
    }
    if let Some(c) = cat.and_then(|c| c.colour(arg)) {
        let text = cat.unwrap().colour_text(c)?;
        let b = parse(&text)?;
        let entry = Entry {
            id: c.id.clone(),
            name: c.name.clone(),
            author: Some(c.author.clone()),
            source: "catalogue".into(),
            mode: Mode::from_dark(b.palette.is_dark()),
        };
        return Ok(Resolved { entry, text, keep: false });
    }
    if let Some((entry, text)) = library_get(paths, arg) {
        return Ok(Resolved { entry, text, keep: false });
    }
    let site_id = arg.strip_prefix("site-").unwrap_or(arg);
    if let Ok(n) = site_id.parse::<u64>() {
        let t = site_cache(paths).into_iter().find(|t| t.id == n).ok_or_else(|| {
            err!("cosmic-themes.org theme {n} is not in the last search: run `pengyos-themes colours search` first")
        })?;
        let b = parse(&t.ron)?;
        let entry = Entry {
            id: format!("site-{n}"),
            name: t.name.clone(),
            author: t.author.clone(),
            source: t.link.clone().filter(|l| l.starts_with("https://")).unwrap_or_else(|| "https://cosmic-themes.org".into()),
            mode: Mode::from_dark(b.palette.is_dark()),
        };
        return Ok(Resolved { entry, text: t.ron, keep: true });
    }
    Err(err!("no colour theme {arg:?}: not a file, a catalogue theme, in your library, or a cosmic-themes.org id"))
}

/// What the tool last applied, to say in `status` whether it is still in use.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Applied {
    pub id: String,
    pub name: String,
    pub mode: Mode,
    pub digest: String,
}

fn applied_path(paths: &Paths) -> PathBuf {
    paths.state().join("applied.json")
}

pub(crate) fn record_applied(paths: &Paths, a: Option<&Applied>) -> Result<()> {
    match a {
        Some(a) => write_atomic(&applied_path(paths), serde_json::to_string_pretty(a).unwrap().as_bytes()),
        None => {
            let _ = fs::remove_file(applied_path(paths));
            Ok(())
        }
    }
}

/// Write a resolved theme to the desktop (no snapshot: the caller takes one).
pub(crate) fn write(paths: &Paths, r: &Resolved) -> Result<Mode> {
    let b = parse(&r.text)?;
    if r.keep {
        library_add(paths, &r.entry, &r.text)?;
    }
    let mode = desktop::apply_builder(paths, &b)?;
    record_applied(
        paths,
        Some(&Applied {
            id: r.entry.id.clone(),
            name: r.entry.name.clone(),
            mode,
            digest: desktop::builder_digest(paths, mode),
        }),
    )?;
    Ok(mode)
}

/// `colours apply`: resolve, snapshot, write.
pub fn apply(paths: &Paths, cat: Option<&Catalogue>, arg: &str) -> Result<(Entry, Mode)> {
    let r = resolve(paths, cat, arg)?;
    desktop::snapshot(paths, &format!("colours {}", r.entry.name))?;
    let mode = write(paths, &r)?;
    Ok((r.entry, mode))
}

/// Which colours the desktop shows now.
#[derive(Debug, Clone, PartialEq)]
pub enum Now {
    /// COSMIC's defaults (nothing of the user's for this mode).
    Default,
    /// The theme this tool applied last, unchanged since.
    Applied(Applied),
    /// Something else (changed in Settings, or applied by another tool).
    Custom,
}

pub fn now(paths: &Paths, mode: Mode) -> Now {
    let digest = desktop::builder_digest(paths, mode);
    if let Some(a) = fs::read(applied_path(paths))
        .ok()
        .and_then(|b| serde_json::from_slice::<Applied>(&b).ok())
        .filter(|a| a.mode == mode && a.digest == digest)
    {
        return Now::Applied(a);
    }
    let user = paths.cosmic().join(match mode {
        Mode::Dark => cosmic_theme::DARK_THEME_BUILDER_ID,
        Mode::Light => cosmic_theme::LIGHT_THEME_BUILDER_ID,
    });
    if desktop::tree(&user).is_empty() {
        return Now::Default;
    }
    // The user's keys may simply be the shipped defaults (after a reset).
    let scratch = Paths { config: paths.state().join(format!(".now-{}", std::process::id())), ..paths.clone() };
    let _ = fs::remove_dir_all(&scratch.config);
    let default = desktop::builder_digest(&scratch, mode);
    let _ = fs::remove_dir_all(&scratch.config);
    if default == digest {
        Now::Default
    } else {
        Now::Custom
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::fetch::stub::Stub;

    pub fn ron_of(b: &ThemeBuilder) -> String {
        ron::ser::to_string_pretty(b, ron::ser::PrettyConfig::default()).unwrap()
    }

    fn home() -> (tempfile::TempDir, Paths) {
        let d = tempfile::tempdir().unwrap();
        let p = Paths::under(d.path());
        (d, p)
    }

    fn site_answer() -> Vec<u8> {
        let light = ThemeBuilder::light().accent(cosmic_theme::palette::Srgb::new(0.9, 0.4, 0.1));
        serde_json::to_vec(&serde_json::json!([
            {"id": 25, "uuid": "x", "name": "Frost Dark", "author": "a", "link": "https://example.org/frost",
             "downloads": 10, "created": "2024", "updated": "2024", "ron": ron_of(&ThemeBuilder::dark())},
            {"id": 26, "name": "Ember Light", "ron": ron_of(&light)},
            {"id": 27, "name": "Broken", "ron": "(palette: Nope)"},
        ]))
        .unwrap()
    }

    #[test]
    fn swatches_come_from_the_built_theme() {
        let s = swatch(&ThemeBuilder::dark());
        assert!(s.dark && s.bg[0] < 0.3 && s.text[0] > 0.7);
        let s = swatch(&ThemeBuilder::light());
        assert!(!s.dark && s.bg[0] > 0.7 && s.text[0] < 0.3);
    }

    #[test]
    fn search_urls() {
        let q = Query { text: "nord dark".into(), order: Order::New, color: parse_hex("#FF0000"), page: 2 };
        assert_eq!(
            search_url("https://cosmic-themes.org/api/", &q),
            "https://cosmic-themes.org/api/themes/?order=new&limit=20&offset=20&search=nord%20dark&color=%23ff0000"
        );
        assert_eq!(search_url(API, &Query::default()), format!("{API}/themes/?order=popular&limit=20&offset=0"));
        assert_eq!(parse_hex("12345"), None);
        assert_eq!(parse_hex("zzzzzz"), None);
    }

    #[test]
    fn search_parses_drops_the_unparseable_and_remembers() {
        let (_d, p) = home();
        let url = search_url(API, &Query::default());
        let stub = Stub::with(&url, site_answer());
        let (found, dropped) = search(&p, &stub, API, &Query::default()).unwrap();
        assert_eq!(dropped, 1);
        assert_eq!(
            found.iter().map(|f| (f.id(), f.mode)).collect::<Vec<_>>(),
            vec![("site-25".into(), Mode::Dark), ("site-26".into(), Mode::Light)]
        );
        // apply takes it from the remembered answer, with no network at all.
        let (e, mode) = apply(&p, None, "site-26").unwrap();
        assert_eq!((e.name.as_str(), mode), ("Ember Light", Mode::Light));
        assert_eq!(desktop::mode(&p), Mode::Light);
        assert!(library_get(&p, "site-26").is_some(), "kept in the library");
        assert_eq!(apply(&p, None, "25").unwrap().0.source, "https://example.org/frost");
    }

    #[test]
    fn offline_search_fails_cleanly() {
        let (_d, p) = home();
        let e = search(&p, &Stub::default(), API, &Query::default()).unwrap_err();
        assert!(e.0.contains("no network"), "{e}");
        assert!(resolve(&p, None, "site-99").unwrap_err().0.contains("colours search` first"));
    }

    #[test]
    fn a_bad_answer_is_an_error_not_a_crash() {
        let (_d, p) = home();
        let stub = Stub::with(&search_url(API, &Query::default()), b"<html>".to_vec());
        assert!(search(&p, &stub, API, &Query::default()).unwrap_err().0.contains("does not understand"));
    }

    #[test]
    fn malformed_or_oversized_themes_touch_nothing() {
        let (d, p) = home();
        let bad = d.path().join("bad.ron");
        fs::write(&bad, "(palette: Dark((name: \"x\"))) // and then some").unwrap();
        let big = d.path().join("big.ron");
        fs::write(&big, format!("{}\n{}", ron_of(&ThemeBuilder::dark()), "/*".to_string() + &" ".repeat(70_000) + "*/"))
            .unwrap();
        for f in [&bad, &big] {
            assert!(apply(&p, None, f.to_str().unwrap()).is_err());
        }
        assert!(!p.config.exists(), "no config written");
        assert_eq!(desktop::undo_depth(&p), 0, "no undo step either");
        assert!(library(&p).is_empty());
    }

    #[test]
    fn a_file_is_applied_and_kept() {
        let (d, p) = home();
        let f = d.path().join("My Theme.ron");
        fs::write(&f, ron_of(&ThemeBuilder::dark())).unwrap();
        let (e, _) = apply(&p, None, f.to_str().unwrap()).unwrap();
        assert_eq!((e.id.as_str(), e.name.as_str()), ("file-my-theme", "My Theme"));
        fs::remove_file(&f).unwrap();
        apply(&p, None, "file-my-theme").unwrap();
        assert_eq!(forget(&p, "file-my-theme").unwrap().name, "My Theme");
        assert!(library(&p).is_empty());
        assert!(forget(&p, "file-my-theme").is_err());
    }

    #[test]
    fn now_tells_default_applied_and_custom_apart() {
        let (_d, p) = home();
        assert_eq!(now(&p, Mode::Dark), Now::Default);
        let stub = Stub::with(&search_url(API, &Query::default()), site_answer());
        search(&p, &stub, API, &Query::default()).unwrap();
        apply(&p, None, "site-25").unwrap();
        assert!(matches!(now(&p, Mode::Dark), Now::Applied(a) if a.id == "site-25"));
        desktop::apply_builder(&p, &ThemeBuilder::dark().accent(cosmic_theme::palette::Srgb::new(0.0, 1.0, 0.0)))
            .unwrap();
        assert_eq!(now(&p, Mode::Dark), Now::Custom);
        desktop::reset(&p).unwrap();
        assert_eq!(now(&p, Mode::Dark), Now::Default);
    }
}
