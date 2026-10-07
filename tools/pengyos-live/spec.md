# PengyOS live — the live/installer session overlay (boot to a desktop, installer running)

## Summary
Emit the live-session overlay for an edition's install medium: an auto-login live user, the installer launched on session start (graphical when a session exists, console fallback otherwise), a live marker, and a lint that the medium carries the installer and actually autostarts it.

## Purpose
'The desktop version should boot into a live desktop environment with the installer running by default.' There is no live concept today — the Core installer copies the running stick. Making the live session declarative + offline-testable is the front door of the whole install UX.

## Deliverable
A Python package `pengyos_live`: `emit` (the live overlay: an autologin drop-in for the graphical/console getty, the installer autostart as a session entry + a systemd unit that runs it on the live session, a live-user/sudo policy (a sudoers drop-in letting the live user run the installer passwordless), and `/etc/pengyos/live`), and `lint` (installer present AND wired to autostart; autologin present; live marker present; live-user sudo policy present; the live path does not write the target disk before the user confirms).

## Interface
- pengyos-live emit --edition desktop|core|server --session cosmic|console --out DIR [--json]
- pengyos-live lint --overlay DIR [--json]

## Verification (tests are first-class)
- emit is golden/deterministic for desktop+cosmic and desktop+console.
- lint fails when the installer is absent, not autostarted, or when autologin/marker is missing.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A lint-clean live overlay autostarting the installer for both session types; every rule has teeth.
- `pytest -q` green; test_github_ready green.

## Non-goals
- The installer itself (pengyos-install) and the session stack (pengyos-desktop-session)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
