//! The session notifier (`pengyos-firmware --notifier`, from XDG autostart).
//!
//! It never touches the network: root's daily `pengyos-firmware-check.timer`
//! asks fwupd and writes `/var/lib/pengyos-firmware/state.json`. Every few
//! minutes the notifier re-reads that and the user's settings and — only if
//! [`crate::decide`] says so — posts one freedesktop notification. Clicking it
//! opens the app, where the person installs the update or picks "remind me
//! later". Same contract as the PengyOS Updates notifier, same snoozes.

use std::collections::HashMap;
use std::path::Path;
use std::process::{Command, ExitCode};
use std::time::Duration;

use zbus::blocking::{Connection, Proxy};
use zbus::zvariant::Value;

use crate::{decide, now, FirmwareState, Prefs, APP_ID, STATE};

const POLL: Duration = Duration::from_secs(10 * 60);
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

fn notify(conn: &Connection, summary: &str, body: &str) -> zbus::Result<u32> {
    let mut hints: HashMap<&str, Value<'_>> = HashMap::new();
    hints.insert("desktop-entry", Value::from(APP_ID));
    hints.insert("urgency", Value::from(1u8));
    proxy(conn)?.call(
        "Notify",
        &("PengyOS Firmware", 0u32, "application-x-firmware", summary, body, vec!["default", "Open Firmware"], hints, -1i32),
    )
}

/// Open the app when notification `id` is clicked (until it is closed).
fn on_click(conn: Connection, id: u32) {
    std::thread::spawn(move || {
        let Ok(p) = proxy(&conn) else { return };
        let Ok(actions) = p.receive_signal("ActionInvoked") else { return };
        for msg in actions {
            let Ok((nid, _key)) = msg.body().deserialize::<(u32, String)>() else { continue };
            if nid == id {
                let _ = Command::new("pengyos-firmware").spawn();
                return;
            }
        }
    });
}

pub fn run() -> ExitCode {
    let conn = match Connection::session() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("pengyos-firmware --notifier: no session bus: {e}");
            return ExitCode::from(1);
        }
    };
    std::thread::sleep(FIRST);
    loop {
        let state = FirmwareState::load(Path::new(STATE));
        let mut prefs = Prefs::load();
        let t = now();
        if let Some(notice) = decide(&state, &prefs, t) {
            let (summary, body) = notice.text();
            match notify(&conn, &summary, &body) {
                Ok(id) => {
                    on_click(conn.clone(), id);
                    prefs.notified = notice.key();
                    prefs.notified_at = t;
                    if let Err(e) = prefs.save() {
                        eprintln!("pengyos-firmware --notifier: cannot save settings: {e}");
                    }
                }
                Err(e) => eprintln!("pengyos-firmware --notifier: notification failed: {e}"),
            }
        }
        std::thread::sleep(POLL);
    }
}
