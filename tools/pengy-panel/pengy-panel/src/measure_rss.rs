//! The `--headless --measure-rss` mode (task 12, the idle-RSS measurement).
//!
//! The idle-RSS target (≤ 15 MiB, [`ARCHITECTURE.md`](../ARCHITECTURE.md))
//! needs a *running release process* to sample. A live compositor can't run
//! headlessly (no seat, no root), but the panel's steady-state memory is
//! dominated by its own state — the widget list, the layout engine, the
//! rasterized ARGB frame buffer — not by the (small) Wayland socket/client
//! overhead. This mode measures that faithfully, with no compositor:
//!
//! 1. build the widget list from the config ([`widgets::build_widgets`]);
//! 2. drive the real [`crate::panel::Panel`] through a short steady-state loop
//!    of poll → layout → paint (the same path the live run uses);
//! 3. rasterize the last frame to an ARGB8888 buffer the same way the live
//!    backend does in [`crate::wayland`], and hold that buffer while
//! 4. reading the process's resident set size (`VmRSS` from
//!    `/proc/self/status`).
//!
//! The held ARGB buffer is exactly what the live backend keeps at idle (one
//! `wl_buffer`), so the number is a **faithful lower bound** on the live
//! panel's idle RSS — it omits only the Wayland client/socket overhead.
//!
//! # Gate
//!
//! With `--max-mib <MIB>` (or the default 15 MiB target), the measured size is
//! compared against the limit and the mode exits **1** when exceeded and **0**
//! when within. That makes the target a real, CI-runnable regression guard
//! without a compositor: run it in CI against the *release* binary and the
//! build fails the moment the idle footprint crosses the line.

use widgets::{build_widgets, PanelConfig};

use crate::headless::HeadlessBackend;
use crate::panel::Panel;
use crate::print_layout::PinnedSource;
use crate::wayland::font::CellFont;
use crate::wayland::rasterize::rasterize_frame;

/// The idle-RSS target in MiB (see ARCHITECTURE.md). This is the default
/// `--max-mib` limit: the number the release panel is held to.
pub const IDLE_RSS_TARGET_MIB: u32 = 15;

/// The bar width (cells) used when `--measure-rss` is given without `--width` —
/// a representative single-monitor bar (same width the layout bench uses).
pub const DEFAULT_MEASURE_WIDTH_CELLS: u32 = 120;

/// The bar height (cells) the measurement rasterizes at — the default one-cell
/// bar the live backend opens with.
const MEASURE_HEIGHT_CELLS: u32 = 1;

/// How many poll → layout → paint cycles to run before sampling, so transient
/// first-time allocations (cache growth, string reallocation) have settled into
/// steady state.
const WARMUP_FRAMES: u32 = 256;

/// A steady-state measurement of the release panel's idle memory.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RssMeasurement {
    /// Resident set size in KiB (`VmRSS` from `/proc/self/status`).
    pub vmrss_kib: u64,
    /// Width of the ARGB pixel buffer held at sample time, in pixels.
    pub buffer_width_px: u32,
    /// Height of the buffer, in pixels.
    pub buffer_height_px: u32,
    /// Pixels in the held buffer (`buffer_width_px * buffer_height_px`).
    pub buffer_pixels: u32,
    /// Poll → layout → paint cycles run before sampling.
    pub frames: u32,
}

impl RssMeasurement {
    /// Resident set size in MiB (for display; integer math is used for the
    /// gate, this is only the human-facing projection).
    pub fn vmrss_mib(&self) -> f64 {
        self.vmrss_kib as f64 / 1024.0
    }

    /// True when the resident set size is at or under `limit_mib`.
    pub fn within(&self, limit_mib: u32) -> bool {
        self.vmrss_kib <= (limit_mib as u64) * 1024
    }

    /// The single stdout line this mode prints: the measured KiB, the MiB
    /// projection, the limit, and the pass/fail word. Machine-parseable (the
    /// KiB is the first number) and human-readable.
    pub fn format_line(&self, limit_mib: u32) -> String {
        format!(
            "idle-rss: {} KiB ({:.2} MiB) [limit {} MiB] {}",
            self.vmrss_kib,
            self.vmrss_mib(),
            limit_mib,
            if self.within(limit_mib) { "OK" } else { "EXCEEDED" }
        )
    }
}

/// Parse the `VmRSS:` line out of a `/proc/self/status` image.
///
/// Pure and injectable, so it is unit-testable against canned text — the test
/// never needs a real `/proc`. Returns `None` when the field is absent.
/// (The field value has the shape `  1234 kB`; we take the leading integer.)
pub fn parse_vmrss_kib(status: &str) -> Option<u64> {
    for line in status.lines() {
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        if key.trim() != "VmRSS" {
            continue;
        }
        // The value is like `  2424 kB` (leading whitespace + a `kB` suffix);
        // trim, then take the leading integer.
        let digits: String = value
            .trim()
            .chars()
            .take_while(|c| c.is_ascii_digit())
            .collect();
        return digits.parse::<u64>().ok();
    }
    None
}

/// Read the *current* process's resident set size in KiB.
pub fn current_vmrss_kib() -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    parse_vmrss_kib(&status)
}

