# PengyOS seal — wire provenance into a build output (sign -> name -> repro -> witness -> verify)

## Summary
The missing glue that closes US-CORE-6 in one command: sign images/rootfs.ext4 + images/bzImage (pengyos-sig-v1), place the SBOM at the verifier's conventional root name, surface the repro report, build the witness layout, and run pengyos-verifier — every external tool call behind an injected runner.

## Purpose
SPEC.ss556 makes 'emits SBOM + signature' and the repro score Core-done criteria; USE-CASES US-CORE-6 wants a rebuild the curator can verify. On 2026-09-19 the verifier still FAILed 3/5 on a real M2 build because nothing ran the provenance tools as a closing step. This ships that step as a durable, testable tool (not a one-off script) so the wiring is repeatable across Core/Server/Desktop.

## Deliverable
A Python CLI `pengyos-seal` (stdlib-only runtime) that, given a pengyos-build output dir + a signing key, runs the provenance closure: (1) sign the expected artifacts with pengyos-sign, (2) normalise the SBOM to the verifier's conventional root name, (3) place an existing (or freshly computed) repro report at the conventional root name, (4) build the witness layout via pengyos-witness-bundle, (5) run pengyos-verifier and surface its verdict + exit code. It never builds and never writes outside the build dir.

## Interface
- pengyos-seal plan --build DIR [--json]
- pengyos-seal run --build DIR --key SEED [--pub PUB] [--attest LOG] [--prev DIR] [--json OUT]
- pengyos-seal run --build DIR --key SEED --sign-bin PATH --verify-bin PATH --witness-bin PATH --repro-bin PATH

## Verification (tests are first-class)
- `plan` is deterministic and lists the exact expected argv for a fixture build dir.
- signing invokes pengyos-sign once per EXPECTED_ARTIFACT with the right argv (golden, mock runner); a missing artifact fails cleanly.
- SBOM normalisation: present -> symlink created at the conventional name; the symlink resolves to the real SPDX doc.
- repro: present -> symlinked; absent + --prev -> pengyos-repro-report invoked then symlinked; absent + no --prev -> recorded skip, not a failure.
- verifier invocation argv golden; pass -> exit 0, any failing check -> non-zero (mapped from the verifier's own exit code).
- every external tool is mocked via the injected runner; no network/root/real artifact needed.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- On a real signed build, `pengyos-seal run` leaves the build verifier-green, or reports exactly the one residual failing check (e.g. repro with no --prev).
- `pytest -q` green; test_github_ready green.

## Non-goals
- Running Buildroot or QEMU (the seal operates on an existing build output).
- Generating the SBOM itself (pengyos-build sbom does) or measured boot / UKI (separate tool + task).
- Key management or the trust root (signing keys are a human boundary, SPEC 1.5 tier 2).

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
