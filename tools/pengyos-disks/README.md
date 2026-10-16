# pengyos-disks — "Disks"

The Desktop's disk utility, over UDisks2: see every drive and how it is laid
out, partition it, format it, mount it, and do the USB-stick jobs (format the
whole stick, write an installer image, eject). The GNOME Disks shape, for
COSMIC. Before this the Desktop had UDisks2 and every mkfs tool but nothing
graphical to drive them: COSMIC Files mounts and ejects, and has no Format
action at all.

- **Every drive**: internal disks, external and USB disks, sticks, memory cards,
  down the side. For the one you pick: its partition table (GPT or MBR), and a
  bar of its partitions and free space in proportion. Click a partition or a
  stretch of free space for its details and what you can do with it.
- **Partitions**: *Create partition…* in free space (a size like `20 GB` or all
  of it; ext4, btrfs, XFS, FAT32, exFAT, NTFS, swap, or left unformatted; a name);
  *Format…* a partition in place; *Grow…* a partition and its filesystem into the
  free space after it; *Delete…*; *Mount* / *Unmount*; *Turn swap on/off*.
- **The whole drive**: *New partition table…* (GPT, or MBR for old computers:
  erases the drive); *Write image…*: an `.img`, `.iso`, or a compressed `.img.xz`
  / `.zst` / `.gz` (unpacked on the fly), with real progress and a Stop button.
  This is how to make a PengyOS installer stick from a running PengyOS: download
  the `.img.xz`, *Open with Disks*.
- **Removable drives** also get *Format…* (one partition, FAT32 by default, exFAT,
  NTFS or ext4, the table chosen for you) and *Eject* (unmount and power off).

**This system cannot be broken from the window.** A drive the system has
something mounted from (outside `/run/media`: the disk it boots from, the live
stick a session runs from, a data disk) or swapping on is locked for the
whole-drive actions. A partition is locked when the system has it mounted, it is
active swap, it is write-protected, or it is one of the running PengyOS's own
partitions: `PENGYOS-ESP`, `pengyos-root-a`, `pengyos-root-b`, `pengyos-data` (and
the installer stick's `PENGYOS-PAYLOAD`), even when it is not mounted, since the
inactive root slot never is. The same names on a *different* disk (an old install)
are not locked. Locked things are shown with the reason, their buttons greyed.
Sizes and free space are checked before anything is written, and every
destructive step names what it will erase and needs a red button pressed. See
[`spec.md`](spec.md) for the contract.

## How it works

Everything goes through UDisks2 on the system bus; the app never runs as root
and adds no polkit rules. With UDisks' shipped policy the active local user
formats, partitions, mounts and ejects removable drives without a password; an
internal disk (`*-system` actions) and writing an image (`open-device`) ask for an
administrator's password in COSMIC's polkit dialog (for wheel members, their own
password — `50-pengyos-admin.rules`).

`80-pengyos-udisks-hide.rules` sets `UDISKS_IGNORE` on PengyOS's own partitions.
That only hints file managers to hide them; UDisks still lists them, so Disks
shows them (locked) while COSMIC Files keeps them out of its sidebar.

- `src/lib.rs` — std only: the drive model from a `GetManagedObjects` snapshot,
  the layout (partitions and 1 MiB-aligned free space, GPT's backup table and MBR's
  2 TiB limit allowed for), what is locked and why, filesystem name and size
  rules, the UDisks call plan for each action, the image copy loop, user-facing
  error text.
- `src/udisks.rs` (feature `udisks`, zbus) — reads the snapshot, runs the plan.
- `src/app.rs` (feature `cosmic`) — the window.

## Use

```console
pengyos-disks                      # the window
pengyos-disks ~/Downloads/x.img.xz # the window, with that image ready to write
pengyos-disks --list               # every drive, its partitions and free space, and what is locked
```

## Tested

0.1.0 (the stick flows) in QEMU on the Desktop image (26.10.24), live session and
installed: FAT32 / exFAT / NTFS / ext4 with names; the Core `.img.xz` and a raw
`.img` written byte-identical (`cmp`), the written stick boots; a 1.1 GB image
refused for a 512 MB stick; the password prompt, Cancel, Stop mid-write; Eject;
the live stick locked.

0.2.0: `cargo test` (37 tests, no display and no D-Bus); `--list` and the window
against a real host (two NVMe disks, every in-use partition locked); and in QEMU
on the Desktop image, a second virtio disk taken through the partition work. See
the CHANGELOG for exactly what that pass covered.

## Build

```console
cargo test                                   # the decision logic: no display, no D-Bus
cargo run --features udisks -- --list        # against the host's UDisks
CARGO_TARGET_DIR=$PENGYOS_COSMIC_BUILD/install-gui cargo build --release --features cosmic
```

`scripts/build-cosmic.sh pengyos-disks` does the last one; the Desktop
post-build installs `$PENGYOS_COSMIC_BUILD/install-gui/release/pengyos-disks`,
and the overlay carries `io.catbee.pengyos.Disks.desktop`.
