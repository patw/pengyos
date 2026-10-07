# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Conventional Commits](https://www.conventionalcommits.org/).

## [0.7.0] — 2026-09-16

### Added

- **`check --pub FILE`** (the final subtask — ed25519 signature
  verification against a supplied public key):
  - `pengyos_verifier/ed25519.py`: an in-tree, pure-stdlib
    implementation of RFC 8032 Ed25519 (RFC Appendix C reference;
    `publickey`/`sign`/`verify`/`is_on_curve`), pinned by the RFC
    8032 §7.1 test vectors in `tests/test_ed25519.py`. The witness
    still imports no producer tool. New `verify_file(path, sig, pub)`
    streams the message through the `H(R ‖ A ‖ M) mod l` hash so
    multi-GB artifacts are never held in memory; a missing file
    returns `False` (a witness that cannot see the evidence cannot
    pass it).
  - `signature_format.load_public_key(path)`: a `--pub` key file is
    32 raw bytes or 64 hex characters in a text file (whitespace
    tolerated — the same formats as the sibling signer/attest tools);
    anything else is a `PublicKeyError`. `signature_format.keyid_of`
    fixes the v1 binding: `keyid == SHA-256(public_key)`.
  - The `signatures` check with a supplied key additionally requires
    the header `keyid` to bind the signature to THE supplied key and
    verifies the 64-byte body with ed25519 over the artifact's bytes
    (`CheckContext.public_key`); every problem across all expected
    artifacts is still reported. Without a key the check is exactly
    the key-independent binding it was before (digest + wire format).
  - CLI: `check --build DIR ... --pub FILE`; a missing or malformed
    key file is a usage error (exit 2, checks never run — 1 stays
    "checks ran and one failed").
- `tests/test_ed25519.py` (RFC 8032 vectors both directions,
  tamper/wrong-key/malformed negatives, `verify_file` equivalence) and
  `tests/test_pub_verification.py` (key-file contract, the
  keyid-binding vs cryptographic-failure distinction, full check runs,
  CLI boundary including exit 2). Suite: 266 passed.
- Fixture helper `tests/fixtures_build.build_signed_dir` builds a good
  build dir whose `.sig` bodies are REAL ed25519 signatures (deterministic
  fixed seed); the committed byte-pinned fixtures are untouched.

## [0.6.0] — 2026-09-16

### Added

- **`test_github_ready`** (the single "test_github_ready" subtask —
  `tests/test_github_ready.py`, 12 tests): the spec's GitHub-ready
  contract is now asserted by the suite itself:
  - `README.md`, `LICENSE`, `CHANGELOG.md`, `.gitignore`,
    `pyproject.toml` all exist and are non-empty (authored, not
    scaffolded);
  - `LICENSE` is genuinely MIT: the title, "Copyright (c) 2026 Pat
    Wendorf", the permission grant, and the no-warranty disclaimer are
    all required (a renamed/truncated file fails);
  - `pyproject.toml` declares the MIT license text + classifier, the
    `pengyos-verifier` console entry point, and a `readme` field that
    points at a file that exists;
  - docs-in-sync: the CHANGELOG must carry an entry for the current
    version (lockstep with `__version__`/pyproject), the README must
    name all five check ids + the `check --build` usage + the 0/1/2
    exit-code contract + the copyright line, and every `tests/...` /
    `pengyos_verifier/...` path the README references must exist on
    disk;
  - trackability: `git check-ignore` proves none of the required files
    (or the package) would be silently dropped from a fresh clone (the
    same regression class as the fixture `build/` incident);
  - the "full green test suite" is asserted from inside by shelling out
    to `pytest -q` on `tests/` with this file excluded (no
    self-recursion) — a red suite elsewhere turns GitHub-ready red.

## [0.5.0] — 2026-09-16

### Added

