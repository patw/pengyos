//! `pengyos-disks` — the "Disks" window. `pengyos-disks FILE` opens it with FILE
//! chosen as the image to write; `--list` prints the drives instead (no window).

use std::process::ExitCode;

#[cfg(feature = "udisks")]
fn list() -> ExitCode {
    let drives = match pengyos_disks::udisks::connect().and_then(|c| pengyos_disks::udisks::drives(&c)) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("pengyos-disks: {e}");
            return ExitCode::from(1);
        }
    };
    if drives.is_empty() {
        println!("no removable drives");
    }
    for d in &drives {
        println!("{}  {}  ({})", d.device, d.name(), d.summary());
        match d.lock() {
            Some(l) => println!("    locked: {}", l.text()),
            None => println!("    can format, write an image, eject"),
        }
    }
    ExitCode::SUCCESS
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();

    #[cfg(feature = "udisks")]
    if args.iter().any(|a| a == "--list") {
        return list();
    }

    #[cfg(feature = "cosmic")]
    {
        let image = args.iter().find(|a| !a.starts_with("--")).map(std::path::PathBuf::from);
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(680.0, 560.0));
        match cosmic::app::run::<pengyos_disks::app::App>(settings, image) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-disks: {err}");
                ExitCode::from(1)
            }
        }
    }

    #[cfg(not(feature = "cosmic"))]
    {
        let _ = args;
        eprintln!("pengyos-disks: built without the window (--features cosmic); --list needs --features udisks");
        ExitCode::from(2)
    }
}
