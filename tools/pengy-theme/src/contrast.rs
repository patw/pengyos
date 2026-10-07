//! WCAG relative contrast-ratio math for `fg`/`bg` color pairs.
//!
//! This module implements the WCAG 2.x *relative contrast ratio* between two
//! sRGB colors. It is the small, dependency-free core that [`validate`](crate::validate)
//! builds on: a theme is only valid when its key foreground/background pairs meet
//! a threshold ratio. The `contrast` CLI subcommand (a later subtask) exposes the
//! same function to humans.
//!
//! ## The math
//!
//! 1. Normalize each 8-bit sRGB channel to `[0, 1]`.
//! 2. **Linearize** each channel (undo the sRGB gamma):
//!    - if `c <= 0.03928`: `c_lin = c / 12.92`
//!    - else: `c_lin = ((c + 0.055) / 1.055) ^ 2.4`
//! 3. Compute the **relative luminance** `L = 0.2126·R + 0.7152·G + 0.0722·B`.
//! 4. The contrast ratio is `(L_lighter + 0.05) / (L_darker + 0.05)`, which is
//!    always `>= 1`.
//!
//! Reference values (WCAG 2.2, test suite): pure white on pure black is `21.0`,
//! and `#767676` on white is `4.54`.

use crate::manifest::Color;

/// WCAG AA threshold for normal text.
///
/// A `fg`/`bg` pair must meet *at least* this ratio to pass AA for normal-sized
/// text. This is the value [`validate`](crate::validate) enforces by default.
pub const WCAG_AA_NORMAL: f64 = 4.5;

/// WCAG AA threshold for large text and UI components / graphics.
///
/// A looser threshold (`3.0`) that applies to large text (>= 18pt, or >= 14pt
/// bold) and meaningful UI parts. [`validate`](crate::validate) does not require
/// this for the key pairs, but it is exported so callers can apply the large-text
/// rule where appropriate.
pub const WCAG_AA_LARGE: f64 = 3.0;

/// The sRGB linearization threshold at the low end (WCAG 2.x).
const SRGB_THRESHOLD: f64 = 0.03928;
/// The sRGB linearization divisor at the low end (WCAG 2.x).
const SRGB_DIVISOR: f64 = 12.92;
/// The sRGB linearization offset added to the channel at the high end
/// (WCAG 2.x): the numerator is `(channel + SRGB_OFFSET)`.
const SRGB_OFFSET: f64 = 0.055;
/// The sRGB linearization denominator at the high end (WCAG 2.x): the numerator
/// is divided by this (note: `1.055`, not the `0.055` offset above).
const SRGB_DENOM: f64 = 1.055;
/// The sRGB linearization exponent (WCAG 2.x).
const SRGB_EXP: f64 = 2.4;

/// Linearize a single sRGB channel (0–1) to its relative-luminance contribution.
///
/// Implements the WCAG sRGB-to-linear transfer function. Values outside `[0, 1]`
/// are clamped so a stray input can never produce a negative or implausible
/// component.
fn linearize(channel01: f64) -> f64 {
    let c = channel01.clamp(0.0, 1.0);
    if c <= SRGB_THRESHOLD {
        c / SRGB_DIVISOR
    } else {
        ((c + SRGB_OFFSET) / SRGB_DENOM).powf(SRGB_EXP)
    }
}

/// Relative luminance (WCAG) of a color: `0.2126·R + 0.7152·G + 0.0722·B` over
/// the linearized 8-bit channels.
///
/// Returns a value in `[0, 1]` (`0` = pure black, `1` = pure white).
pub fn relative_luminance(c: Color) -> f64 {
    let r = linearize(c.r as f64 / 255.0);
    let g = linearize(c.g as f64 / 255.0);
    let b = linearize(c.b as f64 / 255.0);
    0.2126 * r + 0.7152 * g + 0.0722 * b
}

