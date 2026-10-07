//! The `--measure-rss-live` one-shot mode — the *live-compositor* idle RSS.
//!
//! [`crate::measure_rss`] measures the release panel's steady-state RSS with
//! **no** compositor (a faithful *lower bound* that omits the Wayland
//! socket/client overhead). This module adds the residual that mode cannot
//! reach: run the panel's **real** [`crate::wayland::WaylandBackend`] against a
//! live compositor for a short steady-state window and sample the process's
//! resident set size **while it is actually connected and painting** — i.e.
//! headless number + the measured Wayland client/socket overhead.
//!
//! # Headless-test boundary
//!
//! Only the *sampled-while-connected* part needs a compositor, so it is the
//! `PENGYOS_IT=1`-gated end-to-end path (see `pengy-panel/tests/live_rss.rs`)
//! and the live [`measure_live_idle_rss`] driver. Everything else is pure or
//! `/proc`-local and unit-tested here with no compositor:
//!
//! - [`wayland_socket_path`] resolves `WAYLAND_DISPLAY` + `XDG_RUNTIME_DIR`
//!   into a socket path (pure over injected env values);
//! - [`steady_min_kib`] reduces a window of RSS samples to the idle value
//!   (the minimum — the kernel frees untouched pages, so idle is the floor,
//!   and warm-up allocation spikes can only raise it);
//! - [`run_steady`] is the bounded open → tick/wait/paint → close loop, fully
//!   generic over the [`Backend`], so it is driven headlessly against
//!   [`crate::headless::HeadlessBackend`].
//!
//! A failed attach (no compositor, or a compositor that never sizes the layer
//! surface) is reported as [`LiveRssError::NoCompositor`]; the binary maps
//! that to a one-line diagnostic and exit **69** (EX_UNAVAILABLE).

use std::path::PathBuf;
use std::time::{Duration, Instant};

use widgets::{build_widgets, PanelConfig, SysfsSource};

use crate::backend::{Backend, BackendError, BackendEvent};
use crate::measure_rss;
use crate::panel::{Panel, RunReport};
use crate::wayland::WaylandBackend;

/// How long the live run stays connected before sampling the idle RSS: long
/// enough for transient first-time allocations (font cache, layout reflow,
/// the first paint's pool) to settle, short enough to be a one-shot mode.
pub const STEADY_DURATION: Duration = Duration::from_millis(2000);

/// How often the process's `VmRSS` is sampled during the steady-state window.
pub const SAMPLE_INTERVAL: Duration = Duration::from_millis(250);

/// A live-compositor idle RSS measurement.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LiveRssMeasurement {
    /// The idle resident set size in KiB (the minimum over the sample window).
    pub vmrss_kib: u64,
    /// Frames painted during the steady-state window.
    pub frames: u64,
    /// The bar width (cells) the backend reported (the primary output).
    pub width: u32,
    /// How many `VmRSS` samples were actually taken.
    pub samples: u32,
}

impl LiveRssMeasurement {
    /// Resident set size in MiB (display only; the gate uses integer KiB).
    pub fn vmrss_mib(&self) -> f64 {
        self.vmrss_kib as f64 / 1024.0
    }

    /// True when the resident set size is at or under `limit_mib`.
    pub fn within(&self, limit_mib: u32) -> bool {
        self.vmrss_kib <= (limit_mib as u64) * 1024
    }

    /// The single stdout line: the measured KiB, the MiB projection, the
    /// limit, and the pass/fail word. Machine-parseable (KiB is the first
    /// number) and human-readable.
    pub fn format_line(&self, limit_mib: u32) -> String {
        format!(
            "live-idle-rss: {} KiB ({:.2} MiB) [limit {} MiB] {}",
            self.vmrss_kib,
            self.vmrss_mib(),
            limit_mib,
            if self.within(limit_mib) { "OK" } else { "EXCEEDED" }
        )
    }
}

