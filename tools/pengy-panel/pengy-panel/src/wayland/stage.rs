//! Reusable **pixel staging** for the Wayland backend (task 13).
//!
//! A status bar repaints every poll interval. The previous backend staged each
//! frame into a *fresh* temp file and backed a brand-new `wl_shm` pool from it
//! — a file create/unlink, a new pool, and a new buffer **per frame**. That
//! per-frame churn is pure waste for a bar whose pixel buffer is (almost)
//! always the same size, and it is exactly the allocation the idle-RSS target
//! (T12) wants gone.
//!
//! [`PixelStage`] is the pure, headless-testable core of the fix. It owns
//! **one** byte buffer — the pixel backing region — and re-stages each frame
//! into it **in place**: the buffer grows only when a frame's pixel count
//! outgrows the current capacity (e.g. the output got wider after a resize) and
//! never shrinks. [`PixelStage::reallocs`] counts how many times the backing
//! region was (re)allocated; that is the metric a test asserts on to prove that
//! *many* frames at a stable size cost exactly **one** allocation, and a resize
//! to a bigger size costs exactly one more.
//!
//! Nothing here touches the compositor: it runs on a plain `Vec<u8>` and is
//! exercised by the normal `cargo test` suite. The live
//! [`super::ShmPool`] wraps it and maps it onto a real `wl_shm` pool (that part
//! is `PENGYOS_IT=1`-gated).

use crate::wayland::rasterize::Raster;

/// A single, reusable pixel backing region for the bar's buffer.
///
/// The region's length (in bytes) is its [`capacity`](Self::capacity). Each
/// [`stage`](Self::stage) call writes one frame's ARGB bytes into the region at
/// offset 0, growing the region first if the frame is larger than the current
/// capacity. The region is zero-allocated, so it never contains uninitialized
/// bytes.
#[derive(Debug, Default)]
pub struct PixelStage {
    /// The backing bytes; its length is the current capacity (in bytes).
    bytes: Vec<u8>,
    /// How many times the backing region was (re)allocated as it grew.
    reallocs: u32,
    /// How many frames have been staged.
    frames: u32,
    /// Pixel width of the most recent frame (0 before the first stage).
    last_width_px: u32,
    /// Pixel height of the most recent frame (0 before the first stage).
    last_height_px: u32,
}

impl PixelStage {
    /// An empty stage (capacity 0, nothing allocated yet).
    pub fn new() -> Self {
        Self::default()
    }

    /// The byte size a buffer for a `width_px` × `height_px` ARGB8888 image
    /// occupies: `width * height * 4`.
    ///
    /// Pure geometry, shared by [`stage`](Self::stage) and the live
    /// [`super::ShmPool`] so the pool size and the buffer it cuts from always
    /// agree.
    pub fn required_capacity(width_px: u32, height_px: u32) -> usize {
        // Compute in `usize` (not `u32`) so the product keeps full headroom and
        // only saturates at the true address-space limit — a malformed (huge)
        // dimension must not wrap into a tiny capacity handed to a real alloc.
        (width_px as usize)
            .saturating_mul(height_px as usize)
            .saturating_mul(4)
    }

    /// Stage `raster` into this region **in place**, growing it first if the
    /// frame is larger than the current capacity.
    ///
    /// Returns the number of bytes written (the frame's pixel bytes). The
    /// region is only (re)allocated when the frame outgrows the capacity — see
    /// [`reallocs`](Self::reallocs) — so a run of same-size frames allocates
    /// exactly once.
    pub fn stage(&mut self, raster: &Raster) -> u32 {
        let need = Self::required_capacity(raster.width_px, raster.height_px);
        if need > self.bytes.len() {
            // `resize` zero-fills any newly added bytes, so the region never
            // holds uninitialized data. A single growth = one (re)allocation.
            self.bytes.resize(need, 0);
            self.reallocs += 1;
        }
        let frame = raster.as_bytes();
        self.bytes[..frame.len()].copy_from_slice(&frame);
        self.frames += 1;
        self.last_width_px = raster.width_px;
        self.last_height_px = raster.height_px;
        frame.len() as u32
    }

    /// The current capacity of the backing region, in bytes.
    pub fn capacity(&self) -> usize {
        self.bytes.len()
    }

