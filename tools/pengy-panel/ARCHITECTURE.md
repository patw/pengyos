# Architecture

pengy-panel is a pure-Rust Wayland `layer-shell` status bar. The whole design
revolves around one idea: **the panel's brain is headless-testable, and only
its hands touch the compositor.**

## The two crates

```text
┌───────────────────────┐        ┌───────────────────────────────┐
│  pengy-panel (bin)    │        │  widgets (lib)                │
│                       │        │                               │
│  • CLI arg parsing    │──────▶ │  • AppInfo (version metadata) │
│  • config loading     │  uses  │  • Widget trait               │
│  • layer-shell draw   │        │  • data-source abstraction    │
│  • event loop         │        │  • LayoutEngine               │
│                       │        │  • Clock/Battery/… widgets    │
│  (Wayland, rendering) │        │  (pure data, no rendering)    │
└───────────────────────┘        └───────────────────────────────┘
```

**Dependency rule:** the binary depends on the core; the core never depends
on the binary, on Wayland, on a rendering crate, or on *any* external
dependency at all (`widgets` has an empty `[dependencies]` table). This is
the one-directional arrow, and it is load-bearing: it is what keeps `cargo
test` green on a machine with no compositor, no network, and no hardware.

## The headless-test boundary

Anything that needs the outside world is injected behind a trait and swapped
for a fixture in tests:

| Real input            | Boundary                 | Test double                    |
|-----------------------|--------------------------|--------------------------------|
| Wall clock / TZ       | `DataSource` trait (T2)  | fixed instant + fixed `TZ`     |
| `/sys/class/power_supply` | `DataSource` trait (T2) | committed file fixtures       |
| Wayland compositor    | backend trait in the bin crate (T6) | `PENGYOS_IT=1`-gated integration only |

The `DataSource` (T2) is deliberately scoped to the **non-compositor inputs a
panel *polls***: the clock, the timezone, and the power-supply sysfs tree.
Compositor-derived **data** (e.g. workspace state) is a different kind of input:
it does not flow through `DataSource`. The T6 backend owns the compositor and
*drives* such widgets directly — a **state-fed** widget (like the upcoming
Workspaces widget) exposes a way for the backend to set its state, and its
`Widget::update` renders that state, ignoring the source. So a widget is either
*poll-based* (reads its `DataSource`) or *state-fed* (driven by the backend);
the `Widget` trait supports both, and both are headless-testable.

Consequences:

- Every widget is a pure function of `(state → output)` plus an injected
  data source. Tests construct the state, call the widget, and assert.
- The `LayoutEngine` is pure geometry over widget descriptors; tests assert
  exact pixel boxes for sizing, alignment, and overflow cases.
- A `--headless --print-layout` mode (T7) renders the computed layout as
  text, so CI can golden-file the *visible* layout without a display.
- Real-compositor runs are opt-in: tests gated behind `PENGYOS_IT=1` are
  skipped by default and never run in CI.

## Memory & performance

A status bar lives on screen forever. Targets (measured, not vibes):

- **Idle RSS ≤ 15 MiB** (release build) — the panel must not feel like a
  browser tab that leaked. **Measured: ≈ 2.5 MiB** (2 512 KiB) on this host,
  far under the target — see the measurement below (T12). It is regression-
  guarded by the `--headless --measure-rss` CI gate (exit 1 over the limit),
  and the target is pinned by the `perf_contract` doc-guard.
- **Layout recompute < 1 ms** on 20 widgets — re-layout runs on every
  widget state change, so it must be effectively free. This one *is* measured
  by the criterion bench (below); the 20-widget point is the number this target
  names.
- **Release profile:** `opt-level = "z"`, LTO, stripped, `panic = "abort"`
  (a panel that panics should die quietly and be respawned by the compositor,
  not unwind the whole process). See `[profile.release]` in the workspace
  `Cargo.toml`.

### Measuring layout cost (T8)

`cargo bench -p widgets --bench layout` runs a **criterion** harness over
`widgets::bench::workload` — the same deterministic generator the unit tests
exercise, so the bench and the tests measure identical input. It reports the
cost of one `LayoutEngine::layout` at **4 / 20 / 40 / 80 widgets** on a
120-cell bar, plus an **overflow-heavy** case (40 wide widgets on a 20-cell
bar, where the outside-in drop loop iterates many times). Run in release —
criterion's default bench profile — so the figures reflect steady-state cost.
Results land in `target/criterion/` for run-over-run comparison. `criterion`
is a **dev**-dependency of `widgets`: it is linked only into `cargo bench` /
`cargo test`, never into the published rlib, so the crate's empty
`[dependencies]` table (the headless-test boundary) is untouched.

