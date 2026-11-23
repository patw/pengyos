//! The COSMIC config this tool reads and writes, undo snapshots, and reset.
//!
//! Only the keys COSMIC Settings itself writes for the same actions:
//!
//! * `com.system76.CosmicTk` `icon_theme` (Settings → Appearance → icons);
//! * `com.system76.CosmicTheme.Mode` `is_dark`;
//! * `com.system76.CosmicTheme.{Dark,Light}.Builder` and
//!   `com.system76.CosmicTheme.{Dark,Light}`, written by the same
//!   `write_entry` calls as Settings' *Import* (`cosmic-settings`
//!   `appearance/commands.rs`), so the desktop cannot tell the difference.
//!
//! COSMIC watches these files and redraws at once; nothing needs a restart.

use std::fs;
use std::path::{Path, PathBuf};

use cosmic_config::{Config, ConfigSet, CosmicConfigEntry};
use cosmic_theme::{
    Theme, ThemeBuilder, DARK_THEME_BUILDER_ID, DARK_THEME_ID, LIGHT_THEME_BUILDER_ID, LIGHT_THEME_ID,
    THEME_MODE_ID,
};
use serde::{Deserialize, Serialize};

use crate::catalogue::{Mode, COSMIC_ICONS};
use crate::{err, io_at, write_atomic, Paths, Result};

pub const TK_ID: &str = "com.system76.CosmicTk";
pub const TK_VERSION: u64 = 1;
pub const MODE_VERSION: u64 = 1;

/// The config ids a colour theme writes.
pub const THEME_IDS: [&str; 4] = [DARK_THEME_BUILDER_ID, DARK_THEME_ID, LIGHT_THEME_BUILDER_ID, LIGHT_THEME_ID];

/// How many undo steps are kept.
pub const UNDO_KEEP: usize = 20;

fn config(paths: &Paths, id: &str, version: u64) -> Result<Config> {
    Ok(Config::with_custom_path(id, version, paths.config.clone())?)
}

/// `<id>/v<version>/<key>`, relative to `~/.config/cosmic` (and to
/// `<system data>/cosmic`).
fn key_rel(id: &str, version: u64, key: &str) -> PathBuf {
    Path::new(id).join(format!("v{version}")).join(key)
}

/// A key's value as COSMIC would see it: the user's, else the system default.
fn read_key<T: serde::de::DeserializeOwned>(paths: &Paths, id: &str, version: u64, key: &str) -> Option<T> {
    let rel = key_rel(id, version, key);
    std::iter::once(paths.cosmic())
        .chain(paths.system_data.iter().map(|d| d.join("cosmic")))
        .find_map(|base| fs::read_to_string(base.join(&rel)).ok())
        .and_then(|s| ron::from_str(&s).ok())
}

pub fn icon_theme(paths: &Paths) -> String {
    read_key(paths, TK_ID, TK_VERSION, "icon_theme").unwrap_or_else(|| COSMIC_ICONS.to_string())
}

/// The icon theme the system ships for the session (the image's CosmicTk
/// default: Papirus-Dark on PengyOS Desktop), else Cosmic.
pub fn system_icon_theme(paths: &Paths) -> String {
    let rel = key_rel(TK_ID, TK_VERSION, "icon_theme");
    paths
        .system_data
        .iter()
        .find_map(|d| fs::read_to_string(d.join("cosmic").join(&rel)).ok())
        .and_then(|s| ron::from_str(&s).ok())
        .unwrap_or_else(|| COSMIC_ICONS.to_string())
}

/// Back to the system's icon theme, in the variant for the current mode when
/// the catalogue pairs it (Papirus-Light in light mode). Written as a value,
/// not by deleting the user's key, so running apps see the change.
pub fn default_icons(paths: &Paths, cat: Option<&crate::catalogue::Catalogue>) -> Result<String> {
    set_icon_theme(paths, &system_icon_theme(paths))?;
    if let Some(cat) = cat {
        crate::follow::once(paths, cat)?;
    }
    Ok(icon_theme(paths))
}

pub fn set_icon_theme(paths: &Paths, name: &str) -> Result<()> {
    config(paths, TK_ID, TK_VERSION)?.set("icon_theme", name.to_string())?;
    Ok(())
}

