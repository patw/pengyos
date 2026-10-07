//! Pure **multi-output** bookkeeping for the Wayland backend (task 15).
//!
//! The single-surface backend (tasks 11–14) owned one layer surface and one
//! scale. Supporting **multiple outputs — one layer surface per monitor** —
//! means the backend owns *N* surfaces, each with its own pixel width, its own
//! scale, and its own exclusive zone. That state machine is exactly the part
//! that is *not* testable against a live compositor, so it is split here into
//! a pure, headless-testable model ([`MultiOutput`]), mirroring how
//! [`super::scale::Scale`] (task 14) and [`super::stage::PixelStage`] (task 13)
//! carry the pure half while the socket plumbing stays `PENGYOS_IT=1`-gated.
//!
//! ## Why per-output state is unambiguous here
//!
//! The wlr `layer-shell` protocol reports only `configure` and `closed` for a
//! layer surface — it has **no** `output_enter`/`output_leave` (documented in
//! the task-14 notes). So a *single* layer surface cannot know which of several
//! outputs the compositor placed it on, and the task-14 `integer_scale` rule
//! had to guess (one output / uniform / else 1).
//!
//! With **one surface per output**, that ambiguity disappears: a layer surface
//! is a `wl_surface`, and `wl_surface` *does* emit `enter(output)` /
//! `leave(output)`. Each dedicated surface therefore knows the output it is
//! mapped on, so its scale is that output's reading — no guessing. [`MultiOutput`]
//! models exactly this: every output carries its own `wl_output.scale` reading,
//! its own per-surface fractional scale, and its own configure width, and
//! [`MultiOutput::effective_scale`] resolves a surface's scale from *its*
//! output rather than from an ambiguous global list.
//!
//! ## What the model knows, and what it doesn't
//!
//! [`MultiOutput`] knows *which* surfaces exist and *how big / scaled* each is,
//! and — crucially — which one is the **primary** (the output whose width the
//! single-bar [`Backend`](crate::backend::Backend) seam reports, so the panel
//! lays out against it). It does **not** own the Wayland proxies themselves
//! (a `WlSurface`, a `ZwlrLayerSurfaceV1`, a shm pool): the live backend keeps
//! those in a parallel [`Surface`] table keyed by the same [`OutputId`], and
//! asks the model for the [`PaintTarget`] list to drive a paint pass.

use std::collections::BTreeMap;

use crate::wayland::scale::Scale;

/// An output identity — the [`u32`] global name the registry advertised the
/// `wl_output` with. It is stable for the output's lifetime on a connection and
/// is the key the live backend uses to pair each output with its dedicated
/// layer surface.
pub type OutputId = u32;

/// One monitor the panel paints a bar on.
///
/// All fields are plain data, so an [`Output`] is trivially inspectable in a
/// headless test (the model carries no Wayland proxies).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Output {
    /// The latest `wl_output.scale` reading for this output (`1` until the
    /// first event; the protocol guarantees ≥ 1 for valid compositors).
    pub integer_scale: i32,
    /// The latest per-surface `wp_fractional_scale_v1.preferred_scale` (24.8
    /// fixed point) for the surface dedicated to this output, or `None` (no
    /// fractional-scale-v1 manager, no event yet, or a `0` value — which the
    /// protocol uses to mean "use the integer scale"). See
    /// [`Scale::resolve`] for the rule.
    pub fractional_fixed: Option<i32>,
    /// The bar's pixel width for this output, learned from its layer surface's
    /// `configure` (`0` until the first configure).
    pub width_px: u32,
    /// The compositor has mapped the dedicated surface (a `wl_surface.enter`
    /// arrived). A surface must be mapped before it can be the panel's
    /// reference (primary) output.
    pub entered: bool,
    /// A layer surface has been created for this output. Cleared when the
    /// output is removed (the surface is then destroyed).
    pub created: bool,
}

