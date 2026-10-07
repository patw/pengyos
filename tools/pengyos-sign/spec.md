# pengyos-sign — minimal artifact signing (Rust)

## Summary
A Rust library + CLI for ed25519 artifact signing/verification and simple key management.

## Purpose
SPEC §1: minimal, modern signatures over every artifact.

## Deliverable
A Rust crate `pengyos-sign` (lib + bin) with `keygen|sign|verify`, deterministic signatures, and a documented wire format.

## Wire format (v1)
The `.sig` wire format is defined and versioned in [`FORMAT.md`](FORMAT.md)
(the authoritative spec; `src/signature.rs` is the reference encoder/decoder).
In short, a `.sig` file is a header line followed by the raw 64-byte ed25519
signature:

```text
pengyos-sig-v1 1 ed25519 <keyid-hex> sha256 <hash-hex>\n<64 raw signature bytes>
```

The header is six whitespace-separated fields: `magic`, `version`, `algorithm`,
`keyid` (`SHA-256(public_key)`, 64 lowercase hex), `hash-algorithm`, and `hash`
(`SHA-256(artifact)`, 64 lowercase hex). The signature is ed25519 (RFC 8032) over
the 32-byte `hash` digest; verification re-hashes the artifact and rejects any
digest mismatch or invalid signature. The format is versioned via the `version`
field (current: `1`) and `magic`; verifiers reject unknown versions.

## Interface
- pengyos-sign keygen --out key | sign <file> --key K [--sig F] | verify <file> --sig F --pub PUB

## Verification (tests are first-class)
- keygen→sign→verify round-trip true.
- tampered file fails verify; wrong key fails.
- 'cargo test' green including a large-file streaming test.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Deterministic signatures (RFC 8032) verified against a known test vector.
- `cargo test` green.

## Non-goals
- minisign byte-compat (may come later)
- Key servers

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