pub fn mode(paths: &Paths) -> Mode {
    Mode::from_dark(
        read_key(paths, THEME_MODE_ID, MODE_VERSION, "is_dark")
            .unwrap_or(cosmic_theme::ThemeMode::default().is_dark),
    )
}

pub fn set_mode(paths: &Paths, mode: Mode) -> Result<()> {
    config(paths, THEME_MODE_ID, MODE_VERSION)?.set("is_dark", mode.is_dark())?;
    Ok(())
}

fn builder_id(mode: Mode) -> &'static str {
    if mode.is_dark() {
        DARK_THEME_BUILDER_ID
    } else {
        LIGHT_THEME_BUILDER_ID
    }
}

fn theme_id(mode: Mode) -> &'static str {
    if mode.is_dark() {
        DARK_THEME_ID
    } else {
        LIGHT_THEME_ID
    }
}

/// Apply a colour theme exactly as COSMIC Settings' *Import* does: switch to the
/// palette's mode, write the builder, write the theme built from it.
pub fn apply_builder(paths: &Paths, builder: &ThemeBuilder) -> Result<Mode> {
    let mode = Mode::from_dark(builder.palette.is_dark());
    if self::mode(paths) != mode {
        set_mode(paths, mode)?;
    }
    builder
        .write_entry(&config(paths, builder_id(mode), ThemeBuilder::VERSION)?)
        .map_err(|e| err!("cannot write the colour theme: {e:?}"))?;
    builder
        .clone()
        .build()
        .write_entry(&config(paths, theme_id(mode), Theme::VERSION)?)
        .map_err(|e| err!("cannot write the colour theme: {e:?}"))?;
    Ok(mode)
}

/// The first system copy of `<id>` (COSMIC's shipped defaults), if any.
fn system_dir(paths: &Paths, id: &str) -> Option<PathBuf> {
    paths.system_data.iter().map(|d| d.join("cosmic").join(id)).find(|p| p.is_dir())
}

/// Put COSMIC's default colours back for `mode` (the shipped defaults when the
/// image has them, else libcosmic's own), leaving the icon theme alone.
pub fn default_colours(paths: &Paths, mode: Mode) -> Result<()> {
    if self::mode(paths) != mode {
        set_mode(paths, mode)?;
    }
    reset_ids(paths, &[builder_id(mode), theme_id(mode)])
}

fn reset_ids(paths: &Paths, ids: &[&str]) -> Result<()> {
    for id in ids {
        let user = paths.cosmic().join(id);
        match system_dir(paths, id) {
            Some(sys) => sync_tree(&sys, &user)?,
            None => {
                let dark = *id == DARK_THEME_BUILDER_ID || *id == DARK_THEME_ID;
                let b = if dark { ThemeBuilder::dark() } else { ThemeBuilder::light() };
                if *id == DARK_THEME_BUILDER_ID || *id == LIGHT_THEME_BUILDER_ID {
                    b.write_entry(&config(paths, id, ThemeBuilder::VERSION)?)
                } else {
                    b.build().write_entry(&config(paths, id, Theme::VERSION)?)
                }
                .map_err(|e| err!("cannot write the default colours: {e:?}"))?;
            }
        }
    }
    Ok(())
}

/// Reset: the system's icon theme and colours (on PengyOS, Glacier and
/// Papirus; COSMIC's own where nothing is shipped), dark and light. Dark or
/// light stays as the user has it (as Settings' *Reset to default*).
pub fn reset(paths: &Paths, cat: Option<&crate::catalogue::Catalogue>) -> Result<()> {
    snapshot(paths, "reset")?;
    reset_ids(paths, &THEME_IDS)?;
    crate::colours::record_applied(paths, None)?;
    default_icons(paths, cat)?;
    Ok(())
}

