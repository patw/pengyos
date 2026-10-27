# `tools/` — the PengyOS toolkit and components

PengyOS is assembled by a **monorepo of small, bounded tools**. Each subdirectory
here is one of them: a self-contained project with its own README, license,
changelog, spec, tests and a pinned verifier. They fall into two groups:

- **The toolkit** — the tools that *build, verify, package, install, update and
  maintain* the distro (e.g. `pengyos-build`, `pengyos-qemu-verify`,
  `pengyos-sbom`, `pengyos-nightly`).
- **Components we author** — things the editions actually ship (e.g. the Rust
  Desktop apps `pengyos-updates` / `pengyos-disks`, the installer, `pengy-games`).

Prototypes that were measured but not shipped (`pengyos-jev`, `pengyos-stt-whistle`,
`pengyos-upscale`) live in [`../prototypes/`](../prototypes/), outside the gate.

Most tools have a `spec.md` (the contract) next to their README. Newer tools
written directly in the repo (`pengyos-update`, `pengyos-updates`) skip it; their
README is the contract.

## What a tool directory contains

```
tools/<id>/
├── README.md          # what it is + how to use it           (the entry point)
├── spec.md            # the contract: behaviour + acceptance criteria (frontier-authored)
├── LICENSE            # MIT, "Copyright (c) 2026 Pat Wendorf"
├── CHANGELOG.md       # Keep a Changelog
├── verify.toml        # how the tool is tested; the gate's full mode runs it
├── <package>/         # the tool itself (Python package / Rust crate / CMake project)
└── tests/             # its suite (the verifier runs this for real)
```

## Running a tool

```sh
# Python tools
cd tools/<id> && python3 -m pytest -q

# Rust tools
cd tools/<id> && cargo test
```

The **repo-level integration gate** lives at
[`../scripts/pengyos_gate.py`](../scripts/pengyos_gate.py). A tool's own suite is
self-consistent and can be green while the *integration* is broken, so the gate
runs an external check over the tree:

```sh
python3 scripts/pengyos_gate.py run --repo . --mode fast        # wiring lints (offline)
python3 scripts/pengyos_gate.py run --repo . --mode full        # + every tool's suite
```

## Index