### Measuring idle RSS (T12)

`pengy-panel --headless --measure-rss [--width <CELLS>] [--max-mib <MIB>]`
measures the **release** binary's steady-state resident set size with **no
compositor**: it builds the widget list, drives the real `Panel` through a
short steady-state loop of poll → layout → paint, **rasterizes the last frame
to an ARGB8888 buffer (the same buffer the live backend holds at idle)** and
keeps it alive while reading `VmRSS` from `/proc/self/status`.

- The held ARGB buffer is exactly what the live Wayland backend keeps idle, so
  the number is a **faithful lower bound** on the live panel's idle RSS — it
  omits only the Wayland socket/client overhead (tens of KiB, small).
- `--width` (cells, default 120) sets the representative bar width;
  `--max-mib` (default **15**, the target) is the gate limit. The mode prints
  `idle-rss: <KIB> KiB (<MIB> MiB) [limit <MIB> MiB] OK|EXCEEDED` to stdout and
  exits **1** when over the limit (else **0**), making the target a real,
  CI-runnable regression guard: run it against the *release* build and the CI
  fails the moment the footprint crosses the line.
- **Measured (this host, release, 256 warm-up frames):** ≈ 2.5 MiB for the
  default 120-cell bar, ≈ 2.7 MiB for `examples/panel.toml` (5 widgets),
  ≈ 2.8 MiB at a 4K-wide (1024-cell) bar. All comfortably under 15 MiB.

### The live-compositor residual (T12, `--measure-rss-live`)

The headless number omits the Wayland socket/client overhead — the small
residual a *real* running panel carries. `pengy-panel --measure-rss-live`
measures it: it runs the **real** `WaylandBackend` against a live compositor
for a bounded steady-state window (`live_rss::run_steady`, a time-bounded
cousin of `Panel::run`) and samples the process's `VmRSS` while connected and
painting, reporting the **minimum** sample over the window (idle is the floor
the kernel settles to; warm-up spikes only raise a sample). Output:
`live-idle-rss: <KIB> KiB (<MIB> MiB) [limit <MIB> MiB] OK|EXCEEDED`; exit 0/1
against `--max-mib` (default 15), **69** when it cannot attach to a
layer-shell compositor, **65** on a bad config. The pure parts (socket-path
resolution, the min reduction, the bounded loop) are headless-tested; the live
attach is `PENGYOS_IT=1`-gated. **Recording the number itself is blocked on a
wlroots compositor** (sway/labwc/wayfire) that sizes the layer surface — a
gnome-shell/Mutter session accepts the surface but never sends a sizing
`configure`, and installing a wlroots compositor needs root — see `NEEDS.md`.

## Task map

How the panel was built, in order:

- **T1** scaffold (this split, docs, `github_ready` test) — done
- **T2** `Widget` trait + `DataSource` abstraction — done
- **T3** `LayoutEngine` — done (full rule set in `widgets/src/layout.rs`:
  sections, intra-section gaps, center-in-free-space, outside-in overflow)
- **T4** built-in widgets — done (poll-based `Clock` + `Battery`; state-fed
  `Workspaces`; `Text`/`Template` labels; plus the production `SysfsSource`
  `DataSource` — real clock/`/sys`/`date +%z` reads with a fixture-tested pure
  core. See `widgets/src/{clock,battery,workspaces,text,source_impl,civil}.rs`)
- **T5** config parsing (`panel.toml`) — done (model + validation in the
  zero-dep core `widgets::config`; serde/toml wire layer in the binary;
  `Section::from_str`; strict `clock::validate_format`; `examples/panel.toml`;
  `-c` now loads + validates, exit 65 on bad config)
- **T6** layer-shell backend behind a trait — done (the `Backend` trait +
  `Frame` paint unit + `HeadlessBackend` + the `Panel` driver live in the
  binary crate and run fully headless; `Widget` gained `as_any`/`as_any_mut`
  and the driver `widget_mut::<T>(id)` is the state-fed channel. The Wayland
  implementation of the trait is T11 below, `PENGYOS_IT`-gated)
- **T7** `--headless --print-layout` mode
- **T8** memory/perf targets + bench harness — done (targets documented; the
  `criterion` layout bench lives in `widgets/benches/layout.rs` over the
  `widgets::bench` workload; the `perf_contract` doc-guard keeps the targets and
  the bench wiring honest. Idle RSS is now **measured** headlessly by T12's
  `--measure-rss` — ≈ 2.5 MiB on this host, well under the 15 MiB target)
- **T9** full green suite
- **T10** build the widget list from `PanelConfig` (config →
  `Vec<Box<dyn Widget>>`, headless-testable)
