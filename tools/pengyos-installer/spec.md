# PengyOS installer — install Core from the live stick to a target disk

## Summary
A from-scratch installer that takes a target block device and lays down PengyOS
Core on it (GPT: an EFI System Partition + a single ext4 root), copies the running
system's root filesystem, and wires the bootloader to the new disk.

## Purpose
SPEC: PengyOS must install onto real hardware. The live stick currently *boots*;
`pengyos-installer` puts the system **on** a machine.

## Deliverable
A Python CLI `pengyos-installer` (stdlib only) that computes an explicit, ordered
install plan for a target block device and can execute it, plus a pytest suite
that golden-tests the plan without touching any disk.

## Interface
```
pengyos-installer plan    --target /dev/sdX [--esp-size 512M] [--hostname H]
                          [--rootfs-src DIR|TAR] [--json]
pengyos-installer install --target /dev/sdX [ ...same... ] --yes
```
- `plan` prints the ordered, numbered step list; every step is a shell-quotable
  argv (deterministic, golden-testable). `--json` emits the machine-readable plan.
- `install` runs the plan through an **injectable command runner**
  (`runner(argv) -> {argv, returncode, stdout, stderr}`; the default shells out
  via a subprocess runner). It refuses to run without `--yes`, and re-checks the
  target every run. Execution semantics:
  * a step carrying `argv` is run through the runner; a non-zero exit on a
    *required* step is a hard error (the install aborts);
  * a step carrying an `optional:` note (the `efibootmgr` NVRAM entry) is run,
    but a non-zero exit is *tolerated* (recorded as a warning, the install
    continues) because the `--removable` fallback path already boots;
  * the two `blkid` steps' stdout is parsed into the PARTUUIDs (a missing /
    ambiguous PARTUUID is a hard error — no file is written);
  * the internal `configure` steps perform the file writes under the mounted
    work dir via the `render_*` functions;
  * on success the teardown `umount`s the ESP then the root and `sync`s; on
    *any* failure the executor unmounts whatever it mounted (best effort) before
    the error propagates, so a failed install never leaves a partition mounted.
- **Exit codes**: `0` ok, `1` error (e.g. a sanity refusal or a step failure),
  `2` usage error (argparse: unknown options, missing `--target`/subcommand),
  `3` not-yet-implemented (only used while this spec is still being built out;
  every command that is fully implemented returns 0/1/2 only).

### Plan model (implementation notes)
- The plan is a pure value (`InstallPlan`) built from a **`TargetProbe`**:
  a read-only snapshot of the target (path, `is_block_device`, `size_bytes`)
  plus the live `root_device` / `esp_device` paths. Injecting the probe is
  what keeps `plan` pure and golden-testable — tests supply fixtures. The
  default `collect_target_probe` only stats the path, reads `/sys`, and reads
  `/proc/mounts` (it never writes).
- **Live root/ESP discovery.** `collect_target_probe` populates the probe's
  `root_device` / `esp_device` by parsing the mount table: `parse_mounts`
  (a whitespace split + the kernel's octal unescape of embedded whitespace in
  the device/mountpoint fields) feeds `live_mount_devices`, which returns the
  block device of the `/` mount and the block device of the first FAT mount
  (fstype `vfat`/`msdos`/`fat`/`fat32`) at a well-known ESP mountpoint
  (`/boot/efi`, `/efi`; matched case-insensitively). A FAT *data* partition
  mounted elsewhere is **not** the ESP (a false ESP would spuriously refuse
  valid targets on that disk), and an overlay/chroot `/` has no block-device
  root. The mount table is read through an injectable `read_mounts` seam
  (default `/proc/mounts`), and an unreadable table degrades gracefully to
  `None`/`None` rather than crashing. The `is-live-*` refusal is therefore only
  as strong as the probe — but on a real system it now *actually* refuses
  installing over the running root/ESP.