| Tool | What it is |
|---|---|
| [`pengy-blocks`](pengy-blocks/) | *Glacier Blocks*: ten columns, twenty rows, seven pieces — falling blocks with a 7-bag, wall kicks, hold and a ghost (Rust, libcosmic). |
| [`pengy-floe`](pengy-floe/) | *Flappy Pengy*: flap a penguin through the gaps in drifting ice pillars, under an aurora (Rust, libcosmic). |
| [`pengy-games`](pengy-games/) | The eight Desktop games in one binary: `pengy-games GAME`, or `pengy-GAME` as a symlink to it — one libcosmic for all eight (Rust). |
| [`pengy-invaders`](pengy-invaders/) | *Thaw Invaders*: Space Invaders (1978) — the formation, three ice shields, one laser (Rust, libcosmic). |
| [`pengy-lander`](pengy-lander/) | *Ice Lander*: Lunar Lander (1979) — land the probe slow and level on a pad, moon or asteroid (Rust, libcosmic). |
| [`pengy-merge`](pengy-merge/) | *Icefloe 2048*: slide the ice floes, fuse equal ones, reach 2048 (Rust, libcosmic). |
| [`pengy-panel`](pengy-panel/) | A fast, low-memory status bar for Wayland, written in pure Rust on top of layer-shell — no GTK, no C++ bindings, no… |
| [`pengy-pong`](pengy-pong/) | *Penguin Pong*: Pong (1972) on an ice rink, against the penguin at three levels or a friend (Rust, libcosmic). |
| [`pengy-rogue`](pengy-rogue/) | *Ice Rogue*: Rogue (1980) — five floors down to The Thaw, permadeath (Rust, libcosmic). |
| [`pengy-solitaire`](pengy-solitaire/) | *Frostbite Solitaire*: Klondike, draw three, drag and drop (Rust, libcosmic). |
| [`pengy-theme`](pengy-theme/) | Minimal, modern theme & desktop-defaults packager/validator for PengyOS. |
| [`pengyos-apparmor`](pengyos-apparmor/) | Minimal, strict-by-default AppArmor profiles for PengyOS core components (including the pengy agent) plus a validat… |
| [`pengyos-appimage`](pengyos-appimage/) | Inspect AppImages, add them to the app launcher, and find the libraries they need. Never runs or unpacks one. Shipped on Desktop. |
| [`pengyos-attest`](pengyos-attest/) | A tamper-evident, append-only hash-chained attestation log for PengyOS: record build and change events as an append… |
| [`pengyos-build`](pengyos-build/) | Reproducible Buildroot build driver for PengyOS targets: drive a named target declared in targets.toml through a pi… |
| [`pengyos-cosmic-build`](pengyos-cosmic-build/) | The wiring lane that closes the gap between the COSMIC recipes' placeholder 'shapes' and a real Buildroot build. |
| [`pengyos-cosmic-native-deps`](pengyos-cosmic-native-deps/) | Buildroot recipes for the native (non-Rust) dependencies COSMIC needs that 2026.08 does not ship — the closing BR2_… |
| [`pengyos-cosmic-pkg`](pengyos-cosmic-pkg/) | The COSMIC packaging engine: turn a git Rust component (a component manifest — URL + pinned tag + Cargo.lock) into… |
| [`pengyos-cosmic-recipes`](pengyos-cosmic-recipes/) | Manifest-driven Buildroot cargo recipes for the COSMIC desktop components, built on top of the pengyos-cosmic-pkg e… |
| [`pengyos-desktop-curation`](pengyos-desktop-curation/) | Curated PengyOS Desktop default-application set as declarative data. |
| [`pengyos-desktop-drivers`](pengyos-desktop-drivers/) | The generic (no vendor-acceleration) driver set for the PengyOS Desktop live + installed image: a kernel linux.frag… |
| [`pengyos-desktop-nvidia`](pengyos-desktop-nvidia/) | Containment lint for the opt-in NVIDIA editions, Server and Desktop (package `nvidia-open`): the proprietary driver stays out of every default image. |
| [`pengyos-desktop-session`](pengyos-desktop-session/) | The COSMIC session + a VM-testable smoke matrix (US-DSK-1) for PengyOS Desktop. |
| [`pengyos-disks`](pengyos-disks/) | *Disks*: every drive and its partitions; create a partition table, create, format, grow and delete partitions, mount, swap; format, write images to and eject USB sticks and cards. The running system's own partitions are locked (Rust, libcosmic, over UDisks2). |
| [`pengyos-firmware`](pengyos-firmware/) | *Firmware*: the devices fwupd can update, what is on offer, and Update all / just this one, with update notifications (Rust, libcosmic, over fwupd). |
| [`pengyos-firewall`](pengyos-firewall/) | Open and close this machine's own ports on top of the default-deny nftables ruleset: one rule file per port in `/etc/pengyos/nftables.d/`, checked with `nft -c` before reload. Every edition. |
| [`pengyos-flatpak-mirror`](pengyos-flatpak-mirror/) | Self-hosted Flatpak (OSTree) repo manager for PengyOS. |
| [`pengyos-gamepads`](pengyos-gamepads/) | *Gamepads*: list the attached game controllers and joysticks and test their buttons, sticks, triggers and D-pad live (Rust, libcosmic, over evdev + sysfs). |
| [`pengyos-printers`](pengyos-printers/) | *Printers*: add and remove printers, manage the print queue and print a test page (Rust, libcosmic, IPP to CUPS' local socket). |
| [`pengyos-fleet-inventory`](pengyos-fleet-inventory/) | Collect hardware facts for a fleet of hosts and emit a declarative, version-controllable per-machine profile for Pe… |
| [`pengyos-fleet-rollout`](pengyos-fleet-rollout/) | Plan, stage and roll back a PengyOS image/update rollout across a fleet. |
| [`pengyos-guest`](pengyos-guest/) | Declarative distrobox/podman guests (Lane 4) — the dev box: `pengyos-guest enter dev`. Ships in Server/Desktop images with the `dev` (and on NVIDIA `cuda`) guest. |
| [`pengyos-hardening`](pengyos-hardening/) | A declarative PengyOS hardening baseline plus an overlay/defconfig linter — the tool that audits Buildroot BR2_EXTE… |
| [`pengyos-hwprofile`](pengyos-hwprofile/) | Detect hardware and emit a declarative, version-controllable per-machine profile for PengyOS — a from-source, repro… |
| [`pengyos-hwprofile-kernel`](pengyos-hwprofile-kernel/) | Turn a pengyos-hwprofile hardware profile into a minimal kernel fragment set + a defconfig fragment for PengyOS — a… |
| [`pengyos-initramfs`](pengyos-initramfs/) | Deterministic minimal initramfs builder for PengyOS. |
| [`pengyos-install`](pengyos-install/) | The edition-agnostic install engine for PengyOS. |
| [`pengyos-install-gui`](pengyos-install-gui/) | A libcosmic-native graphical installer for PengyOS Desktop. It drives pengyos-install through a pure, headless view… |
| [`pengyos-install-tui`](pengyos-install-tui/) | Guided Core/Server installer wizard + unattended --auto for PengyOS. |
| [`pengyos-installer`](pengyos-installer/) | Install PengyOS Core from the live stick onto a target disk. |
| [`pengyos-kernel-fragments`](pengyos-kernel-fragments/) | Kernel config fragment library + composer for PengyOS. |
| [`pengyos-layout`](pengyos-layout/) | The declarative disk/layout planner for PengyOS installs (ESP + A/B + data, optional LUKS2). |
| [`pengyos-license-audit`](pengyos-license-audit/) | Verify each PengyOS component's license claim and enforce the freedom policy; emit LICENSES.md. |
| [`pengyos-live`](pengyos-live/) | Emit + lint the PengyOS live/installer session overlay — the overlay that makes a PengyOS install medium boot into… |
| [`pengyos-media`](pengyos-media/) | Build PengyOS — the live install medium (ISO or disk image) for an edition. |
| [`pengyos-net`](pengyos-net/) | Baseline systemd-networkd/systemd-resolved configuration plus an nftables ruleset with a default-deny inbound polic… |
| [`pengyos-nightly`](pengyos-nightly/) | The nightly maintenance loop: observe upstream, triage CVEs with the local model, email a report, batch the allowed fixes into one boot-checked testing release, and a web page to approve or deny what it recommends. |
| [`pengyos-postmortem`](pengyos-postmortem/) | Why did that session end? One read-only command over the journal. Shipped on Server and Desktop. |
| [`pengyos-qemu-verify`](pengyos-qemu-verify/) | A headless QEMU/KVM boot-test harness for PengyOS: boot a PengyOS disk image under QEMU (q35), wait for readiness m… |
| [`pengyos-recipe-lint`](pengyos-recipe-lint/) | A linter for the Buildroot package recipes PengyOS authors. It parses .mk/.hash recipe pairs and reports violations… |
| [`pengyos-recipe-uutils`](pengyos-recipe-uutils/) | Buildroot recipe for uutils/coreutils — the Rust reimplementation of GNU Coreutils — for the PengyOS distribution,… |
| [`pengyos-release`](pengyos-release/) | Publish a signed, versioned PengyOS release to a local update feed. |
| [`pengyos-repro`](pengyos-repro/) | Reproducible-build differ for PengyOS. |
| [`pengyos-repro-report`](pengyos-repro-report/) | Rebuild-and-compare reproducibility reporting for PengyOS build outputs, written in the witness layout so the exter… |
| [`pengyos-sbom`](pengyos-sbom/) | Turn Buildroot legal-info / show-info manifests into a valid SPDX 2.3 SBOM (and CycloneDX 1.5), with ed25519 signin… |
| [`pengyos-seal`](pengyos-seal/) | Wire provenance into a pengyos-build output — the closing glue for US-CORE-6. |
| [`pengyos-server-backup`](pengyos-server-backup/) | The backup planner for PengyOS. |
| [`pengyos-server-containers`](pengyos-server-containers/) | PengyOS server-containers — rootless podman + declarative services (US-SRV-2). |
| [`pengyos-server-firewall`](pengyos-server-firewall/) | A default-deny nftables ruleset generator plus a WireGuard config generator, with an offline validator. Part of the… |
| [`pengyos-server-monitoring`](pengyos-server-monitoring/) | Node-exporter + TheWatcher systemd units, a small Prometheus-style alert-rule set stored as data, and a fixture-dri… |
| [`pengyos-server-toolset`](pengyos-server-toolset/) | The Server edition's remote-admin comfort set for PengyOS. It ships two things, both validated by a pytest suite: |
| [`pengyos-sign`](pengyos-sign/) | Minimal, modern artifact signing for PengyOS. |
| [`pengyos-storage`](pengyos-storage/) | `du` that draws: a read-only terminal/HTML/JSON storage report (Python, stdlib only) for Server and scripts; the Desktop app is `pengyos-usage`. |
| [`pengyos-systemd-units`](pengyos-systemd-units/) | Core systemd unit set for PengyOS, plus a small, dependency-free Python validator that checks unit syntax and secti… |
| [`pengyos-update`](pengyos-update/) | The on-device A/B updater: read the signed feed, write the release into the other root slot, boot it once, keep it only if it comes up (`status`/`check`/`apply`/`rollback`/`channel`). |
| [`pengyos-updates`](pengyos-updates/) | *PengyOS Updates*: the Desktop app + notifier (Rust, libcosmic) over `pengyos-update`. |
| [`pengyos-usage`](pengyos-usage/) | *Disk Usage*: what is taking the space, as a ring chart you click into beside a ranked list; your home or any filesystem, read-only (Rust, libcosmic). |
| [`pengyos-verifier`](pengyos-verifier/) | External witness for PengyOS builds. |
| [`pengyos-webui`](pengyos-webui/) | `pengyos-webui status\|on\|off` — serve the Pengy web UI as a named user (Server/Desktop): the `pengy-web@.service` system template, `User=%i`, loopback by default, state in `/etc/pengyos/webui.conf`. The UI has no login. |
| [`pengyos-witness-bundle`](pengyos-witness-bundle/) | Present a pengyos-build output in the canonical witness layout and verify its SBOM signature, so an external witnes… |
