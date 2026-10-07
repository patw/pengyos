# PengyOS SBOM — Buildroot manifests → SPDX (+ CycloneDX)

## Summary
Turn Buildroot `legal-info` (a directory of manifest CSVs + saved license texts
and source artifacts) and `show-info` (a per-package JSON document) into a valid
SPDX 2.3 SBOM (and CycloneDX), with signing/verification.

## Purpose
Provenance is a first-class feature (SPEC §1/§3): every artifact ships verifiable bill-of-materials.

## Input formats (Buildroot 2026.x: 2026.02.3 and 2026.08)

`pengyos-sbom` consumes the *real* output of a Buildroot build. These shapes
were reverse-engineered from the pinned Buildroot trees on the build host;
they are the contract the parsers must honor (see
`pengyos_sbom/legalinfo.py` and `pengyos_sbom/showinfo.py` docstrings).
The legal-info/show-info generation code was diffed **verbatim** between
2026.02.3 (initial M1 pin) and 2026.08 (current-line engine per the PengyOS
version policy) and is identical, so the parsers cover both.

### `make legal-info` → a **directory**
- `manifest.csv` — target packages; `host-manifest.csv` — host (build-time)
  packages plus one special 6-column `buildroot` row.
- Both CSVs have one unquoted header line
  `PACKAGE,VERSION,LICENSE,LICENSE FILES,SOURCE ARCHIVE,SOURCE SITE,DEPENDENCIES WITH LICENSES`
  then one fully-quoted row per package. `LICENSE` folds `" ,"`→`","`; the
  `DEPENDENCIES WITH LICENSES` column is space-separated `name [license]` pairs
  where the bracketed license may itself contain commas/spaces; `not saved`
  cells mean absent (local/override/non-redistributable packages).
- License texts: `licenses/<name>-<ver>/<file>` (target) and
  `host-licenses/...` (host). `<ver>` is the *sanitized* version (space, `:`,
  `/` → `_`).
- Saved sources: `sources/<name>-<ver>/...` and `host-sources/...` — the
  original download, extra downloads, applied patches and a `series` file, for
  redistributable packages only.
- The legal-info CSVs carry **no content digests**. The real per-component
  digests are therefore computed by SHA-256 over the saved source artifacts;
  `Component.source_hash` selects the primary (original tarball) digest.

### `make show-info` → a **JSON object** keyed by lower-case package name
- Keys are `<pkg>` for target packages, `host-<pkg>` for host packages, and the
  rootfs target names (e.g. `rootfs-ext4`).
- Each package entry has `type` (`target`/`host`/`rootfs`), `name` (the RAWNAME,
  `host-` prefix stripped), `virtual`, and for non-virtual packages
  `version`, `licenses` (free-form, e.g. `"MIT, GPL-2.0+"`), `license_files`,
  `downloads[]` (`{source, uris[]}`), `hashes[]` (paths to Buildroot `.hash`
  files — **not** digests), `patches[]`, `provides[]`, `cpe-id`,
  `dependencies[]`, `reverse_dependencies[]`. Rootfs entries carry only
  `type`, `image_name`, `dependencies`.

## Internal component model

`pengyos_sbom.model` defines the canonical model that every emitter consumes:
- `Component(name, scope, version, licenses, license_files, license_texts,
  source_site, archive, origin, downloads, hashes, patches, provides, cpe_id,
  dependencies, redistributable, virtual, package_dir)` with `scope`
  ∈ `target`/`host`/`build`. Keying on `(scope, name)` is required because a
  name can appear in both the target and host manifests.
- `IngestResult(components, rootfs, warnings, buildroot_version,
  skipped_virtual)` — deterministically ordered (build < host < target, then
  name). Manifest rows are authoritative for name/scope/version/license;
  show-info only enriches (origin URI, patches, provides, cpe-id, deps).
  Virtual show-info aliases are not components; rootfs targets are not
  components (they become the SPDX root artifact).

## SPDX 2.3 output contract

`generate` (and `pengyos_sbom.spdx.build_spdx_document`) turn an
`IngestResult` into an SPDX 2.3 tag-value JSON document. Fixed decisions:

- **Document**: `spdxVersion` `SPDX-2.3`, `dataLicense` `CC0-1.0`, `SPDXID`
  `SPDXRef-DOCUMENT`, `documentDescribes` = `[SPDXRef-RootArtifact]`, and a
  content-derived `documentNamespace` under `https://pengyos.catbee.ca/sbom/` (hashes
  every component + rootfs so two different SBOMs never share a namespace).
