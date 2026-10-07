# PengyOS — partition & state layout (2026-09-28)

> Status: **phase 1 implemented (Server and Desktop).** The installer lays out the disk as
> below; installed systems boot slot a read-only through preinit, with `/etc` as
> an overlay and `/var`, `/home`, `/root` on the data partition. Verified in QEMU
> by rewriting root-a from the stick (a hand-made "update"): hostname, accounts,
> SSH host keys, NetworkManager profiles, `/etc` edits and `/home` all survived.
> The desktop now runs as the user the installer created, in a logind session
> (see the CHANGELOG). **Updates (2026-09-30):** `pengyos-update` installs a
> signed release into the other slot and GRUB tries it once, falling back if it
> never comes up ([below](#how-an-update-works-implemented-pengyos-update-2026-09-30)).
> **Not yet:** the live stick on the same layout, snapshot tooling.
>
> **Server (2026-09-28):** the Server installs the same way — its USB stick
> (`pengyos-server-x86_64-installer.img`) runs the console installer
> (`pengyos-install-tui install`) on tty1; the layout, preinit, account merge
> and payload are shared (they live in the Server layer, which the Desktop
> stacks on; the stick is built by `board/common/installer-image.sh`). The
> installer can give the user an SSH key: the Server's sshd is key-only.
>
> **2026-09-28:** `data` is now **btrfs** with a subvolume per area (see below) so
> machine state can be snapshotted — the basis for the agent's reversible Tier-1
> changes (SPEC §1.5, USE-CASES US-AGT-2). Installs made before that keep their
> ext4 `data`; preinit mounts either.
>
> It replaces
> the parked A/B design's storage half (`install-update-architecture.md`) with a
> smaller one that keeps GRUB + ext4, and it is the prerequisite for *any* update
> mechanism: an update replaces the system, so machine state must not live in it.

## The problem today

An installed machine is one ext4 root holding everything. Replacing the system
(any update mechanism, or a reinstall) destroys:

| State | Where it lives now | Written by |
|---|---|---|
| accounts + passwords | `/etc/passwd`, `shadow`, `group`, `gshadow` | installer, `adduser`, `passwd` |
| hostname | `/etc/hostname`, `/etc/hosts` | installer |
| machine identity | `/etc/machine-id` (empty in the image, generated on first boot) | systemd |
| SSH host keys | `/etc/ssh/ssh_host_*` — **baked into the image at build time** | `post-build.sh` |
| Wi-Fi / network profiles | `/etc/NetworkManager/system-connections/` | NetworkManager |
| local admin edits | anything else in `/etc` (firewall, timers, …) | the operator |
| the desktop user's settings | `/home/<user>/.config/cosmic`, … (the installed user's session; the live stick runs as root) | COSMIC |
| apps, containers, logs, caches | `/var/lib/flatpak`, `/var/lib/containers`, `/var/log`, … | flatpak, podman, journald |
| user files | `/home/*` | people |

**Fix independently of this draft:** the baked SSH host keys mean every installed
machine (and every stick) shares one set of host keys, so anyone with the image
can impersonate any PengyOS host. The installer should delete them and run
`ssh-keygen -A` in the target; the image should stop shipping them.

## Proposed layout

```
GPT (installed disk)
 1  ESP        vfat    256 MiB   PENGYOS-ESP     GRUB + grub.cfg + grubenv + one kernel per slot
 2  root-a     ext4      8 GiB   pengyos-root-a  system image, mounted READ-ONLY
 3  root-b     ext4      8 GiB   pengyos-root-b  the other system image (empty until the first update)
 4  data       btrfs    rest     pengyos-data    everything that is this machine
```

- **Root slots are pure build output**, byte-for-byte the release, mounted `ro`.
  Slots were 4 GiB until 2026-09-30, when the NVIDIA Desktop image reached
  3.1 GiB used; 8 GiB leaves room to grow. The
  second slot is what makes updates and rollback possible (A/B *lite*: no UKI, no
  Secure Boot, no btrfs root — GRUB picks the slot; the roots stay ext4).
- **ESP** grows from 64 MiB to 256 MiB to hold a kernel per slot
  (`/slot-a/bzImage`, `/slot-b/bzImage`). `grub.cfg` is static — two entries,
  each with its slot's `root=PARTUUID=` — and the choice between them lives in
  `grubenv` (`default`, plus a boot counter for fallback).
- **data** holds all state, on btrfs (`compress=zstd:1`, `noatime`) so it can be
  snapshotted: the agent snapshots before every Tier-1 change, and an update can
  snapshot before it boots a new image. The kernel has btrfs built in, since
  preinit mounts `data` before any module could load. The installer refuses disks under 18.25 GiB (ESP + two slots +
  2 GiB of data); 16 GiB or more is sensible.

### What goes where on `data`

```
/data/etc/upper, /data/etc/work   overlayfs upper for /etc (see below)   — subvolume etc
/data/var                         bind-mounted on /var   (flatpak, podman, journal, NM leases…) — subvolume var
/data/home                        bind-mounted on /home                    — subvolume home
/data/root                        bind-mounted on /root                    — subvolume root
```

One subvolume per area so each is snapshotted and rolled back on its own
(reverting an `/etc` change must not roll back `/home`). Preinit mounts the
top level and bind-mounts the subvolumes as directories, so no `subvol=`
options are needed. Snapshots go in `/data/.snapshots/<area>/<timestamp>`
(read-only; tooling not built yet).

### `/etc`: an overlay, mounted before systemd starts

`/etc` = overlayfs, lower = the image's `/etc` (read-only), upper =
`/data/etc/upper`. Every change a machine makes lands in the upper layer and
survives updates; everything it didn't touch comes from the (new) image.

It must be in place before PID 1 reads `/etc/systemd`, and there is no initramfs.
So the kernel runs a tiny **preinit** (`init=/usr/lib/pengyos/preinit`, shell):
mount `/proc`, `/sys`; find `data`; set up the `/etc` overlay and the `/var`,
`/home`, `/root` binds; `exec /sbin/init`. The kernel line says
`pengyos.data=4`: the data partition is partition 4 *of the disk root is on*,
found through sysfs (no udev yet, and busybox `blkid` cannot search by
PARTUUID) and accepted only if its label is `pengyos-data` — so a second
PengyOS disk (or the stick) plugged in is never picked up by mistake. If `data` is missing or
won't mount, preinit boots with a tmpfs upper and says so loudly (a usable,
forgetful system beats an unbootable one).

