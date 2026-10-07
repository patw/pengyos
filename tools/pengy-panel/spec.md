# pengy-panel — pure-Rust Wayland layer-shell panel

## Summary
A fast, low-memory status bar built on layer-shell (no GTK), with a modular widget core.

## Purpose
The toolkit-thread wedge: own the desktop surface in pure Rust (SPEC §2.3 artistry).

## Deliverable
A Rust workspace: `pengy-panel` (binary) + a `widgets` lib, with a headless testable core (layout/data) separated from rendering.

## Interface
- pengy-panel --config panel.toml
- core library (`widgets`): `Widget` trait, `DataSource` seam, `LayoutEngine`,
  and the Clock/Battery/Workspaces/Text widgets

## Core model (refined during implementation)

The core is a pure `data -> output` pipeline; no widget touches the outside
world directly.

- **`Widget` trait** — `id()`, `section()` (Left/Center/Right), and
  `update(&mut self, &dyn DataSource) -> WidgetRender`. It is object-safe, so a
  panel holds a heterogeneous `Vec<Box<dyn Widget>>`. A widget is either
  **poll-based** (reads its `DataSource` in `update`) or **state-fed** (the
  backend sets internal state; `update` renders it and ignores the source).
- **`WidgetRender`** — the renderable description a widget produces: a section,
  ordered `TextRun`s (text + a coarse style tag), and a width in **text cells**
  (counted as UTF-8 chars — a documented approximation; the backend may refine
  it at render time). No fonts, colors, or pixels live in the core.
- **`DataSource` trait** — the seam for the *non-compositor inputs a panel
  polls*: wall-clock seconds since the epoch, signed UTC offset (seconds), and
  `/sys/class/power_supply` file reads. Every method returns `Option`, and
  widgets render a fallback when a reading is `None`, so the panel degrades
  gracefully (e.g. no battery). Compositor-derived data (e.g. workspaces) is
  **not** part of `DataSource` — the backend drives those widgets directly.
- **`LayoutEngine` (T3)** — pure geometry over `WidgetRender` values (no
  widgets, no source, no IO). Given a bar width (text cells) and a list of
  renders: renders group into `Left`/`Center`/`Right` (input order preserved
  within a section); zero-width renders are excluded; the left section packs
  from cell 0, the right section packs against the far edge, and the center
  section centers in the free space *between* them — which guarantees no
  overlap when everything fits. Adjacent widgets within a section are separated
  by `LayoutConfig::gap_cells` (default 1); there is no gap between sections.
  When the total (widgets + intra-section gaps) exceeds the bar width, whole
  widgets are dropped — outside-in toward the center, alternately from the
  left and right sections (tie → left), center widgets last in input order —
  until it fits; widgets are never truncated. Output is a `Layout` of
  `PlacedWidget { index, x, width }` per section plus the dropped indices in
  drop order. All of this is deterministic and headless-tested.
- **Built-in widgets (T4)** — four widgets, all pure and headless-testable:
  - **Clock** (poll-based): reads `now_secs` + `tz_offset_secs`, converts the
    epoch to a civil date + time in the local zone, and renders a
    strftime-flavoured format (`%H %I %M %S %p %d %b %B %a %A %Y`, `%%`). The
    date math is a zero-dependency port of Hinnant's civil-time algorithm; the
    `civil_from_days` step **must** add one to the year for Jan/Feb (the `yoe`
    math runs on the shifted year where Jan/Feb belong to the previous year)
    or those dates come out a year early. Falls back to `??` when the clock is
    unreadable, and to UTC when the offset is `None`.
  - **Battery** (poll-based): reads `<bat>/capacity` + `<bat>/status`. Renders
    an arrow (`↓` draining, `↑` on power, none when status is unknown) +
    percent; the style escalates Normal→Warning→Critical for a *discharging*
    battery below configurable low/critical thresholds (defaults 25/10), but a
    *connected* battery (charging/full/not-charging) is never warned. Missing
    or non-numeric capacity → a graceful `no battery` fallback.
  - **Workspaces** (state-fed): does **not** read the `DataSource`. The backend
    pushes compositor state via `set_state(Vec<WorkspaceState>)` and `update`
    renders it (empty = number, visible = `●`, focused = `◉`). This is the
    required channel for compositor-derived data (a `Vec<Box<dyn Widget>>` can
    tick but not set state — the backend keeps a typed handle per such widget).
  - **Text / Template**: a literal label, and a `{{var}}`-substituting label
    (values set by the backend via `set_vars`; a missing key substitutes an
    empty string; `{{name}}` with an empty/invalid name is left verbatim).
