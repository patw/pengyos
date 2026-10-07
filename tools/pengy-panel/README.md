# pengy-panel

A fast, low-memory status bar for Wayland, written in pure Rust on top of
`layer-shell` — no GTK, no C++ bindings, no toolkit.

pengy-panel is the toolkit-thread wedge of PengyOS: it owns the desktop
surface in pure Rust.

## Architecture (in one paragraph)

The workspace has two crates. `widgets` is the **headless-testable core**:
the `Widget` trait, a data-source abstraction, the `LayoutEngine`, and the
built-in widgets (Clock, Battery, Workspaces, Text/Template). It has **zero**
dependencies and never touches a compositor. `pengy-panel` is the thin
binary: CLI parsing, config loading, and the layer-shell rendering backend.
The dependency arrow points one way only — binary → core. Full rationale in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Layout

```text
pengy-panel/           workspace root
├── widgets/           core lib: data + layout + config model, no deps
├── pengy-panel/       binary: CLI, config parsing, layer-shell backend
├── examples/          panel.toml sample (parsed by the test suite)
├── spec.md            task spec (the source of truth for *what*)
├── ARCHITECTURE.md    the headless-test boundary and its rationale
```

## Building & testing

```sh
cargo build          # build both crates
cargo test           # full headless test suite (no compositor, no network)
```

All tests run with no display, no network, and no hardware: system inputs
(batteries, clock) are injected behind traits and swapped for committed
fixtures. Real-compositor integration tests are opt-in via `PENGYOS_IT=1`.

## Configuration

`pengy-panel --config panel.toml` (or `-c`). A fully commented sample lives in
[examples/panel.toml](examples/panel.toml). Every key is optional; an empty
file is valid and means "all defaults".

```toml
[bar]
gap_cells = 1            # 0–16, default 1 — intra-section gap in text cells
poll_interval_ms = 1000  # >= 250, default 1000 — clock/battery refresh rate

[[widgets]]               # each entry needs `type` and `section`
type = "clock"            # clock | battery | workspaces | text | template
section = "center"        # left | center | right
format = "%a %b %d  %H:%M" # clock: strftime tokens %H %I %M %S %p %d %b %B %a %A %Y, %%

[[widgets]]
type = "battery"
section = "right"
supply = "BAT0"           # /sys/class/power_supply entry, default "BAT0"
low = 25                  # defaults 25 / 10; must satisfy 1 <= critical < low <= 100
critical = 10

[[widgets]]
type = "workspaces"
section = "left"
count = 3                 # 1–32, default 3

[[widgets]]
type = "text"             # or "template" with {{var}} placeholders
section = "left"
text = "PengyOS"
```

The file is parsed **and validated** at startup: unknown widget types, unknown
keys, wrong value types, bad clock tokens (e.g. `%Q`), inverted battery
thresholds, and out-of-range counts are all load-time errors — a one-line
diagnostic on stderr and exit code 65. The schema, defaults, and validation
rules are authoritative in [`widgets/src/config.rs`](widgets/src/config.rs)
and [spec.md](spec.md); the TOML wire layer is
[`pengy-panel/src/config.rs`](pengy-panel/src/config.rs).

## Headless one-shot mode (CI)

The panel can render its layout as plain text without any compositor:

```sh
pengy-panel --headless --print-layout --width 40 --epoch 1000000000 -c panel.toml
```

prints the bar as **one line of exactly `--width` characters** (one text
cell per character) and exits 0 — the same one poll → layout → paint cycle
the real backend runs, projected to text. `--epoch <SECONDS>` pins the wall
clock for deterministic output; without it, poll-based widgets (clock,
battery) read the real machine. `--print-layout` and `--headless` must be
given together, and `--width` is required; violations are usage errors
(exit 2), invalid config stays exit 65. This is what CI asserts on.

## Status

The core is in place and the real compositor backend has landed: the `Widget`
trait and `DataSource` seam (task 2), the `LayoutEngine` (task 3), the four
built-in widgets plus the production `SysfsSource` (task 4), `panel.toml`
config parsing with validation and defaults (task 5), the `Backend` seam
(task 6) — the backend trait, the `Panel` driver, and the fully headless
`HeadlessBackend` (scripted events, recorded frames, text projection) — the
`--headless --print-layout` one-shot mode with the config → widgets builder
(task 7), a layout bench harness (task 8), and the **real Wayland
`layer-shell` backend** (task 11) with a **reusable shared-memory pool**
(task 13 — one `memfd`-backed `wl_shm` pool for the whole session, not a temp
file + pool per paint) are all implemented. The backend's
font/cell mapping, frame rasterizer, and pixel-staging core are pure and
golden-tested headlessly; the socket plumbing is type-checked against the real
protocol bindings and covered by `PENGYOS_IT=1`-gated live tests. Running the
panel itself needs a Wayland compositor (`layer-shell`-capable, e.g. a
wlroots-based one). The test suite is green with no
compositor/network/hardware (297 tests).
See [CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).
