//! Integration tests for the `pengyos` binary's trivial (scaffold) surface.
//!
//! These spawn the actual compiled binary via Cargo's `CARGO_BIN_EXE_<name>`
//! env var, so they exercise the real exit codes and output — not just the
//! library. No network, no root, no external tools.

use std::process::Command;

/// Spawn the compiled `pengyos` binary with the given args.
fn pengyos(args: &[&str]) -> (i32, String, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_pengyos"))
        .args(args)
        .output()
        .expect("failed to spawn the pengyos binary");
    let status = out.status;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    (status.code().unwrap_or(-1), stdout, stderr)
}

#[test]
fn version_flag_prints_program_and_version() {
    let (code, stdout, _) = pengyos(&["--version"]);
    assert_eq!(code, 0, "exit code should be 0");
    assert!(
        stdout.starts_with("pengyos ") && stdout.trim_end() == format!("pengyos {}", pengyos_cli::VERSION),
        "unexpected --version output: {stdout:?}"
    );
}

#[test]
fn short_version_flag_works() {
    let (code, stdout, _) = pengyos(&["-V"]);
    assert_eq!(code, 0);
    assert!(stdout.contains(pengyos_cli::VERSION));
}

#[test]
fn help_prints_usage_and_exits_zero() {
    let (code, stdout, _) = pengyos(&["--help"]);
    assert_eq!(code, 0);
    assert!(stdout.contains("USAGE:"));
    assert!(stdout.contains("COMMANDS:"));
    // Every documented subcommand is advertised.
    for cmd in ["build", "verify", "triage", "profile", "sbom", "doctor"] {
        assert!(stdout.contains(cmd), "help output missing `{cmd}`");
    }
}

#[test]
fn no_command_is_a_usage_error() {
    let (code, _, stderr) = pengyos(&[]);
    assert_eq!(code, 2, "missing command should exit with usage code 2");
    assert!(stderr.contains("missing command"), "stderr should explain the missing command");
}

#[test]
fn unknown_command_is_a_usage_error() {
    let (code, _, stderr) = pengyos(&["not-a-command"]);
    assert_eq!(code, 2, "unknown command should exit with usage code 2");
    assert!(
        stderr.contains("unknown command"),
        "stderr should name the unknown command, got: {stderr:?}"
    );
}