/// Run the real pipeline in a short steady-state loop, hold the last frame's
/// ARGB pixel buffer, and sample the process's resident set size.
///
/// `width_cells` is the bar width (a representative value, not a live output
/// size); `epoch` pins the clock so the rendered content is deterministic.
/// Returns `None` only when the process's `VmRSS` cannot be read.
pub fn measure_idle_rss(config: &PanelConfig, width_cells: u32, epoch: Option<i64>) -> Option<RssMeasurement> {
    let mut backend = HeadlessBackend::new(width_cells);
    let mut panel = Panel::new(
        build_widgets(config),
        Box::new(PinnedSource::new(epoch)),
        config.bar.clone(),
    )
    .with_width(width_cells);

    let font = CellFont::new();

    // Warm up through WARMUP_FRAMES poll → layout → paint cycles, rasterizing
    // each frame the way the live backend does in `paint`, so the held buffer
    // and every per-frame allocation are at steady state.
    let mut frame = panel.tick(&mut backend);
    let mut raster = rasterize_frame(&frame, &font, MEASURE_HEIGHT_CELLS);
    for _ in 1..WARMUP_FRAMES {
        frame = panel.tick(&mut backend);
        raster = rasterize_frame(&frame, &font, MEASURE_HEIGHT_CELLS);
    }

    // Sample with the ARGB buffer still in scope (alive until this function
    // returns), exactly the buffer the live backend holds at idle.
    let vmrss_kib = current_vmrss_kib()?;
    let buffer_pixels = raster.width_px.saturating_mul(raster.height_px);
    Some(RssMeasurement {
        vmrss_kib,
        buffer_width_px: raster.width_px,
        buffer_height_px: raster.height_px,
        buffer_pixels,
        frames: WARMUP_FRAMES,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use widgets::Section;

    /// Canned `/proc/self/status` text: parse the VmRSS field and ignore the
    /// rest (and the `kB` unit suffix).
    #[test]
    fn parse_vmrss_reads_the_field_and_unit() {
        let status = "Name:\tpengy-panel\nUmask:\t0022\nState:\tS (sleeping)\nVmPeak:\t  20000 kB\nVmSize:\t  18000 kB\nVmRSS:\t   4096 kB\nThreads:\t1\n";
        assert_eq!(parse_vmrss_kib(status), Some(4096));
    }

    /// VmRSS must not be confused with the similarly-named Vm* fields.
    #[test]
    fn parse_vmrss_does_not_match_other_vm_fields() {
        let status = "VmPeak:\t  20000 kB\nVmSize:\t  18000 kB\nVmData:\t   9000 kB\nVmStk:\t     136 kB\n";
        assert_eq!(parse_vmrss_kib(status), None);
    }

    /// An absent field is `None`, not a panic or a wrong field's value.
    #[test]
    fn parse_vmrss_absent_is_none() {
        assert_eq!(parse_vmrss_kib("Name:\tx\nState:\tR (running)\n"), None);
    }

    /// The measurement's arithmetic: the gate is integer KiB-vs-limit, and the
    /// MiB projection is the human-facing rounding.
    #[test]
    fn within_and_format_use_integer_kib_math() {
        // Exactly at the limit → within (<=).
        let at = RssMeasurement {
            vmrss_kib: 15 * 1024,
            buffer_width_px: 60,
            buffer_height_px: 8,
            buffer_pixels: 480,
            frames: WARMUP_FRAMES,
        };
        assert!(at.within(15));
        assert!(!at.within(14));
        assert_eq!(at.format_line(15), "idle-rss: 15360 KiB (15.00 MiB) [limit 15 MiB] OK");
        // One KiB over → exceeded.
        let over = RssMeasurement {
            vmrss_kib: 15 * 1024 + 1,
            ..at
        };
        assert!(!over.within(15));
        assert!(over.format_line(15).ends_with("EXCEEDED"));
    }

    /// The held ARGB buffer has exactly `width_cells*cell_w x cell_h` pixels —
    /// the same buffer the live backend would allocate, so the sample includes
    /// it. A 120-cell, 1-cell-tall bar is 120*6 x 8 = 720x8 = 5760 px.
    #[test]
    fn measured_buffer_matches_the_live_backend_allocation() {
        let m = measure_idle_rss(
            &PanelConfig {
                widgets: vec![widgets::WidgetConfig::text(
                    Section::Left,
                    "PengyOS".to_string(),
                )],
                ..Default::default()
            },
            DEFAULT_MEASURE_WIDTH_CELLS,
            Some(1_000_000_000),
        )
        .expect("VmRSS must be readable on this host");
        assert_eq!(m.buffer_width_px, DEFAULT_MEASURE_WIDTH_CELLS * CellFont::new().cell_w);
        assert_eq!(m.buffer_height_px, MEASURE_HEIGHT_CELLS * CellFont::new().cell_h);
        assert_eq!(
            m.buffer_pixels,
            (DEFAULT_MEASURE_WIDTH_CELLS * CellFont::new().cell_w * CellFont::new().cell_h) as u32
        );
        // A real, positive resident-set-size reading.
        assert!(m.vmrss_kib > 0);
        assert_eq!(m.frames, WARMUP_FRAMES);
    }

    /// The measurement path itself is host-independent in shape (same widget
    /// count → same buffer geometry) and never panics on an empty config.
    #[test]
    fn empty_config_measures_a_blank_bar_without_panicking() {
        let m = measure_idle_rss(&PanelConfig::default(), 40, None).expect("VmRSS readable");
        assert_eq!(m.buffer_width_px, 40 * CellFont::new().cell_w);
        assert!(m.vmrss_kib > 0);
    }
}
