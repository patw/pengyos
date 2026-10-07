# PengyOS verifier — external witness for builds

## Summary
Independently check a build's attest chain, SBOM presence, signatures, and repro score; report findings.

## Purpose
SPEC §3/§1.5: verification, not authority — the witness that lets the seed be untrusted.

## Deliverable
A Python CLI `pengyos-verifier` that inspects a build output dir + its attest log and emits a pass/fail report with findings.

## Interface
- pengyos-verifier check --build DIR [--attest ATTEST.log] [--json OUT]
    [--min-score F] [--pub FILE]

  `--build DIR` is the only required argument. `--attest` is optional:
  the attest log is NOT under the build dir, and when it is omitted (or
  the path does not exist) the attest-chain check treats the chain as
  empty and passes — consistent with the pinned rule that a missing or
  empty log is a valid empty chain. `--json OUT` optionally writes the
  machine-readable `Report.to_json()` document. `--min-score F` lowers
  the repro-score threshold for triage (default
  `checks.DEFAULT_REPRO_THRESHOLD = 1.0`). `--pub FILE` supplies the
  32-byte ed25519 public key against which the signatures check
  cryptographically verifies the `.sig` bodies (see the `signatures`
  definition and "Public-key verification" below); a missing or
  malformed key file is a usage error — exit 2, the checks never run.

## Check definitions (fixed 2026-09-16, iteration 2 — `pengyos_verifier/checks.py`)

The five checks, in report order, with stable ids and pass criteria:

1. `attest-chain` — the attest log (given by `--attest`, NOT under the
   build dir) is a valid append-only JSONL hash chain per
   `pengyos_verifier/attest.py`: required fields `seq,ts,type,data,
   prev_hash,hash`; `seq` a real int (bool invalid) and contiguous
   from 1; `ts` canonical `YYYY-MM-DDTHH:MM:SSZ` (fractions allowed);
   `prev_hash`/`hash` 64 lowercase hex; self-hash rule
   `sha256(prev_hash ‖ canonical(record minus "hash"))` with canonical
   = sorted keys, compact separators, `ensure_ascii`; genesis
   `prev_hash = "0"*64`. The walk stops at the first failing record
   (JSON parse → schema → self-hash → seq contiguity → linkage); a
   missing or empty log is a valid empty chain. This is an
   independent re-implementation of the `pengyos-attest` contract —
   the witness never trusts the producer — and the `pengyos-attest`
   spec's known-answer vectors pin interoperability.
2. `sbom` — exactly one SBOM exists under a conventional name
   (`sbom.spdx.json` preferred, then `sbom.cdx.json`; a collision is a
   FAIL listing both), it parses as a JSON object, and it is
   structurally valid for its detected format per
   `pengyos_verifier/sbom_format.py`: `spdxVersion` → SPDX 2.3
   (document fields `spdxVersion`/`dataLicense`/`SPDXID`/`name`/
   `documentNamespace`/`documentDescribes`/`creationInfo`, per-package
   required fields, SPDX 2.3 relationship enum, id uniqueness across
   document + packages + extracted license ids, and every
   `documentDescribes`/relationship reference must resolve) or
   `bomFormat` → CycloneDX 1.5 (`specVersion`, UUID `serialNumber`,
   integer `version`, component-type enum, unique bom-refs, purl/cpe
   prefixes, hash entries carry `alg`+`content`); neither or BOTH
   markers → not a recognizable SBOM.
3. `signatures` — every expected artifact (below) has a sibling
   `<artifact>.sig` in the `pengyos-sig-v1` wire format per
   `pengyos_verifier/signature_format.py`: six-field header
   (`pengyos-sig-v1 1 ed25519 <keyid-hex> sha256 <hash-hex>`) +
   newline + exactly 64 raw signature bytes, and the embedded `hash`
   equals the artifact's actual streaming sha256 (the signature is
   bound to this exact artifact). With no key supplied this
   key-independent binding is the whole check. With a public key
   supplied (`check --pub FILE`, fixed 2026-09-16, iteration 7 — see
   "Public-key verification" below), the check additionally requires,
   per artifact, that the header `keyid` equal
   `SHA-256(public_key)` (`signature_format.keyid_of`) and that the
   64-byte body be a valid ed25519 signature over the artifact's SHA-256
   digest (per pengyos-sig-v1 / pengyos-sign FORMAT.md; the file is
   streamed to that digest, then `ed25519.verify`); a keyid mismatch
   and a cryptographic failure are distinct, separately worded FAIL
   evidence lines, and every problem across all expected artifacts is
   still reported in one finding.
