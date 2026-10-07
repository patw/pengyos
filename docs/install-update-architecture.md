# PengyOS — Install & Update architecture (pinned 2026-09-24)

> **Status 2026-09-30 — updates shipped, "A/B lite".** Installed machines update
> with `pengyos-update` from the signed feed at
> <https://pengyos.catbee.ca/update/>: GRUB + ext4 slots + a grubenv one-shot
> trial boot, ed25519 manifests (`pengyos-sig-v1`), channels stable/testing. See
> `docs/state-layout.md` ("How an update works") and `tools/pengyos-update`.
> The UKI / systemd-boot / systemd-sysupdate design below stays parked, and
> the tools built for it (`pengyos-uki`, `pengyos-bootchain`,
> `pengyos-bootcount`, `pengyos-sysupdate`) were retired on 2026-10-03 — see
> git history before commit "Retire the parked UKI/sysupdate tools";
> decision 4's "local static feed first" became an HTTPS static feed directly.

> **Status 2026-09-28 — A/B is PARKED.** Decisions 1 and 2 below (A/B slots +
> btrfs + `systemd-sysupdate`; UKI + systemd-boot + Secure Boot + measured boot)
> are **not** a prerequisite for installing and are not being built now. They are
> an *update* mechanism, and there is no update mechanism yet; how installed
> machines get updates is being decided separately and may or may not use A/B.
>
> **What installs today:** the edition's `pengyos-build` disk image (GRUB + one
> ext4 root) is carried on the USB stick as a payload and written to the target
> by `pengyos-install image` (GUI: `pengyos-install-gui`; console:
> `pengyos-install-tui install`). That still honours decision 3 — only the build
> output's bytes reach the disk, hash-checked, never a copy of the running live
> system. See `tools/pengyos-install/README.md`.
>
> **Parked code:** `pengyos-install plan` / `install` / `provision-plan` (the A/B
> plan renderer + executor) — golden-tested but not executable against a real
> disk; kept for reference, not used by any front-end. The rest of this document
> is the original A/B design, unchanged.

> One artifact, one writer, one trust chain. **Install is the first update.**

This doc pins the decisions that close SPEC open decision **#6** (storage/update
model) and the bootloader/boot-chain rows, and defines the shared substrate that
both the **installer** and the **update delivery** pipeline are built on. It is
the spec-of-specs for the install/update waves; the per-task specs implement it.

## Decisions (operator, 2026-09-24)

