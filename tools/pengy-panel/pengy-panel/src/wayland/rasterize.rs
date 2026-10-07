//! Pure **frame → pixel-buffer** rasterization (task 11).
//!
//! Given a [`Frame`] (cells + runs) and a [`CellFont`], produce an ARGB8888
//! buffer the Wayland backend can hand to a shared-memory `wl_buffer`. This is
//! the other half of the "font/cell mapping": [`font`](super::font) maps one
//! cell to pixels; this maps a *whole frame* to a *whole buffer*.
//!
//! It is pure — no Wayland, no I/O — and therefore golden-testable headlessly,
//! which is exactly the part of the backend the headless test suite can assert
//! on (the socket plumbing itself is `PENGYOS_IT`-gated).

use crate::backend::Frame;
use crate::wayland::font::{CellFont, Glyph};
use crate::wayland::scale::Scale;
use widgets::Style;

/// A rasterized ARGB8888 buffer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Raster {
    /// Buffer width in pixels.
    pub width_px: u32,
    /// Buffer height in pixels.
    pub height_px: u32,
    /// Row stride in **bytes** (`width_px * 4`).
    pub stride_px: u32,
    /// Pixels, **bottom-up**, row-major: `pixels[y * stride_px + x]` with
    /// `y = 0` the bottom row. Each pixel is ARGB8888 with alpha in the top
    /// byte.
    pub pixels: Vec<u32>,
}

impl Raster {
    /// The pixel at (x, y), `y = 0` at the bottom, or `None` if out of bounds.
    pub fn at(&self, x: u32, y: u32) -> Option<u32> {
        if x >= self.width_px || y >= self.height_px {
            return None;
        }
        self.pixels.get((y * self.width_px + x) as usize).copied()
    }

    /// The pixel buffer as a little-endian byte stream, ready to back a
    /// `wl_shm` pool. Each 32-bit ARGB pixel is stored in native (LE) order —
    /// the format Wayland expects for `ARGB8888`.
    pub fn as_bytes(&self) -> Vec<u8> {
        self.pixels.iter().flat_map(|p| p.to_ne_bytes()).collect()
    }
}

/// Rasterize `frame` into an ARGB8888 buffer that is `height_cells` cells tall.
///
/// The buffer is `frame.width` cells wide (`width_cells * cell_w` px) and
/// `height_cells` cells tall. Text sits in the **bottom** cell row; the rows
/// above are pure background. Each character is blitted with its run's style
/// color and clipped to the buffer — a character whose cell runs past the
/// right edge is clipped, never a panic.
pub fn rasterize_frame(frame: &Frame, font: &CellFont, height_cells: u32) -> Raster {
    // The whole-cell-width, 1:1 case of the scaled rasterizer.
    rasterize_frame_scaled(
        frame,
        font,
        height_cells,
        frame.width.saturating_mul(font.cell_w),
        &Scale::ONE,
    )
}

/// Convenience: the color the rasterizer uses for a style (thin wrapper over
/// the font, so callers/tests can ask "what color is `Warning`?" in one place).
#[allow(unused)]
pub fn style_color(font: &CellFont, style: Style) -> u32 {
    font.color(style)
}

/// Rasterize `frame` into a buffer of exactly `width_px` pixels wide and
/// `height_cells` cells tall, drawing the placed widgets by their absolute cell
/// columns (clipped to `width_px`).
///
/// This is what the live backend uses: the compositor decides the bar's real
/// pixel width (it may not be a multiple of the cell width), so the buffer must
/// match it exactly rather than rounding to whole cells. The pure
/// [`rasterize_frame`] sizes the buffer to whole cells and is what the
/// headless paths use.
pub fn rasterize_frame_at(frame: &Frame, font: &CellFont, height_cells: u32, width_px: u32) -> Raster {
    // The explicit-width, 1:1 case of the scaled rasterizer.
    rasterize_frame_scaled(frame, font, height_cells, width_px, &Scale::ONE)
}

