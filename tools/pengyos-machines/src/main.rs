//! `pengyos-machines` — the "Machines" window. `--list [--json]` prints what the
//! window would show (the VMs and the ISO library) instead, for SSH and tests.

use std::process::ExitCode;

fn list(json: bool) -> ExitCode {
    let vms = match pengyos_machines::backend::list_vms() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("pengyos-machines: {e}");
            return ExitCode::from(1);
        }
    };
    let isos = pengyos_machines::backend::list_isos().unwrap_or_default();
    if json {
        let v = serde_json::json!({
            "vms": vms.iter().map(|v| serde_json::json!({
                "name": v.name, "state": v.state, "screen": v.has_screen(),
                "snapshots": v.snapshots.len(),
            })).collect::<Vec<_>>(),
            "isos": isos.iter().map(|i| i.name.clone()).collect::<Vec<_>>(),
        });
        println!("{v:#}");
        return ExitCode::SUCCESS;
    }
    if vms.is_empty() {
        println!("no VMs");
    }
    for v in &vms {
        println!(
            "{:<20} {:<9} {} CPUs, {}{}{}",
            v.name,
            v.state,
            v.cpus,
            v.memory,
            if v.has_screen() { ", screen" } else { "" },
            if v.snapshots.is_empty() { String::new() } else { format!(", {} snapshots", v.snapshots.len()) }
        );
    }
    for i in &isos {
        println!("iso  {} ({})", i.name, i.size);
    }
    ExitCode::SUCCESS
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--list") {
        return list(args.iter().any(|a| a == "--json"));
    }

    #[cfg(feature = "cosmic")]
    {
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(1100.0, 760.0));
        match cosmic::app::run::<pengyos_machines::app::App>(settings, ()) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-machines: {err}");
                ExitCode::from(1)
            }
        }
    }

    #[cfg(not(feature = "cosmic"))]
    {
        list(false)
    }
}