- **Root artifact** (`SPDXRef-RootArtifact`): the first `rootfs` target becomes
  a package named after its `image_name` (falling back to the target name),
  `primaryPackagePurpose` `OPERATING-SYSTEM`, `versionInfo` = the Buildroot
  version. With no rootfs target a synthetic `PengyOS rootfs` package is used.
- **Packages**: one per component, id `SPDXRef-Package-<scope>-<name>` (so a
  target and its `host-` version are distinct). `licenseConcluded` and
  `licenseDeclared` carry the converted license expression; `downloadLocation`
  is the origin URL or `NOASSERTION`; the single `checksums` entry is the
  SHA-256 of the component's primary saved source artifact (`source_hash`),
  present only when a source was saved; a CPE `externalRef` is added when a
  `cpe-id` is known; `primaryPackagePurpose` is `OTHER` (we cannot classify
  each package) and `filesAnalyzed` is `false`.
- **Relationships**: one `SPDXRef-DOCUMENT --DESCRIBES--> SPDXRef-RootArtifact`,
  plus one `SPDXRef-RootArtifact --CONTAINS--> <package>` per component.
- **License expressions** (`pengyos_sbom.licenses.LicenseConverter`): Buildroot
  `LICENSE` is not SPDX — top-level commas become `OR`, `&&` becomes `AND`
  (grouped in parens when nested under an `OR`), the `+` suffix maps to the
  SPDX `-or-later` id, and `<id> WITH <exception>` is kept when both sides are
  known SPDX ids. Any unrecognizable atom is minted as `LicenseRef-<slug>` and
  declared once in the document's `hasExtractedLicensingInfos` so the document
  stays self-consistent. Empty input → `NOASSERTION`.
- **Determinism**: `build_spdx_document(result, created=...)` takes the
  creation timestamp as a parameter (the CLI fills it with the current UTC
  time) and serializes with sorted keys, so identical inputs give byte-identical
  output. Components are already deterministically ordered by ingestion, package
  ids and the document namespace derive from content, so the only run-varying
  byte is `creationInfo.created`. The signing canonicalization step (normalizing
  that timestamp out of the hashed body) lives in `pengyos_sbom.signing` — see
  the Signing contract below.
- **CycloneDX** is emitted from the same model by
  `pengyos_sbom.cyclonedx.build_cyclonedx_document`; see the
  "CycloneDX 1.5 output contract" below.

## CycloneDX 1.5 output contract

`generate --cdx` (and `pengyos_sbom.cyclonedx.build_cyclonedx_document`) turn
the same `IngestResult` into a CycloneDX 1.5 JSON BOM. Fixed decisions:

- **BOM**: `bomFormat` `CycloneDX`, `specVersion` `1.5`, `version` `1`, and a
  content-derived `serialNumber` (a `urn:uuid:` name-based UUIDv5 — the same
  input always reproduces the same serial, two distinct builds never collide;
  not a wall-clock UUID). `metadata` carries the injected `timestamp`, the
  `tools` array (`[{name: pengyos-sbom, version}]`) and the root component.
- **Root** (`metadata.component`): the first `rootfs` target becomes an
  `operating-system` component named after its `image_name` (falling back to the
  target name; a synthetic `PengyOS rootfs` when no rootfs target exists), with
  the Buildroot version as its version and a deterministic `pkg-root` bom-ref.
  This is the CycloneDX analogue of the SPDX described root artifact.
- **Components**: one per model component, `type` `other` (we cannot classify
  each Buildroot package — the CycloneDX analogue of the SPDX `OTHER` purpose),
  `bom-ref` `pkg-<scope>-<name>` (so a target and its `host-` version are
  distinct), `purl` `pkg:generic/<name>[@<version>]`, its single primary-source
  SHA-256 `hash` (`{alg: SHA-256, content: <hex>}`, present only when a source
  was saved), a `cpe` (when a `cpe-id` is known) and a `distribution`
  `externalReferences` entry to the origin URL (when known).
- **Licenses**: each component's `licenses` is a single SPDX `expression` — the
  exact same `LicenseConverter` output the SPDX emitter uses, so both formats
  agree on every license spelling (`NOASSERTION` passes through as a legal SPDX
  assertion). Custom `LicenseRef-<slug>` ids are declared once in the BOM's
  top-level `licenses` array (`[{licenseId, license:{name}}]`) so the BOM stays
  self-consistent — CycloneDX's mechanism for custom license identifiers.
