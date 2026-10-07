//! The [`LayoutEngine`]: pure geometry that arranges widget renders into a bar.
//!
//! This module is the heart of the headless-testable core: it takes the
//! [`crate::widget::WidgetRender`]s produced by a tick and computes **exact cell
//! coordinates** for every surviving widget. No fonts, no pixels, no backend —
//! the binary later maps cells to coordinates using the selected font.
//!
//! # The rules (all deterministic, all tested)
//!
//! Given a bar `width` (text cells) and a list of renders, in input order:
//!
//! 1. **Grouping.** Renders are grouped into the three sections by
//!    [`crate::widget::Section`], preserving input order *within* each section.
//! 2. **Zero-width exclusion.** A render whose
//!    [`crate::widget::WidgetRender::width_cells`] is `0` is excluded from the
//!    layout entirely: it draws nothing and would otherwise reserve gap space
//!    around an invisible widget.
//! 3. **Alignment.**
//!    - The **left** section is packed from cell `0` left-to-right.
//!    - The **right** section is packed right-to-left against cell `width - 1`.
//!    - The **center** section is centered in the free space *between* the end
//!      of the left section and the start of the right section (x-offset =
//!      `(free - center_width) / 2`, integer division). Centering in the free
//!      space — rather than in the whole window — guarantees that when
//!      everything fits, no widget ever overlaps another.
//! 4. **Gaps.** Adjacent widgets *within* a section are separated by
//!    [`LayoutConfig::gap_cells`] cells (default `1`). There is no gap between
//!    sections — the centering rule keeps the sections apart on its own.
//! 5. **Overflow.** If the total required width (all widgets plus intra-section
//!    gaps) exceeds `width`, whole widgets are dropped — **outside-in toward
//!    the center** — until everything fits. Drop order: alternately the
//!    outermost left widget and the outermost right widget (a tie goes to the
//!    left); once both outer sections are empty, center widgets are dropped in
//!    input order until the remaining center fits. The engine never truncates a
//!    widget; it either places it in full or drops it.

use crate::widget::{Section, WidgetRender};

/// Tunable parameters for the [`LayoutEngine`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LayoutConfig {
    /// Gap, in text cells, between two adjacent widgets in the same section.
    pub gap_cells: u32,
}

impl Default for LayoutConfig {
    fn default() -> Self {
        LayoutConfig { gap_cells: 1 }
    }
}

/// Where one widget ends up on the bar.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PlacedWidget {
    /// The index of the render in the original input slice.
    pub index: usize,
    /// The start cell, counted from the left edge (0-based).
    pub x: u32,
    /// The cells the widget occupies.
    pub width: u32,
}

/// The fully computed layout of one frame of the bar.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Layout {
    /// The bar width (text cells) the layout was computed against.
    pub width: u32,
    /// Placed widgets of the left section, in display order.
    pub left: Vec<PlacedWidget>,
    /// Placed widgets of the center section, in display order.
    pub center: Vec<PlacedWidget>,
    /// Placed widgets of the right section, in display order.
    pub right: Vec<PlacedWidget>,
    /// Input indices of dropped widgets, in the order they were dropped
    /// (zero-width exclusions first, then overflow drops).
    pub dropped: Vec<usize>,
}

impl Layout {
    /// True when every non-empty render was placed.
    pub fn fits_all(&self) -> bool {
        self.dropped.is_empty()
    }

    /// The section layout for a given section.
    pub fn section(&self, s: Section) -> &[PlacedWidget] {
        match s {
            Section::Left => &self.left,
            Section::Center => &self.center,
            Section::Right => &self.right,
        }
    }
}

/// Arranges [`WidgetRender`]s into a [`Layout`] over a fixed bar width.
///
/// The engine is a value type with a [`LayoutConfig`]; it holds no per-frame
/// state, so calling [`LayoutEngine::layout`] is a pure function of its
/// arguments. Re-layout on every widget tick is the intended use.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayoutEngine {
    config: LayoutConfig,
}

