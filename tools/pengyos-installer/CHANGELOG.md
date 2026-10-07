# Changelog

All notable changes to **pengyos-installer** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/); the
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **NVMe/loop-style partition naming** (`partition_device`): the plan and the
  install execution now derive every partition device name from a single
  helper, `plan.partition_device(target, index)`. A target whose device name
  ends in a digit (`/dev/nvme0n1`, `/dev/loopN`, `/dev/mmcblk0`, `/dev/md0`)
  uses the kernel's `p`-suffixed form (`/dev/nvme0n1p1` / `p2`) instead of the
  ambiguous bare form (`/dev/nvme0n11`); a plain disk (`/dev/sdb`) is unchanged
  (`/dev/sdb1` / `sdb2`). The `parted` / `mkfs.*` / `mount` / `blkid` argv in
  the partition, format, copy, configure, and bootloader phases, and the
  `blkid` PARTUUID capture in `execute_install`, all use the same name, so an
  NVMe or loop target is handled consistently everywhere. The whole disk
  (`parted -s`, `--exclude`, `efibootmgr -d`) is never renamed, and
  `efibootmgr -p` still takes the partition *number*.
  - The loop-device IT symlink tree now names its partition symlinks with the
    `p` form (`<workdir>/loopNp1|p2` → `/dev/loopNp1|p2`) to match the plan.
  - New `tests/test_partition_naming.py` golden-tests the rule (parametric
    plain vs. `p` names, no-ambiguous-name check, per-phase NVMe argv, and an
    NVMe install run through the fake runner).

### Added
- **Live root/ESP discovery populates the probe's sanity re-check**:
  `collect_target_probe` now parses the mount table to find the block device
  backing the running `/` and the EFI System Partition, and populates the
  `TargetProbe`'s `root_device` / `esp_device` — so `check_target` (and thus
  the `plan`/`install` sanity re-check) actually refuses installing over the
  running system instead of only for a hand-injected probe. New pure,
  injectable helpers in `plan`: `parse_mounts` (whitespace split + the
  kernel's octal unescape of embedded whitespace in the device/mountpoint
  fields; blank/malformed lines are skipped) and `live_mount_devices` (returns
  the `/` mount's device and the first FAT mount — fstype `vfat`/`msdos`/`fat`
  /`fat32` — at a well-known ESP mountpoint `/boot/efi` or `/efi`,
  case-insensitive). A FAT *data* partition elsewhere is not the ESP; an
  overlay/chroot `/` has no block-device root. The table is read through an
  injectable `read_mounts` seam (default `/proc/mounts`, resolved at call time
  so it is a monkeypatchable module seam); an unreadable table degrades to
  `None`/`None` rather than crashing. No `cli.py` change: both `plan` and
  `install` build their probe via the default `collect_target_probe`, so the
  re-check picks the live devices up automatically. New `tests/test_live_mounts.py`
  (31 tests) golden-tests the parser, the discovery (incl. the FAT-data and
  overlay non-ESP cases), the collector wiring/degradation, and the end-to-end
  CLI refusal/acceptance — all hermetic (no test reads the real `/proc/mounts`).

- **`--rootfs-src` validation at install time**: `execute_install` now
  re-validates the copy source before running the (destructive) copy step, via
  a new injectable inspector (`default_source_inspector`, one `os.stat`; passed
  as `source_inspector=`) and `validate_rootfs_src(source, kind)`. A missing
  source (`rootfs-src-missing`) or a source whose actual type contradicts the
  plan-time classification — a `*.tar`-named path that is a directory, or a
  directory-kind source that is a regular file (`rootfs-src-type-mismatch`) —
  is a hard error that aborts the install *before any command touches the
  target* (no step runs, nothing is mounted). The running-root default (`/`)
  is validated too. New tests cover the inspector, `validate_rootfs_src`, the
  `execute_install` abort paths (including a hermetic CLI refusal for a real
  non-existent `--rootfs-src`), and the injected-inspector seam.