/// Rasterize `frame` at output `scale` (task 14) into an ARGB8888 buffer of
/// exactly `width_px` pixels wide and `height_cells` cells tall, with each
/// cell `scale`-scaled (`cell_w` → `scale.scaled_px(cell_w)` px, same for the
/// height).
///
/// This is the single drawing primitive the live backend uses: at 1:1 it is
/// byte-identical to the legacy [`rasterize_frame`]/[`rasterize_frame_at`],
/// and at non-1:1 it scales *crisply* — every inked 5×7 font pixel is blitted
/// to the (round-to-nearest) integer pixel rectangle its logical position maps
/// to, so integer scales (2:1) are exact block scaling and fractional scales
/// (1.5) stretch proportionally, with no intermediate blur and no floats.
///
/// The cell's margins scale too: the font cell is `cell_w`×`cell_h` logical
/// pixels holding a 5×7 glyph (right column and bottom row are margin), and
/// each logical pixel — glyph or margin — maps to `[k*cw/cell_w,
/// (k+1)*cw/cell_w)` pixels of the scaled cell. Text still sits in the bottom
/// cell row; every blit is clipped to the buffer, so a cell running past the
/// right edge is clipped, never a panic.
pub fn rasterize_frame_scaled(
    frame: &Frame,
    font: &CellFont,
    height_cells: u32,
    width_px: u32,
    scale: &Scale,
) -> Raster {
    let height_cells = height_cells.max(1);
    let cell_h_px = scale.scaled_px(font.cell_h);
    let height_px = cell_h_px.saturating_mul(height_cells);
    let stride_px = width_px.saturating_mul(4);
    let mut pixels = vec![font.background; (width_px as usize).saturating_mul(height_px as usize)];
    // The top edge (from the top of the buffer) of the bottom cell row.
    let bottom_cell_top = height_px.saturating_sub(cell_h_px);

    for painted in &frame.placed {
        let mut cell = painted.x as u64;
        for run in &painted.runs {
            let color = font.color(run.style);
            for cp in run.text.chars() {
                blit_scaled(&mut pixels, width_px, height_px, font, scale, cell, bottom_cell_top, cp, color);
                cell += 1;
            }
        }
    }

    Raster {
        width_px,
        height_px,
        stride_px,
        pixels,
    }
}

