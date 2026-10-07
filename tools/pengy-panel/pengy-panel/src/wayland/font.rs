//! A built-in, dependency-free 5×7 monospace bitmap font and its per-style
//! colors — the **font/cell mapping** half of the Wayland backend (task 11).
//!
//! The core [`widgets`] crate deliberately carries *no* fonts, colors, or
//! pixels: it only asks for a coarse [`widgets::Style`] and a width in
//! **text cells**. This module is where cells become pixels. Each cell is a
//! fixed-size box (`CELL_W`×`CELL_H` px); inside it, one glyph from the
//! 5×7 bitmap font is blitted. The bitmap covers every printable ASCII
//! character (`U+0020..=U+007E`) plus the handful of non-ASCII glyphs the
//! built-in widgets actually emit (the workspace bullets `●`/`◉` and the
//! battery arrows `↓`/`↑`); anything else falls back to a hollow box so an
//! unexpected character is still *visible* rather than silently dropped.
//!
//! This whole module is pure (no Wayland, no I/O) and therefore fully
//! unit-testable headlessly; only [`crate::wayland`] glues it to the
//! compositor.

use widgets::Style;

/// A 5×7 glyph stored as seven 5-bit rows, top row first, bit 4 = leftmost
/// column. `0` is blank.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Glyph([u8; 7]);

impl Glyph {
    /// True when the column `col` (0 = leftmost) in row `row` is set.
    pub fn bit(&self, row: u8, col: u8) -> bool {
        (self.0[row as usize] >> (4 - col as u32)) & 1 == 1
    }
}

/// The printable-ASCII font table, row-major: glyph `i` (where `i = cp - 0x20`)
/// occupies bytes `i*7 .. i*7+7`.
const FONT: [u8; 95 * 7] = [
    0, 0, 0, 0, 0, 0, 0, 8,
    8, 8, 8, 8, 0, 8, 21, 21,
    0, 0, 0, 0, 0, 12, 12, 31,
    12, 31, 12, 12, 4, 15, 20, 14,
    5, 30, 4, 24, 25, 2, 4, 8,
    19, 3, 12, 18, 20, 8, 21, 18,
    13, 4, 8, 0, 0, 0, 0, 0,
    2, 4, 8, 8, 8, 4, 2, 2,
    4, 2, 2, 2, 4, 2, 0, 10,
    21, 14, 21, 10, 0, 0, 4, 4,
    31, 4, 4, 0, 0, 0, 0, 0,
    10, 8, 16, 0, 0, 0, 31, 0,
    0, 0, 0, 0, 0, 0, 0, 8,
    8, 1, 2, 2, 4, 8, 8, 16,
    14, 17, 19, 21, 25, 17, 14, 4,
    12, 4, 4, 4, 4, 14, 14, 17,
    1, 6, 8, 16, 31, 30, 1, 1,
    14, 1, 1, 30, 2, 6, 10, 18,
    31, 2, 2, 31, 16, 30, 1, 1,
    17, 14, 6, 8, 16, 30, 17, 17,
    14, 31, 1, 2, 4, 8, 8, 8,
    14, 17, 17, 14, 17, 17, 14, 14,
    17, 17, 15, 1, 2, 12, 0, 8,
    8, 0, 8, 8, 0, 0, 8, 8,
    0, 10, 8, 16, 2, 4, 8, 16,
    8, 4, 2, 0, 0, 31, 0, 31,
    0, 0, 8, 4, 2, 1, 2, 4,
    8, 14, 17, 1, 6, 4, 0, 4,
    14, 17, 23, 21, 22, 16, 14, 14,
    17, 17, 31, 17, 17, 17, 30, 17,
    17, 30, 17, 17, 30, 14, 17, 16,
    16, 16, 17, 14, 28, 18, 17, 17,
    17, 18, 28, 31, 16, 16, 30, 16,
    16, 31, 31, 16, 16, 30, 16, 16,
    16, 14, 17, 16, 23, 17, 17, 15,
    17, 17, 17, 31, 17, 17, 17, 14,
    4, 4, 4, 4, 4, 14, 7, 2,
    2, 2, 2, 18, 12, 17, 18, 20,
    24, 20, 18, 17, 16, 16, 16, 16,
    16, 16, 31, 17, 27, 21, 21, 17,
    17, 17, 17, 17, 25, 21, 19, 17,
    17, 14, 17, 17, 17, 17, 17, 14,
    30, 17, 17, 30, 16, 16, 16, 14,
    17, 17, 17, 21, 18, 13, 30, 17,
    17, 30, 20, 18, 17, 15, 16, 16,
    14, 1, 1, 30, 31, 4, 4, 4,
    4, 4, 4, 17, 17, 17, 17, 17,
    17, 14, 17, 17, 17, 17, 17, 10,
    4, 17, 17, 17, 21, 21, 27, 17,
    17, 17, 10, 4, 10, 17, 17, 17,
    17, 10, 4, 4, 4, 4, 31, 1,
    2, 4, 8, 16, 31, 14, 8, 8,
    8, 8, 8, 14, 16, 8, 8, 4,
    2, 2, 1, 14, 2, 2, 2, 2,
    2, 14, 4, 10, 21, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 31,
    8, 16, 0, 0, 0, 0, 0, 0,
    0, 14, 1, 15, 17, 15, 16, 16,
    22, 25, 17, 17, 30, 0, 0, 14,
    16, 16, 17, 14, 1, 1, 13, 19,
    17, 17, 15, 0, 0, 14, 17, 31,
    16, 14, 6, 9, 8, 28, 8, 8,
    8, 0, 15, 17, 17, 15, 1, 14,
    16, 16, 22, 25, 17, 17, 17, 4,
    0, 12, 4, 4, 4, 14, 2, 0,
    6, 2, 2, 18, 12, 16, 16, 18,
    20, 24, 20, 18, 12, 4, 4, 4,
    4, 4, 14, 0, 0, 26, 21, 21,
    17, 17, 0, 0, 22, 25, 17, 17,
    17, 0, 0, 14, 17, 17, 17, 14,
    0, 22, 25, 17, 22, 16, 16, 0,
    13, 19, 1, 13, 0, 1, 0, 22,
    25, 16, 16, 16, 16, 0, 15, 16,
    14, 1, 30, 0, 8, 8, 28, 8,
    8, 9, 6, 0, 17, 17, 17, 19,
    17, 15, 0, 17, 17, 17, 17, 10,
    4, 0, 17, 17, 21, 21, 21, 10,
    0, 17, 10, 4, 10, 17, 0, 0,
    17, 17, 15, 1, 14, 1, 0, 31,
    2, 4, 8, 16, 31, 2, 4, 4,
    8, 4, 4, 2, 4, 4, 4, 0,
    4, 4, 4, 8, 4, 4, 2, 4,
    4, 8, 0, 0, 8, 21, 0, 0,
    0,
];

