# `docs/` — the project's design & process documents

This directory holds the **durable, human-readable design record** for PengyOS:
the documents that explain *how* the system is meant to be built, verified,
installed and updated — as opposed to the *what* (`../SPEC.md`) and the *why*
(`../README.md`). If a decision took more than a paragraph to justify, its
reasoning lives in one of these files.

Everything here is plain Markdown, under version control, and meant to be read
in this order if you are picking the project up cold:

| Document | What it answers |
|---|---|
| [`../README.md`](../README.md) | *Should I try this?* — the front page: what each edition is, how to install it, what's different, what isn't ready. |
| [`principles.md`](principles.md) | *Why is it built this way?* — the principles, the human–AI maintenance loop, the component rubric, the failure modes we design against. |
| [`../SPEC.md`](../SPEC.md) | *What are we building?* — the component stack and the keystone decisions. |
| [`../USE-CASES.md`](../USE-CASES.md) | *For whom, and to what end?* — the user stories whose acceptance criteria become our tests. |
| [`stack-down-replace.md`](stack-down-replace.md) | *How far down the stack do we go, and what may we fork?* — the replacement ladder, banded by verifiability; the admissibility test a replacement must pass; the divergence budget and fork policy. |
| [`ai-contribution-stance.md`](ai-contribution-stance.md) | *How do we contribute outward in a climate hostile to AI-assisted work?* — attribution convention, who signs an outward submission, and what we do when an upstream says no. |
| [`../CHANGELOG.md`](../CHANGELOG.md) | *What has shipped?* — the system-level history of each edition and release. |
| [`install-update-architecture.md`](install-update-architecture.md) | *How is PengyOS installed and updated?* — the original design. Its UKI / systemd-boot / sysupdate half stays **parked**; what shipped is the smaller GRUB + ext4 "A/B lite" in `state-layout.md` (see the banners). |
| [`state-layout.md`](state-layout.md) | *Where does a machine's state live, and how does an update work?* — **implemented** for Server and Desktop: ESP + two read-only root slots + a btrfs `data` partition, `/etc` as an overlay; `pengyos-update` writes the other slot and keeps it only if it boots. |
| [`qemu-testing.md`](qemu-testing.md) | *How do we boot and inspect an image?* — the headless boot harness, SSH into a booted guest, the serial console, driving it non-interactively, and how to screenshot a GL desktop in a VM (plus the VM-vs-real-hardware caveats). |
| [`operations.md`](operations.md) | *What has to exist around the repo for the repo to run itself?* — the signing key, the schedule, the policy file, the state dir (and which files are precious), the mailer, the model gateway, the site host, the secrets inventory, the monitoring, and the human sign-off. Every contract, no machine names, no secret values, and an honest list of what cannot be reproduced. For anyone building **their own** OS from this repo. |
| [`hdmi-audio-problems.md`](hdmi-audio-problems.md) | *What does a real hardware investigation look like here?* — a silent-HDMI case worked end to end: what was measured, which conclusions were superseded, and how the fault was narrowed to PipeWire's split-mode ALSA sink node. Kept for the method, not as a support document. |
| [`jev-model-service-viability.md`](jev-model-service-viability.md) | *Can a Jev-class decision model run here?* — the open-weight landscape, the llama.cpp `/v1/systemone` path, the static-Vulkan build recipe, measured numbers down to an integrated-graphics 16 GB laptop class, and the three Buildroot gaps. Prototype: `prototypes/pengyos-jev/`. |
| [`steamvr-quest-wireless-viability.md`](steamvr-quest-wireless-viability.md) | *Can we play SteamVR games wirelessly on the household Quest 3S?* — the 2026 streaming stack (WiVRn / ALVR / Steam Link VR), what the Desktop image already provides (kernel, udev, Avahi, firewall, PipeWire, NVIDIA 610), the four gaps behind the "not today" answer (Valve's SteamVR-in-Flatpak position, the missing Flatpak NVIDIA extension, no VR recipes, `ntsync` built but never loaded), and the ranked paths. Requirements analysis, **no measurement**: no PengyOS machine in the fleet has a VR-capable GPU. |

## Conventions

- **One decision, one document, one home.** If a doc grows a section that stops
  belonging to it, split it out rather than letting it sprawl.
- **Dates and provenance.** Process documents are dated and, where useful, cite
  the agent-memory (BotTalk) post that recorded the decision — the repo is the
  durable record, the memory bus is task recall.
- **Docs are authored, not generated.** These files are written by a person (or
  a person directing an agent); they are not build outputs.

> The per-tool design specs live next to their tools
> ([`../tools/<id>/spec.md`](../tools/)), not here. This directory is for
> system-level and process documents that span more than one tool.