- **Opt-in loop-device integration (install → `pengyos-qemu-verify` boot)**: new
  module `pengyos_installer/itenv.py` + `tests/test_integration.py` +
  `tests/test_itenv.py`. The IT (skipped unless `PENGYOS_IT=1`, and skipped
  again with an actionable message until the prerequisites — root; `parted`,
  `mkfs.vfat`, `mkfs.ext4`, `rsync`, `blkid`, `grub-install`, `efibootmgr`,
  `losetup`, `qemu-system-x86_64`, `pengyos-qemu-verify`; and a rootfs source
  via `$PENGYOS_IT_ROOTFS_SRC` or `~/build/pengyos/output/images/rootfs.tar` —
  are all present) creates a 6 GiB sparse image, attaches it to a free loop
  device, runs the *unmocked* production install (`build_plan` +
  `execute_install` with the real `SubprocessRunner`, work dir the production
  `/mnt/pengyos-install`), and boots the resulting image with
  `pengyos-qemu-verify boot <img> --uefi --expect login: --timeout N --json …
  --junit …` — OVMF → the installed `EFI/BOOT` loader → the installed GRUB →
  the login prompt (no `--kernel`, so the *installed* boot path is what is
  proved). The loop device is always detached (best-effort, in `finally`).
  - `itenv.py` API (pure where possible, injected fakes where not):
    `it_enabled()`, `missing_requirements(euid, which)` (stable-order, actionable
    messages), `find_rootfs_source(environ, home, exists)` (explicit env var →
    buildroot output → `None`; the host's running `/` is deliberately *not* a
    candidate), `make_sparse_image()`, `losetup_attach_argv()` /
    `losetup_detach_argv()` (golden-stable), `attach_loop()` / `detach_loop()`
    (the actors, via the runner), and `qemu_verify_boot_argv()` (golden-stable;
    validated against the real `pengyos-qemu-verify` CLI with `--dry-run`).
  - **The loop-naming tree** (spec.md "Constraints"): the kernel creates a
    loop device's partitions as `/dev/loopNp1|p2` (the `p` form, since the
    name ends in a digit — the NVMe rule). The IT installs through a temp-dir
    symlink tree so the image and qemu-verify's report land under `tmp_path`
    and the target's basename is the real loop name (so the `/sys/block`
    sanity check passes): `<workdir>/loopN` → `/dev/loopN` plus
    `<workdir>/loopNp1|p2` → `/dev/loopNp1|p2` (the symlink *names* match the
    `p`-suffixed partition paths the plan now emits via
    `partition_device`; see the "Changed" note above).
  - `tests/test_itenv.py` (hermetic): the `PENGYOS_IT` gate (only the value
    `"1"` enables), the argv goldens (losetup attach/detach, qemu-verify boot
    defaults/options/multiple-`--expect`, never `--kernel`), `missing_requirements`
    (root-first, stable binary order, injectable `which`/`euid`),
    `find_rootfs_source` (precedence, missing-candidate fall-through, empty env
    value, no-`$HOME` no-crash), `make_sparse_image` (apparent size with no
    allocation; directory → `RuntimeError`), `attach_loop` (symlink tree layout,
    bad-`losetup`-output refusals, multi-line/whitespace stdout), `detach_loop`
    (symlink cleanup incl. dangling, error vs. best-effort), and the constants.
  - `tests/test_integration.py`: the gated IT plus two always-on tests (the
    gate is off by default; the requirement messages are actionable).
  - README.md documents the IT (prerequisites + how to run it); spec.md's
    Verification and Constraints record the IT design and the loop-naming
    workaround. Closes the "Opt-in (PENGYOS_IT=1, root) integration" item.
- **GitHub-ready contract test (`test_github_ready`)**: new
  `tests/test_github_ready.py` asserts the repo is publishable at all times —
  `README.md` (names the tool + both subcommands, links the docs), `LICENSE`
  (MIT, exact `Copyright (c) 2026 Pat Wendorf` line, real MIT body),
  `CHANGELOG.md` (`[Unreleased]` section, Keep-a-Changelog shaped), `.gitignore`
  (build/test/editor noise), and `pyproject.toml` (MIT license text + classifier,
  `readme = "README.md"`). `test_test_suite_collectable` runs
  `pytest --collect-only` in a subprocess to prove every test module still
  imports/collects cleanly — a non-recursive guard (collect-only does not
  execute test functions) that a broken test module cannot slip into a
  GitHub-ready commit. Closes the spec.md "GitHub-ready contract" item.
