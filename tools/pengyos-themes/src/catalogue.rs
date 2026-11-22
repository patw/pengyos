//! The catalogue the image ships (`/usr/share/pengyos/themes/catalogue.toml`):
//! curated icon packs, curated colour themes and curated Looks.
//!
//! ```toml
//! version = 1
//!
//! [[icons]]
//! id = "papirus"
//! name = "Papirus"
//! version = "20260801"
//! summary = "…"
//! license = "GPL-3.0-only"
//! homepage = "https://github.com/PapirusDevelopmentTeam/papirus-icon-theme"
//! url = "https://github.com/…/20260801.tar.gz"
//! sha256 = "646f…"
//! size = 33053628            # the download, bytes
//! installed_size = 260000000 # unpacked, bytes (a little over: it is also the unpack budget)
//! themes = [
//!   { dir = "Papirus", path = "papirus-icon-theme-20260801/Papirus" },
//! ]
//!
//! [[colours]]
//! id = "pengyos-glacier-dark"
//! name = "Glacier Dark"
//! author = "PengyOS"
//! license = "MIT"
//! file = "colours/glacier-dark.ron"   # next to the catalogue
//!
//! [[looks]]
//! id = "glacier"
//! name = "Glacier"
//! summary = "…"
//! icons = "Papirus-Dark"     # a theme a pack provides, or "Cosmic"
//! colours = "pengyos-glacier-dark"   # a catalogue colour theme, or "default"
//! mode = "dark"
//! ```
//!
//! [`Catalogue::load`] checks every rule, so a bad catalogue is caught by the
//! tests on the shipped one, never on a user's machine.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::{err, io_at, valid_id, Result};