impl Default for Output {
    fn default() -> Self {
        Output {
            integer_scale: 1,
            fractional_fixed: None,
            width_px: 0,
            entered: false,
            created: false,
        }
    }
}

/// The scale + pixel width the live backend paints **one** surface at.
///
/// [`MultiOutput::paint_targets`] yields one of these per mapped, sized
/// output, in deterministic order. The backend rasterizes the panel's frame to
/// `width_px` at `scale` and commits it to that output's dedicated surface.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PaintTarget {
    /// The output (and its dedicated surface) to paint.
    pub id: OutputId,
    /// The bar's pixel width for this output (from its layer `configure`).
    pub width_px: u32,
    /// The scale this surface renders at ([`Scale::resolve`] of the output's
    /// integer reading and its per-surface fractional scale).
    pub scale: Scale,
}

/// The pure multi-output state machine (see the module docs).
///
/// Construct with [`MultiOutput::new`], passing the font's cell width (the
/// model converts each output's pixel width to cells to report the primary
/// width and detect resizes). The live backend mirrors each output with a
/// `Surface` of Wayland proxies; this model only carries the geometry and the
/// decisions a paint/resize pass needs.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct MultiOutput {
    /// The outputs, keyed by [`OutputId`]. A `BTreeMap` so iteration (and thus
    /// primary selection and the paint-target order) is **deterministic** for a
    /// given set of ids, independent of the compositor's advertisement order.
    outputs: BTreeMap<OutputId, Output>,
    /// The font's cell width in logical (scale-1) pixels, used to convert a
    /// configure width (px) to a bar width (cells).
    cell_w: u32,
    /// The last bar width (cells) reported to the panel through the
    /// [`Backend`](crate::backend::Backend) seam. A primary change is only
    /// re-reported when the cell width actually differs from this (a no-op
    /// never fires — the same dedupe rule task 14 used for scale changes).
    reported_primary: Option<u32>,
}

impl MultiOutput {
    /// A model tracking no outputs, using `cell_w` logical pixels per cell.
    pub fn new(cell_w: u32) -> Self {
        MultiOutput {
            outputs: BTreeMap::new(),
            cell_w,
            reported_primary: None,
        }
    }

    /// The font cell width this model was built with.
    pub fn cell_w(&self) -> u32 {
        self.cell_w
    }

    /// How many outputs are currently tracked (added, not yet removed).
    pub fn len(&self) -> usize {
        self.outputs.len()
    }

    /// True when no outputs are tracked.
    pub fn is_empty(&self) -> bool {
        self.outputs.is_empty()
    }

    /// Look up one output by id (its scale/width/presence), or `None`.
    pub fn get(&self, id: OutputId) -> Option<&Output> {
        self.outputs.get(&id)
    }

    /// The ids of every tracked output, in deterministic (ascending) order.
    pub fn ids(&self) -> Vec<OutputId> {
        self.outputs.keys().copied().collect()
    }

    /// A new `wl_output` global was advertised: track it.
    ///
    /// A fresh output starts at the protocol's sane defaults (integer scale 1,
    /// no fractional reading, no configure width, not yet mapped) and is
    /// marked for surface creation (`created` stays `false` until the backend
    /// builds its dedicated layer surface). Returns `false` if the id was
    /// already tracked (a re-advertisement is a no-op, not a second surface).
    pub fn add_output(&mut self, id: OutputId) -> bool {
        if self.outputs.contains_key(&id) {
            return false;
        }
        self.outputs.insert(id, Output::default());
        true
    }

    /// A `wl_output` global was removed (the monitor went away): stop tracking
    /// it. The caller destroys the dedicated surface (its `created` flag is
    /// the signal the backend already acted on). Returns `true` if the id was
    /// tracked (and is now gone), `false` if it was unknown (a no-op).
    pub fn remove_output(&mut self, id: OutputId) -> bool {
        self.outputs.remove(&id).is_some()
    }

