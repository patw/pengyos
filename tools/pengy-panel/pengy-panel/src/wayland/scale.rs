//! Pure **output-scale arithmetic** for the Wayland backend (task 14).
//!
//! A compositor reports the display scale of the output a layer surface sits on
//! in two ways:
//!
//! - **Integer** — the `wl_output.scale` event (`i32`, 1 = 100%).
//! - **Fractional** — the `wp_fractional_scale_v1.preferred_scale` event
//!   (`u32` in 24.8 fixed point, so 256 = 1.0, 384 = 1.5, 512 = 2.0). Per the
//!   fractional-scale-v1 protocol, when the compositor sends a fractional scale
//!   for a surface it **replaces** the output's integer scale for that surface.
//!
//! [`Scale::resolve`] encodes exactly that rule from the two raw readings, and
//! [`Scale::scaled_px`] does the fixed-point pixel math (round, saturate —
//! never overflow, never float) the rasterizer and the live backend share, so
//! the *pure* half of scaling is headless-testable while the socket plumbing
//! stays `PENGYOS_IT=1`-gated.

/// A positive rational display scale, `num / den`.
///
/// Canonical den is 256 (the 24.8 fixed point the protocols speak), but the
/// arithmetic is ratio-based and works for any positive denominator.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Scale {
    /// Numerator (always > 0 — a zero or negative scale is invalid and is
    /// rejected/clamped at construction, so a `Scale` is always a real scale).
    pub num: u32,
    /// Denominator (always > 0).
    pub den: u32,
}

impl Scale {
    /// 1:1 — the scale a surface has until the compositor says otherwise.
    pub const ONE: Self = Scale { num: 256, den: 256 };

    /// 1:1 (method form, for call sites that build scales uniformly).
    pub fn one() -> Self {
        Self::ONE
    }

    /// A scale from a positive ratio. `None` if either part is 0 (a zero
    /// scale would make every pixel math below degenerate).
    pub fn new(num: u32, den: u32) -> Option<Self> {
        (num > 0 && den > 0).then(|| Scale { num, den })
    }

    /// Parse a **24.8 fixed-point** scale (the `wp_fractional_scale_v1`
    /// `preferred_scale` payload): 256 = 1.0, 384 = 1.5, 512 = 2.0.
    ///
    /// `0` (or a negative value) is not a scale — the protocol uses 0 to mean
    /// "use the integer output scale" — so those yield `None`.
    pub fn from_fixed24_8(fixed: i32) -> Option<Self> {
        if fixed <= 0 {
            return None;
        }
        Some(Scale {
            num: fixed as u32,
            den: 256,
        })
    }

    /// Resolve the two raw compositor readings into the one scale a surface
    /// renders at, per the fractional-scale-v1 protocol:
    ///
    /// 1. a **nonzero** fractional scale *replaces* the integer scale
    ///    entirely (even when it is smaller than the integer one);
    /// 2. a `0`/absent fractional scale falls back to the integer
    ///    `wl_output.scale`;
    /// 3. a missing or non-positive integer scale clamps to 1:1.
    pub fn resolve(integer_scale: i32, fractional_fixed: Option<i32>) -> Self {
        if let Some(f) = fractional_fixed {
            if f > 0 {
                return Self {
                    num: f as u32,
                    den: 256,
                };
            }
        }
        let integer = integer_scale.max(1) as u32;
        Scale {
            num: integer * 256,
            den: 256,
        }
    }

    /// Map `logical_px` logical (scale-1) pixels to physical pixels:
    /// `logical_px * num / den`, **rounded to nearest** (half rounds up) and
    /// **saturating** at `u32::MAX` — a malformed (enormous) reading must not
    /// overflow into a tiny dimension handed to a real allocation.
    ///
    /// All math is in `u64`; no floats cross this boundary.
    pub fn scaled_px(&self, logical_px: u32) -> u32 {
        if logical_px == 0 {
            return 0;
        }
        let (n, d) = (self.num as u64, self.den as u64);
        let px = (logical_px as u64).saturating_mul(n).saturating_add(d / 2) / d;
        px.min(u32::MAX as u64) as u32
    }

