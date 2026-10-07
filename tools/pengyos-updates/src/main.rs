//! `pengyos-updates` — the "PengyOS Updates" app; `--notifier` runs the session
//! notifier instead (started from /etc/xdg/autostart).

use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();

    #[cfg(feature = "cosmic")]
    {
        if args.iter().any(|a| a == "--notifier") {
            return pengyos_updates::notifier::run();
        }
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(620.0, 640.0));
        match cosmic::app::run::<pengyos_updates::app::App>(settings, ()) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-updates: {err}");
                ExitCode::from(1)
            }
        }
    }

    // Without the GUI: print what the app would show (handy over SSH too).
    #[cfg(not(feature = "cosmic"))]
    {
        use pengyos_updates::*;
        let _ = args;
        let running = Running::load();
        let state = SystemState::load(std::path::Path::new(STATE));
        println!("{} {}", running.name, running.version);
        match (state.pending_restart(&running.version), state.offer(&running.version)) {
            (Some(v), _) => println!("{v} is installed — restart to start it"),
            (None, Some(a)) => println!("{} is available", a.version),
            _ => println!("up to date"),
        }
        match decide(&state, &running.version, &Prefs::load(), now()) {
            Some(n) => println!("would notify: {}", n.text(&running.name).0),
            None => println!("would not notify"),
        }
        ExitCode::SUCCESS
    }
}