/// Fallback for a character with no dedicated glyph: a hollow box.
const FALLBACK: Glyph = Glyph([0x1C, 0x22, 0x22, 0x22, 0x22, 0x22, 0x1C]);

/// A fixed-pitch, dependency-free bitmap font with per-style colors.
///
/// All geometry is in pixels; all of it is public so the rasterizer and the
/// tests can reason about exact coordinates.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CellFont {
    /// Pixels per cell, horizontally.
    pub cell_w: u32,
    /// Pixels per cell, vertically.
    pub cell_h: u32,
    /// The opaque background color (ARGB, alpha in the top byte).
    pub background: u32,
}

impl Default for CellFont {
    fn default() -> Self {
        CellFont::new()
    }
}

impl CellFont {
    /// The default font: a 6×8 cell holding a 5×7 glyph, Catppuccin-Mocha
    /// dark background.
    pub fn new() -> Self {
        CellFont {
            cell_w: 6,
            cell_h: 8,
            background: 0xFF_1E_1E_2E,
        }
    }

    /// The 5×7 glyph for `cp`, or `None` (only for the space, which is blank).
    ///
    /// Printable ASCII maps into [`FONT`]; the panel's own non-ASCII glyphs map
    /// into a small dedicated table; everything else is `None` and the
    /// rasterizer draws [`FALLBACK`] instead.
    pub fn glyph(&self, cp: char) -> Option<Glyph> {
        let c = cp as u32;
        if (0x20..=0x7E).contains(&c) {
            let i = (c - 0x20) as usize;
            let rows = [
                FONT[i * 7],
                FONT[i * 7 + 1],
                FONT[i * 7 + 2],
                FONT[i * 7 + 3],
                FONT[i * 7 + 4],
                FONT[i * 7 + 5],
                FONT[i * 7 + 6],
            ];
            return Some(Glyph(rows));
        }
        match cp {
            // Workspace bullets / battery arrows the built-in widgets emit.
            '●' => Some(Glyph([0x3C, 0x7E, 0x7E, 0x7E, 0x7E, 0x7E, 0x3C])),
            '◉' => Some(Glyph([0x3C, 0x7E, 0x7E, 0x3C, 0x7E, 0x7E, 0x3C])),
            '↓' => Some(Glyph([0x18, 0x3C, 0x7E, 0x18, 0x18, 0x18, 0x00])),
            '↑' => Some(Glyph([0x18, 0x18, 0x18, 0x7E, 0x3C, 0x18, 0x00])),
            _ => None,
        }
    }

