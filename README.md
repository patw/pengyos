# PengyOS

A Linux distribution built from source with [Buildroot](https://buildroot.org), in
three nested editions (Core ⊆ Server ⊆ Desktop), running on hardware we own. Every
component is picked on its own merits and justified in [`SPEC.md`](SPEC.md). The
whole system rebuilds from pinned sources, and a person signs the release key.

PengyOS is a Catbee project. Catbee isn't a company and this isn't a product: no
roadmap to a business model, no target market, no growth metric. The aim is the
opposite, a computing environment that the people using it understand and control.
Most of the code is upstream. The part we added is the process: a human–AI loop
that curates, builds, verifies and maintains the system. Keeping a bespoke OS alive
is normally a decade of unpaid drudgery; the claim is that a small team can do it
now.

## Status

Images are calendar-versioned `YY.MM.N`. Current: 26.10.13 ([`VERSION`](VERSION)).
Downloads at <https://pengyos.catbee.ca/download/>. Updates come from the signed
feed at <https://pengyos.catbee.ca/update/>: `pengyos-update` writes the new image
into the inactive root slot, boots it once on trial, and keeps it only if it
reaches multi-user.

| Edition | Where it actually runs |
|---|---|
| Core (`pengyos-core`) | An Intel N100 box: install, DHCP, key-only `sshd`, one unattended service, video console, ~224 MB idle RAM. Rebuilt byte for byte on a second build host at 26.10.3. Still missing: measured boot, in-place updates. |
| Server (`pengyos-server`) | An i3-1215U machine ("behocraptop"): installs from its own USB stick, boots headless with everything Core has plus the comfort CLI, rootless podman, Python and pip, zsh, git, distrobox, monitoring, backup and a default-deny firewall. |
| Desktop (`pengyos-desktop`) | Daily drivers on five machines: two N100 boxes ("behoweather", "behobasement"), an i3-1215U, an i7-7600U ThinkPad, a Ryzen 7 6800H laptop. COSMIC behind greetd (auto-login is opt-in), Wi-Fi, Bluetooth, PipeWire audio, printing, Xwayland, Vulkan, Plymouth splash, Flatpak from Flathub. Graphical installer on the stick. |
| Server NVIDIA (`pengyos-server-nvidia`) | Builds and boots in QEMU, not yet booted on an NVIDIA machine (behoserver is the intended one). CUDA, `nvidia-smi`, persistence mode, GPUs inside podman containers through CDI. |
| Desktop NVIDIA (`pengyos-desktop-nvidia`) | The same with the proprietary driver on the open kernel modules, for GeForce RTX. beholaptop ran the plain Desktop image instead. |

An installed machine keeps its state the way [`docs/state-layout.md`](docs/state-layout.md)
describes it: two read-only ext4 system slots that GRUB picks between, and a btrfs
`data` partition for everything that belongs to the machine.

[`tools/`](tools/) holds 68 directories, each a tested project with its own spec
and suite. `scripts/release.sh` builds, checks and publishes every edition in one
command. `tools/pengyos-nightly` watches upstream releases and CVE feeds, triages
them with a local model, and has been publishing its own boot-checked images to the
testing channel since 2026-10-05 (26.10.8: one night's batch of changes, one signed
image). Core and Server rebuild byte for byte on a second host; the Desktop doesn't
yet, because COSMIC is built on the host, not from a Buildroot recipe. What
remains is that recipe work, measured boot, Core in-place updates, and more
hardware. Open work is tracked as GitHub issues, and the history is in
[`CHANGELOG.md`](CHANGELOG.md).

## Editions

One base, three additive layers:

```
        ┌──────────────────────────────────────────────┐
        │  DESKTOP   = Server + graphical stack + apps  │   the one you look at
        ├──────────────────────────────────────────────┤
        │  SERVER    = Core  + remote work + services   │   headless, comfortable
        ├──────────────────────────────────────────────┤
        │  CORE      = the shared, minimal, bootable base│  tiny, everything builds on it
        └──────────────────────────────────────────────┘
```

Core is the exact base the other two are defined on, not a cut-down subset of them.
A Desktop component may add; it may not contradict a Core decision. Each edition is
a Buildroot target in [`targets.toml`](targets.toml) with its own defconfig and
board under [`buildroot-external/`](buildroot-external/).

## Quick start

Build an edition, boot it headless, write it to a stick. The verified build host is
Ubuntu 26.04 on x86_64 with KVM, ~16 GB RAM and ~150 GB of disk. Core and Server
need nothing else from the host; the Desktop also needs COSMIC built on the host
first (SPEC §7).

```sh
# 0. provision a build host (once, idempotent; it calls sudo for apt-get)
scripts/provision-build-host.sh             # add --desktop for the Desktop editions
scripts/build-cosmic.sh                     # Desktop only: pinned COSMIC + our libcosmic apps

# 1. build an edition. Reads targets.toml, writes under ~/build/pengyos.
#    Only `acquire` touches the network (blobless clone of the pinned Buildroot
#    tag); configure and build refuse to run without it.
cd tools/pengyos-build
python3 -m pengyos_build list      --targets ../../targets.toml
python3 -m pengyos_build acquire   pengyos-core --targets ../../targets.toml
python3 -m pengyos_build configure pengyos-core --targets ../../targets.toml
python3 -m pengyos_build build     pengyos-core --targets ../../targets.toml

# 2. boot it headless under QEMU/KVM (UEFI) and assert it reaches a login prompt
cd ../pengyos-qemu-verify
python3 -m pengyos_qemu_verify boot \
    ~/build/pengyos/output/pengyos-core/images/pengyos-core-x86_64.img --uefi

# 3. or run the smoke matrix (Core by default; --matrix smoke/server.toml for Server)
python3 -m pengyos_qemu_verify smoke

# 4. write it to a USB stick (the post-dd GPT repair is mandatory; the script does it)
sudo scripts/flash-usb.sh ~/build/pengyos/output/pengyos-core/images/pengyos-core-x86_64.img /dev/sdX
```

Targets are `pengyos-core`, `pengyos-server`, `pengyos-desktop`, and the opt-in
`pengyos-server-nvidia` and `pengyos-desktop-nvidia`. Every tool under [`tools/`](tools/)
is an ordinary Python or Rust project; run its suite with `pytest -q` or `cargo test`.

## Repository layout

```
.
├── README.md / SPEC.md / USE-CASES.md            the product & design documents
├── CHANGELOG.md / CONTRIBUTING.md / LICENSE      project-level housekeeping
├── SECURITY.md             reporting vulnerabilities; the update trust model
├── .github/workflows/      CI: the full gate (every tool's tests + wiring lints) on each push
├── VERSION                 the image version (YY.MM.N)
├── targets.toml            the build driver's canonical target registry
├── buildroot-external/     our Buildroot BR2_EXTERNAL tree (boards, defconfigs, recipes)
├── docs/                   design & process documents (install/update, state layout, QEMU testing)
├── scripts/                host-side helpers (defconfig composer, gate, flashing, provisioning)
└── tools/                  the toolkit & authored components — one bounded project each
```

Every directory has its own `README.md` saying why it exists and what's in it.
Start there when you're looking for something.

## Where the rest of it is written down

- [`SPEC.md`](SPEC.md) — what we build: the component stack and the keystone
  decisions, each with the alternatives that were rejected.
- [`USE-CASES.md`](USE-CASES.md) — for whom: the user stories whose acceptance
  criteria become our tests. When this file and the spec disagree, this file wins.
- [`docs/`](docs/) — install/update architecture, the state layout, the QEMU
  runbook, the replacement ladder, our AI-contribution stance.
- [`SECURITY.md`](SECURITY.md) — reporting a vulnerability, and how an update is
  trusted.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — the rules, versioning, releasing.
- [`divergence.toml`](divergence.toml) — every fork and carried patch set, with the
  condition under which it goes away.

This file is the *why*: what we believe, and how the work is organized.

## Why this exists

**Freedom.** Software you can read, build, change and keep. No opaque blob at the
core, no license that takes the freedoms back. The test for a component is
whether it keeps the user in control, not whether it's the most popular option.

**Self-hosting.** The system builds, updates and explains itself on hardware we
own, with no dependency on a vendor's cloud, telemetry or account.

**Reproducible and verifiable.** Same inputs, same system, and you can check the
result. If you can't reproduce it, you don't own it.

**Assembled, not invented.** The open-source world already contains most of what a
good system needs. The craft is choosing, integrating, and occasionally replacing
one component at a time. So we keep Linux and the contracts other people depend on
(POSIX and the filesystem hierarchy, ELF and dynamic linking, systemd unit
semantics, the D-Bus wire protocol, Wayland, the freedesktop specs) and replace
only what earns replacement. Leaves first, inward second: smallest blast radius,
cheapest to verify, easiest to revert.

What it isn't. Not a business, and not a distro for everyone; it's tailored to our
hardware and our use, and "please every user" isn't a goal. Not a Redox-style
rewrite from zero, kernel included. Not a race to ship AI-generated code the
fastest, because speed of change isn't the virtue here. Verifiability of change is.

## Principles

When a decision is contested, these decide it.

1. **Freedom first.** MIT and Apache-2.0 preferred. LGPL where it's dynamically
   linked. GPL for standalone system components, when that's the price of genuinely
   free software. Where hardware forces a proprietary piece (device firmware,
   NVIDIA's driver) it ships in plain sight, in its own package or image, with the
   reasoning recorded in SPEC. Utility beats purity, and the reasoning ships with
   the exception.

2. **Assemble, don't derive.** Compatible with the wider Linux world, derived from
   no distro. We take upstream sources and releases; we don't inherit another
   distro's configuration, patch sets or packaging decisions. Growth comes through
   four supply lanes: source recipes by default, upstream artifacts, sandboxed
   Flatpak and AppImage, and compatibility guest containers. Proprietary software
   stays out of the base image except where hardware forces it, and those cases are
   listed in SPEC.

3. **Compatibility is a contract, not a courtesy.** We reimplement *behind* stable
   interfaces and don't break them. A component may be replaced; its public
   contract may not.

4. **Reproducible by construction.** A declarative definition, built from source.
   Bit-for-bit where that's achievable; elsewhere derivation reproducibility: same
   inputs and the same build definition, same result, with the input graph recorded.
   The bootstrap chain (a seed toolchain that builds the system) is documented and
   auditable.

5. **Verifiable by construction.** Every image ships a software bill of materials
   and a signed attestation. Changes are validated by build, boot and behaviour
   tests, witnessed independently, and logged append-only. "It works on my machine"
   isn't evidence.

6. **Contribute back by default.** Improvements are written as upstreamable patches
   wherever possible and tracked with their rationale and upstream status. Carrying
   a local patch, or a maintained fork, has to be conscious and time-bounded, never
   drift that happened by accident. The budget is a number: at most 10 maintained
   forks, each with a named expiry condition, in
   [`divergence.toml`](divergence.toml). Today: 0.

7. **Boring designs, current versions.** Well-understood architectures, latest
   stable releases (SPEC §1.6). Novelty in architecture is a cost, and so is falling
   behind; we buy currency with rebase discipline.

8. **Human artistry is protected.** The boot experience, the default theme, which
   apps ship, keybindings and layout, documentation voice, naming: human-led and
   AI-assisted, never AI-decided. A machine with no taste is an appliance. This one
   should feel like a place.

9. **The human signs the boundary.** The AI may draft, propose, build, test and
   document anything. Release signing, update acceptance and privilege changes need
   independent verification *and* a person's sign-off. A system that maintains
   itself must never be able to silently change its own trust root.

10. **Continuity lives in artifacts.** Philosophy, specs, tests and build recipes
    live in this repository, in plain text, under version control. The agent memory
    bus is for task recall. If it matters, it gets written down here.

## The loop

What's new is how the system gets curated and kept alive: the same loop already
running on our fleet, with an external witness, no self-modification, and a human
approving.

```
OBSERVE → PROPOSE → BUILD → VERIFY → RATIFY → (attest, log, repeat)
```

**Observe.** Track upstream releases, changelogs and advisories for the pinned
component set, and triage each one as security, relevant, or ignore. This is the
part a model is genuinely good at: reading a great deal and judging relevance.

**Propose.** The AI drafts the change (a version bump, a patch, a new component)
with its justification and files it. It does not apply it.

**Build.** A reproducible build produces the artifact and its SBOM.

**Verify.** An independent witness runs the boot and behaviour test matrix. The
AI's own tests are necessary but not sufficient; somebody else checks the work.

**Ratify.** A person approves anything that crosses the security boundary.

`tools/pengyos-nightly` is this loop applied to an operating system: observe,
triage, plan, act, one boot-checked image on the testing channel per night, and no
code path that could reach stable.

## How far down the stack we go

The build engine is the keystone decision, and it's Buildroot (SPEC §0). Above it,
each layer is replaceable behind a stable interface, and the order matters: leaf
userspace first, then the desktop and session, then the mid-stack, then init, then
boot. Replace interiors, not interfaces. The kernel stays, configured per hardware,
not rewritten. A from-scratch browser is a non-goal; the web platform grows faster
than any implementation can follow. The ABI line is the firewall: customize above
it, pin it at and below.

The bands, the test a replacement has to pass before we start one, and the fork
policy are in [`docs/stack-down-replace.md`](docs/stack-down-replace.md).

## Component selection rubric

Every component in `SPEC.md` is scored against the same criteria, and each entry
records the justification, the alternatives considered, and why they were rejected.
That's what makes "which component, and why" a reviewable decision instead of a
mood, and it's what lets whoever inherits the decision inherit the reasoning.

- **Freedom / license.** Permissive preferred; weak copyleft when dynamically
  linked; strong copyleft for standalone tools; exceptions documented.
- **Maintenance and longevity.** Is it alive, who carries it, will it still be
  there in five years?
- **Security posture.** CVE history and response, attack surface, memory safety,
  sandboxing.
- **Dependency mass.** Fewer, deeper dependencies beat many shallow ones.
- **Interface stability.** A contract we can rely on, or a moving target.
- **Replaceability.** How hard is it to swap out when it goes wrong?
- **Fit.** Does it serve our hardware and our use case?
- **Modernity, spent wisely.** "Modern" only counts as a reason when it buys memory
  safety, reproducibility or maintainability. Boring is the default posture.

Two worked examples are in SPEC §5. GNU coreutils ships today because Buildroot has
its recipe and ours isn't written; uutils is the target, and the swap is cheap
because the interfaces are POSIX either way. systemd is kept for the contract, not
loved.

## Failure modes we design against

Named so we notice them early.

| Failure mode | What we do about it |
|---|---|
| Verification debt: generating changes faster than we can test them | Test infrastructure is a deliverable, not overhead. Changes stay small and atomic, and rollback always exists. This, not code generation, is the limiting reagent of the project. |
| Divergence debt: forks and local patches piling up | Contribute-back by default, patches tracked with upstream status, and a 10-fork budget paid down against the expiry conditions in `divergence.toml`. |
| Trust-boundary corruption: the maintainer compromising its own trust root | The AI cannot change the trust root, and a human signs. Changelogs, advisory feeds and patches are treated as untrusted input, so nothing derived from them is applied automatically. Attestation is append-only. |
| Supply chain: a malicious or compromised upstream | Pinned sources, build from source, SBOM, attestation. |
| Hardware drift: the OS stops fitting the machines it runs on | Declarative per-machine config today (the defconfig layers and board dirs under `buildroot-external/`); a hardware-profile generator is still a goal. |
| Scope seduction: replacing everything because we can | Leaves first. The kernel and a browser are explicit non-goals. |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The short version: point at a user story,
prove your change with a build or test someone else can reproduce, run the repo gate
(`python3 scripts/pengyos_gate.py run --repo . --mode full`), and keep changes small
and atomic. Revert is a feature. Contributions from people and from agents are both
welcome and both disclosed; how we behave when contributing into someone else's
project is in [`docs/ai-contribution-stance.md`](docs/ai-contribution-stance.md).

## License

[MIT](LICENSE) © 2026 Pat Wendorf. Where a component carries a different license,
the choice and its reasoning are recorded in [`SPEC.md`](SPEC.md).

"PengyOS" is a working name and it collides with the Pengy assistant. Naming is a
protected artistry domain, so alternatives are open (SPEC §6).
