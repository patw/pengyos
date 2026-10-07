# pengyos-installer

Install **PengyOS Core** from the live stick onto a target disk.

`pengyos-installer` is a from-scratch installer (Python, **stdlib only**) that
takes a target block device, lays down PengyOS Core on it — GPT layout with an
EFI System Partition plus a single ext4 root — copies the running system's
root filesystem, and wires GRUB to the new disk.

The live stick currently *boots*; `pengyos-installer` puts the system **on** a
machine.

## Status

`plan` is implemented: it sanity-checks the target and computes the explicit,
ordered install plan (deterministic, golden-testable, `--json` for the
machine-readable form) **without touching any disk**. The partition, format,
copy, and configure steps carry their full `parted` / `mkfs.*` / `rsync` /
`tar` / `blkid` argv, and the configure phase pins the exact content of
`/etc/fstab`, `/etc/hostname`, `/etc/pengyos/installed`, and the fallback
`EFI/BOOT/grub.cfg` (written at install time from the `blkid` output), and the
bootloader argv (mount ESP + `grub-install --removable` + an optional
`efibootmgr` entry) is complete. See [spec.md](spec.md).

`install` runs that plan for real: it gates on `--yes`, re-checks the target,
runs every command through an **injectable command runner** (default a
subprocess runner), performs the internal file writes, and tears down
(`umount` ESP then root, `sync`) on both success and failure. A required
step's failure aborts; the `optional:` `efibootmgr` entry's failure is
tolerated (the `--removable` fallback path already boots).

## How it works

`plan` computes an **explicit, ordered install plan** for the target device.
Every step is a shell-quotable argv — deterministic and golden-testable. The
planned steps, in order:

1. **Sanity.** Target is a block device (`/dev/…`, verified via `os.stat` and
   the `/sys/block` node); it is *not* the live root or ESP device; its size
   is at least the documented 4 GiB minimum.
2. **Partition.** `parted -s <tgt> mklabel gpt`; ESP = `mkpart ESP fat32
   1MiB <esp-size>` + `set 1 esp on`; root = `mkpart root ext4 <esp-end>
   100%` (one step per `parted` invocation; `<esp-end>` is the `--esp-size`
   value itself).
3. **Format.** `mkfs.vfat -F32 -n PENGYOS-ESP <tgt>1`; `mkfs.ext4 -L
   pengyos-root -F <tgt>2` (one step each).
4. **Copy.** Mount the root partition at a work dir; `rsync -aHAX
   --numeric-ids` the source rootfs, excluding the virtual and live dirs
   (`/proc /sys /dev /run /mnt /media /tmp` and the target device). Default
   source is the running `/`; `--rootfs-src DIR|TAR` overrides (TAR →
   `tar -xpf`).
5. **Configure.** `blkid -s PARTUUID -o value` for `<tgt>1` and `<tgt>2`
   (a missing PARTUUID is a hard error); then write `/etc/fstab`
   (`PARTUUID=…` lines), `/etc/hostname`, and the install marker
   `/etc/pengyos/installed`, plus the fallback `EFI/BOOT/grub.cfg` content
   (written once the ESP is mounted, in step 6).
6. **Bootloader.** Mount the ESP at `<work>/boot/efi`; `grub-install
   --target=x86_64-efi --efi-directory=<work>/boot/efi
   --boot-directory=<work>/boot --bootloader-id=PengyOS --removable`; write
   `<work>/boot/efi/EFI/BOOT/grub.cfg` with `root=PARTUUID=<root-partuuid>`;
   optional `efibootmgr` entry.
7. **Teardown.** `sync`, unmount ESP then root, print a summary.

`install` runs exactly that plan through an **injectable command runner**:
every external command (`parted`, `mkfs.*`, `rsync`, `grub-install`, `blkid`,
`mount`, …) goes through the runner, so the whole suite can run against a
recording fake — **no test ever touches a disk**.

## Usage

```console
$ pengyos-installer plan    --target /dev/sdb [--esp-size 512M]
                            [--hostname H] [--rootfs-src DIR|TAR] [--json]
$ pengyos-installer install --target /dev/sdb [ ...same... ] --yes
```

- `plan` prints the ordered, numbered step list; `--json` emits the
  machine-readable plan. `plan` is *pure* — it never writes to any disk.
- `install` refuses to run without `--yes` and re-checks the target every run.

The installed system boots from the removable (fallback) EFI boot path:
`EFI/BOOT/grub.cfg` with `root=PARTUUID=<root-partuuid>`, which is what
dual-boot-friendly firmware picks up without any firmware NVRAM entry.

## Safety

- The installer **refuses** to target the live root or ESP device, anything
  that is not a block device, and disks below the 4 GiB minimum.
- `plan` is read-only by construction.
- `install` requires explicit `--yes` and executes only the planned argv.
- Non-goals (documented): no LUKS, no dual-boot/Windows management, no
  shrinking of existing partitions, no firmware/ESP-key management.

## Development

Python ≥ 3.11, standard library only (no third-party runtime dependencies).

```console
$ python3 -m venv .venv && . .venv/bin/activate
$ pip install -e '.[test]'
$ pytest -q
```

The test suite is fully hermetic: no network, no root, no real hardware —
every external command goes through the injected runner.

### Integration test (opt-in: `PENGYOS_IT=1`, root)

`tests/test_integration.py` performs the end-to-end proof on a **loop
device**: it attaches a 6 GiB sparse image, runs the *unmocked* production
install (`build_plan` + `execute_install` with the real `SubprocessRunner`),
and then boots the resulting disk with the sibling tool
`pengyos-qemu-verify` (OVMF → the installed `EFI/BOOT` loader → the
installed GRUB → `login:`). The IT is skipped unless `PENGYOS_IT=1`, and
skips again — with an actionable message — until every prerequisite is
present: root, `parted`, `mkfs.vfat`, `mkfs.ext4`, `rsync`, `blkid`,
`grub-install`, `efibootmgr`, `losetup`, `qemu-system-x86_64`,
`pengyos-qemu-verify` (plus OVMF firmware for the boot), and a rootfs
source (`$PENGYOS_IT_ROOTFS_SRC`, else
`~/build/pengyos/output/images/rootfs.tar`).

```console
$ PENGYOS_IT=1 sudo pytest -q          # the whole suite + the loop-device IT
$ PENGYOS_IT_TIMEOUT=600 PENGYOS_IT=1 sudo pytest -q tests/test_integration.py
```

Because the kernel names a loop device's partitions `…p1`/`…p2` (the
`p`-suffixed form used for digit-ending device names), the IT installs
through a temp-dir **symlink tree** (`<workdir>/loopN` → `/dev/loopN`,
`<workdir>/loopN1` → `/dev/loopNp1`, `<workdir>/loopN2` → `/dev/loopNp2`)
instead of targeting `/dev/loopN` directly — see `spec.md` and
`pengyos_installer/itenv.py`.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