/// The WCAG relative contrast ratio between two colors.
///
/// The result is always `>= 1.0`: the lighter of the two colors is placed in the
/// numerator. White-on-black is `21.0`; a color and itself is `1.0`.
///
/// ```
/// use pengy_theme::contrast::contrast_ratio;
/// use pengy_theme::manifest::Color;
/// use std::str::FromStr;
///
/// let white = Color::from_str("#ffffff").unwrap();
/// let black = Color::from_str("#000000").unwrap();
/// let ratio = contrast_ratio(white, black);
/// assert!((ratio - 21.0).abs() < 1e-9, "white on black is 21:1, got {ratio}");
/// ```
pub fn contrast_ratio(fg: Color, bg: Color) -> f64 {
    let l1 = relative_luminance(fg);
    let l2 = relative_luminance(bg);
    // (L_lighter + 0.05) / (L_darker + 0.05) — order is irrelevant: the larger
    // luminance always lands in the numerator.
    let (lighter, darker) = if l1 >= l2 { (l1, l2) } else { (l2, l1) };
    (lighter + 0.05) / (darker + 0.05)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    fn c(hex: &str) -> Color {
        Color::from_str(hex).unwrap()
    }

    #[test]
    fn white_on_black_is_21() {
        // The canonical WCAG reference value.
        let r = contrast_ratio(c("#ffffff"), c("#000000"));
        assert!((r - 21.0).abs() < 1e-9, "white on black: {r}");
    }

    #[test]
    fn black_on_white_is_21() {
        // The ratio is symmetric in its arguments.
        let r = contrast_ratio(c("#000000"), c("#ffffff"));
        assert!((r - 21.0).abs() < 1e-9, "black on white: {r}");
    }

    #[test]
    fn grey_on_white_is_4_54() {
        // WCAG references #767676 on white as 4.54:1.
        let r = contrast_ratio(c("#767676"), c("#ffffff"));
        assert!((r - 4.54).abs() < 1e-2, "grey on white: {r}");
    }

    #[test]
    fn identical_colors_ratio_is_one() {
        let r = contrast_ratio(c("#1a2b3c"), c("#1a2b3c"));
        assert!((r - 1.0).abs() < 1e-9, "same color: {r}");
    }

    #[test]
    fn ratio_is_algebraically_symmetric() {
        let a = c("#3a6ea5");
        let b = c("#f8fafc");
        assert!((contrast_ratio(a, b) - contrast_ratio(b, a)).abs() < 1e-12);
    }

    #[test]
    fn ratio_is_at_least_one() {
        // By construction the lighter color is always in the numerator.
        for a in ["#000000", "#1a1a1a", "#5a5a5a", "#ffffff"] {
            for b in ["#000000", "#1a1a1a", "#5a5a5a", "#ffffff"] {
                assert!(
                    contrast_ratio(c(a), c(b)) >= 1.0,
                    "ratio for {a}/{b} must be >= 1"
                );
            }
        }
    }

    #[test]
    fn relative_luminance_bounds() {
        assert!((relative_luminance(c("#000000")) - 0.0).abs() < 1e-12);
        assert!((relative_luminance(c("#ffffff")) - 1.0).abs() < 1e-12);
        // Mid grey sits between the two extremes.
        let mid = relative_luminance(c("#808080"));
        assert!(mid > 0.2 && mid < 0.6, "mid grey luminance: {mid}");
    }

    #[test]
    fn linearize_clamps_out_of_range() {
        // A channel value outside [0,1] must not produce a negative luminance.
        assert!(linearize(-1.0) >= 0.0);
        assert!(linearize(2.0) <= 1.0 + 1e-9);
    }

    #[test]
    fn dark_on_light_meets_aa_normal_threshold() {
        // #1a1a1a on #f5f5f5 is a common PengyOS pairing; it must clear AA normal.
        let fg = c("#1a1a1a");
        let bg = c("#f5f5f5");
        assert!(
            contrast_ratio(fg, bg) >= WCAG_AA_NORMAL,
            "dark on light should clear AA normal: {}",
            contrast_ratio(fg, bg)
        );
    }
}