impl Default for LayoutEngine {
    fn default() -> Self {
        Self::new(LayoutConfig::default())
    }
}

impl LayoutEngine {
    /// Build an engine with the given configuration.
    pub fn new(config: LayoutConfig) -> Self {
        LayoutEngine { config }
    }

    /// The configured gap between adjacent widgets in a section.
    pub fn gap_cells(&self) -> u32 {
        self.config.gap_cells
    }

    /// Compute the layout for `renders` on a bar `width` cells wide.
    pub fn layout(&self, width: u32, renders: &[WidgetRender]) -> Layout {
        let gap = self.config.gap_cells;

        // (1) Group indices by section, preserving input order.
        let mut left: Vec<usize> = Vec::new();
        let mut center: Vec<usize> = Vec::new();
        let mut right: Vec<usize> = Vec::new();
        for (i, r) in renders.iter().enumerate() {
            match r.section() {
                Section::Left => left.push(i),
                Section::Center => center.push(i),
                Section::Right => right.push(i),
            }
        }

        // (2) Zero-width exclusion: nothing to draw, no gap space to reserve.
        // Recorded in `dropped` *before* any overflow drop so diagnostics can
        // tell "nothing to draw" apart from "no room".
        let mut dropped: Vec<usize> = Vec::new();
        for list in [&mut left, &mut center, &mut right] {
            list.retain(|&i| {
                let keep = renders[i].width_cells() != 0;
                if !keep {
                    dropped.push(i);
                }
                keep
            });
        }

        // (5) Overflow: drop whole widgets outside-in until the total fits.
        let total = |ls: &[usize], cs: &[usize], rs: &[usize]| -> u64 {
            let mut t = 0u64;
            for group in [ls, cs, rs] {
                let n = group.len() as u64;
                if n == 0 {
                    continue;
                }
                t += group
                    .iter()
                    .map(|&i| renders[i].width_cells() as u64)
                    .sum::<u64>();
                if n > 1 {
                    t += (n - 1) * gap as u64;
                }
            }
            t
        };

        let mut dropped_from_left: u64 = 0;
        let mut dropped_from_right: u64 = 0;
        while total(&left, &center, &right) > width as u64 {
            if left.is_empty() && right.is_empty() {
                // Only the center can still overflow; drop it in input order.
                if center.is_empty() {
                    break;
                }
                dropped.push(center.remove(0));
                continue;
            }
            // Prefer the side that has dropped fewer so far; a tie goes left;
            // an empty side yields to the other.
            if !left.is_empty() && (right.is_empty() || dropped_from_left <= dropped_from_right)
            {
                dropped.push(left.remove(0));
                dropped_from_left += 1;
            } else {
                let idx = *right.last().expect("right non-empty in this branch");
                right.pop();
                dropped.push(idx);
                dropped_from_right += 1;
            }
        }

        // (3)+(4) Pack each section into exact cell positions.
        let mut placed_left = Vec::with_capacity(left.len());
        let mut x: u32 = 0;
        for &i in &left {
            let w = renders[i].width_cells();
            placed_left.push(PlacedWidget { index: i, x, width: w });
            x = x.saturating_add(w).saturating_add(gap);
        }
        let left_end = if left.is_empty() {
            0u32
        } else {
            x - gap
        };

        // Packed right-to-left, then reversed so the output is in display
        // (input) order, like the other sections.
        let mut placed_right = Vec::with_capacity(right.len());
        let mut rx: u32 = width;
        for &i in right.iter().rev() {
            let w = renders[i].width_cells();
            rx = rx.saturating_sub(w);
            placed_right.push(PlacedWidget { index: i, x: rx, width: w });
            rx = rx.saturating_sub(gap);
        }
        let right_start = if right.is_empty() {
            width
        } else {
            rx + gap
        };
        placed_right.reverse();

        // Center in the free space between the sections.
        let mut placed_center = Vec::with_capacity(center.len());
        if !center.is_empty() {
            let center_total: u32 = center
                .iter()
                .map(|&i| renders[i].width_cells())
                .sum::<u32>()
                .saturating_add((center.len() - 1) as u32 * gap);
            // By construction (total <= width) `right_start >= left_end`, so no
            // underflow is reachable; saturating math keeps that honest.
            let free = right_start.saturating_sub(left_end);
            let mut cx = left_end + free.saturating_sub(center_total) / 2;
            for &i in &center {
                let w = renders[i].width_cells();
                placed_center.push(PlacedWidget { index: i, x: cx, width: w });
                cx = cx.saturating_add(w).saturating_add(gap);
            }
        }

        Layout {
            width,
            left: placed_left,
            center: placed_center,
            right: placed_right,
            dropped,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::widget::TextRun;

    /// Build a render with a section, one run of `text`, and an optional
    /// width override (defaults to the content width).
    fn render(section: Section, text: &str) -> WidgetRender {
        WidgetRender::new(section, vec![TextRun::new(text, crate::widget::Style::Normal)])
    }

    fn render_w(section: Section, text: &str, width: u32) -> WidgetRender {
        render(section, text).with_width(width)
    }

    /// Assert the two geometric invariants that must hold for every layout:
    /// nothing spills off the bar, and nothing overlaps.
    fn check_invariants(layout: &Layout) {
        for s in [Section::Left, Section::Center, Section::Right] {
            for w in layout.section(s) {
                assert!(
                    (w.x as u64 + w.width as u64) <= layout.width as u64,
                    "widget {} (w={}) at x={} overflows bar of {}: {layout:?}",
                    w.index,
                    w.width,
                    w.x,
                    layout.width
                );
            }
        }
        let mut all: Vec<(u32, u32, usize)> = layout
            .section(Section::Left)
            .iter()
            .chain(layout.section(Section::Center))
            .chain(layout.section(Section::Right))
            .map(|w| (w.x, w.width, w.index))
            .collect();
        all.sort();
        for pair in all.windows(2) {
            let (x0, w0, i0) = pair[0];
            let (x1, w1, i1) = pair[1];
            assert!(
                (x0 + w0) <= x1,
                "widgets {} and {} overlap: [{}..{}] vs [{}..{}]: {layout:?}",
                i0,
                i1,
                x0,
                x0 + w0,
                x1,
                x1 + w1
            );
        }
    }

    // -- basic alignment ----------------------------------------------------

    #[test]
    fn empty_input_layouts_to_empty_bar() {
        let l = LayoutEngine::default().layout(100, &[]);
        assert!(l.left.is_empty() && l.center.is_empty() && l.right.is_empty());
        assert!(l.dropped.is_empty());
        assert!(l.fits_all());
        assert_eq!(l.width, 100);
    }

    #[test]
    fn single_left_widget_starts_at_zero() {
        let l = LayoutEngine::default().layout(100, &[render(Section::Left, "workspaces")]);
        assert_eq!(l.left, vec![PlacedWidget { index: 0, x: 0, width: 10 }]);
        assert!(l.right.is_empty() && l.center.is_empty());
        assert!(l.fits_all());
        check_invariants(&l);
    }

    #[test]
    fn single_right_widget_is_flush_with_the_right_edge() {
        let l = LayoutEngine::default().layout(100, &[render(Section::Right, "12:34")]);
        assert_eq!(
            l.right,
            vec![PlacedWidget { index: 0, x: 95, width: 5 }]
        );
        check_invariants(&l);
    }

    #[test]
    fn left_section_preserves_input_order() {
        let renders = vec![
            render(Section::Left, "one"),
            render(Section::Left, "two"),
            render(Section::Left, "three"),
        ];
        let l = LayoutEngine::default().layout(100, &renders);
        assert_eq!(
            l.left,
            vec![
                PlacedWidget { index: 0, x: 0, width: 3 },
                PlacedWidget { index: 1, x: 4, width: 3 },
                PlacedWidget { index: 2, x: 8, width: 5 },
            ]
        );
        check_invariants(&l);
    }

    #[test]
    fn right_section_preserves_input_order_but_is_right_justified() {
        let renders = vec![
            render(Section::Right, "one"),
            render(Section::Right, "two"),
            render(Section::Right, "three"),
        ];
        let l = LayoutEngine::default().layout(100, &renders);
        // "one" "two" "three" = 3+3+5 content + 2 gaps = 13, flush at 87.
        assert_eq!(
            l.right,
            vec![
                PlacedWidget { index: 0, x: 87, width: 3 },
                PlacedWidget { index: 1, x: 91, width: 3 },
                PlacedWidget { index: 2, x: 95, width: 5 },
            ]
        );
        check_invariants(&l);
    }

    #[test]
    fn widgets_are_grouped_by_section_regardless_of_input_order() {
        // Interleaved input: the engine must regroup by section.
        let renders = vec![
            render(Section::Right, "r1"),
            render(Section::Left, "l1"),
            render(Section::Center, "c1"),
            render(Section::Left, "l2"),
        ];
        let l = LayoutEngine::default().layout(100, &renders);
        assert_eq!(l.left, vec![
            PlacedWidget { index: 1, x: 0, width: 2 },
            PlacedWidget { index: 3, x: 3, width: 2 },
        ]);
        assert_eq!(l.right, vec![PlacedWidget { index: 0, x: 98, width: 2 }]);
        assert_eq!(l.center.len(), 1);
        assert_eq!(l.center[0].index, 2);
        check_invariants(&l);
    }

    // -- centering ----------------------------------------------------------

    #[test]
    fn lone_center_widget_is_centered_in_the_window() {
        // "hello" (5) in a 20-cell window: free = 20, offset = (20-5)/2 = 7.
        let l = LayoutEngine::default().layout(20, &[render(Section::Center, "hello")]);
        assert_eq!(l.center, vec![PlacedWidget { index: 0, x: 7, width: 5 }]);
        check_invariants(&l);
    }

    #[test]
    fn center_is_centered_between_left_and_right_not_in_the_window() {
        // Left: "ll" (2) at [0..2]; Right: "rrrr" (4) at [16..20] of a 20 bar.
        // Free space = [2..16) = 14; center "cc" (2) at 2 + (14-2)/2 = 8.
        // (Window-centering would put it at 9.)
        let renders = vec![
            render(Section::Left, "ll"),
            render(Section::Center, "cc"),
            render(Section::Right, "rrrr"),
        ];
        let l = LayoutEngine::default().layout(20, &renders);
        assert_eq!(l.center, vec![PlacedWidget { index: 1, x: 8, width: 2 }]);
        check_invariants(&l);
    }

    #[test]
    fn center_offset_uses_integer_division_truncating() {
        // Bar 21, center width 5, no side widgets: (21-5)/2 = 8 (13/2 truncated).
        let l = LayoutEngine::default().layout(21, &[render(Section::Center, "hello")]);
        assert_eq!(l.center[0].x, 8);
        check_invariants(&l);
    }

    #[test]
    fn center_fills_free_space_exactly_when_tight() {
        // One widget per section: no intra-section gaps, so the total is the
        // bare content sum 3 + 4 + 3 = 10, an exact fit in a 10-cell bar.
        let renders = vec![
            render(Section::Left, "abc"),
            render(Section::Center, "wxyz"),
            render(Section::Right, "123"),
        ];
        let l = LayoutEngine::default().layout(10, &renders);
        assert!(l.fits_all());
        // free = 7 - 3 = 4 = center width, so the offset is 0: [3..7).
        assert_eq!(l.center, vec![PlacedWidget { index: 1, x: 3, width: 4 }]);
        check_invariants(&l);
    }

    #[test]
    fn multiple_center_widgets_center_as_a_group_and_preserve_order() {
        // Center: "a" "b" = 3 cells total in a 10 bar: offset (10-3)/2 = 3.
        let renders = vec![
            render(Section::Center, "a"),
            render(Section::Center, "b"),
        ];
        let l = LayoutEngine::default().layout(10, &renders);
        assert_eq!(
            l.center,
            vec![
                PlacedWidget { index: 0, x: 3, width: 1 },
                PlacedWidget { index: 1, x: 5, width: 1 },
            ]
        );
        check_invariants(&l);
    }

    // -- gaps ---------------------------------------------------------------

    #[test]
    fn custom_gap_is_respected_within_sections() {
        let engine = LayoutEngine::new(LayoutConfig { gap_cells: 3 });
        let renders = vec![
            render(Section::Left, "a"),
            render(Section::Left, "b"),
        ];
        let l = engine.layout(100, &renders);
        assert_eq!(
            l.left,
            vec![
                PlacedWidget { index: 0, x: 0, width: 1 },
                PlacedWidget { index: 1, x: 4, width: 1 },
            ]
        );
        check_invariants(&l);
    }

    #[test]
    fn zero_gap_puts_widgets_adjacent() {
        let engine = LayoutEngine::new(LayoutConfig { gap_cells: 0 });
        let renders = vec![render(Section::Left, "ab"), render(Section::Left, "cd")];
        let l = engine.layout(10, &renders);
        assert_eq!(
            l.left,
            vec![
                PlacedWidget { index: 0, x: 0, width: 2 },
                PlacedWidget { index: 1, x: 2, width: 2 },
            ]
        );
        check_invariants(&l);
    }

    #[test]
    fn gaps_between_sections_are_not_doubled() {
        // No left, no right: two center widgets in a 10 bar with gap 1 => group
        // width 2+1+2 = 5, offset (10-5)/2 = 2. There must be no phantom section
        // gap shifting the group.
        let renders = vec![render(Section::Center, "aa"), render(Section::Center, "bb")];
        let l = LayoutEngine::default().layout(10, &renders);
        assert_eq!(l.center[0].x, 2);
        assert_eq!(l.center[1].x, 5);
        check_invariants(&l);
    }

    // -- zero-width widgets ---------------------------------------------------

    #[test]
    fn zero_width_widget_is_excluded_and_counts_in_dropped() {
        // An empty render (no runs) has width 0 and must not reserve a gap.
        let renders = vec![
            render(Section::Left, "abc"),
            WidgetRender::new(Section::Left, vec![]), // width 0
        ];
        let l = LayoutEngine::default().layout(100, &renders);
        assert_eq!(l.left, vec![PlacedWidget { index: 0, x: 0, width: 3 }]);
        assert_eq!(l.dropped, vec![1]);
        check_invariants(&l);
    }

    #[test]
    fn explicit_zero_width_override_is_excluded() {
        let renders = vec![
            render(Section::Right, "xy"),
            render_w(Section::Right, "zzzz", 0), // overridden to 0
        ];
        let l = LayoutEngine::default().layout(10, &renders);
        assert_eq!(l.right, vec![PlacedWidget { index: 0, x: 8, width: 2 }]);
        assert_eq!(l.dropped, vec![1]);
        check_invariants(&l);
    }

    #[test]
    fn all_zero_width_widgets_yield_an_empty_layout() {
        let renders = vec![
            WidgetRender::new(Section::Left, vec![]),
            WidgetRender::new(Section::Center, vec![]),
            WidgetRender::new(Section::Right, vec![]),
        ];
        let l = LayoutEngine::default().layout(50, &renders);
        assert!(l.left.is_empty() && l.center.is_empty() && l.right.is_empty());
        assert_eq!(l.dropped, vec![0, 1, 2]);
        check_invariants(&l);
    }

    // -- overflow -------------------------------------------------------------

    #[test]
    fn overflow_drops_outside_in_alternating_left_and_right() {
        // Left: "L1"(2) "L2"(2); Right: "R1"(2) "R2"(2); bar 8.
        // Total = (2+2+1) + (2+2+1) = 10 > 8.
        // Drop L1 (tie -> left): left = 2, right = 5, total 7 <= 8 -> done.
        let renders = vec![
            render(Section::Left, "L1"),
            render(Section::Left, "L2"),
            render(Section::Right, "R1"),
            render(Section::Right, "R2"),
        ];
        let l = LayoutEngine::default().layout(8, &renders);
        assert_eq!(l.dropped, vec![0]);
        assert_eq!(l.left, vec![PlacedWidget { index: 1, x: 0, width: 2 }]);
        assert_eq!(
            l.right,
            vec![
                PlacedWidget { index: 2, x: 3, width: 2 },
                PlacedWidget { index: 3, x: 6, width: 2 },
            ]
        );
        check_invariants(&l);
    }

    #[test]
    fn overflow_drops_tie_goes_to_left() {
        // Two drops needed; the first tie goes to left.
        // Left: "L1"(2) "L2"(2) "L3"(2); Right: "R1"(2); bar 7.
        // Total = 8+1 = 9 > 7 -> drop L1: 2+1+2 + 2 = 7? left [L2,L3]=5, right=2
        // total 7 <= 7. Only one drop needed.
        // Use bar 6: total 9 > 6 -> drop L1 (tie -> left): total 7 > 6
        // -> drop R1: total 5 <= 6.
        let renders = vec![
            render(Section::Left, "L1"),
            render(Section::Left, "L2"),
            render(Section::Left, "L3"),
            render(Section::Right, "R1"),
        ];
        let l = LayoutEngine::default().layout(6, &renders);
        assert_eq!(l.dropped, vec![0, 3]);
        assert_eq!(
            l.left,
            vec![
                PlacedWidget { index: 1, x: 0, width: 2 },
                PlacedWidget { index: 2, x: 3, width: 2 },
            ]
        );
        check_invariants(&l);
    }

    #[test]
    fn overflow_keeps_alternating_when_counts_differ() {
        // Left: 3 widgets, Right: 1 widget, all 2 cells wide; bar 5.
        // Total = (2+2+2 + 2 gaps) + 2 = 10 > 5.
        // Drop A (tie -> left): total 2+2+1 + 2 = 7 > 5.
        // Drop R (right dropped 0 < left's 1): total 5 <= 5.
        let renders = vec![
            render(Section::Left, "AA"),
            render(Section::Left, "BB"),
            render(Section::Left, "CC"),
            render(Section::Right, "RR"),
        ];
        let l = LayoutEngine::default().layout(5, &renders);
        assert_eq!(l.dropped, vec![0, 3]);
        check_invariants(&l);
    }

    #[test]
    fn overflow_center_is_dropped_last_and_in_input_order() {
        // Only a center section that cannot fit.
        let renders = vec![
            render(Section::Center, "abcde"), // 5
            render(Section::Center, "fghij"), // 5
        ];
        // Bar 6: total 11 > 6 -> drop first center (5+0 = 5 <= 6).
        let l = LayoutEngine::default().layout(6, &renders);
        assert_eq!(l.dropped, vec![0]);
        assert_eq!(l.center, vec![PlacedWidget { index: 1, x: 0, width: 5 }]);
        check_invariants(&l);

        // Bar 4: even the last widget alone does not fit -> drop everything.
        let l = LayoutEngine::default().layout(4, &renders);
        assert_eq!(l.dropped, vec![0, 1]);
        assert!(l.center.is_empty());
        check_invariants(&l);
    }

    #[test]
    fn overflow_never_truncates_and_may_drop_everything() {
        let big = render(Section::Left, "0123456789"); // 10 cells
        let l = LayoutEngine::default().layout(3, &[big]);
        assert!(l.left.is_empty());
        assert_eq!(l.dropped, vec![0]);
        check_invariants(&l);
    }

    #[test]
    fn overflow_considers_gaps_in_the_fit_test() {
        // Two 1-cell widgets: content 2, but with gap 1 the total is 3.
        // A 2-wide bar must therefore drop one, not squeeze them together.
        let renders = vec![render(Section::Left, "a"), render(Section::Left, "b")];
        let l = LayoutEngine::default().layout(2, &renders);
        assert_eq!(l.dropped, vec![0]);
        assert_eq!(l.left, vec![PlacedWidget { index: 1, x: 0, width: 1 }]);
        check_invariants(&l);
    }

    #[test]
    fn exact_fit_never_drops() {
        // One widget per section: no intra-section gaps, total = 2 + 1 + 2 = 5,
        // exactly the bar width. Nothing may be dropped.
        let renders = vec![
            render(Section::Left, "ab"),
            render(Section::Center, "c"),
            render(Section::Right, "de"),
        ];
        let l = LayoutEngine::default().layout(5, &renders);
        assert!(l.fits_all());
        assert_eq!(l.left[0].x, 0); // [0..2)
        assert_eq!(l.right[0].x, 3); // [3..5)
        // free = 3 - 2 = 1 = center width: centered at x = 2, no slack.
        assert_eq!(l.center[0].x, 2);
        check_invariants(&l);
    }

    // -- determinism & config --------------------------------------------------

    #[test]
    fn layout_is_deterministic_for_equal_inputs() {
        let engine = LayoutEngine::default();
        let renders = vec![
            render(Section::Left, "L1"),
            render(Section::Center, "C1"),
            render(Section::Right, "R1"),
            render(Section::Right, "R2"),
        ];
        let a = engine.layout(15, &renders);
        let b = engine.layout(15, &renders);
        assert_eq!(a, b);
    }

    #[test]
    fn engine_and_config_are_cloneable_and_eq() {
        let engine = LayoutEngine::new(LayoutConfig { gap_cells: 4 });
        assert_eq!(engine.clone(), engine);
        assert_eq!(engine.gap_cells(), 4);
        let l = engine.layout(10, &[render(Section::Left, "ab")]);
        assert_eq!(l.clone(), l);
    }

    #[test]
    fn default_config_has_one_cell_gap() {
        assert_eq!(LayoutConfig::default().gap_cells, 1);
        assert_eq!(LayoutEngine::default().gap_cells(), 1);
    }

    // -- randomized invariants (fixed seed, no network, no nondeterminism) -----

    #[test]
    fn invariants_hold_over_a_sweep_of_widths() {
        // A small LCG (no rand crate — the core stays dependency-free) drives a
        // pseudo-random mix of sections, widths and bar sizes. Whatever it
        // throws at the engine, the two geometric invariants must hold.
        let mut state: u64 = 0x9E37_79B9_7F4A_7C15;
        let mut next = || {
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            (state >> 33) as u32
        };

        let engine = LayoutEngine::default();
        let sections = [Section::Left, Section::Center, Section::Right];
        for trial in 0..2000 {
            let mut renders = Vec::new();
            let n = (next() % 8) as usize;
            for _ in 0..n {
                let section = sections[(next() % 3) as usize];
                let w = next() % 12; // 0..12 cells (0 tests exclusion)
                let text = "x".repeat(w as usize);
                renders.push(render(section, &text));
            }
            let width = next() % 30;
            let layout = engine.layout(width, &renders);

            check_invariants(&layout);
            // Dropped indices must be unique and cover exactly the non-placed
            // non-zero-width renders.
            let mut d: Vec<usize> = layout.dropped.clone();
            d.sort();
            d.dedup();
            assert_eq!(
                d.len(),
                layout.dropped.len(),
                "dropped indices must be unique"
            );
            let placed: std::collections::HashSet<usize> = layout
                .left
                .iter()
                .chain(&layout.center)
                .chain(&layout.right)
                .map(|p| p.index)
                .collect();
            for (i, r) in renders.iter().enumerate() {
                if r.width_cells() == 0 {
                    assert!(layout.dropped.contains(&i), "zero-width {i} must be dropped");
                    assert!(!placed.contains(&i));
                } else {
                    let is_placed = placed.contains(&i);
                    let is_dropped = layout.dropped.contains(&i);
                    assert!(is_placed ^ is_dropped, "render {i} must be placed xor dropped");
                }
            }
            let _ = trial;
        }
    }
}