**Account files are merged, not overlaid.** An overlay would freeze
`/etc/passwd` at install time, so a system user added by a later image (a new
daemon) would never appear. Preinit instead regenerates `passwd`/`group`/`shadow`/
`gshadow` (`/usr/lib/pengyos/merge-accounts`) on every boot:

- every account and group **the image defines (by name)** comes from the image,
  except root's password, which is the machine's;
- the image's groups get **the machine's members added** (the user stays in
  `wheel`, `seat`, `video`, …);
- accounts/groups **only the machine has** are kept if their id is ≥ 1000
  (people) and dropped below that (system accounts an older image had);
  `shadow`/`gshadow` keep exactly the names `passwd`/`group` kept.

By name, not by id range, because Buildroot gives some system groups automatic
ids that can land at 1000+.

**Divergence should be visible (not built yet).** An upper-layer file shadows
every future image version of it. A `pengyos-etc-diff` tool should list upper
files whose image copy changed since they were written, so an update can report
"you kept your old `nftables.conf`; the image's changed".

## How install works (implemented: `pengyos-install image`)

The installer:

1. partitions the disk as above (sgdisk);
2. writes the payload's **ESP** (GRUB + `slot-a/bzImage`) into partition 1 and
   its **rootfs** into `root-a` (grown to the slot, fresh UUID); writes a
   `grub.cfg` for this disk's PARTUUIDs and a `grubenv` with `pengyos_slot=a`;
3. `mkfs.btrfs -L pengyos-data`, creates the `etc`/`var`/`home`/`root` subvolumes,
   seeds `/data/var` and `/data/root` from the image,
   then mounts the same overlay + binds preinit uses and provisions through them:
   hostname, the first user (admin + seat groups, and the `User=` drop-in that
   makes the desktop auto-log-in as them), passwords, and the network profiles
   made in the live session. SSH host keys are generated on first boot
   (`sshd-keygen.service`) and land in the upper layer.

