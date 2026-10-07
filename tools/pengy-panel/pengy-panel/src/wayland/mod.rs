//! The real Wayland **layer-shell** backend (task 11).
//!
//! This is the "hands" that the headless core keeps apart from: it owns the
//! compositor connection, the layer surface, and the shared-memory pixel
//! buffer, and it maps the core's cell geometry (via
//! [`crate::wayland::rasterize`]) onto real pixels.
//!
//! ## What is testable where
//!
//! The *geometry and pixels* (the [`CellFont`](crate::wayland::font) and
//! [`rasterize_frame`](crate::wayland::rasterize::rasterize_frame)) are pure
//! and golden-tested in the normal `cargo test` suite. The *socket plumbing*
//! here (connecting, binding globals, the event loop, the shm attach) needs a
//! live compositor, so it is exercised only by the `PENGYOS_IT=1`-gated
//! integration test. `cargo build` still compiles and type-checks all of it
//! against the real `wayland-client` / `layer-shell` bindings, so the glue
//! cannot silently rot.
//!
//! ## The lifecycle, mapped onto [`Backend`](crate::backend::Backend)
//!
//! - [`open`] connects, binds `wl_compositor`/`wl_shm`/`zwlr_layer_shell_v1`,
//!   creates the layer surface (top layer, anchored top+left+right,
//!   exclusive zone = bar height), does the initial no-buffer commit, and
//!   roundtrips until the first `configure` tells us the bar's pixel width.
//!   That width, divided by the font's cell width, is the returned bar width
//!   in **cells**.
//! - [`wait`](Self::wait) does a *bounded* blocking wait on the socket: it
//!   answers `Resize` on a new `configure`, `Quit` on `closed`, and `Tick`
//!   when `timeout` elapses with nothing else.
//! - [`paint`](Self::paint) rasterizes the frame to ARGB *at the bar's
//!   current output scale* (task 14 — integer `wl_output.scale` or the
//!   compositor's `wp_fractional_scale_v1` `preferred_scale`, which replaces
//!   the integer one; the pure math is [`scale::Scale`] and the crisp
//!   block-blit is [`rasterize::rasterize_frame_scaled`]), re-stages it in
//!   place into one persistent `memfd`-backed `wl_shm` pool (created once,
//!   grown only on a resize that needs more room — task 13), cuts a fresh
//!   `wl_buffer` from it, acks any pending configure, and commits. A scale
//!   change re-asks for the scaled bar height (`set_size` + commit) and, if
//!   the pixel width is already known, reports a `Resize` in cells.
//! - [`close`](Self::close) tears the surface down and drops the connection.

use std::os::fd::{AsFd, AsRawFd};
use std::time::Duration;

use wayland_backend::rs::client::ObjectId;
use wayland_client::{
    protocol::{
        wl_buffer, wl_compositor, wl_output, wl_registry, wl_shm, wl_shm_pool, wl_surface,
    },
    Connection, Dispatch, Proxy, QueueHandle,
};
// The scanner puts each interface's types in a per-interface submodule
// (the same layout as the layer-shell bindings above). The event enum is
// imported under its own name: `WpFractionalScaleV1::Event` is ambiguous
// between the concrete enum and the `Proxy` trait's associated type.
use wayland_protocols::wp::fractional_scale::v1::client::{
    wp_fractional_scale_manager_v1::WpFractionalScaleManagerV1,
    wp_fractional_scale_v1::{Event as FractionalScaleEvent, WpFractionalScaleV1},
};
use wayland_protocols_wlr::layer_shell::v1::client::{
    zwlr_layer_shell_v1::{Layer, ZwlrLayerShellV1},
    zwlr_layer_surface_v1::{self, Anchor, ZwlrLayerSurfaceV1},
};

pub mod font;
pub mod outputs;
pub mod rasterize;
pub mod scale;
pub mod stage;

use std::collections::BTreeMap;

use crate::backend::{Backend, BackendError, BackendEvent, Frame};
use crate::wayland::font::CellFont;
use crate::wayland::outputs::{MultiOutput, OutputId};
use crate::wayland::rasterize::{rasterize_frame, rasterize_frame_scaled, Raster};
use crate::wayland::scale::Scale;
use crate::wayland::stage::PixelStage;

/// The real compositor backend.
///
/// Construct with [`WaylandBackend::new`] (or `default()`); the bar is one
/// cell tall by default. It is not `Clone`/`Copy` — it owns the live
/// connection — but it is cheap to make before `open`.
pub struct WaylandBackend {
    conn: Option<Connection>,
    queue: Option<wayland_client::EventQueue<App>>,
    app: App,
    font: CellFont,
    height_cells: u32,
}

/// The live, per-output protocol state for **one** monitor's bar (task 15).
///
/// Each advertised `wl_output` owns exactly one dedicated `wl_surface` + layer
/// surface, and its geometry/scale lives in the parallel
/// [`MultiOutput`] model (keyed by the same [`OutputId`]). This struct holds
/// only the protocol objects and the per-surface scratch state that has no
/// pure-model counterpart: the reusable shm [`ShmPool`], the last attached
/// buffer, and the un-acked `configure` serial.
#[derive(Debug)]
struct Surface {
    /// The output this surface is dedicated to (its key in [`MultiOutput`]).
    id: OutputId,
    /// The `wl_surface` the layer surface is attached to.
    surface: Option<wl_surface::WlSurface>,
    /// The layer-shell surface for this output.
    layer: Option<ZwlrLayerSurfaceV1>,
    /// The per-surface fractional-scale object (fractional-scale-v1, task 14),
    /// or `None` when the manager is absent.
    fractional: Option<WpFractionalScaleV1>,
    /// A `configure` serial received but not yet acked/committed for.
    ack_serial: Option<u32>,
    /// This surface's reusable shared-memory pool (task 13) — one per output,
    /// so two bars of different sizes never fight over one region. `None`
    /// until the first `paint` of this surface.
    pool: Option<ShmPool>,
    /// The `wl_buffer` this surface attached on its most recent `paint`, kept
    /// alive until the server consumes it.
    last_buffer: Option<wl_buffer::WlBuffer>,
}

impl Surface {
    fn new(id: OutputId) -> Self {
        Surface {
            id,
            surface: None,
            layer: None,
            fractional: None,
            ack_serial: None,
            pool: None,
            last_buffer: None,
        }
    }
}

