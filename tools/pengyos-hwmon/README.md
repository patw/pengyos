# pengyos-hwmon — PengyOS Hardware Monitor

A hardware monitor for the COSMIC desktop: temperatures, fans, voltages, clocks,
utilization, power and storage health in one dense table, with **Value / Min /
Max** columns and a per-sensor **warning / critical** threshold editor.

It is the GUI half of "what is this machine actually doing". The collection
library is [HWall](https://github.com/pulpul-s/HWall) (MIT), which reads sysfs
and the standard helper tools and never needs root: `hwmon`, `thermal`,
`powercap`, `/proc/stat`, `nvme`, `smartctl`, `dmidecode`, `ethtool`,
`bluetoothctl`, `sensors`.

## Why it exists

PengyOS Desktop ships COSMIC and **no GTK4 runtime**, so a GTK hardware monitor
cannot run there natively. This is a native libcosmic front-end to the same
collectors instead: `hwall-core` collects, `hwall-app` owns the presentation
policy, and this crate is only the view.

## Views

| Pane | What it does |
|---|---|
| **Sensors** | Every device, grouped, with Value / Min / Max since start. Values turn **amber within 15 °C** and **red within 5 °C** of the sensor's limit. The limit and where it came from are shown in the device header (`limit 95 °C · Zen3/Zen3+/Zen4`). |
| **Hardware** | Inventory: processors, memory, graphics, storage, network, Bluetooth, PCI, USB, batteries, firmware. |
| **Limits** | Per-sensor warning and critical thresholds (the hwall-app alert rules). Prefilled at limit−15 / limit−5. Saved to `~/.config/hwall/settings.json`; a configured rule always wins over the built-in limit. |

`Pause` freezes sampling without losing the table; `Reset stats` clears min/max.

## Where the limits come from

1. `hwmon`'s own `*_crit`, then `*_max` — the kernel/firmware answer.
2. Otherwise the CPU family table (`src/tjmax.rs`), keyed on `cpu_family` /
   `model_number` from `/proc/cpuinfo`: AMD families 23/25/26 → 95 °C, Intel
   family 6 → 100 °C, GPU → 95 °C, storage → 80 °C.
3. A user rule from the Limits pane always overrides both.

Only the first has any authority; the table exists because `k10temp` and AMD
iGPUs publish no `*_crit` at all, and a red number nobody can explain is worse
than no red number — hence the provenance in the device header.

## Board sensors need one line of configuration

The Super-I/O chips that carry desktop fan/voltage/board-temperature sensors
(`nct6775`, `it87`, `f71882fg`, `w83627ehf`) have no `MODULE_DEVICE_TABLE` and
probe I/O ports inside `module_init`, so **udev can never auto-load them**. The
image ships `/etc/modules-load.d/60-pengyos-sensors.conf` for exactly that
reason. Without it a desktop shows CPU/GPU/NVMe only, which is what PengyOS did
until 26.11.9. Verified on three fleet desktops: NCT6798D (ASUS Z790, ASUS
X570) and NCT6797D (MSI B450) each add 34–43 sensors (fans, rails, board temps,
PWM) once the module is loaded.

## CPU power and effective clocks

Those two rows need unprivileged perf counters: the kernel default
(`perf_event_paranoid=2`) only allows a process to profile itself, while these
read system-wide (`perf_event_open` with `pid=-1`). The image ships
`/usr/lib/sysctl.d/60-pengyos-perf-events.conf` in **core** (every edition) that
sets it to `0`, alongside the sensor drivers, because the Server TUI wants the
same rows. Without it the app still runs - those rows are simply absent.

`energy_uj` is a separate dead end: it is mode `0400 root`, so RAPL is only
readable through the perf counters.

## Build

```sh
# headless (no libcosmic, no display) - what the gate runs
cargo test
cargo run -- --check

# the GUI (what the image builds, via scripts/build-cosmic.sh)
cargo build --release --features cosmic
```

The image builds it into `$PENGYOS_COSMIC_BUILD/install-gui` alongside the other
COSMIC apps, so it shares their libcosmic build. libcosmic is pinned to the same
rev they use; `Cargo.lock` is committed and the build is `--locked`.

## Flags

| Flag | Meaning |
|---|---|
| `--check` | One collection pass, printed as text. Works with or without the GUI feature; this is what a Server TUI will build on. |
| `--view sensors\|hardware\|limits` | Start on a given pane. |
| `--stress-scroll`, `--stress-repaint` | Developer flags: drive synthetic scrolling/repainting so the rendering budget can be measured (`/proc/<pid>/stat`). |

## Performance

Measured on beholaptop (Ryzen 7 6800H, 700×620 logical window, 120% scale),
one core:

| Scenario | software raster (tiny-skia) | wgpu |
|---|---|---|
| idle | 5.7% | 3.1% |
| 60 Hz repaint, no scrolling | 24.3% | 14.7% |
| fast scrolling | **57.7%** | **0.8%** |

`wgpu` is therefore not optional: without it, scrolling a 106-row list burned
most of a core. The list is also virtualised (fixed per-kind row heights, a
cumulative offset table, only visible rows built), which bounds the cost on a
server board with hundreds of sensors.

## Licence

MIT. The collectors are HWall's (MIT, © pulpul-s) — see `THIRD-PARTY.md`.