/// The builder COSMIC is using for `mode`, as Settings would export it.
pub fn current_builder(paths: &Paths, mode: Mode) -> Result<ThemeBuilder> {
    let id = builder_id(mode);
    // COSMIC reads each key from the user's dir, else the system's: merge them
    // into a scratch dir and read that.
    let scratch = paths.state().join(format!(".read-{}", std::process::id()));
    let _ = fs::remove_dir_all(&scratch);
    let merged = scratch.join("cosmic").join(id);
    if let Some(sys) = system_dir(paths, id) {
        copy_tree(&sys, &merged)?;
    }
    let user = paths.cosmic().join(id);
    if user.is_dir() {
        copy_tree(&user, &merged)?;
    }
    let vdir = merged.join(format!("v{}", ThemeBuilder::VERSION));
    let out = if vdir.is_dir() {
        let cfg = Config::with_custom_path(id, ThemeBuilder::VERSION, scratch.clone())?;
        match ThemeBuilder::get_entry(&cfg) {
            Ok(b) => b,
            Err((_, partial)) => partial,
        }
    } else if mode.is_dark() {
        ThemeBuilder::dark()
    } else {
        ThemeBuilder::light()
    };
    let _ = fs::remove_dir_all(&scratch);
    Ok(out)
}

/// A digest of the builder COSMIC uses for `mode` (user keys over system keys),
/// to tell whether it changed since this tool applied it.
pub fn builder_digest(paths: &Paths, mode: Mode) -> String {
    let b = current_builder(paths, mode).ok();
    crate::sha256_hex(ron::to_string(&b).unwrap_or_default().as_bytes())
}

// ---- the image's defaults -------------------------------------------------------

/// Write COSMIC's system defaults for the catalogue's `[defaults]` into `out`
/// (laid over `/usr/share/cosmic` by the Desktop's post-build): each mode's
/// builder and the theme built from it, the default mode, and the default
/// mode's icon theme. The same `write_entry` calls as everything else here, so
/// the files are exactly what Settings would write for those choices.
pub fn write_system_defaults(cat: &crate::catalogue::Catalogue, out: &Path) -> Result<()> {
    let d = cat.defaults.as_ref().ok_or_else(|| err!("the catalogue has no [defaults]"))?;
    let scratch = out.join(".pengyos-themes-defaults");
    let _ = fs::remove_dir_all(&scratch);
    let tmp = Paths::under(&scratch);
    let mut icons = None;
    for id in [&d.dark, &d.light] {
        let look = cat.look(id).ok_or_else(|| err!("no Look {id}"))?;
        let c = cat.colour(&look.colours).ok_or_else(|| err!("Look {id} must carry its own colours"))?;
        let b = crate::colours::parse(&cat.colour_text(c)?)?;
        let mode = Mode::from_dark(b.palette.is_dark());
        b.write_entry(&config(&tmp, builder_id(mode), ThemeBuilder::VERSION)?)
            .map_err(|e| err!("cannot write the defaults: {e:?}"))?;
        b.build()
            .write_entry(&config(&tmp, theme_id(mode), Theme::VERSION)?)
            .map_err(|e| err!("cannot write the defaults: {e:?}"))?;
        if mode == d.mode {
            icons = Some(look.icons.clone());
        }
    }
    set_mode(&tmp, d.mode)?;
    set_icon_theme(&tmp, icons.as_deref().unwrap_or(COSMIC_ICONS))?;
    for e in io_at(fs::read_dir(tmp.cosmic()), "cannot read", &tmp.cosmic())? {
        let e = e?;
        sync_tree(&e.path(), &out.join(e.file_name()))?;
    }
    io_at(fs::remove_dir_all(&scratch), "cannot remove", &scratch)
}

// ---- undo -------------------------------------------------------------------

/// What a snapshot holds: these keys and these whole config ids, as they were
/// (present or absent).
fn snapshot_set() -> Vec<(PathBuf, bool)> {
    let mut v = vec![
        (key_rel(TK_ID, TK_VERSION, "icon_theme"), false),
        (key_rel(THEME_MODE_ID, MODE_VERSION, "is_dark"), false),
    ];
    v.extend(THEME_IDS.iter().map(|id| (PathBuf::from(id), true)));
    v
}

#[derive(Debug, Serialize, Deserialize)]
struct Manifest {
    label: String,
    present: Vec<PathBuf>,
}

fn undo_dir(paths: &Paths) -> PathBuf {
    paths.state().join("undo")
}

/// Snapshots, oldest first.
fn snapshots(paths: &Paths) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = fs::read_dir(undo_dir(paths))
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.join("manifest.json").is_file())
                .collect()
        })
        .unwrap_or_default();
    v.sort();
    v
}