- **Determinism**: `build_cyclonedx_document(result, created=...)` takes the
  timestamp as a parameter (the CLI fills it with the current UTC time) and
  serializes with sorted keys, so identical inputs give byte-identical output.

## Validation contract

`pengyos-sbom validate FILE [--format {auto,spdx,cyclonedx}]` checks an
emitted SBOM file *structurally* (it is not a general-purpose SPDX validator —
see Non-goals):

- **Detection**: `auto` (default) keys off `spdxVersion` → SPDX,
  `bomFormat` → CycloneDX; a document with neither (or both) markers is an
  error. `--format` forces the validator regardless of detection, so a
  format mismatch surfaces as validation problems.
- **SPDX 2.3**: required document fields (`spdxVersion` == `SPDX-2.3`,
  `dataLicense` == `CC0-1.0`, `SPDXID` == `SPDXRef-DOCUMENT`, `name`,
  `documentNamespace` as a URI, non-empty `documentDescribes`,
  `creationInfo` with an ISO-8601 `created` and `creators` each prefixed
  `Person:`/`Organization:`/`Tool:`); every package carries `SPDXID`,
  `name`, `downloadLocation`, `filesAnalyzed` (boolean),
  `licenseConcluded`, `licenseDeclared`, `copyrightText`; id uniqueness
  (packages, document id, and `hasExtractedLicensingInfos` `licenseId`s share
  the SPDX id space); `relationshipType` must be in the SPDX 2.3 enum; both
  ends of every relationship *and* every `documentDescribes` entry must
  resolve to an id present in the document; checksum/externalRef entries must
  carry their required non-empty string fields.
- **CycloneDX 1.5**: required `bomFormat` == `CycloneDX`,
  `specVersion` == `1.5`, UUID `serialNumber` (bare or `urn:uuid:`), integer
  `version`, `components` array; every component (including
  `metadata.component`) has a type from the CycloneDX 1.5 enum and a
  non-empty `name`; `bom-ref`s are unique across `metadata.component` and
  `components`; `purl` starts with `pkg:`, `cpe` with `cpe:`; hash entries
  carry `alg`+`content`; license entries carry an `expression` or a
  `license` object with `id`/`name`; top-level `licenses` entries carry a
  unique `licenseId` plus a `license` object with `name`.
- **Exit contract**: 0 + `OK` line on stdout when valid; 1 with one line per
  problem on stderr when not; 1 with a clear error for invalid JSON or a
  missing file.

## Deliverable
A Python tool `pengyos-sbom` with subcommands `generate|validate|sign|verify|keygen` producing SPDX 2.3 JSON and CycloneDX JSON.

## Signing contract

`pengyos-sbom sign FILE --key K [--out SIG]` and
`pengyos-sbom verify FILE --key K [--sig SIG]` implement detached ed25519
signing (RFC 8032) over the SBOM file.

- **Keys**: a *signing key* is the 32-byte ed25519 secret-key *seed*; a
  *verification key* is the 32-byte public key. Key files hold exactly 32 raw
  bytes, or a single base64 string (padding optional, whitespace ignored).
  `verify --key` also accepts a *seed* file. The two interpretations are
  **ambiguous**: an ed25519 seed is itself a valid curve point with ~50%
  probability, so "if the bytes are not a curve point, treat as seed" is
  unsound. Resolution: `verify` resolves the key file to *candidate public
  keys* — the raw bytes (when they are a valid curve point) plus the public
  key derived from them as a seed — and accepts iff the signature verifies
  against **any** candidate (`verification_key_candidates` in
  `pengyos_sbom.signing`). This makes the promise "the file that signed can
  verify" hold for every key, including on-curve seeds.
- **Key generation**: `pengyos-sbom keygen [--out SEED_FILE] [--pub PUB_FILE]`
  needs no external tooling (`openssl` etc.). The seed comes from
  `os.urandom(32)`; the public key is derived in-tree from it, so the pair
  can never be mismatched. Defaults: `seed.key` and `seed.key.pub` (the
  `.pub` suffix of the seed path). The seed file is written mode `0600`
  (owner-only), the public key `0644`. `keygen` **refuses to overwrite** an
  existing seed or public-key file (exit 1, naming the offender; if either
  target exists, neither file is written) — clobbering the only copy of a
  signing seed is the failure the subcommand must not cause. The generated
  files are exactly the formats `sign`/`verify` accept.
