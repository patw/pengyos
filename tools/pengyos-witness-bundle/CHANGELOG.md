# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Discovery now matches what `pengyos-build` actually emits.** The alias table
  only knew `images/*.cyclonedx.json`, but the build writes `images/<target>.cdx.json`,
  so the CycloneDX document was reported missing. Added the `*.cdx.json` aliases.
- **`repro_report` canonical renamed `repro-report.txt` -> `repro-report.json`**, to
  match the name the external witness (`pengyos-verifier`, `REPRO_REPORT_FILENAME`)
  and `pengyos-repro-report` use. Kept the legacy `.txt` names as lower-priority
  aliases so older outputs still resolve. (Found 2026-09-19 by running the tool
  against the real M2 build: `sbom`+`signature` OK, `cyclonedx` wrongly missing.)

### Added

- GitHub-ready contract asserted by `tests/test_github_ready.py` (the
  `test_github_ready` task). It locks in spec.md's contract with checks that can
  silently regress: `README.md` present and *authored* (≥300 chars, mentions the
  tool and "witness"), `LICENSE` MIT with the "Copyright (c) 2026 Pat Wendorf"
  line, `CHANGELOG.md` present with a versioned section, `.gitignore` present with
  at least one real entry, `pyproject.toml` valid and declaring the
  `pengyos-witness-bundle` console script + an MIT license, and a **green suite**
  (every `tests/test_*.py` module imports cleanly and the package surface
  (`__version__`, `cli.main`) is importable). `test_github_ready_contract`
  consolidates the gate. Proven to catch a stub README (and to pass once restored).

