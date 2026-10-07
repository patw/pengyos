# Signature File Format — `pengyos-sign`

**Status:** stable, version 1.
**Specification authority:** this document is the normative description of the
`.sig` wire format. The implementation in `src/signature.rs` is the reference
encoder/decoder (`to_bytes` / `from_bytes`); where they disagree, the code is
corrected to match this document.

---

## 1. Overview

A `pengyos-sign` signature is a **detached** signature: it lives in its own file
(`<artifact>.sig`) and never alters the artifact. The file is a single **header
line** (a small, self-describing, human-readable record) followed immediately by
the **raw signature bytes**.

The signature is an **ed25519** signature (RFC 8032, i.e. standard Ed25519 over
the SHA-256 digest) over the **SHA-256 digest** of the artifact. Verification
re-hashes the artifact and checks the digest against the one recorded in the
header, so it works on arbitrarily large files with bounded memory (streaming).

## 2. Byte layout

```
+------------------------------------------------------------------+
| header line: ASCII, terminated by a single 0x0A (\n)             |
|                                                                  |
|   <magic> <version> <algorithm> <keyid> <hash-algorithm> <hash>  |
|   (6 whitespace-separated fields, single spaces)                 |
+------------------------------------------------------------------+
| raw signature: 64 bytes (ed25519), exactly, no terminator        |
+------------------------------------------------------------------+
```

- The header line is UTF-8/ASCII, terminated by exactly one `\n` (0x0A).
- The 64 raw signature bytes follow the newline with no padding or trailing
  newline.
- Total size: `len(header line) + 64` bytes.

## 3. Header fields

The header is six whitespace-separated fields.

| # | Field          | Meaning                                            | Format                                   |
|---|----------------|----------------------------------------------------|------------------------------------------|
| 1 | `magic`        | Format identifier                                  | `pengyos-sig-v1` (literal)               |
| 2 | `version`      | Format version                                     | decimal integer (`1`)                    |
| 3 | `algorithm`    | Signature algorithm                                | `ed25519` (literal)                      |
| 4 | `keyid`        | `SHA-256(public_key)` of the signing key           | 64 lowercase hex chars (32 bytes)        |
| 5 | `hash-algorithm`| Digest algorithm over the artifact                | `sha256` (literal)                       |
| 6 | `hash`         | `SHA-256(artifact)` — the digest the signature binds to | 64 lowercase hex chars (32 bytes)  |

Example header line:

```
pengyos-sig-v1 1 ed25519 3b5b8df6e0c9a0b7f2d4e6a1c8f3b9d0e5a7c2f4b6d8e0f2a4c6e8f0b2d4e6f8 sha256 3b5b8df6e0c9a0b7f2d4e6a1c8f3b9d0e5a7c2f4b6d8e0f2a4c6e8f0b2d4e6f8
```

> Note: the `keyid` and `hash` values above are illustrative placeholders, not a
> real signature. See §7 for a machine-checkable example.

## 4. Semantics

- **`magic`** — identifies the file as a `pengyos-sign` signature. A file whose
  first field is not exactly `pengyos-sig-v1` is rejected (`Format`).
- **`version`** — the format version. The current and only version is `1`.
  Verifiers MUST reject any other version (`Format`, "unsupported signature
  format version"). This is the mechanism by which the format is **versioned**:
  a future major version will use a new integer and/or a new `magic`, and old
  verifiers will refuse to read it rather than misinterpreting it.
- **`algorithm`** — the signature algorithm. Only `ed25519` is supported; any
  other value is rejected (`UnsupportedAlgorithm`).
- **`keyid`** — `SHA-256(public_key)` of the signing key, rendered as 64
  lowercase hex characters. It binds a signature to the key that produced it, so
  a verifier holding several public keys can select the right one. It is a
  non-secret identifier, not a cryptographic commitment to the artifact.
- **`hash-algorithm`** — the digest algorithm applied to the artifact. Only
  `sha256` is supported; any other value is rejected (`UnsupportedHash`).
- **`hash`** — `SHA-256(artifact)`, the 32-byte digest rendered as 64 lowercase
  hex characters. The ed25519 signature is computed over these 32 bytes. The
  verifier re-hashes the artifact and rejects a mismatch (`HashMismatch`).

## 5. Versioning policy

The format is versioned by two cooperating fields:

1. **`magic`** — the high-level format family. A change to the format *family*
   (e.g. a fundamentally different container) changes the magic.
2. **`version`** — the numeric version within the family. A change to any field
   type, encoding, or semantic meaning bumps this integer.

Rules for all implementers:

- **Forward incompatibility:** a verifier MUST accept only the version(s) it
  implements. The current implementation accepts only `1` and rejects any other
  integer. Verifiers SHOULD NOT silently accept unknown versions.
- **New versions:** a future version MUST bump `version` (and, if the container
  changes, `magic`). The new magic/version is documented in this file with its
  own "Version N" section before shipping.
- **Lowercase hex:** `keyid` and `hash` are always lowercase ASCII hex.
- **Single newline:** the header is terminated by exactly one `\n`; the signature
  bytes carry no terminator.

## 6. Error handling

Parsing (`from_bytes`) validates, in order, and rejects with the indicated error:

| Condition                                   | Error variant            |
|---------------------------------------------|--------------------------|
| No header line (no `\n`)                    | `Format`                 |
| Header not valid UTF-8                      | `Format`                 |
| Not exactly 6 header fields                 | `Format`                 |
| `magic` != `pengyos-sig-v1`                 | `Format`                 |
| `version` not a `u16`                       | `Format`                 |
| `version` != `1`                            | `Format`                 |
| `algorithm` != `ed25519`                    | `UnsupportedAlgorithm`   |
| `hash-algorithm` != `sha256`                | `UnsupportedHash`        |
| `hash` not 64 lowercase/uppercase hex chars | `Format`                 |
| Signature not exactly 64 bytes              | `SignatureLength`        |

Parsing never verifies the signature; cryptographic validity is established by
`verify_file` (which re-hashes and checks, then verifies the ed25519 signature
over the recorded digest).

## 7. Machine-checkable example

The following is a real, self-consistent record for the artifact
`b"pengyos reproducible artifact payload\n"` signed with seed
`00 01 02 … 1f` (bytes `0x00`..`0x1f`). It is used by the test suite to pin the
documented format against the reference encoder.

- Artifact: `pengyos reproducible artifact payload\n`
- `SHA-256(artifact)`:
  `699e82b6d0cde2ae130ee3a26249861ccf04f62e1031cb2c3968c845f5fdd1f4`
- ed25519 signature (over the digest):
  `17781243318895e1b858d373c7ee1b666e6b8bd1bf0d8b0d7d316925baa8b0f663f8a7068f690b4372d3e25070fbc7a6737ae4eee029953694f4b584ec136509`
- `keyid` (`SHA-256(public_key)`): produced by the encoder at sign time.

The full `.sig` is the header line `pengyos-sig-v1 1 ed25519 <keyid> sha256
699e82b6d0cde2ae130ee3a26249861ccf04f62e1031cb2c3968c845f5fdd1f4` followed by
the 64 raw signature bytes above.

## 8. Relation to the key file format

The signing key is stored in a `pengyos-key-v1` key file
(`src/keys.rs`):

```
pengyos-key-v1 ed25519 <keyid-hex> <seed-hex>        (private)
pengyos-key-v1 ed25519 <keyid-hex> <public-key-hex>  (public)
```

The `<keyid>` in the key file is the same value recorded as the `keyid` field
of the signature header, so a `.sig` and the corresponding `.pub` share a key id.