    /// Record a `wl_output.scale` event for `id`.
    ///
    /// Returns `true` when the reading actually changed (the backend then
    /// re-applies that surface's size hint and exclusive zone). A `0` or
    /// negative reading is stored as given — [`Output::effective_scale`]
    /// (via [`Scale::resolve`]) is the single place such a reading is clamped
    /// to a real scale, so the raw event is preserved for diagnostics.
    pub fn set_scale(&mut self, id: OutputId, scale: i32) -> bool {
        match self.outputs.get_mut(&id) {
            Some(o) if o.integer_scale != scale => {
                o.integer_scale = scale;
                true
            }
            _ => false,
        }
    }

    /// Record a per-surface `wp_fractional_scale_v1.preferred_scale` event for
    /// `id`'s surface. A `0` value is stored as `Some(0)` — [`Scale::resolve`]
    /// treats `Some(0)` exactly as `None` ("use the integer scale"), but
    /// keeping the raw value distinguishes "the compositor explicitly cleared
    /// the fractional scale" from "no fractional-scale-v1 in this session".
    /// Returns `true` when the value changed.
    pub fn set_fractional(&mut self, id: OutputId, fixed: i32) -> bool {
        match self.outputs.get_mut(&id) {
            Some(o) if o.fractional_fixed != Some(fixed) => {
                o.fractional_fixed = Some(fixed);
                true
            }
            _ => false,
        }
    }

    /// Record the dedicated surface's `configure` width (physical pixels) for
    /// `id`. Marks the surface as mapped-and-sized: from this point the output
    /// is a paint target (if it has also received `enter`) and is eligible to
    /// be the primary. A `0` width (the layer-shell "compositor decides"
    /// sentinel we *request* as the width hint) still records a configure but
    /// yields zero cells; the backend treats `width_px == 0` as "not yet
    /// sized".
    pub fn set_configured_width(&mut self, id: OutputId, width_px: u32) {
        if let Some(o) = self.outputs.get_mut(&id) {
            o.width_px = width_px;
            o.created = true;
        }
    }

    /// Record that the dedicated surface for `id` has been (re)created.
    ///
    /// The live backend calls this right after building a `wl_surface` + layer
    /// surface, so [`surfaces_pending`](Self::surfaces_pending) stops
    /// requesting a surface for that output. Idempotent.
    pub fn mark_created(&mut self, id: OutputId) {
        if let Some(o) = self.outputs.get_mut(&id) {
            o.created = true;
        }
    }

    /// Record the dedicated surface's `wl_surface.enter` (`entered = true`) or
    /// `leave` (`entered = false`) for `id`. A surface must be mapped (entered)
    /// before it can anchor the panel's reference width. No-op for an unknown
    /// id.
    pub fn set_entered(&mut self, id: OutputId, entered: bool) {
        if let Some(o) = self.outputs.get_mut(&id) {
            o.entered = entered;
        }
    }

    /// The scale a surface for `id` renders at: [`Scale::resolve`] of that
    /// output's integer reading and its per-surface fractional scale.
    ///
    /// This is the crux of multi-output: the scale is *this output's*, not a
    /// guess from a global list (the task-14 `integer_scale` heuristic),
    /// because each surface is dedicated to — and entered on — one output.
    /// `Scale::ONE` for an unknown id.
    pub fn effective_scale(&self, id: OutputId) -> Scale {
        match self.outputs.get(&id) {
            Some(o) => Scale::resolve(o.integer_scale, o.fractional_fixed),
            None => Scale::ONE,
        }
    }

    /// The bar width in **cells** for `id`'s configure width at its scale
    /// (`width_px / scaled cell width`, sub-cell remainder dropped). `0` for an
    /// unknown id or a not-yet-sized output.
    pub fn width_cells(&self, id: OutputId) -> u32 {
        match self.outputs.get(&id) {
            Some(o) => self.effective_scale(id).cells_for_px(o.width_px, self.cell_w),
            None => 0,
        }
    }

