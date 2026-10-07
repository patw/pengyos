# pengyos-update

The on-device updater for [PengyOS](../../README.md): read the update
feed, verify the signed release, write it into the **other** root slot, boot it
once, and keep it only if it comes up.

```console
$ pengyos-update status              # version, channel, both slots (sudo for slots)
$ pengyos-update check               # is there a newer release on my channel?
$ sudo pengyos-update apply --reboot # install it and restart into it
$ sudo pengyos-update rollback       # make the other slot the default again
$ sudo pengyos-update channel testing
```

`pengyos-update confirm` runs at boot (`pengyos-update-confirm.service`): it
makes the slot that just came up the default.

## How it works

The disk layout is `docs/state-layout.md`'s: ESP (GRUB + one kernel per slot),
`root-a`, `root-b` (read-only ext4), `data` (btrfs, the machine's state).

1. **Feed** — `https://pengyos.catbee.ca/update/feed.xml` (Atom, see
   `/etc/pengyos/update.conf`). Each entry names an `IMAGE_ID`, a `YY.MM.N`
   version, its channels and its manifest. The feed is *not* trusted.
2. **Manifest** — `manifest.json` (format `pengyos-update-1`) lists the rootfs
   (`rootfs.ext4.zst`: compressed and raw size + sha256) and the kernel
   (`bzImage`). Its detached `manifest.json.sig` is `pengyos-sig-v1` —
   `pengyos-sign`'s format, ed25519 over the SHA-256 — and must verify against a
   key in `/usr/share/pengyos/keys/` (checked with the `openssl` CLI). The
   manifest must name this machine's `IMAGE_ID` (no Desktop ↔ Desktop NVIDIA
   crossovers) and a newer version (no downgrades unless `--version` asks).
3. **Write** — downloads are resumable (`/var/cache/pengyos-update`) and each is
   hash-checked. The rootfs is unpacked onto the other slot while hashing; the
   kernel goes to `ESP/slot-X/bzImage`; `grub.cfg` is rewritten (both slots,
   the installed kernel arguments) with `pengyos_install`'s renderer.
4. **Boot once** — grubenv `pengyos_try=X`. GRUB clears it and boots slot X;
   if X reaches multi-user, `confirm` sets `pengyos_slot=X`. If it doesn't, the
   next boot is the old slot again. A trial boot must be confirmed before the
   next `apply` (so the fallback is never overwritten).

Runtime: Python standard library, plus `curl`, `zstd`, `openssl`, e2fsprogs,
`lsblk`/`blockdev` and `grub-editenv` from the image.

## Publishing

`pengyos-release update-publish --build <buildroot output> --site <web root>
--key <pengyos-key-v1 private key> --channel testing` writes the release and
regenerates `feed.xml` + `index.html`; `update-promote` adds a channel.

## Tests

```console
python3 -m pytest -q
```
