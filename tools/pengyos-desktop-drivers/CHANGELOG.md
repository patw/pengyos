# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Firmware-subset completeness** (`pengyos_desktop_drivers/lint.py`): the
  `firmware-subset` rule now has two halves. Besides checking that every firmware
  line is an upstream `linux-firmware` name, it now requires the list to be
  **complete** -- at least one entry under every curated prefix (the profile's
  `firmware_prefixes`). A firmware list missing the wifi stack (or any other
  curated prefix) is now rejected, which is the spec's "reject a firmware list
  missing the wifi stack" requirement. The two halves fold into the same rule id
  (`firmware-subset`), so a dirty list surfaces both problems at once; an empty
  curated-prefix set is vacuously complete. `lint.LintViolation` now also carries
  a `rules` dict (rule id -> passed) so callers/tests can tell which rule fired.
  `tests/test_lint.py` gains the completeness unit tests, the
  "reject missing wifi firmware" composition test, and a golden-hash pin that the
  completeness check does not change what the clean profile emits.
- Implemented `lint` (`pengyos_desktop_drivers/lint.py`): validates a kernel
  `linux.fragment` + `firmware.list` against four rules and raises
  `LintViolation` with **every** failing reason collected:
  - **symbol-exists** -- every enabled `CONFIG_*` symbol in the fragment exists
    in the vendored 2026.08 symbol list.
  - **no-vendor-accel** -- the fragment enables none of the forbidden
    vendor-proprietary / vendor-acceleration symbols (NVIDIA, the open KMS
    drivers' vendor-accel toggles such as `CONFIG_DRM_NOUVEAU` /
    `CONFIG_DRM_ACCEL` / the AMDGPU/RADEON accel knobs). Symbols absent from the
    2026.08 list are reported as documented guards.
  - **firmware-subset** -- every line in the firmware list is an upstream
    `linux-firmware` name.
  - **class-coverage** -- the fragment enables at least one display, one input,
    and one wifi driver (matched against `profile`'s declared classes).
  - `lint_fragment(fragment_path, firmware_path, as_json=False)` returns a
    `{ok, rules, errors, fragment, firmware}` dict and raises `PengyOSError` on
    a missing/unreadable file. The CLI now exits `0` for a clean profile and
    `2` for a dirty one, printing every rule's reason.
  - `tests/test_lint.py` pins the four-rule contract, the pure helpers, the
    emit->lint composition (the generic Desktop profile is lint-clean), and the
    CLI exit-code contract. `test_cli` was updated off the old "surfaces
    pending" expectation now that `lint` is implemented.
- Implemented `emit` (`pengyos_desktop_drivers/emit.py`): deterministically
  render the Desktop `linux.fragment` (Kconfig `CONFIG_*=y` lines, vendored
  generic-driver header, sorted + de-duplicated) and the curated `firmware.list`
  (upstream `linux-firmware` names under the profile's prefixes, sorted).
  - `emit_desktop_profile(out_dir)` writes both artifacts, creates `out_dir` if
    needed, and returns a report with per-artifact path / bytes-or-lines /
    SHA-256. `render_fragment` and `render_firmware_list` are pure, so they unit
    test in isolation.
  - Output is byte-for-byte deterministic; `tests/test_emit.py` pins the golden
    fragment + firmware SHA-256 so any profile or render drift fails loudly.
  - `test_cli` now exercises the real `emit` path (writes artifacts, exit 0,
    `--json` report) instead of the pending-feature error path.
- Modeled the Desktop driver profile as data
  (`pengyos_desktop_drivers/profile.py`, `data.py`):
  - The five driver classes -- `display`, `input`, `wifi`, `storage`, `net` --
    each with its kernel `CONFIG_*` symbols and the `linux-firmware` prefixes it
    curates. `display` => i915/amdgpu/radeon; `wifi` => intel/iwlwifi + Realtek
    `rtlwifi`/`rtl_bt`. Storage/net lean on virtio paravirtualization.
  - `virtio` ships no firmware (paravirtualized), so it contributes kernel
    symbols only, not a firmware prefix -- recorded in `profile.py`.
  - `tests/test_profile.py` grounds the model: every declared symbol exists in
    `symbols.2026.08.txt`, every prefix matches an upstream firmware name, and no
    vendor-acceleration/proprietary symbol is declared.
- Vendored the reference data in `pengyos_desktop_drivers/data/`:
  - `symbols.2026.08.txt` -- every `CONFIG_*` symbol in the pinned 2026.08
    kernel build (4571 symbols, extracted from the sibling Buildroot checkout's
    `linux-7.2.5/.config`).
  - `firmware.upstream.txt` -- every upstream `linux-firmware` name (4715
    loadable firmware paths, derived from the pinned `linux-firmware-20260810`
    release tarball; LICENSES/ and repo-doc entries excluded). Declared as
    package data via `tool.setuptools.package-data`.
  - `tests/test_data.py` -- commits/well-formedness/coverage checks for both
    fixtures.

### Fixed

- **`test_github_ready` was self-recursing and could never pass.**
  `test_suite_is_green` runs the *whole* suite via a `pytest` subprocess, and
  that suite includes `test_suite_is_green` itself -- so each run spawned
  another `pytest`, which spawned another, ... to a depth that blew the 300s
  timeout every time (the GitHub-ready contract asserted "a full green test
  suite" but the suite that proved it never exited). The fix is a
  `PENGYOS_SUITE_CHECK_RUNNING` sentinel: once the check has been handed off to
  a child, the child sees it and skips the re-check, breaking the cycle. The
  suite now runs green end-to-end in ~0.7s (`99 passed, 1 skipped`;
  `PENGYOS_IT=1` = `100 passed`). This is the `test_github_ready` unit of work
  — the contract existed but was not actually verifiable until now.

## [0.1.0] - 2026-09-25

### Added

- Scaffold the `pengyos-desktop-drivers` Python package:
  - `pyproject.toml` (setuptools backend, no runtime deps, `test` extra with
    pytest, `pengyos-desktop-drivers` console script).
  - Package `pengyos_desktop_drivers` with `emit`, `lint`, `cli`, `errors`, and
    `__main__` modules mapping to the units of work in `implementation.md`.
  - CLI: `pengyos-desktop-drivers emit --out DIR [--json]` and
    `pengyos-desktop-drivers lint --fragment FILE --firmware FILE [--json]`.
  - Test suite (`pytest`) with `test_github_ready`, CLI, package-import, and
    opt-in integration tests (`PENGYOS_IT=1`).
  - GitHub-ready docs: `README.md`, `LICENSE` (MIT, Copyright (c) 2026 Pat
    Wendorf), `CHANGELOG.md`, `.gitignore`.
