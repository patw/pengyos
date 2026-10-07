//! Integration tests for the dispatch framework, exercising the real compiled
//! `pengyos` binary end-to-end.
//!
//! These spawn the actual binary (via `CARGO_BIN_EXE_pengyos`) and assert on its
//! exit codes and output. They need no network, root, display, or hardware:
//!
//! * Parse errors (missing/unknown args) are resolved before any external tool
//!   runs.
//! * The "command not found" path uses the real [`ProcessRunner`] against a
//!   sibling tool that is intentionally not installed (`pengyos-doctor`).
//!
//! Deeper dispatch golden coverage (argv → exact `CommandSpec`) lives in the
//! unit tests of `src/dispatch.rs`, which use a mock runner.

use std::process::Command;

/// Spawn the compiled `pengyos` binary with the given args.
fn pengyos(args: &[&str]) -> (i32, String, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_pengyos"))
        .args(args)
        .output()
        .expect("failed to spawn the pengyos binary");
    let code = out.status.code().unwrap_or(-1);
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    (code, stdout, stderr)
}

#[test]
fn no_args_is_usage_error() {
    let (code, _, stderr) = pengyos(&[]);
    assert_eq!(code, 2);
    assert!(stderr.contains("missing command"), "stderr: {stderr:?}");
}

#[test]
fn unknown_command_is_usage_error() {
    let (code, _, stderr) = pengyos(&["frobnicate"]);
    assert_eq!(code, 2);
    assert!(
        stderr.contains("unknown command `frobnicate`"),
        "stderr: {stderr:?}"
    );
}

#[test]
fn build_without_target_is_usage_error() {
    // `build` requires a positional target; parse fails before any tool runs.
    let (code, _, stderr) = pengyos(&["build"]);
    assert_eq!(code, 2);
    assert!(
        stderr.contains("missing 1 positional argument"),
        "stderr: {stderr:?}"
    );
}

#[test]
fn command_help_exits_zero() {
    let (code, stdout, _) = pengyos(&["doctor", "--help"]);
    assert_eq!(code, 0);
    assert!(
        stdout.contains("report host readiness"),
        "stdout: {stdout:?}"
    );
}

#[test]
fn help_lists_all_subcommands() {
    let (code, stdout, _) = pengyos(&["--help"]);
    assert_eq!(code, 0);
    for cmd in ["build", "verify", "triage", "profile", "sbom", "doctor"] {
        assert!(stdout.contains(cmd), "help missing `{cmd}`: {stdout:?}");
    }
}

#[test]
fn missing_sibling_tool_is_clean_error() {
    // `pengyos-build` is not installed, so the real ProcessRunner reports a
    // clean "command not found" (exit 127) rather than panicking. (`build`
    // takes a positional, so it dispatches before the runner runs.)
    let (code, _, stderr) = pengyos(&["build", "x86_64"]);
    assert_eq!(code, 127);
    assert!(
        stderr.contains("command not found: pengyos-build"),
        "stderr: {stderr:?}"
    );
}

#[test]
fn dispatched_failure_is_surfaced_with_annotation() {
    // The real ProcessRunner reports "command not found" as exit 127; the
    // meta-CLI must surface a clear annotation naming the subcommand, tool, and
    // exit status *before* the tool's own message. (`build` takes a positional,
    // so it dispatches to the uninstalled `pengyos-build`.)
    let (code, _, stderr) = pengyos(&["build", "x86_64"]);
    assert_eq!(code, 127);
    assert!(
        stderr.contains("pengyos build: `pengyos-build` exited with status 127"),
        "stderr should carry the clear error annotation: {stderr:?}"
    );
    // The tool's own message is preserved after the annotation.
    assert!(
        stderr.contains("command not found: pengyos-build"),
        "stderr should still name the missing tool: {stderr:?}"
    );
}

#[test]
fn doctor_reports_readiness_and_exits_zero_when_ready() {
    // `doctor` is a native Rust readiness check (not a dispatch to a sibling
    // tool), so it never hits the "command not found" path. On a host where the
    // required tools are present it exits 0; otherwise 1. Either way it must
    // print the readiness matrix and a summary, and never a usage error.
    let (code, stdout, stderr) = pengyos(&["doctor"]);
    assert!(
        code == 0 || code == 1,
        "doctor should exit 0 (ready) or 1 (not ready), got {code}; stderr: {stderr}"
    );
    assert!(
        stdout.contains("READINESS:"),
        "doctor output should print a readiness matrix: {stdout:?}"
    );
    for check in ["buildroot", "kvm", "tools"] {
        assert!(
            stdout.contains(check),
            "doctor output should list the `{check}` check: {stdout:?}"
        );
    }
}

#[test]
fn doctor_help_lists_the_command() {
    let (code, stdout, _) = pengyos(&["doctor", "--help"]);
    assert_eq!(code, 0);
    assert!(stdout.contains("report host readiness"));
}

#[test]
fn doctor_json_output_is_stable_schema() {
    // `doctor` is a native Rust check, so `--json` runs end-to-end without any
    // external tool. The output is a single JSON document with the stable
    // top-level shape (spec.md: "--json schema stable"), regardless of whether
    // this host is ready (exit 0) or not (exit 1).
    let (code, stdout, stderr) = pengyos(&["doctor", "--json"]);
    assert!(code == 0 || code == 1, "doctor exit code: {code}");
    assert!(stderr.is_empty(), "JSON mode must not write to stderr: {stderr:?}");
    assert_eq!(
        stdout.lines().count(),
        1,
        "JSON mode must emit exactly one line: {stdout:?}"
    );
    // Stable top-level keys are present (a hand-rolled check, since the suite
    // has no JSON parser): the document is well-formed and complete.
    for key in [
        r#""command":"doctor"#,
        r#""executable":"pengyos-doctor"#,
        r#""exit_code":"#,
        r#""ok":"#,
        r#""checks":["#,
    ] {
        assert!(stdout.contains(key), "missing {key} in {stdout:?}");
    }
    // Each readiness check appears with its fields.
    for field in [r#""id":"#, r#""severity":"#, r#""status":"#, r#""detail":"#] {
        assert!(stdout.contains(field), "missing {field} in {stdout:?}");
    }
    // The human report never leaks into stdout in JSON mode.
    assert!(!stdout.contains("READINESS:"), "human report leaked: {stdout:?}");
}

#[test]
fn json_flag_is_never_forwarded_to_the_tool() {
    // `pengyos-build` is not installed, so this hits the "command not found"
    // path (exit 127). The point is that `--json` is consumed by the meta-CLI:
    // the tool would never see it, and the document captures the raw stderr.
    let (code, stdout, _) = pengyos(&["build", "x86_64", "--json"]);
    assert_eq!(code, 127);
    assert!(stdout.contains(r#""exit_code":127"#));
    assert!(stdout.contains(r#""ok":false"#));
    assert!(
        stdout.contains("command not found: pengyos-build"),
        "the tool's raw stderr should be captured in the document: {stdout:?}"
    );
    // The `--json` flag itself must not appear in the forwarded args.
    assert!(!stdout.contains(r#""--json""#), "--json leaked into args: {stdout:?}");
}