- **Live-device refusal** means: refuse if the target equals a live device,
  or is the whole disk that contains one, or is a partition of the live disk
  (any of which would wipe the running system). The *comparison* is a
  device-identity (path / disk-containment) check — safe to run in `plan` —
  and the *live devices* it compares against are discovered from the mount
  table by `collect_target_probe` (see **Live root/ESP discovery** above).
- **Partition device naming** (`partition_device(target, index)`): the block
  device of partition `n` of a disk is `<tgt>n` **unless the device *name* ends
  in a digit**, in which case it is `<tgt>pn`. The kernel inserts the `p` so the
  partition number is not ambiguous with a higher-numbered namespace / device
  (`/dev/nvme0n1` → `/dev/nvme0n1p1`; `/dev/loop0` → `/dev/loop0p1`;
  `/dev/mmcblk0` → `/dev/mmcblk0p1`). A plain disk (`/dev/sda`) keeps the bare
  form (`/dev/sda1`). `partition_device` is the single source of every
  partition device name in the plan (format / copy / configure / bootloader)
  and in the install execution, so an NVMe or loop target is handled
  consistently in every `parted`/`mkfs.*`/`mount`/`blkid` argv. The whole disk
  itself (`parted -s <tgt>`, `--exclude=<tgt>`, `efibootmgr -d <tgt>`) is never
  renamed; only the per-partition references use the `p` form. `efibootmgr
  -p` takes the partition *number*, not the device path.
- The **ordered step list** contains all seven phases in order from the start.
  Steps whose command is produced by a later task (partition / format / copy /
  configure / bootloader) carry a stable `note` naming the pending task rather
  than any placeholder command; their exact argv is filled in by those tasks.
- `plan` prints the numbered step list; a failing sanity check is a hard
  refusal (exit 1, reasons on stderr, no plan emitted). `--json` emits the
  machine-readable plan with a fixed key order (deterministic byte-for-byte).

### Steps the plan must contain (in order)
1. **Sanity.** target is a block device (`/dev/…`, verified via `os.stat` + the
   `/sys/block` node); NOT the live root or ESP device (compare against the live
   mounts / their PARTUUIDs); size ≥ 4 GiB (documented minimum).
2. **Partition.** `parted -s <tgt> mklabel gpt`; ESP = `mkpart ESP fat32 1MiB <esp-size>`
   + `set 1 esp on`; root = `mkpart root ext4 <esp-end> 100%`. `<esp-end>` is the
   ESP end offset, i.e. the `--esp-size` value itself: the ESP occupies
   `1MiB..<esp-size>` (roughly `esp-size` minus 1 MiB), and root starts there.
   One step per `parted` invocation (four steps for this phase).
3. **Format.** `mkfs.vfat -F32 -n PENGYOS-ESP <esp-part>`; `mkfs.ext4 -L
   pengyos-root -F <root-part>` (one step each; the partition device names
   `<esp-part>` / `<root-part>` come from :func:`partition_device` — the
   plain `<tgt>1` / `<tgt>2` for most disks, the `p`-suffixed
   `<tgt>p1` / `<tgt>p2` for NVMe/loop-style names that end in a digit; see
   "Partition device naming" below).