- **`install` execution (the injectable-runner layer)**: `pengyos-installer
  install --target … --yes` now actually runs the plan. New module
  `pengyos_installer/install.py`:
  - `CommandRunner` — an injectable `runner(argv) -> CommandResult` callable;
    `SubprocessRunner` is the real one, tests use a recording fake (no test
    touches a disk). `CommandResult` carries `argv` / `returncode` / `stdout` /
    `stderr` (+ `.ok`).
  - `execute_install(plan, runner, *, confirm, hostname_provider, work_root,
    write_file)` walks the ordered plan: a required step's non-zero exit is a
    hard `InstallError` (abort); an `optional:` step's failure is *tolerated*
    (recorded as a warning — the `--removable` fallback path already boots);
    the two `blkid` steps' stdout is parsed into the PARTUUIDs (a missing /
    ambiguous PARTUUID is a hard error, no file written); the internal
    `configure` steps perform the file writes under the (injectable) work root
    via the `render_*` functions. Returns an `InstallResult` (executed argvs,
    files written, mounts/unmounts, warnings, summary).
  - **`--yes` gate and sanity re-check**: refuses (exit 1, before any command
    runs) when `--yes` is absent or the target failed sanity.
  - **mount/unmount + teardown**: on success it `umount`s the ESP then the root
    and `sync`s; on *any* failure it unmounts whatever it mounted (best effort)
    before the error propagates, so a failed install never leaves a partition
    mounted. `install` error codes: `not-confirmed`, `sanity-refused`,
    `step-failed`, `umount-failed`, `missing-partuuid`, `unknown-internal-step`.
  - CLI wiring in `cli.py`: the `install` handler (`run_install`) builds the
    plan, re-checks the target, runs it through the runner, and prints the
    summary on success / the error on failure. Runner and work root are
    module-level seams (`_install_runner`, `INSTALL_WORK_ROOT`) so CLI tests
    stay hermetic.
  - **Follow-up surfaced**: populating the *live* root/ESP devices into the
    probe is now a separate `- [ ]` item in `implementation.md` (spec.md
    refined accordingly) — until it lands, `collect_target_probe` leaves
    `root_device` / `esp_device` `None`, so the `is-live-*` refusal only fires
    for a probe that already carries them (as in the tests).
- `tests/test_install.py`: the injectable-runner suite — the exact 13-command
  execution order + teardown, PARTUUID capture into fstab/grub.cfg (root not
  swapped with ESP), the config-file contents on disk, the `--yes` / sanity
  refusals (nothing runs), a step failure aborting + tearing down only what was
  mounted, the optional `efibootmgr` failure being tolerated, a missing /
  ambiguous PARTUUID being a hard error that writes no file, unmount-failure
  handling on the success and failure paths, the internal-step dispatch errors,
  a custom `write_file`, the `SubprocessRunner` (a harmless `echo` + a missing
  binary), and the `install` CLI handler (refusal without `--yes`, a hermetic
  success path through the injected runner, a step-failure → exit 1).
  `test_plan.py` / `test_scaffold.py` updated: `install` without `--yes` is now
  an exit-1 refusal (not the exit-3 placeholder), and the placeholder tests
  were replaced accordingly.

- **Bootloader step (mount ESP + grub-install + optional efibootmgr)**: the
  bootloader phase is no longer a `pending` note — the plan now carries real
  command lines (the last phase to be filled in):
  - `mount <tgt>1 /mnt/pengyos-install/boot/efi` (the ESP at `<work>/boot/efi`).
  - `grub-install --target=x86_64-efi
    --efi-directory=/mnt/pengyos-install/boot/efi
    --boot-directory=/mnt/pengyos-install/boot --bootloader-id=PengyOS
    --removable`. `--removable` installs the loader in the firmware-independent
    `EFI/BOOT` location, so the system boots even without a UEFI NVRAM entry.
  - an **optional** `efibootmgr -c -d <tgt> -p 1 -L PengyOS
    -l /EFI/BOOT/bootx64.efi` entry, marked with an `optional:` note: because
    the removable fallback path already boots, a failure to create the NVRAM
    entry is best-effort and must not abort the install. This is the one step
    that is a *real* command yet carries a note — it records that the failure
    is tolerated, distinct from a `pending:` placeholder.
  - `bootloader_steps()` and the stable constants `GRUB_TARGET`,
    `BOOTLOADER_ID`, `ESP_EFI_DIR`, `GRUB_BOOT_DIR`, `EFI_LOADER_PATH` in
    `pengyos_installer/plan.py`.
  - The plan now has 19 steps (1 sanity + 4 partition + 2 format + 2 copy +
    6 configure + 3 bootloader + 1 teardown); **no step carries a `pending`
    note any more** — every external-tool step has its argv.
