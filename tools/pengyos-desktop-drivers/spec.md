# PengyOS desktop-drivers — the generic display/input/wifi driver + firmware profile

## Summary
The generic (no vendor acceleration) driver set for the Desktop live + installed image: a kernel fragment for KMS/input/wifi/virtio, a curated linux-firmware subset (i915/amdgpu/radeon/iwlwifi/rtl/virtio + the standard intel/realtek bits), and generic Mesa — enough to show a desktop and get network, with NO proprietary or vendor-optimized acceleration; plus a linter over the fragment and firmware list.

## Purpose
'Using the generic drivers, no AMD/nvidia optimized at this point — just enough to configure network/wifi and run the installer.' Pin that minimal profile as declarative, tested data instead of a hand-edit that drifts.

## Deliverable
A Python package `pengyos_desktop_drivers`: `emit` (the Desktop `linux.fragment` and a `firmware.list` subset), and `lint` (every fragment symbol exists in the vendored 2026.08 list; NO vendor-proprietary/accel symbols; the firmware list is a subset of upstream linux-firmware names; at least one display + one input + one wifi driver present).

## Interface
- pengyos-desktop-drivers emit --out DIR [--json]
- pengyos-desktop-drivers lint --fragment FILE --firmware FILE [--json]

## Verification (tests are first-class)
- emit is golden/deterministic; lint passes a generic profile.
- lint rejects a vendor-accel symbol, a made-up fragment symbol, and a firmware list missing the wifi stack.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A lint-clean generic driver/firmware profile with class coverage and no vendor acceleration.
- `pytest -q` green; test_github_ready green.

## Non-goals
- GPU acceleration, NVIDIA, suspend/audio/Bluetooth on real hardware (SPEC 8)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
