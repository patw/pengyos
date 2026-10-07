# Principles, the maintenance loop, and how we choose components

The durable record of *why PengyOS is built the way it is*. This material lived in
the root [`README.md`](../README.md) until 2026-10-07, when the README became the
front page for people deciding whether to try the OS. Nothing was dropped in the
move; the wording was tightened.

Read alongside: [`../SPEC.md`](../SPEC.md) (the component decisions and the
alternatives rejected), [`stack-down-replace.md`](stack-down-replace.md) (how far
down the stack we go), [`ai-contribution-stance.md`](ai-contribution-stance.md)
(how we contribute outward), and [`../SECURITY.md`](../SECURITY.md) (what a human
has to sign).

## Why this exists

**Freedom.** Software you can read, build, change and keep. No opaque blob at the
core, no license that takes the freedoms back. The test for a component is whether
it keeps the user in control, not whether it's the most popular option.

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
   [`../divergence.toml`](../divergence.toml). Today: 0.

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

## The maintenance loop

What's new here isn't the code, it's how the system gets curated and kept alive:
the same loop already running on our fleet, with an external witness, no
self-modification, and a human approving.

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

[`../tools/pengyos-nightly`](../tools/pengyos-nightly/) is this loop applied to an
operating system: observe, triage, plan, act, one boot-checked image on the testing
channel per night, and no code path that could reach stable.

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

## How far down the stack we go

The build engine is the keystone decision, and it's Buildroot (SPEC §0). Above it,
each layer is replaceable behind a stable interface, and the order matters: leaf
userspace first, then the desktop and session, then the mid-stack, then init, then
boot. Replace interiors, not interfaces. The kernel stays, configured per hardware,
not rewritten. A from-scratch browser is a non-goal; the web platform grows faster
than any implementation can follow. The ABI line is the firewall: customize above
it, pin it at and below.

The bands, the test a replacement has to pass before we start one, and the fork
policy are in [`stack-down-replace.md`](stack-down-replace.md).

## Failure modes we design against

Named so we notice them early.

| Failure mode | What we do about it |
|---|---|
| Verification debt: generating changes faster than we can test them | Test infrastructure is a deliverable, not overhead. Changes stay small and atomic, and rollback always exists. This, not code generation, is the limiting reagent of the project. |
| Divergence debt: forks and local patches piling up | Contribute-back by default, patches tracked with upstream status, and a 10-fork budget paid down against the expiry conditions in `../divergence.toml`. |
| Trust-boundary corruption: the maintainer compromising its own trust root | The AI cannot change the trust root, and a human signs. Changelogs, advisory feeds and patches are treated as untrusted input, so nothing derived from them is applied automatically. Attestation is append-only. |
| Supply chain: a malicious or compromised upstream | Pinned sources, build from source, SBOM, attestation. |
| Hardware drift: the OS stops fitting the machines it runs on | Declarative per-machine config today (the defconfig layers and board dirs under `../buildroot-external/`); a hardware-profile generator is still a goal. |
| Scope seduction: replacing everything because we can | Leaves first. The kernel and a browser are explicit non-goals. |
