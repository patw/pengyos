# pengyos-verifier

**External witness for [PengyOS](../../README.md) builds.**

The verifier is an *independent witness*: it inspects a build output
directory and its attest log, and reports findings about

- the **attest chain** (append-only, hash-chained event log),
- **SBOM presence** and validity,
- **artifact signatures**, and
- the **repro score** (is reproducibility documented and above threshold),

plus that the **expected files** exist.

It is verification, *not* authority — the witness exists so the seed can
be untrusted. It never modifies the build, and it works offline against
fixtures.

## Install

```bash
pip install -e .            # editable dev install
pip install -e '.[dev]'     # + pytest
```

Runtime dependencies: **none** (standard library only).

## Usage

```bash
pengyos-verifier check --build DIR [--attest ATTEST.log] [--json OUT] [--min-score F] [--pub FILE]
```

- Inspects the build output dir `DIR` and the attest log `ATTEST.log`.
- Emits a pass/fail report with per-check findings (which check failed
  and the evidence) to stdout.
- Writes a machine-readable JSON report to `OUT` (optional).
- The exit code is non-zero when any check fails (0 = all pass,
  1 = any check failed, 2 = usage/IO error).
- `--attest` is optional: omitting it (or giving a missing path) makes
  the attest check see an empty chain and pass.
- `--min-score F` lowers the repro-score threshold for triage
  (default 1.0 — the SPEC's byte-identical-rebuild promise).
- `--pub FILE` supplies a 32-byte ed25519 public key (raw bytes, or 64
  hex characters in a text file). When given, the signatures check
  additionally requires each `.sig` header's `keyid` to be
  `SHA-256(public key)` and verifies the 64-byte body with ed25519
  (in-tree, pure stdlib — `pengyos_verifier/ed25519.py`, RFC 8032).
  A missing or malformed key file is a usage error (exit 2).

Also available:

```bash
pengyos-verifier --version
pengyos-verifier --help
python -m pengyos_verifier check --build DIR
```

## How it works

The five checks are **defined** in `pengyos_verifier/checks.py`
(stable ids, pass criteria, and the build-dir conventions — see
`spec.md`'s "Check definitions" section for the full contract):

| id | what it checks |
|---|---|
| `attest-chain` | `--attest` log is a valid JSONL hash chain (independent re-verification, `pengyos_verifier/attest.py`) |
| `sbom` | exactly one of `sbom.spdx.json` / `sbom.cdx.json`, structurally valid (`pengyos_verifier/sbom_format.py`) |
| `signatures` | every expected artifact has a sibling `.sig` in the `pengyos-sig-v1` wire format bound to its sha256 (`pengyos_verifier/signature_format.py`); with `--pub` the body is additionally verified with in-tree ed25519 against the supplied key (`pengyos_verifier/ed25519.py`) |
| `repro-score` | `repro-report.json` present, `score >= 1.0` by default (`pengyos_verifier/repro_format.py`) |
| `expected-files` | `images/rootfs.ext4` and `images/bzImage` exist, regular and non-empty |

Each check is implemented as a **pluggable, individually-testable
function** in `pengyos_verifier/checks_impl.py`:
`check_<name>(ctx: CheckContext) -> Finding`, registered in
`CHECK_RUNNERS` and runnable alone or together via
`run_checks(ctx)`. A check never raises for build-side conditions —
a missing or unreadable target is a FAIL finding with evidence (a
witness that cannot see the evidence cannot pass it); the one pinned
exception is the attest check, where a missing or empty log is a
valid *empty* chain. `checks_impl.build_report` aggregates the findings
into one self-describing `Report`, and the `check` subcommand prints
`Report.render()` (or writes `Report.to_json()` via `--json`) and maps
any failure to a non-zero exit code — a FAIL finding always carries
evidence pinning down the failure. A good build passes; each bad variant (broken
attest chain, missing/invalid SBOM, unsigned or digest-mismatched
artifacts, repro below threshold, missing expected files) yields a
specific FAIL finding.

## Test fixtures

Committed, deterministic on-disk inputs live under `tests/fixtures/`:

* `build/good/` — a fully good build output dir (M1 artifacts +
  digest-correct `.sig` files + valid SPDX SBOM + score-1.0 repro
  report); and
* `build/bad/<variant>/` — six bad variants (missing/invalid SBOM,
  unsigned, digest-mismatch, bad repro score, missing artifact), each
  exactly one mutation of the good dir, plus
  `attest/{good,tampered,gapped,broken-linkage}.log` attest chains.

You can point the CLI straight at them:

```bash
pengyos-verifier check --build tests/fixtures/build/good \
                       --attest tests/fixtures/attest/good.log
pengyos-verifier check --build tests/fixtures/build/bad/unsigned
```

They are byte-pinned by the test suite (`tests/test_fixture_builds.py`)
— regenerating them must reproduce the same bytes.

## Development

```bash
pytest -q                 # run the suite
```

The suite is green with no network and no root. Real-system integration
is opt-in behind `PENGYOS_IT=1`.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
