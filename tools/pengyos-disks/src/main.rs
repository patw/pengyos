//! `pengyos-disks` — the "Disks" window. `pengyos-disks FILE` opens it with FILE
//! chosen as the image to write; `--list` prints the drives, their partitions
//! and free space, and what is locked, instead (no window).

use std::process::ExitCode;

#[cfg(feature = "udisks")]
fn list() -> ExitCode {
    use pengyos_disks::{human, Slot};
    let drives = match pengyos_disks::udisks::connect().and_then(|c| pengyos_disks::udisks::drives(&c)) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("pengyos-disks: {e}");
            return ExitCode::from(1);
        }
    };
    if drives.is_empty() {
        println!("no drives");
    }
    for d in &drives {
        println!("{}  {}  ({} · {} · {})", d.device, d.name(), d.kind(), human(d.size), d.table_name());
        match d.lock() {
            Some(l) => println!("    drive locked: {}", l.text()),
            None => println!("    drive: can repartition, format, write an image{}", if d.removable { ", eject" } else { "" }),
        }
        let show = |v: &pengyos_disks::Volume, indent: &str| {
            let at = if v.mount_points.is_empty() { String::new() } else { format!(" at {}", v.mount_points.join(", ")) };
            let lock = d.volume_lock(v).map(|l| format!("  [locked: {}]", l.text())).unwrap_or_default();
            println!("{indent}{}  {}{at}{lock}", v.device, v.describe());
        };
        for s in d.layout() {
            match s {
                Slot::Part(i) => {
                    let v = &d.volumes[i];
                    show(v, "    ");
                    if v.container {
                        d.partitions().filter(|p| p.contained).for_each(|p| show(p, "        "));
                    }
                }
                Slot::Free(g) => println!("    free  {} at {}", human(g.size), human(g.offset)),
            }
        }
        if d.table.is_empty() {
            d.volumes.iter().filter(|v| v.backing.is_empty()).for_each(|v| show(v, "    "));
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
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(980.0, 680.0));
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
