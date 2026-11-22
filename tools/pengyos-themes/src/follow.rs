//! Icons that follow dark and light.
//!
//! COSMIC has one icon theme setting, whatever the mode. A pack with dark and
//! light variants (Papirus-Dark / Papirus-Light) then shows the wrong one as
//! soon as the desktop changes mode (Settings' toggle, or the sunset switch).
//! `pengyos-themes follow` runs in each session (from `/etc/xdg/autostart`):
//! whenever `com.system76.CosmicTheme.Mode` changes, if the icon theme is one
//! half of a catalogue pair, it switches to the other half.
//!
//! It only ever swaps within a declared pair, and only when the mode changes
//! (or at login): an icon theme the user picks by hand stays until the next
//! mode change, and any other theme is never touched. The swap is not an undo
//! step: it follows a change the user already made.

use std::sync::mpsc;
use std::time::Duration;

use cosmic_config::Config;
use cosmic_theme::THEME_MODE_ID;

use crate::catalogue::Catalogue;
use crate::{desktop, icons, Paths, Result};

/// If the icon theme is half of a pair and the mode wants the other half (and
/// it is installed), switch. Returns the theme switched to.
pub fn once(paths: &Paths, cat: &Catalogue) -> Result<Option<String>> {
    let current = desktop::icon_theme(paths);
    let mode = desktop::mode(paths);
    match cat.follow(&current, mode) {
        Some(want) if icons::find_theme(paths, want).is_some() => {
            desktop::set_icon_theme(paths, want)?;
            Ok(Some(want.to_string()))
        }
        _ => Ok(None),
    }
}

/// Follow for as long as the session lasts.
pub fn run(paths: &Paths, cat: &Catalogue, log: impl Fn(&str)) -> Result<()> {
    if let Some(t) = once(paths, cat)? {
        log(&format!("icons: {t}"));
    }
    let cfg = Config::with_custom_path(THEME_MODE_ID, desktop::MODE_VERSION, paths.config.clone())?;
    let (tx, rx) = mpsc::channel();
    let _watcher = cfg.watch(move |_, _| {
        let _ = tx.send(());
    })?;
    while rx.recv().is_ok() {
        // One mode change is several file events; settle first.
        while rx.recv_timeout(Duration::from_millis(250)).is_ok() {}
        match once(paths, cat) {
            Ok(Some(t)) => log(&format!("icons: {t}")),
            Ok(None) => {}
            Err(e) => log(&format!("error: {e}")),
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalogue::{tests::GOOD, Mode};
    use std::fs;

    fn setup() -> (tempfile::TempDir, Paths, Catalogue) {
        let d = tempfile::tempdir().unwrap();
        let p = Paths::under(d.path());
        // Ice and Ice-Dark come with the system, as Papirus does on the Desktop.
        for t in ["Ice", "Ice-Dark"] {
            let t = p.system_data[0].join("icons").join(t);
            fs::create_dir_all(&t).unwrap();
            fs::write(t.join("index.theme"), "[Icon Theme]\n").unwrap();
        }
        (d, p, Catalogue::parse(GOOD).unwrap())
    }

    #[test]
    fn swaps_within_the_pair_only() {
        let (_d, p, cat) = setup();
        desktop::set_icon_theme(&p, "Ice-Dark").unwrap();
        desktop::set_mode(&p, Mode::Light).unwrap();
        assert_eq!(once(&p, &cat).unwrap().as_deref(), Some("Ice"));
        assert_eq!(desktop::icon_theme(&p), "Ice");
        assert_eq!(once(&p, &cat).unwrap(), None, "nothing more to do");
        desktop::set_mode(&p, Mode::Dark).unwrap();
        assert_eq!(once(&p, &cat).unwrap().as_deref(), Some("Ice-Dark"));
        assert_eq!(desktop::undo_depth(&p), 0, "following is not an undo step");

        desktop::set_icon_theme(&p, "Cosmic").unwrap();
        desktop::set_mode(&p, Mode::Light).unwrap();
        assert_eq!(once(&p, &cat).unwrap(), None, "a theme outside a pair is never touched");
        assert_eq!(desktop::icon_theme(&p), "Cosmic");
    }

    #[test]
    fn a_missing_half_is_left_alone() {
        let (_d, p, cat) = setup();
        fs::remove_dir_all(p.system_data[0].join("icons/Ice")).unwrap();
        desktop::set_icon_theme(&p, "Ice-Dark").unwrap();
        desktop::set_mode(&p, Mode::Light).unwrap();
        assert_eq!(once(&p, &cat).unwrap(), None);
        assert_eq!(desktop::icon_theme(&p), "Ice-Dark");
    }

    #[test]
    fn follows_a_live_mode_change() {
        let (_d, p, cat) = setup();
        desktop::set_icon_theme(&p, "Ice-Dark").unwrap();
        desktop::set_mode(&p, Mode::Dark).unwrap();
        let (p2, cat2) = (p.clone(), cat.clone());
        std::thread::spawn(move || run(&p2, &cat2, |_| {}));
        std::thread::sleep(Duration::from_millis(300));
        desktop::set_mode(&p, Mode::Light).unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while desktop::icon_theme(&p) != "Ice" {
            assert!(std::time::Instant::now() < deadline, "the follower did not switch");
            std::thread::sleep(Duration::from_millis(50));
        }
    }
}
