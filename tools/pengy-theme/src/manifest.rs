//! The theme schema — the in-memory data model for a PengyOS theme manifest.
//!
//! This module *defines* the theme surface: the shape of a `theme.toml`
//! manifest and the types that make up a coherent theme. It is the foundation
//! the follow-up subtasks build on:
//!
//! - validation (`validate`) checks a loaded [`Theme`] against the schema
//!   (required tokens, well-formed colors, WCAG contrast for key pairs);
//! - packaging (`pack`) hashes a manifest + its referenced assets into a
//!   deterministic artifact.
//!
//! The manifest is TOML (see the README's `validate`/`pack` examples), so the
//! types derive [`serde::Deserialize`] / [`serde::Serialize`] and are loaded via
//! [`load_manifest`]. The schema is intentionally declarative: it records *what*
//! a theme is, not *how* it is validated or packaged — those are later tasks.
//!
//! ## The schema
//!
//! A [`Theme`] is composed of:
//!
//! - [`Palette`] — the color roles (foreground, background, accent, and the
//!   optional semantic roles used for contrast checks);
//! - [`Fonts`] — the font families and the sizes they are used at;
//! - [`Wallpaper`] — a named, versioned desktop background asset;
//! - [`Keybinding`] — a key chord mapped to an action name;
//! - [`Defaults`] — the desktop defaults the theme installs (session, cursor,
//!   icon theme, ...).
//!
//! Every role is required except where noted; optional pieces are `Option<T>`
//! and default to `None` when absent so a manifest can be partial and validated
//! incrementally.

use std::fmt;
use std::path::Path;
use std::str::FromStr;

use serde::{Deserialize, Serialize};

/// Load a theme manifest from a TOML file on disk.
///
/// Reads the file at `path` and parses it into a [`Theme`]. Returns a
/// [`ManifestError`] describing the failure (missing file, unreadable file, or
/// a schema/parse error) instead of panicking, so the `validate` subcommand can
/// surface a clean diagnostic.
///
/// ```no_run
/// use pengy_theme::manifest::load_manifest;
///
/// let theme = load_manifest("theme.toml")?;
/// println!("loaded theme `{}` v{}", theme.name, theme.version);
/// # Ok::<(), Box<dyn std::error::Error>>(())
/// ```
pub fn load_manifest<P: AsRef<Path>>(path: P) -> Result<Theme, ManifestError> {
    let path = path.as_ref();
    let raw = std::fs::read_to_string(path).map_err(|e| ManifestError::Read {
        path: path.display().to_string(),
        source: e.to_string(),
    })?;
    Theme::from_toml(&raw).map_err(|source| ManifestError::Parse {
        path: path.display().to_string(),
        source,
    })
}

/// A parsed theme manifest, or an error describing why parsing failed.
///
/// `FromStr` lets callers choose between parsing a string in memory
/// ([`Theme::from_toml`]) and loading from disk ([`load_manifest`]) through a
/// single entry point.
impl FromStr for Theme {
    type Err = ManifestError;

    fn from_str(s: &str) -> Result<Theme, ManifestError> {
        Theme::from_toml(s).map_err(|source| ManifestError::Parse {
            path: String::new(),
            source,
        })
    }
}

/// Errors produced while loading/parsing a theme manifest.
///
/// The variants are deliberately coarse so the `validate` subcommand can render
/// them without leaking implementation details.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ManifestError {
    /// The manifest file could not be read from disk.
    Read {
        /// The path that failed to read.
        path: String,
        /// The underlying I/O error message.
        source: String,
    },
    /// The manifest content was not valid TOML or did not match the schema.
    Parse {
        /// The path being parsed (informational when parsing a string).
        path: String,
        /// The underlying parse/schema error message.
        source: String,
    },
}

impl fmt::Display for ManifestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ManifestError::Read { path, source } => {
                write!(f, "could not read manifest `{path}`: {source}")
            }
            ManifestError::Parse { path, source } => {
                write!(f, "invalid manifest `{path}`: {source}")
            }
        }
    }
}

impl std::error::Error for ManifestError {}

