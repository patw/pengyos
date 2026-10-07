//! End-to-end tests for the `--measure-rss-live` mode (the live-compositor
//! idle RSS).
//!
//! The mode runs the panel's *real* Wayland backend to steady state against a
//! live compositor and samples the process's resident set size, so the parts
//! that actually need a compositor are `PENGYOS_IT=1`-gated and skip otherwise.
//! The flag-combination rules and the config-load path, however, run **before**
//! any backend is touched, so those are asserted unconditionally — deterministic
//! on any machine, no compositor needed:
//!
//! - `--measure-rss-live --headless` is a contradiction (exit 2);
//! - `--width`/`--epoch` are rejected for the live mode (exit 2);
//! - two one-shot modes together are mutually exclusive (exit 2);
//! - an invalid `-c` config is a data error (exit 65) even in the live mode.

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

fn is_it_gated() -> bool {
    std::env::var("PENGYOS_IT").ok().as_deref() == Some("1")
}

/// Parse the measured KiB out of the `live-idle-rss: <KIB> KiB (...)` line.
fn measured_kib(stdout: &str) -> u64 {
    let line = stdout.lines().next().expect("one stdout line");
    let after_prefix = line
        .strip_prefix("live-idle-rss: ")
        .expect("line starts with `live-idle-rss: `");
    after_prefix
        .split_whitespace()
        .next()
        .expect("a KiB number follows the prefix")
        .parse::<u64>()
        .expect("KiB parses as a number")
}

/// `--measure-rss-live` is a *live* mode (it needs a compositor), so it is a
/// contradiction to pair it with `--headless` (exit 2).
#[test]
fn live_rss_incompatible_with_headless() {
    let out = run_panel(&["--measure-rss-live", "--headless"]);
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("--measure-rss-live requires a compositor and cannot be used with --headless"),
        "{stderr}"
    );
    assert!(out.stdout.is_empty());
}

/// `--width` and `--epoch` are headless-only: the live width comes from the
/// compositor and the clock is live, so both are rejected (exit 2).
#[test]
fn live_rss_rejects_width_and_epoch() {
    for args in [
        vec!["--measure-rss-live", "--width", "10"],
        vec!["--measure-rss-live", "--epoch", "5"],
    ] {
        let out = run_panel(&args);
        assert_eq!(out.status.code(), Some(2), "{args:?}");
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(
            stderr.contains("is not supported with --measure-rss-live"),
            "{stderr}"
        );
        assert!(out.stdout.is_empty());
    }
}

/// Two one-shot modes together are mutually exclusive (exit 2), including the
/// live mode against either headless one.
#[test]
fn live_rss_is_mutually_exclusive_with_the_other_modes() {
    for args in [
        vec!["--measure-rss-live", "--print-layout", "--width", "5"],
        vec!["--measure-rss-live", "--measure-rss", "--headless"],
    ] {
        let out = run_panel(&args);
        assert_eq!(out.status.code(), Some(2), "{args:?}");
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("mutually exclusive"), "{stderr}");
    }
}

/// An invalid `-c` config is a data error (exit 65) even in the live mode:
/// config loading runs before any backend is touched, so this is deterministic
/// and needs no compositor.
#[test]
fn live_rss_invalid_config_is_dataerr() {
    let dir = std::env::temp_dir().join(format!("pengy-panel-it-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(format!("live-bad-{:?}.toml", std::thread::current().id()));
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

    let out = run_panel(&["--measure-rss-live", "-c", path.to_str().unwrap()]);
    let _ = std::fs::remove_file(&path);
    assert_eq!(out.status.code(), Some(65));
    assert!(String::from_utf8_lossy(&out.stdout).is_empty());
}

/// The live measurement itself, against whatever compositor `WAYLAND_DISPLAY`
/// points at — `PENGYOS_IT=1`-gated. Two outcomes are both correct:
///
/// - a **working layer-shell compositor**: the mode reports a positive
///   resident-set-size number and exits 0 (with a huge limit);
/// - **no usable compositor** (headless CI, or a compositor without
///   layer-shell): a clean one-line diagnostic and exit 69.
///
/// It never asserts a specific MiB — the *recorded* number is a manual
/// release-build run, so the assertion is build- and machine-independent.
#[test]
fn live_rss_reports_a_number_or_reports_no_compositor() {
    if !is_it_gated() {
        eprintln!("skipping PENGYOS_IT-gated live-rss measurement test");
        return;
    }
    let out = run_panel(&["--measure-rss-live", "--max-mib", "100000"]);
    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    match out.status.code() {
        Some(0) => {
            assert!(stdout.starts_with("live-idle-rss: "), "{stdout}");
            assert!(stdout.contains(" OK"), "{stdout}");
            assert!(measured_kib(&stdout) > 0, "{stdout}");
        }
        Some(69) => {
            assert!(
                stderr.contains("cannot attach to a compositor"),
                "{stderr}"
            );
            assert!(stdout.is_empty(), "{stdout}");
        }
        other => panic!(
            "unexpected exit {other:?}\nstdout: {stdout}\nstderr: {stderr}"
        ),
    }
}

/// The gate: with a working compositor, a `0` MiB limit is exceeded by any
/// real process → `EXCEEDED` and exit 1. Without a usable compositor the mode
/// exits 69 first (the measurement never runs), which is also acceptable.
/// `PENGYOS_IT=1`-gated.
#[test]
fn live_rss_gate_exits_one_when_the_limit_is_zero() {
    if !is_it_gated() {
        eprintln!("skipping PENGYOS_IT-gated live-rss gate test");
        return;
    }
    let out = run_panel(&["--measure-rss-live", "--max-mib", "0"]);
    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    match out.status.code() {
        Some(1) => assert!(stdout.contains(" EXCEEDED"), "{stdout}"),
        Some(69) => {
            assert!(stderr.contains("cannot attach to a compositor"), "{stderr}");
        }
        other => panic!(
            "unexpected exit {other:?}\nstdout: {stdout}\nstderr: {stderr}"
        ),
    }
}
