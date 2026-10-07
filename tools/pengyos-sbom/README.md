# pengyos-sbom

Turn Buildroot **legal-info / show-info** manifests into a valid **SPDX 2.3**
SBOM (and **CycloneDX 1.5**), with **ed25519** signing/verification.

Provenance is a first-class feature of PengyOS: every artifact ships with a
verifiable bill of materials. Buildroot already emits license and source
manifests (`make legal-info` and `make show-info`); this tool merges them into
a machine-readable SBOM and signs it so a downstream verifier can attest the
exact contents.

## Install

Standard library only — no third-party runtime dependencies.

```sh
pip install .          # installs the `pengyos-sbom` command
# or run in place:
python -m pengyos_sbom
```

## Usage

```
pengyos-sbom generate --legal-info DIR --show-info FILE --out sbom.spdx.json [--cdx sbom.cdx.json]
pengyos-sbom validate <file> [--format {auto,spdx,cyclonedx}]
pengyos-sbom sign <file> --key <seed-file> [--out <file>.sig]
pengyos-sbom verify <file> --key <pub-or-seed-file> [--sig <file>.sig]
pengyos-sbom keygen [--out seed.key] [--pub seed.key.pub]
```

- **generate** — merge Buildroot legal-info + show-info into a valid SPDX 2.3
  JSON SBOM (document, packages, DESCRIBES/CONTAINS relationships, SPDX
  license expressions, SHA-256 checksums). The optional `--cdx` flag
  additionally writes a CycloneDX 1.5 BOM from the same internal model.
- **validate** — structural + required-field validation for either format.
- **sign** — detached ed25519 signature (64 raw bytes) over the canonicalized
  document, written to `--out` or `FILE + ".sig"`. Refuses to sign a document
  that fails structural validation. The `--key` file is the 32-byte ed25519
  secret-key *seed* (raw 32 bytes, or one base64 string).
- **verify** — checks the detached signature against the public key *and*
  re-validates the document structurally; fails loudly (exit 1) on any
  tampering, wrong key, or invalid document. The `--key` file is the 32-byte
  public key — or the seed file, from which the public key is derived. (The
  two interpretations are ambiguous — ~50% of random seeds are themselves
  valid curve points — so both are tried and the signature is accepted iff
  either verifies.)
- **keygen** — generate an ed25519 keypair with no external tooling: a fresh
  32-byte seed from `os.urandom` plus its derived public key, written to
  `--out` (default `seed.key`, mode `0600`) and `--pub` (default
  `seed.key.pub`, mode `0644`). Existing files are never overwritten.

## Subcommands

| Command    | Status          | Notes                                        |
|------------|-----------------|----------------------------------------------|
| generate   | **implemented** | SPDX 2.3 output; `--cdx` emits CycloneDX 1.5 |
| validate   | **implemented** | required-field + structural checks for both formats (auto-detect or `--format`) |
| sign       | **implemented** | detached RFC 8032 ed25519 signature, `FILE + ".sig"` sidecar by default |
| verify     | **implemented** | signature + structural re-validation; tamper detection |
| keygen     | **implemented** | ed25519 seed + public key files (0600/0644); never overwrites |

All five subcommands are implemented; a failed input (missing file, bad key,
invalid document, broken signature) exits with code `1` and a clear message.

## Python API (ingestion)

The Buildroot → internal-model ingestion layer is already available as a
library (the seam `generate` calls):

```python
from pathlib import Path
from pengyos_sbom.ingest import ingest

result = ingest(Path("output/legal-info"), Path("show-info.json"))
for c in result.components:        # deterministic: build < host < target
    print(c.scope, c.name, c.version, c.licenses, c.origin, c.source_hash)
```

`result` is an `IngestResult` of frozen, hashable `Component`s (keyed by
`(scope, name)` — a package and its `host-` version are distinct) carrying
version, license string + saved license texts, source origin/URIs, SHA-256
digests of the saved source artifacts, patches, CPE id and dependencies,
plus `rootfs` targets, `buildroot_version` and non-fatal `warnings`.
Input formats are documented in `spec.md` and the module docstrings of
`pengyos_sbom.legalinfo` / `pengyos_sbom.showinfo` (Buildroot 2026.02.3).

## Exit codes

| Code | Meaning                                    |
|------|--------------------------------------------|
| 0    | success                                    |
| 1    | error (bad input / validation failed / I/O)|
| 2    | command-line usage error (argparse)        |
| 3    | reserved (legacy; no longer issued)             |

## Signing keys

Ed25519 is implemented in the standard library (`pengyos_sbom.ed25519`,
RFC 8032 Appendix C; pinned by the RFC test vectors). A *signing key* is the
32-byte secret-key seed; a *verification key* is the 32-byte public key. Key
files are either exactly 32 raw bytes or a single base64 string (padding
optional, whitespace tolerated).

Generate a keypair — no `openssl` or Python snippet needed:

```sh
pengyos-sbom keygen                  # -> seed.key (mode 0600) + seed.key.pub (mode 0644)
pengyos-sbom keygen --out my/seed.key --pub my/pub.key
```

The public key is derived from the seed in-tree, so the pair can never be
mismatched, and `keygen` refuses to overwrite an existing file (exit 1) — it
will never clobber the only copy of a signing seed.

`verify --key` also accepts a seed file. The two interpretations of a
32-byte key file are *ambiguous* (an ed25519 seed is a valid curve point
with ~50% probability), so `verify` resolves the file to candidate public
keys — the raw bytes, when they are a valid curve point, plus the key
derived from them as a seed — and accepts iff the signature verifies against
any candidate. The file that signed can therefore always be used to verify.

## Determinism

Given the same input and the same creation timestamp, `generate` produces
byte-identical output: components are deterministically ordered (build < host <
target, then name), package ids / `bom-ref`s derive from `(scope, name)`, and
document namespaces / BOM serial numbers derive from a content hash.

Signing is **stable across the creation timestamp** (and across whitespace and
key order): `sign` and `verify` both hash `canonical_bytes(file)`
(`pengyos_sbom.signing`), which parses the document, normalizes the wall-clock
`created` / `timestamp` field to a fixed constant, and re-serializes canonically
(sorted keys, compact separators, ASCII-escaped). A signature produced over one
file therefore verifies a copy of the same document regenerated at a different
instant — while any change to actual *content* (version, license, component, or
tampered checksum) is still detected.

## Development

```sh
python -m pytest -q          # unit tests (no network, no root)
```

Integration tests are opt-in behind `PENGYOS_IT=1` and are skipped otherwise.

`tests/test_github_ready.py` asserts the GitHub-ready contract from `spec.md`
(README / MIT LICENSE / CHANGELOG / .gitignore present and correct, docs in
sync with `spec.md`'s Interface, and the full test suite green — the latter
done by re-running pytest in a subprocess, excluding itself).

### Test fixtures

The whole suite runs against a small committed sample of real Buildroot output
under `tests/fixtures/` (`legal-info/` directory + `show-info.json`). It
mirrors the shapes the parsers must handle — target/host/build scopes, a
package in both target and host, a virtual alias, a rootfs target, a `not
saved` source, a non-redistributable package, a saved patch + `series`, and a
multi-URI download. `tests/test_fixtures.py` pins that sample (the exact file
tree, its Buildroot shape, and manifest↔show-info consistency) and drives the
full `generate → validate → sign → verify` pipeline over it for both output
formats, so the fixtures stay a working, non-rotting input.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