- **Ed25519 is implemented in-tree** (`pengyos_sbom.ed25519`, pure stdlib —
  CPython has no built-in ed25519) following RFC 8032 Appendix C; the RFC
  §7.1 test vectors pin its correctness. Ed25519 signatures are
  deterministic, so signing the same file twice yields the same bytes.
- **Canonicalized body** (the "Deterministic output" contract): sign/verify
  hash `canonical_bytes(raw file bytes)` (`pengyos_sbom.signing`), the single
  seam both subcommands must route through. It binds the document's *semantic
  content*, not its exact on-disk serialization: for a JSON SPDX 2.3 or
  CycloneDX document it parses the JSON, normalizes the wall-clock creation
  timestamp to a fixed constant (`creationInfo.created` for SPDX,
  `metadata.timestamp` for CycloneDX), and re-serializes canonically (sorted
  keys, compact separators, ASCII-escaped non-ASCII). Consequently two files
  that differ only in the timestamp — or in key order or whitespace/indentation
  — hash identically and one signature verifies the other, while any *content*
  change (a version, a license, an added/removed component, a tampered checksum)
  still fails. Non-JSON bytes fall back to the identity (bound verbatim), which
  is the safe default for anything not recognized as an SBOM. Because `sign` and
  `verify` both call the same function, the round-trip invariant holds whatever
  it returns.
- **Detached signature**: a 64-byte binary file. `sign` writes it to
  `--out` or, by default, `FILE + ".sig"`; `verify` reads from `--sig` or
  `FILE + ".sig"`.
- **Gate**: both subcommands run the structural validation first — `sign`
  *refuses* to sign a document that fails validation (prints the problems,
  exits 1); `verify` requires **both** the signature check and the structural
  check to pass (exit 0 + `signature OK` + `OK (verified … SBOM)` on stdout;
  any failure — tampered document, tampered/truncated signature, wrong key,
  invalid document — exits 1 with `signature INVALID` and/or the problem
  lines on stderr).
- **Errors**: missing SBOM/key/signature file, malformed key file, invalid
  JSON → exit 1 with a clear one-line error (never a traceback).

## Interface
- pengyos-sbom generate --legal-info DIR --show-info FILE --out sbom.spdx.json [--cdx sbom.cdx.json]
- pengyos-sbom validate <file> [--format {auto,spdx,cyclonedx}]
- pengyos-sbom sign <file> --key <seed-file> [--out <file>.sig]
- pengyos-sbom verify <file> --key <pub-or-seed-file> [--sig <file>.sig]
- pengyos-sbom keygen [--out seed.key] [--pub seed.key.pub]

## Verification (tests are first-class)
- Ingestion: manifest rows parse with correct scope/version/license/dep parsing;
  `not saved`→empty; the 6-column buildroot row yields a `build`-scope component
  and the Buildroot version; license texts load; saved source artifacts are
  SHA-256 hashed; show-info enriches origin/patches/deps; virtual and rootfs
  entries are routed to `skipped_virtual`/`rootfs`; manifest-only and
  un-manifested entries warn; output ordering is deterministic.
- SPDX output re-parses and validates against required fields; SPDXID uniqueness.
- CycloneDX output parses; components match input count.
- sign→verify round-trip true; tampered document fails verify.
- determinism: two runs on the same input (same `created`) are byte-identical,
  and a signature over one document verifies a copy differing only in the
  creation timestamp (SPDX `creationInfo.created` / CycloneDX
  `metadata.timestamp`); content tampering still fails.
- malformed input → clear error, non-zero exit.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in
sync: every CLI usage line in the spec's Interface section must appear in the
README verbatim, and no subcommand may be described as unimplemented. The
"full green test suite" is asserted by re-running `pytest` in a subprocess
while **excluding the `test_github_ready` tests themselves** (they all carry
that name, filtered with `-k "not test_github_ready"`) so the re-run cannot
recurse; the outer `pytest -q` therefore covers everything once.

## Acceptance criteria
- Produces valid SPDX 2.3 and CycloneDX for the fixtures.
- Signatures verify; tampering detected.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Scanning binaries for licenses
- Being an SPDX validator for arbitrary docs

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
