# pengyos-hwmon — PengyOS Hardware Monitor

A hardware monitor: temperatures, fans, voltages, clocks, utilization, power and
storage health in one dense table, with **Value / Min / Max** columns.

- On the **Desktop** it is a COSMIC window, with a per-sensor **warning /
  critical** threshold editor.
- On the **Server** (and over ssh to any PengyOS machine) it is a **terminal
  view** of the same table: `pengyos-hwmon` in a shell, or on the console.

Both answer "what is this machine actually doing" from one data path. The collection
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

## Terminal view

The cut-down sibling for the Server image, ssh sessions and the Linux console
(`--features tui`, ratatui). The Sensors table with the same colours and limit
line per device, and the Hardware inventory. There is no Limits editor; rules
saved by the Desktop app (`~/.config/hwall/settings.json`) are read and still
colour the table, and the terminal view never writes that file.

| Key | Does |
|---|---|
| Up/Down, `j`/`k`, PgUp/PgDn, Home/End | scroll |
| `/` | filter by name (Enter keeps it, Esc clears it) |
| `p` or Space | pause / resume sampling |
| `r` | reset Min and Max |
| `+` / `-` | sample less / more often (0.5 to 10 s, 2 s to start) |
| `h` or Tab | sensors / hardware inventory |
| `?` | the key list |
| `q`, Esc, Ctrl-C | quit |

Drawn for the console first: 16 colours (a rule's `#rrggbb` becomes red or
yellow), no mouse, and it fits 80x25. Below 64 columns only Value is shown, and a
device's limit shortens before its name does.

Which front-end starts: the window when the binary has it and there is a display
(`WAYLAND_DISPLAY` or `DISPLAY`), the terminal view otherwise. `--tui` forces the
terminal view on a desktop session.

## Where the limits come from

1. `hwmon`'s own `*_crit`, then `*_max` — the kernel/firmware answer. hwall-core
   drops these from any sensor whose value lm-sensors supplied (a `compute`
   line could rescale it), and PengyOS ships lm-sensors, so for temperatures
   the app reads `tempN_crit` / `_emergency` / `_max` from sysfs itself, next to
   the sensor's `tempN_input`. A temperature limit outside 20..150 °C is a
   "not set" sentinel (NVMe uses 65261.85 °C) and is ignored.
2. Otherwise the CPU family table (`src/tjmax.rs`), keyed on `cpu_family` /
   `model_number` from `/proc/cpuinfo`: AMD families 23/25/26 → 95 °C, Intel
   family 6 → 100 °C, GPU → 95 °C, storage → 80 °C.
3. A user rule from the Limits pane always overrides both.

Only the first has any authority; the table exists because `k10temp` and AMD
iGPUs publish no `*_crit` at all, and a red number nobody can explain is worse
than no red number — hence the provenance in the device header. The header shows
the device's kernel limit if any sensor has one (the highest), otherwise the
highest table limit, so an NVMe drive shows its composite sensor's `crit`.

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
sets it to `0`, alongside the sensor drivers, because the Server's terminal view
shows the same rows. Without it the app still runs - those rows are simply absent.

`energy_uj` is a separate dead end: it is mode `0400 root`, so RAPL is only
readable through the perf counters.

## Build

```sh
# headless (no libcosmic, no display) - what the gate runs
cargo test
cargo test --features tui
cargo run -- --check

# the terminal view alone (the Server binary)
cargo run --features tui

# window + terminal view (the Desktop binary)
cargo build --release --features cosmic,tui
```

`scripts/build-cosmic.sh` builds the Desktop binary (`cosmic,tui`) into
`$PENGYOS_COSMIC_BUILD/install-gui` alongside the other COSMIC apps, so it shares
their libcosmic build, and the Server binary (`tui`, links only libc and
libgcc_s) into `$PENGYOS_COSMIC_BUILD/hwmon-tui`; `build-cosmic.sh
pengyos-hwmon-tui` builds just that one, which is what a Server-only release
runs. The Server's post-build installs it, and the Desktop's post-build (which
runs after it) replaces it with the Desktop binary. libcosmic is pinned to the
same rev the other apps use; `Cargo.lock` is committed and the build is `--locked`.

## Flags

| Flag | Meaning |
|---|---|
| `--check` | One collection pass printed as text: every sensor, grouped by device, with each device's limit and its source. Works in every build; for scripts, agents and rescue shells. |
| `--tui` | The terminal view, even on a desktop session. |
| `--hardware` | Start the terminal view on the Hardware inventory. |
| `--view sensors\|hardware\|limits` | Start the window on a given pane. |
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