    /// The output the panel lays out against (the one the single-bar
    /// [`Backend`](crate::backend::Backend) seam reports).
    ///
    /// The candidates are the mapped, sized outputs (`entered && width_px > 0`)
    /// in ascending-id order. Selection rule:
    ///
    /// - **no candidates** → `None` (nothing to lay out against yet);
    /// - **exactly one** → it (the common single-monitor case, whether or not
    ///   its `enter` has landed yet);
    /// - **several** → the first one that is mapped (`entered`); if none has
    ///   mapped yet (e.g. before any `enter` is delivered), the first candidate
    ///   so the width is stable and the loop can proceed.
    pub fn primary(&self) -> Option<OutputId> {
        let candidates: Vec<OutputId> = self
            .outputs
            .iter()
            .filter(|(_, o)| o.width_px > 0)
            .map(|(id, _)| *id)
            .collect();
        match candidates.len() {
            0 => None,
            1 => Some(candidates[0]),
            _ => candidates
                .iter()
                .copied()
                .find(|id| self.outputs[id].entered)
                .or(Some(candidates[0])),
        }
    }

    /// The bar width (cells) to report through the
    /// [`Backend`](crate::backend::Backend) seam: the [`primary`](Self::primary)
    /// output's cell width, or `0` when there is no primary yet.
    pub fn primary_width_cells(&self) -> u32 {
        self.primary().map(|id| self.width_cells(id)).unwrap_or(0)
    }

    /// Whether the primary's cell width differs from the last one reported.
    ///
    /// This is the single decision point for a
    /// [`BackendEvent::Resize`](crate::backend::BackendEvent::Resize): a change
    /// in *any* output's scale or width only reaches the panel when it moves the
    /// **primary's** cell width (the panel lays out against one width). Call
    /// [`report_primary`](Self::report_primary) to acknowledge the current
    /// width so the same width does not re-fire. Returns `false` when there is
    /// no primary (nothing to report).
    pub fn primary_changed(&self) -> bool {
        match self.primary() {
            Some(id) => self.reported_primary != Some(self.width_cells(id)),
            None => false,
        }
    }

    /// Acknowledge the current primary width (mark it reported). Call this in
    /// [`Backend::open`] (so `open` does not immediately re-`Resize` to the
    /// width it just opened at) and after each `Resize` the panel consumes.
    /// No-op when there is no primary.
    pub fn report_primary(&mut self) {
        if let Some(id) = self.primary() {
            self.reported_primary = Some(self.width_cells(id));
        }
    }

    /// The ids that need a dedicated layer surface created: tracked outputs
    /// (`len() > 0`) that have not been created yet, in ascending order. The
    /// live backend builds a `wl_surface` + layer surface for each and calls
    /// [`mark_created`](Self::mark_created).
    pub fn surfaces_pending(&self) -> Vec<OutputId> {
        self.outputs
            .iter()
            .filter(|(_, o)| !o.created)
            .map(|(id, _)| *id)
            .collect()
    }

