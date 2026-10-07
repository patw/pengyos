//! The `pengyos` meta-CLI binary.
//!
//! A thin entry point over the [`pengyos_cli`] framework: parse `argv`, print
//! help/version, resolve config, and dispatch to a registered subcommand. Each
//! subcommand shells out to the matching Python tool under `tools/` via the
//! framework's [`Runner`] abstraction.

use std::io::Write;
use std::process::ExitCode;

use pengyos_cli::{Config, ProcessRunner, Registry};

/// Entry point. Returns the process exit code.
fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let registry = Registry::new();

    match args.first().map(String::as_str) {
        Some("-V") | Some("--version") => {
            let stdout = std::io::stdout();
            let mut handle = stdout.lock();
            let _ = writeln!(handle, "pengyos {}", pengyos_cli::VERSION);
            ExitCode::SUCCESS
        }
        Some("-h") | Some("--help") => {
            let stdout = std::io::stdout();
            let mut handle = stdout.lock();
            let _ = handle.write_all(registry.usage().as_bytes());
            ExitCode::SUCCESS
        }
        _ => {
            // Config is optional: a missing file yields an empty config, but a
            // present-but-bad file is a hard error.
            let config = match Config::resolve() {
                Ok(config) => config,
                Err(err) => {
                    eprintln!("pengyos: {err}");
                    return ExitCode::from(1);
                }
            };

            let mut runner = ProcessRunner;
            let outcome = registry.dispatch(&args, &config, &mut runner);

            if !outcome.stdout.is_empty() {
                print!("{}", outcome.stdout);
            }
            if !outcome.stderr.is_empty() {
                eprint!("{}", outcome.stderr);
            }

            // `ExitCode::from` takes a `u8`; map negative/oversized codes safely.
            let code = if outcome.exit_code < 0 {
                1
            } else {
                (outcome.exit_code.min(255)) as u8
            };
            ExitCode::from(code)
        }
    }
}