/// What can go wrong running the live measurement (distinct from a CLI or
/// config error).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LiveRssError {
    /// The panel could not attach to a layer-shell compositor (no
    /// `WAYLAND_DISPLAY`, no `layer-shell` global, or the compositor never
    /// sized the surface).
    NoCompositor(String),
    /// A live connection was made, but the process's `VmRSS` could not be read.
    VmRSSUnreadable,
}

impl LiveRssError {
    /// A one-line, stderr-friendly diagnostic. The `NoCompositor` reason is
    /// already the full [`BackendError`] rendering (which carries its own
    /// "cannot attach to a compositor:" prefix), so only the program name is
    /// added here — never the prefix again.
    pub fn diagnostic(&self) -> String {
        match self {
            LiveRssError::NoCompositor(reason) => format!("pengy-panel: {reason}"),
            LiveRssError::VmRSSUnreadable => {
                "pengy-panel: cannot read the process memory map (VmRSS)".to_string()
            }
        }
    }
}

/// Resolve the Wayland socket path from the `WAYLAND_DISPLAY` and
/// `XDG_RUNTIME_DIR` environment values.
///
/// `WAYLAND_DISPLAY` is either an **absolute path** (used as-is) or an
/// **abstract name** (e.g. `wayland-0`) that must be joined onto
/// `$XDG_RUNTIME_DIR`. Returns `None` when there is nothing to resolve: an
/// empty/absent `WAYLAND_DISPLAY`, or an abstract name with no runtime dir.
/// Pure over injected values so it is unit-tested without touching the
/// process environment.
pub fn wayland_socket_path(display: Option<&str>, runtime_dir: Option<&str>) -> Option<PathBuf> {
    let display = display?.trim();
    if display.is_empty() {
        return None;
    }
    if display.starts_with('/') {
        // An absolute socket path (some compositors / CI harnesses set this).
        Some(PathBuf::from(display))
    } else {
        // An abstract name: it lives under $XDG_RUNTIME_DIR.
        Some(PathBuf::from(runtime_dir?).join(display))
    }
}

/// The Wayland socket path for *this* process's environment, if resolvable.
pub fn compositor_socket() -> Option<PathBuf> {
    wayland_socket_path(
        std::env::var("WAYLAND_DISPLAY").ok().as_deref(),
        std::env::var("XDG_RUNTIME_DIR").ok().as_deref(),
    )
}

/// True when the resolved Wayland socket actually exists on this host.
///
/// This is a *pre-flight* hint only: a socket can exist without supporting
/// `layer-shell`, and the real check is [`WaylandBackend::open`]. It lets the
/// gated end-to-end test distinguish "no compositor at all" from "a
/// compositor that did not size our surface" without running the live loop.
pub fn compositor_available() -> bool {
    compositor_socket().is_some_and(|p| p.exists())
}

/// Reduce a window of resident-set-size samples (KiB) to the idle value.
///
/// Idle RSS is the **minimum** observed over the window: the kernel lazily
/// frees pages the process stopped touching, so once the panel is steady the
/// resident set is at or below its warm-up peak, and warm-up allocation
/// spikes can only *raise* a sample, never lower the settled floor. An empty
/// window is `None` (there was no reading to reduce).
pub fn steady_min_kib(samples: &[u64]) -> Option<u64> {
    samples.iter().copied().min()
}

