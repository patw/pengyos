//! pengyos-themes: icon themes, colour themes and Looks for COSMIC, per user.
//!
//! Everything here runs as the user and writes only under their own
//! `~/.local/share` (themes, the colour library, Looks, undo snapshots) and
//! `~/.config/cosmic` (the keys COSMIC Settings itself writes). See `spec.md`.
//!
//! * [`catalogue`]: the curated list shipped in the image (icon packs with a
//!   pinned sha256, curated colour themes, curated Looks).
//! * [`icons`]: install, use and remove icon themes.
//! * [`colours`]: colour themes from cosmic-themes.org or a file; the library.
//! * [`looks`]: an icon theme and a colour theme, applied together.
//! * [`desktop`]: the COSMIC config keys, undo snapshots and reset.

#[cfg(feature = "cosmic")]
pub mod app;
pub mod catalogue;
pub mod colours;
pub mod desktop;
pub mod fetch;
pub mod follow;
pub mod icons;
pub mod looks;
pub mod unpack;

use std::fmt;
use std::path::{Path, PathBuf};

/// Everything that can go wrong is reported to the user as one sentence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error(pub String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error(e.to_string())
    }
}

impl From<cosmic_config::Error> for Error {
    fn from(e: cosmic_config::Error) -> Self {
        Error(format!("COSMIC config: {e:?}"))
    }
}

pub type Result<T> = std::result::Result<T, Error>;

/// Shorthand for an [`Error`] built with `format!`.
#[macro_export]
macro_rules! err {
    ($($t:tt)*) => { $crate::Error(format!($($t)*)) };
}

/// Attach the path to an I/O error.
pub(crate) fn io_at<T>(r: std::io::Result<T>, what: &str, path: &Path) -> Result<T> {
    r.map_err(|e| err!("{what} {}: {e}", path.display()))
}

/// Where everything lives. Built from the environment by [`Paths::from_env`]; the
/// tests build one over a temporary directory.
#[derive(Debug, Clone)]
pub struct Paths {
    /// `$XDG_CONFIG_HOME` (`~/.config`): COSMIC's config is under `cosmic/`.
    pub config: PathBuf,
    /// `$XDG_DATA_HOME` (`~/.local/share`): `icons/` and `pengyos-themes/`.
    pub data: PathBuf,
    /// `$XDG_CACHE_HOME` (`~/.cache`): downloads in progress.
    pub cache: PathBuf,
    /// `$XDG_DATA_DIRS` (`/usr/local/share:/usr/share`): system icon themes and
    /// COSMIC's shipped defaults (`cosmic/<id>/v<N>`).
    pub system_data: Vec<PathBuf>,
    /// The catalogue file (`/usr/share/pengyos/themes/catalogue.toml`).
    pub catalogue: PathBuf,
}

/// The catalogue the image ships.
pub const CATALOGUE: &str = "/usr/share/pengyos/themes/catalogue.toml";

impl Paths {
    pub fn from_env() -> Result<Paths> {
        let home = std::env::var_os("HOME")
            .filter(|h| !h.is_empty())
            .map(PathBuf::from)
            .ok_or_else(|| err!("HOME is not set"))?;
        let xdg = |var: &str, rel: &str| {
            std::env::var_os(var)
                .map(PathBuf::from)
                .filter(|p| p.is_absolute())
                .unwrap_or_else(|| home.join(rel))
        };
        let system_data = std::env::var("XDG_DATA_DIRS")
            .ok()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "/usr/local/share:/usr/share".into())
            .split(':')
            .filter(|s| s.starts_with('/'))
            .map(PathBuf::from)
            .collect();
        let catalogue = std::env::var_os("PENGYOS_THEMES_CATALOGUE")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(CATALOGUE));
        Ok(Paths {
            config: xdg("XDG_CONFIG_HOME", ".config"),
            data: xdg("XDG_DATA_HOME", ".local/share"),
            cache: xdg("XDG_CACHE_HOME", ".cache"),
            system_data,
            catalogue,
        })
    }

    /// A layout entirely under `root` (tests).
    pub fn under(root: &Path) -> Paths {
        Paths {
            config: root.join("config"),
            data: root.join("data"),
            cache: root.join("cache"),
            system_data: vec![root.join("system")],
            catalogue: root.join("catalogue/catalogue.toml"),
        }
    }

    /// `~/.local/share/icons`: where installed icon themes go.
    pub fn icons(&self) -> PathBuf {
        self.data.join("icons")
    }

    /// `~/.local/share/pengyos-themes`: the library, Looks, undo, state.
    pub fn state(&self) -> PathBuf {
        self.data.join("pengyos-themes")
    }

    /// `~/.config/cosmic`.
    pub fn cosmic(&self) -> PathBuf {
        self.config.join("cosmic")
    }
}

/// A lower-case slug for ids made from names: `"Nord Dark!"` -> `"nord-dark"`.
pub fn slug(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
        } else if !out.ends_with('-') && !out.is_empty() {
            out.push('-');
        }
    }
    out.truncate(48);
    while out.ends_with('-') {
        out.pop();
    }
    out
}

/// An id is a non-empty slug: lower-case letters, digits and single dashes.
pub fn valid_id(s: &str) -> bool {
    !s.is_empty() && s.len() <= 64 && slug(s) == s
}

/// Hex sha256 of a byte slice.
pub fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    hex(&Sha256::digest(bytes))
}

pub(crate) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Write `bytes` to `path` through a temporary file and a rename, so a reader
/// never sees half a file.
pub(crate) fn write_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let dir = path.parent().ok_or_else(|| err!("{} has no parent", path.display()))?;
    io_at(std::fs::create_dir_all(dir), "cannot create", dir)?;
    let tmp = dir.join(format!(
        ".{}.tmp-{}",
        path.file_name().map(|n| n.to_string_lossy()).unwrap_or_default(),
        std::process::id()
    ));
    io_at(std::fs::write(&tmp, bytes), "cannot write", &tmp)?;
    io_at(std::fs::rename(&tmp, path), "cannot write", path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugs() {
        assert_eq!(slug("Nord Dark!"), "nord-dark");
        assert_eq!(slug("  Catppuccin -- Mocha  "), "catppuccin-mocha");
        assert_eq!(slug("ÆØÅ"), "");
        assert!(valid_id("papirus"));
        assert!(valid_id("site-123"));
        assert!(!valid_id("Papirus"));
        assert!(!valid_id("a--b"));
        assert!(!valid_id("../x"));
        assert!(!valid_id(""));
    }

    #[test]
    fn sha() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