4. **Copy.** Two steps, in order:
   * `mount <root-part> /mnt/pengyos-install` — the root partition at a fixed
     work dir (`WORK_DIR = /mnt/pengyos-install`; the ESP later mounts at
     `<work>/boot/efi`). `<root-part>` is the root partition device from
     :func:`partition_device` (`<tgt>2` or `<tgt>p2`).
   * **Source discovery is syntactic and pure** (plan must not touch the
     source): no `--rootfs-src` → the running `/` (kind *dir*); a
     `--rootfs-src` whose name ends in `.tar`, `.tar.gz`, `.tgz`, `.tar.xz`,
     `.txz`, `.tar.bz2`, or `.tbz2` → kind *tar*; anything else → kind *dir*.
     Install execution re-validates the source with `stat` before running
     the (destructive) copy step: a missing source, or one whose actual type
     contradicts the classification (a "tarball" that is a directory, or a
     directory-kind source that is not a directory), is a hard error and the
     install aborts *before any command touches the target*. The source
     `stat` is an injected inspector (`source_inspector` on the executor,
     defaulting to a real `os.stat`), so it is hermetic in tests.
   * DIR → `rsync -aHAX --numeric-ids --exclude=/proc --exclude=/sys
     --exclude=/dev --exclude=/run --exclude=/mnt --exclude=/media --exclude=/tmp
     --exclude=<tgt> <src> <work>`. Excludes are relative to the transfer
     root; the target device is excluded explicitly; `<src>` is `/` for the
     running root, or the explicit directory normalised to a single trailing
     slash (`/srv/rootfs/`) so its *contents* — not the directory itself — are
     copied into the work dir.
   * TAR → `tar -xpf <src> -C <work>` (the archive is expected to carry the
     rootfs at its top level).
5. **Configure.** Two `blkid` plan steps first — `blkid -s PARTUUID -o
   value <esp-part>` (ESP), then `<root-part>` (root); the stdout is a single
   token, and a missing/empty/ambiguous PARTUUID is a hard error (the install
   aborts; no file is written). `<esp-part>` / `<root-part>` are the
   :func:`partition_device` names (step 3). Then write, into the installed
   rootfs:
   * `/etc/fstab` — `PARTUUID=<root> / ext4 defaults 0 1` and
     `PARTUUID=<esp> /boot/efi vfat umask=0077 0 1` (root entry first;
     deterministic content, `render_fstab`).
   * `/etc/hostname` — the `--hostname` value (default: the live system's
     hostname, read at install time) on one line with a single trailing
     newline (`render_hostname`); empty is a hard error.
   * `/etc/pengyos/installed` — the install marker
     (`installed-by:`/`hostname:`/`target:` lines; `render_installed_marker`).
   * the fallback GRUB config (written once the ESP is mounted, i.e. in the
     bootloader phase) — `EFI/BOOT/grub.cfg` with `search --partuuid <root>`
     and `linux … root=PARTUUID=<root> …` (`render_grub_cfg`).
   The file-write steps are internal plan steps (`argv=None`, `internal:`
   note): their content embeds install-time values (the PARTUUIDs; the live
   hostname when `--hostname` is absent), so `plan` stays pure and the
   `install` execution task performs the writes via the `render_*`
   functions.
6. **Bootloader.** Three steps, in order:
   * mount the ESP at `<work>/boot/efi` — `mount <esp-part>
     /mnt/pengyos-install/boot/efi` (`<esp-part>` = the :func:`partition_device`
     name of the ESP, step 3);
   * `grub-install --target=x86_64-efi --efi-directory=<work>/boot/efi
     --boot-directory=<work>/boot --bootloader-id=PengyOS --removable`.
     `--removable` installs the loader in the firmware-independent
     `EFI/BOOT` location (`/EFI/BOOT/bootx64.efi`), so the system boots even
     with no UEFI NVRAM entry.
   * an **optional** `efibootmgr -c -d <tgt> -p 1 -L PengyOS
     -l /EFI/BOOT/bootx64.efi` entry. Because the removable fallback path
     already boots, this explicit NVRAM entry is best-effort: a failure to
     create it does **not** abort the install. The step therefore carries an
     `optional:` note (a real command whose failure is tolerated), not a
     `pending:` note.
   * the `EFI/BOOT/grub.cfg` fallback config is **not** a bootloader step —
     it is the fourth internal *configure*-phase write (content from
     `render_grub_cfg`, step 5), written at install time once the ESP is
     mounted by the step above.
7. **Teardown.** sync, unmount ESP then root, print a summary.