/// Run a panel to a bounded steady state on any [`Backend`], sampling the
/// process between frames.
///
/// Mirrors [`Panel::run`] but for a fixed `duration` instead of until `Quit`:
/// `open`, then loop { `tick`; sample at [`SAMPLE_INTERVAL`]; `wait` capped so
/// the total never exceeds `duration` } until the window elapses or a `Quit`
/// arrives, then exactly one `close`. A `Resize` reflows the layout on the
/// next tick. Returns the run report and the collected samples.
///
/// Generic over the backend so the exact loop is unit-tested headlessly (the
/// gated e2e test and the real `WaylandBackend` only differ in the backend).
pub fn run_steady<B: Backend, S: Fn() -> Option<u64>>(
    panel: &mut Panel,
    backend: &mut B,
    duration: Duration,
    sample: S,
) -> Result<(RunReport, Vec<u64>), BackendError> {
    panel.set_width(backend.open()?);
    let poll = panel.poll_interval();
    let start = Instant::now();
    let mut frames = 0u64;
    let mut samples: Vec<u64> = Vec::new();
    let mut next_sample = start;

    loop {
        panel.tick(backend);
        frames += 1;

        let now = Instant::now();
        if now >= next_sample {
            if let Some(v) = sample() {
                samples.push(v);
            }
            next_sample = now + SAMPLE_INTERVAL;
        }

        let elapsed = now - start;
        if elapsed >= duration {
            break;
        }
        // Wait at most `poll`, but never past the end of the window.
        let remaining = duration - elapsed;
        match backend.wait(remaining.min(poll)) {
            BackendEvent::Tick => {}
            BackendEvent::Resize { width_cells } => panel.set_width(width_cells),
            BackendEvent::Quit => break,
        }
    }

    backend.close();
    Ok((
        RunReport {
            frames,
            width: panel.width(),
        },
        samples,
    ))
}