4. `repro-score` — the build dir contains `repro-report.json`
   (the `pengyos-repro compare --json` document: numeric `score` in
   [0,1], bool `identical`, optional `differing`/`missing` lists) and
   `score >= threshold`. The witness default threshold is **1.0**
   (byte-identical rebuild — the SPEC reproducibility promise); a
   lower triage bar may be exposed by the CLI, never by default.
5. `expected-files` — every expected artifact exists under the build
   dir as a regular, non-empty file. M1 layout (from
   `buildroot-external` board): `images/rootfs.ext4`, `images/bzImage`.

Conventions are fixed in `checks.py` (`SBOM_FILENAMES`,
`REPRO_REPORT_FILENAME`, `DEFAULT_REPRO_THRESHOLD`,
`EXPECTED_ARTIFACTS`, `SIGNATURE_SUFFIX`) so the implementations and
the tests cannot drift.

## Public-key verification (fixed 2026-09-16, iteration 7 — `pengyos_verifier/ed25519.py`)

The witness verifies ed25519 signatures with an **in-tree, pure-stdlib**
implementation of RFC 8032 (RFC Appendix C reference: complete
extended-coordinate twisted-Edwards arithmetic, double-and-add scalar
multiplication, `a &= (1<<254)-8; a |= 1<<254` clamping, SHA-512 via
`hashlib`): `publickey(seed)`, `sign(message, seed)` (deterministic),
`verify(message, sig, pub)`, `verify_file(path, sig, pub)` (the message
is streamed through the `H(R ‖ A ‖ M) mod l` hash — multi-GB artifacts
are never held in memory; a missing file is `False`), and
`is_on_curve(pub)`. Correctness is pinned in THIS suite by the RFC 8032
§7.1 test vectors (`tests/test_ed25519.py`): `publickey`/`sign`
reproduce the RFC bytes exactly and `verify`/`verify_file` accept them;
negatives cover tampered messages/signatures, wrong keys, malformed
lengths, and off-curve points. The witness imports no producer tool.

`check --pub FILE` loads the key with
`signature_format.load_public_key`: a file of exactly 32 raw bytes, or
a text file holding the 64-character hex of those bytes (case-insensitive,
surrounding whitespace tolerated) — the same two key-file formats as
the sibling signer/attest tools. Anything else is a `PublicKeyError`,
and a missing file a `FileNotFoundError`; the CLI turns both into a
usage error (stderr message + exit 2) BEFORE any check runs. The
supplied key travels to the checks as `CheckContext.public_key` (raw
bytes, `None` when no `--pub` was given); the checks themselves never
raise for key conditions — the key was either loaded or the CLI exited
2.

Report vocabulary (`pengyos_verifier/model.py`): each check yields a
`Finding(check, status, message, evidence)` — a FAIL finding MUST
carry evidence pinning down the failure (which file/seq/field); a
`Report(build, attest, findings)` aggregates them, `ok` iff no FAIL,
`exit_code` 0/1, and `to_json()` is deterministic (sorted keys).
`Finding` rejects a bare string passed as `evidence` (ValueError) —
it would otherwise be silently iterated character-by-character into
per-char evidence; the 1-element tuple `(evidence,)` is the form.

## Check implementations (fixed 2026-09-16, iteration 3 — `pengyos_verifier/checks_impl.py`)

Each definition above is one **pluggable, individually-testable
function** `check_<name>(ctx: CheckContext) -> Finding`, registered in
`CHECK_RUNNERS` (stable check id -> function, in `CHECK_IDS` report
order) and runnable individually or via `run_checks(ctx, check_ids=
None)` (unknown id -> `KeyError`). `CheckContext` is the single input
a check may need: `build_dir`, `attest_path` (the `--attest` log —
NOT under the build dir), and `repro_threshold` (default
`checks.DEFAULT_REPRO_THRESHOLD`).

Behavior contract, fixed here because the definitions implied it but
did not state it:

* A check NEVER raises for build-side conditions — a missing or
  unreadable target is a FAIL finding with evidence (a witness that
  cannot see the evidence cannot pass it).
* The ONE exception, already pinned by the attest definition: a
  missing or empty attest log is a valid empty chain (PASS).
* `signatures` and `expected-files` inspect ALL expected artifacts
  and report every problem in one FAIL finding (paths in
  `EXPECTED_ARTIFACTS` order), not just the first; their PASS
  evidence names each artifact (with its size, for
  `expected-files`).
* `attest-chain` FAIL evidence names the log path, the 1-based line,
  the walk reason (and the seq when the failing line is a parseable
  record), matching `attest.AttestReport`.
* `repro-score` FAIL evidence names the path with the actual score
  and the threshold used (so a triage threshold is visible in the
  report).

