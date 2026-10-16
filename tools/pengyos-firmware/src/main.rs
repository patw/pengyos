//! `pengyos-firmware` — the "Firmware" app, and the root-side commands its
//! systemd units run.
//!
//! * `pengyos-firmware` — the window (with the `cosmic` feature), or a text
//!   report of the same facts (without it, which is also what an SSH session gets).
//! * `pengyos-firmware --notifier` — the session notifier (from /etc/xdg/autostart).
//! * `pengyos-firmware check --save` — ask fwupd and save the answer for the app
//!   and the notifier (`pengyos-firmware-check.service`, daily).
//! * `pengyos-firmware apply [DEVICE-ID…]` — install, streaming progress
//!   (`pengyos-firmware-update@<ids>.service`, started by the app).

use std::path::Path;
use std::process::ExitCode;

use pengyos_firmware::*;

const HELP: &str = "\
pengyos-firmware — see and apply firmware updates (fwupd)

  pengyos-firmware                 the Firmware window (COSMIC)
  pengyos-firmware --notifier      the session notifier
  pengyos-firmware check --save    ask fwupd what is available, save the answer
  pengyos-firmware apply [ID…]     install the updates for these devices (root)
  pengyos-firmware status          what is available, as text
";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let first = args.first().map(String::as_str).unwrap_or("");

    match first {
        "--help" | "-h" | "help" => {
            print!("{HELP}");
            return ExitCode::SUCCESS;
        }
        "check" => return check_cmd(&args),
        "apply" => return apply_cmd(&args[1..]),
        "status" => return status_cmd(),
        #[cfg(feature = "cosmic")]
        "--notifier" => return notifier::run(),
        _ => {}
    }

    #[cfg(feature = "cosmic")]
    {
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(680.0, 660.0));
        match cosmic::app::run::<pengyos_firmware::app::App>(settings, ()) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-firmware: {err}");
                ExitCode::from(1)
            }
        }
    }

    // Without the GUI: the same facts as text (handy over SSH too).
    #[cfg(not(feature = "cosmic"))]
    {
        if first == "--notifier" {
            eprintln!("pengyos-firmware: built without the cosmic feature, no notifier");
            return ExitCode::from(2);
        }
        status_cmd()
    }
}

/// Ask fwupd and save the answer. Root's daily job; `--quiet` for the unit.
fn check_cmd(args: &[String]) -> ExitCode {
    let save = args.iter().any(|a| a == "--save");
    let quiet = args.iter().any(|a| a == "--quiet");
    match check() {
        Ok(state) => {
            if save {
                if let Err(e) = state.save_to(Path::new(STATE)) {
                    eprintln!("pengyos-firmware: could not save the check: {e}");
                    return ExitCode::from(1);
                }
            }
            if !quiet {
                report(&state);
            }
            ExitCode::SUCCESS
        }
        Err(e) => {
            // Record the failure where the app can show it, then fail.
            if save {
                let _ = FirmwareState { error: Some(e.clone()), checked: Some(now()), ..Default::default() }
                    .save_to(Path::new(STATE));
            }
            if !quiet {
                eprintln!("pengyos-firmware: {e}");
            }
            ExitCode::from(1)
        }
    }
}

/// Install. Root's job (the unit runs it); progress goes to PROGRESS.
/// `args` is what follows `apply`: the unit's instance name, ids comma-joined.
fn apply_cmd(args: &[String]) -> ExitCode {
    match apply(&apply_ids(args), Path::new(PROGRESS)) {
        Ok(()) => {
            println!("Firmware update finished. Anything that needs a restart says so.");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("pengyos-firmware: {e}");
            ExitCode::from(1)
        }
    }
}

/// The text report: what the window would show.
fn status_cmd() -> ExitCode {
    match std::fs::read_to_string(STATE) {
        Ok(t) => {
            report(&FirmwareState::parse(&t));
            ExitCode::SUCCESS
        }
        Err(_) => {
            println!("No firmware check saved yet. Run: pengyos-firmware check --save");
            ExitCode::SUCCESS
        }
    }
}

fn report(state: &FirmwareState) {
    match &state.error {
        Some(e) => println!("Could not check: {e}"),
        None => println!("Checked {}", state.checked.map(|c| ago(c, now())).unwrap_or_else(|| "never".into())),
    }
    let staged = state.pending_reboot();
    if !staged.is_empty() {
        println!("Restart to finish: {}", FirmwareState::names(&staged));
    }
    let offers = state.offers();
    if offers.is_empty() {
        println!("No firmware updates are available ({} devices fwupd can update).", state.updatable().len());
    } else {
        for d in &offers {
            let a = d.offer().unwrap();
            println!("{}: {} -> {} ({}{})", d.name, d.current, a.version, human_size(a.size),
                a.urgency_word().map(|u| format!(", {u}")).unwrap_or_default());
            for w in d.warnings() {
                println!("  {w}");
            }
        }
    }
    match decide(state, &Prefs::load(), now()) {
        Some(n) => println!("would notify: {}", n.text().0),
        None => println!("would not notify"),
    }
}
