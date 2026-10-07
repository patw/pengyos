# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- The top-`implementation.md` item ("Operator boot (rung e)") is now framed
  explicitly as a **STOP state**: its loop-doable substance (the runbook, the
  rung-b kconfig-resolve proof, and the boot-readiness gate) is complete and
  tested, and the only remaining work is the human boot itself — operator work,
  forbidden inside the ralph loop. The item line records this boundary (records
  it, never marks it done), and a hermetic guard
  (`tests/test_board_runge.py::test_runge_boundary_is_operator_work`) asserts the
  runbook still labels rung-e operator work / forbidden-in-loop and points at the
  real board + defconfig — so the operator hand-off stays honest even though the
  boot itself is not done in the loop. Suite: 89 passed, 2 skipped.
- A hermetic **rung-e runbook guard** (`tests/test_board_runge.py`, 8 tests). The
  top unchecked `implementation.md` item ("Confirm the Desktop board boots to a
  managed Wi-Fi link end to end") is ladder rung **e** (full image build + boot)
  — operator work, forbidden inside the ralph loop; its *substance* (rung b, the
  kconfig resolve) is a guaranteed suite test. The only thing a human needs to
  boot is the runbook in `docs/ralph-verification-ladder.md` rung (e), and
  nothing asserted it was present and correct. This module guards it: the
  runbook exists, labels rung-e operator work / forbidden-in-loop, targets the
  real `qemu-x86_64-desktop` board + `pengyos_desktop_bringup_defconfig`, builds
  detached to a log on disk (`~/tmp`), boots qemu and confirms NM manages the
  Wi-Fi link, and uses the pinned `~/build/pengyos-2608` toolchain. Suite: 88
  passed, 2 skipped. The operator-boot item stays unchecked (operator work).
- A hermetic **boot-readiness gate** for the rung-e operator boot
  (`tests/test_board_integrity.py`, 12 tests). The top unchecked
  `implementation.md` item — *"Confirm the Desktop board boots to a managed
  Wi-Fi link end to end"* — is ladder rung **e** (full image build + boot),
  operator work explicitly forbidden inside the ralph loop; its *substance*
  (rung b, the kconfig resolve) is already a guaranteed, verified-clean suite
  test. This item decomposes the remaining operator-boot work into a concrete,
  loop-testable pre-boot check: it asserts the Desktop board's
  network-relevant build files are present and well-formed — `post-build.sh`
  drops the `cosmic-applets` bundle that provides `cosmic-applet-network`;
  `post-image.sh` assembles the bootable GPT image via `genimage`;
  `genimage.cfg` has a bootable EFI System Partition + ext4 root (root found by
  PARTUUID); the overlay ships the NetworkManager config + `wpa_supplicant.conf`
  + `wifi-firmware.txt` + the COSMIC panel/network defaults, pins
  `cosmic-applet-network`, and has no `networkd`; the bringup defconfig selects
  the real `BR2_PACKAGE_NETWORK_MANAGER` + nmcli + `BR2_PACKAGE_WPA_SUPPLICANT`.
  This de-risks the human's rung-e boot (it starts from a known-good board). The
  boot itself (rung e) remains operator work and stays unchecked.
  `pytest -q` green (80 tests). See `docs/ralph-verification-ladder.md` rung (e).

### Changed
- Iteration 29 consolidated the two **redundant** rung-e operator-boot
  `implementation.md` items into one. The first item ("Confirm the Desktop board
  boots to a managed Wi-Fi link end to end") and the second ("Operator boot
  (ladder rung e)") were the same operator-boot step stated twice; both are
  ladder rung **e** (full image build + boot, operator work, forbidden inside
  the ralph loop). Collapsing them leaves a single honest unchecked item and
  ends the degenerate re-confirm cycle. No spec/behavior change; the rung-b
  kconfig resolve (the item's substance) is unchanged and remains a guaranteed
  verified-clean suite test. `pytest -q` green (80 passed, 2 skipped).
- Consolidated the `AGENTS.md` STOP-state record. Iterations 11–18 were a
  degenerate re-confirm cycle (each re-ran the same kconfig resolve and re-wrote
  "lane complete"); iterations 19, 21–24 were near-identical STOP-state
  re-affirmations. They are collapsed into one concise STOP-state section that
  states the lane is functionally complete, that the unchecked
  `implementation.md` item is ladder rung **e** (operator work), that its
  substance (rung b, the kconfig resolve) is a guaranteed verified-clean suite
  test, and that the loop has no more loop-testable work. This breaks the
  self-similar cycle instead of adding a 7th re-confirmation pass. All genuine
  learnings are preserved (the pre-boot gate, the operator hand-off runbook).
  No spec/behavior change; `pytest -q` green (80 passed, 2 skipped).

## [0.5.0] - 2026-09-25

### Changed
- The network-wiring proof (implementation.md rung-b subtask, "Confirm the
  Desktop board boots to a managed Wi-Fi link end to end") is now a
  **guaranteed** part of the suite, not just an opt-in integration test. The
  `PENGYOS_IT=1` integration run had proven rung (b) — that the bringup
  defconfig's `BR2_PACKAGE_NETWORK_MANAGER=y` (+ `BR2_PACKAGE_NETWORK_MANAGER_CLI`
  nmcli, `BR2_PACKAGE_WPA_SUPPLICANT=y` + `_NL80211`) actually lands in a real
  Buildroot 2026.08 kconfig `.config` — but only when that flag was set. The
  suite now resolves the bringup defconfig against any present Buildroot
  checkout + `make` + `cc` on **every** run, and skips only when none are
  available (so it stays hermetic: no network/root/display/hardware, just a
  local kconfig resolve against a pinned checkout). `tests/test_board_network.py`
  gains `test_bringup_defconfig_network_wiring_resolves_kconfig` plus the
  `_buildroot_checkout` / `_kconfig_skip_reason` / `_resolve_defconfig`
  helpers; the pinned `~/build/pengyos-2608` checkout resolves with zero
  kconfig warnings. `pytest -q` green (67 tests). The end-to-end boot (ladder
  rung e) remains operator work and is left unchecked.
- Rung (b) — the network-wiring correctness proof — was verified clean on the
  loop box: `make … pengyos_desktop_bringup_defconfig` against the pinned
  `~/build/pengyos-2608/buildroot` resolves with **zero** kconfig warnings and
  `BR2_PACKAGE_NETWORK_MANAGER=y`, `BR2_PACKAGE_NETWORK_MANAGER_CLI=y`,
  `BR2_PACKAGE_WPA_SUPPLICANT=y` and `BR2_PACKAGE_WPA_SUPPLICANT_NL80211=y`
  all land in the resolved `.config`. This closes the open question in the top
  unchecked implementation.md item ("Confirm the Desktop board boots … end to
  end"): its substance is rung (b) (the wiring is correct), which is now both a
  guaranteed suite test and a verified-clean resolve on the box. The item's
  *title* (rung e — a full image build + boot to a joined Wi-Fi link) remains
  operator work and stays unchecked.
- The rung-(b) proof now also asserts `BR2_PACKAGE_WPA_SUPPLICANT_NL80211=y`
  resolves. NM selects it automatically once `WPA_SUPPLICANT` is on, but the
  board only comes online over Wi-Fi if the driver interface actually lands — so
  `test_bringup_defconfig_network_wiring_resolves_kconfig` now verifies it,
  closing the gap between "wpa_supplicant selected" and "Wi-Fi can come online".
  Test-only change (no version bump).
- The "decide" implementation.md item ("Decide whether `cosmic-applet-network`
  needs a from-source Buildroot recipe or ships from the cosmic-build release")
  is now locked in with a regression guard. The decision was already made and
  evidenced (the applet ships from the cosmic-build release, dropped in by the
  board's `post-build.sh`, NOT a Buildroot recipe — it is a Rust/Vala applet
  with heavy COSMIC deps that are NOT stock 2026.08 packages). `tests/test_board_network.py`
  gains three hermetic tests: the bringup defconfig selects no
  `BR2_PACKAGE_COSMIC_APPLET_NETWORK` symbol (no Buildroot recipe), the Desktop
  `post-build.sh` drops the `cosmic-applets` bundle (which provides the network
  applet) from the cosmic-build release, and the emitted panel default pins
  `cosmic-applet-network` (the GUI path to Wi-Fi is wired). `pytest -q` green
  (68 tests).
- Iteration 11 re-confirmed the lane is complete: the two unchecked
  implementation.md lines are both ladder rung **e** (full image build + boot to
  a joined Wi-Fi link) — operator work, forbidden inside the loop. Its substance
  (rung **b**, the kconfig resolve) is a guaranteed suite test. A fresh
  `make … pengyos_desktop_bringup_defconfig` against `~/build/pengyos-2608/buildroot`
  resolved clean (rc=0, `BR2_PACKAGE_NETWORK_MANAGER=y` + `_CLI=y` +
  `BR2_PACKAGE_WPA_SUPPLICANT=y` + `_NL80211=y` all land, zero kconfig symbol
  warnings — the only stderr line is kconfig's own `-Wdiscarded-qualifiers` from
  `confdata.c`). The emit→lint round-trip is clean and the board overlay is
  byte-identical to the emit output. Both rung-e lines stay unchecked (operator
  work); the blocker is the operator-work boundary itself, not a missing tool.
  No version bump (lockstep untouched — `0.5.0`), no new `- [ ]` line.

### Added
- `docs/ralph-verification-ladder.md` — the cheapest-first verification ladder
  for this lane (rung a `check-package` → b kconfig resolve → c source hash → d
  build in the existing tree → e full image build + boot). Rung (b) is the loop's
  proof that the network wiring is correct and is a guaranteed suite test; rung (e)
  is operator work. This doc was referenced by `spec.md`, `implementation.md`, and
  `AGENTS.md` but did not exist; authoring it also resolved that dangling
  reference and pinned the rung-(e) operator-boot runbook so the top unchecked
  implementation.md item ("Confirm the Desktop board boots …") is actionable for a
  human. The item stays unchecked (operator work); a crisp follow-up operator-boot
  line was appended to `implementation.md`.

## [0.4.0] - 2026-09-25

### Added
- Board network wiring (implementation.md item 7, "Add a NetworkManager
  Buildroot recipe … so the Desktop board can come online over Wi-Fi"). The
  premise was **partly wrong**: `NetworkManager` **is** a stock Buildroot
  2026.08 package — the real symbol is `BR2_PACKAGE_NETWORK_MANAGER` (with
  underscore, `package/network-manager/`), and `BR2_PACKAGE_NETWORKMANAGER`
  (no underscore) does not exist. The Desktop bringup defconfig now selects
  `BR2_PACKAGE_NETWORK_MANAGER=y` + `BR2_PACKAGE_NETWORK_MANAGER_CLI=y` (nmcli),
  pulling dbus / libglib2 / util-linux and the keyfile plugin the overlay
  expects; `BR2_PACKAGE_WPA_SUPPLICANT=y` (the Wi-Fi backend NM drives) stays.
  `cosmic-applet-network` is the GUI path and ships from the cosmic-build
  release (dropped in by the board's `post-build.sh`), wired via the emitted
  panel default — no Buildroot recipe. `tests/test_board_network.py` (10 tests)
  asserts the bringup defconfig selects the real symbol (not the hallucinated
  no-underscore one), keeps wpa_supplicant, and has no stale "NM is not a
  Buildroot package" note; the `PENGYOS_IT=1` integration run resolves the
  bringup defconfig against a real Buildroot 2026.08 checkout and asserts
  `BR2_PACKAGE_NETWORK_MANAGER=y` actually resolves. `pytest -q` green (64
  hermetic + 2 integration under `PENGYOS_IT=1`).

## [0.3.0] - 2026-09-25

### Added
- Board integration (implementation.md item 6, "Wire the emitted overlay into
  the board"): the PengyOS Desktop buildroot-external board now **consumes** the
  emitted overlay. `board/qemu-x86_64-desktop/rootfs-overlay/` ships the emitted
  overlay byte-for-byte — NetworkManager config + `conf.d/10-pengyos.conf`
  drop-in, `/etc/wpa_supplicant.conf`, `wifi-firmware.txt`, the COSMIC panel
  default under `/etc/skel/.config/cosmic/panel.toml` pinning
  `cosmic-applet-network`, and `cosmic-settings` network defaults under
  `/etc/skel/.config/cosmic-settings/network.toml`. The conflicting
  `systemd-networkd` ethernet stub (`etc/systemd/network/10-pengyos-ethernet.network`) was removed so NetworkManager is the sole network service, and the
  bringup defconfig selects `BR2_PACKAGE_WPA_SUPPLICANT=y` (configures cleanly,
  pulls `BR2_PACKAGE_WPA_SUPPLICANT_NL80211`) while consuming the overlay via
  `BR2_ROOTFS_OVERLAY`. `tests/test_board_overlay.py` (8 tests) asserts the emit
  output and the board overlay are byte-identical, that the board overlay passes
  `lint`, and that no `systemd-networkd` claims a link. `pytest -q` green (56
  tests). `check-package` (Buildroot's recipe linter) is clean on the new files.

## [0.2.0] - 2026-09-25

### Added
- Golden-config grounding (implementation.md item 4, "Ground with fixtures"):
  a committed golden fixture at `tests/fixtures/golden/golden.json` (the exact
  `emit_json()` document) plus `tests/test_golden.py`. `emit` is now verified to
  reproduce the fixture **byte-for-byte** and to write each file to disk with
  exactly the golden content — so the overlay is a safe, diffable, pinned
  Buildroot overlay. The golden panel default's `cosmic-applet-network` pin and
  the golden TOML artifacts' validity are locked into the fixture. The
  mutation suite in `test_lint` already fires `network-applet-in-panel` /
  `nm-enabled` when the applet is removed or NM is disabled.
- GitHub-ready master gate (`test_github_ready`, implementation.md item 5):
  `tests/test_github_ready.py` asserts the spec's GitHub-ready contract as a
  single master gate plus granular checks — the four docs (`README.md`,
  `LICENSE`, `CHANGELOG.md`, `.gitignore`) are present and non-empty, LICENSE is
  MIT carrying "Copyright (c) 2026 Pat Wendorf", pyproject declares the same
  MIT license, CHANGELOG is Keep-a-Changelog with its newest version equal to
  the pyproject version, and every relative README link resolves. The suite is
  green (`python3 -m pytest -q`, 48 tests), so the repo is GitHub-ready.

## [0.1.0] - 2026-09-25

### Added
- Project scaffold (implementation.md item 1): `pyproject.toml` (PEP 621, MIT,
  `setuptools` build backend, stdlib-only at test time), the
  `pengyos_desktop_net/` package, and the pytest harness.
- `.gitignore`, `README.md`, `CHANGELOG.md`, and `LICENSE` (MIT, Copyright
  (c) 2026 Pat Wendorf) so the repo is GitHub-ready from the first commit.
- `conftest.py` (adds the repo root to `sys.path` so a bare
  `python3 -m pytest -q` works without `pip install -e .` — the verifier runs
  exactly that, cwd = `tools/pengyos-desktop-net`).
- `pengyos_desktop_net.profile` — the network profile as data (the manager, the
  Wi-Fi backend + firmware reference, and the COSMIC panel applet set that must
  include `cosmic-applet-network`).
- `pengyos_desktop_net.emit` — renders the profile into a deterministic,
  golden build-consumed overlay: NetworkManager config + drop-in,
  `wpa_supplicant.conf`, a Wi-Fi firmware manifest, the COSMIC default panel
  (listing `cosmic-applet-network`), and `cosmic-settings` network defaults.
- `pengyos_desktop_net.lint` — checks an overlay for the spec's invariants:
  NetworkManager is the chosen network service, the Wi-Fi stack + firmware are
  referenced, the panel default includes the network applet, and no conflicting
  `systemd-networkd` ownership exists.
- `pengyos_desktop_net.cli` — the `emit` / `lint` CLI (console script +
  `python -m pengyos_desktop_net`).
- Tests: `test_scaffold`, `test_emit` (golden artifacts + determinism),
  `test_lint` (a mutation suite — each rule fires when its invariant is broken),
  `test_cli`, and `test_github_ready`.