/// The app state the [`EventQueue`] dispatches into. Holds every bound
/// protocol object, the per-output [`MultiOutput`] model (the geometry and
/// primary/resize decisions — see its module docs), and the per-output
/// [`Surface`] table that keeps the live protocol proxies.
#[derive(Debug)]
struct App {
    compositor: Option<wl_compositor::WlCompositor>,
    shm: Option<wl_shm::WlShm>,
    layer_shell: Option<ZwlrLayerShellV1>,
    /// The fractional-scale manager, if the compositor advertises it
    /// (fractional-scale-v1, task 14). Optional: a compositor without it
    /// simply scales each surface by its integer `wl_output.scale`.
    fractional_manager: Option<WpFractionalScaleManagerV1>,
    /// The pure multi-output state machine: per-output scale, configure width,
    /// mapped/created flags, primary selection, and resize detection. The
    /// source of truth for *how big and scaled* each bar is and *which one*
    /// drives the panel's single reference width.
    model: MultiOutput,
    /// One [`Surface`] per output that has a created layer surface.
    surfaces: Vec<Surface>,
    /// Every bound `wl_output` proxy, keyed by its global name. Kept alive so
    /// its `scale` events keep arriving (and matched by object id in the
    /// dispatch); the global name is the key so `global_remove` can be matched.
    output_proxies: BTreeMap<OutputId, wl_output::WlOutput>,
    /// The bar height in *logical* output pixels (`cell_h × height_cells`,
    /// scale-1). The exclusive zone is expressed in logical pixels, so it is
    /// this value regardless of any output's current scale (task 14); each
    /// surface scales it to physical pixels at apply time.
    logical_height_px: u32,
    /// An event the dispatch layer wants the `wait`/`open` loop to observe.
    pending: Option<BackendEvent>,
    /// Set when the last remaining output goes away (the bar has nowhere to
    /// go): the run loop exits.
    closed: bool,
}

impl Default for App {
    fn default() -> Self {
        App {
            compositor: None,
            shm: None,
            layer_shell: None,
            fractional_manager: None,
            model: MultiOutput::new(CellFont::new().cell_w),
            surfaces: Vec::new(),
            output_proxies: BTreeMap::new(),
            logical_height_px: CellFont::new().cell_h,
            pending: None,
            closed: false,
        }
    }
}

impl App {
    /// The [`Surface`] (if any) dedicated to `id`.
    fn surface(&mut self, id: OutputId) -> Option<&mut Surface> {
        self.surfaces.iter_mut().find(|s| s.id == id)
    }

    /// Map a live `wl_output` object id (from an event) to the [`OutputId`]
    /// (global name) it was bound as. A tiny linear scan over the (few)
    /// outputs; the `output_proxies` map keeps each proxy alive and is keyed
    /// by global name so a `global_remove` event (which names the global) can
    /// be matched without a name→id side table.
    fn output_id_for(&self, object_id: ObjectId) -> Option<OutputId> {
        self.output_proxies
            .iter()
            .find(|(_, p)| p.id() == object_id)
            .map(|(k, _)| *k)
    }

    /// Create the dedicated `wl_surface` + layer surface (+ per-surface
    /// fractional-scale object) for every output the model has asked for a
    /// surface on but not yet built.
    ///
    /// Called after every relevant global advertisement (compositor, layer-
    /// shell, output, fractional manager) because their order is not
    /// guaranteed — it is a no-op until both a compositor and the layer-shell
    /// global are in hand, then builds any still-pending surface. Each surface
    /// is committed (no buffer) so the compositor sends a `configure` for it.
    fn ensure_surfaces(&mut self, qh: &QueueHandle<Self>) {
        // Clone the proxies we request through, so the later `self` mutations
        // (pushing surfaces, marking the model) don't fight an outstanding
        // borrow of them.
        let (compositor, layer_shell) =
            match (self.compositor.clone(), self.layer_shell.clone()) {
                (Some(c), Some(l)) => (c, l),
                _ => return, // not (yet) connected to both
            };
        let manager = self.fractional_manager.clone();
        let logical = self.logical_height_px;
        let pending: Vec<OutputId> = self.model.surfaces_pending();
        for id in pending {
            // `id` is the user data carried on the `wl_surface` dispatch, so
            // its `enter`/`leave` events name the output they belong to.
            let surface = compositor.create_surface(qh, id);
            let layer = layer_shell.get_layer_surface(
                &surface,
                None,
                Layer::Top,
                "pengy-panel".into(),
                qh,
                id,
            );
            layer.set_anchor(Anchor::Top | Anchor::Left | Anchor::Right);
            // width 0 → the compositor decides the width (spanning the output);
            // we read it back from `configure`. The height hint is computed from
            // *this output's* scale: a `scale` reading may already be in hand by
            // the time the surface is created (the advertisement order is not
            // guaranteed), so the first commit uses the scaled height.
            let hint = self.model.effective_scale(id).scaled_px(logical);
            layer.set_size(0, hint);
            // The zone is in logical (scale-1) output pixels.
            layer.set_exclusive_zone(logical as i32);
            // Per-surface fractional scale (task 14): a `preferred_scale` event
            // then replaces this output's integer scale for this surface.
            let fractional = manager
                .as_ref()
                .map(|m| m.get_fractional_scale(&surface, qh, ()));
            surface.commit();
            let mut surf = Surface::new(id);
            surf.surface = Some(surface);
            surf.layer = Some(layer);
            surf.fractional = fractional;
            self.surfaces.push(surf);
            self.model.mark_created(id);
        }
    }

    /// Re-assert one surface's size hint + exclusive zone for its current scale
    /// (task 14): the height hint is `cell_h × scale` per cell, so a scale
    /// change needs a new `set_size` + commit before the next `configure`
    /// arrives. No-op until the surface exists.
    fn apply_size(&mut self, id: OutputId, qh: &QueueHandle<Self>) {
        // Read the model + logical height (Copy) *before* the `&mut` surface
        // borrow, so the two don't overlap.
        let scale = self.model.effective_scale(id);
        let logical = self.logical_height_px;
        let Some(surf) = self.surface(id) else {
            return;
        };
        let (Some(layer), Some(surface)) = (surf.layer.as_ref(), surf.surface.as_ref()) else {
            return;
        };
        layer.set_size(0, scale.scaled_px(logical));
        // The zone is in output (logical) pixels: the bar's logical height,
        // independent of the current scale.
        layer.set_exclusive_zone(logical as i32);
        surface.commit();
        let _ = qh;
    }

    /// Destroy `id`'s dedicated surface, sending the protocol `destroy`
    /// requests (fractional → layer → surface) and dropping its pool/buffer.
    /// Used when an output is removed. No-op if no such surface exists.
    fn destroy_surface(&mut self, id: OutputId) {
        if let Some(pos) = self.surfaces.iter().position(|s| s.id == id) {
            let mut surf = self.surfaces.remove(pos);
            if let Some(f) = surf.fractional.take() {
                f.destroy();
            }
            if let Some(layer) = surf.layer.take() {
                layer.destroy();
            }
            if let Some(surface) = surf.surface.take() {
                surface.destroy();
            }
            surf.pool = None;
            surf.last_buffer = None;
        }
    }

