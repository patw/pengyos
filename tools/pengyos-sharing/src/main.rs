//! `pengyos-sharing` — the "File Sharing" app.
//!
//! * `pengyos-sharing` — the window (with the `cosmic` feature), or the text
//!   report below without it.
//! * `pengyos-sharing --list` — what is shared and how other computers open it.

use std::process::ExitCode;

use pengyos_sharing::*;

const HELP: &str = "\
pengyos-sharing — share folders with the other computers on your network (SMB)

  pengyos-sharing          the File Sharing window (COSMIC)
  pengyos-sharing --list   what is shared, as text

Changes are made by pengyos-shares (sudo pengyos-shares --help).
";

fn main() -> ExitCode {
    let first = std::env::args().nth(1).unwrap_or_default();
    match first.as_str() {
        "--help" | "-h" | "help" => {
            print!("{HELP}");
            return ExitCode::SUCCESS;
        }
        "--list" => return list(),
        _ => {}
    }

    #[cfg(feature = "cosmic")]
    {
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(660.0, 720.0));
        match cosmic::app::run::<pengyos_sharing::app::App>(settings, ()) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-sharing: {err}");
                ExitCode::from(1)
            }
        }
    }

    #[cfg(not(feature = "cosmic"))]
    list()
}

fn list() -> ExitCode {
    match status(false) {
        Ok(s) => {
            println!("File sharing is {}.", if s.active { "on" } else { "off" });
            for sh in &s.shares {
                let (win, url) = addresses(&s.host, &sh.name);
                println!("  {}  {}  ({})", sh.name, sh.path, describe(sh));
                println!("      {win}   {url}");
            }
            if s.shares.is_empty() {
                println!("Nothing is shared.");
            }
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("pengyos-sharing: {e}");
            ExitCode::from(1)
        }
    }
}
