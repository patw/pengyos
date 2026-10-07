# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added

- **`--measure-rss-live` — the live-compositor idle RSS** (the T12 residual):
  the live complement of `--headless --measure-rss`. It runs the panel's
  **real** Wayland `layer-shell` backend against a live compositor for a
  bounded steady-state window and samples the process's resident set size
  *while connected and painting* — the headless number **plus** the measured
  Wayland socket/client overhead.
  - New `pengy-panel/src/live_rss.rs`: the pure, headless-tested helpers
    (`wayland_socket_path` resolves `WAYLAND_DISPLAY` + `XDG_RUNTIME_DIR`;
    `steady_min_kib` reduces the sample window to its minimum — idle is the
    floor; `run_steady` is a time-bounded cousin of `Panel::run`, generic over
    the `Backend` and driven headlessly against `HeadlessBackend`) and the
    `measure_live_idle_rss` driver that opens the real `WaylandBackend` and
    samples `VmRSS` at a fixed cadence over a 2 s window.
  - New `--measure-rss-live` flag + CLI rules (a one-shot mode mutually
    exclusive with the others; a *live* mode, so rejected with `--headless` and
    with `--width`/`--epoch`; `--max-mib` gate). Output:
    `live-idle-rss: <KIB> KiB (<MIB> MiB) [limit <MIB> MiB] OK|EXCEEDED`; exit
    0/1 by limit, **69** when it cannot attach to a layer-shell compositor,
    **65** on a bad config.
  - `Panel::set_width(&mut self, w)` so a live backend's `open`/`Resize` can
    drive the layout width in place.
  - Headless unit tests for all the pure parts + the bounded loop; a new
    `pengy-panel/tests/live_rss.rs` with un-gated flag/config e2e tests
    (deterministic, no compositor) and `PENGYOS_IT=1`-gated live tests.
  - The *recorded* number itself is blocked on a wlroots compositor that sizes
    the layer surface (see `NEEDS.md`); a gnome-shell/Mutter session accepts
    the surface but never sends a sizing `configure`, so the mode exits 69
    there. The checklist item stays open until the number is captured.