/// A complete PengyOS theme manifest — the root of the schema.
///
/// Maps to the top level of a `theme.toml`:
///
/// ```toml
/// name = "Catbee Drift"
/// version = "1.0.0"
/// description = "A warm, low-contrast-friendly palette."
///
/// [colors]
/// foreground = "#f5f5f5"
/// background = "#1a1a1a"
/// accent = "#ffb454"
///
/// [fonts]
/// mono = "DejaVu Sans Mono"
/// sans = "DejaVu Sans"
/// base_size = 14
///
/// [[wallpapers]]
/// name = "drift-mountain"
/// file = "wallpapers/drift-mountain.svg"
///
/// [[keybindings]]
/// key = "Super+Return"
/// action = "terminal"
///
/// [defaults]
/// session = "cosmic"
/// ```
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Theme {
    /// Human-readable theme name (required).
    pub name: String,
    /// Semantic version of the manifest/theme (required).
    pub version: String,
    /// Optional human-readable description.
    #[serde(default)]
    pub description: Option<String>,
    /// The color roles (required).
    pub colors: Palette,
    /// The font families and sizes (required).
    pub fonts: Fonts,
    /// The desktop background assets (optional; may be empty).
    #[serde(default)]
    pub wallpapers: Vec<Wallpaper>,
    /// The key chords mapped to actions (optional; may be empty).
    #[serde(default)]
    pub keybindings: Vec<Keybinding>,
    /// The desktop defaults the theme installs (required).
    pub defaults: Defaults,
}

/// The color roles of a theme.
///
/// `foreground`, `background`, and `accent` are required; the semantic roles
/// (`warning`, `error`, `success`) are optional and are precisely the pairs the
/// follow-up `validate` subtask checks for WCAG contrast.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Palette {
    /// Text/UI foreground color.
    pub foreground: Color,
    /// Background color behind foreground content.
    pub background: Color,
    /// Accent color for selections, links, and focus rings.
    pub accent: Color,
    /// Optional semantic "warning" color.
    #[serde(default)]
    pub warning: Option<Color>,
    /// Optional semantic "error" color.
    #[serde(default)]
    pub error: Option<Color>,
    /// Optional semantic "success" color.
    #[serde(default)]
    pub success: Option<Color>,
}

/// A font family name and, where relevant, the size it is rendered at.
///
/// [`Fonts`] bundles the families a theme ships with and the canonical sizes for
/// the UI so the packager installs a consistent typographic scale.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Fonts {
    /// Monospace family (terminals, code).
    pub mono: String,
    /// Proportional sans family (UI body text).
    pub sans: String,
    /// Optional serif family (headings, prose).
    #[serde(default)]
    pub serif: Option<String>,
    /// Canonical body/UI font size in points.
    pub base_size: u16,
    /// Canonical monospace font size in points (defaults to `base_size` when
    /// omitted).
    #[serde(default)]
    pub mono_size: Option<u16>,
}

/// A single desktop background asset.
///
/// `name` is a stable identifier and `file` is the path to the asset relative
/// to the manifest directory (the reference the `pack` subtask hashes).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Wallpaper {
    /// Stable identifier for the wallpaper.
    pub name: String,
    /// Path to the asset, relative to the manifest directory.
    pub file: String,
    /// Optional human-readable caption.
    #[serde(default)]
    pub caption: Option<String>,
}

/// A key chord mapped to an action.
///
/// `key` is the literal chord as typed by the user (e.g. `"Super+Return"`);
/// `action` is the logical action name the compositor/desktop interprets.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Keybinding {
    /// The literal key chord (e.g. `"Super+Return"`).
    pub key: String,
    /// The logical action the chord triggers (e.g. `"terminal"`).
    pub action: String,
}

/// The desktop defaults a theme installs.
///
/// These are the settings the packager applies system-wide; every field is
/// optional so a theme can override only what it cares about.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct Defaults {
    /// Preferred desktop session (e.g. `"cosmic"`).
    #[serde(default)]
    pub session: Option<String>,
    /// Preferred cursor theme.
    #[serde(default)]
    pub cursor_theme: Option<String>,
    /// Preferred icon theme.
    #[serde(default)]
    pub icon_theme: Option<String>,
    /// Preferred color scheme preference (`"dark"` / `"light"`).
    #[serde(default)]
    pub color_scheme: Option<String>,
}

