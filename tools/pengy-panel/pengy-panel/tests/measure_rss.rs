//! End-to-end tests for the `--headless --measure-rss` mode (task 12).
//!
//! These exercise the *real binary* the way CI will: launch it, pass the
//! one-shot flags, and assert on the stdout line and exit code. No compositor,
//! display, network, or hardware is needed. The gate is exercised with
//! deterministic limits — a huge limit always passes, a `0` MiB limit always
//! fails (every real process uses more than 0 KiB) — so the assertions hold on
//! any machine and for either the debug or release build.

use std::process::Command;

fn panel_bin() -> &'static str {
    env!("CARGO_BIN_EXE_pengy-panel")
}

fn run_panel(args: &[&str]) -> std::process::Output {
    Command::new(panel_bin())
        .args(args)
        .output()
        .unwrap_or_else(|e| panic!("failed to launch {}: {e}", panel_bin()))
}

/// Parse the measured KiB out of the `idle-rss: <KIB> KiB (...)` stdout line.
fn measured_kib(stdout: &str) -> u64 {
    let line = stdout.lines().next().expect("one stdout line");
    let after_prefix = line
        .strip_prefix("idle-rss: ")
        .expect("line starts with `idle-rss: `");
    let kib = after_prefix
        .split_whitespace()
        .next()
        .expect("a KiB number follows the prefix")
        .to_string();
    kib.parse::<u64>().expect("KiB parses as a number")
}

/// The mode runs the real pipeline and reports a positive resident-set-size
/// number on stdout, exiting 0 when the limit is comfortably met (a huge
/// limit, so this holds for the debug and the release build alike).
#[test]
fn measure_rss_reports_a_positive_kib_and_passes_a_large_limit() {
    let out = run_panel(&[
        "--headless",
        "--measure-rss",
        "--width",
        "120",
        "--max-mib",
        "100000",
    ]);
    assert!(
        out.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.starts_with("idle-rss: "), "{stdout}");
    assert!(stdout.contains(" OK"), "{stdout}");
    assert!(
        measured_kib(&stdout) > 0,
        "a running process must have a positive VmRSS: {stdout}"
    );
}

/// The gate: a `0` MiB limit is exceeded by any real process, so the mode
/// reports EXCEEDED and exits 1. Deterministic on any machine/build.
#[test]
fn measure_rss_gates_and_exits_one_when_the_limit_is_exceeded() {
    let out = run_panel(&["--headless", "--measure-rss", "--max-mib", "0"]);
    assert_eq!(out.status.code(), Some(1));
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains(" EXCEEDED"), "{stdout}");
    assert!(measured_kib(&stdout) > 0, "{stdout}");
}

/// The default limit (15 MiB) is applied when `--max-mib` is omitted: a bare
/// `--headless --measure-rss` still prints a line and, on a normal host, the
/// release/debug panel sits well under 15 MiB → OK / exit 0.
#[test]
fn measure_rss_without_max_mib_uses_the_default_target() {
    let out = run_panel(&["--headless", "--measure-rss"]);
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("[limit 15 MiB]"), "{stdout}");
    // On a normal host the panel is far under 15 MiB, so this passes.
    assert!(
        out.status.success(),
        "panel should be under the 15 MiB target; stdout: {stdout}\nstderr: {}",
        String::from_utf8_lossy(&out.stderr)
    );
}

/// `--measure-rss` without `--headless` is a usage error.
#[test]
fn measure_rss_requires_headless() {
    let out = run_panel(&["--measure-rss"]);
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("--measure-rss requires --headless"), "{stderr}");
    assert!(out.stdout.is_empty());
}

/// The two one-shot modes are mutually exclusive.
#[test]
fn measure_rss_and_print_layout_are_mutually_exclusive() {
    let out = run_panel(&[
        "--headless",
        "--print-layout",
        "--width",
        "10",
        "--measure-rss",
    ]);
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("mutually exclusive"), "{stderr}");
}

/// A headless-only flag given with no active mode is a usage error (and now
/// mentions both modes).
#[test]
fn max_mib_without_a_mode_is_a_usage_error() {
    let out = run_panel(&["--max-mib", "5"]);
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("--max-mib is only supported with --print-layout or --measure-rss"),
        "{stderr}"
    );
}

/// The mode honours `-c`: a valid config is loaded (the `config OK` diagnostic
/// stays on stderr) and the measurement still runs.
#[test]
fn measure_rss_loads_a_valid_config() {
    let dir = std::env::temp_dir().join(format!("pengy-panel-it-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(format!("measure-{:?}.toml", std::thread::current().id()));
    std::fs::write(
        &path,
        r#"
[[widgets]]
type = "text"
section = "left"
text = "PengyOS"
"#,
    )
    .unwrap();

    let out = run_panel(&[
        "--headless",
        "--measure-rss",
        "--max-mib",
        "100000",
        "-c",
        path.to_str().unwrap(),
    ]);
    let _ = std::fs::remove_file(&path);
    assert!(
        out.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("config OK (1 widget(s))"), "{stderr}");
    assert!(String::from_utf8_lossy(&out.stdout).starts_with("idle-rss: "));
}

/// An invalid config is a data error even in measure-rss mode (exit 65).
#[test]
fn measure_rss_invalid_config_is_dataerr() {
    let dir = std::env::temp_dir().join(format!("pengy-panel-it-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(format!("measure-bad-{:?}.toml", std::thread::current().id()));
    // Inverted battery thresholds violate `critical < low`.
    std::fs::write(
        &path,
        r#"
[[widgets]]
type = "battery"
section = "right"
low = 5
critical = 25
"#,
    )
    .unwrap();

    let out = run_panel(&[
        "--headless",
        "--measure-rss",
        "-c",
        path.to_str().unwrap(),
    ]);
    let _ = std::fs::remove_file(&path);
    assert_eq!(out.status.code(), Some(65));
    assert!(String::from_utf8_lossy(&out.stdout).is_empty());
}
