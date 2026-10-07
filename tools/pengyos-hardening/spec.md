# PengyOS hardening — a declarative security baseline + an overlay/defconfig linter

## Summary
A hardening baseline (SSH key-only + no root login, a sysctl set, nodev/nosuid where apt, a default-deny firewall that is actually enabled, no debug/telnet surfaces, a service allow-list) as committed data, plus a linter that audits the BR2_EXTERNAL rootfs-overlays and the three defconfigs against it and reports every deviation by rule id.

## Purpose
US-SRV-5 (secure remote access) + cross-cutting: the editions hand-write an sshd_config, an nftables ruleset and defconfigs today, and nothing asserts the posture is actually hardened — a reopened PermitRootLogin or a policy-accept firewall would ship silently. Turn the hardening posture into data the build is checked against, so drift fails a test instead of reaching a machine.

## Deliverable
A Python package `pengyos_hardening` with `baseline` (load + structurally validate the committed baseline), `check` (audit a repo tree — every BR2_EXTERNAL `*/rootfs-overlay/` and the `*_defconfig`s — into per-rule findings with file:line evidence), and `report` (text + JSON). Hermetically tested against committed fixture trees (a hardened one plus single-rule mutations); no network, no root, no live system.

## Interface
- pengyos-hardening baseline [--json]
- pengyos-hardening check --tree REPO_ROOT [--json OUT]
- pengyos-hardening check --tree REPO_ROOT --rule ID [--json]

## Verification (tests are first-class)
- the hardened fixture tree yields zero findings.
- each single-rule mutation yields exactly that rule's finding (and only it) — one mutation per rule.
- report output is deterministic/golden for a given tree.
- the baseline loader rejects a malformed rule (missing id / unknown domain).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- `pengyos-hardening check` on the real repo tree produces a JSON findings report, and every rule has at least one mutation test that proves it can fire.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Applying fixes (the report is advisory; a human ratifies the boundary)
- Auditing a running system or reading a live kernel (config files only)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