    /// The bar width in **text cells** for a `width_px`-pixel configure at this
    /// scale: physical width divided by the scaled cell width. A sub-cell
    /// remainder is dropped (the right edge is clipped, never a partial cell).
    pub fn cells_for_px(&self, width_px: u32, cell_w: u32) -> u32 {
        let scaled_cell_w = self.scaled_px(cell_w);
        if scaled_cell_w == 0 {
            return 0;
        }
        width_px / scaled_cell_w
    }

    /// Re-express this scale as 24.8 fixed point (round-trip for logging and
    /// tests): `num * 256 / den`, rounded to nearest, saturated at `i32::MAX`.
    pub fn as_fixed24_8(&self) -> i32 {
        let n = (self.num as u64).saturating_mul(256).saturating_add(self.den as u64 / 2);
        (n / self.den as u64).min(i32::MAX as u64) as i32
    }

    /// True for an integer scale (num is an exact multiple of den) — the case
    /// where every font pixel maps to an exact square block.
    pub fn is_integer(&self) -> bool {
        (self.num as u64) % (self.den as u64) == 0
    }
}

impl std::fmt::Display for Scale {
    /// A short human form: `2`, `1.5`, `1.25`, `1.016` (up to 3 decimals,
    /// trailing zeros trimmed). Display-only — never parsed back.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let milli = (self.num as u64)
            .saturating_mul(1000)
            .saturating_add(self.den as u64 / 2)
            / self.den as u64;
        let (whole, frac) = (milli / 1000, milli % 1000);
        if frac == 0 {
            write!(f, "{whole}")
        } else {
            // Trim trailing zeros: 1.5, 1.25, 1.016.
            let digits = format!("{frac:03}");
            let trimmed = digits.trim_end_matches('0');
            write!(f, "{whole}.{trimmed}")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_is_the_identity() {
        assert_eq!(Scale::ONE, Scale::one());
        assert_eq!(Scale::ONE.scaled_px(0), 0);
        assert_eq!(Scale::ONE.scaled_px(6), 6);
        assert_eq!(Scale::ONE.scaled_px(8), 8);
        assert_eq!(Scale::ONE.scaled_px(1_000_000), 1_000_000);
        assert_eq!(Scale::ONE.as_fixed24_8(), 256);
        assert!(Scale::ONE.is_integer());
        assert_eq!(Scale::ONE.to_string(), "1");
        assert_eq!(Scale::ONE.cells_for_px(120, 6), 20);
    }

    #[test]
    fn from_fixed24_8_parses_the_protocol_encoding() {
        assert_eq!(Scale::from_fixed24_8(256), Some(Scale::ONE));
        assert_eq!(Scale::from_fixed24_8(512), Some(Scale { num: 512, den: 256 }));
        assert_eq!(Scale::from_fixed24_8(384), Some(Scale { num: 384, den: 256 }));
        // 0 is "use the integer scale", not a scale.
        assert_eq!(Scale::from_fixed24_8(0), None);
        // Negative is not a scale either.
        assert_eq!(Scale::from_fixed24_8(-1), None);
        // A non-representable scale round-trips within rounding.
        let s = Scale::from_fixed24_8(260).unwrap(); // 1.015625
        assert_eq!(s.as_fixed24_8(), 260);
    }

    #[test]
    fn resolve_prefers_a_nonzero_fractional_scale_over_the_integer_one() {
        // The protocol rule: fractional *replaces* integer — even when the
        // integer reading is larger.
        assert_eq!(Scale::resolve(1, Some(512)), Scale { num: 512, den: 256 });
        assert_eq!(Scale::resolve(3, Some(384)), Scale { num: 384, den: 256 });
        // 0 or absent fractional falls back to the integer scale.
        assert_eq!(Scale::resolve(2, Some(0)), Scale { num: 512, den: 256 });
        assert_eq!(Scale::resolve(2, None), Scale { num: 512, den: 256 });
        assert_eq!(Scale::resolve(1, Some(-5)), Scale { num: 256, den: 256 });
        // Garbage integer readings clamp to 1:1, never 0 or negative.
        assert_eq!(Scale::resolve(0, None), Scale::ONE);
        assert_eq!(Scale::resolve(-3, None), Scale::ONE);
    }

    #[test]
    fn scaled_px_rounds_to_nearest_and_is_exact_for_common_scales() {
        let s2 = Scale::resolve(1, Some(512)); // 2.0
        let s32 = Scale::resolve(1, Some(384)); // 1.5
        assert_eq!(s2.scaled_px(6), 12);
        assert_eq!(s2.scaled_px(8), 16);
        assert_eq!(s32.scaled_px(6), 9);
        assert_eq!(s32.scaled_px(8), 12);
        // 1.25: 6 → 7.5 → rounds half-up to 8; 5 → 6.25 → 6.
        let s54 = Scale::new(5, 4).unwrap();
        assert_eq!(s54.scaled_px(6), 8);
        assert_eq!(s54.scaled_px(5), 6);
        // 1.5 of 1 px rounds up to 2 px (a pixel can't be half-inked).
        assert_eq!(s32.scaled_px(1), 2);
        // A scale < 1 is legal math; 1 px at 0.5 rounds down to... 1
        // (0.5 rounds half-up to 1) — never 0 for a nonzero input at sane
        // scales, and the saturating floor is the contract.
        let half = Scale::new(1, 2).unwrap();
        assert_eq!(half.scaled_px(1), 1);
        assert_eq!(half.scaled_px(0), 0);
    }

    #[test]
    fn scaled_px_saturates_instead_of_overflowing() {
        let s2 = Scale::resolve(1, Some(512));
        // 2^32-1 times 2 must not wrap to 0.
        assert_eq!(s2.scaled_px(u32::MAX), u32::MAX);
        // An absurd scale reading still saturates, never panics.
        let big = Scale::new(u32::MAX, 1).unwrap();
        assert_eq!(big.scaled_px(u32::MAX), u32::MAX);
        assert_eq!(big.scaled_px(2), u32::MAX);
    }

    #[test]
    fn cells_for_px_divides_by_the_scaled_cell_width() {
        let s1 = Scale::ONE;
        let s2 = Scale::resolve(1, Some(512));
        let s32 = Scale::resolve(1, Some(384));
        // A 120-px-wide bar: 20 cells at 1:1 (6 px/cell), 10 at 2:1 (12 px/cell),
        // 13 at 1.5 (9 px/cell → 120/9 = 13 with 3 px of right-edge clip).
        assert_eq!(s1.cells_for_px(120, 6), 20);
        assert_eq!(s2.cells_for_px(120, 6), 10);
        assert_eq!(s32.cells_for_px(120, 6), 13);
        // Fewer pixels than one scaled cell → 0 cells, not a panic.
        assert_eq!(s2.cells_for_px(5, 6), 0);
        assert_eq!(s2.cells_for_px(0, 6), 0);
    }

    #[test]
    fn fixed248_round_trip_is_within_one_quantum() {
        for (num, den) in [(256, 256), (512, 256), (384, 256), (260, 256), (5, 4), (3, 2), (1, 2)] {
            let s = Scale::new(num, den).unwrap();
            let back = Scale::from_fixed24_8(s.as_fixed24_8()).unwrap();
            // Both sides within one 1/256 quantum of the original value.
            let lhs = (num as i64) * 256 - den as i64 * s.as_fixed24_8() as i64;
            assert!(lhs.abs() <= 256, "round trip {s} drifted: {lhs}");
            assert!(back.scaled_px(256).abs_diff(s.scaled_px(256)) <= 1);
        }
    }

    #[test]
    fn new_rejects_zero_parts() {
        assert_eq!(Scale::new(0, 256), None);
        assert_eq!(Scale::new(256, 0), None);
        assert_eq!(Scale::new(0, 0), None);
    }

    #[test]
    fn display_is_a_short_human_form() {
        assert_eq!(Scale::ONE.to_string(), "1");
        assert_eq!(Scale::resolve(1, Some(512)).to_string(), "2");
        assert_eq!(Scale::resolve(1, Some(384)).to_string(), "1.5");
        assert_eq!(Scale::new(5, 4).unwrap().to_string(), "1.25");
    }

    #[test]
    fn is_integer_detects_exact_block_scales() {
        assert!(Scale::ONE.is_integer());
        assert!(Scale::resolve(1, Some(512)).is_integer());
        assert!(Scale::resolve(3, None).is_integer());
        assert!(!Scale::resolve(1, Some(384)).is_integer());
        assert!(!Scale::new(5, 4).unwrap().is_integer());
    }
}