## Verification (tests are first-class)
- **Deterministic plan**: the same inputs give byte-identical output (golden).
  Assert the exact argv for a sample target — the parted/mkfs/rsync/grub-install
  lines and the `root=PARTUUID=` wiring.
- **PARTUUID discovery** from a fixture `blkid` output; the fstab + grub.cfg carry
  the right UUIDs; a missing PARTUUID is a hard error.
- **Refusals**: target == live device; not a block device; too small; `install`
  without `--yes`; unknown options.
- **`--json`** shape is asserted.
- Every external command goes through an injected runner — **no test touches a disk**.
- **Opt-in integration (loop device + boot)**: `tests/test_integration.py` skips
  unless `PENGYOS_IT=1`, and then skips again with an actionable message when
  prerequisites are missing (root; `parted`, `mkfs.vfat`, `mkfs.ext4`, `rsync`,
  `blkid`, `grub-install`, `efibootmgr`, `losetup`, `qemu-system-x86_64`,
  `pengyos-qemu-verify`; a rootfs source — `$PENGYOS_IT_ROOTFS_SRC`, else
  `~/build/pengyos/output/images/rootfs.tar`). It then: creates a 6 GiB sparse
  image, attaches it to a free loop device, runs the *unmocked* production
  install (`build_plan` + `execute_install` with the real `SubprocessRunner`),
  and boots the resulting image with
  `pengyos-qemu-verify boot <img> --uefi --expect login: --timeout N
  --json … --junit …` (OVMF → the installed `EFI/BOOT` loader → the installed
  GRUB → the login prompt; no `--kernel`, so the installed boot path — not a
  kernel-injected one — is what is proved). The IT never leaves a loop device
  attached (best-effort detach in `finally`).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.
The file artifacts are asserted directly; "a full green test suite" is enforced
two ways without a recursive self-run: (a) the loop's objective function *is*
exit-0 `pytest -q` (the whole suite, of which `test_github_ready` is part), and
(b) an in-test `pytest --collect-only` subprocess proves every test module still
imports/collects cleanly — collect-only does not execute test functions, so it
cannot recurse back into this test.

## Acceptance criteria
- `plan` is pure (no disk writes) and golden-stable.
- `install` is gated by `--yes`, runs only the planned argv, and tears down cleanly.
- `pytest -q` green; test_github_ready green.

## Non-goals
- No GUI, no network installer, no dual-boot/Windows management, no LUKS (a
  documented follow-up), no shrinking of existing partitions, no firmware/ESP-key
  management.
- It does **not** reimplement partitioning: it shells out to `parted`, `mkfs.*`,
  `rsync`, `grub-install`, `blkid`, `mount` — the alternatives the live stick
  already ships (see the `/usr/sbin` stubs).

## Constraints
- Runs as a **non-root user with no network at test time**. Mock external tools
  (parted, mkfs.*, rsync, grub-install, blkid, mount) via the injected runner; use
  committed fixtures. Real disk integration is opt-in via `PENGYOS_IT=1` (root +
  a loop device).
- **Loop-device partition naming (the IT).** A loop device's name ends in a
  digit, so the plan names its partitions via `partition_device` as
  `<target>p1` / `<target>p2` — the same `p`-suffixed rule as NVMe, and exactly
  the form the kernel creates for a loop device (`/dev/loopNp1` /
  `/dev/loopNp2`). The plan's argv is therefore already correct for a real
  `/dev/loopN`. The IT installs through a **symlink tree** in a temp dir —
  `<workdir>/loopN` → `/dev/loopN` (the plan's *target*),
  `<workdir>/loopNp1` → `/dev/loopNp1`, `<workdir>/loopNp2` → `/dev/loopNp2`
  (the partition symlink *names* match the plan's `p`-suffixed paths) — so the
  image and qemu-verify's report land under `tmp_path` and the target's
  basename is the real loop name, which is what makes the `/sys/block/<name>`
  check in `collect_target_probe` pass. It lives in
  `pengyos_installer/itenv.py`.