- **Production `DataSource` (T4)** — `SysfsSource` is the concrete source the
  backend wires in at runtime (it lives in the zero-dep core, using only
  `std`): `SystemTime` for the clock; trimmed, path-traversal-safe reads of
  `<base>/<entry>` under `/sys/class/power_supply` (base injectable for
  fixtures); `discover_battery()` finds the first supply with a `capacity`
  file (AC adapters have none → `None` on a desktop/server); and the local UTC
  offset (seconds **east** of UTC) read via `date +%z` — note `SystemTime` is
  UTC-absolute so it cannot yield the offset, and `/etc/timezone` is not
  present on every host, so `date +%z` (the `+` is mandatory) is the
  zero-dep, DST-aware source. The offset is memoized (it only changes on a
  DST/TZ switch). The pure parts are unit-tested against temp-dir fixtures;
  the live `date` read is an opt-in `PENGYOS_IT=1` smoke test.

- **Config → widgets builder (T7 bridge)** — `widgets::build::build_widgets(&PanelConfig) -> Vec<Box<dyn Widget>>` (and `build_widget` for one entry) is the pure bridge between the validated config model and a live widget list. It lives in the **zero-dep core** (pure construction; the wire layer has already applied defaults and validated, so the builder assumes `validate()`'s invariants). Built state-fed widgets start at their initial state (all-empty workspaces, no template variables); the backend drives them later through the `Panel::widget_mut` channel.
- **`panel.toml` config (T5)** — the config support is split across the
  crates along the dependency rule:
  - **Model + rules live in the zero-dep core** (`widgets::config`):
    `PanelConfig { bar: BarConfig, widgets: Vec<WidgetConfig> }` where
    `BarConfig { gap_cells: u32, poll_interval_ms: u64 }` and
    `WidgetConfig` is an enum over `Clock { section, format }`,
    `Battery { section, supply, low, critical }`,
    `Workspaces { section, count }`, `Text { section, text }`,
    `Template { section, text }`. `PanelConfig::validate()` enforces every
    semantic rule and reports the **first** violation (deterministic, so a
    test can assert on it): `gap_cells` in `0..=16`; `poll_interval_ms` ≥ 250;
    at most 64 widgets; clock formats pass **strict** strftime validation
    (`clock::validate_format` — an unknown `%x` or a lone trailing `%` is a
    load-time error, even though the renderer is lenient and would pass such
    tokens through); battery thresholds must satisfy `1 ≤ critical < low ≤ 100`;
    workspaces count in `1..=32`; text/template non-empty after trim.
    `Section` gained a `FromStr` impl (`left`/`center`/`right`,
    case-sensitive) so the section-name list lives in exactly one place.
  - **TOML syntax lives in the binary crate** (`pengy-panel/src/config.rs`,
    the only crate allowed to depend on `serde`/`toml`): wire structs mirror
    the file with `#[serde(deny_unknown_fields)]` and a `type`-tagged widget
    enum, so typos (`type = "cpu"`, `low = 25` on a clock, a stray key in
    `[bar]`) are load-time errors; `parse(&str) -> Result<PanelConfig, ConfigError>`
    = TOML syntax check → per-widget mapping (defaults applied) →
    `validate()`.
  - **Defaults** (an empty `panel.toml` is legal and means "all defaults"):
    `gap_cells = 1`, `poll_interval_ms = 1000`, clock `format = "%H:%M"`,
    battery `supply = "BAT0"` / `low = 25` / `critical = 10`, workspaces
    `count = 3`. Each `[[widgets]]` entry **requires** `type` and `section`;
    there is deliberately no default section.
  - **CLI:** `pengy-panel -c panel.toml` now actually loads + validates the
    file: unreadable/invalid config → one-line diagnostic on stderr + exit
    **65** (EX_DATAERR); valid config → `config OK (N widget(s))` on stderr,
    then the existing exit-69 "backend not implemented" path.
  - `examples/panel.toml` is a committed, fully-commented sample that the
    test suite parses (and T7 will reuse for its golden layout).
- **Backend trait + headless driver (T6)** — the seam between the core and
  the compositor lives in the *binary* crate (`pengy-panel/src`), per the
  dependency rule (the core never touches a compositor):
  - **`Backend` trait** (`backend.rs`): four methods — `open() -> Result<u32,
    BackendError>` (connect + create the layer surface; returns the bar width
    **in text cells**, which the backend computes from its font),
    `wait(timeout) -> BackendEvent`, `paint(&Frame)`, `close()`.
    `BackendEvent` is `Tick` / `Resize { width_cells }` / `Quit`. All sizes
    cross this trait in cells, never pixels — a backend maps cells to pixels
    with its font at paint time.
  - **`Frame`** (`backend.rs`): the complete output of one tick — `width`
    (cells) plus `placed: Vec<PaintedWidget>` in display order (ascending
    `x`), each with an absolute `x`, a `width`, and the widget's `TextRun`s.
    Sections are already resolved to coordinates; a backend paints one flat
    list in a single pass.
  - **`Panel` driver** (`panel.rs`): owns the widget list, the `DataSource`,
    a `LayoutEngine` built from `BarConfig` (`gap_cells` to the gap,
    `poll_interval_ms` to the loop period) and the current bar width.
    `tick(backend)` = update every widget, layout on the current width,
    paint + return the `Frame`. `run(backend)` = `open()`, then loop
    { `tick`; `wait(poll_interval)` } until `Quit`, then exactly one
    `close()`; a `Resize` changes the width for the **next** tick. An
    `open()` error propagates: nothing is painted and `close()` is *not*
    called (there is nothing to release). The width always comes from the
    backend, never guessed by the panel.
  - **The state-fed channel is concrete now**: a `Vec<Box<dyn Widget>>` can
    tick widgets but cannot call `set_state`/`set_vars` through `dyn`. The
    `Widget` trait therefore gained `as_any(&self) -> &dyn Any` and
    `as_any_mut(&mut self) -> &mut dyn Any` (object-safe; every built-in
    widget implements both), and the driver exposes
    `Panel::widget_mut::<T>(id) -> Option<&mut T>` — find by `id()`, then
    downcast. A wrong type or id is `None`, never a panic. The backend keeps
    each widget's id + concrete type (both come from the config) and drives
    state-fed widgets through this one channel, always reaching the *same
    instance* the panel ticks.
  - **`HeadlessBackend`** (`headless.rs`): the core-side proof that the seam
    needs no compositor — a scripted, recording backend: `open` returns a
    programmable width, `wait` replays a FIFO event queue (an empty queue
    yields `Tick`, so a loop always makes progress), `paint` records every
    `Frame`, and `open`/`close`/poll-timeouts are counted.
    `render_frame(&Frame) -> String` projects a frame to one text line (one
    cell per char, gaps as spaces; a run longer than its reservation is
    clipped at the bar edge, never a panic). The whole driver — poll, layout,
    resize, quit, close, the state-fed channel, and real widgets at a fixed
    epoch — is exercised against it with hand-computed golden lines.
  - The real Wayland `layer-shell` implementation of `Backend` (task 11)
    **is now implemented** in `pengy-panel/src/wayland/`, behind the same
    seam. Its *geometry and pixels* are pure and golden-tested headlessly;
    its *socket plumbing* is `PENGYOS_IT=1`-gated. Key real-world facts
    discovered while landing it:
    - **layer-shell is a *wlroots* protocol** — its bindings live in the
      `wayland-protocols-wlr` crate (`layer_shell::v1::client`), **not** in
      `wayland-protocols`. wlr's layer-shell also depends on the xdg-shell
      `xdg_popup` interface, so `wayland-protocols` (with the `client` feature)
      must be present too.
    - **`wayland-client` 0.31's API is `Connection` + `EventQueue<State>` +
      `Dispatch`** (not the old `Display::connect()`). A client implements
      `Dispatch<WlRegistry, ()>` to bind globals as they advertise, and
      `delegate_noop!` for interfaces it binds but ignores. Constructors
      (e.g. `get_layer_surface`, `create_buffer`) take the protocol args and
      then `(&QueueHandle, user_data)`.
    - **`wayland-backend` 0.3 dropped the ready-made shm helpers**
      (`UninitBuffer`/`SharedMemory`); the backend backs a `wl_shm_pool` with
      a real file descriptor, then `create_buffer`. As of task 13 that fd is a
      **`memfd`** (see the reusable-pool bullet below) — not a temp file.
    - **Reusable shm pool (task 13):** the backend keeps **one** `wl_shm` pool
      for the whole session instead of allocating a fresh temp file + pool +
      `wl_buffer` on every `paint`. The pool is backed by a **`memfd`** (an
      anonymous shared-memory fd) — *not* a temp file: unlinking a file on a
      `tmpfs` (where `/tmp` often lives) frees its data **immediately**, which
      would corrupt the very region the compositor `mmap`s. The pure,
      headless-testable half is `wayland::stage::PixelStage`: one reusable byte
      region, re-staged in place each frame, grown only when a resize needs more
      room, with `reallocs` counting allocations (so a stable-size run allocates
      exactly once). The live `wayland::ShmPool` maps that region onto a
      `wl_shm` pool (created lazily on the first paint, recreated only on a
      grow-resize). A consumed `wl_buffer` cannot be reattached, so each frame
      still cuts a fresh (cheap) buffer *handle* from the same pool — the pool
      and its backing region are what get reused, which is the real per-frame
      waste the idle-RSS target (T12) targets. `ShmPool::backing` (the `memfd`
      create + zero-fill) is headless-tested; the live open/paint is
      `PENGYOS_IT=1`-gated.
    - **Output scale (task 14):** the bar renders correctly and *crisply* at
      non-100% scaling. The pure half lives in `wayland::scale::Scale`
      (headless-tested): a positive rational `num/den` with `resolve(integer,
      fractional_fixed)` encoding the fractional-scale-v1 rule — a **nonzero**
      `wp_fractional_scale_v1.preferred_scale` (24.8 fixed point: 256 = 1.0,
      384 = 1.5, 512 = 2.0; 0 means "use the integer scale") **replaces** the
      integer `wl_output.scale`; else the integer reading (clamped ≥ 1); else
      1:1. Pixel math is `scaled_px` = `logical * num / den` **rounded to
      nearest, saturated at `u32::MAX`, in `u64` — no floats**. The drawing
      primitive is now one function, `rasterize_frame_scaled(frame, font,
      height_cells, width_px, scale)` (the 1:1 `rasterize_frame`/
      `rasterize_frame_at` delegate to it and stay byte-identical): the font
      cell is `cell_w × cell_h` logical pixels holding a 5×7 glyph (right
      column + bottom row are margin), and every logical pixel — glyph or
      margin — maps to `[k*cw/cell_w, (k+1)*cw/cell_w)` pixels of the scaled
      cell, so integer scales (2:1) are exact block scaling and fractional
      scales (1.5) stretch proportionally, with the cell's margins scaling too
      (tests pin: 2:1 == 2×2 blocks of the 1:1 pixel, 1.5 keeps the scaled
      margins blank, right-edge clipping unchanged).
      The live backend: binds every `wl_output` (≥ v2, for the `scale` event)
      and the optional `wp_fractional_scale_manager_v1` (needs the `staging`
      feature of `wayland-protocols`); requests the per-surface fractional
      scale next to the layer surface, *before* the first commit; scales the
      `set_size` height hint (physical px) while the exclusive zone stays the
      bar's *logical* height; recomputes the hint at layer-surface creation
      (the compositor's advertisement order is not guaranteed — a `scale`
      reading can arrive first); and on any scale change re-applies the size
      and reports a `Resize` **only when the cell width actually changed**
      (`cells_for_px` = `width_px / scaled cell width`; the last reported
      width is remembered so a no-op never re-fires). `paint` rasterizes at
      `app.effective_scale()`. A scale reading that changes only after
      `open` (e.g. the compositor adopts fractional scaling late) reaches the
      panel through the same `Resize` channel. **Protocol limitation
      (discovered):** the wlr `layer-shell` protocol reports only `configure`
      and `closed` for the layer surface — there is **no** `output_enter`/
      `output_leave` — so the client cannot tell which of several outputs the
      bar sits on. Integer reading selection (`integer_scale`, pure +
      headless-tested) is therefore: one output → its reading; several uniform
      outputs → the reading; several *differently-scaled* outputs → 1 (don't
      guess). A compositor advertising fractional-scale-v1 still scales such
      setups correctly, because `preferred_scale` is per-surface and replaces
      the integer reading. Per-output bars (one layer surface per monitor) are
      the separate multi-output checklist item — **implemented in task 15**
      (below), which supersedes the `integer_scale` guess: with one surface per
      output, each surface knows its own output, so the scale is unambiguous.
      The live path is `PENGYOS_IT=1`-gated (a gated test asserts the open width
      / pixel width / reported scale are mutually consistent; without a
      compositor it skips).
    - **Multiple outputs (task 15):** the backend paints **one bar per
      monitor** — one `wl_surface` + `zwlr_layer_surface_v1` per advertised
      `wl_output`, each with its own size hint/exclusive zone, its own
      per-surface fractional-scale object, and its own reusable
      [`ShmPool`] (so two bars of different sizes never share a region). The
      state machine that is *not* testable against a live compositor is split
      into a pure, headless-tested model, `wayland::outputs::MultiOutput`,
      mirroring the `Scale`/`PixelStage` pattern: it tracks per-output integer
      `scale`, per-surface fractional reading, `configure` width, and
      `entered`/`created` flags, and makes the decisions a paint/resize pass
      needs.
      - **The layer-shell limitation is resolved by `wl_surface`:** layer-shell
        reports only `configure`/`closed`, but a layer surface *is* a
        `wl_surface`, which does emit `enter`/`leave`. Each dedicated surface
        therefore knows it is mapped (the `entered` flag), and — because it is
        dedicated to one output — its scale is that output's reading (no
        global guess). The `wl_surface` dispatch carries the output id as user
        data (set at `create_surface`).
      - **The single-bar `Backend` seam is driven by a *primary* output**
        (deterministic: the mapped, sized output; one candidate → it; several →
        the first mapped, else the first, in ascending-id order). `open` returns
        the primary's cell width; `Resize` fires only when the *primary's* cell
        width actually changed (a scale/size change on a secondary output never
        reaches the panel). `paint` takes the panel's single frame (laid out
        against the primary) and rasterizes it for **each** mapped, sized
        surface at that surface's own `width_px` and scale.
      - **Lifecycle:** a new `wl_output` global → `ensure_surfaces` builds its
        dedicated surface (called after every relevant global advertisement,
        since order is not guaranteed); a `global_remove` (monitor unplugged)
        destroys that surface and drops it from the model; a layer `closed`
        does the same. The panel quits only when the **last** output is gone.
      - **Known limitation (documented, follow-up item):** the panel lays out
        one frame against the primary's width and the backend replicates it to
        every surface. This is exact for equal-size monitors (the common case);
        on mixed-size monitors a secondary surface gets the primary-width frame
        clipped/padded at its own width, so right-anchored widgets are not
        re-flowed per monitor. True per-monitor layout (a per-output `Frame`)
        is the separate unchecked checklist item — it changes the `Panel`/
        `Backend` seam, not just the backend.
      - **Tested:** the `MultiOutput` model is exhaustively headless-tested
        (add/remove/scale/fractional/configure/enter/leave, per-output scale,
        primary selection, resize detection/dedupe, paint targets, and a full
        plug/unplug lifecycle); the live open/paint is `PENGYOS_IT=1`-gated
        (a gated test asserts one surface per output and that the open width is
        the primary's cell width; without a compositor it skips).
    - **`Backend::wait(timeout)`** needs a *bounded* blocking wait on the
      socket: the API exposes the fd (`EventQueue: AsFd` / the
      `ReadEventsGuard::connection_fd` — which must be created *before*
      polling) but no timed wait, so the backend uses `libc::poll`.
    - The `Run` path (without the one-shot flags) now drives the `Panel` on
      this backend: a clean compositor close exits 0; a failed attach (no
      compositor, or the compositor lacks `layer-shell`) prints a one-line
      diagnostic and exits **69**.
- **`--headless --print-layout` (T7)** — a one-shot, compositor-free mode for
  CI assertions: `pengy-panel --headless --print-layout --width <CELLS> [--epoch <SECONDS>] [-c panel.toml]` builds the widget list from the config (`build_widgets`), runs exactly one poll → layout → paint cycle on the `HeadlessBackend`, and prints the frame as **one text line of exactly `width` characters** (one cell per char, via `render_frame`) to stdout — exit 0. Flag rules (all enforced in `cli::parse`, all exit **2** with a one-line diagnostic): `--print-layout` and `--headless` must appear **together**; `--print-layout` requires `--width <CELLS>` (a non-negative integer — there is no backend to negotiate a size); `--headless`/`--width`/`--epoch` are rejected outside `--print-layout`. With no `-c`, the default (empty) config renders an all-spaces line. Config loading and the exit-65 behavior are identical to the normal run path; stdout carries only the layout line (the `config OK` diagnostic stays on stderr).
  - **Determinism:** the wall clock is the one reading that would otherwise vary between runs. `--epoch <SECONDS>` pins it via `PinnedSource` (a `DataSource` that overrides `now_secs` and delegates tz + power-supply reads to the real `SysfsSource`); `--epoch` omitted → live clock. State-fed and text widgets are host-independent regardless; poll-based widgets reflect the real machine (a server prints the battery's `no battery` fallback).
  - **Tested two ways:** unit goldens on `layout_line_with` (binary `print_layout.rs`) with a fully canned source (fixed epoch/tz/sysfs → host-independent: clock at a fixed epoch, battery arrows/thresholds, overflow drops, zero-width template exclusion, `gap_cells`); and end-to-end tests (`pengy-panel/tests/print_layout.rs`) that launch the **real binary** via `CARGO_BIN_EXE_pengy-panel` and assert the exact stdout line, exit code, and stderr diagnostic for success (a host-independent state-fed config at a pinned epoch) and every flag/combo/config error.
- **`--headless --measure-rss` (T12)** — a one-shot, compositor-free mode that
  measures the **release** panel's idle resident set size (the idle-RSS target
  in ARCHITECTURE.md):
  `pengy-panel --headless --measure-rss [--width <CELLS>] [--max-mib <MIB>] [--epoch <SECONDS>] [-c panel.toml]`.
  It builds the widget list from the config, drives the real `Panel` through a
  short steady-state loop of poll → layout → paint (256 warm-up frames),
  **rasterizes the last frame to an ARGB8888 buffer — the same buffer the live
  Wayland backend holds at idle — and keeps it alive**, then reads the process's
  `VmRSS` from `/proc/self/status`. Because the held buffer is exactly the
  live idle state, the number is a **faithful lower bound** on the live panel's
  idle RSS (it omits only the small Wayland socket/client overhead). Flags:
  `--width <CELLS>` (bar width, default 120 — a representative single-monitor
  bar); `--max-mib <MIB>` (gate limit, default **15**, the target). Output: one
  stdout line `idle-rss: <KIB> KiB (<MIB> MiB) [limit <MIB> MiB] OK|EXCEEDED`;
  exit **1** when the measured size exceeds the limit (else **0**), so it is a
  CI-runnable regression guard for the target (run it against the *release*
  build). Flag rules extend T7's: `--measure-rss` requires `--headless`, it and
  `--print-layout` are mutually exclusive, and `--width`/`--epoch`/`--max-mib`
  are accepted only in a headless one-shot mode (exit 2 otherwise; invalid
  config is still exit 65). Config loading and the exit-65 behavior are shared
  with `--print-layout`.
  - **Determinism / testability:** the parser (`parse_vmrss_kib`) is pure and
    unit-tested against canned `/proc/self/status` text; the arithmetic (integer
    KiB-vs-limit gate, MiB projection, buffer geometry) is unit-tested; and 8
    end-to-end tests launch the real binary and assert the stdout line, the
    positive KiB value, and the exit code — using **deterministic limits** (a
    huge limit always passes, a `0` MiB limit always fails) so the gate
    behavior is asserted independently of the machine or the debug/release
    build. **Measured on this host (release, 256 warm-up frames): ≈ 2.5 MiB**
    for the default 120-cell bar, ≈ 2.7 MiB for `examples/panel.toml`, ≈ 2.8 MiB
    at a 1024-cell (4K) bar — all comfortably under the 15 MiB target.

- **`--measure-rss-live` (T12 live residual)** — the live complement of
  `--headless --measure-rss`: instead of measuring the release pipeline with
  *no* compositor (a faithful lower bound that omits the Wayland client/socket
  overhead), this mode runs the panel's **real** Wayland `layer-shell` backend
  against a live compositor for a short steady-state window and samples the
  process's resident set size **while it is actually connected and painting** —
  i.e. the headless number **plus** the measured Wayland socket/client
  overhead. Usage: `pengy-panel --measure-rss-live [--max-mib <MIB>] [-c
  panel.toml]`.
  - **Mechanism:** build the widget list from the config, open the real
    `WaylandBackend`, then run a bounded steady-state loop
    (`pengy-panel::live_rss::run_steady`, a time-bounded cousin of
    `Panel::run`) for a fixed window (2 s default): each iteration ticks →
    paints, and the process's `VmRSS` (`/proc/self/status`) is sampled on a
    fixed cadence (250 ms). The reported idle RSS is the **minimum** sample
    over the window — the kernel lazily frees pages the steady panel stops
    touching, so idle is the floor and warm-up allocation spikes can only raise
    a sample, never lower the settled value. Output: one stdout line
    `live-idle-rss: <KIB> KiB (<MIB> MiB) [limit <MIB> MiB] OK|EXCEEDED`.
  - **Exit codes:** **0** within the limit (default `--max-mib` 15, the
    documented target), **1** when exceeded, **69** when the panel cannot
    attach to a layer-shell compositor (no `WAYLAND_DISPLAY`, no
    `layer-shell` global, or the compositor never sized the surface — the same
    EX_UNAVAILABLE path the `Run` path uses), **65** for an invalid `-c` config
    (config loading runs before any backend is touched).
  - **Flag rules** (enforced in `cli::parse`, all exit **2**): it is a
    one-shot mode **mutually exclusive** with `--print-layout` and
    `--measure-rss`; it is a *live* mode, so it **requires a compositor** and
    is rejected together with `--headless`; it takes **neither** `--width`
    (the width comes from the compositor) **nor** `--epoch` (the clock is
    live); `--max-mib` is accepted as the gate limit.
  - **Tested where:** the *pure* parts are unit-tested headlessly —
    `wayland_socket_path` (resolves `WAYLAND_DISPLAY` + `XDG_RUNTIME_DIR`,
    absolute-vs-abstract names, `None` on missing pieces), `steady_min_kib`
    (the min reduction; empty → `None`), the `LiveRssMeasurement`
    gate/format arithmetic, and `run_steady` (the bounded open → tick/wait →
    close loop, generic over the `Backend` and driven headlessly against
    `HeadlessBackend`: it paints ≥ 1 frame, closes exactly once, applies a
    mid-window `Resize` on the next tick, propagates an `open` failure without
    closing, and honours a `None`-returning sampler). The *live* part (actually
    attaching and sampling) is `PENGYOS_IT=1`-gated end-to-end: it accepts a
    real measurement (positive KiB, exit 0/1 by limit) **or** a clean
    no-compositor diagnostic (exit 69), and skips without `PENGYOS_IT=1`. The
    un-gated e2e tests assert only the compositor-free flag/config behavior
    (deterministic on any host).
  - **Blocked on a real wlroots compositor:** this mode is the mechanism by
    which the *live-compositor* idle RSS is recorded, but recording the number
    itself needs a compositor that (a) supports `wlr-layer-shell` and (b) sizes
    the surface — a **wlroots** compositor (sway/labwc/wayfire). A
    gnome-shell/Mutter session (the only compositor present on the reference
    host) accepts the layer surface but never sends a `configure` with a width,
    so `open` fails gracefully (exit 69) and no number can be sampled;
    installing a wlroots compositor requires root (`apt`), so the recorded
    number is a follow-up gated on `NEEDS.md`.

## Verification (tests are first-class)
- LayoutEngine: sizing/overflow/alignment cases.
- Battery widget from /sys fixtures (charging/discharging/full/critical).
- Clock formatting under fixed TZ.
- config validation errors (wire layer + core `validate()`, incl. the
  committed `examples/panel.toml`).
- backend driver on the `HeadlessBackend`: poll/layout, resize-taking-effect
  on the next tick, quit/close discipline, `open()` error propagation, the
  `widget_mut` state-fed channel (right/wrong id and type), and a real
  `Clock` at a fixed epoch — all with hand-computed golden lines.
- headless --print-layout golden output for a sample config.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Core is testable headlessly (no compositor needed).
- `cargo test` green.

## Non-goals
- Full Wayland protocol coverage (integration-gated)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