    /// The backing bytes (capacity bytes, the last frame at offset 0).
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }

    /// How many times the backing region was (re)allocated as it grew.
    pub fn reallocs(&self) -> u32 {
        self.reallocs
    }

    /// How many frames have been staged.
    pub fn frames(&self) -> u32 {
        self.frames
    }

    /// The pixel geometry of the most recent frame (0, 0 before any stage).
    pub fn last_geometry(&self) -> (u32, u32) {
        (self.last_width_px, self.last_height_px)
    }

    /// Reset to a brand-new stage (capacity 0, no allocations, no frames).
    ///
    /// This models the backend being closed: the pool built on top of the
    /// region is destroyed and the next `open` starts from an empty stage.
    pub fn reset(&mut self) {
        *self = Self::default();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::{Frame, PaintedWidget};
    use widgets::{Style, TextRun};

    /// A `Raster` of exactly `width_px` × `height_px`, every pixel = `color`.
    fn raster(width_px: u32, height_px: u32, color: u32) -> Raster {
        Raster {
            width_px,
            height_px,
            stride_px: width_px * 4,
            pixels: vec![color; (width_px * height_px) as usize],
        }
    }

    #[test]
    fn new_stage_is_empty_with_no_allocations() {
        let s = PixelStage::new();
        assert_eq!(s.capacity(), 0);
        assert_eq!(s.reallocs(), 0);
        assert_eq!(s.frames(), 0);
        assert_eq!(s.last_geometry(), (0, 0));
        assert!(s.as_bytes().is_empty());
    }

    #[test]
    fn required_capacity_is_width_times_height_times_four() {
        assert_eq!(PixelStage::required_capacity(60, 8), 60 * 8 * 4);
        assert_eq!(PixelStage::required_capacity(0, 8), 0);
        assert_eq!(PixelStage::required_capacity(60, 0), 0);
        assert_eq!(PixelStage::required_capacity(0, 0), 0);
        // Saturating: an absurdly large dimension does not wrap to a tiny size.
        assert_eq!(
            PixelStage::required_capacity(u32::MAX, u32::MAX),
            usize::MAX
        );
    }

    #[test]
    fn staging_allocates_exactly_once_for_many_same_size_frames() {
        let mut s = PixelStage::new();
        let cap = PixelStage::required_capacity(60, 8);
        for i in 0..100u32 {
            // A distinct color per frame so a regression that *stops* writing
            // would still be caught by the content assertion below.
            let color = 0xFF00_0000 | i;
            let wrote = s.stage(&raster(60, 8, color));
            assert_eq!(wrote, cap as u32);
        }
        assert_eq!(s.frames(), 100);
        // The whole point of the fix: 100 frames → exactly one allocation.
        assert_eq!(s.reallocs(), 1);
        assert_eq!(s.capacity(), cap);
        assert_eq!(s.last_geometry(), (60, 8));
        // The last frame's pixels are present at offset 0.
        let last = raster(60, 8, 0xFF00_0000 | 99);
        assert_eq!(&s.as_bytes()[..cap], &last.as_bytes()[..cap]);
    }

    #[test]
    fn a_larger_frame_grows_the_region_exactly_once() {
        let mut s = PixelStage::new();
        s.stage(&raster(30, 8, 1));
        assert_eq!(s.reallocs(), 1);
        // A wider bar (a resize) forces exactly one more allocation.
        s.stage(&raster(60, 8, 2));
        assert_eq!(s.reallocs(), 2);
        assert_eq!(s.capacity(), PixelStage::required_capacity(60, 8));
        // Subsequent same-size frames do not allocate again.
        s.stage(&raster(60, 8, 3));
        s.stage(&raster(60, 8, 4));
        assert_eq!(s.reallocs(), 2);
        assert_eq!(s.frames(), 4);
    }

    #[test]
    fn a_smaller_frame_does_not_shrink_the_region() {
        let mut s = PixelStage::new();
        s.stage(&raster(120, 8, 1));
        let cap_before = s.capacity();
        // Shrinking (e.g. the output got narrower) must NOT reallocate or
        // shrink the region — it only grows.
        s.stage(&raster(30, 8, 2));
        assert_eq!(s.capacity(), cap_before);
        assert_eq!(s.reallocs(), 1);
        // The smaller frame is written at offset 0, in place.
        let small = raster(30, 8, 2);
        assert_eq!(&s.as_bytes()[..small.as_bytes().len()], &small.as_bytes());
    }

    #[test]
    fn staging_is_in_place_and_does_not_leak_previous_frames_past_the_buffer() {
        let mut s = PixelStage::new();
        s.stage(&raster(60, 8, 0xAA));
        s.stage(&raster(60, 8, 0xBB));
        // The whole region (capacity == this frame's size) is the new frame.
        let expect = raster(60, 8, 0xBB);
        assert_eq!(s.as_bytes(), &expect.as_bytes());
    }

    #[test]
    fn the_region_is_never_left_uninitialized() {
        let mut s = PixelStage::new();
        // Grow to a size, then stage a *smaller* frame into the same region.
        s.stage(&raster(60, 8, 0));
        let cap = s.capacity();
        s.stage(&raster(10, 8, 0x11));
        // The bytes beyond the small frame are the zeros from the grow, not
        // garbage.
        let bytes = s.as_bytes();
        assert_eq!(bytes.len(), cap);
        assert_eq!(&bytes[..10 * 8 * 4], &raster(10, 8, 0x11).as_bytes());
        assert!(bytes[10 * 8 * 4..].iter().all(|&b| b == 0), "tail must be zero");
    }

    #[test]
    fn reset_returns_the_stage_to_a_fresh_state() {
        let mut s = PixelStage::new();
        s.stage(&raster(60, 8, 1));
        s.stage(&raster(60, 8, 2));
        assert_eq!(s.reallocs(), 1);
        assert_eq!(s.frames(), 2);
        s.reset();
        assert_eq!(s.capacity(), 0);
        assert_eq!(s.reallocs(), 0);
        assert_eq!(s.frames(), 0);
        assert_eq!(s.last_geometry(), (0, 0));
        // After a reset, the next stage allocates from scratch.
        s.stage(&raster(60, 8, 3));
        assert_eq!(s.reallocs(), 1);
        assert_eq!(s.frames(), 1);
    }

    #[test]
    fn staging_a_real_raster_matches_its_bytes() {
        // Use the actual rasterizer (not a hand-built Raster) to prove the
        // stage carries exactly what `paint` will hand to the pool.
        let mut frame = Frame::new(4);
        frame.placed.push(PaintedWidget {
            x: 0,
            width: 2,
            runs: vec![TextRun::new("hi", Style::Normal)],
        });
        let font = crate::wayland::font::CellFont::new();
        let r = crate::wayland::rasterize::rasterize_frame(&frame, &font, 1);
        let mut s = PixelStage::new();
        let wrote = s.stage(&r);
        assert_eq!(wrote, r.as_bytes().len() as u32);
        assert_eq!(s.as_bytes(), &r.as_bytes());
    }
}
