# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`gamemode` in `USER_GROUPS`.** GameMode's polkit rule grants its CPU-governor
  and GPU-clock helpers only to that group (allow_active=no otherwise), so GameMode
  did nothing for the desktop user. `tests/test_owner_groups.py` keeps
  `USER_GROUPS` equal to the image's `/usr/lib/pengyos/owner-groups`, which
  `merge-accounts` uses to add existing owners to a group appended later.

### Changed
- **`loglevel=4` with the serial console on real hardware.** Where GRUB finds a
  UART and the SMBIOS vendor is not QEMU, `render_grub_cfg` sets
  `pengyos_serial="loglevel=4 console=ttyS0,115200"`: at 115200 baud every
  kernel message costs ~6 ms, ~5 s of black screen per boot on an ASUS PRIME
  Z790-P with a COM header. QEMU keeps the full kernel log for testing.
- **The serial console is no longer fixed in the installed kernel command
  line.** `KERNEL_ARGS` drops `console=ttyS0,115200`; `render_grub_cfg` writes
  a GRUB probe (`serial --unit=0`) that sets `${pengyos_serial}` to it only when
  a real UART answers at COM1, and ends every kernel line with that variable.
  A phantom COM1 made a ThinkPad boot for minutes behind a black screen. Older
  kernel arguments passed in (the fixed console, or the variable read back
  from a grub.cfg) are normalised, so re-rendering is stable.
- A Desktop install starts at its **login screen**; automatic login is opt-in
  (`--autologin`, `ProvisionAnswers.autologin`), written to
  `/etc/pengyos/autologin.conf`. Replaces the `cosmic-session.service` `User=`
  drop-in (that unit is gone: greetd runs the session).
- Root slots are 8 GiB (`SLOT_SIZE`); the minimum disk is 18.25 GiB.

### Fixed
- root-b is wiped (`wipefs -a`) after partitioning: an old filesystem under
  it (the previous layout's data volume) showed up as a drive.

### Added

- **`provision` unit (`pengyos_install.provision`)** — the first-boot
  renderer: `render_provision(config, layout, active_slot) -> Plan`. Renders the
  first-boot steps that make a freshly written slot a usable host — `fstab`
  (active slot, ESP, data partitions), `hostname` (`/etc/hostname`), `users`
  (`useradd` + each user's `authorized_keys`), and `network` (`systemctl enable
  <manager>`) — from the validated `ProvisionConfig` (`install.toml`) + `Layout`
  (the fstab needs the partition paths) + the active slot. **Pure** (no I/O, no
  shell) so it is golden-testable. File content is pinned *exactly*: each file
  is written via `printf '%s' <content>` to **stdout** (a clean argv — no shell
  metacharacter) with the destination rendered as a shell redirect (`> '<path>'`),
  so the rendered script writes the file byte-for-byte. Wired the `provision-plan`
  CLI subcommand (loads `install.toml` + layout, renders a shell script or
  `--json`; `--layout` for the fstab paths, `--slot` for which root the fstab
  lists first, defaults to `a`).
- **`install` unit (`pengyos_install.install`)** — execute the golden plan
  through the injectable `Runner` seam. `install(plan, runner, *, confirm,
  layout, release)` (and the `InstallEngine` class) runs every step in order,
  **refuses without `--yes`** (`InstallRefused`), **re-checks the target each
  run** (the release/layout JSON round-trips and the release's `slot` is still a
  declared slot), and **hard-fails on the first non-ok step** (`InstallError`).
  It **tears down — unmounts every mount it opened (in reverse order) and
  `sync`s — on success *and* on failure** (a `finally` guarantees teardown even
  when a step raises); the atomic slot write (write the inactive slot, then
  commit the active slot) is executed as two ordered steps. Mounts are tracked
  via `InstallEngine.mount()` / `InstallResult.mounted` /
  `InstallResult.unmounted` / `InstallResult.unmounted_all`. Exposes
  `InstallEngine`, `InstallResult`, `InstallError`, `InstallRefused`, and the
  module-level `install()` entry point. Wired the `install` CLI subcommand
  (loads the release + layout, renders the plan, runs it, prints a one-line
  summary; `--yes` required, `--runner CMD` prefixes every step); the
  `provision-plan` handler still raises the not-yet-implemented sentinel.
- **`WrapperRunner`** (`pengyos_install.runner`) — a `Runner` that prefixes every
  argv with an external command, backing the `install` CLI's `--runner CMD`
  (e.g. `--runner echo` traces the commands that would run).
- **`plan` unit (`pengyos_install.plan`)** — the pure install-plan renderer:
  `(release, layout) -> Plan`, an ordered, deterministic list of
  shell-quotable-argv steps. Phases, in order: `partition` (GPT table +
  partitions via `parted`), `fs` (`mkfs.fat`/`ext4`/`btrfs`), `slot` (atomic
  rootfs write — write the *inactive* slot, then commit the *active* slot),
  `uki` (drop the UKI at the ESP's `EFI/BOOT/bootx64.efi`), `loader`
  (`install-bootctl`), `data` (btrfs subvolumes), and `provision` (a
  `pengyos-install provision-plan` CLI step). The release's `slot` is
  authoritative for the active slot and is cross-checked against the declared
  slots (`Layout.assert_has_slot`). Exposes `render_plan` + a `PlanBuilder`.
  Wired the `plan` CLI subcommand (renders a shell script or `--json`); the
  install/provision handlers still raise the not-yet-implemented sentinel.
- Validated data models (`pengyos_install.models`): `Release` (signed release
  manifest — rootfs/UKI artifacts + signature record, `is_signed`), `Layout`
  (target partitioning: slots, ESP, data; unique index + table checks,
  `assert_has_slot` cross-check), `Step` / `Plan` (the install plan as an
  ordered, shell-quotable-argv list; deterministic JSON + shell-script render;
  a `Step.redirect` shell-script-only field lets a file-content writer emit its
  content to stdout and have the shell redirect it — never a literal `>` argv),
  and `ProvisionConfig` (`install.toml`: hostname, timezone, network, users with
  SSH keys; preserves extra keys). Every input raises a typed
  `ValidationError` subclass (`ReleaseError` / `LayoutError` / `ConfigError`).
- Scaffold the `pengyos_install` package (src layout, stdlib-only): injectable
  `Runner` / `FakeRunner` / `RunResult` seam, argparse CLI with `plan`,
  `install`, and `provision-plan` subcommands plus `plan` / `install` /
  `provision-plan` console scripts, and `tomllib` backfill for Python 3.10.
- GitHub-ready contract: README, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"),
  CHANGELOG, `.gitignore`, and a `test_github_ready` suite. The contract is now
  verified for *correctness*, not just presence: each declared console script is
  imported and confirmed callable (the real "pip install yields the CLIs"
  contract), the package `__version__` is pinned to match `[project] version` in
  `pyproject.toml`, and every flag the CLI actually exposes is documented in the
  README.
- Opt-in integration tests behind `PENGYOS_IT=1`.

### Fixed

- **Refuse an unverified release.** The install engine now refuses to install a
  release whose signature has not been verified (`release.is_signed` is
  `False`) — even with `--yes`. This was a documented promise in the `models`
  `Signature` docstring ("the engine refuses to install a release whose
  signature is not verified") that the `install` unit never enforced. The
  refusal is a security gate that fires *before* any step runs (no I/O), raises
  `InstallRefused` (a `CommandError` → CLI exit 2), and is independent of the
  `--yes` confirmation gate. Covered by `test_install.py` (install + CLI) and
  the `test_github_ready` suite stays green.
