//! Looks: an icon theme and a colour theme, applied in one step and undone in
//! one step. Curated ones come from the catalogue; `save` keeps the current
//! pairing as the user's own (in `~/.local/share/pengyos-themes/looks/`).

use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::catalogue::{Catalogue, Mode, COSMIC_ICONS};
use crate::colours::{self, Entry, Now};
use crate::fetch::Fetch;
use crate::{desktop, err, icons, io_at, slug, valid_id, write_atomic, Paths, Result};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Look {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub summary: String,
    /// An icon theme directory name, or "Cosmic".
    pub icons: String,
    /// A catalogue or library colour id, or "default".
    pub colours: String,
    pub mode: Mode,
    /// From the catalogue (not the user's own).
    #[serde(skip)]
    pub curated: bool,
}

fn looks_dir(paths: &Paths) -> PathBuf {
    paths.state().join("looks")
}

fn user_looks(paths: &Paths) -> Vec<Look> {
    fs::read_dir(looks_dir(paths))
        .map(|rd| {
            rd.flatten()
                .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
                .filter_map(|e| serde_json::from_slice::<Look>(&fs::read(e.path()).ok()?).ok())
                .filter(|l| valid_id(&l.id))
                .collect()
        })
        .unwrap_or_default()
}

/// Curated Looks first (catalogue order), then the user's (by name).
pub fn list(paths: &Paths, cat: Option<&Catalogue>) -> Vec<Look> {
    let mut v: Vec<Look> = cat
        .map(|c| {
            c.looks
                .iter()
                .map(|l| Look {
                    id: l.id.clone(),
                    name: l.name.clone(),
                    summary: l.summary.clone(),
                    icons: l.icons.clone(),
                    colours: l.colours.clone(),
                    mode: l.mode,
                    curated: true,
                })
                .collect()
        })
        .unwrap_or_default();
    let mut mine = user_looks(paths);
    mine.sort_by_key(|l| l.name.to_lowercase());
    v.extend(mine);
    v
}

/// The colours a Look's preview shows: its colour theme, or COSMIC's shipped
/// default for its mode.
pub fn swatch(paths: &Paths, cat: Option<&Catalogue>, look: &Look) -> Option<colours::Swatch> {
    let b = match look.colours.as_str() {
        "default" => {
            let scratch = Paths { config: paths.state().join(".swatch-default"), ..paths.clone() };
            desktop::current_builder(&scratch, look.mode).ok()?
        }
        c => colours::parse(&colours::resolve(paths, cat, c).ok()?.text).ok()?,
    };
    Some(colours::swatch(&b))
}

/// Apply a Look. An icon theme from the catalogue that is not installed yet is
/// installed first (the only network a Look needs); everything else is local.
pub fn apply(paths: &Paths, cat: Option<&Catalogue>, id: &str, fetch: &dyn Fetch) -> Result<Look> {
    let look = list(paths, cat)
        .into_iter()
        .find(|l| l.id == id)
        .ok_or_else(|| err!("no Look {id:?} (`pengyos-themes looks list`)"))?;
    // Resolve everything before changing anything.
    let resolved = match look.colours.as_str() {
        "default" => None,
        c => Some(colours::resolve(paths, cat, c)?),
    };
    if look.icons != COSMIC_ICONS && icons::find_theme(paths, &look.icons).is_none() {
        let pack = cat
            .and_then(|c| c.pack_for_theme(&look.icons))
            .ok_or_else(|| err!("the icon theme {} is not installed", look.icons))?;
        icons::install(paths, cat.unwrap(), &pack.id, fetch)?;
    }
    desktop::snapshot(paths, &format!("look {}", look.name))?;
    desktop::set_icon_theme(paths, &look.icons)?;
    match &resolved {
        None => {
            desktop::default_colours(paths, look.mode)?;
            colours::record_applied(paths, None)?;
        }
        Some(r) => {
            colours::write(paths, r)?;
        }
    }
    Ok(look)
}

/// Save what the desktop shows now as a Look called `name`.
pub fn save(paths: &Paths, cat: Option<&Catalogue>, name: &str) -> Result<Look> {
    let id = slug(name);
    if id.is_empty() {
        return Err(err!("give the Look a name with some letters or digits in it"));
    }
    if cat.is_some_and(|c| c.look(&id).is_some()) {
        return Err(err!("{id} is a curated Look's name: choose another"));
    }
    let mode = desktop::mode(paths);
    let colours = match colours::now(paths, mode) {
        Now::Default => "default".to_string(),
        // Keep the colours themselves, so the Look survives the library entry
        // (or the Settings tweak) it came from.
        Now::Applied(_) | Now::Custom => {
            let b = desktop::current_builder(paths, mode)?;
            let text = ron::ser::to_string_pretty(&b, ron::ser::PrettyConfig::default())
                .map_err(|e| err!("cannot save the colours: {e}"))?;
            let entry = Entry {
                id: format!("look-{id}"),
                name: name.trim().to_string(),
                author: None,
                source: "look".into(),
                mode,
            };
            colours::library_add(paths, &entry, &text)?;
            entry.id
        }
    };
    let look = Look {
        id: id.clone(),
        name: name.trim().to_string(),
        summary: String::new(),
        icons: desktop::icon_theme(paths),
        colours,
        mode,
        curated: false,
    };
    write_atomic(
        &looks_dir(paths).join(format!("{id}.json")),
        serde_json::to_string_pretty(&look).unwrap().as_bytes(),
    )?;
    Ok(look)
}