The payload (`manifest.json` format `pengyos-layout-1`: `rootfs.ext4.zst` +
`esp.vfat.zst`, hashes of each) is the artifact an update will ship too.


## How an update works (implemented: `pengyos-update`, 2026-09-30)

Releases are published to <https://pengyos.catbee.ca/update/> by
`pengyos-release update-publish` (see `tools/pengyos-release`):

```
update/feed.xml                      Atom feed — announces releases (unsigned)
update/<IMAGE_ID>/<YY.MM.N>/
    manifest.json + .sig             files + sha256s; ed25519 (pengyos-sig-v1)
    rootfs.ext4.zst  bzImage         the install payload's rootfs + its kernel
    release.json                     channels (stable/testing), date, notes
```

On the machine, `sudo pengyos-update apply`:

1. reads the feed and picks the newest release for this machine's `IMAGE_ID`
   on its channel (`/etc/pengyos/update.conf`; testing also takes stable);
2. fetches `manifest.json` and checks its signature against the keys the image
   ships (`/usr/share/pengyos/keys/*.pub`, with the `openssl` CLI); refuses
   another image, a version mismatch, or a downgrade;
3. downloads the files to `/var/cache/pengyos-update` (resumable), checks each
   sha256 against the manifest;
4. clears any pending trial, writes the rootfs into the **other** slot (hashing
   what it writes), `e2fsck` + `resize2fs` + fresh UUID + label, copies the
   kernel to `ESP/slot-X/bzImage`, runs the **new image's update steps**
   (`/usr/lib/pengyos/update.d/*` in the new slot, as root — part of the signed
   image; e.g. `50-grub-efi` copies the image's GRUB + font to the ESP, keeping
   `*.prev`; a failing step stops the update), rewrites `grub.cfg` with both
   slots (keeping the installed kernel arguments), and sets `pengyos_try=X`;
5. on the next boot GRUB clears `pengyos_try` and boots slot X **once**. If it
   reaches multi-user, `pengyos-update-confirm.service` sets `pengyos_slot=X`
   (the new default). If it doesn't, the next boot is the old slot again.

`pengyos-update rollback` makes the other slot the default again;
`pengyos-update status` shows both slots. `data` is never touched by an update,
so accounts, Wi-Fi, apps, settings and files carry over. Preinit copies any new
build-time `/var` content from a new image into `data` (no overwriting) the
first time that image boots.

Every installed machine checks daily (`pengyos-update-check.timer` ->
`/var/lib/pengyos-update/state.json`, and `/run/motd.d` for logins). On the
Desktop, *PengyOS Updates* (`tools/pengyos-updates`) shows it and installs on
request through `pengyos-update-apply.service` (progress in
`/run/pengyos-update/progress.json`); its notifier says so only as the user
wants (off, or "remind me" 1 day … 1 month).

Not yet: Secure Boot / signed kernels, delta downloads.

## Why not the alternatives

- **Symlink individual state files into `/data`** — tools such as `passwd` and
  NetworkManager replace files by rename, which breaks the symlink; the overlay
  has no such failure mode.
- **Keep one root and update in place** — an interrupted update leaves no
  bootable system; there is no rollback.
- **OSTree** — fits this layout too (a later swap for the slot mechanism), but
  needs an initramfs and its own `/etc` conventions; more integration for the
  first version.

## Open questions

1. ~~**Root as the desktop user.**~~ Done: the desktop runs as the installed
   user (`/home/<user>`); `/root` stays persistent for root's own use.
2. **Existing installs.** Converting a single-root install in place means
   shrinking a mounted ext4 — not worth it. Reinstall (there are few installs).
3. ~~**Slot size.**~~ 8 GiB since 2026-09-30: 4 GiB was nearly full with the
   NVIDIA Desktop (3.1 GiB used). Machines installed with 4 GiB slots need a
   reinstall to take a release larger than 4 GiB.
4. **The live stick.** Same layout (so the stick exercises the real boot path),
   with `data` as a tmpfs — keep the stick read-only.
5. ~~**`/var` seeding across updates.**~~ Preinit copies what a new image has
   under `/var` and the machine lacks (`cp -an`), once per image (`BUILD_ID`,
   recorded in `/data/var/lib/pengyos/image-build`). Changed files are not
   merged — the machine's copy wins.