- **Committed fixture build dirs + attest logs** (the single "Fixture
  build dirs (good + several bad variants)" subtask):
  - `tests/fixtures/build/good/` — a fully good build output dir, byte
    for byte the output of the reference generator
    (`tests/fixtures_build.build_good_dir`): the two M1 artifacts
    (`images/rootfs.ext4`, `images/bzImage`) with digest-correct
    `pengyos-sig-v1` sibling `.sig` files, a minimal-valid SPDX 2.3
    `sbom.spdx.json`, and a score-1.0 `repro-report.json`.
  - `tests/fixtures/build/bad/<variant>/` — six bad variants, each
    EXACTLY one mutation of the good dir: `missing-sbom`,
    `invalid-sbom`, `unsigned` (bzImage `.sig` deleted),
    `digest-mismatch` (`.sig` present but bound to the wrong sha256),
    `bad-repro` (score 0.5 < threshold 1.0), `missing-artifact`
    (bzImage gone — trips both `signatures` and `expected-files`).
  - `tests/fixtures/attest/` — one good 3-record chain
    (`good.log`) and three bad logs, each pinning exactly one walk
    failure: `tampered.log` (self-hash mismatch at seq 2),
    `gapped.log` (seq not contiguous at seq 3), `broken-linkage.log`
    (prev_hash linkage broken at seq 2).
  - `tests/test_fixture_builds.py` (25 tests) pins the committed bytes
    three ways: the good dir is byte-identical to a fresh generator
    output; every bad variant differs from the good dir by exactly its
    intended mutation (tree diff); every attest log verifies with
    exactly its intended outcome. The fixtures are also exercised at
    the CLI boundary (`check` exits 0 on good, 1 with the specific
    `[FAIL]` finding on a bad variant), so docs/examples can point at
    real on-disk inputs without any generator at test time.
  - All inputs are offline and deterministic (fixed bytes, fake but
    structurally valid signatures — no real keys; cryptographic
    ed25519 verification remains the separate `--pub` subtask). Suite:
    190 tests green.
  - `.gitignore` gains a `!tests/fixtures/build/` negation: the
    scaffold's generic `build/` rule otherwise silently ignores the
    committed fixture build tree (a green suite does not prove the
    files are trackable — `test_no_fixture_file_is_git_ignored`
    guards it with `git check-ignore`).

## [0.4.0] — 2026-09-16

### Added

- **Report aggregation + `check` CLI subcommand** (the single "Aggregate
  into a report + non-zero exit on any fail" subtask):
  - `checks_impl.build_report(ctx, check_ids=None) -> Report` folds the
    per-check findings from `run_checks` into a self-describing
    `Report` (`build`/`attest` carry the caller's labels; the verdict and
    exit code live on the report).
  - `model.Report.render()` produces the human-readable pass/fail report:
    a header (build, attest, `verdict: PASS|FAIL (n/m checks failed)`),
    then every finding in report order with a `[PASS]`/`[FAIL]` tag,
    message, and indented evidence lines.
  - `cli.py` gains the `check` subcommand
    (`check --build DIR [--attest ATTEST.log] [--json OUT]
    [--min-score F]`): runs all checks, prints `Report.render()` to
    stdout, optionally writes `Report.to_json()` to `--json OUT`, and
    returns `report.exit_code` (0 all pass, 1 any fail). A JSON write
    failure is a usage error and returns 2. `--attest` is optional —
    omitting it (or giving a missing path) makes the attest check see an
    empty chain and pass. `--min-score` lowers the repro threshold for
    triage (default stays `checks.DEFAULT_REPRO_THRESHOLD = 1.0`).
- Tests: `tests/test_report.py` (build_report aggregation, labels, report
  order, subsets, threshold, every bad variant, `render` structure/
  determinism) and `tests/test_cli.py` (parser shape, good/failing exit
  codes, `--attest` optional, `--min-score` triage, `--json OUT`
  round-trip + write-failure exit 2, `python -m` entrypoint). Suite: 165
  tests green.

## [0.3.0] — 2026-09-16

### Added

- **Check implementations** (the single "Implement each check as a
  pluggable, individually-testable function" subtask):
  `pengyos_verifier/checks_impl.py` realizes every definition in
  `checks.py` as one function
  `check_<name>(ctx: CheckContext) -> Finding`, registered in the
  `CHECK_RUNNERS` map (stable check id -> function, report order) and
  runnable individually or all at once via `run_checks(ctx,
  check_ids=None)`.
- `CheckContext` — the single input a check may need: `build_dir`,
  `attest_path` (the `--attest` log, not under the build dir), and
  `repro_threshold` (default `checks.DEFAULT_REPRO_THRESHOLD = 1.0`).
- Check behavior contract (fixed in `spec.md`, iteration 3): a check
  never raises for build-side conditions — a missing or unreadable
  target is a FAIL finding with evidence (a witness that cannot see
  the evidence cannot pass it), with the ONE exception pinned by the
  attest spec: a missing or empty attest log is a valid *empty* chain
  (PASS). The signatures and expected-files checks report ALL
  problems across all expected artifacts, not just the first.
- `tests/fixtures_build.py` — offline builder of a fully good build
  dir (fixed deterministic artifact bytes, digest-correct v1 `.sig`
  files, minimal-valid SPDX 2.3 and CycloneDX 1.5 documents, score-1.0
  repro report) plus a `make_ctx` helper; bad variants are one-file
  mutations of the good dir.
- Tests: `test_checks_impl.py` (49 tests: registry pinned to
  `checks.CHECK_IDS`, each check individually, every bad variant per
  check, a good build passes all five). Suite: 136 tests green.

### Changed

- `model.Finding` now REJECTS a bare string passed as `evidence`
  (ValueError): it would otherwise be silently iterated
  character-by-character into per-char "evidence". The 1-element
  tuple `(evidence,)` is the required form.

## [0.2.0] — 2026-09-16

### Added

- **Check definitions** (the single "Define checks" subtask): the five
  stable check ids in report order (`attest-chain`, `sbom`,
  `signatures`, `repro-score`, `expected-files`) with titles, pass
  criteria, and build-dir conventions, fixed in
  `pengyos_verifier/checks.py` (`SBOM_FILENAMES`,
  `REPRO_REPORT_FILENAME`, `DEFAULT_REPRO_THRESHOLD = 1.0`,
  `EXPECTED_ARTIFACTS`, `SIGNATURE_SUFFIX`).
- `pengyos_verifier/model.py` — the report vocabulary: `Status`,
  `Finding` (a FAIL finding must carry evidence; deterministic
  `to_dict`), and `Report` (aggregation, `ok`, `exit_code` 0/1,
  deterministic `to_json`).
- `pengyos_verifier/attest.py` — an independent re-implementation of
  the `pengyos-attest` record contract: canonical JSON + self-hash
  rule (pinned by the upstream spec's known-answer vectors), record
  schema rules, and the first-failure chain walk
  (`verify_attest_text` → `AttestReport`).
- `pengyos_verifier/signature_format.py` — the `pengyos-sig-v1` wire
  format: header parsing, whole-file structural rules, and the
  streaming sha256 the embedded digest must bind.
- `pengyos_verifier/repro_format.py` — the repro report contract
  (`score` in [0,1] not-bool, `identical` bool, optional
  `differing`/`missing` lists) + the threshold verdict.
- `pengyos_verifier/sbom_format.py` — witness-level SBOM structural
  validity: format detection and SPDX 2.3 / CycloneDX 1.5 rules
  (document fields, per-package/component fields, id/bom-ref
  uniqueness, reference resolution).
- `conftest.py` with offline chain builders and a `PENGYOS_IT=1`
  integration-mark gate.
- Tests: `test_model.py`, `test_check_definitions.py`,
  `test_attest_format.py`, `test_signature_format.py`,
  `test_repro_format.py`, `test_sbom_format.py` (86 tests green).
- New follow-up subtask: optional ed25519 `.sig` verification against
  a supplied public key (`check --pub FILE`).

## [0.1.0] — 2026-09-16

### Added

- Repository scaffold for `pengyos-verifier`: stdlib-only Python package
  (`pengyos_verifier`), `pyproject.toml` with a `pengyos-verifier` console
  entry point, `README.md`, `LICENSE` (MIT), `CHANGELOG.md`, and
  `.gitignore`.
- Working `--version` / `--help` CLI surface (`pengyos_verifier.cli`) and a
  `python -m pengyos_verifier` entry point.
- Scaffold smoke tests (`tests/test_scaffold.py`) covering the package
  skeleton, packaging metadata, and the CLI entry points.

### Notes

- The `check` subcommand (attest chain, SBOM, signatures, repro score,
  expected files), its checks, aggregation/report, fixtures, and the
  GitHub-ready assertion are implemented in later subtasks (see
  `implementation.md`).
