//! `pengyos-printers` — the "Printers" window, and its headless face.
//!
//! ```text
//! pengyos-printers               the window
//! pengyos-printers --add         the window, on the Add page
//! pengyos-printers --list        the printers and the print queue, no window
//! pengyos-printers --find        the printers CUPS can find, and the driver
//!                                the app would pick for each (needs wheel)
//! ```

use std::process::ExitCode;

use pengyos_printers::cups::Cups;

fn list() -> ExitCode {
    match Cups::new().snapshot() {
        Ok(s) => {
            print!("{}", pengyos_printers::format_snapshot(&s, pengyos_printers::now()));
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("pengyos-printers: {e}");
            ExitCode::from(1)
        }
    }
}

fn find() -> ExitCode {
    let cups = Cups::new();
    let printers = cups.printers().unwrap_or_default();
    let devices = match cups.devices(10) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("pengyos-printers: {e}");
            return ExitCode::from(1);
        }
    };
    let drivers = cups.drivers().unwrap_or_else(|e| {
        eprintln!("pengyos-printers: {e}");
        Vec::new()
    });
    let found = pengyos_printers::found_printers(&devices, &printers);
    if found.is_empty() {
        println!("no printers found");
    }
    for f in &found {
        let added = f.added_as.as_deref().map(|a| format!(" (already added as {a})")).unwrap_or_default();
        println!("{}  {}{added}\n    {}", f.device.title(), f.connection(), f.device.uri);
        for (i, d) in pengyos_printers::drivers_for(&f.device, &drivers).iter().take(3).enumerate() {
            println!("    {} {}  [{}]", if i == 0 { "driver:" } else { "    or:" }, d.title(), d.name);
        }
    }
    ExitCode::SUCCESS
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--list") {
        return list();
    }
    if args.iter().any(|a| a == "--find") {
        return find();
    }

    #[cfg(feature = "cosmic")]
    {
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(760.0, 760.0));
        let flags = pengyos_printers::app::Flags { add: args.iter().any(|a| a == "--add") };
        match cosmic::app::run::<pengyos_printers::app::App>(settings, flags) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-printers: {err}");
                ExitCode::from(1)
            }
        }
    }

    #[cfg(not(feature = "cosmic"))]
    {
        eprintln!("pengyos-printers: built without the window (--features cosmic); --list and --find work");
        ExitCode::from(2)
    }
}