- `tests/test_plan.py`: bootloader tests — stable constants, direct
  `bootloader_steps()` golden argv (mount / grub-install / efibootmgr), the
  exact `grub-install` flag set, the `efibootmgr` `-l /EFI/BOOT/bootx64.efi`
  loader, plan-level bootloader shape (3 steps, order, `optional:` note, no
  `pending` anywhere), a parametric NVMe-path check, determinism, updated
  golden `to_dict` (19 steps), render (13 argv `$` lines + the optional note),
  and a `plan --json` CLI test. Also updated the phase-count and
  no-longer-pending assertions.

- **Configure step (blkid + file content)**: the configure phase is no longer
  a `pending` note — the plan carries real command lines and the file writes
  are pinned:
  - Two `blkid` steps: `blkid -s PARTUUID -o value <tgt>1` (ESP) and
    `<tgt>2` (root). `parse_partuuid()` turns the (fixture) stdout into the
    PARTUUID; empty or multi-token output is a hard error
    (`ConfigError`, code `missing-partuuid`) — "a missing PARTUUID is a hard
    error" (spec).
  - Deterministic renderers for the files the phase writes: `render_fstab()`
    (`PARTUUID=<root> / ext4 defaults 0 1` + `PARTUUID=<esp> /boot/efi vfat
    umask=0077 0 1`, root first), `render_hostname()` (one line + newline;
    empty → `invalid-hostname`), `render_installed_marker()`
    (`installed-by:`/`hostname:`/`target:` lines), and `render_grub_cfg()`
    (the `EFI/BOOT/grub.cfg` fallback boot config with
    `search --partuuid <root>` + `linux … root=PARTUUID=<root> ro quiet`).
  - The four file writes are internal plan steps (`argv=None`, `internal:`
    note — the content embeds install-time values: the PARTUUIDs and, when
    `--hostname` is absent, the live hostname); the `install` execution task
    performs them via the `render_*` functions. Stable constants:
    `ETC_FSTAB`, `ETC_HOSTNAME`, `INSTALL_MARKER`, `ESP_MOUNTPOINT`,
    `FALLBACK_GRUB_CFG`, `GRUB_TIMEOUT`, `ERR_MISSING_PARTUUID`,
    `ERR_INVALID_HOSTNAME`; new API `partuuid_steps()` /
    `configure_file_steps()` / `parse_partuuid()` / `render_*()` in
    `pengyos_installer/plan.py`.
  - The plan now has 17 steps (1 sanity + 4 partition + 2 format + 2 copy +
    6 configure + bootloader + teardown); only the bootloader step still
    carries a `pending` note.
- `tests/test_plan.py`: configure tests — direct `partuuid_steps()` /
  `configure_file_steps()` goldens, plan-level configure phase shape
  (argv vs. internal writes, no `pending` left), `parse_partuuid` fixture
  parsing (incl. blank/multi-token hard errors), golden content for
  fstab/hostname/marker/grub.cfg with hard-error cases, hostname default vs.
  `--hostname` plan difference, updated golden `to_dict` (17 steps), render
  (blkid `$` lines + internal notes), and a `plan --json` CLI test.

- **Copy argv + source discovery**: the copy phase is no longer a `pending`
  note — the plan carries real command lines:
  - `mount <tgt>2 /mnt/pengyos-install` (new stable constant `WORK_DIR`; the
    ESP later mounts at `<work>/boot/efi`).
  - DIR source (default: the running `/`, or `--rootfs-src DIR`) →
    `rsync -aHAX --numeric-ids --exclude=/proc --exclude=/sys --exclude=/dev
    --exclude=/run --exclude=/mnt --exclude=/media --exclude=/tmp
    --exclude=<tgt> <src> <work>`; excludes are relative to the transfer
    root, and `<src>` is `/` or the directory normalised to a single trailing
    slash so its *contents* are copied.
  - TAR source (`--rootfs-src` named `*.tar`, `*.tar.gz`, `*.tgz`,
    `*.tar.xz`, `*.txz`, `*.tar.bz2`, `*.tbz2`) → `tar -xpf <src> -C <work>`.
  - `classify_rootfs_src()` / `copy_steps()` / `rsync_source_arg()` and the
    stable constants `WORK_DIR`, `VIRTUAL_DIR_EXCLUDES`, `TAR_EXTENSIONS` in
    `pengyos_installer/plan.py`. Source discovery is pure and syntactic so
    `plan` never touches the source; install-time `stat` validation of the
    source is a new follow-up task in `implementation.md`.
  - The plan now has 12 steps (1 sanity + 4 partition + 2 format + 2 copy +
    configure + bootloader + teardown); configure/bootloader still carry
    `pending` notes until their tasks land.
