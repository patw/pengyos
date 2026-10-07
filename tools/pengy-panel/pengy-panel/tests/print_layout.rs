//! End-to-end tests for the `--headless --print-layout` mode (task 7).
//!
//! These exercise the *real binary* the way CI will: launch it, pass the
//! one-shot flags, and assert on the exact text line on stdout plus the exit
//! code. No compositor, display, network, or hardware is needed; the
//! fixtures (config written to a temp dir, state-fed widgets only, pinned
//! epoch) keep the goldens host-independent.

use std::process::Command;

fn panel_bin() -> &'static str {
    env!("CARGO_BIN_EXE_pengy-panel")
}

/// Write `toml` to a temp file and return its path (deleted on drop).
struct TempConfig {
    path: std::path::PathBuf,
}

fn temp_config(toml: &str) -> TempConfig {
    let dir = std::env::temp_dir().join(format!("pengy-panel-it-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(format!("panel-{}.toml", uuid_like()));
    std::fs::write(&path, toml).unwrap();
    TempConfig { path }
}

/// Cheap unique-enough suffix (thread id + counter); tests in one process.
fn uuid_like() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static N: AtomicU64 = AtomicU64::new(0);
    let thread = std::thread::current().id();
    format!(
        "{:?}",
        (thread, N.fetch_add(1, Ordering::Relaxed))
    )
}

impl Drop for TempConfig {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

fn run_panel(args: &[&str]) -> std::process::Output {
    Command::new(panel_bin())
        .args(args)
        .output()
        .unwrap_or_else(|e| panic!("failed to launch {}: {e}", panel_bin()))
}

/// Hand-computed, width 20, gap 1: Left `PengyOS`(7) at x=0; the
/// `{{missing}}` template substitutes to the empty string (zero width →
/// excluded, and there is no right section); Center `12`(2) centered in the
/// free space [7,20) (length 13) → x = 7 + (13-2)/2 = 12 (integer division).
/// Line: `PengyOS     12      ` (7 + 5 spaces + `12` + 6 spaces = 20).
const STATE_FED_CONFIG: &str = r#"
[[widgets]]
type = "text"
section = "left"
text = "PengyOS"

[[widgets]]
type = "workspaces"
section = "center"
count = 2

[[widgets]]
type = "template"
section = "right"
text = "{{missing}}"
"#;

#[test]
fn print_layout_prints_the_golden_line_and_exits_zero() {
    let config = temp_config(STATE_FED_CONFIG);
    let out = run_panel(&[
        "--headless",
        "--print-layout",
        "--width",
        "20",
        "--epoch",
        "1000000000",
        "-c",
        config.path.to_str().unwrap(),
    ]);
    assert!(out.status.success(), "stderr: {}", String::from_utf8_lossy(&out.stderr));
    let stdout = String::from_utf8_lossy(&out.stdout);
    // Exactly one line of exactly `width` characters, plus the newline.
    assert_eq!(stdout, "PengyOS     12      \n");
    // The config-load diagnostic stays on stderr; stdout is machine data.
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("config OK (3 widget(s))"),
        "stderr: {}",
        String::from_utf8_lossy(&out.stderr)
    );
}

#[test]
fn print_layout_without_config_prints_an_all_spaces_bar() {
    let out = run_panel(&["--headless", "--print-layout", "--width", "7"]);
    assert!(out.status.success());
    assert_eq!(String::from_utf8_lossy(&out.stdout), "       \n");
}

#[test]
fn print_layout_missing_headless_is_a_usage_error() {
    let out = run_panel(&["--print-layout", "--width", "5"]);
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("--print-layout requires --headless"), "{stderr}");
    assert!(stderr.contains("usage: pengy-panel"), "{stderr}");
    assert!(out.stdout.is_empty(), "stdout must stay clean on errors");
}

#[test]
fn print_layout_missing_width_is_a_usage_error() {
    let out = run_panel(&["--headless", "--print-layout"]);
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("--print-layout requires --width <CELLS>"), "{stderr}");
}

#[test]
fn headless_only_flag_is_a_usage_error() {
    let out = run_panel(&["--headless"]);
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("--headless is only supported with --print-layout"),
        "{stderr}"
    );
}

#[test]
fn non_numeric_width_is_a_usage_error() {
    let out = run_panel(&["--headless", "--print-layout", "--width", "wide"]);
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("--width must be an integer (got `wide`)"), "{stderr}");
}

#[test]
fn invalid_config_is_dataerr_even_in_print_layout_mode() {
    // A battery with the thresholds inverted violates `critical < low`.
    let config = temp_config(
        r#"
[[widgets]]
type = "battery"
section = "right"
low = 5
critical = 25
"#,
    );
    let out = run_panel(&[
        "--headless",
        "--print-layout",
        "--width",
        "10",
        "-c",
        config.path.to_str().unwrap(),
    ]);
    assert_eq!(out.status.code(), Some(65));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("invalid config"), "{stderr}");
    assert!(out.stdout.is_empty());
}

#[test]
fn missing_config_file_is_dataerr() {
    let out = run_panel(&[
        "--headless",
        "--print-layout",
        "--width",
        "10",
        "-c",
        "/nonexistent/pengy-panel/definitely-missing.toml",
    ]);
    assert_eq!(out.status.code(), Some(65));
    assert!(out.stdout.is_empty());
}

#[test]
fn ordinary_run_is_still_the_not_ready_path() {
    // The one-shot mode must not swallow the normal (still-unimplemented)
    // run path: without the flags the binary exits 69 as before.
    let out = run_panel(&[]);
    assert_eq!(out.status.code(), Some(69));
    assert!(out.stdout.is_empty());
}