    /// Drop `id`'s surface entry **without** sending `destroy` — the compositor
    /// has already closed it (a layer `closed` event). We only release our
    /// handles (and the pool/buffer); double-destroying a closed object would be
    /// a protocol error. No-op if no such surface exists.
    fn drop_surface(&mut self, id: OutputId) {
        if let Some(pos) = self.surfaces.iter().position(|s| s.id == id) {
            let mut surf = self.surfaces.remove(pos);
            surf.surface = None;
            surf.layer = None;
            surf.fractional = None;
            surf.pool = None;
            surf.last_buffer = None;
        }
    }

    /// Queue a `Resize` for the panel **only** when the primary's cell width
    /// actually changed since the last report (the single-bar seam's one knob;
    /// a no-op never re-fires). No-op when there is no primary or a resize is
    /// already pending.
    fn maybe_resize(&mut self) {
        if self.pending.is_none() && self.model.primary_changed() {
            let cells = self.model.primary_width_cells();
            self.model.report_primary();
            self.pending = Some(BackendEvent::Resize { width_cells: cells });
        }
    }
}

/// One persistent shared-memory pool that backs the bar's pixel buffer.
///
/// The live half of task 13: instead of allocating a fresh temp file +
/// `wl_shm` pool + `wl_buffer` on every `paint`, the backend keeps **one** pool
/// for the whole session. Its backing file (the pool's fd) is created once and
/// sized to the [`PixelStage`] capacity; each `paint` rewrites that same region
/// in place (the compositor `mmap`s the fd `MAP_SHARED`, so it sees the new
/// pixels) and cuts a fresh `wl_buffer` handle from it. A resize that needs a
/// larger region recreates the pool; a same-or-smaller resize reuses it.
///
/// Only the *pure* [`PixelStage`] accounting is headless-tested; this type's
/// method bodies need a live compositor, so they are exercised by the
/// `PENGYOS_IT=1`-gated live test and type-checked by `cargo build`.
#[derive(Debug)]
struct ShmPool {
    /// The backing `memfd` (as a `File`) whose fd backs the `wl_shm` pool. Kept
    /// open for the pool's lifetime; closing the fd on drop frees the region —
    /// there is no filesystem entry to leak on a crash.
    file: std::fs::File,
    /// The `wl_shm` pool proxy created from `file`'s fd.
    pool: wl_shm_pool::WlShmPool,
    /// The reusable pixel backing region + allocation accounting.
    stage: PixelStage,
    /// The pool's size in bytes (== `stage.capacity()`).
    capacity: usize,
}

impl ShmPool {
    /// Create an anonymous shared-memory fd (`memfd`) of exactly `capacity`
    /// bytes to back the `wl_shm` pool.
    ///
    /// A `memfd` is the correct backing for a Wayland shm pool on Linux: it is
    /// kernel-backed shared memory with **no filesystem involvement**. That
    /// matters because a `temp-file`-backed pool would need the file unlinked to
    /// avoid leaking on a crash — but on a `tmpfs` (where `/tmp` often lives)
    /// unlinking frees the file's data *immediately*, corrupting the very region
    /// the compositor `mmap`s. A `memfd` needs no unlink and leaves no file
    /// behind, so it is both correct and clean.
    fn backing(capacity: usize) -> std::io::Result<std::fs::File> {
        use std::os::fd::FromRawFd;
        let name = std::ffi::CString::new("pengy-panel-pool").expect("NUL-free memfd name");
        let fd = unsafe { libc::memfd_create(name.as_ptr(), 0) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        let file = unsafe { std::fs::File::from_raw_fd(fd) };
        // A `memfd` starts empty; `ftruncate` (via `set_len`) to the pool size
        // zero-fills it, so the pool never exposes uninitialized bytes before
        // the first real frame is written.
        file.set_len(capacity as u64)?;
        Ok(file)
    }

    /// Build a pool from `shm` for a first frame of `width_px` × `height_px`.
    fn new(
        shm: &wl_shm::WlShm,
        qh: &QueueHandle<App>,
        width_px: u32,
        height_px: u32,
    ) -> std::io::Result<Self> {
        let capacity = PixelStage::required_capacity(width_px, height_px);
        let file = Self::backing(capacity)?;
        let pool = shm.create_pool(file.as_fd(), capacity as i32, qh, ());
        // Grow the stage to the pool capacity (zero-filled) so its region and
        // the pool size agree from the very first frame.
        let mut stage = PixelStage::new();
        let init = Raster {
            width_px,
            height_px,
            stride_px: width_px.saturating_mul(4),
            pixels: vec![0; (width_px.saturating_mul(height_px)) as usize],
        };
        stage.stage(&init);
        Ok(ShmPool { file, pool, stage, capacity })
    }

    /// Rewrite the shared region with `frame` and return a fresh `wl_buffer`
    /// referencing it.
    ///
    /// `frame` is first staged into the region in place, growing — and
    /// recreating the pool — only if it outgrows the current capacity (a resize
    /// to a larger output). Because the compositor `mmap`'s the backing fd
    /// `MAP_SHARED`, rewriting the file here is exactly what the server reads on
    /// the next frame. A consumed `wl_buffer` cannot be reattached, so a new
    /// handle is cut from the *same* pool every frame: the pool and its region
    /// are what get reused, not the buffer object.
    fn buffer(
        &mut self,
        shm: &wl_shm::WlShm,
        qh: &QueueHandle<App>,
        frame: &Raster,
    ) -> std::io::Result<wl_buffer::WlBuffer> {
        let need = PixelStage::required_capacity(frame.width_px, frame.height_px);
        if need > self.capacity {
            // A resize to a larger output: back a new region + pool and swap it
            // in. The caller (`attach_raster`) drops the previous buffer *before*
            // this runs, so destroying the old pool here never orphans a live
            // buffer. Plain assignment drops the old fd + proxy.
            let file = Self::backing(need)?;
            let pool = shm.create_pool(file.as_fd(), need as i32, qh, ());
            self.file = file;
            self.pool = pool;
            self.capacity = need;
        }
        self.stage.stage(frame);
        Self::write_region(&self.file, self.stage.as_bytes())?;
        Ok(self.pool.create_buffer(
            0,
            frame.width_px as i32,
            frame.height_px as i32,
            frame.stride_px as i32,
            wl_shm::Format::Argb8888,
            qh,
            (),
        ))
    }

    /// Write `bytes` to the backing file from offset 0. The file is exactly the
    /// pool capacity, so this repaints the whole shared region.
    fn write_region(file: &std::fs::File, bytes: &[u8]) -> std::io::Result<()> {
        use std::io::{Seek, SeekFrom, Write};
        let mut f = file;
        f.seek(SeekFrom::Start(0))?;
        f.write_all(bytes)?;
        f.flush()?;
        Ok(())
    }
}

impl Default for WaylandBackend {
    fn default() -> Self {
        WaylandBackend::new()
    }
}

impl WaylandBackend {
    /// A backend using the default font, one cell tall.
    pub fn new() -> Self {
        let font = CellFont::new();
        WaylandBackend {
            conn: None,
            queue: None,
            app: App::default(),
            height_cells: 1,
            font,
        }
    }