Test fixtures: `tests/fixtures_build.py` builds a fully good build
dir offline (fixed deterministic artifact bytes, digest-correct v1
`.sig` files, minimal-valid SPDX 2.3 and CycloneDX 1.5 documents,
score-1.0 repro report); bad variants are one-file mutations of the
good dir.

## Committed fixtures (fixed 2026-09-16, iteration 5 — `tests/fixtures/`)

The generator's output is COMMITTED as deterministic on-disk inputs, so
the CLI, docs, and future iterations work against the same bytes with no
generator at test time. Layout:

* `tests/fixtures/build/good/` — a fully good build dir, byte-for-byte
  the output of `build_good_dir`: the M1 artifacts
  (`images/rootfs.ext4`, `images/bzImage`), digest-correct v1 `.sig`
  siblings, `sbom.spdx.json` (minimal-valid SPDX 2.3), and
  `repro-report.json` (score 1.0).
* `tests/fixtures/build/bad/<name>/` — six variants, each EXACTLY one
  mutation of the good dir (pinned by a tree diff against good):
  `missing-sbom` (fails `sbom`), `invalid-sbom` (SPDX marker only —
  fails `sbom`), `unsigned` (bzImage `.sig` deleted — fails
  `signatures`), `digest-mismatch` (bzImage `.sig` embeds the wrong
  sha256 — fails `signatures`), `bad-repro` (score 0.5 — fails
  `repro-score` at the default 1.0 threshold), `missing-artifact`
  (bzImage gone — fails BOTH `signatures` and `expected-files`, since a
  missing artifact cannot carry a bound signature).
* `tests/fixtures/attest/good.log` — a valid 3-record chain; and three
  bad logs, each pinning exactly one walk failure:
  `tampered.log` (record 2's data edited after hashing — "self-hash
  mismatch" at seq 2), `gapped.log` (well-formed record claiming seq 3
  with correct linkage — "seq not contiguous"), `broken-linkage.log`
  (record 2's prev_hash forged, hash recomputed — "prev_hash linkage
  broken").

Contract pinned by `tests/test_fixture_builds.py`: the committed good
dir equals a fresh `build_good_dir` output byte-for-byte; every bad
variant's tree diff against good is exactly its intended mutation (no
extra files, nothing silently drifted); every attest log verifies with
exactly its intended `AttestReport`; the fixtures also pass/fail at the
CLI boundary (`check` exit 0 on good, 1 with the specific `[FAIL]`
finding on a bad variant); and no fixture file is git-ignored (the
scaffold's generic `build/` .gitignore rule is negated for
`tests/fixtures/build/`, guarded by `git check-ignore` in the test).
Regenerating the fixtures must reproduce these bytes exactly — any
change is a pinned-contract change.

## Report aggregation and exit codes (fixed 2026-09-16, iteration 4)

`checks_impl.build_report(ctx, check_ids=None) -> Report` folds the
findings from `run_checks` into a self-describing
`model.Report(build=ctx.build_dir, attest=ctx.attest_path,
findings=...)`. The verdict is `Report.ok` (True iff no FAIL finding);
the exit code is `Report.exit_code` (0 all pass, 1 any fail) — a single
failed check fails the whole build. The `check` subcommand (cli.py)
builds this report, prints `Report.render()` (human: a header with
build/attest/`verdict: PASS|FAIL (n/m checks failed)`, then every
finding in report order with a `[PASS]`/`[FAIL]` tag, its message, and
indented evidence lines) to stdout, writes `Report.to_json()` when
`--json OUT` is given, and returns `report.exit_code`. A failure to
write the JSON file, or a missing/malformed `--pub` key file, is a
usage/IO error and returns 2 (argparse also exits 2 on bad usage); the
`--pub` key file is loaded before any check runs, so exit 2 there means
nothing was verified; exit 1 is reserved for "checks ran and one
failed".

## Verification (tests are first-class)
- good fixture → PASS.
- each bad variant (broken chain, missing SBOM, unsigned, bad repro) → specific FAIL finding.
- exit code reflects failures.

**GitHub-ready contract** (asserted by `tests/test_github_ready.py`,
iteration 6):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them
in sync. Behavior fixed: the "full green test suite" is asserted from
inside by running `pytest -q` on the rest of `tests/` in a subprocess
(with the GitHub-ready file excluded, so the check cannot recurse into
itself); "docs in sync" is pinned as (a) the CHANGELOG carrying an entry
for the current version and (b) every `tests/...` / `pengyos_verifier/...`
path referenced by the README existing on disk; a fresh clone must
contain every required file (`git check-ignore` proves none is ignored).

## Acceptance criteria
- Reports pinpoint the failing check with evidence.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Doing the build

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
