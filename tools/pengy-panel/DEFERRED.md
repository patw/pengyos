# DEFERRED — pengy-panel (task retired 2026-09-16)

The operator retired this task before the two items below were completed. They are
carried here as **post-MVP follow-ups** and were **NOT implemented**.

## 1. Record the live-compositor idle RSS (measurement)

Run the release panel under a real wlroots layer-shell compositor and sample `VmRSS`, to
add the Wayland socket/client overhead to the headless ~2.5 MiB figure in ARCHITECTURE.md.

- The measurement mechanism is already built and unit-tested (`pengy-panel
  --measure-rss-live`, plus `PENGYOS_IT=1`-gated e2e tests). Only the measurement was
  blocked.
- **Unblocked 2026-09-16:** `sway` 1.11 is now installed on behoserver and runs headless
  via `WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1` (no GPU/seat needed).
- How:
  ```
  cargo build --release
  WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 sway &
  WAYLAND_DISPLAY=wayland-0 ./target/release/pengy-panel --measure-rss-live --max-mib 100000
  ```
- Original blocker: the reference host's gnome-shell/Mutter accepts the layer surface but
  never sends a sizing configure, so the panel exits 69. No other wlroots compositor existed.

## 2. Independent per-output layout (code)

Lay each monitor's bar out against its *own* width. The task-15 backend replicates the
primary output's frame to every surface — exact for equal-size monitors (the common case),
wrong for mixed-size monitors. Needs a per-output `Frame`; the `Panel`/`Backend` seam is
currently single-width, so it touches the driver, not just the backend.

## Why retired

Operator direction (2026-09-16): converge on an end-to-end core/server/desktop MVP with
human-in-the-loop verification before extending desktop artistry / pro-distro polish.
pengy-panel is already functional and tested (244 tests green); these two items are
polish/measurement, not MVP-critical.
