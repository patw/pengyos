# Changelog

All notable changes to `pengyos-sbom` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Changed
- `generate` takes the documents' `created` timestamp from
  `$SOURCE_DATE_EPOCH` when it is set (else the current time), so two builds
  of one commit embed identical SBOMs.
- SPDX `documentNamespace` prefix and the CycloneDX serial-number namespace
  move from `https://sbom.pengyos.cat` to `https://pengyos.catbee.ca/sbom`
  (the project's one URL).

### Added
- **`keygen` subcommand** (`pengyos_sbom.keys` + `cli.cmd_keygen`):
  `pengyos-sbom keygen [--out seed.key] [--pub seed.key.pub]` generates an
  ed25519 keypair with no external tooling — a fresh 32-byte seed from
  `os.urandom` plus the public key derived in-tree from it (the pair can
  never be mismatched). The seed file is written mode `0600`, the public key
  `0644`; existing files are **never** overwritten (exit 1, naming the
  offender; if either target exists, neither is written). Output is exactly
  the format `sign`/`verify` accept.

- **`tests/test_github_ready.py` — GitHub-ready contract** (per `spec.md`):
  asserts the repo is publishable as-is. Checks that README.md / LICENSE (MIT,
  "Copyright (c) 2026 Pat Wendorf") / CHANGELOG.md / `.gitignore` /
  pyproject.toml exist and are correct, that the `.gitignore` rules are
  actually effective (`git check-ignore`), that the README's CLI usage lines
  match `spec.md`'s Interface section verbatim (docs-in-sync guard), and that
  the full test suite is green — re-running `pytest` in a subprocess while
  excluding the `test_github_ready` tests themselves so the re-run
  terminates. The README was brought in sync: sign/verify usage lines now
  match the spec (`<seed-file>` / `<pub-or-seed-file>` / `<file>.sig`) and
  the exit-code table no longer describes code 3 as a live scaffold path.
- **`sign` / `verify` subcommands** (`pengyos_sbom.signing` +
  `pengyos_sbom.ed25519`): detached RFC 8032 ed25519 signing over the
  canonicalized SBOM. Ed25519 is implemented in the standard library (no
  third-party crypto deps) following RFC 8032 Appendix C and pinned by the
  RFC §7.1 test vectors. `sign FILE --key K [--out SIG]` validates the
  document first (refusing invalid ones), then writes the 64-byte signature
  to `SIG` or `FILE + ".sig"`; `verify FILE --key K [--sig SIG]` requires
  both the signature check and the structural re-validation to pass, failing
  loudly on any tampering, wrong key, or invalid document. Key files are 32
  raw bytes or one base64 string; `verify --key` accepts a signing seed and
  derives the public key. The signed body is `canonical_bytes(file bytes)`
  (`pengyos_sbom.signing`) — see the "Deterministic output" entry below for
  the timestamp/whitespace/key-order normalization it applies.
- **Deterministic / timestamp-independent signing** (`canonical_bytes` in
  `pengyos_sbom.signing`): the "Deterministic output" contract is now in
  place. `sign` and `verify` both hash `canonical_bytes(file bytes)`, which —
  for a JSON SPDX 2.3 or CycloneDX document — parses the document, normalizes
  the wall-clock creation timestamp (`creationInfo.created` /
  `metadata.timestamp`) to a fixed constant, and re-serializes canonically
  (sorted keys, compact separators, ASCII-escaped non-ASCII). A signature over
  one file therefore verifies a copy of the same document regenerated at a
  different instant (and one re-sorted/re-indented), while any change to actual
  *content* (version, license, component, or a tampered checksum) is still
  detected. Non-JSON bytes fall back to the identity (bound verbatim).
  `spec.md`, the emitter module docstrings, and the README's Determinism
  section were updated to record the real behavior; `test_signing.py` and
  `test_sign_verify.py` cover the new canonicalization and the end-to-end
  timestamp-independence of both formats.
- **`validate` subcommand** (`pengyos_sbom.validate`): structural +
  required-fields validation for both emitted formats. Auto-detects SPDX 2.3
  (by `spdxVersion`) vs CycloneDX 1.5 (by `bomFormat`), or takes
  `--format {auto,spdx,cyclonedx}` to force. Checks required document fields,
  field types, pinned spec versions (`SPDX-2.3`, CycloneDX `1.5`), SPDX id
  uniqueness (including document/package/`LicenseRef` collisions), SPDX 2.3
  relationship-type enum, ISO-8601 timestamps, creator prefixes, checksum /
  external-ref shapes, CycloneDX component-type enum, purl/cpe prefixes, UUID
  `serialNumber`, and referential integrity (`documentDescribes` and
  relationship endpoints must resolve to ids present in the document;
  CycloneDX `bom-ref`s are unique across `metadata.component` and
  `components`). Prints one line per problem on stderr and exits `1` when any
  problem is found, `0` with an `OK` line when the document is valid; invalid
  JSON and missing files also exit `1` with a clear error.
- **SPDX 2.3 JSON emitter** (`pengyos_sbom.spdx`): `generate` now produces a
  valid SPDX 2.3 tag-value JSON SBOM from the internal component model —
  document (SPDX-2.3 / CC0-1.0 / content-derived `documentNamespace`), a
  `rootfs` root artifact, one package per component (scoped ids so target and
  `host-` versions are distinct), `DESCRIBES`/`CONTAINS` relationships, and
  SHA-256 checksums of the primary saved source artifact.
- **CycloneDX 1.5 JSON emitter** (`pengyos_sbom.cyclonedx`): `generate --cdx`
  now additionally produces a valid CycloneDX 1.5 BOM from the *same* internal
  component model — `bomFormat` `CycloneDX` / `specVersion` `1.5`, a
  content-derived UUIDv5 `serialNumber`, the rootfs as the `metadata.component`
  `operating-system`, one `other` component per package (scoped `bom-ref`s so
  target and `host-` versions are distinct, `purl`s, SHA-256 `hash`, `cpe`, and
  a `distribution` external reference to the origin), license expressions shared
  with the SPDX emitter (the same `LicenseConverter` output), and any custom
  `LicenseRef` ids declared at the BOM top level.
- **License-expression converter** (`pengyos_sbom.licenses`): converts
  Buildroot `LICENSE` strings into SPDX 2.3 license expressions — commas to
  `OR`, `&&` to `AND`, the `+` suffix to the SPDX `-or-later` id,
  `<id> WITH <exception>` kept when both sides are known, and any unknown
  atom minted as `LicenseRef-<slug>` and declared in
  `hasExtractedLicensingInfos` so the document stays self-consistent.
- Legal-info + show-info ingestion and merge into an internal component model
  (`pengyos_sbom.model`, `pengyos_sbom.legalinfo`, `pengyos_sbom.showinfo`,
  `pengyos_sbom.ingest`), written against the real Buildroot 2026.02.3 output.
  `ingest(legal_info_dir, show_info_file)` yields an `IngestResult` of
  deterministically-ordered `Component`s (scoped `target`/`host`/`build`) with
  version, license string + license texts, source origin/URI, SHA-256 content
  digests of saved source artifacts, and dependency metadata. `spec.md` now
  documents the input formats and the model.
- Committed `tests/fixtures/legal-info/` and `tests/fixtures/show-info.json`
  mirroring real Buildroot `legal-info`/`show-info` output; `tests/test_ingest.py`
  (ingestion + merge + error handling).
- **Committed fixture sample is now a first-class, tested deliverable**
  (`tests/test_fixtures.py`): pins the exact fixture tree (no silent rot),
  asserts it is shaped like real Buildroot 2026.x output (unquoted header,
  fully-quoted rows, the 6-column buildroot row, sanitized license/source dir
  names), exercises every parser shape (target/host/build scopes, a package in
  both target and host, a virtual alias, a rootfs target, a `not saved` source,
  a non-redistributable package, a multi-URI download, a dependency with a
  multi-comma license, a CPE id, a saved patch + series), checks
  manifest↔show-info consistency through the real ingester, and drives the
  whole tool end-to-end (generate → validate → sign → verify for both the
  SPDX 2.3 and CycloneDX 1.5 outputs).

### Fixed
- **`verify --key` with a seed file that is itself a valid curve point**
  (~50% of random seeds): the old "if the bytes are not a curve point, treat
  as seed" fallback silently used such a seed as a public key, so the file
  that signed failed to verify about half the time. `verify` now resolves the
  key file to candidate public keys — the raw bytes when they are a valid
  curve point, plus the key derived from them as a seed — and accepts iff the
  signature verifies against any candidate
  (`signing.verification_key_candidates`); a wrong key still fails.
  Regression tests in `tests/test_signing.py` and a CLI-level round-trip in
  `tests/test_keygen.py`.

## [0.1.0] - 2026-09-13

### Added
- Repository scaffold: `pyproject.toml` (setuptools, stdlib-only, `pengyos-sbom`
  console script), `pengyos_sbom` package, `README.md`, `LICENSE` (MIT),
  `CHANGELOG.md`, `.gitignore`, pytest harness.
- CLI skeleton (`pengyos_sbom.cli`) with the full argument contract for the
  `generate`, `validate`, `sign` and `verify` subcommands, `--version`,
  normalized exit codes (0/1/2/3) and a distinct `EXIT_NOT_IMPLEMENTED` code
  for subcommands not yet implemented.
- `pengyos_sbom.constants`: single source of truth for exit codes, `.sig`
  extension, SPDX 2.3 / CycloneDX 1.5 versions.
- Scaffold unit tests (package version, CLI contract, usage errors,
  not-implemented dispatch, `python -m` entry point).