| # | Decision | Value |
|---|---|---|
| 1 | **Storage/update substrate** *(parked)* | **A/B slots + `systemd-sysupdate`**, Bazzite/SteamOS-style; root on **btrfs** |
| 2 | **Boot chain** *(parked)* | **UKI + `systemd-boot` + boot-counting auto-fallback + Secure Boot + TPM2 measured boot** (replaces today's GRUB/ext4 boot) |
| 3 | **Install model** | **Unified: install = write the signed release artifact to a blank disk** (= the device's first update); no "copy the running stick" |
| 4 | **Update feed** | **Local static feed first** (a signed directory on a LAN host); HTTPS/self-hosted later. Fully local in testing mode |
| 5 | **Desktop installer** | **libcosmic-native GUI** (our stack; accept the multi-night Ralph burn) |

## The artifact — a *release*

`pengyos-release` turns a `pengyos-build` output into a signed, versioned **release**
that is identical whether it is being *installed* or *updated*:

```
<feed>/pengyos/<edition>/<version>/
    pengyos-<edition>-<version>.rootfs.raw.zst   # the slot image (btrfs)
    pengyos-<edition>-<version>.efi              # the UKI for this version
    pengyos-<edition>-<version>.img.sha256
    *.sig                                        # ed25519, pengyos-sig-v1
    sbom.spdx.json  attest.jsonl  repro-report.json
    meta.json                                    # version, channel, sizes, hashes, notes
<feed>/pengyos/<edition>/<channel>               # `stable` / `testing` latest pointer
```

Trust moves with the bytes: the `.sig`, SBOM, attestation and repro report travel
**with** the image, and the device rejects anything not signed by the key baked
into its ESP (Secure Boot / measured boot). No package DB exists (Buildroot), so
**the release is the unit of change** — there is no per-package upgrade.

## On-disk layout (A/B)

```
GPT
├─ ESP (FAT32, 1 GiB)      → systemd-boot + EFI/Linux/pengyos-<edition>+<ver>-<slot>.efi
│                            (+ loader entries with boot counting, 2 boots to fail)
├─ root-A (btrfs)          → slot A rootfs
├─ root-B (btrfs)          → slot B rootfs
└─ data (btrfs)            → /var, /home, and the Tier-1 mutable surface (see below)
   (optional: LUKS2 over root-*/data; ESP stays plaintext)
```

- The **UKI** embeds the slot's `root=PARTUUID` on the cmdline and is signed; the
  ESP carries one UKI per installed version/slot → the boot menu *is* the A/B state.
- **Boot counting** (`bootctl`/loader entry `+N-M`): a slot that fails to reach a
  good-boot marker in N tries is auto-marked bad → previous good slot boots.
- `systemd-sysupdate` owns the swap: `.transfer` units move rootfs → inactive
  slot and UKI → `EFI/Linux`, then update the loader entry.

## Tier-1 must survive an update

An image update rewrites a slot; it must **not** clobber machine state (SPEC §1.5
Tier-1: config, services, bootloader entries; Tier-2 human-signed). So:

- The **rootfs slots are clean/declarative**; all machine-specific mutable state
  lives on the **data** partition (`/var`, `/home`), bind-mounted (and `/etc`
  overrides via a machine-config subvolume / overlay).
- Live fixes still **converge into the declarative definition** (hardware
  profiles + the `pengyos-mutations` journal) so a future rebuild already knows.
- Bootloader *entries* live on the shared ESP — an update adds an entry, never
  destroys the fallback.

## Update flow (on device)

```
pengyos-update check     # read local feed index, compare versions, pick channel
pengyos-update stage     # download+verify (.sig/attest) → systemd-sysupdate -> inactive slot
pengyos-update activate  # set the loader entry to the new UKI; reboot
pengyos-update confirm   # mark good (boot-count reset) once the smoke marker passes
pengyos-update rollback  # deliberately revert to the previous slot
```

- **Auto-rollback** is boot-counting (no human needed if the box never comes up).
- `pengyos-updated` (timer) does check→stage→(activate per policy); `pengyos-update-triage`
  (existing) already classifies upstream moves and proposes them; `pengyos-fleet-rollout`
  (queued) batches across the fleet and gates on per-host health.

## Install flow (unified)

```
pengyos-media  build-live   # make the bootable install medium (installer + live session)
# on the target, from live media:
pengyos-install [--tui | --gui] [--auto install.toml]
   → pengyos-layout   plan ESP + root-A + root-B + data (+ LUKS)
   → write the release rootfs to slot A, the UKI to the ESP, loader entries
   → pengyos-provision (first boot): hostname, users, SSH keys, network
```

Same engine, same artifact, same verifier as the update path — "a totally normal
way to install PengyOS, whatever edition." Core/Server use the **TUI**; Desktop
uses the **libcosmic GUI**; `--auto install.toml` serves unattended/fleet installs.

## Apps are a separate lane

OS updates never carry apps. **Flatpak/AppImage self-update** in their own lane
(§2.5); Lane-4 guests are pinned images the app lane refreshes. So an OS release
stays small and app churn doesn't force an image rebase.

## What's already built vs. missing

- **Built:** `pengyos-build`, `pengyos-pins`, `pengyos-upstream-watch`,
  `pengyos-update-triage`, `pengyos-sbom/sign/attest/seal/repro/repro-report/
  verifier/witness-bundle`, `pengyos-uki` (slot+boot-count skeleton),
  `pengyos-bootcount`, `pengyos-btrfs-layout`, `pengyos-measured-boot`,
  `pengyos-secureboot-keys`, `pengyos-installer` (Core-only prototype),
  `pengyos-fleet-inventory`, `pengyos-fleet-rollout` (queued).
- **Missing (this program):** the release **publisher**, the on-device
  **updater**, the finished **boot chain**, the edition-agnostic **install
  engine + layout**, the **live-media builder**, the **TUI/GUI wizards**, and
  **first-boot provisioning**.

## Wave map

| Wave | Tasks | Goal |
|---|---|---|
| 15 — boot chain | `pengyos-bootchain`, `pengyos-secureboot-enroll` | UKI/systemd-boot/boot-count/secureboot, declaratively |
| 16 — delivery | `pengyos-release`, `pengyos-sysupdate`, `pengyos-update-policy` | publish + on-device updater + channel/policy |
| 17 — install | `pengyos-layout`, `pengyos-install`, `pengyos-media`, `pengyos-install-tui`, `pengyos-provision` | install = first update; TUI for Core/Server |
| 18 — desktop GUI | `pengyos-install-gui` (long-running) | libcosmic GUI wizard |

Operator/build work (not tool tasks): the actual rootfs→btrfs + UKI + systemd-boot
image rebuild, the secure-boot signing ceremony, and real-hardware install/update
trials.

## Still open (deferred, not blocking)

- HTTPS / self-hosted release server (local feed until then — "done this before").
- Delta transport: `systemd-sysupdate` deltas vs full zstd images vs btrfs send.
- LUKS key handling / TPM2 sealing policy for the desktop.
- Signed shim vs our own Secure Boot keys for the Microsoft-chain path.
