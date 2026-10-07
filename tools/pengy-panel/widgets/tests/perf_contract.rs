//! T8 perf-contract guard.
//!
//! The memory/perf subtask has two halves, and this test keeps both honest so
//! they cannot drift apart silently:
//!
//! 1. **"Document the idle RSS target."** `ARCHITECTURE.md` must state a
//!    concrete idle-RSS target and the layout-recompute target (the numbers the
//!    panel is held to), and it must no longer say the bench is only a future
//!    plan.
//! 2. **"Add a bench harness (criterion) for layout."** the `widgets` crate
//!    must actually declare the `criterion` dev-dependency and a `layout`
//!    bench target, and the measured widget count must match the documented
//!    one.
//!
//! This is a doc/manifest *guard*, not a behavioral test: it fails loudly the
//! moment the documented target or the bench wiring disappears.

use std::path::PathBuf;

/// The `widgets` crate directory (where `Cargo.toml` lives).
fn crate_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// The workspace root (where `ARCHITECTURE.md` lives), one level up.
fn workspace_root() -> PathBuf {
    crate_dir()
        .parent()
        .expect("widgets/ must live inside the workspace root")
        .to_path_buf()
}

fn read(path: &std::path::Path) -> String {
    std::fs::read_to_string(path).unwrap_or_else(|err| {
        panic!("perf contract: cannot read {}: {err}", path.display())
    })
}

#[test]
fn architecture_documents_the_idle_rss_target() {
    let arch = read(&workspace_root().join("ARCHITECTURE.md"));
    // The idle-RSS target must be stated concretely (a number + a unit), not
    // just the phrase "low memory".
    assert!(
        arch.contains("Idle RSS"),
        "ARCHITECTURE.md must state an 'Idle RSS' target; got:\n{arch}"
    );
    assert!(
        arch.contains("15 MiB"),
        "ARCHITECTURE.md must give the idle-RSS figure (15 MiB)"
    );
}

#[test]
fn architecture_documents_the_layout_recompute_target() {
    let arch = read(&workspace_root().join("ARCHITECTURE.md"));
    assert!(
        arch.contains("1 ms") && arch.contains("20 widgets"),
        "ARCHITECTURE.md must state the 'layout recompute < 1 ms on 20 widgets' target"
    );
}

#[test]
fn architecture_points_at_the_landed_bench_not_a_future_plan() {
    let arch = read(&workspace_root().join("ARCHITECTURE.md"));
    // Once the harness lands, the doc must reference the real bench (criterion +
    // `cargo bench`) rather than deferring it as a later task.
    assert!(
        arch.to_ascii_lowercase().contains("criterion"),
        "ARCHITECTURE.md must name the criterion bench harness"
    );
    assert!(
        arch.contains("cargo bench"),
        "ARCHITECTURE.md must document how to run the layout bench (`cargo bench`)"
    );
    // The old "until then ... documented targets, not measurements" wording
    // must be gone: the harness is real now.
    assert!(
        !arch.contains("documented targets, not measurements"),
        "ARCHITECTURE.md still claims the bench is only a documented target"
    );
}

#[test]
fn widgets_crate_declares_the_criterion_bench_harness() {
    let manifest = read(&crate_dir().join("Cargo.toml"));
    assert!(
        manifest.contains("criterion"),
        "widgets/Cargo.toml must declare the criterion dependency for the T8 bench"
    );
    assert!(
        manifest.contains("[[bench]]") && manifest.contains("name = \"layout\""),
        "widgets/Cargo.toml must declare a [[bench]] target named `layout`"
    );
    // The bench must use criterion's harness, not cargo's built-in one.
    assert!(
        manifest.contains("harness = false"),
        "the layout bench must set `harness = false` (criterion)"
    );
    // criterion is a *dev*-dependency: the runtime `[dependencies]` table must
    // stay empty so the headless-test boundary (and the idle-RSS target) holds.
    // Scan from the `[dependencies]` header to the next table header and collect
    // any non-comment entries — there must be none.
    let mut in_deps = false;
    let mut runtime_deps: Vec<&str> = Vec::new();
    for line in manifest.lines() {
        let t = line.trim();
        if t == "[dependencies]" {
            in_deps = true;
            continue;
        }
        if in_deps && t.starts_with('[') {
            in_deps = false; // reached the next table ([dev-dependencies], …)
        }
        if in_deps && !t.is_empty() && !t.starts_with('#') {
            runtime_deps.push(t);
        }
    }
    assert!(
        runtime_deps.is_empty(),
        "the headless-test boundary requires widgets' [dependencies] to stay empty; found {runtime_deps:?}"
    );
}

#[test]
fn the_benched_widget_count_matches_the_documented_target() {
    // The bench measures STANDARD_WIDGET_COUNT widgets; the target says "20
    // widgets". Keep them in lockstep so the number we bench is the number we
    // promised.
    let arch = read(&workspace_root().join("ARCHITECTURE.md"));
    assert!(arch.contains("20 widgets"));
    assert_eq!(
        widgets::bench::STANDARD_WIDGET_COUNT, 20,
        "the bench's standard widget count must equal the documented '20 widgets' target"
    );
}
