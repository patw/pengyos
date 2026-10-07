# Security

## Reporting a vulnerability

Email **dungeons@gmail.com**. There is no tracker, no bug bounty and no
guaranteed response time — PengyOS is one person and a set of agents, and that
address is the whole process for now. Please don't open a public issue for a
live problem: disclose it by email first, so there is a fix to point at before
there is a thread about it.

Include the image and version (`cat /etc/os-release`: `IMAGE_ID`,
`IMAGE_VERSION`, `BUILD_ID`), what an attacker needs (network position, a local
account, physical access), and the steps to reproduce. If `BUILD_ID` matches
nothing in this repository's history, send it anyway — the history was squashed
when the project went public, and the number still identifies which build you
are running.

Fixes ship as a new image on the `testing` update channel first, then `stable`.

**Supported versions:** the newest image of each edition on each channel.
Installed machines check for updates daily, so older images are not patched.

## What is in scope

- The images: Core, Server, Server NVIDIA, Desktop, Desktop NVIDIA. That covers the
  configuration we ship (`buildroot-external/`) and the components we write
  (`tools/`).
- The update path: the feed, signed manifests, `pengyos-update`, the A/B slot
  switch and rollback.
- The installer (`pengyos-install`, `-gui`, `-tui`) and the install stick.
- The build and release tooling, where a flaw could put something into an image
  that its source does not explain.

Flaws in upstream components (the kernel, systemd, COSMIC, Firefox, …) belong
upstream. We still want to hear about them if PengyOS ships an affected version
or configures one in a way that makes it worse.

## Update trust model

Updates are served from `https://pengyos.catbee.ca/update/` and work as
described in [`docs/state-layout.md`](docs/state-layout.md):

- **The feed is not trusted.** `feed.xml` only announces releases. Each
  release's `manifest.json` lists every file with its sha256 and carries a
  detached ed25519 signature (`pengyos-sig-v1`).
- **Only the release key can sign an update.** Machines accept a manifest only
  if its signature verifies against a public key the image itself ships, in
  `/usr/share/pengyos/keys/`. The private key never lives in this repository,
  on build machines or on test machines. Signing a release is a deliberate
  human step: `scripts/release.sh` signs only when given `--publish` and the
  key.
- **Updates go to the matching image only.** The manifest must name the
  machine's own `IMAGE_ID`, so a Desktop never takes a Desktop NVIDIA image. It
  must also carry a newer version; `pengyos-update` refuses a downgrade unless
  one is asked for explicitly.
- **Every byte is checked.** Each download is hash-checked before it is used,
  and the root filesystem is hashed again as it is written to the other slot.
- **A bad update rolls itself back.** A new slot boots once on trial. It
  becomes the default only if it reaches multi-user; otherwise the next boot
  returns to the old slot. Updates never write to the `data` partition.

## Known limitations

These are stated so nobody has to discover them:

- **Secure Boot and measured boot are not enforced** in the current images. The
  boot chain (GRUB, kernel) is unsigned, so someone with physical access or
  root can change what boots. This is on the roadmap.
- **Stale updates are not detected.** Manifests have no expiry, so an attacker
  who controls the network can withhold updates and keep a machine on an
  older, validly signed image. They cannot install anything unsigned or older
  than what the machine already runs.
- **Live images log root in automatically.** A flashed image or the install
  stick does this on the console. Their placeholder root password is public.
  Installed systems ask for a login, and the installer sets the passwords.
- **SSH is key-only.** `PasswordAuthentication no`, and root only by key.
  Images carry no authorized key unless one was added at build time
  (`PENGYOS_AUTHORIZED_KEYS_FILE`). Published images never carry one.
- **The Desktop is not yet fully reproducible.** COSMIC and the PengyOS Desktop
  apps are built on the build host rather than from Buildroot recipes, so
  those binaries cannot be checked by an independent rebuild yet. (Core and
  Server can: 26.10.3 rebuilt byte for byte on a second host.)
