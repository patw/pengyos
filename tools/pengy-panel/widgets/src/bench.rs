//! A representative, deterministic layout workload for the T8 criterion bench.
//!
//! `cargo bench` builds a *separate* crate that links `widgets`, so the input
//! it measures must be produced by **public, pure, deterministic** code inside
//! this crate — not by throwaway code living only in `benches/layout.rs`. That
//! keeps the workload itself unit-testable (deterministic, correctly shaped,
//! valid layout input) and guarantees the bench and its tests measure the
//! *same* thing.
//!
//! This module is zero-dependency on purpose: the `widgets` crate keeps an
//! empty `[dependencies]` table (see `ARCHITECTURE.md`), so the workload is
//! generated with a small hand-rolled LCG rather than a `rand` crate.

use crate::widget::{Section, Style, TextRun, WidgetRender};

/// The widget count the "< 1 ms layout recompute" target in `ARCHITECTURE.md`
/// is stated against ("Layout recompute < 1 ms on 20 widgets"). The bench and
/// the perf doc-guard test both pin this number so the target and the
/// measurement cannot drift apart silently.
pub const STANDARD_WIDGET_COUNT: usize = 20;

/// A representative bar width in text cells (close to a 1080p-wide bar at a
/// ~12-px cell: 1920 / 16 ≈ 120). The bench lays out the standard workload
/// against this width.
pub const STANDARD_BAR_WIDTH: u32 = 120;

/// Build a deterministic, panel-shaped layout workload of `count` renders.
///
/// Two calls with the same `(count, seed)` return identical render lists — the
/// property the criterion bench and its tests rely on. The mix is
/// panel-realistic:
///
/// - the section of widget `i` is fixed to `i % 3` (Left, Center, Right) so a
///   standard workload populates all three sections;
/// - the width is drawn from a small LCG seeded by `seed` (2..=12 cells), and
///   roughly one in ten widgets is **zero-width** to exercise the
///   `LayoutEngine`'s zero-width exclusion path on every run.
///
/// Returns `WidgetRender`s with a single [`TextRun`] each; the text is just
/// repeated filler — the layout only ever reads the width, so the content
/// does not affect the measurement.
pub fn workload(count: usize, seed: u64) -> Vec<WidgetRender> {
    let mut state = seed.wrapping_add(0x9E37_79B9_7F4A_7C15);
    (0..count).map(|i| {
        let section = [Section::Left, Section::Center, Section::Right][i % 3];
        // Advance the LCG twice: one value for the "is this zero-width?" gate,
        // one for the width itself, so the two decisions are independent.
        let gate = lcg_next(&mut state);
        let width_roll = lcg_next(&mut state);
        // ~10% zero-width (excluded by the engine), the rest 2..=12 cells.
        let width: u32 = if gate % 10 == 0 {
            0
        } else {
            2 + (width_roll % 11)
        };
        let text = "w".repeat(width as usize);
        WidgetRender::new(section, vec![TextRun::new(text, Style::Normal)])
    }).collect()
}

/// One step of a 64-bit LCG (same constants as the layout invariant fuzz test),
/// returning a 32-bit value. Kept as a free function (not a closure) so it is
/// trivially reusable and the crate stays dependency-free.
fn lcg_next(state: &mut u64) -> u32 {
    *state = state
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    (*state >> 33) as u32
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::LayoutEngine;

    #[test]
    fn workload_is_deterministic_for_a_seed() {
        let a = workload(20, 0xC0FFEE);
        let b = workload(20, 0xC0FFEE);
        assert_eq!(a, b, "same (count, seed) must yield identical workloads");
    }

    #[test]
    fn workload_has_the_requested_count() {
        assert_eq!(workload(0, 1).len(), 0);
        assert_eq!(workload(1, 1).len(), 1);
        assert_eq!(workload(37, 1).len(), 37);
    }

    #[test]
    fn different_seeds_give_different_mixes() {
        let a = workload(40, 1);
        let b = workload(40, 2);
        assert_ne!(a, b, "distinct seeds must not yield identical workloads");
    }

    #[test]
    fn standard_workload_populates_all_three_sections() {
        let w = workload(STANDARD_WIDGET_COUNT, 1);
        // Sections are i % 3, so a 20-widget workload has all three.
        assert!(w.iter().any(|r| r.section() == Section::Left));
        assert!(w.iter().any(|r| r.section() == Section::Center));
        assert!(w.iter().any(|r| r.section() == Section::Right));
    }

    #[test]
    fn standard_workload_exercises_the_zero_width_path() {
        // Over 40 widgets the ~10% zero-width gate must hit at least once, so
        // the benchmark actually measures the exclusion branch, not just the
        // happy path.
        let w = workload(40, 1);
        assert!(w.iter().any(|r| r.width_cells() == 0));
        // And most widgets keep a plausible 2..=12 cell width.
        let non_empty: Vec<u32> = w.iter().map(|r| r.width_cells()).filter(|&x| x > 0).collect();
        assert!(!non_empty.is_empty());
        assert!(non_empty.iter().all(|&x| (2..=12).contains(&x)));
    }

    #[test]
    fn standard_constants_match_the_documented_target() {
        // ARCHITECTURE.md states "< 1 ms on 20 widgets"; pin the constant the
        // bench measures so code and doc cannot disagree silently.
        assert_eq!(STANDARD_WIDGET_COUNT, 20);
        assert_eq!(STANDARD_BAR_WIDTH, 120);
    }

    /// The workload must be *valid* layout input: laying it out against the
    /// standard bar width must preserve the two geometric invariants (nothing
    /// spills off the bar, nothing overlaps). This is the guard that the bench
    /// is measuring a well-formed panel, not garbage.
    #[test]
    fn layout_of_standard_workload_holds_invariants() {
        let engine = LayoutEngine::default();
        let w = workload(STANDARD_WIDGET_COUNT, 0xC0FFEE);
        let layout = engine.layout(STANDARD_BAR_WIDTH, &w);
        // Nothing off the bar.
        for s in [Section::Left, Section::Center, Section::Right] {
            for pw in layout.section(s) {
                assert!(pw.x as u64 + pw.width as u64 <= layout.width as u64);
            }
        }
        // Nothing overlaps: sort all placements and check adjacency.
        let mut all: Vec<(u32, u32)> = layout
            .section(Section::Left)
            .iter()
            .chain(layout.section(Section::Center))
            .chain(layout.section(Section::Right))
            .map(|pw| (pw.x, pw.width))
            .collect();
        all.sort();
        for pair in all.windows(2) {
            assert!(pair[0].0 + pair[0].1 <= pair[1].0, "overlap in bench workload layout");
        }
    }
}
