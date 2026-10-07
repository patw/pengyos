# PengyOS net — baseline network config + validator

## Summary
Baseline systemd-networkd/resolved + nftables configs with a validator.

## Purpose
SPEC §2.2: a sane, secure default network posture.

## Deliverable
A repo with `networkd/*.network|netdev`, `nftables/pengyos.nft`, and a pytest validator (syntax + policy: default-deny inbound, expected open ports).

## Interface
- files: networkd/, nftables/
- tests: pytest config validator

Discovered conventions (recorded 2026-09-16):
- The resolved drop-in lives under `networkd/resolved/90-pengyos.conf`
  (installed to /etc/systemd/resolved.conf.d/), keeping all resolver
  config inside the networkd/ tree per the files: interface above.
- The baseline ships an offline `dummy` link `pengyos-test0`
  (10.99.0.1/24) specifically so integration tests can exercise the
  stack without hardware; it must never be matched by the default unit.
- The resolved FallbackDNS must never be 127.0.0.53 (the stub listener
  itself — a loop); per-link DNS from DHCP/RA is the source of truth.
- The baseline nftables ruleset (`nftables/pengyos.nft`, applied with
  `nft -f` as root) is default-deny inbound: `input` and `forward`
  chains use `policy drop`; `forward` is drop because PengyOS is not a
  router by default; `output` is `policy accept` (baseline convenience
  — outbound hardening is a deliberate follow-up, not part of this
  scope). The inbound new-connection allow list is exactly SSH
  (tcp/22); loopback and established/related pass, invalid is dropped.
  The ruleset starts with `flush ruleset` so it is self-contained.
- The `nft` binary is NOT required (or present) at test time: syntax
  and policy are validated offline by a stdlib structural parser
  (`nft -c` needs privileges anyway). nft grammar notes the parser
  relies on: statements end at `;` **or a newline**; blocks are
  `{ header { … } }` keyed by the last word of the header; comments are
  `#`, `//`, and `/* … */`.
- The PENGYOS_IT=1 integration test (tests/test_integration.py)
  needs MORE than the env var: root AND a systemd-networkd host; it
  skips at runtime otherwise. Only the two 20-pengyos-test units are
  ever installed (never the baseline default unit). Its bring-up state
  machine (install units → start-or-reload networkd → poll link
  UP+dummy+10.99.0.1/24 → teardown link/units/reload in a finally)
  is unit-tested offline via an injected command runner + fake clock;
  the real runner maps `install-unit`/`uninstall-unit` pseudo-commands
  to file copy/delete in /etc/systemd/network. `ip -o link show`
  output is ONE physical line per device with continuations joined by
  a literal backslash — the parser un-escapes before matching, and the
  header is `N: <dev>: <flags>` (space between the `:` and `<`).
- The validator lives in `pengyos_net/nft_validate.py` and exposes
  `validate_ruleset_text(text)` / `validate_ruleset(path)` returning a
  `ValidationResult` (`.ok`, `.errors`, `.root`). Policy checks, all
  reported as a list (not fail-fast): top-level `flush ruleset`;
  exactly one table `inet pengyos` with exactly input/forward/output;
  input+forward `policy drop`; output `policy accept`; input chain may
  contain only the pinned statements; dport allow list == {tcp/22};
  **no `accept` before the allow table** — any accept verdict in a
  top-level statement or in any block preceding the
  `table inet pengyos` block in source order (nested blocks count
  recursively) is a violation; accepts inside/after the allow table are
  fine. Syntax errors short-circuit (result has no parsed root).

## Verification (tests are first-class)
- valid configs pass.
- missing default-deny → fail.
- unexpected open port → fail.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Policy is enforced by tests.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Applying rules

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