/// The icon theme COSMIC uses when nothing else is chosen.
pub const COSMIC_ICONS: &str = "Cosmic";

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Catalogue {
    pub version: u32,
    #[serde(default)]
    pub icons: Vec<IconPack>,
    #[serde(default)]
    pub colours: Vec<CatColour>,
    #[serde(default)]
    pub looks: Vec<CatLook>,
    /// The directory the catalogue was read from (colour files are relative to it).
    #[serde(skip)]
    pub dir: PathBuf,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IconPack {
    pub id: String,
    pub name: String,
    pub version: String,
    pub summary: String,
    pub license: String,
    pub homepage: String,
    pub url: String,
    pub sha256: String,
    pub size: u64,
    pub installed_size: u64,
    pub themes: Vec<PackTheme>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PackTheme {
    /// The theme's directory name under `~/.local/share/icons` (and its name in
    /// COSMIC Settings' picker).
    pub dir: String,
    /// Where it is inside the archive.
    pub path: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatColour {
    pub id: String,
    pub name: String,
    pub author: String,
    pub license: String,
    pub file: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Dark,
    Light,
}

impl Mode {
    pub fn is_dark(self) -> bool {
        self == Mode::Dark
    }
    pub fn from_dark(dark: bool) -> Mode {
        if dark {
            Mode::Dark
        } else {
            Mode::Light
        }
    }
    pub fn as_str(self) -> &'static str {
        if self.is_dark() {
            "dark"
        } else {
            "light"
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatLook {
    pub id: String,
    pub name: String,
    pub summary: String,
    pub icons: String,
    pub colours: String,
    pub mode: Mode,
}

/// A theme directory name: one plain path component, no leading dot.
pub fn valid_theme_dir(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && !s.starts_with('.')
        && s.chars().all(|c| c.is_ascii_alphanumeric() || "-_+.".contains(c))
}

impl Catalogue {
    pub fn load(path: &Path) -> Result<Catalogue> {
        let text = io_at(std::fs::read_to_string(path), "cannot read the catalogue", path)?;
        let mut cat = Catalogue::parse(&text).map_err(|e| err!("{}: {e}", path.display()))?;
        cat.dir = path.parent().map(Path::to_path_buf).unwrap_or_default();
        Ok(cat)
    }

    pub fn parse(text: &str) -> Result<Catalogue> {
        let cat: Catalogue = toml::from_str(text).map_err(|e| err!("{e}"))?;
        cat.check()?;
        Ok(cat)
    }

    /// Every rule a catalogue must keep (the file references are checked by
    /// [`Catalogue::check_files`], which needs the directory).
    pub fn check(&self) -> Result<()> {
        if self.version != 1 {
            return Err(err!("catalogue version {} is not supported (1 is)", self.version));
        }
        let mut ids = HashSet::new();
        let mut dirs = HashSet::new();
        for p in &self.icons {
            if !valid_id(&p.id) || !ids.insert(p.id.clone()) {
                return Err(err!("icon pack id {:?} is not a unique slug", p.id));
            }
            if !p.url.starts_with("https://") {
                return Err(err!("icon pack {}: the url must be https://", p.id));
            }
            if p.sha256.len() != 64 || !p.sha256.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) {
                return Err(err!("icon pack {}: sha256 must be 64 lower-case hex digits", p.id));
            }
            if p.size == 0 || p.installed_size == 0 {
                return Err(err!("icon pack {}: size and installed_size are required", p.id));
            }
            if p.themes.is_empty() {
                return Err(err!("icon pack {}: no themes", p.id));
            }
            for t in &p.themes {
                if !valid_theme_dir(&t.dir) || t.dir == COSMIC_ICONS || !dirs.insert(t.dir.clone()) {
                    return Err(err!("icon pack {}: theme dir {:?} is not a unique plain name", p.id, t.dir));
                }
                if crate::unpack::plain_components(Path::new(&t.path)).is_none_or(|c| c.is_empty()) {
                    return Err(err!("icon pack {}: path {:?} must be relative, without ..", p.id, t.path));
                }
            }
        }
        for c in &self.colours {
            if !valid_id(&c.id) || !ids.insert(c.id.clone()) {
                return Err(err!("colour theme id {:?} is not a unique slug", c.id));
            }
            if crate::unpack::plain_components(Path::new(&c.file)).is_none_or(|c| c.is_empty()) {
                return Err(err!("colour theme {}: file {:?} must be relative, without ..", c.id, c.file));
            }
        }
        for l in &self.looks {
            if !valid_id(&l.id) || !ids.insert(l.id.clone()) {
                return Err(err!("look id {:?} is not a unique slug", l.id));
            }
            if l.icons != COSMIC_ICONS && !dirs.contains(&l.icons) {
                return Err(err!("look {}: icons {:?} is not Cosmic or a catalogue theme", l.id, l.icons));
            }
            if l.colours != "default" && !self.colours.iter().any(|c| c.id == l.colours) {
                return Err(err!("look {}: colours {:?} is not \"default\" or a catalogue colour theme", l.id, l.colours));
            }
        }
        Ok(())
    }

    /// Every colour file parses, and every Look's mode matches its colours.
    pub fn check_files(&self) -> Result<()> {
        for c in &self.colours {
            let _ = crate::colours::parse(&self.colour_text(c)?).map_err(|e| err!("colour theme {}: {e}", c.id))?;
        }
        for l in &self.looks {
            if let Some(c) = self.colours.iter().find(|c| c.id == l.colours) {
                let b = crate::colours::parse(&self.colour_text(c)?)?;
                if b.palette.is_dark() != l.mode.is_dark() {
                    return Err(err!("look {}: mode is {} but {} is not", l.id, l.mode.as_str(), c.id));
                }
            }
        }
        Ok(())
    }

    pub fn colour_text(&self, c: &CatColour) -> Result<String> {
        let p = self.dir.join(&c.file);
        let meta = io_at(std::fs::metadata(&p), "cannot read", &p)?;
        if meta.len() > crate::colours::MAX_RON {
            return Err(err!("{} is larger than a colour theme can be", p.display()));
        }
        io_at(std::fs::read_to_string(&p), "cannot read", &p)
    }

    pub fn pack(&self, id: &str) -> Option<&IconPack> {
        self.icons.iter().find(|p| p.id == id)
    }

    /// The pack that provides the theme directory `dir`.
    pub fn pack_for_theme(&self, dir: &str) -> Option<&IconPack> {
        self.icons.iter().find(|p| p.themes.iter().any(|t| t.dir == dir))
    }

    pub fn colour(&self, id: &str) -> Option<&CatColour> {
        self.colours.iter().find(|c| c.id == id)
    }

    pub fn look(&self, id: &str) -> Option<&CatLook> {
        self.looks.iter().find(|l| l.id == id)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub const GOOD: &str = r#"
version = 1

[[icons]]
id = "ice"
name = "Ice"
version = "1"
summary = "test"
license = "MIT"
homepage = "https://example.org"
url = "https://example.org/ice-1.tar.gz"
sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
size = 100
installed_size = 1000
themes = [
  { dir = "Ice", path = "pack-1/Ice" },
  { dir = "Ice-Dark", path = "pack-1/Ice-Dark" },
]

[[looks]]
id = "frost"
name = "Frost"
summary = "test"
icons = "Ice-Dark"
colours = "default"
mode = "dark"
"#;

    #[test]
    fn a_good_catalogue_loads() {
        let c = Catalogue::parse(GOOD).unwrap();
        assert_eq!(c.pack_for_theme("Ice-Dark").unwrap().id, "ice");
        assert_eq!(c.look("frost").unwrap().mode, Mode::Dark);
    }

    #[test]
    fn every_rule_is_enforced() {
        let bad = [
            ("version = 1", "version = 2", "not supported"),
            ("https://example.org/ice", "http://example.org/ice", "must be https"),
            ("sha256 = \"0000", "sha256 = \"000", "64 lower-case"),
            ("path = \"pack-1/Ice\" }", "path = \"../Ice\" }", "without .."),
            ("{ dir = \"Ice\"", "{ dir = \"../Ice\"", "plain name"),
            ("{ dir = \"Ice\"", "{ dir = \"Ice-Dark\"", "plain name"),
            ("{ dir = \"Ice\"", "{ dir = \"Cosmic\"", "plain name"),
            ("icons = \"Ice-Dark\"", "icons = \"Papirus\"", "not Cosmic"),
            ("colours = \"default\"", "colours = \"nord\"", "not \"default\""),
            ("id = \"frost\"", "id = \"ice\"", "unique slug"),
            ("summary = \"test\"\nlicense", "summary = \"test\"\nhack = 1\nlicense", "unknown field"),
        ];
        for (from, to, why) in bad {
            assert!(GOOD.contains(from), "{from}");
            let e = Catalogue::parse(&GOOD.replacen(from, to, 1)).unwrap_err();
            assert!(e.0.contains(why), "{to}: {e}");
        }
    }
}