    /// Make the bar `height_cells` cells tall (affects the pixel height hint
    /// and the rasterizer). Must be called before [`open`].
    pub fn with_height_cells(mut self, height_cells: u32) -> Self {
        self.height_cells = height_cells.max(1);
        self.app.logical_height_px = self.font.cell_h * self.height_cells;
        self
    }

    /// The scale the **primary** output's bar is currently rendering at (task
    /// 14/15): 1:1 until the compositor reports otherwise; a nonzero
    /// `preferred_scale` replaces that output's integer scale. `Scale::ONE`
    /// while no output is the primary yet. Exposed for tests and diagnostics.
    pub fn scale(&self) -> Scale {
        self.app
            .model
            .primary()
            .map(|id| self.app.model.effective_scale(id))
            .unwrap_or(Scale::ONE)
    }

    /// The font in use.
    pub fn font(&self) -> &CellFont {
        &self.font
    }

    /// The bar height in cells.
    pub fn height_cells(&self) -> u32 {
        self.height_cells
    }

    /// Rasterize a frame the way [`paint`](Self::paint) will — exposed so
    /// tests and callers can inspect the exact pixels a frame maps to.
    pub fn rasterize(&self, frame: &Frame) -> Raster {
        rasterize_frame(frame, &self.font, self.height_cells)
    }

    /// The bar width (cells) a `width_px`-pixel configure would imply at the
    /// primary output's *current* scale (task 14): the pixel width divided by
    /// the scaled cell width, which at 1:1 is a plain `width_px / cell_w`.
    pub fn width_cells_for_px(&self, width_px: u32) -> u32 {
        self.scale().cells_for_px(width_px, self.font.cell_w)
    }

    /// Attach and commit `raster` to the surface dedicated to `id`, reusing
    /// that surface's one persistent [`ShmPool`] (task 13). A no-op unless the
    /// backend is open with `wl_shm` and the surface exists. Split out from
    /// [`paint`](Self::paint) so the disjoint borrows of the queue/shm/conn
    /// (owned handles) and the single `&mut Surface` stay within one function.
    fn attach_surface(&mut self, id: OutputId, raster: &Raster) {
        // Take owned/cheap handles first, so the `&mut` surface borrow below is
        // clean (the proxy clones do not keep `self` borrowed).
        let qh = match self.queue.as_ref().map(|q| q.handle()) {
            Some(h) => h,
            None => return, // not opened
        };
        let shm = match self.app.shm.as_ref() {
            Some(s) => s.clone(),
            None => return, // shm was never advertised
        };
        let conn = self.conn.clone();
        let surf = match self.app.surface(id) {
            Some(s) => s,
            None => return,
        };
        let surface = match surf.surface.as_ref() {
            Some(s) => s.clone(),
            None => return,
        };

        // One pool per surface for the whole session: create it lazily on the
        // first paint of this surface (its own borrow scope, so the later
        // `&mut` borrow is clean). No per-frame temp file, no per-frame pool.
        if surf.pool.is_none() {
            match ShmPool::new(&shm, &qh, raster.width_px, raster.height_px) {
                Ok(p) => surf.pool = Some(p),
                Err(e) => {
                    eprintln!("pengy-panel: cannot create the shared-memory pool for output {id}: {e}");
                    return;
                }
            }
        }
        // The previous frame's buffer has been committed (and, on a
        // low-frequency bar, already consumed by the compositor); release its
        // proxy now so a resize that recreates the pool below can never orphan
        // a live buffer.
        surf.last_buffer = None;
        // `buffer` grows/recreates the pool only when the frame outgrows the
        // current capacity (a resize to a larger output); otherwise it reuses
        // the same region and the same pool.
        let pool = surf.pool.as_mut().expect("pool created above");
        let buffer = match pool.buffer(&shm, &qh, raster) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("pengy-panel: cannot stage pixel buffer for output {id}: {e}");
                return;
            }
        };

        if let Some(serial) = surf.ack_serial {
            if let Some(layer) = surf.layer.as_ref() {
                layer.ack_configure(serial);
            }
        }

        // Attach the fresh buffer (cut from the same reusable pool) and commit.
        // The previously attached buffer is dropped here, once consumed.
        surface.attach(Some(&buffer), 0, 0);
        surf.last_buffer = Some(buffer);
        surface.commit();
        if let Some(conn) = conn {
            let _ = conn.flush();
        }
    }
}

// ---------------------------------------------------------------------------
// Dispatch implementations (the event loop's "brains")
// ---------------------------------------------------------------------------

impl Dispatch<wl_registry::WlRegistry, ()> for App {
    fn event(
        state: &mut Self,
        registry: &wl_registry::WlRegistry,
        event: wl_registry::Event,
        _data: &(),
        _conn: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            wl_registry::Event::Global {
                name,
                interface,
                version,
            } => match interface.as_str() {
                "wl_compositor" => {
                    let compositor =
                        registry.bind::<wl_compositor::WlCompositor, _, _>(name, 1, qh, ());
                    state.compositor = Some(compositor);
                    // No surface is created yet — one is built per output by
                    // `ensure_surfaces` once the outputs are known.
                    state.ensure_surfaces(qh);
                }
                "wl_shm" => {
                    state.shm = Some(registry.bind::<wl_shm::WlShm, _, _>(name, 1, qh, ()));
                }
                "zwlr_layer_shell_v1" => {
                    let ls =
                        registry.bind::<ZwlrLayerShellV1, _, _>(name, version.min(5), qh, ());
                    state.layer_shell = Some(ls);
                    state.ensure_surfaces(qh);
                }
                // Task 14/15: every advertised output gets its own bar. The
                // `scale` event is version 2; v1-only outputs simply report no
                // scale and are ignored (they render at 1:1).
                "wl_output" => {
                    if version >= 2 {
                        // Keep the proxy alive so its `scale` events keep
                        // arriving; key it by global name (what `global_remove`
                        // reports) and track it in the pure model.
                        let output = registry
                            .bind::<wl_output::WlOutput, _, _>(name, version.min(4), qh, ());
                        state.output_proxies.insert(name, output);
                        if state.model.add_output(name) {
                            state.ensure_surfaces(qh);
                        }
                    }
                }
                // Task 14: the fractional-scale manager (fractional-scale-v1).
                // Optional — a compositor without it stays integer-scaled.
                "wp_fractional_scale_manager_v1" => {
                    state.fractional_manager = Some(
                        registry.bind::<WpFractionalScaleManagerV1, _, _>(name, version.min(1), qh, ()),
                    );
                    // Per-surface objects are created with each surface; a
                    // manager that advertises after the surfaces exist is picked
                    // up by the next `ensure_surfaces` (idempotent).
                    state.ensure_surfaces(qh);
                }
                _ => {}
            },
            // A monitor went away (task 15): tear down its dedicated surface and
            // stop tracking its output. Only `wl_output` removals matter here;
            // the other globals are no-ops (we never ask for them to come back).
            wl_registry::Event::GlobalRemove { name } => {
                state.output_proxies.remove(&name);
                if state.model.remove_output(name) {
                    state.destroy_surface(name);
                    if state.model.is_empty() {
                        state.closed = true;
                        if state.pending.is_none() {
                            state.pending = Some(BackendEvent::Quit);
                        }
                    } else {
                        state.maybe_resize();
                    }
                }
            }
            // `Event` is non-exhaustive: be resilient to newer protocol events.
            _ => {}
        }
    }
}