/// Blit one character at absolute cell column `cell` into the bottom cell row
/// (whose top edge is `bottom_cell_top`, measured from the top of the
/// buffer), at `scale`, in `color`.
///
/// Pure fixed-point mapping: logical font pixel `(gc, gr)` of the `cell_w`×
/// `cell_h` cell maps to the pixel rectangle
/// `[ (cell*cell_w+gc)*cw/cell_w, (cell*cell_w+gc+1)*cw/cell_w )` horizontally
/// and the same over the cell height vertically, where `cw`/`ch` are the
/// scaled cell dimensions. At 1:1 this is the legacy 1-pixel-per-font-pixel
/// blit; all products are `u64` and every loop is clamped to the buffer.
fn blit_scaled(
    pixels: &mut [u32],
    width_px: u32,
    height_px: u32,
    font: &CellFont,
    scale: &Scale,
    cell: u64,
    bottom_cell_top: u32,
    cp: char,
    color: u32,
) {
    let glyph: Glyph = font.glyph(cp).unwrap_or_else(|| font.fallback());
    let cw = scale.scaled_px(font.cell_w) as u64;
    let ch = scale.scaled_px(font.cell_h) as u64;
    let cw_font = font.cell_w as u64;
    let ch_font = font.cell_h as u64;
    let w = width_px as u64;
    let h = height_px as u64;
    for gr in 0..7u8 {
        for gc in 0..5u8 {
            if !glyph.bit(gr, gc) {
                continue;
            }
            let x0 = (cell.saturating_mul(cw_font) + gc as u64) * cw / cw_font;
            let x1 = (cell.saturating_mul(cw_font) + gc as u64 + 1) * cw / cw_font;
            let y0 = bottom_cell_top as u64 + (gr as u64 * ch) / ch_font;
            let y1 = bottom_cell_top as u64 + ((gr as u64 + 1) * ch) / ch_font;
            let x_end = x1.min(w);
            let y_end = y1.min(h);
            for y in y0..y_end {
                for x in x0..x_end {
                    // The raster stores rows bottom-up (y = 0 is the bottom row).
                    let y_bottom = h - 1 - y;
                    let idx = (y_bottom * w + x) as usize;
                    if idx < pixels.len() {
                        pixels[idx] = color;
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::{PaintedWidget, Frame};
    use widgets::TextRun;

    fn one_run(frame_width: u32, x: u32, text: &str, style: Style) -> Frame {
        let mut frame = Frame::new(frame_width);
        frame.placed.push(PaintedWidget {
            x,
            width: text.chars().count() as u32,
            runs: vec![TextRun::new(text, style)],
        });
        frame
    }

    #[test]
    fn buffer_dimensions_come_from_cells_and_font() {
        let font = CellFont::new();
        let frame = Frame::new(10);
        let r = rasterize_frame(&frame, &font, 1);
        assert_eq!(r.width_px, 10 * 6);
        assert_eq!(r.height_px, 8);
        assert_eq!(r.stride_px, 10 * 6 * 4);
        assert_eq!(r.pixels.len(), (10 * 6) as usize * 8);
        // Two cells tall doubles the height only.
        let r2 = rasterize_frame(&frame, &font, 2);
        assert_eq!(r2.height_px, 16);
        assert_eq!(r2.width_px, 10 * 6);
    }

    #[test]
    fn an_empty_frame_is_all_background() {
        let font = CellFont::new();
        let r = rasterize_frame(&Frame::new(5), &font, 1);
        for y in 0..r.height_px {
            for x in 0..r.width_px {
                assert_eq!(r.at(x, y), Some(font.background));
            }
        }
    }

    #[test]
    fn a_glyph_inks_its_cell_and_only_its_cell() {
        let font = CellFont::new();
        // "A" occupies cell 0 → pixel columns 0..6.
        let frame = one_run(3, 0, "A", Style::Normal);
        let r = rasterize_frame(&frame, &font, 1);

        // The 'A' has inked pixels somewhere in its cell.
        let inked = (0..6)
            .flat_map(|x| (0..8).map(move |y| (x, y)))
            .any(|(x, y)| r.at(x, y) == Some(font.color(Style::Normal)));
        assert!(inked, "no 'A' pixels found in its cell");

        // Cells 1 and 2 (columns 6..18) are untouched background.
        for x in 6..18 {
            for y in 0..8 {
                assert_eq!(r.at(x, y), Some(font.background));
            }
        }
    }

    #[test]
    fn glyph_sits_in_the_bottom_cell_row_for_a_taller_buffer() {
        let font = CellFont::new();
        let frame = one_run(1, 0, "A", Style::Normal);
        let r = rasterize_frame(&frame, &font, 3); // 24 px tall

        // The topmost inked pixel must be at row (from bottom) height-8 ..
        let top_ink = (0..r.height_px)
            .rev()
            .find(|y| (0..6).any(|x| r.at(x, *y) == Some(font.color(Style::Normal))))
            .unwrap();
        // glyph_top = height - cell_h = 16; so the top inked row (from bottom)
        // is at most 16+something<8 → ≤ 23, but the top 8 rows (y=16..23 from
        // bottom is the TOP cell) must be background.
        assert!(top_ink < 16, "ink leaked above the bottom cell row");
        for y in 16..24 {
            for x in 0..6 {
                assert_eq!(r.at(x, y), Some(font.background));
            }
        }
    }

    #[test]
    fn style_changes_the_glyph_color() {
        let font = CellFont::new();
        let normal = rasterize_frame(&one_run(1, 0, "A", Style::Normal), &font, 1);
        let critical = rasterize_frame(&one_run(1, 0, "A", Style::Critical), &font, 1);
        // The set of inked pixels is identical; only the color differs.
        let normal_ink: Vec<(u32, u32)> = (0..6)
            .flat_map(|x| (0..8).map(move |y| (x, y)))
            .filter(|&(x, y)| normal.at(x, y) == Some(font.color(Style::Normal)))
            .collect();
        assert!(!normal_ink.is_empty());
        for (x, y) in &normal_ink {
            assert_eq!(critical.at(*x, *y), Some(font.color(Style::Critical)));
        }
        assert_ne!(normal.color_probe(), critical.color_probe());
    }

    impl Raster {
        fn color_probe(&self) -> u32 {
            // First inked pixel (bottom-left of 'A' region) as a color fingerprint.
            (0..self.width_px)
                .flat_map(|x| (0..self.height_px).map(move |y| (x, y)))
                .find(|&(x, y)| self.at(x, y) != Some(CellFont::new().background))
                .map(|(x, y)| self.at(x, y).unwrap())
                .unwrap()
        }
    }

    #[test]
    fn multiple_runs_share_a_widget_span_in_order() {
        let font = CellFont::new();
        let mut frame = Frame::new(4);
        frame.placed.push(PaintedWidget {
            x: 0,
            width: 3,
            runs: vec![
                TextRun::new("A", Style::Normal),
                TextRun::new("B", Style::Warning),
                TextRun::new("C", Style::Critical),
            ],
        });
        let r = rasterize_frame(&frame, &font, 1);
        // 'A' in cell 0 → normal color present in columns 0..6.
        let cell_ink = |x0, x1| -> Vec<u32> {
            (x0..x1)
                .flat_map(|x| (0..8).map(move |y| (x, y)))
                .filter_map(|(x, y)| r.at(x, y))
                .filter(|c| *c != font.background)
                .collect()
        };
        assert!(cell_ink(0, 6).iter().all(|&c| c == font.color(Style::Normal)));
        assert!(cell_ink(6, 12).iter().all(|&c| c == font.color(Style::Warning)));
        assert!(cell_ink(12, 18).iter().all(|&c| c == font.color(Style::Critical)));
        assert!(!cell_ink(0, 6).is_empty());
        assert!(!cell_ink(6, 12).is_empty());
        assert!(!cell_ink(12, 18).is_empty());
    }

    #[test]
    fn a_char_at_the_right_edge_is_clipped_not_panicked() {
        let font = CellFont::new();
        // Cell 2 on a 3-cell bar: columns 12..18 exactly fill the 18-px bar.
        let frame = one_run(3, 2, "M", Style::Normal);
        let r = rasterize_frame(&frame, &font, 1);
        assert_eq!(r.width_px, 18);
        // Nothing past the right edge exists; at() is None there.
        assert!(r.at(18, 0).is_none());
        // The glyph is present.
        let inked = (12..18)
            .flat_map(|x| (0..8).map(move |y| (x, y)))
            .any(|(x, y)| r.at(x, y) == Some(font.color(Style::Normal)));
        assert!(inked);
    }

    #[test]
    fn out_of_range_lookups_are_none() {
        let font = CellFont::new();
        let r = rasterize_frame(&Frame::new(1), &font, 1);
        assert!(r.at(1000, 0).is_none());
        assert!(r.at(0, 1000).is_none());
    }

    #[test]
    fn unknown_glyph_uses_the_fallback_box() {
        let font = CellFont::new();
        let frame = one_run(1, 0, "☃", Style::Normal);
        let r = rasterize_frame(&frame, &font, 1);
        // A snowman has no glyph → the fallback box is drawn (non-blank,
        // in the normal color).
        let inked = (0..6)
            .flat_map(|x| (0..8).map(move |y| (x, y)))
            .any(|(x, y)| r.at(x, y) == Some(font.color(Style::Normal)));
        assert!(inked);
    }

    // -----------------------------------------------------------------
    // Scaled rasterization (task 14)
    // -----------------------------------------------------------------

    #[test]
    fn scale_one_is_byte_identical_to_the_legacy_rasterizers() {
        // The refactor of `rasterize_frame`/`rasterize_frame_at` onto
        // `rasterize_frame_scaled` must not move a single pixel at 1:1.
        let font = CellFont::new();
        let mut frame = Frame::new(4);
        frame.placed.push(PaintedWidget {
            x: 1,
            width: 3,
            runs: vec![
                TextRun::new("AB", Style::Normal),
                TextRun::new("C", Style::Critical),
            ],
        });
        for width_px in [18u32, 24, 30] {
            let legacy = rasterize_frame_at(&frame, &font, 2, width_px);
            let scaled = rasterize_frame_scaled(&frame, &font, 2, width_px, &Scale::ONE);
            assert_eq!(
                legacy, scaled,
                "scale-1 raster must equal the legacy one at width {width_px}"
            );
        }
        let whole = rasterize_frame(&frame, &font, 2);
        assert_eq!(
            whole,
            rasterize_frame_scaled(&frame, &font, 2, whole.width_px, &Scale::ONE)
        );
    }

    #[test]
    fn integer_2x_scale_is_exact_block_scaling() {
        // At 2:1 every 1:1 pixel must map to its 2×2 block, no more, no less.
        let font = CellFont::new();
        let frame = one_run(3, 0, "AX", Style::Normal);
        let r1 = rasterize_frame(&frame, &font, 1); // 18 × 8
        let r2 = rasterize_frame_scaled(&frame, &font, 1, 18 * 2, &Scale::resolve(1, Some(512)));
        assert_eq!((r2.width_px, r2.height_px), (36, 16));
        // Every pixel of the 2x buffer equals the 1x pixel under its 2×2 block.
        for y in 0..16u32 {
            for x in 0..36u32 {
                assert_eq!(
                    r2.at(x, y),
                    r1.at(x / 2, y / 2),
                    "2:1 pixel ({x},{y}) must be a block copy of 1:1 ({}, {})",
                    x / 2,
                    y / 2
                );
            }
        }
        // Ink count is exactly 4x (no smearing, no loss).
        let ink = |r: &Raster| {
            (0..r.height_px)
                .flat_map(|y| (0..r.width_px).map(move |x| (x, y)))
                .filter(|&(x, y)| r.at(x, y) == Some(font.color(Style::Normal)))
                .count()
        };
        assert_eq!(ink(&r2), 4 * ink(&r1));
        assert!(ink(&r1) > 0, "fixture must contain ink");
    }

    #[test]
    fn fractional_15x_scale_keeps_glyphs_and_margins_proportionally() {
        // 1.5: a 6×8 cell becomes 9×12. The right margin (font col 5) covers
        // px 7.5..9 and the bottom margin (font row 7) covers the bottom 1.5
        // px — both must stay background; the glyph must fill the rest.
        let font = CellFont::new();
        let frame = one_run(1, 0, "A", Style::Normal);
        let scale = Scale::resolve(1, Some(384)); // 1.5
        let r = rasterize_frame_scaled(&frame, &font, 1, 9, &scale);
        assert_eq!((r.width_px, r.height_px), (9, 12));
        // The whole right-margin column (x=8) is background.
        for y in 0..12 {
            assert_eq!(r.at(8, y), Some(font.background), "x=8 is scaled margin");
        }
        // The bottom margin rows (y=0..2 from the bottom) are all background.
        for x in 0..9 {
            assert_eq!(r.at(x, 0), Some(font.background));
            assert_eq!(r.at(x, 1), Some(font.background));
        }
        // The glyph itself inked proportionally wide: ink reaches into columns
        // 0..=6 of every glyph row band, i.e. well past the 1:1 0..=4 span.
        let color = font.color(Style::Normal);
        let ink_cols: Vec<u32> = (0..9)
            .filter(|x| (0..12).any(|y| r.at(*x, y) == Some(color)))
            .collect();
        assert!(ink_cols.first() == Some(&0), "glyph starts at col 0");
        assert!(
            ink_cols.last() >= Some(&6),
            "1.5x glyph must stretch into the extra columns, got {ink_cols:?}"
        );
    }

    #[test]
    fn scaled_raster_height_grows_with_the_scale_for_multicell_bars() {
        let font = CellFont::new();
        let frame = Frame::new(4);
        let s15 = Scale::resolve(1, Some(384));
        let r1 = rasterize_frame(&frame, &font, 3);
        let r15 = rasterize_frame_scaled(&frame, &font, 3, 4 * 6, &s15);
        assert_eq!(r1.height_px, 24);
        assert_eq!(r15.height_px, 36, "3 cells * 8 px * 1.5 = 36");
        // Glyphs still sit in the BOTTOM cell row at 1.5: the top 24 px
        // (2 scaled cells) are pure background.
        let top_blank = (0..r15.height_px - 24)
            .flat_map(|y_top| (0..r15.width_px).map(move |x| (x, r15.height_px - 1 - y_top)))
            .all(|(x, y)| r15.at(x, y) == Some(font.background));
        assert!(top_blank, "ink leaked above the bottom cell row");
    }

    #[test]
    fn scaled_raster_clips_a_cell_running_past_the_right_edge() {
        let font = CellFont::new();
        // Widget in cell 2 of a 2-cell bar → entirely past the edge: no panic,
        // no ink, no resize of the buffer.
        let frame = one_run(2, 2, "MM", Style::Warning);
        let s2 = Scale::resolve(1, Some(512));
        let r = rasterize_frame_scaled(&frame, &font, 1, 12, &s2);
        assert_eq!((r.width_px, r.height_px), (12, 16));
        let color = font.color(Style::Warning);
        for y in 0..16 {
            for x in 0..12 {
                assert_ne!(r.at(x, y), Some(color), "off-bar cell must not ink");
            }
        }
        // A cell *straddling* the edge inks only up to the edge: a 14-px bar
        // at 2x holds cell 0 fully (cols 0..12) plus 2 px of cell 1 (12..14).
        let frame2 = one_run(2, 1, "M", Style::Warning);
        let r2 = rasterize_frame_scaled(&frame2, &font, 1, 14, &s2);
        // Column 13 is inside cell 1's first scaled glyph column (12..14).
        let col13 = (0..16).any(|y| r2.at(13, y) == Some(color));
        assert!(col13, "cell 1 must ink its visible 2 px (cols 12..14)");
        // Nothing exists at or past the 14-px edge.
        assert!(r2.at(14, 0).is_none());
        // Cell 0's scaled margin column (font col 5 → px 10..12 at 2x) stays
        // background — the margin scales too.
        let margin_blank = (0..16).all(|y| r2.at(11, y) == Some(font.background));
        assert!(margin_blank, "cell 0's right margin must stay background");
    }
}
