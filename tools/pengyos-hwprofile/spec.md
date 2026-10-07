# PengyOS hwprofile — hardware detection → declarative profile

## Summary
Detect hardware and emit a declarative, version-controllable per-machine profile; diff and validate.

## Purpose
SPEC §1.5: the OS morphs around the hardware, declaratively.

## Deliverable
A Python CLI `pengyos-hwprofile` producing `profile.toml` from /sys, lspci, lsusb, dmidecode-style fixtures; `diff` between profiles.

### Source layout
Collectors read a *source directory* laid out exactly like the live kernel
pseudo-filesystems (`/sys` + `/proc`), so tests drive them with a committed
fixture tree and production points the same code at the real tree:
`proc/cpuinfo`, `proc/sys/kernel/{hostname,osrelease}`, `class/dmi/id/*`,
`bus/pci/devices/<bus>/{vendor,device,class,driver}`, `class/net/<iface>/*`,
`class/nvme/nvme<n>/*`, `class/block/<disk>/*`, `bus/usb/devices/<dev>/*`.
The CLI reads exactly these paths from live `/sys`.

## Interface
- pengyos-hwprofile detect [--from DIR] --out profile.toml
- pengyos-hwprofile diff A B

## Verification (tests are first-class)
- fixture tree → expected profile.
- deterministic output.
- diff detects added/removed/changed devices.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- No root needed; fixtures drive tests.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Configuring hardware

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