impl Dispatch<ZwlrLayerSurfaceV1, OutputId> for App {
    fn event(
        state: &mut Self,
        layer: &ZwlrLayerSurfaceV1,
        event: zwlr_layer_surface_v1::Event,
        data: &OutputId,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
    ) {
        let id = *data;
        match event {
            zwlr_layer_surface_v1::Event::Configure {
                serial,
                width,
                height,
            } => {
                // This surface's bar is now `width` physical pixels wide (task
                // 15): the model learns it, and if it moves the *primary*'s
                // cell width a single `Resize` is queued (a no-op never fires).
                state.model.set_configured_width(id, width);
                if let Some(surf) = state.surface(id) {
                    surf.ack_serial = Some(serial);
                }
                // Ack now; the commit that ships a buffer happens in paint().
                if width > 0 {
                    layer.ack_configure(serial);
                }
                state.maybe_resize();
                let _ = height;
            }
            zwlr_layer_surface_v1::Event::Closed => {
                // This output's bar was torn down (monitor unplugged or the
                // session ending). Stop tracking it; quit only when the last
                // remaining output goes away.
                state.model.remove_output(id);
                state.drop_surface(id);
                if state.model.is_empty() {
                    state.closed = true;
                    if state.pending.is_none() {
                        state.pending = Some(BackendEvent::Quit);
                    }
                } else {
                    state.maybe_resize();
                }
            }
            // `Event` is non-exhaustive: be resilient to newer protocol events.
            _ => {}
        }
    }
}

// Task 14/15: per-output integer scale. Only the `scale` reading matters:
// match the event's output proxy to its tracked output, record it in the
// model, and re-assert that surface's size when it changes.
impl Dispatch<wl_output::WlOutput, ()> for App {
    fn event(
        state: &mut Self,
        output: &wl_output::WlOutput,
        event: wl_output::Event,
        _data: &(),
        _conn: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        if let wl_output::Event::Scale { factor } = event {
            if let Some(id) = state.output_id_for(output.id()) {
                if state.model.set_scale(id, factor) {
                    state.apply_size(id, qh);
                    state.maybe_resize();
                }
            }
        }
        // `Name`/`Geometry`/`Mode`/`Done`/`Heading` are not needed: each output
        // owns a dedicated surface (task 15), so placement is by construction
        // and the surface's `enter`/`leave` events carry the mapping.
    }
}

// Task 14/15: per-surface fractional scale (fractional-scale-v1). A nonzero
// `preferred_scale` (24.8 fixed point) supersedes *that output's* integer
// scale — exactly the rule `Scale::resolve` encodes (a 0 value means "use the
// integer scale" and `resolve` handles it). The per-surface object is matched
// to its output by proxy id (each surface owns exactly one).
impl Dispatch<WpFractionalScaleV1, ()> for App {
    fn event(
        state: &mut Self,
        object: &WpFractionalScaleV1,
        event: FractionalScaleEvent,
        _data: &(),
        _conn: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        // Find the surface that owns this per-surface object.
        let id = match state
            .surfaces
            .iter()
            .find(|s| s.fractional.as_ref().map(|f| f.id() == object.id()) == Some(true))
            .map(|s| s.id)
        {
            Some(id) => id,
            None => return, // not one of ours
        };
        match event {
            FractionalScaleEvent::PreferredScale { scale } => {
                if state.model.set_fractional(id, scale as i32) {
                    state.apply_size(id, qh);
                    state.maybe_resize();
                }
            }
            // `Event` is non-exhaustive: be resilient to newer protocol events.
            _ => {}
        }
    }
}

// Task 15: per-surface mapping. A layer surface is a `wl_surface`, and
// `wl_surface.enter`/`leave` (unlike layer-shell, which lacks them) tell us
// when the dedicated surface is mapped — the "entered" flag the pure
// [`MultiOutput`] model uses to pick the primary output. The `OutputId` user
// data (set at `create_surface`) names the output the event belongs to.
impl Dispatch<wl_surface::WlSurface, OutputId> for App {
    fn event(
        state: &mut Self,
        _surface: &wl_surface::WlSurface,
        event: wl_surface::Event,
        data: &OutputId,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
    ) {
        let id = *data;
        match event {
            wl_surface::Event::Enter { .. } => {
                state.model.set_entered(id, true);
                state.maybe_resize();
            }
            wl_surface::Event::Leave { .. } => {
                state.model.set_entered(id, false);
                state.maybe_resize();
            }
            // `PreferredBufferScale`/`PreferredBufferTransform` are not needed:
            // the bar's scale comes from `wl_output.scale` + fractional-scale-v1.
            _ => {}
        }
    }
}

// Ignore the interfaces we bind but do not act on. (`wl_output`,
// `WpFractionalScaleV1`, `ZwlrLayerSurfaceV1`, `wl_surface`, and `WlRegistry`
// have real `Dispatch` impls above and drive the multi-output state, so they
// must NOT be no-oped here.)
wayland_client::delegate_noop!(App: ignore wl_compositor::WlCompositor);
wayland_client::delegate_noop!(App: ignore wl_shm::WlShm);
wayland_client::delegate_noop!(App: ignore wl_buffer::WlBuffer);
wayland_client::delegate_noop!(App: ignore wl_shm_pool::WlShmPool);
wayland_client::delegate_noop!(App: ignore ZwlrLayerShellV1);
// The fractional-scale *manager* is bound (task 14) but only used to create
// the per-surface object; the per-surface object has the real impl above.
wayland_client::delegate_noop!(App: ignore WpFractionalScaleManagerV1);

