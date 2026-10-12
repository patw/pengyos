//! `pengyos-gamepads` — the "Gamepads" window, and its headless face.
//!
//! ```text
//! pengyos-gamepads                 the window
//! pengyos-gamepads --list          the devices, no window (works over ssh)
//! pengyos-gamepads --json          the same as JSON
//! pengyos-gamepads --selftest      the window, driven by a synthetic pad
//! ```
//!
//! `--list` reads sysfs only, so it answers on a machine whose `/dev/input`
//! nodes the user may not open — and says so, which is the app's most useful
//! sentence.

use std::process::ExitCode;

#[cfg(feature = "evdev")]
fn headless(json: bool) -> ExitCode {
    let pads = match pengyos_gamepads::raw::pads() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("pengyos-gamepads: {e}");
            return ExitCode::from(1);
        }
    };
    if json {
        print!("{}", pengyos_gamepads::to_json(&pads));
    } else {
        print!("{}", pengyos_gamepads::format_pads(&pads));
    }
    ExitCode::SUCCESS
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();

    #[cfg(feature = "evdev")]
    {
        if args.iter().any(|a| a == "--json") {
            return headless(true);
        }
        if args.iter().any(|a| a == "--list") {
            return headless(false);
        }
    }

    #[cfg(feature = "cosmic")]
    {
        let selftest = args.iter().any(|a| a == "--selftest");
        let flags = pengyos_gamepads::app::Flags {
            selftest,
            device: args.iter().find(|a| !a.starts_with("--")).map(std::path::PathBuf::from),
        };
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(900.0, 760.0));
        match cosmic::app::run::<pengyos_gamepads::app::App>(settings, flags) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-gamepads: {err}");
                ExitCode::from(1)
            }
        }
    }

    #[cfg(not(feature = "cosmic"))]
    {
        let _ = args;
        eprintln!(
            "pengyos-gamepads: built without the window (--features cosmic); \
             --list/--json need --features evdev"
        );
        ExitCode::from(2)
    }
}