    /// The surfaces to paint this pass: every mapped, sized output
    /// (`entered && width_px > 0`), in ascending-id order, each with the pixel
    /// width and scale to rasterize at. A not-yet-mapped or not-yet-sized
    /// output is excluded (its surface exists but has nothing to show yet).
    pub fn paint_targets(&self) -> Vec<PaintTarget> {
        self.outputs
            .iter()
            .filter(|(_, o)| o.entered && o.width_px > 0)
            .map(|(id, o)| PaintTarget {
                id: *id,
                width_px: o.width_px,
                scale: Scale::resolve(o.integer_scale, o.fractional_fixed),
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The default font's cell width (`CellFont::new().cell_w`); the tests
    /// build models around it so the px→cell arithmetic matches the live font.
    const CW: u32 = 6;

    /// A two-output world: `10` (a 120-px, 1:1 output) and `20` (a 120-px, 2:1
    /// output). Both are sized; neither mapped until a test says so.
    fn two_outputs_sized() -> MultiOutput {
        let mut m = MultiOutput::new(CW);
        m.add_output(10);
        m.add_output(20);
        m.set_scale(20, 2);
        m.set_configured_width(10, 120);
        m.set_configured_width(20, 120);
        m
    }

    // -- basic bookkeeping -------------------------------------------------

    #[test]
    fn new_model_is_empty_and_carries_the_cell_width() {
        let m = MultiOutput::new(CW);
        assert!(m.is_empty());
        assert_eq!(m.len(), 0);
        assert_eq!(m.ids(), Vec::<OutputId>::new());
        assert_eq!(m.cell_w(), CW);
        // Nothing to report or paint until outputs exist.
        assert_eq!(m.primary(), None);
        assert_eq!(m.primary_width_cells(), 0);
        assert!(!m.primary_changed());
        assert!(m.paint_targets().is_empty());
        assert!(m.surfaces_pending().is_empty());
    }

    #[test]
    fn add_output_tracks_and_is_idempotent() {
        let mut m = MultiOutput::new(CW);
        assert!(m.add_output(10)); // fresh
        assert_eq!(m.len(), 1);
        assert!(!m.add_output(10)); // re-advertise → no-op, not a second surface
        assert_eq!(m.len(), 1);
        assert!(m.add_output(20));
        assert_eq!(m.len(), 2);
        assert_eq!(m.ids(), vec![10, 20]); // deterministic ascending order
    }

    #[test]
    fn a_fresh_output_starts_at_protocol_defaults() {
        let mut m = MultiOutput::new(CW);
        m.add_output(7);
        let o = m.get(7).unwrap();
        assert_eq!(o.integer_scale, 1);
        assert_eq!(o.fractional_fixed, None);
        assert_eq!(o.width_px, 0);
        assert!(!o.entered);
        assert!(!o.created);
        // Not created yet → a surface is pending for it.
        assert_eq!(m.surfaces_pending(), vec![7]);
    }

    #[test]
    fn remove_output_drops_it_and_reports_the_change() {
        let mut m = two_outputs_sized();
        assert!(m.remove_output(10));
        assert_eq!(m.ids(), vec![20]);
        assert!(!m.remove_output(10)); // already gone → no-op
        assert!(!m.remove_output(999)); // unknown → no-op
    }

    #[test]
    fn mark_created_clears_the_surface_pending_set() {
        let mut m = MultiOutput::new(CW);
        m.add_output(10);
        m.add_output(20);
        assert_eq!(m.surfaces_pending(), vec![10, 20]);
        m.mark_created(10);
        assert_eq!(m.surfaces_pending(), vec![20]);
        m.mark_created(20);
        assert!(m.surfaces_pending().is_empty());
        // Idempotent: marking an already-created surface changes nothing.
        m.mark_created(10);
        assert!(m.surfaces_pending().is_empty());
        // `configure` also marks created (a configure implies the surface is up).
        let mut m2 = MultiOutput::new(CW);
        m2.add_output(30);
        m2.set_configured_width(30, 60);
        assert!(m2.get(30).unwrap().created);
        assert!(m2.surfaces_pending().is_empty());
    }

    // -- per-output scale & width ------------------------------------------

    #[test]
    fn effective_scale_is_per_output_not_a_global_guess() {
        let m = two_outputs_sized();
        // Output 10 is 1:1, output 20 is 2:1 — each reads ITS OWN output.
        assert_eq!(m.effective_scale(10), Scale::ONE);
        assert_eq!(m.effective_scale(20), Scale::resolve(2, None));
        // An unknown output is 1:1, never a panic.
        assert_eq!(m.effective_scale(999), Scale::ONE);
    }

    #[test]
    fn per_surface_fractional_scale_replaces_that_outputs_integer() {
        let mut m = two_outputs_sized();
        // A fractional reading on output 20 replaces its integer 2 with 1.5.
        m.set_fractional(20, 384); // 1.5
        assert_eq!(m.effective_scale(20), Scale::resolve(2, Some(384)));
        // Output 10 is untouched.
        assert_eq!(m.effective_scale(10), Scale::ONE);
        // A 0 fractional value means "use the integer scale" (resolve rule).
        m.set_fractional(20, 0);
        assert_eq!(m.effective_scale(20), Scale::resolve(2, None));
    }

    #[test]
    fn width_cells_divides_the_configured_width_by_the_scaled_cell() {
        let m = two_outputs_sized();
        // 120px @ 1:1 (6px/cell) → 20 cells; 120px @ 2:1 (12px/cell) → 10 cells.
        assert_eq!(m.width_cells(10), 20);
        assert_eq!(m.width_cells(20), 10);
        // Not yet sized → 0 cells; unknown id → 0 cells.
        let mut m2 = MultiOutput::new(CW);
        m2.add_output(40);
        assert_eq!(m2.width_cells(40), 0);
        assert_eq!(m2.width_cells(555), 0);
    }

    #[test]
    fn set_scale_reports_only_a_real_change() {
        let mut m = two_outputs_sized();
        assert!(!m.set_scale(20, 2)); // same reading → no change
        assert!(m.set_scale(20, 3)); // changed
        assert_eq!(m.get(20).unwrap().integer_scale, 3);
        assert!(!m.set_scale(20, 3)); // same again → no change
        // An unknown id is a no-op, not a change.
        assert!(!m.set_scale(999, 5));
    }

    #[test]
    fn set_fractional_reports_only_a_real_change() {
        let mut m = two_outputs_sized();
        assert!(m.set_fractional(10, 512)); // None → Some(512)
        assert!(!m.set_fractional(10, 512)); // unchanged
        assert!(m.set_fractional(10, 0)); // Some(512) → Some(0) is a change
        assert_eq!(m.get(10).unwrap().fractional_fixed, Some(0));
        assert!(!m.set_fractional(999, 1)); // unknown id
    }

    // -- primary selection -------------------------------------------------

    #[test]
    fn primary_is_none_until_an_output_is_sized() {
        let mut m = MultiOutput::new(CW);
        m.add_output(10);
        assert_eq!(m.primary(), None);
        // Sizing it (a configure) makes it the (only) primary.
        m.set_configured_width(10, 120);
        assert_eq!(m.primary(), Some(10));
        assert_eq!(m.primary_width_cells(), 20);
    }

    #[test]
    fn a_single_sized_output_is_primary_even_before_enter() {
        let mut m = MultiOutput::new(CW);
        m.add_output(10);
        m.set_configured_width(10, 60); // 10 cells
        assert!(!m.get(10).unwrap().entered);
        // The one-output case must not stall on an `enter` that hasn't landed.
        assert_eq!(m.primary(), Some(10));
        assert_eq!(m.primary_width_cells(), 10);
    }

    #[test]
    fn among_several_outputs_the_first_mapped_one_is_primary() {
        let mut m = two_outputs_sized(); // 10 (sized) and 20 (sized), neither mapped
        // Neither mapped yet → fall back to the first candidate (id 10).
        assert_eq!(m.primary(), Some(10));
        // Map output 20 only: it is now the preferred (mapped) primary.
        m.set_entered(20, true);
        assert_eq!(m.primary(), Some(20));
        // Map output 10 too: the first mapped *in id order* (10) wins.
        m.set_entered(10, true);
        assert_eq!(m.primary(), Some(10));
    }

    #[test]
    fn a_very_small_output_is_excluded_from_being_primary() {
        let mut m = MultiOutput::new(CW);
        m.add_output(10);
        m.add_output(20);
        m.set_configured_width(10, 120);
        m.set_configured_width(20, 120);
        m.set_entered(10, true);
        m.set_entered(20, true);
        assert_eq!(m.primary(), Some(10));
        // Output 10 drops to 0 width (unconfigured) → only 20 remains the
        // candidate, so 20 becomes primary.
        m.set_configured_width(10, 0);
        assert_eq!(m.primary(), Some(20));
    }

    #[test]
    fn set_entered_tracks_mapped_and_unmapped() {
        let mut m = two_outputs_sized();
        assert!(!m.get(10).unwrap().entered);
        m.set_entered(10, true);
        assert!(m.get(10).unwrap().entered);
        m.set_entered(10, false);
        assert!(!m.get(10).unwrap().entered);
        // Unknown id is a no-op, not a panic.
        m.set_entered(999, true);
    }

    // -- resize detection (the single-bar seam) ----------------------------

    #[test]
    fn primary_changed_is_false_before_anything_is_reported_then_dedupes() {
        let mut m = two_outputs_sized();
        m.set_entered(10, true);
        // Before the first `open`/`report_primary`, there is a (unreported)
        // primary width → a change is pending.
        assert!(m.primary_changed());
        assert_eq!(m.primary_width_cells(), 20);
        // Acknowledge it: the same width must not fire again.
        m.report_primary();
        assert!(!m.primary_changed());
        // A no-op scale change on a non-primary output never fires.
        m.set_scale(20, 2); // (already 2 → no change)
        assert!(!m.primary_changed());
    }

    #[test]
    fn a_primary_scale_change_moves_the_cell_width_and_refires() {
        let mut m = two_outputs_sized();
        m.set_entered(10, true);
        m.report_primary(); // 20 cells @ 1:1
        assert!(!m.primary_changed());
        // Scaling the PRIMARY output 10 to 2:1 halves its cell width (20→10).
        m.set_scale(10, 2);
        assert!(m.primary_changed(), "the primary's cell width changed");
        assert_eq!(m.primary_width_cells(), 10);
        m.report_primary();
        assert!(!m.primary_changed());
    }

    #[test]
    fn a_non_primary_change_never_reaches_the_panel() {
        let mut m = two_outputs_sized();
        m.set_entered(10, true);
        m.set_entered(20, true);
        m.report_primary(); // primary is 10 (first mapped) → 20 cells
        // Resizing / rescaling the SECONDARY output 20 does not move the
        // primary's width, so the single-bar seam stays quiet.
        m.set_scale(20, 3);
        m.set_configured_width(20, 300);
        assert!(!m.primary_changed());
        assert_eq!(m.primary_width_cells(), 20);
    }

    #[test]
    fn primary_change_is_reported_only_when_the_cell_width_actually_moves() {
        // A width change that does not change the cell count (e.g. 120→125px at
        // 6px/cell, both → 20 cells) must NOT re-fire a resize.
        let mut m = MultiOutput::new(CW);
        m.add_output(10);
        m.set_configured_width(10, 120);
        m.set_entered(10, true);
        m.report_primary(); // 20 cells
        m.set_configured_width(10, 125); // still 20 cells (5px clipped)
        assert!(!m.primary_changed());
        // …but a change that crosses a cell boundary does.
        m.set_configured_width(10, 126); // 21 cells
        assert!(m.primary_changed());
    }

    #[test]
    fn report_primary_is_a_noop_without_a_primary() {
        let m = MultiOutput::new(CW);
        let mut m2 = m.clone();
        m2.report_primary();
        assert_eq!(m2.primary_width_cells(), 0);
        assert!(!m2.primary_changed());
    }

    // -- paint targets -----------------------------------------------------

    #[test]
    fn paint_targets_are_the_mapped_sized_outputs_in_order() {
        let mut m = two_outputs_sized(); // both sized, neither mapped
        assert!(m.paint_targets().is_empty());
        m.set_entered(10, true);
        let mut targets = m.paint_targets();
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0], PaintTarget {
            id: 10,
            width_px: 120,
            scale: Scale::ONE,
        });
        m.set_entered(20, true);
        targets = m.paint_targets();
        assert_eq!(targets.len(), 2); // deterministic ascending-id order
        assert_eq!(targets[0].id, 10);
        assert_eq!(targets[0].scale, Scale::ONE);
        assert_eq!(targets[1].id, 20);
        assert_eq!(targets[1].scale, Scale::resolve(2, None));
        assert_eq!(targets[1].width_px, 120);
    }

    #[test]
    fn paint_targets_skip_unmapped_and_unsized_surfaces() {
        let mut m = MultiOutput::new(CW);
        m.add_output(10);
        m.add_output(20);
        m.add_output(30);
        m.set_configured_width(10, 120);
        m.set_configured_width(20, 120);
        // 30 has no configure (unsized).
        m.set_entered(10, true); // mapped + sized → painted
        m.set_entered(20, true); // mapped + sized → painted
        m.set_entered(30, true); // mapped but unsized → NOT painted
        let targets = m.paint_targets();
        let ids: Vec<OutputId> = targets.iter().map(|t| t.id).collect();
        assert_eq!(ids, vec![10, 20]);
    }

    #[test]
    fn paint_target_scale_reflects_the_current_readings() {
        let mut m = two_outputs_sized();
        m.set_entered(20, true);
        m.set_fractional(20, 384); // 1.5 replaces integer 2
        let targets = m.paint_targets();
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].scale, Scale::resolve(2, Some(384)));
    }

    // -- whole-model lifecycle ---------------------------------------------

    #[test]
    fn a_monitor_plugged_in_adds_a_surface_and_one_unplugged_removes_it() {
        let mut m = MultiOutput::new(CW);
        // Boot with one monitor.
        m.add_output(10);
        m.set_configured_width(10, 120);
        m.set_entered(10, true);
        m.report_primary();
        assert_eq!(m.primary(), Some(10));
        assert_eq!(m.surfaces_pending(), Vec::<OutputId>::new());

        // A second monitor is plugged in: track it; the backend must build a
        // surface for it.
        m.add_output(20);
        assert_eq!(m.surfaces_pending(), vec![20]);
        m.mark_created(20);
        m.set_configured_width(20, 120);
        m.set_entered(20, true);
        // Both monitors are now painted.
        assert_eq!(m.paint_targets().len(), 2);
        // The primary is still the (first-mapped, lowest-id) monitor 10, and
        // the panel's width is unchanged → no resize fires.
        assert_eq!(m.primary(), Some(10));
        assert!(!m.primary_changed());

        // That second monitor is unplugged: its surface is destroyed and the
        // paint set shrinks back to one.
        assert!(m.remove_output(20));
        assert_eq!(m.paint_targets().len(), 1);
        assert_eq!(m.paint_targets()[0].id, 10);
        assert!(!m.primary_changed());
    }

    #[test]
    fn a_removed_primary_shrinks_to_the_remaining_output_and_refires() {
        let mut m = MultiOutput::new(CW);
        m.add_output(10);
        m.add_output(20);
        m.set_scale(20, 2);
        m.set_configured_width(10, 120); // 20 cells @ 1:1 (120 / 6)
        m.set_configured_width(20, 120); // 10 cells @ 2:1 (120 / 12)
        m.set_entered(10, true);
        m.set_entered(20, true);
        m.report_primary(); // primary 10 → 20 cells
        assert!(!m.primary_changed());
        // Unplug monitor 10: the remaining monitor 20 becomes primary at 10
        // cells — a real width change the panel must learn about.
        m.remove_output(10);
        assert_eq!(m.primary(), Some(20));
        assert!(m.primary_changed());
        assert_eq!(m.primary_width_cells(), 10);
    }

    #[test]
    fn the_model_is_cloneable_and_eq() {
        let mut a = two_outputs_sized();
        a.set_entered(10, true);
        a.report_primary();
        let mut b = a.clone();
        assert_eq!(a, b);
        // Mutating the clone diverges the two.
        b.set_scale(20, 3);
        assert_ne!(a, b);
    }
}
