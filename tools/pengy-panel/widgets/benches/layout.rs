//! Criterion benchmark for the [`widgets::LayoutEngine`] (T8).
//!
//! This is the measurement backing the "Layout recompute < 1 ms on 20 widgets"
//! target in `ARCHITECTURE.md`. Run it with:
//!
//! ```text
//! cargo bench -p widgets --bench layout
//! ```
//!
//! The workload comes from [`widgets::bench::workload`] — the same deterministic
//! generator the crate's unit tests exercise — so the number the bench reports
//! and the input the tests validate are identical. Results land in
//! `target/criterion/` for run-over-run comparison.
//!
//! Note: `cargo bench` compiles the crate in **release** mode (bench profile),
//! so these figures reflect the panel's steady-state cost, not a debug build.

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};
use widgets::bench::{self, STANDARD_BAR_WIDTH, STANDARD_WIDGET_COUNT};
use widgets::{LayoutConfig, LayoutEngine};

/// Benchmark `LayoutEngine::layout` across the cases that matter to the target:
/// the standard 20-widget panel, a small panel, a large panel (cost growth),
/// and an overflow-heavy case where the outside-in drop loop runs many times.
fn bench_layout(c: &mut Criterion) {
    let engine = LayoutEngine::new(LayoutConfig::default());
    let mut group = c.benchmark_group("layout");

    // Scale the widget count to show how re-layout cost grows. The middle point
    // is exactly the "< 1 ms on 20 widgets" target.
    for count in [4usize, STANDARD_WIDGET_COUNT, 40, 80] {
        let renders = bench::workload(count, 0xC0FFEE);
        group.throughput(Throughput::Elements(count as u64));
        group.bench_with_input(
            BenchmarkId::new("widgets", count),
            &renders,
            |b, renders| {
                b.iter(|| engine.layout(STANDARD_BAR_WIDTH, renders));
            },
        );
    }

    // Overflow-heavy: 40 wide widgets on a narrow 20-cell bar, so the
    // outside-in drop loop iterates many times before it fits. This is the
    // worst case for layout cost (a cramped bar with too many widgets).
    let narrow = bench::workload(40, 0xBEEF);
    group.throughput(Throughput::Elements(40));
    group.bench_with_input(
        BenchmarkId::new("overflow", "narrow-20"),
        &narrow,
        |b, renders| {
            b.iter(|| engine.layout(20, renders));
        },
    );

    group.finish();
}

criterion_group!(benches, bench_layout);
criterion_main!(benches);