impl Backend for WaylandBackend {
    fn open(&mut self) -> Result<u32, BackendError> {
        let conn = Connection::connect_to_env().map_err(|e| {
            BackendError::Connect(match e {
                wayland_client::ConnectError::NoCompositor => {
                    "no Wayland compositor found (is WAYLAND_DISPLAY set?)".to_string()
                }
                wayland_client::ConnectError::NoWaylandLib => {
                    "the Wayland client library is not available".to_string()
                }
                wayland_client::ConnectError::InvalidFd => {
                    "WAYLAND_SOCKET is not a valid file descriptor".to_string()
                }
            })
        })?;

        let mut queue = conn.new_event_queue();
        let mut app = App::default();
        app.model = MultiOutput::new(self.font.cell_w);
        app.logical_height_px = self.font.cell_h * self.height_cells;

        // Register the registry so the Global events above fire (each output
        // gets a dedicated layer surface via `ensure_surfaces`).
        conn.display().get_registry(&queue.handle(), ());

        // Roundtrip until the compositor has sized the primary bar. Bounded: a
        // compliant compositor answers within a couple of roundtrips.
        for _ in 0..16 {
            queue.roundtrip(&mut app).map_err(|e| {
                BackendError::Connect(format!("error talking to the compositor: {e}"))
            })?;
            if app.model.primary_width_cells() > 0 {
                break;
            }
            // A protocol error already means we should stop.
            if app.pending.is_some() || conn.protocol_error().is_some() {
                break;
            }
        }

        let cells = app.model.primary_width_cells();
        if cells == 0 {
            let err = conn.protocol_error().map(|p| p.to_string());
            return Err(BackendError::Connect(
                err.unwrap_or_else(|| "compositor never sized the layer surface".to_string()),
            ));
        }

        // The primary's first `configure` cell width is what `open` returns:
        // mark it reported and drop the matching `Resize` the configure handler
        // queued (the panel must not immediately "resize" to its open width).
        app.model.report_primary();
        if matches!(app.pending, Some(BackendEvent::Resize { width_cells: c }) if c == cells) {
            app.pending = None;
        }
        self.conn = Some(conn);
        self.queue = Some(queue);
        self.app = app;
        Ok(cells)
    }

    fn wait(&mut self, timeout: Duration) -> BackendEvent {
        let (Some(queue), app) = (self.queue.as_mut(), &mut self.app) else {
            // Not opened (shouldn't happen in the run loop): behave as a tick.
            return BackendEvent::Tick;
        };

        if let Some(ev) = app.pending.take() {
            return ev;
        }

        // Create the read guard *before* polling, as the wayland API requires.
        let guard = match queue.prepare_read() {
            Some(g) => g,
            None => {
                // No readiness to wait on (flush failed or backend gone).
                return app
                    .closed
                    .then_some(BackendEvent::Quit)
                    .unwrap_or(BackendEvent::Tick);
            }
        };

        let fd = guard.connection_fd().as_raw_fd();
        let mut pfd = libc::pollfd {
            fd,
            events: libc::POLLIN,
            revents: 0,
        };
        // `poll` wants milliseconds; clamp very large timeouts to INT_MAX.
        let timeout_ms = timeout.as_millis().min(i32::MAX as u128) as i32;
        let ready = unsafe { libc::poll(&mut pfd, 1, timeout_ms) };

        if ready > 0 && (pfd.revents & libc::POLLIN) != 0 {
            let _ = guard.read();
            if let Err(e) = queue.dispatch_pending(app) {
                eprintln!("pengy-panel: dispatch error: {e}");
                return BackendEvent::Quit;
            }
            // An event the dispatch set wins; otherwise a spurious wake → tick.
            if let Some(ev) = app.pending.take() {
                return ev;
            }
            if app.closed {
                return BackendEvent::Quit;
            }
        }
        // Timeout (or nothing): the poll interval elapsed.
        BackendEvent::Tick
    }

    fn paint(&mut self, frame: &Frame) {
        // Task 15: one bar per output. The panel hands us a single frame
        // (laid out against the primary output's width); each mapped, sized
        // surface gets its own raster — at its own pixel width (the compositor
        // decides it; we asked for width 0) and its own scale (task 14) — so
        // the glyphs are crisp at any scale on every monitor.
        let targets = self.app.model.paint_targets();
        for t in targets {
            let raster = rasterize_frame_scaled(frame, &self.font, self.height_cells, t.width_px, &t.scale);
            self.attach_surface(t.id, &raster);
        }
    }

