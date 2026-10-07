# PengyOS desktop-net — NetworkManager + wifi + the COSMIC network applet defaults

## Summary
Wire Desktop networking so a person can get online (including wifi) with no CLI: NetworkManager enabled, the wpa_supplicant + wifi-firmware stack referenced, and the COSMIC default panel layout including cosmic-applet-network plus the cosmic-settings network defaults — emitted as build-consumed config, with a linter.

## Purpose
US-DSK-1/2 + the live installer: joining wifi is step one of any install. COSMIC configures network through NetworkManager (cosmic-applet-network + cosmic-settings), but the default panel is reported NOT to include the network applet — so without this there is no GUI path to Wi-Fi. Make the network defaults declarative so the GUI path is guaranteed.

## Deliverable
A Python package `pengyos_desktop_net`: `emit` (NetworkManager + wpa_supplicant configuration, the cosmic panel default layout with `cosmic-applet-network`, and the cosmic-settings network defaults), and `lint` (NM enabled as the network service; the wifi stack + firmware are referenced; the emitted panel default includes the network applet; no conflicting networkd ownership).

## Interface
- pengyos-desktop-net emit --out DIR [--json]
- pengyos-desktop-net lint --overlay DIR [--json]

## Verification (tests are first-class)
- emit is golden/deterministic; the panel default contains the network applet.
- lint fails when NM is disabled or the applet is absent from the panel default.
- The Desktop buildroot-external board selects the real NetworkManager symbol
  (`BR2_PACKAGE_NETWORK_MANAGER`, a stock 2026.08 recipe — **not** the
  non-existent `BR2_PACKAGE_NETWORKMANAGER`) in
  `configs/pengyos_desktop_defconfig`, so the board has a network
  service; `tests/test_board_network.py` asserts this (and that the symbol
  resolves in a real Buildroot 2026.08 under `PENGYOS_IT=1`). `cosmic-applet-network`
  is the GUI path and ships from the cosmic-build release (dropped in by the
  board's `post-build.sh`), wired via the emitted panel default.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A lint-clean network profile that guarantees a GUI path to Wi-Fi.
- `pytest -q` green; test_github_ready green.

## Non-goals
- The wifi kernel driver/firmware packaging (pengyos-desktop-drivers)
- Real radio hardware

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
