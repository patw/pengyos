# PengyOS witness-bundle — present a build output in the witness layout (US-CORE-6)

## Summary
Bundle a pengyos-build output into the canonical witness layout and verify its SBOM signature, so an external witness can actually see what was built (closing the 'witness can't find the artifact' gap).

## Purpose
SPEC 5: 'the AI's tests are necessary but not sufficient — someone else checks the work.' Today `pengyos-verifier check --build <real M2 dir>` fails because the build writes images/<target>.spdx.json while the witness looks for sbom.spdx.json, and nothing puts an artifact signature or repro report where the witness looks.

## Deliverable
A Python CLI `pengyos-witness-bundle` with `run --build DIR [--pub KEY] [--json]` that normalizes a build output into the conventional witness layout (documented aliases for the SBOM/CycloneDX/signature/repro-report), verifies the SBOM signature when a public key is given, and reports the resulting witness-ready state with a clean exit code.

## Interface
- pengyos-witness-bundle run --build DIR [--pub KEY.pub] [--link] [--json [OUT]]
  - `--pub KEY.pub` : ed25519 public key used to verify the SBOM signature (a bad
    signature is a hard fail).
  - `--link`        : materialise the witness view with symlinks (default is to copy).
  - `--json [OUT]`  : emit the machine-readable result as JSON; with no argument to
    stdout, with `OUT` write to that path.
- pengyos-witness-bundle layout [--json]   # print the canonical artifact layout it produces

`run` materialises the canonical layout under `<build>/.witness/` (copy, or `--link`
symlink of each discovered artifact), verifies the SBOM signature when `--pub` is
given, and exits `0` only when all required artifacts exist and — if a key was given
— the signature verifies.

## Verification (tests are first-class)
- good fixture -> exit 0 and a witness-ready layout is produced.
- tampered SBOM signature -> exit non-zero, tamper identified.
- empty/missing build dir -> clean failure, no traceback.
- alias discovery finds images/<target>.spdx.json and its .sig.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- `pengyos-witness-bundle run --build <real M2 dir> --pub <key>` reports the bundle witness-ready.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Reimplementing the full verifier
- Changing pengyos-build's output layout

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
