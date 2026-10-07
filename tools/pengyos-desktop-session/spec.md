# PengyOS desktop-session — the COSMIC session + a VM-testable smoke matrix (US-DSK-1)

## Summary
A declarative COSMIC-session manager for the Desktop edition: greetd/cosmic-greeter -> cosmic-session startup as deterministic systemd + session files, plus a check engine for the session facts QEMU+OVMF can actually verify (cosmic-comp started, a wayland socket exists, cosmic-session/panel up, pipewire running, portals respond) — with GPU/audio/BT explicitly out of scope.

## Purpose
USE-CASES US-DSK-1: boot to a usable session. The COSMIC session must be declarative and reproducible, and the in-VM-testable subset must be a real check, with the untestable-in-VM facts (GPU, audio, Bluetooth, suspend) named honestly (SPEC 8).

## Deliverable
A Python tool `pengyos-desktop-session` that (a) emits the COSMIC session definition as deterministic files — a greetd config + cosmic-greeter session entry + the cosmic-session systemd unit/launcher + environment — into a target overlay dir, and (b) checks the VM-testable session facts over a boot log or an opt-in ssh probe (PENGYOS_IT=1).

## Interface
- pengyos-desktop-session emit --out DIR [--json]
- pengyos-desktop-session check --log FILE [--json]
- pengyos-desktop-session check --ssh HOST [--json]   (opt-in, PENGYOS_IT=1)

## Verification (tests are first-class)
- emitted session files are golden + deterministic across runs.
- check engine present/absent semantics; a missing present pattern fails.
- out-of-scope facts are reported, not dropped.
- no network/root/display/hardware needed for the unit suite.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Emits a valid, deterministic COSMIC session definition; checks green on a committed fixture.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Real GPU/audio/Bluetooth/suspend (real hardware, SPEC 8)
- Building the image, the compositor, or the cosmic recipes

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