    /// The foreground color (ARGB) for a coarse style tag.
    pub fn color(&self, style: Style) -> u32 {
        match style {
            Style::Normal => 0xFF_CD_D6_F4,
            Style::Emphasized => 0xFF_89_B4_FA,
            Style::Warning => 0xFF_F9_E2_AF,
            Style::Critical => 0xFF_F3_8B_A8,
        }
    }

    /// The fallback glyph used for characters with no dedicated bitmap.
    pub fn fallback(&self) -> Glyph {
        FALLBACK
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cell_metrics_are_fixed_pitch() {
        let f = CellFont::new();
        assert_eq!(f.cell_w, 6);
        assert_eq!(f.cell_h, 8);
        assert_eq!(f.background, 0xFF_1E_1E_2E);
        assert_eq!(CellFont::default(), f);
    }

    #[test]
    fn space_is_the_only_blank_glyph() {
        let f = CellFont::new();
        assert!(f.glyph(' ').is_some());
        let sp = f.glyph(' ').unwrap();
        for r in 0..7 {
            for c in 0..5 {
                assert!(!sp.bit(r, c), "space has an inked pixel at {r},{c}");
            }
        }
    }

    #[test]
    fn every_printable_ascii_has_a_nonblank_glyph() {
        let f = CellFont::new();
        for cp in 0x21..=0x7E_u32 {
            let g = f.glyph(char::from_u32(cp).unwrap()).expect("ascii glyph present");
            // At least one inked pixel (a real glyph, not blank).
            let inked = (0..7)
                .flat_map(|r| (0..5).map(move |c| g.bit(r, c)))
                .any(|b| b);
            assert!(inked, "ASCII {cp} rendered blank");
        }
    }

    #[test]
    fn known_glyph_shapes_are_stable() {
        let f = CellFont::new();
        // 'X' is the classic symmetric test glyph.
        let x = f.glyph('X').unwrap();
        assert!(x.bit(0, 0) && x.bit(0, 4)); // top corners
        assert!(!x.bit(3, 0) && !x.bit(3, 4)); // middle row sides are empty
        assert!(x.bit(3, 2)); // center dot
        // '0' has inked top and bottom.
        let zero = f.glyph('0').unwrap();
        assert!(zero.bit(0, 1) && zero.bit(6, 1));
    }

    #[test]
    fn special_panel_glyphs_are_present() {
        let f = CellFont::new();
        for cp in ['●', '◉', '↓', '↑'] {
            let g = f.glyph(cp).expect("panel glyph present");
            let inked = (0..7).flat_map(|r| (0..5).map(move |c| g.bit(r, c))).any(|b| b);
            assert!(inked, "{cp} rendered blank");
        }
    }

    #[test]
    fn unknown_chars_fall_back_to_a_box() {
        let f = CellFont::new();
        assert!(f.glyph('☃').is_none()); // snowman has no glyph
        // The fallback box is non-blank and hollow in the center.
        let fb = f.fallback();
        assert!(fb.bit(0, 1));
        assert!(!fb.bit(3, 2));
    }

    #[test]
    fn styles_map_to_distinct_colors() {
        let f = CellFont::new();
        let n = f.color(Style::Normal);
        let c = f.color(Style::Critical);
        assert_ne!(n, c);
        // All are fully opaque (alpha 0xFF).
        for s in [
            Style::Normal,
            Style::Emphasized,
            Style::Warning,
            Style::Critical,
        ] {
            assert_eq!(f.color(s) >> 24, 0xFF);
        }
    }
}
