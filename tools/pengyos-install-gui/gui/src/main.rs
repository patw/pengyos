//! `pengyos-install-gui` binary.
//!
//! In the default (no-`cosmic`) build this is a headless smoke tool: it drives
//! the pure [`pengyos_install_model::Model`] from a scripted answer set and
//! prints the derived plan — no window, no display, no network.
//!
//! With the `cosmic` feature enabled this binary becomes the libcosmic app
//! shell (see `cosmic_app.rs`): it launches the graphical installer wizard,
//! renders each screen from the model and drives the external
//! `pengyos-install` backend once the wizard completes.

use std::process::ExitCode;

// Only the headless smoke tool (`run`) uses these; dead under `--features cosmic`.
#[cfg(not(feature = "cosmic"))]
use std::path::PathBuf;

#[cfg(not(feature = "cosmic"))]
use pengyos_install_gui::answers_from_json;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();

    // With the `cosmic` feature enabled, the binary is the graphical installer
    // shell. It renders the wizard from the model and drives the external
    // `pengyos-install` backend; it needs a running desktop and libcosmic, so it
    // is only built where libcosmic is available (see the `cosmic` feature).
    #[cfg(feature = "cosmic")]
    {
        use pengyos_install_gui::cosmic_app::{AppModel, Flags};

        // `--engine PATH` overrides the install engine (default: `pengyos-install`
        // on PATH); the app lists disks and runs the install through it.
        let mut flags = Flags::default();
        if let Some(engine) = args
            .iter()
            .position(|a| a == "--engine")
            .and_then(|i| args.get(i + 1))
        {
            flags.engine = engine.clone();
        }
        let settings = cosmic::app::Settings::default()
            .size(cosmic::iced::Size::new(760.0, 620.0));
        match cosmic::app::run::<AppModel>(settings, flags) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-install-gui: {err}");
                ExitCode::from(1)
            }
        }
    }

    // Default (no-`cosmic`) build: the headless smoke tool.
    #[cfg(not(feature = "cosmic"))]
    match run(&args) {
        Ok(code) => code,
        Err(err) => {
            eprintln!("pengyos-install-gui: {err}");
            ExitCode::from(2)
        }
    }
}

// Headless smoke tool. Used by the non-`cosmic` build (`main`) and by the unit
// tests; dead under `--features cosmic` (where the app shell replaces it), so it
// is excluded from that build to avoid a dead-code warning.
#[cfg(any(test, not(feature = "cosmic")))]
fn run(args: &[String]) -> Result<ExitCode, String> {
    let mut answers_path: Option<PathBuf> = None;
    let mut show_help = false;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-h" | "--help" => show_help = true,
            "--answers" => {
                i += 1;
                let path = args
                    .get(i)
                    .ok_or("--answers requires a file argument")?;
                answers_path = Some(PathBuf::from(path));
            }
            other => return Err(format!("unknown argument {other:?}")),
        }
        i += 1;
    }

    if show_help {
        print_usage();
        return Ok(ExitCode::SUCCESS);
    }

    // Default: a built-in scripted smoke run.
    let answers = if let Some(path) = answers_path {
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        answers_from_json(&text)?
    } else {
        vec![
            ("accepted_terms", "true"),
            ("selected_disk", "/dev/sda"),
            ("layout", "wipe"),
            ("hostname", "pengyos-box"),
            ("username", "alice"),
            ("password", "hunter2"),
            ("network_ssid", "home"),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
    };

    let model = pengyos_install_model::Model::run_answers(&answers[..])
        .map_err(|e| format!("{e}"))?;

    println!("{}", pengyos_install_gui::render_all(&model));
    match model.install_toml() {
        Some(toml) => {
            println!("--- derived install.toml ---");
            println!("{toml}");
        }
        None => {
            eprintln!("warning: plan incomplete");
            return Ok(ExitCode::from(1));
        }
    }
    Ok(ExitCode::SUCCESS)
}

#[cfg(any(test, not(feature = "cosmic")))]
fn print_usage() {
    println!(
        "pengyos-install-gui — the PengyOS Desktop installer wizard\n\
\n\
USAGE:\n\
    pengyos-install-gui [--answers <file.json>]\n\
    pengyos-install-gui -h | --help\n\
\n\
OPTIONS:\n\
    --answers <file>   Drive the wizard from a scripted JSON answer set and\n\
                       print the derived plan (headless smoke path).\n\
    -h, --help         Show this help.\n\
\n\
Without --answers a built-in scripted run is used. The answer file is a flat\n\
JSON object of string values, e.g.:\n\
    {{ \"accepted_terms\": \"true\", \"selected_disk\": \"/dev/sda\",\n\
      \"layout\": \"wipe\", \"hostname\": \"pengyos-box\",\n\
      \"username\": \"alice\", \"password\": \"hunter2\" }}"
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn help_exits_success() {
        let code = run(&["--help".to_string()]).expect("help should succeed");
        assert_eq!(code, ExitCode::SUCCESS);
    }

    #[test]
    fn default_smoke_completes() {
        let code = run(&[]).expect("smoke run should succeed");
        assert_eq!(code, ExitCode::SUCCESS);
    }

    #[test]
    fn unknown_argument_errors() {
        assert!(run(&["--bogus".to_string()]).is_err());
    }

    // --- the `--answers <file>` smoke path (spec interface) -------------------
    //
    // The fixtures live in `gui/tests/fixtures/`. `cargo test` runs each test
    // with the package root (`gui/`) as the working directory, so the relative
    // path resolves to `gui/tests/fixtures/...`.

    #[test]
    fn answers_file_completes_headlessly() {
        // The spec interface: `pengyos-install-gui --answers a.json` renders
        // from a scripted answer set and completes with no window.
        let code = run(&["--answers".to_string(), "tests/fixtures/answers.json".to_string()])
            .expect("a complete scripted run should succeed");
        assert_eq!(code, ExitCode::SUCCESS);
    }

    #[test]
    fn answers_file_missing_errors() {
        let err = run(&[
            "--answers".to_string(),
            "tests/fixtures/does_not_exist.json".to_string(),
        ]);
        assert!(err.is_err(), "a missing file must be an error");
        let msg = format!("{}", err.unwrap_err());
        assert!(msg.contains("does_not_exist.json"), "error should name the file: {msg}");
    }

    #[test]
    fn answers_file_incomplete_blocks() {
        // No password supplied -> the run must stop with the User blocker.
        let err = run(&[
            "--answers".to_string(),
            "tests/fixtures/answers_incomplete.json".to_string(),
        ])
        .unwrap_err();
        let msg = format!("{}", err);
        assert!(msg.contains("password"), "expected a password blocker, got: {msg}");
    }
}