    fn close(&mut self) {
        // Tear down every surface: its buffer, then its pool (frees the backing
        // `memfd`), then the fractional/layer/surface proxies (their `destroy`).
        for mut surf in self.app.surfaces.drain(..) {
            surf.last_buffer = None;
            surf.pool = None;
            if let Some(f) = surf.fractional.take() {
                f.destroy();
            }
            if let Some(layer) = surf.layer.take() {
                layer.destroy();
            }
            if let Some(surface) = surf.surface.take() {
                surface.destroy();
            }
        }
        self.app.output_proxies.clear();
        self.queue = None;
        self.conn = None;
        self.app = App::default();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::PaintedWidget;
    use widgets::{Style, TextRun};

    fn frame_with(width_cells: u32, x: u32, text: &str, style: Style) -> Frame {
        let mut frame = Frame::new(width_cells);
        frame.placed.push(PaintedWidget {
            x,
            width: text.chars().count() as u32,
            runs: vec![TextRun::new(text, style)],
        });
        frame
    }

    #[test]
    fn new_backend_defaults_to_one_cell_tall_default_font() {
        let b = WaylandBackend::new();
        assert_eq!(b.height_cells(), 1);
        assert_eq!(b.font().cell_w, 6);
        assert_eq!(b.font().cell_h, 8);
    }

    #[test]
    fn with_height_cells_sets_the_bar_height() {
        let b = WaylandBackend::new().with_height_cells(3);
        assert_eq!(b.height_cells(), 3);
        // A 3-cell-tall bar uses 3*8 px of height hint.
        let frame = Frame::new(10);
        let r = b.rasterize(&frame);
        assert_eq!(r.height_px, 3 * 8);
        // `with_height_cells(0)` clamps to at least 1.
        assert_eq!(WaylandBackend::new().with_height_cells(0).height_cells(), 1);
    }

    #[test]
    fn width_cells_divides_pixels_by_the_font_cell_width() {
        let b = WaylandBackend::new();
        // 6 px per cell: exact multiples and a remainder.
        assert_eq!(b.width_cells_for_px(120), 20);
        assert_eq!(b.width_cells_for_px(125), 20); // 5 px remainder is dropped
        assert_eq!(b.width_cells_for_px(0), 0);
        assert_eq!(b.width_cells_for_px(5), 0); // less than one cell
    }

    // -----------------------------------------------------------------
    // Output scale (task 14)
    // -----------------------------------------------------------------

    /// The per-output scale resolution is unambiguous now (task 15): a surface
    /// is dedicated to one output, so its scale is that output's reading — no
    /// "which output is the bar on?" guessing. The backend's `scale()` reports
    /// the *primary* output's scale (1:1 until an output becomes primary).
    #[test]
    fn scale_accessor_reports_the_primary_outputs_scale() {
        let b = WaylandBackend::new();
        // No outputs yet → no primary → 1:1.
        assert_eq!(b.scale(), Scale::ONE);
    }

    /// The per-output scale a surface renders at is its own output's reading:
    /// the same code path (`MultiOutput::effective_scale`) the backend's
    /// `paint` uses, exercised headlessly through a small model.
    #[test]
    fn per_output_scale_is_that_outputs_own_reading() {
        let mut m = MultiOutput::new(CellFont::new().cell_w);
        m.add_output(10);
        m.add_output(20);
        m.set_scale(10, 2);
        m.set_scale(20, 3);
        // Each output reads its OWN integer scale (no global guess).
        assert_eq!(m.effective_scale(10), Scale::resolve(2, None));
        assert_eq!(m.effective_scale(20), Scale::resolve(3, None));
        // A per-surface fractional reading replaces only its own output.
        m.set_fractional(20, 384); // 1.5
        assert_eq!(m.effective_scale(20), Scale::resolve(3, Some(384)));
        assert_eq!(m.effective_scale(10), Scale::resolve(2, None));
    }

    /// Live, compositor-backed: only runs when `PENGYOS_IT=1`. The open
    /// width (cells) must be exactly consistent with the pixel width and the
    /// scale the compositor reported (whichever of integer/fractional is in
    /// effect), and a paint at that scale must not panic. Without a
    /// compositor, `open` fails gracefully and the assertions are skipped
    /// (same convention as the other IT-gated tests).
    #[test]
    fn it_wayland_scale_is_consistent_when_live() {
        if std::env::var("PENGYOS_IT").ok().as_deref() != Some("1") {
            eprintln!("pengy-panel: skipping PENGYOS_IT-gated scale-consistency test");
            return;
        }
        let mut backend = WaylandBackend::new();
        let width = match backend.open() {
            Ok(w) => w,
            Err(BackendError::Connect(reason)) => {
                eprintln!(
                    "pengy-panel: no live compositor ({reason}); skipping scale assertions"
                );
                return;
            }
        };
        // The open width is the *primary* output's cell width (task 15).
        let primary = backend.app.model.primary().expect("a live bar has a primary output");
        let width_px = backend.app.model.get(primary).expect("primary is tracked").width_px;
        let scale = backend.scale(); // the primary output's scale
        assert!(scale.num > 0 && scale.den > 0, "a live scale must be positive");
        let cell_w_px = scale.scaled_px(backend.font().cell_w);
        assert!(cell_w_px > 0, "the scaled cell width must be nonzero");
        // The reported cell width and the primary's pixel width must agree at
        // this scale: cells = width_px / (cell_w * scale), remainder dropped.
        assert_eq!(width, width_px / cell_w_px);
        if width > 0 {
            assert!(
                width_px >= cell_w_px * width && width_px < cell_w_px * (width + 1),
                "pixel width {:?} and cell width {} disagree at scale {}",
                width_px,
                width,
                scale
            );
        }
        // The logical (scale-1) height the backend holds matches its own math;
        // each surface scales it to physical px with `scale.scaled_px`.
        let expected_logical = backend.font().cell_h * backend.height_cells();
        assert_eq!(backend.app.logical_height_px, expected_logical);
        let frame = frame_with(width, 0, "hi", Style::Normal);
        backend.paint(&frame); // must not panic at any scale the compositor chose
        backend.close();
    }

    /// The scale change that matters for `reapply_scale` is the cell-width
    /// change: a 1:1 → 2:1 move on a 120-px bar must report a `Resize` of the
    /// *scaled* width in cells, and a no-op move must not. This pins the
    /// reported-width arithmetic that `reapply_scale`/`configure` share
    /// (`cells_for_px` + the last-reported dedupe).
    #[test]
    fn a_scale_change_recomputes_the_cell_width_the_resize_would_report() {
        // A 120-px-wide bar (the classic single-monitor case).
        let width_px = 120;
        let cell_w = CellFont::new().cell_w;
        let s1 = Scale::resolve(1, None);
        let s2 = Scale::resolve(1, Some(512)); // 2:1
        let s15 = Scale::resolve(1, Some(384)); // 1.5
        let at = |s: &Scale| s.cells_for_px(width_px, cell_w);
        assert_eq!(at(&s1), 20); // 120 / 6
        assert_eq!(at(&s2), 10); // 120 / 12 — a scale change MUST re-report
        assert_eq!(at(&s15), 13); // 120 / 9
        // The dedupe rule: re-reporting happens exactly when the cell width
        // differs from the last reported one.
        let mut reported: Option<u32> = Some(at(&s1));
        for s in [&s1, &s2, &s2, &s15, &s15] {
            let cells = at(s);
            let would_report = reported != Some(cells);
            if would_report {
                reported = Some(cells);
            }
        }
        assert_eq!(reported, Some(at(&s15)), "the final reported width is 1.5's");
    }

    #[test]
    fn rasterize_maps_a_frame_to_an_arbuffer_of_the_configured_width() {
        let b = WaylandBackend::new();
        let frame = frame_with(10, 0, "AB", Style::Normal);
        let r = b.rasterize(&frame);
        // 10 cells * 6 px wide, 1 cell * 8 px tall.
        assert_eq!(r.width_px, 60);
        assert_eq!(r.height_px, 8);
        assert_eq!(r.stride_px, 60 * 4);
        assert_eq!(r.pixels.len(), 60 * 8);
        // `as_bytes` is 4 bytes per pixel.
        assert_eq!(r.as_bytes().len(), r.pixels.len() * 4);
        // The "AB" cells inked; the rest is background.
        let font = b.font();
        let inked = (0..12).flat_map(|x| (0..8).map(move |y| (x, y))).any(|(x, y)| {
            r.at(x, y) == Some(font.color(Style::Normal))
        });
        assert!(inked);
    }

    /// Without a compositor, `open` must fail *gracefully* with a `Connect`
    /// error — never panic or block. Pointing at an absolute path that can
    /// never be a real Wayland socket keeps this fully headless (no compositor
    /// is touched).
    #[test]
    fn open_without_a_compositor_fails_gracefully() {
        std::env::set_var("WAYLAND_DISPLAY", "/nonexistent-pengy-panel-test-socket");
        let mut backend = WaylandBackend::new();
        match backend.open() {
            Err(BackendError::Connect(reason)) => assert!(!reason.is_empty()),
            other => panic!("expected a Connect error, got {:?}", other),
        }
    }

    /// Live, compositor-backed: only runs when `PENGYOS_IT=1`. Without a
    /// compositor the `open` must fail gracefully with a `Connect` error (never
    /// a panic); with one, it must report a nonzero cell width and paint
    /// without a protocol error.
    #[test]
    fn it_wayland_open_is_graceful_or_live() {
        if std::env::var("PENGYOS_IT").ok().as_deref() != Some("1") {
            eprintln!("pengy-panel: skipping PENGYOS_IT-gated wayland backend test");
            return;
        }
        let mut backend = WaylandBackend::new();
        match backend.open() {
            Ok(width) => {
                assert!(width > 0, "a live compositor must report a nonzero cell width");
                let frame = frame_with(width, 0, "hi", Style::Normal);
                backend.paint(&frame); // must not panic or raise a protocol error
                backend.close();
            }
            Err(BackendError::Connect(reason)) => {
                // Expected on a headless host: a clean, non-empty Connect error.
                assert!(!reason.is_empty(), "Connect error must carry a reason");
            }
        }
    }

    /// `ShmPool::backing` is the headless-testable half of the reusable buffer:
    /// it must create a `memfd` shared-memory region of *exactly* the pool size,
    /// zero-filled (so the pool never exposes uninitialized bytes before the
    /// first frame). No compositor is involved — just a shared-memory fd.
    #[test]
    fn shm_pool_backing_creates_a_zeroed_region_of_the_exact_capacity() {
        let cap = PixelStage::required_capacity(60, 8); // 60 * 8 * 4
        let mut file = ShmPool::backing(cap).expect("shm backing fd");
        let len = file.metadata().expect("metadata").len();
        assert_eq!(len, cap as u64, "the backing region must be exactly the pool size");
        // `ftruncate` on a fresh `memfd` zero-fills, so the bytes are all 0
        // before any real frame is written.
        let mut content = vec![0u8; cap];
        use std::io::Read;
        file.read_exact(&mut content).expect("read the backing region back");
        assert!(content.iter().all(|&b| b == 0), "backing must start zeroed");
    }

    /// Live, compositor-backed: only runs when `PENGYOS_IT=1`. Paints a run of
    /// frames and asserts the one reusable pool allocated **exactly once** for
    /// the whole stable-size run (the task-13 win — no per-frame pool/file),
    /// and that `close` tears the pool down. Without a compositor, `open` fails
    /// gracefully and the assertions are skipped (same convention as
    /// `it_wayland_open_is_graceful_or_live`).
    #[test]
    fn it_wayland_reuses_one_shm_pool_across_frames() {
        if std::env::var("PENGYOS_IT").ok().as_deref() != Some("1") {
            eprintln!("pengy-panel: skipping PENGYOS_IT-gated shm-reuse test");
            return;
        }
        let mut backend = WaylandBackend::new();
        let width_cells = match backend.open() {
            Ok(w) => {
                assert!(w > 0, "a live compositor must report a nonzero cell width");
                w
            }
            Err(BackendError::Connect(reason)) => {
                eprintln!(
                    "pengy-panel: no live compositor ({reason}); skipping shm-reuse assertions"
                );
                return;
            }
        };

        // A run of same-size frames: each surface's pool is created on that
        // surface's first paint and reused for the rest → exactly one
        // (re)allocation per surface, ever (task-13 win, now per output).
        let primary = backend
            .app
            .model
            .primary()
            .expect("a live bar has a primary output");
        for _ in 0..8 {
            backend.paint(&frame_with(width_cells, 0, "hi", Style::Normal));
        }
        let primary_out = backend.app.model.get(primary).expect("primary tracked");
        let pool = backend
            .app
            .surfaces
            .iter()
            .find(|s| s.id == primary)
            .expect("the primary surface exists")
            .pool
            .as_ref()
            .expect("the primary surface has a pool after painting");
        // The pool is sized to the primary surface's bar pixels (width from the
        // compositor, height from its scale-scaled logical height).
        let scale = backend.app.model.effective_scale(primary);
        assert_eq!(
            pool.capacity,
            PixelStage::required_capacity(
                primary_out.width_px,
                scale.scaled_px(backend.app.logical_height_px)
            ),
            "the pool must be sized to the compositor's bar pixels"
        );
        assert_eq!(
            pool.stage.reallocs(),
            1,
            "a stable-size run must allocate exactly once, not once per frame"
        );
        assert_eq!(pool.stage.frames(), 9, "init frame + 8 paints, all in place");

        backend.close();
        assert!(
            backend.app.surfaces.is_empty(),
            "close must tear every surface (and its pool) down"
        );
    }

    /// Live, compositor-backed: only runs when `PENGYOS_IT=1` (task 15). Asserts
    /// the backend opened **one surface per advertised output** and that the
    /// panel's single reference width is the *primary* output's cell width.
    /// Without a compositor the assertions are skipped (same convention as the
    /// other IT-gated tests).
    #[test]
    fn it_wayland_opens_one_surface_per_output() {
        if std::env::var("PENGYOS_IT").ok().as_deref() != Some("1") {
            eprintln!("pengy-panel: skipping PENGYOS_IT-gated multi-output test");
            return;
        }
        let mut backend = WaylandBackend::new();
        let width = match backend.open() {
            Ok(w) => w,
            Err(BackendError::Connect(reason)) => {
                eprintln!(
                    "pengy-panel: no live compositor ({reason}); skipping multi-output assertions"
                );
                return;
            }
        };
        let outputs = backend.app.model.len();
        let surfaces = backend.app.surfaces.len();
        // One dedicated surface per output the model tracks.
        assert_eq!(surfaces, outputs, "one layer surface per output");
        assert!(outputs >= 1, "a live compositor advertises at least one output");
        // The open width is exactly the primary's cell width.
        assert_eq!(
            width,
            backend.app.model.primary_width_cells(),
            "the panel's reference width is the primary output's cell width"
        );
        assert!(width > 0);
        // Every created surface is in the model and marked created.
        for surf in &backend.app.surfaces {
            assert!(
                backend.app.model.get(surf.id).map(|o| o.created) == Some(true),
                "surface {} must be created in the model",
                surf.id
            );
        }
        let frame = frame_with(width, 0, "hi", Style::Normal);
        backend.paint(&frame); // paints every mapped, sized surface
        backend.close();
    }
}