- `tests/test_plan.py`: copy tests — classification table (incl. negative
  suffixes and case-insensitivity), trailing-slash normalisation, direct
  `copy_steps()` golden argv (mount / rsync / tar, excludes order), plan-level
  golden argv for both source kinds, tar-plan determinism, updated golden
  `to_dict` (12 steps), render, and a `plan --rootfs-src … --json` CLI test.
- **Partition + format argv**: the plan now carries the real command lines for
  the partition and format phases (previously `pending` notes):
  - `parted -s <tgt> mklabel gpt`; `parted -s <tgt> mkpart ESP fat32 1MiB
    <esp-size>`; `parted -s <tgt> set 1 esp on`; `parted -s <tgt> mkpart root
    ext4 <esp-size> 100%` (one step per invocation; `<esp-end>` is the
    `--esp-size` value itself).
  - `mkfs.vfat -F32 -n PENGYOS-ESP <tgt>1`; `mkfs.ext4 -L pengyos-root -F
    <tgt>2` (stable volume labels).
  - `partition_steps()` / `format_steps()` builders in
    `pengyos_installer/plan.py` with the stable constants `ESP_PART_NAME`,
    `ROOT_PART_NAME`, `ESP_START_OFFSET`, `ESP_FS_LABEL`, `ROOT_FS_LABEL`.
  - The plan now has 11 steps (1 sanity + 4 partition + 2 format + copy +
    configure + bootloader + teardown); copy/configure/bootloader still carry
    `pending` notes until their tasks land.
- `tests/test_plan.py`: golden argv tests for every partition/format step
  (exact argv + esp-size parametric + label constants + direct builder
  tests), plus updated golden `to_dict` / render / `--json` expectations.
- `plan` subcommand (previously a not-implemented placeholder):
  - **Plan model** in `pengyos_installer/plan.py`: `TargetProbe`, `Step`,
    `InstallPlan`, `SanityResult` dataclasses with deterministic
    `to_dict()` / `to_json()` (fixed key order) and a `render_plan_text()`
    numbered-list renderer.
  - **Target sanity checks** (`check_target`): the target must be a block
    device, must **not** be the live root or ESP device (or the disk that
    contains them), and must be at least the 4 GiB minimum. All applicable
    failures are reported together with stable error codes
    (`not-block-device`, `is-live-root`, `is-live-esp`, `too-small`).
  - The `plan` handler computes the full **ordered step list** (all seven
    phases in order) and prints it; a failing sanity check is a hard refusal
    (exit 1, reasons on stderr, no plan emitted). `--json` emits the
    machine-readable plan.
  - `plan` is **pure**: device facts come from an injected `TargetProbe`; the
    default `collect_target_probe` only stats the path / reads `/sys`.
- `tests/test_plan.py`: golden + behaviour tests for the sanity checks, the
  ordered step list, byte-identical determinism, a golden `to_dict` for a
  sample target, the `--json` shape, and the CLI wiring (success / refusal /
  usage). Fully hermetic — no test touches a disk.

## [0.1.0] — 2026-09-17

### Added
- Repository scaffold: `pengyos_installer/` package (`__init__`, `__main__`,
  `cli`) with the `pengyos-installer` CLI skeleton — `plan` and `install`
  subcommands with the full spec option surface (`--target`, `--esp-size`,
  `--hostname`, `--rootfs-src`, `--json`, `--yes`), `ExitCode` convention
  (0 ok / 1 error / 2 usage / 3 not-yet-implemented), and `--version`.
- `pyproject.toml` (stdlib only, console script `pengyos-installer`,
  `pytest` test extra, `pythonpath` configured for the test suite).
- `README.md`, `LICENSE` (MIT), `.gitignore`, and smoke tests covering
  package/CLI import, version ↔ pyproject agreement, the console-script
  target, parser/option wiring, unknown-option and missing-target refusals,
  the not-yet-implemented exit code, and `python -m pengyos_installer`.

[Unreleased]: https://github.com/patw/pengyos/tree/main/tools/pengyos-installer
[0.1.0]: https://github.com/patw/pengyos/tree/main/tools/pengyos-installer
