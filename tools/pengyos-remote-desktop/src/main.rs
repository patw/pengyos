//! `pengyos-remote-desktop` — the "Remote Desktop" app.
//!
//! * `pengyos-remote-desktop` — the window (with the `cosmic` feature), or the
//!   text report below without it.
//! * `pengyos-remote-desktop --status` — the same, as text.

use std::process::ExitCode;

use pengyos_remote_desktop::*;

const HELP: &str = "\
pengyos-remote-desktop — let other computers see and use this desktop (RDP)

  pengyos-remote-desktop           the Remote Desktop window (COSMIC)
  pengyos-remote-desktop --status  how it stands, as text

Changes are made by pengyos-remote (pengyos-remote --help).
";

fn main() -> ExitCode {
    let first = std::env::args().nth(1).unwrap_or_default();
    match first.as_str() {
        "--help" | "-h" | "help" => {
            print!("{HELP}");
            return ExitCode::SUCCESS;
        }
        "--status" => return report(),
        _ => {}
    }

    #[cfg(feature = "cosmic")]
    {
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(620.0, 680.0));
        match cosmic::app::run::<pengyos_remote_desktop::app::App>(settings, ()) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-remote-desktop: {err}");
                ExitCode::from(1)
            }
        }
    }

    #[cfg(not(feature = "cosmic"))]
    report()
}

fn report() -> ExitCode {
    match status() {
        Ok(s) => {
            println!("{}.", s.headline());
            println!("  connect to: {}", s.connect_to().join(", "));
            println!("  user: {}   password: {}", s.user, if s.password_set { "set" } else { "not set" });
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("pengyos-remote-desktop: {e}");
            ExitCode::from(1)
        }
    }
}