- Shared, reusable build-dir test fixtures (`conftest.py`): `good_build` (all four
  canonical artifacts present — dummy signature, so witness-ready with no `--pub`
  → **PASS**), `signed_build` (a good build whose SBOM is signed with the in-tree
  Ed25519 → verifies with `--pub` → **PASS**), `tampered_build` (a good build whose
  SBOM body is corrupted *after* signing → signature no longer matches → **FAIL**),
  `partial_build` (only SBOM + signature present → not witness-ready → **FAIL**),
  `empty_build` (nothing present → every kind missing → **FAIL**), and `pub` (the
  `signer`'s public key written to a file). The suite now leans on these instead of
  duplicated inline helpers. `tests/test_fixtures.py` is the dedicated group that
  pins each fixture's PASS / FAIL behaviour through the `run` CLI.

- Canonical witness layout defined as data (`pengyos_witness_bundle/layout.py`):
  an ordered `CANONICAL_LAYOUT` of `ArtifactSpec` rows — one per artifact kind
  (`sbom`, `cyclonedx`, `sbom_signature`, `repro_report`) — each carrying its
  conventional `.witness/` name and the ordered alias globs `pengyos-build`
  actually emits (e.g. `images/<target>.spdx.json`). `ArtifactSpec.matches` does
  POSIX-normalised glob matching; `spec_for`/`kinds`/`canonical_names` expose the
  table; `iter_files`/`find_matching` are the discovery primitives task 3 builds
  on. `cli.CANONICAL_LAYOUT` now imports this authoritative definition.

- Artifact discovery (`pengyos_witness_bundle/discovery.py`): `discover(build_dir)`
  walks a build dir once and, for every kind in the canonical layout, takes the
  first alias that matches an existing file, returning a `DiscoveryResult`
  (`build_dir`, `artifacts`, `present`, `missing`, `complete`). Each kind's
  outcome is a frozen `DiscoveredArtifact` (kind, canonical, found, path,
  aliases). Missing artifacts are reported, never raised — a missing dir / empty
  dir / file-as-root yields a clean complete-empty result. `DiscoveryResult`
  exposes `complete` and a `missing_message()` that lists the expected canonical
  names; `missing_kinds`/`present_kinds` are thin conveniences.

- SBOM signature verification (`pengyos_witness_bundle/sigverify.py`): verifies
  the detached ed25519 signature that `pengyos-build` writes next to each SBOM
  (`<sbom>.sig`) against an ed25519 public key. Prefers `pengyos-sbom`'s own
  `verify` when importable; otherwise falls back to a documented, stdlib-only
  pure-Python Ed25519 (RFC 8032, SHA-512 only) that mirrors `pengyos-sbom`'s
  reference and is pinned by the RFC 8032 §7.1 test vectors. `canonical_bytes`
  binds the SBOM's *semantic* content — SPDX `creationInfo.created` / CycloneDX
  `metadata.timestamp` are normalized out and the body is re-serialized canonically
  (sorted keys, compact separators, ASCII-escaped) so a signature survives
  re-serialization and timestamp drift, while any content change is detected.
  Key files accept raw 32-byte keys or a single base64 string; a seed and its
  derived public key are both tried (`public_key_candidates`). `verify_file`
  returns `(verified, method)` and raises `VerifyError` (a hard fail) on a
  missing/malformed input or a bad signature. The `run` CLI now wires `--pub`
  through it: it reports `OK`/`FAILED` via the `pengyos-sbom` or `stdlib-ed25519`
  backend and exits non-zero on any verification failure.

- Witness-ready view (`pengyos_witness_bundle/bundle.py`): `bundle(build_dir,
  discovery, mode=...)` materialises the canonical layout under `<build>/.witness/`
  by copying (default) or symlinking (`mode="symlink"`) each **discovered** artifact
  to its conventional name — so whatever `pengyos-build` emitted as
  `images/<target>.spdx.json` also exists at the conventional name the witness looks
  for. Present kinds are bundled in canonical order; missing kinds are reported in
  `BundleResult.skipped` (never raised). Bundling is idempotent (existing
  destinations are overwritten). The `run` CLI now wires it in via `--link` and
  includes the view (`witness_dir`, `mode`, `bundled`, `skipped`) in the
  machine-readable result; a structural failure is a clean runtime error (exit 1),
  never a traceback.

### Fixed

- `find_matching` (discovery primitive in `layout.py`) no longer lets a broad
  `*.spdx.json` alias shadow the real SBOM. It previously iterated files in
  sorted path order and returned the first file matching *any* alias, so a
  spurious host-package SPDX doc (e.g. `host-python3-.../Misc/externals.spdx.json`,
  which sorts before `images/`) won over the real `images/<target>.spdx.json`. Now
  it prefers the file matching the lowest-index (most specific) alias
  (`images/*.spdx.json` over `*.spdx.json`); ties on the same alias break by
  sorted path order, keeping discovery deterministic. This closes the
  "witness can't find the artifact" gap against the real M2 build.

- `run --json` never emitted JSON: its `--json` argument used `nargs="?"` with both
  `const=""` and `default=""`, so `args.json` was always the falsy `""` and the
  human table was printed instead. Fixed to `const="stdout"` (and `default=""`), so
  `--json` prints JSON to stdout and `--json OUT` writes it to `OUT`. Also fixes the
  `run` display: a `--pub` that hard-fails now reports `FAILED (hard fail — see
  stderr)` instead of the misleading `(no --pub given)` (the signature result is
  `None` both when `--pub` is absent and when it fails, so `signature_requested`
  now tracks which case we're in).

## [0.1.0] — 2026-09-18

### Added

- Repository scaffold: `pyproject.toml` (stdlib-only, `pengyos-witness-bundle`
  console entry point), `pengyos_witness_bundle/` package (`cli`, `__main__`),
  pytest suite, and GitHub-ready docs (`README.md`, `LICENSE`, `CHANGELOG.md`,
  `.gitignore`).
- `pengyos-witness-bundle layout` subcommand: prints the canonical witness artifact
  layout (human table or `--json`).
- Documented canonical witness layout: `sbom.spdx.json`, `sbom.cyclonedx.json`,
  `sbom.spdx.json.sig`, `repro-report.txt`.
