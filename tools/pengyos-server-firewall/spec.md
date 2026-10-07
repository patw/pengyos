# PengyOS server-firewall — nftables profile + WireGuard generator (US-SRV-5)

## Summary
The Server network posture: a default-deny nftables ruleset exposing only declared ports, plus a WireGuard config generator for remote access — private keys never leak.

## Purpose
USE-CASES US-SRV-5: only intended ports are exposed; the WireGuard tunnel works; brute-force attempts are mitigated.

## Deliverable
A repo with `nftables/server.nft`, a `pengyos-netctl` CLI emitting a validated ruleset + a wg0 server/peer config pair from a small spec, and a pytest validator (default-deny, declared-ports only, 0600 private keys, no key material in rendered output).

## Interface
- pengyos-netctl rules --config ports.yaml --out server.nft
- pengyos-netctl wg --spec wg.yaml --out wg/
- pengyos-netctl validate --rules server.nft

## Verification (tests are first-class)
- default-deny enforced; only declared ports open.
- no private-key bytes appear in any rendered output.
- peer/address allocation is deterministic; a bad wg spec fails cleanly.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- The ruleset exposes only declared ports; private keys never leak.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Applying rules or bringing up the tunnel

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