/// An sRGB color (`#rrggbb`) with hex parsing/serialization.
///
/// `Color` is the single source of truth for how colors are represented in the
/// schema: three 8-bit channels, always rendered as a lowercase `#rrggbb` hex
/// string. The `validate` subtask reuses [`Color`] (and its channels) to compute
/// WCAG contrast ratios, so parsing and channel access live here rather than in
/// a later task.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Color {
    /// Red channel, 0–255.
    pub r: u8,
    /// Green channel, 0–255.
    pub g: u8,
    /// Blue channel, 0–255.
    pub b: u8,
}

/// Why [`Color`] parsing failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ColorError {
    /// The string was not a 6-hex-digit `#rrggbb` color.
    InvalidFormat(String),
}

impl fmt::Display for ColorError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ColorError::InvalidFormat(s) => write!(f, "invalid color `{s}` (expected #rrggbb)"),
        }
    }
}

impl std::error::Error for ColorError {}

impl Color {
    /// Parse a color from a `#rrggbb` (or `rrggbb`) hex string.
    ///
    /// The `#` prefix is optional; exactly six hex digits are required.
    ///
    /// ```
    /// use pengy_theme::manifest::Color;
    /// use std::str::FromStr;
    ///
    /// let c = Color::from_str("#1A2B3C").unwrap();
    /// assert_eq!((c.r, c.g, c.b), (0x1a, 0x2b, 0x3c));
    /// assert_eq!(c.to_string(), "#1a2b3c");
    /// ```
    pub fn from_hex(s: &str) -> Result<Color, ColorError> {
        Color::from_str(s)
    }

    /// Render the color as a lowercase `#rrggbb` hex string.
    pub fn to_hex(&self) -> String {
        format!("#{:02x}{:02x}{:02x}", self.r, self.g, self.b)
    }
}

impl FromStr for Color {
    type Err = ColorError;

    fn from_str(s: &str) -> Result<Color, ColorError> {
        let hex = s.trim().strip_prefix('#').unwrap_or(s);
        if hex.len() != 6 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
            return Err(ColorError::InvalidFormat(s.to_string()));
        }
        let r = u8::from_str_radix(&hex[0..2], 16).map_err(|_| {
            ColorError::InvalidFormat(s.to_string())
        })?;
        let g = u8::from_str_radix(&hex[2..4], 16).map_err(|_| {
            ColorError::InvalidFormat(s.to_string())
        })?;
        let b = u8::from_str_radix(&hex[4..6], 16).map_err(|_| {
            ColorError::InvalidFormat(s.to_string())
        })?;
        Ok(Color { r, g, b })
    }
}

impl fmt::Display for Color {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.to_hex())
    }
}

// Serialize/serialize a `Color` as its `#rrggbb` hex string so the schema
// round-trips through TOML as plain strings.
impl Serialize for Color {
    fn serialize<S: serde::Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(&self.to_hex())
    }
}

impl<'de> Deserialize<'de> for Color {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Color, D::Error> {
        let s = String::deserialize(d)?;
        Color::from_str(&s).map_err(serde::de::Error::custom)
    }
}

impl Theme {
    /// Parse a [`Theme`] from a TOML string.
    pub fn from_toml(s: &str) -> Result<Theme, String> {
        toml::from_str::<Theme>(s).map_err(|e| e.to_string())
    }