/// Forget one of the user's Looks (and the colours saved with it).
pub fn forget(paths: &Paths, id: &str) -> Result<Look> {
    let look = user_looks(paths)
        .into_iter()
        .find(|l| l.id == id)
        .ok_or_else(|| err!("{id} is not one of your Looks"))?;
    let p = looks_dir(paths).join(format!("{id}.json"));
    io_at(fs::remove_file(&p), "cannot remove", &p)?;
    if look.colours == format!("look-{id}") {
        let _ = colours::forget(paths, &look.colours);
    }
    Ok(look)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalogue::tests::GOOD;
    use crate::colours::tests::ron_of;
    use crate::fetch::stub::Stub;
    use crate::unpack::testing::{tar_gz, E};
    use cosmic_theme::ThemeBuilder;

    fn setup() -> (tempfile::TempDir, Paths, Catalogue, Stub) {
        let d = tempfile::tempdir().unwrap();
        let p = Paths::under(d.path());
        let bytes = tar_gz(&[
            E::File("pack-1/Ice/index.theme", b"[Icon Theme]\n"),
            E::File("pack-1/Ice-Dark/index.theme", b"[Icon Theme]\n"),
        ]);
        let cdir = d.path().join("catalogue");
        fs::create_dir_all(cdir.join("colours")).unwrap();
        let light = ThemeBuilder::light().accent(cosmic_theme::palette::Srgb::new(0.2, 0.6, 0.4));
        fs::write(cdir.join("colours/meadow.ron"), ron_of(&light)).unwrap();
        let text = GOOD
            .replace("0000000000000000000000000000000000000000000000000000000000000000", &crate::sha256_hex(&bytes))
            .replace("size = 100", &format!("size = {}", bytes.len()))
            + r#"
[[colours]]
id = "meadow"
name = "Meadow"
author = "PengyOS"
license = "MIT"
file = "colours/meadow.ron"

[[looks]]
id = "spring"
name = "Spring"
summary = "test"
icons = "Ice"
colours = "meadow"
mode = "light"
"#;
        fs::write(cdir.join("catalogue.toml"), text).unwrap();
        let cat = Catalogue::load(&cdir.join("catalogue.toml")).unwrap();
        cat.check_files().unwrap();
        (d, p, cat, Stub::with("https://example.org/ice-1.tar.gz", bytes))
    }

    #[test]
    fn a_curated_look_installs_its_icons_then_works_offline() {
        let (_d, p, cat, stub) = setup();
        let l = apply(&p, Some(&cat), "spring", &stub).unwrap();
        assert_eq!(l.name, "Spring");
        assert_eq!(desktop::icon_theme(&p), "Ice");
        assert_eq!(desktop::mode(&p), Mode::Light);
        assert!(matches!(colours::now(&p, Mode::Light), Now::Applied(a) if a.id == "meadow"));
        // Again, with no network at all.
        apply(&p, Some(&cat), "frost", &Stub::default()).unwrap();
        assert_eq!((desktop::icon_theme(&p).as_str(), desktop::mode(&p)), ("Ice-Dark", Mode::Dark));
        assert_eq!(colours::now(&p, Mode::Dark), Now::Default);
        apply(&p, Some(&cat), "spring", &Stub::default()).unwrap();
        assert_eq!(desktop::icon_theme(&p), "Ice");
    }

    #[test]
    fn a_look_is_one_undo_step() {
        let (_d, p, cat, stub) = setup();
        let before = desktop::tree(&p.cosmic());
        apply(&p, Some(&cat), "spring", &stub).unwrap();
        desktop::undo(&p).unwrap();
        assert_eq!(desktop::tree(&p.cosmic()), before);
    }

    #[test]
    fn an_unknown_look_or_missing_colours_change_nothing() {
        let (_d, p, cat, stub) = setup();
        assert!(apply(&p, Some(&cat), "nope", &stub).is_err());
        fs::create_dir_all(looks_dir(&p)).unwrap();
        fs::write(
            looks_dir(&p).join("x.json"),
            r#"{"id":"x","name":"X","icons":"Cosmic","colours":"site-404","mode":"dark"}"#,
        )
        .unwrap();
        assert!(apply(&p, Some(&cat), "x", &stub).is_err());
        assert!(!p.config.exists());
        assert_eq!(desktop::undo_depth(&p), 0);
    }

    #[test]
    fn save_keeps_the_pairing_and_the_colours_themselves() {
        let (_d, p, cat, stub) = setup();
        apply(&p, Some(&cat), "spring", &stub).unwrap();
        let mine = save(&p, Some(&cat), "My Spring").unwrap();
        assert_eq!((mine.id.as_str(), mine.icons.as_str(), mine.colours.as_str()), ("my-spring", "Ice", "look-my-spring"));
        let wanted = desktop::current_builder(&p, Mode::Light).unwrap();
        desktop::reset(&p, None).unwrap();
        apply(&p, Some(&cat), "my-spring", &Stub::default()).unwrap();
        assert_eq!(desktop::current_builder(&p, Mode::Light).unwrap(), wanted);
        assert_eq!(desktop::icon_theme(&p), "Ice");
        assert!(list(&p, Some(&cat)).iter().any(|l| l.id == "my-spring" && !l.curated));
        assert!(save(&p, Some(&cat), "Spring").unwrap_err().0.contains("curated"));
        forget(&p, "my-spring").unwrap();
        assert!(colours::library(&p).is_empty());
        assert!(forget(&p, "spring").is_err(), "curated Looks are not the user's to forget");
    }
}
