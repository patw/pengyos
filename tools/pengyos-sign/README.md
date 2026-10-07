# pengyos-sign

Minimal, modern **artifact signing** for [PengyOS](../../README.md).

`pengyos-sign` is a small Rust library (`pengyos_sign`) plus a CLI binary
(`pengyos-sign`) for signing and verifying build artifacts with **ed25519**
signatures, along with simple local key management. Its purpose (PengyOS SPEC §1)
is to provide a minimal, tamper-evident signature over every artifact the distro
produces, so that a downstream verifier can trust what it can't re-derive.

---

## Why

PengyOS rebuilds the same system from source and checks the result. Trust comes
from **verification, not authority**: every released artifact carries a signature
over its content hash, produced by a key that lives outside the build. `pengyos-sign`
is that signing tool.

## Features

- **ed25519 signatures** — modern, fast, and deterministic under RFC 8032.
- **Detached signatures** — the signature lives in its own file, so artifacts
  (ISOs, kernels, tarballs) are not altered in place.
- **Small, versioned header** — each signature file records the algorithm, a
  key id, and the content hash it binds to.
- **Simple key management** — generate a key pair locally; the private key is
  written with `0600` permissions.
- **Streaming verification** — large files are hashed in chunks, so memory use
  stays flat regardless of artifact size.

> The cryptographic surface (`keygen`, `sign`, `verify`) is complete; the
> signature wire format is documented in [`FORMAT.md`](FORMAT.md).

## Install

This crate builds with a standard Rust toolchain (no network, no root):

```console
$ cargo build --release --offline
$ cargo install --path .
```

The binary is installed as `pengyos-sign`.

## Usage

```
pengyos-sign [COMMAND] [OPTIONS]

COMMANDS:
    keygen    Generate an ed25519 key pair (private key, 0600; derived public key)
    sign      Produce a detached signature over a file
    verify    Verify a detached signature over a file

GLOBAL OPTIONS:
    -h, --help      Print help
    -V, --version   Print version
```

### keygen

Generate a key pair. The private key is written to the requested path with
`0600` permissions; the public key is written alongside it (or to `--pub`).

```console
$ pengyos-sign keygen --out signing.key --pub signing.pub
```

### sign

Produce a detached signature over a file. The signature file contains a small
header (algorithm, key id, content hash) followed by the ed25519 signature.

```console
$ pengyos-sign sign ./pengyos.iso --key signing.key --sig pengyos.iso.sig
```

### verify

Verify a detached signature against a public key.

```console
$ pengyos-sign verify ./pengyos.iso --sig pengyos.iso.sig --pub signing.pub
```

## Wire format

The signature file format is defined and versioned in
[`FORMAT.md`](FORMAT.md) (the authoritative spec; the implementation in
`src/signature.rs` is the reference encoder/decoder). In short, a `.sig` file is
a header line followed by the raw 64-byte ed25519 signature:

```text
pengyos-sig-v1 1 ed25519 <keyid-hex> sha256 <hash-hex>\n<64 raw signature bytes>
```

The header line is six whitespace-separated fields:

| # | Field          | Meaning                                            |
|---|----------------|----------------------------------------------------|
| 1 | `magic`        | `pengyos-sig-v1`                                   |
| 2 | `version`      | format version (`1`)                               |
| 3 | `algorithm`    | signing algorithm (`ed25519`)                      |
| 4 | `keyid`        | `SHA-256(public_key)`, 64 lowercase hex chars      |
| 5 | `hash-algorithm`| digest algorithm over the artifact (`sha256`)     |
| 6 | `hash`         | `SHA-256(artifact)`, 64 lowercase hex chars        |

The signature is ed25519 over the 32-byte `hash` digest, so verification streams
the artifact to a digest and never loads it whole into memory. See `FORMAT.md`
for the byte layout, field semantics, versioning policy, and error handling.

## Streaming verification

`pengyos-sign` uses **hash-based verification**: it signs and verifies the
SHA-256 digest of an artifact, never the artifact itself. Both `sign` and
`verify` stream the artifact through SHA-256 in fixed 64 KiB chunks
(`hash_file`), so memory use stays flat at roughly one chunk regardless of
artifact size. A multi-gigabyte ISO is signed and verified with the same bounded
memory as a small file, and a single changed byte anywhere in the file is
detected once the stream is re-hashed (`HashMismatch`).

## Exit codes

| Code | Meaning                                             |
| ---- | --------------------------------------------------- |
| `0`  | Success (including `--help` / `--version`).         |
| `1`  | Generic runtime error.                              |
| `2`  | Usage error (bad or missing arguments).             |
| `3`  | Subcommand not yet implemented.                     |

Errors never print key material.

## Development

Run the test suite (no network, no root):

```console
$ cargo test
```

The suite covers library units, the CLI, and the GitHub-ready contract
(`tests/github_ready.rs`). Real integration tests are opt-in behind
`PENGYOS_IT=1`.

## License

This project is licensed under the [MIT license](LICENSE),
**Copyright (c) 2026 Pat Wendorf**.