/// Run the panel's **real** Wayland backend to steady state against a live
/// compositor and measure the process's idle resident set size.
///
/// This is the live complement of [`crate::measure_rss::measure_idle_rss`]:
/// the number it returns includes the Wayland socket/client overhead the
/// headless mode omits. A failed attach is [`LiveRssError::NoCompositor`].
pub fn measure_live_idle_rss(config: &PanelConfig) -> Result<LiveRssMeasurement, LiveRssError> {
    let mut panel = Panel::new(
        build_widgets(config),
        Box::new(SysfsSource::new()),
        config.bar.clone(),
    );
    let mut backend = WaylandBackend::new();
    let (report, samples) = run_steady(
        &mut panel,
        &mut backend,
        STEADY_DURATION,
        || measure_rss::current_vmrss_kib(),
    )
    .map_err(|err| LiveRssError::NoCompositor(err.to_string()))?;

    let vmrss_kib = steady_min_kib(&samples).ok_or(LiveRssError::VmRSSUnreadable)?;
    Ok(LiveRssMeasurement {
        vmrss_kib,
        frames: report.frames,
        width: report.width,
        samples: samples.len() as u32,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::headless::HeadlessBackend;
    use widgets::{BarConfig, Section};

    fn bar(poll_ms: u64) -> BarConfig {
        BarConfig {
            gap_cells: 1,
            poll_interval_ms: poll_ms,
        }
    }

    // --- wayland_socket_path (pure) --------------------------------------

    #[test]
    fn socket_path_joins_abstract_name_onto_runtime_dir() {
        assert_eq!(
            wayland_socket_path(Some("wayland-0"), Some("/run/user/1000")),
            Some(PathBuf::from("/run/user/1000/wayland-0"))
        );
    }

    #[test]
    fn socket_path_uses_an_absolute_path_verbatim() {
        assert_eq!(
            wayland_socket_path(Some("/tmp/wl.sock"), Some("/run/user/1000")),
            Some(PathBuf::from("/tmp/wl.sock"))
        );
        // An absolute path must not depend on the runtime dir at all.
        assert_eq!(
            wayland_socket_path(Some("/tmp/wl.sock"), None),
            Some(PathBuf::from("/tmp/wl.sock"))
        );
    }

    #[test]
    fn socket_path_is_none_without_a_display() {
        assert_eq!(wayland_socket_path(None, Some("/run/user/1000")), None);
        assert_eq!(wayland_socket_path(Some(""), Some("/run/user/1000")), None);
        assert_eq!(wayland_socket_path(Some("   "), Some("/run/user/1000")), None);
    }

    #[test]
    fn socket_path_is_none_for_abstract_name_without_runtime_dir() {
        assert_eq!(wayland_socket_path(Some("wayland-0"), None), None);
    }

    #[test]
    fn socket_path_trims_surrounding_whitespace() {
        assert_eq!(
            wayland_socket_path(Some("  wayland-0  "), Some("/r")),
            Some(PathBuf::from("/r/wayland-0"))
        );
    }

    // --- steady_min_kib (pure) -------------------------------------------

    #[test]
    fn steady_min_is_the_floor_of_the_window() {
        assert_eq!(steady_min_kib(&[3000, 2500, 2600, 2512]), Some(2500));
        // A single sample is its own minimum.
        assert_eq!(steady_min_kib(&[4096]), Some(4096));
        // The warm-up spike first, settled floor last → the floor wins.
        assert_eq!(steady_min_kib(&[5000, 2700, 2650, 2650]), Some(2650));
    }

    #[test]
    fn steady_min_of_an_empty_window_is_none() {
        assert_eq!(steady_min_kib(&[]), None);
    }

    // --- LiveRssMeasurement arithmetic -----------------------------------

    #[test]
    fn within_and_format_use_integer_kib_math() {
        let at = LiveRssMeasurement {
            vmrss_kib: 15 * 1024,
            frames: 8,
            width: 20,
            samples: 8,
        };
        assert!(at.within(15));
        assert!(!at.within(14));
        assert_eq!(
            at.format_line(15),
            "live-idle-rss: 15360 KiB (15.00 MiB) [limit 15 MiB] OK"
        );
        let over = LiveRssMeasurement {
            vmrss_kib: 15 * 1024 + 1,
            ..at
        };
        assert!(!over.within(15));
        assert!(over.format_line(15).ends_with("EXCEEDED"));
    }

    // --- LiveRssError diagnostics ----------------------------------------

    /// The `NoCompositor` reason already carries the `BackendError`'s own
    /// "cannot attach to a compositor:" prefix, so the diagnostic must add only
    /// the program name — not a second copy of the prefix.
    #[test]
    fn the_no_compositor_diagnostic_does_not_double_the_prefix() {
        let reason =
            "cannot attach to a compositor: compositor never sized the layer surface";
        let diag = LiveRssError::NoCompositor(reason.to_string()).diagnostic();
        assert_eq!(
            diag,
            "pengy-panel: cannot attach to a compositor: compositor never sized the layer surface"
        );
        // Exactly one occurrence of the prefix (the doubled form is the bug this
        // test exists to catch).
        assert_eq!(
            diag.matches("cannot attach to a compositor").count(),
            1,
            "{diag}"
        );
    }

    #[test]
    fn the_vmrss_unreadable_diagnostic_names_the_memory_map() {
        let diag = LiveRssError::VmRSSUnreadable.diagnostic();
        assert!(diag.starts_with("pengy-panel: "));
        assert!(diag.contains("VmRSS"), "{diag}");
    }

    // --- run_steady (headless, generic over Backend) ---------------------

    /// The bounded loop runs for roughly `duration` and paints at least one
    /// frame. `HeadlessBackend::wait` returns instantly, so the loop is bounded
    /// purely by the wall clock — this asserts the *termination* contract (it
    /// must not spin forever) with a short window to keep the test fast.
    #[test]
    fn run_steady_runs_to_the_time_limit_and_paints() {
        let mut panel = Panel::new(
            vec![Box::new(widgets::Text::new(Section::Left, "ab"))],
            Box::new(widgets::SysfsSource::new()),
            bar(1),
        );
        let mut backend = HeadlessBackend::new(10);
        let (report, samples) =
            run_steady(&mut panel, &mut backend, Duration::from_millis(3), || Some(1234)).unwrap();
        // `HeadlessBackend::wait` returns instantly, so the loop is bounded purely
        // by the wall clock: at least one frame, possibly many (never forever).
        assert!(report.frames >= 1, "the bounded loop paints at least one frame");
        assert_eq!(report.width, 10, "the open width drives the layout");
        assert_eq!(backend.open_calls, 1);
        assert_eq!(backend.close_calls, 1, "exactly one close");
        // The window is far shorter than SAMPLE_INTERVAL, so exactly one sample.
        assert_eq!(samples, vec![1234], "one sample before the interval elapses");
        // The recorded first frame is laid out on the open width.
        assert_eq!(backend.frame_text(0), "ab        ");
    }

    /// `open`'s error propagates and **nothing is closed** (nothing was
    /// acquired) — the same discipline as [`Panel::run`].
    #[test]
    fn run_steady_propagates_open_errors_without_closing() {
        struct FailingBackend;
        impl Backend for FailingBackend {
            fn open(&mut self) -> Result<u32, crate::backend::BackendError> {
                Err(crate::backend::BackendError::Connect(
                    "no compositor".to_string(),
                ))
            }
            fn wait(&mut self, _timeout: Duration) -> BackendEvent {
                BackendEvent::Tick
            }
            fn paint(&mut self, _frame: &crate::backend::Frame) {}
            fn close(&mut self) {
                panic!("close must not run after a failed open");
            }
        }

        let mut panel = Panel::new(Vec::new(), Box::new(widgets::SysfsSource::new()), bar(1));
        let result = run_steady(&mut panel, &mut FailingBackend, Duration::from_millis(3), || Some(1));
        assert!(result.is_err(), "an open failure must propagate");
    }

    /// A `Resize` mid-window reflows the layout on the next tick: the panel
    /// width the run reports reflects the last resize, and the later frames
    /// are laid out on the new width.
    #[test]
    fn run_steady_applies_resizes_on_the_next_tick() {
        let mut panel = Panel::new(
            vec![
                Box::new(widgets::Text::new(Section::Left, "ab")),
                Box::new(widgets::Text::new(Section::Right, "ef")),
            ],
            Box::new(widgets::SysfsSource::new()),
            bar(1),
        );
        // open = 10; first wait → Resize to 5; then the window elapses.
        let mut backend = HeadlessBackend::with_events(10, vec![BackendEvent::Resize {
            width_cells: 5,
        }]);
        let (report, _) = run_steady(&mut panel, &mut backend, Duration::from_millis(3), || Some(1))
            .unwrap();
        // The report's width is the last width in effect (the resized 5).
        assert_eq!(report.width, 5);
        // Frame 0 (width 10): "ab      ef". Frame 1 (width 5): "ab ef".
        assert_eq!(backend.frame_text(0), "ab      ef");
        assert_eq!(backend.frame_text(1), "ab ef");
    }

    /// The sample callback is honoured: a closure that returns `None` records
    /// nothing, so `steady_min_kib` sees an empty window.
    #[test]
    fn run_steady_honors_a_none_sampling_closure() {
        let mut panel = Panel::new(Vec::new(), Box::new(widgets::SysfsSource::new()), bar(1));
        let mut backend = HeadlessBackend::new(8);
        let (_, samples) =
            run_steady(&mut panel, &mut backend, Duration::from_millis(3), || None).unwrap();
        assert!(samples.is_empty());
        assert_eq!(steady_min_kib(&samples), None);
    }

    // --- self VmRSS read (the sampler the live mode uses) ----------------

    /// The sampler used by the live mode reads *this* process's resident set
    /// size — a real, positive reading on any Linux host (no compositor).
    #[test]
    fn the_process_vmrss_sampler_reads_a_positive_number() {
        let kib = measure_rss::current_vmrss_kib();
        assert!(kib.is_some(), "VmRSS must be readable from /proc/self/status");
        assert!(kib.unwrap() > 0);
    }
}