    /// Validate a loaded [`Theme`]: required tokens, valid colors, and WCAG
    /// contrast ratios for the key foreground/background pairs.
    ///
    /// Returns a [`validate::ValidationReport`]; see [`validate::validate`] for
    /// the exact rules. A report is valid only when it has no
    /// [`validate::Severity::Error`] issues.
    ///
    /// ```
    /// use pengy_theme::manifest::Theme;
    ///
    /// let toml = r##"
    /// name = "Catbee Drift"
    /// version = "1.0.0"
    /// [colors]
    /// foreground = "#000000"
    /// background = "#ffffff"
    /// accent = "#00aaff"
    /// [fonts]
    /// mono = "Mono"
    /// sans = "Sans"
    /// base_size = 14
    /// [defaults]
    /// "##;
    /// let theme = Theme::from_toml(toml).unwrap();
    /// let report = theme.validate();
    /// assert!(report.valid, "{}", report.render());
    /// ```
    pub fn validate(&self) -> crate::validate::ValidationReport {
        crate::validate::validate(self)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    const EXAMPLE: &str = r##"
name = "Catbee Drift"
version = "1.0.0"
description = "A warm, low-contrast-friendly palette."

[colors]
foreground = "#f5f5f5"
background = "#1a1a1a"
accent = "#ffb454"
warning = "#ffd54a"
error = "#ff5772"
success = "#7cffcb"

[fonts]
mono = "DejaVu Sans Mono"
sans = "DejaVu Sans"
serif = "DejaVu Serif"
base_size = 14
mono_size = 12

[[wallpapers]]
name = "drift-mountain"
file = "wallpapers/drift-mountain.svg"
caption = "Layers of drift."

[[keybindings]]
key = "Super+Return"
action = "terminal"

[defaults]
session = "cosmic"
cursor_theme = "Bibata"
icon_theme = "Papirus"
color_scheme = "dark"
"##;

    #[test]
    fn color_parses_hex_case_insensitively() {
        let c = Color::from_str("#1A2B3C").unwrap();
        assert_eq!((c.r, c.g, c.b), (0x1a, 0x2b, 0x3c));
    }

    #[test]
    fn color_parses_without_hash() {
        let c = Color::from_str("010203").unwrap();
        assert_eq!((c.r, c.g, c.b), (0x01, 0x02, 0x03));
    }

    #[test]
    fn color_roundtrips_through_display() {
        let c = Color::from_str("#1a2b3c").unwrap();
        assert_eq!(c.to_hex(), "#1a2b3c");
        assert_eq!(Color::from_str(&c.to_hex()).unwrap(), c);
    }

    #[test]
    fn color_rejects_bad_formats() {
        for bad in ["", "#fff", "#12345", "green", "#gggggg", "#1234567"] {
            assert_eq!(
                Color::from_str(bad),
                Err(ColorError::InvalidFormat(bad.to_string())),
                "`{bad}` should be invalid"
            );
        }
    }

    #[test]
    fn theme_loads_from_example_toml() {
        let theme: Theme = Theme::from_toml(EXAMPLE).expect("example parses");
        assert_eq!(theme.name, "Catbee Drift");
        assert_eq!(theme.version, "1.0.0");
        assert_eq!(
            theme.description.as_deref(),
            Some("A warm, low-contrast-friendly palette.")
        );
    }

    #[test]
    fn palette_fields_decode() {
        let theme: Theme = Theme::from_toml(EXAMPLE).unwrap();
        assert_eq!(theme.colors.background, Color::from_str("#1a1a1a").unwrap());
        assert_eq!(theme.colors.accent, Color::from_str("#ffb454").unwrap());
        assert_eq!(theme.colors.warning, Some(Color::from_hex("#ffd54a").unwrap()));
        assert_eq!(theme.colors.error, Some(Color::from_hex("#ff5772").unwrap()));
        assert_eq!(theme.colors.success, Some(Color::from_hex("#7cffcb").unwrap()));
    }

    #[test]
    fn fonts_decode_with_sizes() {
        let theme: Theme = Theme::from_toml(EXAMPLE).unwrap();
        assert_eq!(theme.fonts.mono, "DejaVu Sans Mono");
        assert_eq!(theme.fonts.sans, "DejaVu Sans");
        assert_eq!(theme.fonts.serif.as_deref(), Some("DejaVu Serif"));
        assert_eq!(theme.fonts.base_size, 14);
        assert_eq!(theme.fonts.mono_size, Some(12));
    }

    #[test]
    fn wallpapers_and_keybindings_decode() {
        let theme: Theme = Theme::from_toml(EXAMPLE).unwrap();
        assert_eq!(theme.wallpapers.len(), 1);
        assert_eq!(
            theme.wallpapers[0],
            Wallpaper {
                name: "drift-mountain".into(),
                file: "wallpapers/drift-mountain.svg".into(),
                caption: Some("Layers of drift.".into()),
            }
        );
        assert_eq!(theme.keybindings.len(), 1);
        assert_eq!(
            theme.keybindings[0],
            Keybinding {
                key: "Super+Return".into(),
                action: "terminal".into(),
            }
        );
    }

    #[test]
    fn defaults_decode_all_roles() {
        let theme: Theme = Theme::from_toml(EXAMPLE).unwrap();
        assert_eq!(theme.defaults.session.as_deref(), Some("cosmic"));
        assert_eq!(
            theme.defaults.cursor_theme.as_deref(),
            Some("Bibata")
        );
        assert_eq!(
            theme.defaults.icon_theme.as_deref(),
            Some("Papirus")
        );
        assert_eq!(
            theme.defaults.color_scheme.as_deref(),
            Some("dark")
        );
    }

    #[test]
    fn minimal_theme_defaults_optional_fields() {
        // A manifest with only the required fields must still parse; optional
        // fields default to None / empty.
        let minimal = r##"
name = "Minimal"
version = "0.1.0"

[colors]
foreground = "#ffffff"
background = "#000000"
accent = "#00aaff"

[fonts]
mono = "Mono"
sans = "Sans"
base_size = 14

[defaults]
"##;
        let theme: Theme = Theme::from_toml(minimal).unwrap();
        assert_eq!(theme.description, None);
        assert!(theme.wallpapers.is_empty());
        assert!(theme.keybindings.is_empty());
        assert_eq!(theme.fonts.serif, None);
        assert_eq!(theme.fonts.mono_size, None);
        assert_eq!(theme.colors.warning, None);
        assert_eq!(theme.defaults, Defaults::default());
    }

    #[test]
    fn theme_serializes_back_to_toml_strings() {
        // The schema must round-trip: colors serialize as #rrggbb strings.
        let theme: Theme = Theme::from_toml(EXAMPLE).unwrap();
        let toml_out = toml::to_string_pretty(&theme).expect("serializes");
        assert!(toml_out.contains("foreground = \"#f5f5f5\""));
        assert!(toml_out.contains("accent = \"#ffb454\""));
        // And the re-parsed theme is equal to the original.
        let reparsed: Theme = Theme::from_toml(&toml_out).unwrap();
        assert_eq!(theme, reparsed);
    }

    #[test]
    fn load_manifest_reads_file() {
        // Round-trip through disk to exercise the read path.
        let dir = std::env::temp_dir().join(format!("pengy-theme-schema-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("theme.toml");
        std::fs::write(&path, EXAMPLE).unwrap();
        let theme = load_manifest(&path).expect("loads from disk");
        assert_eq!(theme.name, "Catbee Drift");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn load_manifest_missing_file_errors() {
        let err = load_manifest("/nonexistent/pengy-theme-nope.toml").unwrap_err();
        assert!(matches!(err, ManifestError::Read { .. }));
    }

    #[test]
    fn load_manifest_invalid_toml_errors() {
        let dir = std::env::temp_dir().join(format!("pengy-schema-bad-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("bad.toml");
        std::fs::write(&path, "this is = = not toml").unwrap();
        let err = load_manifest(&path).unwrap_err();
        assert!(matches!(err, ManifestError::Parse { .. }));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn schema_rejects_missing_required_field() {
        // `fonts` is required: a manifest without it must fail to parse.
        let missing_fonts = r##"
name = "No Fonts"
version = "0.1.0"
[colors]
foreground = "#ffffff"
background = "#000000"
accent = "#00aaff"
[defaults]
"##;
        assert!(
            Theme::from_toml(missing_fonts).is_err(),
            "missing required [fonts] should fail to parse"
        );
    }

    #[test]
    fn schema_rejects_missing_top_level_name() {
        let no_name = r##"
version = "0.1.0"
[colors]
foreground = "#ffffff"
background = "#000000"
accent = "#00aaff"
[fonts]
mono = "Mono"
sans = "Sans"
base_size = 14
[defaults]
"##;
        let err = Theme::from_toml(no_name).expect_err("should fail");
        assert!(
            err.to_lowercase().contains("name"),
            "error should mention the missing `name` field: {err}"
        );
    }

    #[test]
    fn schema_rejects_malformed_color() {
        // A color that is not #rrggbb must fail to parse (Color deserialization).
        let bad_color = r##"
name = "Bad Color"
version = "0.1.0"
[colors]
foreground = "not-a-color"
background = "#000000"
accent = "#00aaff"
[fonts]
mono = "Mono"
sans = "Sans"
base_size = 14
[defaults]
"##;
        assert!(
            Theme::from_toml(bad_color).is_err(),
            "malformed color should fail to parse"
        );
    }
}
