# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Exit-code contract tests for the CLI (`tests/cli.rs`): a well-formed
  invocation that fails at I/O or crypto time (`sign`/`verify` with a missing
  key, missing artifact, missing public key, or a malformed signature) exits
  with the generic runtime code `1`, distinct from the usage code `2`; a
  `sign` without `--key` remains a usage error (`2`). The distinguishing
  property (usage `2` vs. runtime `1` for the same operation under different
  argument shapes) is pinned.
- No-key-material-leak tests for the sign/verify paths: the happy path
  (`keygen` -> `sign` -> `verify`) and error paths (a recognizable all-`0x42`
  seed on a failed `sign`, a recognizable all-`0xab` public key on a failed
  `verify`) are all asserted to never surface seed or public-key material in
  stdout or stderr.

- Large-file streaming verification, tested end-to-end through the CLI. A new
  `tests/cli_large_file.rs` suite signs and verifies a multi-megabyte artifact
  (`keygen` -> `sign` -> `verify`) and confirms that tampering a byte in the
  middle of a large artifact is detected (`HashMismatch`). This exercises the
  streaming SHA-256 path (`hash_file`, fixed 64 KiB chunks, bounded memory)
  through the actual binary, proving that signing and verification use flat
  memory regardless of artifact size.
- Documentation of the hash-based streaming verification design in
  README.md: `sign` / `verify` stream the artifact in fixed 64 KiB chunks, so
  memory use stays flat at roughly one chunk regardless of artifact size.

## [0.2.0] - 2026-09-15

### Added

- Formal, spec-level documentation of the versioned signature wire format in
  [`FORMAT.md`](FORMAT.md): the byte layout, the six header fields
  (`magic`, `version`, `algorithm`, `keyid`, `hash-algorithm`, `hash`), field
  semantics, the versioning policy (`magic` + `version`), error-handling table,
  and a machine-checkable example. `FORMAT.md` is the authoritative spec;
  `src/signature.rs` (`to_bytes`/`from_bytes`) is the reference encoder/decoder.
- Versioning policy: the format is versioned by the `version` field (current:
  `1`) and the `magic`; verifiers reject unknown versions rather than
  misinterpreting them.
- Tests pinning the documented format against the reference encoder, the
  signature `version` round-trip, and the `FORMAT.md`/code consistency contract.
- Bumped package to `0.2.0` (new documented feature).

## [0.1.0] - 2026-09-15

### Added

- Crate scaffold for `pengyos-sign`: a `pengyos_sign` library plus the
  `pengyos-sign` CLI binary, built from a single package.
- CLI dispatcher with `--help` / `--version`, clean exit codes, and a
  documented `keygen` / `sign` / `verify` surface.
- GitHub-ready contract: MIT LICENSE (Copyright (c) 2026 Pat Wendorf),
  authored README, Keep-a-Changelog `CHANGELOG.md`, and `.gitignore`.
- Rust test suite: unit tests for the library and integration tests for the
  CLI and the GitHub-ready contract (`tests/github_ready.rs`, `tests/cli.rs`).
- `keygen` (ed25519): generates a key pair, writes the private key with `0600`
  permissions and the public key alongside it, and leaks no key material to
  stdout/stderr. Key files use a small, versioned, self-describing text format.
- `sign` / `verify`: detached ed25519 signatures over the artifact's SHA-256
  digest. A `.sig` file is a small, versioned, self-describing header line
  (`pengyos-sig-v1 <version> ed25519 <keyid> sha256 <hash-hex>`) followed by the
  raw 64-byte signature. Verification re-hashes the artifact and rejects any
  digest mismatch or invalid signature.
- Streaming SHA-256 hashing (`hash_file`) in fixed 64 KiB chunks, so signing
  and verification use bounded memory regardless of artifact size.
- Deterministic signatures (RFC 8032) verified against the published
  RFC 8032 §7.1 TEST 1 vector, plus a dedicated sign-over-SHA-256 vector.

### Work planned (separate subtasks)

- Streaming hash-based verification of large files.