pub fn undo_depth(paths: &Paths) -> usize {
    snapshots(paths).len()
}

/// Save the current state of every key this tool may change, before changing it.
pub fn snapshot(paths: &Paths, label: &str) -> Result<()> {
    let base = undo_dir(paths);
    io_at(fs::create_dir_all(&base), "cannot create", &base)?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let mut n = 0;
    let dir = loop {
        let d = base.join(format!("{now:024}-{n:03}"));
        if !d.exists() {
            break d;
        }
        n += 1;
    };
    let tmp = base.join(format!(".new-{}", std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    let mut present = Vec::new();
    for (rel, _) in snapshot_set() {
        let src = paths.cosmic().join(&rel);
        if src.is_dir() {
            copy_tree(&src, &tmp.join("files").join(&rel))?;
            present.push(rel);
        } else if src.is_file() {
            let dst = tmp.join("files").join(&rel);
            fs::create_dir_all(dst.parent().unwrap())?;
            io_at(fs::copy(&src, &dst), "cannot copy", &src)?;
            present.push(rel);
        }
    }
    let m = Manifest { label: label.to_string(), present };
    fs::create_dir_all(&tmp)?;
    write_atomic(&tmp.join("manifest.json"), serde_json::to_string_pretty(&m).unwrap().as_bytes())?;
    io_at(fs::rename(&tmp, &dir), "cannot save the undo step", &dir)?;
    let all = snapshots(paths);
    for old in all.iter().take(all.len().saturating_sub(UNDO_KEEP)) {
        let _ = fs::remove_dir_all(old);
    }
    Ok(())
}

/// Put back the newest snapshot, byte for byte, and drop it. Returns its label,
/// or `None` when there is nothing to undo.
pub fn undo(paths: &Paths) -> Result<Option<String>> {
    let Some(snap) = snapshots(paths).pop() else {
        return Ok(None);
    };
    let m: Manifest = serde_json::from_slice(&fs::read(snap.join("manifest.json"))?)
        .map_err(|e| err!("the undo step {} is damaged: {e}", snap.display()))?;
    for (rel, is_dir) in snapshot_set() {
        let dst = paths.cosmic().join(&rel);
        let src = snap.join("files").join(&rel);
        if m.present.contains(&rel) {
            if is_dir {
                sync_tree(&src, &dst)?;
            } else {
                write_atomic(&dst, &io_at(fs::read(&src), "cannot read", &src)?)?;
            }
        } else if is_dir {
            if dst.exists() {
                io_at(fs::remove_dir_all(&dst), "cannot remove", &dst)?;
            }
        } else if dst.exists() {
            io_at(fs::remove_file(&dst), "cannot remove", &dst)?;
        }
    }
    io_at(fs::remove_dir_all(&snap), "cannot remove", &snap)?;
    Ok(Some(m.label))
}

// ---- trees --------------------------------------------------------------------

/// Copy the regular files and directories of `src` into `dst` (merging).
pub(crate) fn copy_tree(src: &Path, dst: &Path) -> Result<()> {
    io_at(fs::create_dir_all(dst), "cannot create", dst)?;
    for e in io_at(fs::read_dir(src), "cannot read", src)? {
        let e = e?;
        let (s, d) = (e.path(), dst.join(e.file_name()));
        let t = e.file_type()?;
        if t.is_dir() {
            copy_tree(&s, &d)?;
        } else if t.is_file() || (t.is_symlink() && s.is_file()) {
            io_at(fs::copy(&s, &d), "cannot copy", &s)?;
        }
    }
    Ok(())
}

/// Make `dst` hold exactly what `src` holds, rewriting only the files that
/// differ (so COSMIC sees one change per key that changed, not a delete and
/// re-create of everything).
pub(crate) fn sync_tree(src: &Path, dst: &Path) -> Result<()> {
    io_at(fs::create_dir_all(dst), "cannot create", dst)?;
    let mut keep = std::collections::HashSet::new();
    for e in io_at(fs::read_dir(src), "cannot read", src)? {
        let e = e?;
        let (s, d) = (e.path(), dst.join(e.file_name()));
        keep.insert(e.file_name());
        if s.is_dir() {
            if d.exists() && !d.is_dir() {
                fs::remove_file(&d)?;
            }
            sync_tree(&s, &d)?;
        } else {
            let want = io_at(fs::read(&s), "cannot read", &s)?;
            if d.is_dir() {
                fs::remove_dir_all(&d)?;
            }
            if fs::read(&d).ok().as_deref() != Some(&want[..]) {
                write_atomic(&d, &want)?;
            }
        }
    }
    for e in io_at(fs::read_dir(dst), "cannot read", dst)? {
        let e = e?;
        if !keep.contains(&e.file_name()) {
            let p = e.path();
            if e.file_type()?.is_dir() {
                fs::remove_dir_all(&p)?;
            } else {
                fs::remove_file(&p)?;
            }
        }
    }
    Ok(())
}

/// Every file under `root` with its bytes, sorted (tests compare trees with it).
pub fn tree(root: &Path) -> Vec<(PathBuf, Vec<u8>)> {
    fn walk(root: &Path, at: &Path, out: &mut Vec<(PathBuf, Vec<u8>)>) {
        let Ok(rd) = fs::read_dir(at) else { return };
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                walk(root, &p, out);
            } else if let Ok(b) = fs::read(&p) {
                out.push((p.strip_prefix(root).unwrap().to_path_buf(), b));
            }
        }
    }
    let mut out = Vec::new();
    walk(root, root, &mut out);
    out.sort();
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn home() -> (tempfile::TempDir, Paths) {
        let d = tempfile::tempdir().unwrap();
        let p = Paths::under(d.path());
        (d, p)
    }

    #[test]
    fn icon_theme_defaults_to_cosmic_and_is_written_where_settings_writes_it() {
        let (_d, p) = home();
        assert_eq!(icon_theme(&p), "Cosmic");
        set_icon_theme(&p, "Papirus-Dark").unwrap();
        let f = p.config.join("cosmic/com.system76.CosmicTk/v1/icon_theme");
        assert_eq!(fs::read_to_string(f).unwrap(), "\"Papirus-Dark\"");
        assert_eq!(icon_theme(&p), "Papirus-Dark");
    }

    #[test]
    fn mode_falls_back_to_the_system_default() {
        let (_d, p) = home();
        assert_eq!(mode(&p), Mode::Dark, "libcosmic's default");
        let sys = p.system_data[0].join("cosmic/com.system76.CosmicTheme.Mode/v1");
        fs::create_dir_all(&sys).unwrap();
        fs::write(sys.join("is_dark"), "false").unwrap();
        assert_eq!(mode(&p), Mode::Light);
        set_mode(&p, Mode::Dark).unwrap();
        assert_eq!(mode(&p), Mode::Dark);
    }

    #[test]
    fn apply_writes_what_settings_import_writes() {
        // Settings' Import: dark_mode(palette.is_dark()), then
        // builder.write_entry(<mode>.Builder) and builder.build().write_entry(<mode>).
        let (_d, p) = home();
        let b = ThemeBuilder::light().accent(cosmic_theme::palette::Srgb::new(0.8, 0.2, 0.3));
        assert_eq!(apply_builder(&p, &b).unwrap(), Mode::Light);

        let (_e, q) = home();
        Config::with_custom_path(THEME_MODE_ID, 1, q.config.clone()).unwrap().set("is_dark", false).unwrap();
        b.write_entry(&Config::with_custom_path(LIGHT_THEME_BUILDER_ID, ThemeBuilder::VERSION, q.config.clone()).unwrap())
            .unwrap();
        b.clone()
            .build()
            .write_entry(&Config::with_custom_path(LIGHT_THEME_ID, Theme::VERSION, q.config.clone()).unwrap())
            .unwrap();
        let (ours, theirs) = (tree(&p.cosmic()), tree(&q.cosmic()));
        assert!(!ours.is_empty());
        assert_eq!(ours, theirs, "key by key, byte by byte");
        assert!(!p.cosmic().join(DARK_THEME_ID).exists(), "the other mode is untouched");
    }

    #[test]
    fn undo_restores_byte_for_byte_including_absence() {
        let (_d, p) = home();
        // A user with their own icon theme, a light builder with an odd key, no dark one.
        set_icon_theme(&p, "Breeze").unwrap();
        let lb = p.cosmic().join(LIGHT_THEME_BUILDER_ID).join("v2");
        fs::create_dir_all(&lb).unwrap();
        fs::write(lb.join("accent"), "Some((red: 1.0, green: 0.0, blue: 0.0))  # by hand").unwrap();
        fs::write(lb.join("future_key"), "42").unwrap();
        let before = tree(&p.cosmic());

        snapshot(&p, "test").unwrap();
        set_icon_theme(&p, "Papirus").unwrap();
        apply_builder(&p, &ThemeBuilder::light()).unwrap();
        apply_builder(&p, &ThemeBuilder::dark()).unwrap();
        assert_ne!(tree(&p.cosmic()), before);

        assert_eq!(undo(&p).unwrap().as_deref(), Some("test"));
        assert_eq!(tree(&p.cosmic()), before);
        assert!(!p.cosmic().join(DARK_THEME_ID).exists());
        assert_eq!(undo(&p).unwrap(), None, "nothing left");
    }

    #[test]
    fn undo_is_a_stack_and_keeps_twenty() {
        let (_d, p) = home();
        for i in 0..25 {
            snapshot(&p, &format!("s{i}")).unwrap();
            set_icon_theme(&p, &format!("T{i}")).unwrap();
        }
        assert_eq!(undo_depth(&p), UNDO_KEEP);
        assert_eq!(undo(&p).unwrap().as_deref(), Some("s24"));
        assert_eq!(icon_theme(&p), "T23");
        assert_eq!(undo(&p).unwrap().as_deref(), Some("s23"));
        assert_eq!(icon_theme(&p), "T22");
    }

    #[test]
    fn reset_mirrors_the_shipped_defaults_and_keeps_the_mode() {
        let (_d, p) = home();
        for id in THEME_IDS {
            let s = p.system_data[0].join("cosmic").join(id).join("v2");
            fs::create_dir_all(&s).unwrap();
            fs::write(s.join("accent"), format!("shipped {id}")).unwrap();
        }
        set_mode(&p, Mode::Light).unwrap();
        set_icon_theme(&p, "Papirus").unwrap();
        apply_builder(&p, &ThemeBuilder::light()).unwrap();
        reset(&p, None).unwrap();
        assert_eq!(icon_theme(&p), "Cosmic", "no shipped icon theme: Cosmic");
        assert_eq!(mode(&p), Mode::Light);
        for id in THEME_IDS {
            assert_eq!(
                tree(&p.cosmic().join(id)),
                vec![(PathBuf::from("v2/accent"), format!("shipped {id}").into_bytes())]
            );
        }
        undo(&p).unwrap();
        assert_eq!(icon_theme(&p), "Papirus", "reset is undoable");
    }

    #[test]
    fn reset_without_shipped_defaults_writes_libcosmics() {
        let (_d, p) = home();
        reset(&p, None).unwrap();
        assert_eq!(current_builder(&p, Mode::Light).unwrap(), ThemeBuilder::light());
        assert_eq!(current_builder(&p, Mode::Dark).unwrap(), ThemeBuilder::dark());
    }

    #[test]
    fn current_builder_reads_back_what_was_applied() {
        let (_d, p) = home();
        // COSMIC keeps colours as 8-bit hex, so compare what reaches the config:
        // writing back the builder that was read changes no builder key. (The
        // built theme's derived shades may move by one step, as they do when
        // Settings re-applies the same builder.)
        let b = ThemeBuilder::dark().accent(cosmic_theme::palette::Srgb::new(0.1, 0.5, 0.9));
        apply_builder(&p, &b).unwrap();
        let keys = p.cosmic().join(DARK_THEME_BUILDER_ID);
        let before = tree(&keys);
        let back = current_builder(&p, Mode::Dark).unwrap();
        assert!(back.accent.is_some_and(|a| (a.blue - 0.9).abs() < 1.0 / 255.0));
        apply_builder(&p, &back).unwrap();
        assert_eq!(tree(&keys), before);
        let d = builder_digest(&p, Mode::Dark);
        apply_builder(&p, &ThemeBuilder::dark()).unwrap();
        assert_ne!(builder_digest(&p, Mode::Dark), d);
    }
}
