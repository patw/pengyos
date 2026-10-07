# PengyOS

> A reproducible, verifiable, free Linux-based operating system — assembled from the
> best of the open-source world, curated and maintained through a human–AI
> partnership, so that it can be understood, rebuilt, and **owned end to end**.

**PengyOS** is a from-source Linux-based OS with three layered editions —
**Core ⊆ Server ⊆ Desktop** — built with [Buildroot](https://buildroot.org),
proven by build + boot tests, and maintained by a small human–AI team. It is a
**Catbee** project: not a product, but a demonstration that a self-maintaining,
self-verifying, freedom-respecting system is buildable *today*.

*(Working name — see [Open questions](#open-questions).)*

---

## Contents

- [What is PengyOS?](#what-is-pengyos)
- [Status](#status)
- [Editions](#editions)
- [Quick start](#quick-start)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Why this exists](#why-this-exists)
- [The North Star](#the-north-star)
- [What this is — and what it is not](#what-this-is--and-what-it-is-not)
- [Principles](#principles)
- [The human–AI process — the actual innovation](#the-humanai-process--the-actual-innovation)
- [Verification & trust model](#verification--trust-model)
- [Composition & compatibility model](#composition--compatibility-model)
- [Component selection rubric](#component-selection-rubric)
- [Human artistry domains](#human-artistry-domains)
- [Goals](#goals)
- [Failure modes we design against](#failure-modes-we-design-against)
- [Open questions](#open-questions)
- [Working conventions](#working-conventions)
- [Contributing](#contributing)
- [License](#license)

---

## What is PengyOS?

An operating system that is **assembled, not derived**: every component is chosen
on its own merits and justified in [`SPEC.md`](SPEC.md), the whole system rebuilds
from pinned sources, and the result is attested. It keeps Linux and the shared
contracts (POSIX, ELF, systemd unit semantics, D-Bus, Wayland, freedesktop) and
replaces only what earns replacement — *leaves first, inward second*.

The genuinely new part is not the code; it is the **process** — a continuous
human–AI loop that curates, builds, verifies and maintains the system, with a
person signing the security boundary. See [The human–AI process](#the-humanai-process--the-actual-innovation).

- **What we build** → [`SPEC.md`](SPEC.md)
- **For whom, and why** → [`USE-CASES.md`](USE-CASES.md)
- **What has shipped** → [`CHANGELOG.md`](CHANGELOG.md)

## Status

🌱 **Early — every edition installs and boots; the Desktop and Server are in use
on real hardware. Installer sticks and the Core disk image are published at
<https://pengyos.catbee.ca/download/>.** Releases are calendar versions,
`YY.MM.N` ([`VERSION`](VERSION)).

| Edition | Image (`IMAGE_ID`) | State |
|---|---|---|
| **Core** | `pengyos-core` | ✅ **On real hardware** (Intel N100): install, DHCP, key-only `sshd`, one unattended service, video console, ~224 MB idle RAM. Rebuilds byte for byte on a second build host (26.10.3). Not done yet: measured boot and in-place updates (`SPEC.md` §7). |
| **Server** | `pengyos-server` | ✅ **On real hardware** (Intel i3-1215U "behocraptop"): installs from its USB stick (console installer) and boots headless with everything Core has, the comfort-CLI set, rootless podman, the developer set (Python + pip, zsh, git, distrobox), monitoring, backup and a default-deny firewall. |
| **Desktop** | `pengyos-desktop` | ✅ **Daily use on real hardware** — Intel N100 ("behoweather", "behobasement"), Intel laptops (i3-1215U "behocraptop", i7-7600U "behothinkpad") and an AMD Ryzen 7 6800H laptop ("beholaptop"): COSMIC behind a login screen (greetd; auto-login opt-in), Wi-Fi, Bluetooth, PipeWire audio, printing, Xwayland, Vulkan, Plymouth splash, Flatpak + Flathub (Firefox, Chrome, Steam, Discord, VS Code). Graphical installer on the USB stick. |
| **Server NVIDIA** | `pengyos-server-nvidia` | 🔧 The Server plus NVIDIA's driver (compute half: CUDA, `nvidia-smi`, persistence, GPUs in podman containers through CDI) for headless GPU work. Builds and boots in QEMU; **not yet booted on an NVIDIA machine** (behoserver is the intended one). |
| **Desktop NVIDIA** | `pengyos-desktop-nvidia` | 🔧 The Desktop plus NVIDIA's driver (proprietary, on the open kernel modules) for GeForce RTX cards. Builds and boots in QEMU; **not yet booted on an NVIDIA machine** (beholaptop ran the plain Desktop image). |

Installed systems use the A/B layout of
[`docs/state-layout.md`](docs/state-layout.md): two read-only ext4 system slots
picked by GRUB, and a btrfs `data` partition for everything that is the machine.
**Updates** come from the signed feed at <https://pengyos.catbee.ca/update/>:
`pengyos-update` writes the new image into the other slot, boots it once, and
keeps it only if it comes up ([`tools/pengyos-update`](tools/pengyos-update/)).

The **toolkit** that builds and maintains all of it lives under
[`tools/`](tools/) (68 tested tools). `scripts/release.sh` builds, checks
and publishes every edition in one command; images and docs are published,
Core and Server rebuild byte for byte on a second host, and the nightly loop
(`tools/pengyos-nightly`, phase 1) watches upstream and CVE feeds. What remains
is moving COSMIC into Buildroot recipes, the rest of the nightly loop, more
hardware, and polish — see the [CHANGELOG](CHANGELOG.md) and
[`TODO.md`](TODO.md).

## Editions

One base, three additive layers:

```
        ┌──────────────────────────────────────────────┐
        │  DESKTOP   = Server + graphical stack + apps  │   the masterpiece (+ an NVIDIA image)
        ├──────────────────────────────────────────────┤
        │  SERVER    = Core  + remote-work + services   │   headless, comfortable
        ├──────────────────────────────────────────────┤
        │  CORE      = the shared, minimal, bootable base│  tiny, builds on it
        └──────────────────────────────────────────────┘
             (all three share one build engine + one verification layer)
```

Core is the *exact* base the other two are defined on — not a cut-down subset. A
Desktop component never contradicts a Core decision; it only *adds*. Each edition
is a Buildroot target in [`targets.toml`](targets.toml) with its own defconfig
and board under [`buildroot-external/`](buildroot-external/).

## Quick start

Build a target, boot it headless, then write it to a stick. The verified build
host is Ubuntu 26.04 on x86_64 with KVM, ~16 GB RAM and ~150 GB of disk. Core and
Server need nothing else from the host; the Desktop also needs COSMIC built on
the host first (see [`SPEC.md`](SPEC.md) §7).

```sh
# 0. provision a build host (once, idempotent; it calls sudo for apt-get)
scripts/provision-build-host.sh             # add --desktop for the Desktop editions
scripts/build-cosmic.sh                     # Desktop only: pinned COSMIC + our libcosmic apps

# 1. build an edition — e.g. Core. Reads targets.toml; writes under ~/build/pengyos.
cd tools/pengyos-build
python3 -m pengyos_build list  --targets ../../targets.toml
python3 -m pengyos_build build pengyos-core --targets ../../targets.toml

# 2. boot it headless under QEMU/KVM (UEFI) and assert it reaches a login prompt
cd ../pengyos-qemu-verify
python3 -m pengyos_qemu_verify boot \
    ~/build/pengyos/output/pengyos-core/images/pengyos-core-x86_64.img --uefi

# 3. or run the smoke matrix (Core by default; --matrix smoke/server.toml for Server)
python3 -m pengyos_qemu_verify smoke

# 4. write it to a USB stick (the post-dd GPT repair is mandatory — the script does it)
sudo scripts/flash-usb.sh ~/build/pengyos/output/pengyos-core/images/pengyos-core-x86_64.img /dev/sdX
```

Targets are declared in [`targets.toml`](targets.toml):
`pengyos-core`, `pengyos-server`, `pengyos-desktop`, and the opt-in
`pengyos-server-nvidia` and `pengyos-desktop-nvidia`. Each tool under [`tools/`](tools/) is a normal
Python/Rust project; run its suite with `pytest -q` or `cargo test`.

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

Every directory above has its own `README.md` explaining why it exists and what
is in it. Start there when you are looking for something.

## Documentation

| Document | What it answers |
|---|---|
| [`README.md`](README.md) | *Why* PengyOS exists — philosophy, principles, process. *(this file)* |
| [`SPEC.md`](SPEC.md) | *What* we build — the component stack and the keystone decisions. |
| [`USE-CASES.md`](USE-CASES.md) | *For whom* — the user stories whose acceptance criteria become our tests. |
| [`CHANGELOG.md`](CHANGELOG.md) | System-level milestones and notable changes. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to contribute — the three rules, conventions, and the gate. |
| [`SECURITY.md`](SECURITY.md) | How to report a vulnerability; how updates are trusted; known limitations. |
| [`docs/`](docs/) | Design & process documents — install/update architecture, the state layout, the [QEMU boot/testing runbook](docs/qemu-testing.md), the [replacement ladder](docs/stack-down-replace.md) and our [AI-contribution stance](docs/ai-contribution-stance.md). |
| [`divergence.toml`](divergence.toml) | The divergence ledger — every fork, carried patch set and host-built component, with its expiry condition. |
| [`tools/`](tools/) | The toolkit and authored components, one tested project per directory. |
| [`buildroot-external/`](buildroot-external/) | Our Buildroot tree — boards, configs, authored recipes. |

## Why this exists

PengyOS is a **Catbee** project. Catbee is not a company, and this is not a product.
There is no roadmap to a business model, no target market, and no growth metric. The
whole point is the opposite: to build and keep a computing environment that the people
who use it fully understand and fully control.

The project rests on four convictions:

1. **Freedom.** Software you can read, build, change, and keep. No opaque blobs at the
   core. No license that quietly takes the freedoms back. The measure of a choice here
   is "does this keep the user in control?" — not "is this the most popular option?"

2. **Self-hosting.** The system should be able to build, update, and explain *itself*,
   on hardware we own, with no dependency on a vendor's cloud, telemetry, or account.
   Compute that answers only to us.

3. **Reproducible and verifiable.** Anyone with the same inputs should be able to
   rebuild the same system and *check* the result. Trust comes from verification, not
   from authority. If you cannot reproduce it, you do not own it.

4. **Assembled, not invented.** The open-source world already contains most of what a
   great system needs. Our craft is in *choosing*, *integrating*, and occasionally
   *replacing* — deliberately, one component at a time — not in reinventing the
   universe. Every layer we build, we build where it earns its keep.

The one thing that makes this project genuinely new is not the code. It is the
**process**: a continuous human–AI partnership that curates, assembles, verifies, and
maintains the system — so that keeping a bespoke OS alive stops being a decade of
unpaid drudgery and becomes something a small group of augmented humans can actually
sustain.

---

## The North Star

A system where:

- Every component is **chosen on its own merits**, with a written justification —
  no component is grandfathered in just because "that's how distros do it."
- The **entire system rebuilds from source, reproducibly, with one command**, and the
  result is attested.
- **Updates are triaged, proposed, built, tested, and attested automatically** — and
  ratified by a human before they cross the security boundary.
- The result is **fast, small, and a pleasure to use** — with genuine human taste in
  the experience, not just correctness.
- **Nothing is locked in.** Every layer sits behind a stable interface and can be
  replaced without breaking the rest of the system.

If we get this right, PengyOS is not "a distro." It is a demonstration that a
self-maintaining, self-verifying, freedom-respecting system is buildable *today* by a
small human–AI team — and a place to keep applying the pattern as the tools improve.

---

## What this is — and what it is not

**It is:**
- A Linux-based OS assembled from upstream open-source components, chosen and
  justified one at a time.
- Reproducible and verifiable by construction — build, provenance, and attestation are
  features of the system, not an afterthought.
- Tailored to real hardware and real use, with human-crafted defaults.
- A living vehicle for the human–AI assembly process.

**It is not:**
- A business, a product, or a growth story.
- A general-purpose distro for everyone. It is tailored to *our* hardware and *our*
  use; others are welcome to learn from it, but "please every user" is not a goal.
- A Redox-style project that rewrites everything — including the kernel — from zero.
  We keep Linux and the shared contracts; we replace what earns replacement.
- A race to ship AI-generated code fastest. Speed of *change* is not the virtue;
  verifiability of *change* is.

---

## Principles

These are the laws of the project. When in doubt, they decide.

1. **Freedom first.** Prefer permissive licenses (MIT / Apache-2.0). Accept weak
   copyleft (LGPL) where it is dynamically linked, and strong copyleft (GPL) for
   standalone system components where that is the price of genuinely free software.
   Free software by default — but **utility beats purity**: where hardware needs a
   proprietary piece (device firmware, NVIDIA's driver) to work, ship it in plain
   sight, in its own package or image, and **document the exception and its
   reasoning** rather than hide it or leave the machine half-working. See the license note in the [component rubric](#component-selection-rubric).

2. **Assemble, don't derive.** We are *compatible* with the wider Linux world but
   *derived from no distro*. We consume upstream **sources and releases** happily; we
   do **not** inherit another distro's configuration, patch sets, or packaging
   decisions. "Compatible, not derived" is the rule. System growth flows through
   **four supply lanes** (source recipes by default; upstream artifacts; sandboxed
   Flatpak/AppImage; compatibility guest containers) — proprietary software is
   kept out of the base image; the exceptions hardware forces (firmware, the
   NVIDIA image's driver) are listed and justified.

3. **Compatibility is a contract, not a courtesy.** We reimplement *behind* stable
   interfaces and never casually break them: filesystem hierarchy and POSIX, the ELF
   and dynamic-linking model, systemd unit semantics, the D-Bus wire protocol, Wayland
   protocols, and the relevant freedesktop specs. A component may be replaced; its
   public contract may not.

4. **Reproducible by construction.** The system is defined declaratively and built
   from source. Where bit-for-bit reproducibility is achievable, we hold ourselves to
   it; everywhere else we hold ourselves to **derivation reproducibility** — the same
   inputs and the same build definition yield the same result, and the full input
   graph is recorded. The bootstrap chain (seed toolchain → system) is documented and
   auditable.

5. **Verifiable by construction.** Every image ships a **software bill of materials**
   and signed attestation. Changes are validated by build + boot + behavior tests,
   witnessed independently, and logged append-only. "It works on my machine" is not
   evidence.

6. **Contribute back by default.** We improve upstream projects and send those
   improvements home: changes are written as *upstreamable patches* wherever possible,
   tracked in `patches/` with rationale and upstream status, with a **divergence
   budget** we actively pay down. Carrying a local patch — or, unusually, a maintained
   fork — is allowed, but must be *conscious, documented, and time-bounded*, never
   accidental drift. The budget is a number, not a stance: **at most 10 maintained
   forks**, each with a named expiry condition, recorded in
   [`divergence.toml`](divergence.toml). The ladder that decides *what* we may replace
   is in [`docs/stack-down-replace.md`](docs/stack-down-replace.md); how we attribute
   and contribute outward is in
   [`docs/ai-contribution-stance.md`](docs/ai-contribution-stance.md).

7. **Proven designs, current versions.** Choose boring, well-understood *designs* — but
   track the latest *stable releases* of components (SPEC §1.6). Novelty in architecture
   is a cost, and so is falling behind; we take currency deliberately and pay for it with
   rebase discipline.

8. **Human artistry is protected.** The things that make a machine a *pleasure* —
   boot experience, default theme, sensible application curation, documentation
   voice, naming — are **human-led and AI-assisted**, never AI-decided. A machine with
   no taste is an appliance; this one should feel like a place.

9. **The human signs the boundary.** The AI may draft, propose, build, test, and
   document anything. Nothing crosses the **security boundary** — release signing,
   update acceptance, privilege change — without independent verification *and* a
   human's sign-off. A system that maintains itself must never be able to silently
   change its own trust root.

10. **Continuity lives in artifacts.** Durable knowledge — philosophy, specs, tests,
    build recipes — lives in the repository, in plain text, under version control.
    The agent memory bus is for *task recall*; the repo is for *memory that must
    outlive the instances*. If it matters, it gets written down here.

---

## The human–AI process — the actual innovation

Everything above is recognizable. The genuinely new part is **how the system gets
curated, assembled, and kept alive.** The loop is deliberately the same shape as the
one already proven on our fleet — external witness, no self-modification, human
approval — applied to an operating system:

```
   ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐
   │  OBSERVE  │──▶│  PROPOSE  │──▶│   BUILD   │──▶│   VERIFY  │──▶│  RATIFY   │
   └───────────┘   └───────────┘   └───────────┘   └───────────┘   └───────────┘
   upstream         the AI drafts   reproducible    independent      HUMAN signs
   releases,        a change +      build from      witness +        anything that
   CVEs, changelogs its justification declaration     test matrix    crosses the
   hardware drift                                     (QEMU + real)   trust boundary
         ▲                                                                   │
         └──────────────────── attest, log, learn ◀─────────────────────────┘
```

- **Observe.** Track upstream releases, changelogs, and security advisories for the
  pinned component set. Triage with the AI: *security / relevant / ignore.* This is the
  task LLMs are strongest at — reading a lot, judging relevance, and summarizing.
- **Propose.** The AI drafts the change (version bump, patch, new component) with a
  written justification, and files it. It does **not** apply it.
- **Build.** A reproducible build produces the artifact and its SBOM.
- **Verify.** An independent witness runs the boot + behavior test matrix. The AI's own
  tests are necessary but not sufficient — someone else checks the work.
- **Ratify.** A human approves anything touching the security boundary. Everything is
  logged, attested, and fed back into what the system knows about itself.

The **human–AI curation rubric** (below) is what makes "which component, and why" a
repeatable, reviewable decision rather than a mood.

---

## Verification & trust model

A self-maintaining system is a juicy target, and the honest risks are not about the
AI being *smart* enough — they are about **trust**:

- **Supply chain.** A malicious or compromised upstream. Mitigated by pinned sources,
  build-from-source, SBOM, and attestation.
- **Input poisoning.** A poisoned changelog, advisory feed, or patch. The AI's inputs
  are treated as untrusted; changes derived from them are never auto-applied.
- **Unattended self-modification.** The core risk of the whole idea. Mitigated by the
  hard rule that **the AI cannot change the system's trust root**, and by keeping the
  human as the durable signer of the boundary.
- **Verification debt.** Generating changes faster than we can test them. This — not
  code generation — is the real limiting reagent of the project. Test infrastructure
  is a first-class deliverable, and the immutable/atomic image base is the safety net
  that makes mistakes recoverable.

The guiding sentence: **the AI may draft anything; the human signs the boundary.**

---

## Composition & compatibility model

The OS is a stack of layers, each replaceable behind a stable interface. The strategy
is to **replace leaves first and move inward**, never the reverse — smallest blast
radius, cheapest to verify, easiest to revert:

| Layer | Approach |
|---|---|
| **Build / reproducibility engine** | The keystone decision — chosen explicitly, documented, and treated as a component in its own right. |
| **Leaf userspace** | Replace freely where interfaces are stable and the win is concrete. |
| **Desktop & session** | Harnessed from the modern Rust stack; where we replace, we keep Wayland / freedesktop contracts. |
| **Mid-stack** (compositor, session/services) | Replaced behind stable wire/ABI contracts, never by breaking them. |
| **Init / service layer** | Contract-compatible (systemd unit semantics); replace interiors, not interfaces. |
| **Boot chain** | Small and well-specified; a candidate for careful replacement over time. |
| **Kernel** | Kept. *Configured* per hardware, not rewritten. |
| **Web engine** | Embedded, not built. The web platform grows faster than any implementation can follow; a from-scratch browser is a non-goal. |

The ABI/interface line is the firewall: **customize above it, pin it at and below.**
Everything the wider world depends on stays stable; everything we own, we own fully.

---

## Component selection rubric

Every chosen component gets an entry in `SPEC.md` scored against the same criteria.
This rubric *is* the curation innovation; it is meant to be applied by human and AI
together, and reviewed.

- **Freedom / license** — permissive preferred; weak copyleft acceptable when
  dynamically linked; strong copyleft acceptable for standalone tools; exceptions
  (e.g. firmware) documented explicitly.
- **Maintenance & longevity** — is it alive, responsive, and likely to still be alive
  in five years? Who carries it?
- **Security posture** — CVE history and response, attack surface, language memory
  safety, sandboxing.
- **Dependency mass** — how much do we drag in with it? Fewer, deeper dependencies
  beat many shallow ones.
- **Interface stability** — does it honor a contract we can rely on, or is it a moving
  target?
- **Replaceability / blast radius** — how hard is it to swap out if it goes wrong?
- **Fit & performance** — does it actually serve our hardware and use case?
- **Modernity, spend wisely** — only claim "modern" as a reason when it buys memory
  safety, reproducibility, or maintainability. Boring is the default posture.

Each entry records the **justification**, the **alternatives considered**, and the
**reason they were rejected** — so future maintainers (human or AI) inherit the
reasoning, not just the decision.

---

## Human artistry domains

Protected. Human-led, AI-assisted. Not to be decided by a rubric or an agent:

- **Boot experience** — the first five seconds should feel considered.
- **Theme & visual identity** — a catchy, coherent default; dark/light done with taste.
- **Default application set** — a curated, complete-feeling collection so a fresh
  install is immediately productive and comfortable.
- **Desktop defaults** — keybindings, layout, sensible choices that feel *designed*.
- **Documentation voice & naming** — the project should sound like a person, not a
  committee, and not a marketing department.

AI's role here is to *implement and iterate* on human taste — not to supply it.

---

## Goals

**Near term (the foundation — doable now):**
- A declarative, reproducible, **attested** image that rebuilds one of our machines,
  boot-tested in QEMU. *(This also delivers the long-standing "the whole house could
  be rebuilt from repos" goal.)*
- An **update-triage loop**: upstream changelogs + advisories → AI classification →
  proposed change → sandboxed build/test → witness → human approval.
- A **hardware-profile generator**: detect a machine, emit a declarative profile —
  the seed of "the OS morphs around the hardware."

**Mid term:**
- Assemble a complete, pleasant daily-driver stack from chosen upstream components,
  with our defaults and theme.
- Replace a handful of leaves with in-house or modern alternatives, each behind a
  stable interface, each verified.
- Formalize SBOM + attestation across every artifact.

**Long term (the north star):**
- A distro that **self-triages, self-proposes, and self-verifies its maintenance**,
  with a human ratifying the boundary — maintained by a small augmented-human team,
  indefinitely.
- A desktop and userspace that is genuinely fast and low-memory, on our terms.
- Selective inward replacement — session, mid-stack, boot — as each earns it. The ladder, the admissibility test a replacement must pass, and the divergence budget we pay it down against: [`docs/stack-down-replace.md`](docs/stack-down-replace.md).

---

## Failure modes we design against

Named out loud so we notice them early:

- **Verification debt** — outrunning our tests. *Mitigation: test infra is a
  deliverable; changes are small and atomic; rollback always exists.*
- **Divergence debt** — forks and local patches piling up. *Mitigation: contribute-back
  default; tracked patches; a divergence budget we pay down.*
- **Trust-boundary corruption** — the maintainer compromising its own trust root.
  *Mitigation: human signs the boundary; inputs are untrusted; append-only attestation.*
- **Hardware drift** — the OS stops fitting the machines it runs on. *Mitigation:
  declarative hardware profiles, regenerated and version-controlled.*
- **Continuity loss** — the knowledge lives only in an agent's head. *Mitigation:
  everything durable is written to the repo; the memory bus is for task recall only.*
- **Scope seduction** — drifting toward "replace everything" because we can.
  *Mitigation: replace leaves first; the kernel and the browser are explicit non-goals.*

---

## Open questions

- **Name.** "PengyOS" risks confusion with the Pengy agent. Alternatives welcome; naming
  is a protected artistry domain.
- **Build/reproducibility engine.** Nix/Guix vs. declarative image tooling
  (`mkosi`/`bootc`) vs. Buildroot/Yocto vs. a hand-rolled engine. The single most
  consequential decision — recorded, with the current direction, in
  [`SPEC.md` §0](SPEC.md).
- ~~**Firmware policy.**~~ Decided: ship it (practical) — linux-firmware for the
  devices we support, and separate Server NVIDIA and Desktop NVIDIA images with
  NVIDIA's driver.
- **Package/installation model.** Declarative/system image vs. a package manager vs.
  both, and how "tailored to our hardware" interacts with updatability.
- **Where the security boundary sits**, precisely, for *this* system — the list of
  actions that always require a human signature.

---

## Working conventions

- **This repo is the durable memory.** If it matters, it lives here in plain text,
  under version control.
- **The agent memory bus is task recall**, not the source of truth for the project.
- **Every component decision is written down** — in `SPEC.md`, with its justification
  and the alternatives rejected.
- **Small, atomic, reviewable changes.** Revert is a feature.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The short version: point at a user story,
prove your change by a build/test you can reproduce, run the repo gate
(`python3 scripts/pengyos_gate.py run --repo . --mode full`), and keep changes small
and atomic. The human signs the security boundary.

## License

[MIT](LICENSE) © 2026 Pat Wendorf. Where a component must carry a different license,
the choice and its justification are recorded in [`SPEC.md`](SPEC.md).
