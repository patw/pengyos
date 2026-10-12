# pengyos-disks — "Disks"

Format, write disk images to, and eject USB sticks and memory cards on the
PengyOS Desktop. Before this the Desktop had UDisks2 and every mkfs tool but
nothing graphical to drive them: COSMIC Files mounts and ejects, and has no
Format action.

- **Format…**: one partition with FAT32 (the default: works everywhere, files
  up to 4 GB), exFAT, NTFS or ext4, and an optional name (checked against
  libblockdev's limits first: UDisks only checks a name after it has rewritten
  the partition table). MBR up to 2 TiB, GPT above.
- **Write image…**: an `.img`, `.iso`, or a compressed `.img.xz` / `.zst` /
  `.gz` (unpacked on the fly), written to the whole drive with real progress
  (bytes written over the unpacked size; it flushes every 64 MiB, so the bar
  follows the stick, not the page cache)
  and a Stop button. This is how to make a PengyOS installer stick from a
  running PengyOS: download the `.img.xz`, *Open with Disks* (or Write image…).
- **Eject**: unmounts and powers the drive off.

Only removable drives are listed (USB, SD/MMC, or flagged removable). A drive
the system itself has mounted (outside `/run/media`, e.g. the live stick a
session runs from), one holding active swap, and a write-protected card are
shown but locked, with the reason. Internal disks are never shown. See
[`spec.md`](spec.md) for the contract.

## How it works

Everything goes through UDisks2 on the system bus; the app never runs as root
and adds no polkit rules. With UDisks' shipped policy the active local user
formats (ext4 is made owned by the user) and ejects removable drives without a
password; writing an image (`open-device`) asks for an administrator's password
in COSMIC's polkit dialog (for wheel members, their own password —
`50-pengyos-admin.rules`).

- `src/lib.rs` — std only: the drive model from a `GetManagedObjects`
  snapshot, which drives are locked and why, filesystem name rules, the UDisks
  call plan for each action, the copy loop, user-facing error text.
- `src/udisks.rs` (feature `udisks`, zbus) — reads the snapshot, runs the plan.
- `src/app.rs` (feature `cosmic`) — the window.

## Use

```console
pengyos-disks                      # the window
pengyos-disks ~/Downloads/x.img.xz # the window, with that image ready to write
pengyos-disks --list               # the drives it offers / locks, no window
```

## Tested

In QEMU on the Desktop image (26.10.24 + this change), live session and an
installed system as a wheel user: FAT32 / exFAT / NTFS / ext4 with names, on a
mounted stick too; the Core `.img.xz` and a raw `.img` written byte-identical
(`cmp`), and the written stick boots; a 1.1 GB image refused for a 512 MB
stick; the password prompt, its Cancel, and Stop mid-write; Eject; the live
stick locked; the file chooser and *Open with* for disk images.

## Build

```console
cargo test                                   # the decision logic: no display, no D-Bus
cargo run --features udisks -- --list        # against the host's UDisks
CARGO_TARGET_DIR=~/cosmic-build/install-gui cargo build --release --features cosmic
```

`scripts/build-cosmic.sh pengyos-disks` does the last one; the Desktop
post-build installs `~/cosmic-build/install-gui/release/pengyos-disks`, and the
overlay carries `io.catbee.pengyos.Disks.desktop`.
