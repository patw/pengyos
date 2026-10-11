//! The session notifier (`pengyos-updates --notifier`, from XDG autostart).
//!
//! It never checks the network itself: root's daily `pengyos-update-check.timer`
//! does, and writes `/var/lib/pengyos-update/state.json`. Every few minutes the
//! notifier re-reads that, the running version and the user's settings, and —
//! only if [`crate::decide`] says so — posts one freedesktop notification.
//! Clicking it opens the app, where the user updates or picks "remind me later".
//!
//! The app lane (Flatpak) has no root-side check: after login and then every
//! [`crate::APP_CHECK_EVERY`] the notifier asks `flatpak remote-ls --updates`
//! itself (read-only, no privileges needed) and, if [`crate::decide_apps`] says
//! so, posts one notification that opens COSMIC Store, where apps are updated.

use std::collections::HashMap;
use std::path::Path;
use std::process::{Command, ExitCode};
use std::time::Duration;

use zbus::blocking::{Connection, Proxy};
use zbus::zvariant::Value;

use crate::{decide, decide_apps, now, AppUpdates, Prefs, Running, SystemState, APP_CHECK_EVERY, APP_ID, STATE};

const POLL: Duration = Duration::from_secs(5 * 60);
/// Give the desktop a moment after login before the first notification.
const FIRST: Duration = Duration::from_secs(90);

fn proxy(conn: &Connection) -> zbus::Result<Proxy<'_>> {
    Proxy::new(
        conn,
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications",
    )
}

/// What a notification opens when clicked.
struct Opens {
    program: &'static str,
    label: &'static str,
    icon: &'static str,
}

const OPENS_UPDATES: Opens =
    Opens { program: "pengyos-updates", label: "Open PengyOS Updates", icon: "software-update-available" };
const OPENS_STORE: Opens = Opens { program: "cosmic-store", label: "Open the Store", icon: "system-software-install" };

fn notify(conn: &Connection, opens: &Opens, summary: &str, body: &str) -> zbus::Result<u32> {
    let mut hints: HashMap<&str, Value<'_>> = HashMap::new();
    hints.insert("desktop-entry", Value::from(APP_ID));
    hints.insert("urgency", Value::from(1u8));
    proxy(conn)?.call(
        "Notify",
        &("PengyOS Updates", 0u32, opens.icon, summary, body, vec!["default", opens.label], hints, -1i32),
    )
}

/// Open `program` when notification `id` is clicked (until it is closed).
fn on_click(conn: Connection, id: u32, program: &'static str) {
    std::thread::spawn(move || {
        let Ok(p) = proxy(&conn) else { return };
        let Ok(actions) = p.receive_signal("ActionInvoked") else { return };
        for msg in actions {
            let Ok((nid, _key)) = msg.body().deserialize::<(u32, String)>() else { continue };
            if nid == id {
                let _ = Command::new(program).spawn();
                return;
            }
        }
    });
}

pub fn run() -> ExitCode {
    let conn = match Connection::session() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("pengyos-updates --notifier: no session bus: {e}");
            return ExitCode::from(1);
        }
    };
    std::thread::sleep(FIRST);
    let mut apps_checked: Option<i64> = None;
    loop {
        let running = Running::load();
        let state = SystemState::load(Path::new(STATE));
        let mut prefs = Prefs::load();
        let t = now();
        if let Some(notice) = decide(&state, &running.version, &prefs, t) {
            let name = if state.name.is_empty() { running.name.clone() } else { state.name.clone() };
            let (summary, body) = notice.text(&name);
            match notify(&conn, &OPENS_UPDATES, &summary, &body) {
                Ok(id) => {
                    on_click(conn.clone(), id, OPENS_UPDATES.program);
                    prefs.notified = notice.key();
                    prefs.notified_at = t;
                    if let Err(e) = prefs.save() {
                        eprintln!("pengyos-updates --notifier: cannot save settings: {e}");
                    }
                }
                Err(e) => eprintln!("pengyos-updates --notifier: notification failed: {e}"),
            }
        }
        if prefs.notify && apps_checked.map_or(true, |c| t - c >= APP_CHECK_EVERY) {
            apps_checked = Some(t);
            if let Some(apps) = AppUpdates::check() {
                if decide_apps(&apps, &prefs, t) {
                    let (summary, body) = apps.text();
                    match notify(&conn, &OPENS_STORE, &summary, &body) {
                        Ok(id) => {
                            on_click(conn.clone(), id, OPENS_STORE.program);
                            let mut prefs = Prefs::load();
                            prefs.apps_notified = apps.key();
                            prefs.apps_notified_at = t;
                            if let Err(e) = prefs.save() {
                                eprintln!("pengyos-updates --notifier: cannot save settings: {e}");
                            }
                        }
                        Err(e) => eprintln!("pengyos-updates --notifier: notification failed: {e}"),
                    }
                }
            }
        }
        std::thread::sleep(POLL);
    }
}