- Multiple outputs in the Wayland backend (task 15): the panel now paints
  **one bar per monitor** — one `wl_surface` + `layer-shell` surface per
  advertised `wl_output`, each with its own size hint/exclusive zone, its own
  per-surface fractional-scale object, and its own reusable shm pool. This
  also *resolves* the task-14 scale guess: with one surface per output, a
  surface knows its own output (via `wl_surface.enter`/`leave`, which
  layer-shell itself lacks), so its scale is that output's reading.
  - **`wayland::outputs::MultiOutput`** (pure, headless-tested, 29 unit tests):
    the per-output state machine — per-output integer scale + per-surface
    fractional reading, `configure` width, `entered`/`created` flags,
    deterministic primary selection, resize detection (a change reaches the
    panel only when the *primary's* cell width actually moves), paint-target
    enumeration, and a full monitor plug/unplug lifecycle. The same
    pure-half / gated-live-half split as `Scale` (task 14) and `PixelStage`
    (task 13).
  - **Live backend** (`wayland/mod.rs`, live half `PENGYOS_IT=1`-gated): one
    surface per output (`ensure_surfaces`, idempotent across the compositor's
    unordered global advertisements), a `wl_surface` dispatch that tracks
    mapping, a `global_remove` handler that tears a monitor's bar down, and a
    `paint` that rasterizes the panel's (primary-width) frame for each mapped,
    sized surface at its own width/scale. The panel's single-bar `Backend`
    seam is driven by the *primary* output; `open` returns its cell width.
  - Documented limitation + follow-up: the panel replicates the primary's
    frame to every surface (exact for equal-size monitors); independent
    per-output layout (a per-output `Frame`, changing the `Panel`/`Backend`
    seam) is the next unchecked checklist item.

- Output scale in the Wayland backend (task 14): the bar is now correctly
  sized and crisp at non-100% scaling (150%, 200%, …), via the integer
  `wl_output.scale` plus the compositor's `wp_fractional_scale_v1`
  `preferred_scale` (which, per the fractional-scale-v1 protocol, *replaces*
  the integer reading when nonzero).
  - **`wayland::scale::Scale`** (pure, headless-tested, 10 unit tests): a
    positive rational scale with the protocol resolve rule, 24.8 fixed-point
    parsing, saturating fixed-point `scaled_px` (round-to-nearest, `u64`
    math — no floats) and `cells_for_px` for the bar width in cells.
  - **`rasterize_frame_scaled`** (5 new tests): the single drawing primitive;
    every 5×7 font pixel (and the cell's margins) blits to the integer
    rectangle its logical position maps to — 2:1 is exact block scaling (a
    test pins the exact 2×2-block equivalence with the 1:1 pixel), 1.5:1
    stretches proportionally with the scaled margins staying blank. The 1:1
    `rasterize_frame`/`rasterize_frame_at` delegate to it and are
    byte-identical (a golden test pins that).
  - **Live backend** (`wayland/mod.rs`, 6 new tests, live half
    `PENGYOS_IT=1`-gated): binds `wl_output` (≥ v2) and the optional
    `wp_fractional_scale_manager_v1` (`wayland-protocols` gained the `staging`
    feature), requests the per-surface fractional scale before the first
    commit, scales the `set_size` height hint while the exclusive zone stays
    the bar's *logical* height, and re-reports a `Resize` (cells) only when a
    scale change actually changes the cell width.
  - Documented protocol limitation: wlr layer-shell has no
    `output_enter`/`output_leave`, so with several *differently-scaled*
    outputs the integer reading falls back to 1:1 (fractional-scale-v1 still
    wins); per-output bars are the multi-output item.

- Reusable shared-memory pool in the Wayland backend (task 13): the backend
  keeps **one** `wl_shm` pool for the whole session instead of allocating a
  fresh temp file + `wl_shm` pool on every `paint`.
  - **`wayland::stage::PixelStage`** (pure, headless-tested): one reusable byte
    region that re-stages each frame in place, grows only when a frame outgrows
    the current capacity (a resize to a larger output) and never shrinks, with
    `reallocs`/`frames` counters so a test can assert a stable-size run allocates
    *exactly once*. 9 unit tests.
  - **`wayland::ShmPool`** (the live half): backs that region with a **`memfd`**
    (`libc::memfd_create`), created lazily on the first paint and recreated only
    on a grow-resize; each `paint` rewrites the region in place and cuts a fresh
    (cheap, one-shot) `wl_buffer` handle from the same pool. The `memfd` create
    + zero-fill (`ShmPool::backing`) is headless-tested; the live open/paint is
    `PENGYOS_IT=1`-gated (a gated test asserts one allocation for a whole
    stable-size run, and that `close` tears the pool down).
  - Why a `memfd` and not a temp file: unlinking a file on a `tmpfs` (where
    `/tmp` often lives) frees its data **immediately**, which would corrupt the
    very region the compositor `mmap`s — a `memfd` has no filesystem entry, so
    it is both correct and leaves nothing behind on a crash.
- Real Wayland `layer-shell` backend (task 11): `pengy-panel::wayland` now
  implements the `Backend` trait against a live compositor —
  `wayland-client` (0.31 `Connection`/`EventQueue`/`Dispatch`), the wlr
  `layer-shell` protocol (from `wayland-protocols-wlr`), and a shared-memory
  `wl_buffer` for pixels. The binary's `Run` path drives the `Panel` on this
  backend (previously it exited 69 as "not implemented"); a clean compositor
  close exits 0, and a failed attach (no compositor / no `layer-shell`) prints
  a one-line diagnostic and exits 69.
  - **Font/cell mapping** (`wayland::font`): a dependency-free 5×7 monospace
    bitmap font (all printable ASCII + the panel's own `●`/`◉`/`↓`/`↑` glyphs,
    hollow-box fallback) with per-`Style` Catppuccin-Mocha colors and a 6×8
    cell metric.
  - **Rasterizer** (`wayland::rasterize`): pure `Frame → ARGB8888` buffer
    (`rasterize_frame`, plus `rasterize_frame_at` for the compositor's exact
    pixel width). Fully golden-tested headlessly; only the socket plumbing is
    `PENGYOS_IT`-gated.
  - `cargo build` type-checks all the Wayland glue against the real protocol
    bindings (so it can't silently rot); a `PENGYOS_IT=1`-gated test opens the
    backend live, and an ungated test proves `open` fails gracefully (no panic,
    no block) when no compositor is present.
- Binary (task 12): **`--headless --measure-rss`** — a compositor-free one-shot
  mode that measures the **release** binary's idle resident set size and gates
  it against the idle-RSS target:
  `pengy-panel --headless --measure-rss [--width <CELLS>] [--max-mib <MIB>]
  [--epoch <SECONDS>] [-c panel.toml]`. It runs the real pipeline (build widgets
  → poll → layout → paint) through a short steady-state loop, rasterizes the
  last frame to the ARGB8888 buffer the live backend holds at idle and keeps it
  alive, then reads `VmRSS` from `/proc/self/status`. It prints
  `idle-rss: <KIB> KiB (<MIB> MiB) [limit <MIB> MiB] OK|EXCEEDED` and exits **1**
  when over `--max-mib` (default **15**, the target), else **0** — a CI-runnable
  regression guard for the target (run against the *release* build). Flag rules
  extend `--print-layout`'s: `--measure-rss` requires `--headless`, the two
  one-shot modes are mutually exclusive, and `--width`/`--epoch`/`--max-mib`
  are only accepted in a headless one-shot mode (exit 2 otherwise; invalid
  config is still exit 65).
  - **Measured on this host (release, 256 warm-up frames): ≈ 2.5 MiB**
    (2 512 KiB) for the default 120-cell bar, ≈ 2.7 MiB for
    `examples/panel.toml` (5 widgets), ≈ 2.8 MiB at a 1024-cell (4K) bar — all
    comfortably under the 15 MiB target. The number is a faithful lower bound
    on the live panel's idle RSS (it omits only the small Wayland
    socket/client overhead).

- `widgets` core (task 2): the `Widget` trait — a pure `data -> output`
  transform — plus the renderable description it produces (`Section`,
  `Style`, `TextRun`, `WidgetRender`). Renders carry text, a coarse style tag,
  and a width in text cells (no fonts/pixels), so the layout engine and the
  rendering backend can each consume them without the core changing.
- `widgets` core (task 2): the `DataSource` trait — the single seam between a
  widget and the outside world (wall clock, timezone offset, and
  `/sys/class/power_supply` reads). Every method returns `Option`, and widgets
  must render a fallback when a reading is `None`, so the panel degrades
  gracefully. A `#[cfg(test)]` `MockSource` double proves widgets are
  unit-testable with no compositor, and a `Probe` test widget exercises the
  seam end-to-end (object safety, determinism, graceful degradation, and the
  state-fed pattern the Workspaces widget will use).
- `widgets` core (task 3): the `LayoutEngine` — pure, dependency-free geometry
  that arranges `WidgetRender`s into a `Layout` of exact cell coordinates for a
  bar of a given width. Left packs from cell 0, right packs against the far
  edge, center centers in the free space between them (no overlap possible when
  everything fits). Widgets in the same section are separated by a configurable
  `gap_cells` (default 1); zero-width widgets are excluded. On overflow, whole
  widgets are dropped outside-in toward the center (alternating left/right,
  center last, deterministic) — never truncated. Covered by 28 tests including
  a seeded 2000-trial invariant sweep (nothing off-bar, nothing overlapping,
  every render placed-xor-dropped).
- `widgets` core (task 4): the four built-in widgets, all pure and
  headless-testable:
  - **`Clock`** (poll-based): reads the wall clock + UTC offset and renders a
    strftime-flavoured format (`%H %I %M %S %p %d %b %B %a %A %Y`, `%%`). The
    date math is a zero-dependency port of Hinnant's civil-time algorithm
    (`civil::CivilDate`, `civil_from_days`/`days_from_civil`), cross-checked
    against `datetime.date`. Falls back to `??` when the clock is unreadable.
  - **`Battery`** (poll-based): reads `<bat>/capacity` + `<bat>/status` from
    `DataSource`. Renders `↑`/`↓` + percent; escalating style (Normal →
    Warning → Critical) for a discharging battery below configurable
    thresholds, and a graceful `no battery` fallback when the reading is
    absent or nonsensical.
  - **`Workspaces`** (state-fed): does **not** read the `DataSource`; the
    backend drives it via `set_state(Vec<WorkspaceState>)` and `update` renders
    the state (numbered / `●` visible / `◉` focused). This is the documented
    channel for compositor-derived data.
  - **`Text` / `Template`**: a literal label and a `{{var}}`-substituting label
    (`set_vars` backend channel; missing keys substitute empty).
- `widgets` core (task 4): the **production** `DataSource` — `SysfsSource`:
  real `SystemTime` clock, `/sys/class/power_supply` file reads (trimmed,
  path-traversal-safe), battery discovery (`discover_battery` finds the supply
  with a `capacity` file), and the local UTC offset read via `date +%z`
  (DST-aware, memoized). Its parsing/traversal/discovery logic is unit-tested
  against temp-directory fixtures; the live `date` read is an opt-in
  `PENGYOS_IT=1` smoke test.
- `widgets` core (task 5): the **config model** — `PanelConfig` / `BarConfig`
  / `WidgetConfig` with every default and `PanelConfig::validate()`, the
  single home for the semantic rules: `gap_cells` 0–16 (default 1),
  `poll_interval_ms` ≥ 250 (default 1000), ≤ 64 widgets, strict clock-format
  validation (`clock::validate_format` rejects unknown `%x` tokens and a lone
  trailing `%` at load time, while the renderer stays lenient), battery
  thresholds `1 ≤ critical < low ≤ 100` (defaults 25/10), workspaces count
  1–32 (default 3), non-empty text/template. `Section` gained a `FromStr`
  impl (`left`/`center`/`right`) so the name list lives in one place. The
  core stays dependency-free; the model, not the parser, is the testable
  surface.
- Binary (task 5): **`panel.toml` parsing** — serde/toml wire structs (only
  in the binary crate, preserving the core's zero-dep boundary) with
  `deny_unknown_fields` on every table and a `type`-tagged widget enum, so
  typos are load-time errors with toml line/column context. Defaults are
  applied during mapping, then the core `validate()` runs. `pengy-panel -c`
  now actually loads the file: valid → `config OK (N widget(s))`; unreadable
  or invalid → one-line diagnostic + exit **65** (EX_DATAERR).
- `examples/panel.toml`: committed, fully commented sample config; the test
  suite parses it (and T7 will reuse it for the golden layout).
- Test suite grew to **165** green tests (22 binary + 2 github_ready + 141
  widgets), covering config defaults, every validation rule, wire-layer
  rejections (unknown type/key, wrong value type, missing required keys), and
  the committed example config.
- Binary (task 6): the **`Backend` trait** — the seam between the headless
  core and a compositor — in `pengy-panel/src/backend.rs`. Four methods
  (`open` → bar width in **text cells**, `wait(poll_interval)` →
  `Tick`/`Resize`/`Quit`, `paint(&Frame)`, `close`) and the `Frame` paint
  unit: one tick's complete output as a display-ordered list of
  `PaintedWidget { x, width, runs }` at absolute cell coordinates. Cells, not
  pixels, cross the seam; a backend maps to pixels with its font.
- Binary (task 6): the **`Panel` driver** (`panel.rs`) — owns the widget
  list, the `DataSource`, and a `LayoutEngine` built from `BarConfig`;
  `tick` = poll → layout → paint (+ returns the frame); `run` = `open` →
  loop until `Quit` → exactly one `close`, with `Resize` taking effect on the
  next tick and an `open` failure propagating without painting or closing.
- Binary (task 6): the **`HeadlessBackend`** (`headless.rs`) — scripted FIFO
  events, frame recording, and `render_frame` (frame → one text line, one
  cell per char) — proving the whole driver loop is testable with no
  compositor; it is also the foundation for T7's `--print-layout`.
- `widgets` core (task 6): the **state-fed channel** — the `Widget` trait
  gained `as_any(&self) -> &dyn Any` and `as_any_mut(&mut self) -> &mut dyn
  Any` (implemented by every built-in widget), so the driver's
  `Panel::widget_mut::<T>(id) -> Option<&mut T>` (find by `id()`, downcast)
  is how a backend drives `Workspaces::set_state` / `Template::set_vars` on
  the *same instance* it ticks. Wrong type or id is `None`, never a panic.
- Test suite grew to **201** green tests (57 binary + 2 github_ready + 142
  widgets): the backend trait (object safety, events, error shape), the
  headless backend (scripting, recording, the text projection incl. clipping
  and Unicode single-cell glyphs), and the driver (three-section layout with
  hand-computed golden lines, overflow drops, zero-width exclusion,
  per-tick source polling, the state-fed channel in both directions, the
  full run loop incl. resize-on-next-tick and quit/close discipline, `open`
  failure, and a real `Clock` at a fixed epoch). The real Wayland
  `layer-shell` backend implementing the trait is a follow-up checklist item
  (its tests will be `PENGYOS_IT=1`-gated).
- `widgets` core (task 7): **`build::build_widgets(&PanelConfig) ->
  Vec<Box<dyn Widget>>`** — the pure bridge from the validated config model
  to a live widget list (in the zero-dep core, so it stays
  headless-testable): all five widget kinds, config order preserved, built
  state-fed widgets at their initial state.
- Verified (task 7 checklist item closed): the `build::build_widgets` bridge
  carries 6 core unit tests (all five widget kinds with field/section
  fidelity, config order, initial state for state-fed widgets, purity for the
  same config, default-applied entries) and is exercised end-to-end by the
  `--print-layout` tests that launch the real binary through it. The full
  workspace suite — **244 tests** (73 binary + 2 github_ready + 9
  print_layout e2e + 155 + 5 perf_contract + 0 doc, incl. the builder's 6) —
  is green with and without `PENGYOS_IT=1`.
- Binary (task 7): **`--headless --print-layout` one-shot mode** —
  `pengy-panel --headless --print-layout --width <CELLS> [--epoch <SECONDS>]
  [-c panel.toml]` builds the widget list from the config, runs exactly one
  poll → layout → paint cycle on the `HeadlessBackend`, and prints the bar
  as a single text line of exactly `<CELLS>` characters to stdout (exit 0).
  `--epoch` pins the wall clock (via `PinnedSource`, which delegates
  tz/sysfs to the real `SysfsSource`) so the output is deterministic for CI;
  state-fed widgets are host-independent regardless. The flags must be given
  in the documented combination (`--print-layout` ⇔ `--headless`,
  `--width` required) — violations are one-line diagnostics with exit 2;
  unreadable/invalid config stays exit 65.
- Test suite grew to **232** green tests: builder coverage in the core, the
  CLI flag-combination rules, host-independent golden lines (clock at a fixed
  epoch, battery arrows/thresholds from a canned sysfs, overflow drops,
  zero-width template exclusion, `gap_cells`), and 9 end-to-end tests that
  launch the **real binary** (via `CARGO_BIN_EXE_pengy-panel`) and assert the
  exact stdout line, exit code, and stderr diagnostic.
- `widgets` core (task 8): **`bench` module** — a deterministic,
  panel-shaped, zero-dependency layout workload (`bench::workload(count, seed)`)
  plus the `STANDARD_WIDGET_COUNT` / `STANDARD_BAR_WIDTH` constants the
  "< 1 ms on 20 widgets" target is measured against. The workload is public and
  pure so the criterion bench and the unit tests measure the *same* input.
- `widgets` core (task 8): **criterion layout benchmark** —
  `widgets/benches/layout.rs` benchmarks one `LayoutEngine::layout` at
  4 / 20 / 40 / 80 widgets on a 120-cell bar and an overflow-heavy case (40
  wide widgets on a 20-cell bar), reporting per-layout cost and throughput.
  `criterion` is a **dev**-dependency (`[[bench]] harness = false`), so it
  never links into the published rlib and the crate's empty `[dependencies]`
  table (the headless-test boundary) stays intact. Run with
  `cargo bench -p widgets --bench layout`.
- Memory/perf (task 8): the idle-RSS (≤ 15 MiB) and layout-recompute
  (< 1 ms on 20 widgets) targets are documented in `ARCHITECTURE.md`, and a
  `perf_contract` doc-guard integration test keeps them and the bench wiring
  honest (it fails if the targets, the `criterion` dev-dep, the `[[bench]]`
  target, or the empty `[dependencies]` boundary drift). Idle RSS stays a
  target until the T11 backend provides a running release process to measure.
- Test suite grew to **244** green tests (7 `bench`-module unit tests + 5
  `perf_contract` doc-guard tests).
- Verified (checklist gate): `cargo test` is **all green** — 244 tests pass
  (73 binary + 2 github_ready + 9 print-layout e2e + 155 widgets unit + 5
  `perf_contract` doc-guard), with zero compiler warnings on a clean
  `cargo build` / `cargo test`.
- Test suite grew to **286** green tests (107 binary + 2 github_ready + 8
  measure-rss e2e + 9 print-layout e2e + 155 widgets + 5 perf_contract), all
  green with and without `PENGYOS_IT=1`, zero warnings: the `parse_vmrss_kib`
  parser (canned `/proc/self/status`), the integer KiB-vs-limit gate + MiB
  projection, the held-buffer geometry, and 8 end-to-end tests that launch the
  real binary and assert the stdout line, the positive KiB value, and the exit
  code using **deterministic limits** (a huge limit always passes, a `0` MiB
  limit always fails) so the gate is asserted independently of the machine or
  the debug/release build.

## [0.1.0] - 2026-09-15

### Added

- Cargo workspace scaffold: `widgets` core lib + `pengy-panel` binary crate,
  MIT-licensed, with the headless-test boundary documented in
  `ARCHITECTURE.md`.
- `widgets` core: `AppInfo` version metadata (single source of truth for the
  `--version` line).
- Binary CLI: `--config <file>`, `--help`, `--version`; usage errors exit 2;
  starting the panel without the (not-yet-implemented) backend exits 69 with
  a clear diagnostic instead of pretending.
- `github_ready` integration test asserting the GitHub-ready contract:
  README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
  and .gitignore all exist at the workspace root.
- Project docs: `README.md`, `CHANGELOG.md`, `ARCHITECTURE.md`, `.gitignore`.