- **T11** real Wayland `layer-shell` backend implementing `Backend`
  (wayland-client + layer-shell protocol + shm buffer + font/cell mapping),
  tests `PENGYOS_IT`-gated — done (`pengy-panel/src/wayland/`: the `CellFont`
  + pure `rasterize_frame`/`rasterize_frame_at` are golden-tested headlessly;
  the `WaylandBackend` glue (wlr layer-shell via `wayland-protocols-wlr`,
  shm `wl_buffer`, `Connection`/`EventQueue`/`Dispatch` loop, `libc::poll`
  timed wait) compiles against the real bindings and is wired into the
  binary's `Run` path; live open/paint is `PENGYOS_IT`-gated, and a no-
  compositor `open` is unit-tested to fail gracefully)
- **T12** measure the release panel's idle RSS — done (`pengy-panel/src/measure_rss.rs`:
  the `--headless --measure-rss` mode runs the real pipeline to steady state
  holding the ARGB frame buffer, reads `VmRSS`, and gates against the 15 MiB
  target (exit 1 over the limit). Measured ≈ 2.5 MiB on this host, well under
  the target; the residual live-compositor socket overhead is a follow-up item
  (needs a real wlroots compositor under `PENGYOS_IT=1`).
- **T13** reusable shm pool in the Wayland backend — done. The backend now
  keeps **one** `wl_shm` pool for the whole session instead of allocating a
  temp file + pool on every `paint`. `pengy-panel/src/wayland/stage.rs`
  (`PixelStage`) is the pure, headless-tested core: one reusable byte region
  re-staged in place each frame, grown only on a resize that needs more room,
  with `reallocs`/`frames` counters (a stable-size run allocates exactly once).
  `wayland::ShmPool` (in `mod.rs`) backs it with a **`memfd`**
  (`libc::memfd_create`) — not a temp file, because unlinking a file on a
  `tmpfs` frees its data immediately and would corrupt the region the
  compositor `mmap`s — created lazily on the first paint and recreated only on
  a grow-resize. Each `paint` rewrites the region in place and cuts a fresh
  (cheap, one-shot) `wl_buffer` handle from the same pool. `ShmPool::backing`
  is headless-tested; the live open/paint is `PENGYOS_IT`-gated.
- **T14** output scale in the Wayland backend — done. `wayland::scale::Scale`
  (pure, headless-tested) resolves the integer `wl_output.scale` and the
  compositor's `wp_fractional_scale_v1.preferred_scale` (24.8 fixed point,
  which per the protocol *replaces* the integer reading) into one positive
  rational scale, with saturating fixed-point pixel math (no floats). The
  rasterizer's single drawing primitive is now
  `rasterize::rasterize_frame_scaled` (the 1:1 paths delegate to it): every
  5×7 font pixel blits to the integer rectangle its logical position maps to,
  so 2:1 is exact block scaling and 1.5:1 stretches proportionally — crisp,
  no blur. The live backend binds `wl_output` (≥ v2) and the optional
  `wp_fractional_scale_manager_v1`, requests the per-surface fractional scale
  before the first commit, scales the height hint + exclusive zone, re-reports
  a `Resize` (cells) when a scale change changes the cell width, and paints
  through the scaled rasterizer. Note: the wlr layer-shell protocol has no
  `output_enter`/`output_leave`, so with several *differently-scaled* outputs
  the integer reading falls back to 1:1 (fractional-scale-v1 still wins); a
  per-output bar is T15's territory. The live path is `PENGYOS_IT=1`-gated.
- **T15** multiple outputs — done. The backend paints **one bar per monitor**:
  one `wl_surface` + layer surface per `wl_output`, each with its own size
  hint, per-surface fractional-scale object, and its own reusable `ShmPool`.
  The untestable state machine is split into a pure, headless-tested model,
  `wayland::outputs::MultiOutput` (per-output scale/width, `entered`/`created`,
  deterministic **primary** selection, resize detection, paint targets, and a
  plug/unplug lifecycle) — the same pure/gated split as T13/T14. The
  layer-shell limitation is resolved because a layer surface *is* a
  `wl_surface` (which emits `enter`/`leave`): each dedicated surface knows its
  own output, so its scale is unambiguous and T14's `integer_scale` guess is
  obsolete (deleted). The single-bar `Backend` seam is driven by the **primary**
  output: `open` returns its cell width, `Resize` fires only when the
  primary's cell width moves, and `paint` replicates the panel's frame to each
  mapped, sized surface at its own width/scale (exact for equal-size monitors;
  independent per-output layout for mixed sizes is the next unchecked item).
  The live open/paint is `PENGYOS_IT=1`-gated.