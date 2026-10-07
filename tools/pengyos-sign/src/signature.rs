//! Detached, versioned ed25519 signatures over a file's SHA-256 digest.
//!
//! This module implements the `sign` / `verify` surface. It signs the SHA-256 digest of
//! an artifact with ed25519 (deterministic under RFC 8032) and records the
//! digest, algorithm, key id, and a format version in a small header that
//! precedes the raw signature bytes.
//!
//! ## Wire format (v1)
//!
//! A `.sig` file is a single header line followed by the raw 64-byte ed25519
//! signature:
//!
//! ```text
//! pengyos-sig-v1 1 ed25519 <keyid-hex> sha256 <hash-hex>\n<64 raw signature bytes>
//! ```
//!
//! The header line is six whitespace-separated fields:
//!
//! | # | Field          | Meaning                                            |
//! |---|----------------|----------------------------------------------------|
//! | 1 | `magic`        | `pengyos-sig-v1`                                   |
//! | 2 | `version`      | format version (`1`)                               |
//! | 3 | `algorithm`    | signing algorithm (`ed25519`)                      |
//! | 4 | `keyid`        | `SHA-256(public_key)`, 64 lowercase hex chars      |
//! | 5 | `hash-algorithm`| digest algorithm over the artifact (`sha256`)     |
//! | 6 | `hash`         | `SHA-256(artifact)`, 64 lowercase hex chars        |
//!
//! The 64 raw signature bytes follow the first newline. The signature is the
//! ed25519 signature over the 32-byte `hash` digest, so verification streams the
//! artifact to a digest and never loads it whole into memory.
//!
//! [`crate`]: crate

use std::fs;
use std::io::{self, Read};
use std::path::Path;

use ed25519_dalek::{Signer, VerifyingKey, Verifier};
use sha2::{Digest, Sha256};

use crate::keys::load_signing_seed;

/// Header magic for the signature-file format (`pengyos-sig-v1`).
pub const SIG_MAGIC: &str = "pengyos-sig-v1";

/// Current signature format version.
pub const SIG_VERSION: u16 = 1;

/// Signing algorithm identifier for ed25519 signatures.
pub const ED25519: &str = "ed25519";

/// Digest algorithm identifier for SHA-256.
pub const SHA256_ALGO: &str = "sha256";

/// Length in bytes of an ed25519 signing seed (and the private key material).
pub const SEED_LENGTH: usize = 32;

/// Length in bytes of an ed25519 public (verifying) key.
pub const PUBLIC_KEY_LENGTH: usize = 32;

/// Length in bytes of a SHA-256 digest.
pub const HASH_LENGTH: usize = 32;

/// Length in bytes of an ed25519 signature.
pub const SIGNATURE_LENGTH: usize = 64;

/// Default signature file name: the artifact path with `.sig` appended
/// (e.g. `pengyos.iso` -> `pengyos.iso.sig`).
pub fn default_sig_path(artifact: &Path) -> std::path::PathBuf {
    let mut stem = artifact.as_os_str().to_os_string();
    stem.push(".sig");
    std::path::PathBuf::from(stem)
}

/// A parsed, in-memory detached signature (header + raw ed25519 signature).
#[derive(Debug, Clone)]
pub struct Signature {
    /// Signature format version (`SIG_VERSION`).
    pub version: u16,
    /// Signing algorithm (`ED25519`).
    pub algorithm: String,
    /// `SHA-256(public_key)` of the signing key, 64 lowercase hex chars.
    pub keyid: String,
    /// Digest algorithm over the artifact (`SHA256_ALGO`).
    pub hash_algorithm: String,
    /// `SHA-256(artifact)` — the digest the signature binds to.
    pub hash: [u8; HASH_LENGTH],
    /// The raw 64-byte ed25519 signature over `hash`.
    pub signature: [u8; SIGNATURE_LENGTH],
}

/// Errors produced by signing, verifying, and parsing signature files.
///
/// The variant messages never contain key material or the artifact contents: a
/// failed `sign`/`verify` reports only the failure class and, at most, a
/// length or algorithm identifier.
#[derive(Debug)]
pub enum SignError {
    /// An underlying filesystem / OS error.
    Io(io::Error),
    /// A signature file is malformed or not a recognized `pengyos` signature.
    Format(String),
    /// A signature names an signing algorithm this build does not support.
    UnsupportedAlgorithm(String),
    /// A signature names a digest algorithm this build does not support.
    UnsupportedHash(String),
    /// The trailing signature bytes are not exactly 64 bytes long.
    SignatureLength(usize),
    /// The ed25519 signature does not verify against the recorded digest.
    InvalidSignature,
    /// The artifact's recomputed digest does not match the signed digest.
    HashMismatch,
}

impl std::fmt::Display for SignError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SignError::Io(err) => write!(f, "signature I/O error: {err}"),
            SignError::Format(msg) => write!(f, "malformed signature file: {msg}"),
            SignError::UnsupportedAlgorithm(algo) => {
                write!(f, "unsupported signing algorithm: {algo}")
            }
            SignError::UnsupportedHash(algo) => write!(f, "unsupported digest algorithm: {algo}"),
            SignError::SignatureLength(n) => {
                write!(f, "signature must be {SIGNATURE_LENGTH} bytes, found {n}")
            }
            SignError::InvalidSignature => write!(f, "signature does not verify"),
            SignError::HashMismatch => {
                write!(f, "artifact digest does not match the signed digest")
            }
        }
    }
}

impl std::error::Error for SignError {}

impl From<io::Error> for SignError {
    fn from(err: io::Error) -> Self {
        SignError::Io(err)
    }
}

/// Map a key-file error onto a signature error so the `*_keyfile` helpers can
/// use `?` uniformly. Key-file format/algorithm errors surface as signature
/// format/algorithm errors; the messages never contain key material.
impl From<crate::keys::KeyError> for SignError {
    fn from(err: crate::keys::KeyError) -> Self {
        match err {
            crate::keys::KeyError::Io(e) => SignError::Io(e),
            crate::keys::KeyError::Format(m) => SignError::Format(m),
            crate::keys::KeyError::UnsupportedAlgorithm(a) => {
                SignError::UnsupportedAlgorithm(a)
            }
        }
    }
}

/// Serialize this signature to the v1 wire format (header line + raw bytes).
pub fn to_bytes(sig: &Signature) -> Vec<u8> {
    let header = format!(
        "{magic} {version} {algo} {keyid} {halgo} {hash}\n",
        magic = SIG_MAGIC,
        version = sig.version,
        algo = sig.algorithm,
        keyid = sig.keyid,
        halgo = sig.hash_algorithm,
        hash = hex(&sig.hash),
    );
    let mut out = header.into_bytes();
    out.extend_from_slice(&sig.signature);
    out
}

/// Parse a signature from its wire format (header line + raw bytes).
///
/// Validates the magic, format version, signing algorithm, digest algorithm,
/// and signature length. Does **not** verify the signature — see
/// [`verify_file`].
pub fn from_bytes(data: &[u8]) -> Result<Signature, SignError> {
    // The header is the first line; everything after the first newline is the
    // raw signature.
    let newline = match data.iter().position(|&b| b == b'\n') {
        Some(pos) => pos,
        None => {
            return Err(SignError::Format(
                "signature file has no header line".to_string(),
            ))
        }
    };
    let header = &data[..newline];
    let sig_bytes = &data[newline + 1..];

    let header_str = std::str::from_utf8(header).map_err(|_| {
        SignError::Format("signature header is not valid UTF-8".to_string())
    })?;
    let fields: Vec<&str> = header_str.split_whitespace().collect();
    if fields.len() != 6 {
        return Err(SignError::Format(format!(
            "expected 6 header fields, found {}",
            fields.len()
        )));
    }
    let (magic, version_s, algo, keyid, halgo, hash_s) =
        (fields[0], fields[1], fields[2], fields[3], fields[4], fields[5]);

    if magic != SIG_MAGIC {
        return Err(SignError::Format(format!(
            "unexpected signature magic `{magic}` (expected `{SIG_MAGIC}`)"
        )));
    }

    let version = version_s
        .parse::<u16>()
        .map_err(|_| SignError::Format(format!("invalid signature version `{version_s}`")))?;
    if version != SIG_VERSION {
        return Err(SignError::Format(format!(
            "unsupported signature format version {version} (expected {SIG_VERSION})"
        )));
    }

    if algo != ED25519 {
        return Err(SignError::UnsupportedAlgorithm(algo.to_string()));
    }
    if halgo != SHA256_ALGO {
        return Err(SignError::UnsupportedHash(halgo.to_string()));
    }

    let hash = decode_hex32(hash_s)?;

    if sig_bytes.len() != SIGNATURE_LENGTH {
        return Err(SignError::SignatureLength(sig_bytes.len()));
    }
    let mut signature = [0u8; SIGNATURE_LENGTH];
    signature.copy_from_slice(sig_bytes);

    Ok(Signature {
        version,
        algorithm: algo.to_string(),
        keyid: keyid.to_string(),
        hash_algorithm: halgo.to_string(),
        hash,
        signature,
    })
}

/// Sign the artifact at `path` with the ed25519 seed, returning the serialized
/// signature (header + raw bytes).
///
/// The artifact is hashed with SHA-256 (streaming, bounded memory) and the
/// digest is signed. The signing key's id is recorded in the header so a
/// verifier holding several keys can select the right one.
pub fn sign_file<P: AsRef<Path>>(
    artifact: P,
    seed: &[u8; SEED_LENGTH],
) -> Result<Vec<u8>, SignError> {
    let signing_key = ed25519_dalek::SigningKey::from_bytes(seed);
    let hash = hash_file(&artifact)?;
    let signature = signing_key.sign(&hash);

    let pubkey = signing_key.verifying_key().to_bytes();
    let keyid = sha256_hex(&pubkey);

    let sig = Signature {
        version: SIG_VERSION,
        algorithm: ED25519.to_string(),
        keyid,
        hash_algorithm: SHA256_ALGO.to_string(),
        hash,
        signature: signature.to_bytes(),
    };
    Ok(to_bytes(&sig))
}

/// Verify the signature over the artifact at `path`.
///
/// `data` is the raw contents of the `.sig` file and `pubkey` is the 32-byte
/// ed25519 verifying key. The signature is verified against the digest recorded
/// in the header; the artifact is then re-hashed and checked to match that
/// digest. Either failure rejects the signature.
pub fn verify_file<P: AsRef<Path>>(
    artifact: P,
    data: &[u8],
    pubkey: &[u8; PUBLIC_KEY_LENGTH],
) -> Result<(), SignError> {
    let sig = from_bytes(data)?;

    let verifying_key = VerifyingKey::from_bytes(pubkey)
        .map_err(|_| SignError::InvalidSignature)?;

    // Rehydrate the raw 64-byte signature into an ed25519 Signature. `from_bytes`
    // is infallible here (it only requires 64 bytes); cryptographic validity is
    // established by the `verify` call below.
    let signature = ed25519_dalek::Signature::from_bytes(&sig.signature);

    // Verify the signature over the recorded digest first. A signature valid
    // over the original digest but over a tampered artifact fails here; a
    // signature over the tampered digest (which the signer never produced)
    // also fails here.
    verifying_key
        .verify(&sig.hash, &signature)
        .map_err(|_| SignError::InvalidSignature)?;

    // Then confirm the on-disk artifact matches the signed digest.
    let actual = hash_file(&artifact)?;
    if actual != sig.hash {
        return Err(SignError::HashMismatch);
    }

    Ok(())
}

/// Stream the artifact at `path` through SHA-256 and return the 32-byte digest.
///
/// Reads in fixed-size chunks so memory use stays flat regardless of artifact
/// size — this is what makes signing/verification of large files feasible.
pub fn hash_file<P: AsRef<Path>>(path: P) -> Result<[u8; HASH_LENGTH], SignError> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; READ_CHUNK];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    let digest = hasher.finalize();
    let mut out = [0u8; HASH_LENGTH];
    out.copy_from_slice(&digest);
    Ok(out)
}

/// The chunk size (64 KiB) used by [`hash_file`] for streaming reads.
pub const READ_CHUNK: usize = 64 * 1024;

/// Load a signing seed from a `pengyos` key file and sign the artifact in one
/// step. Convenience wrapper over [`load_signing_seed`] + [`sign_file`].
pub fn sign_keyfile<P: AsRef<Path>>(
    artifact: P,
    key_path: P,
) -> Result<Vec<u8>, SignError> {
    let seed = load_signing_seed(key_path)?;
    sign_file(artifact, &seed)
}

/// Convenience wrapper: verify an artifact against a `.sig` file and a `pengyos`
/// public-key file.
pub fn verify_keyfile<P: AsRef<Path>>(
    artifact: P,
    sig_path: P,
    pub_path: P,
) -> Result<(), SignError> {
    let data = fs::read(sig_path)?;
    let pubkey = crate::keys::load_public_key(pub_path)?;
    verify_file(artifact, &data, &pubkey)
}

/// Render bytes as a lowercase hex string.
fn hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = Vec::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.push(HEX[(b >> 4) as usize]);
        out.push(HEX[(b & 0x0f) as usize]);
    }
    // Hex is always ASCII; an allocation failure is the only way this fails.
    String::from_utf8(out).expect("hex output is valid UTF-8")
}

/// Decode a 64-hex-char string into 32 bytes (a SHA-256 digest).
fn decode_hex32(hex_str: &str) -> Result<[u8; HASH_LENGTH], SignError> {
    if hex_str.len() != 2 * HASH_LENGTH {
        return Err(SignError::Format(format!(
            "expected {} hex chars for the hash, found {}",
            2 * HASH_LENGTH,
            hex_str.len()
        )));
    }
    let bytes = hex_str.as_bytes();
    let mut out = [0u8; HASH_LENGTH];
    for i in 0..HASH_LENGTH {
        out[i] = hex_nibble(bytes[i * 2])? << 4 | hex_nibble(bytes[i * 2 + 1])?;
    }
    Ok(out)
}

/// Convert a single ASCII hex digit to its value.
fn hex_nibble(c: u8) -> Result<u8, SignError> {
    match c {
        b'0'..=b'9' => Ok(c - b'0'),
        b'a'..=b'f' => Ok(c - b'a' + 10),
        b'A'..=b'F' => Ok(c - b'A' + 10),
        _ => Err(SignError::Format("non-hex character".to_string())),
    }
}

/// `SHA-256(bytes)` rendered as 64 lowercase hex chars.
fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hex(&hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::Signer;
    use std::io::Write;

    /// RFC 8032 Section 7.1, TEST 1 (Ed25519, empty message). Proves the
    /// signing primitive matches the published vector.
    // RFC 8032 Section 7.1, TEST 1 secret key (verbatim, 32 bytes).
    const RFC8032_TEST1_SEED: [u8; 32] = [
        0x9d, 0x61, 0xb1, 0x9d, 0xef, 0xfd, 0x5a, 0x60, 0xba, 0x84, 0x4a, 0xf4, 0x92, 0xec, 0x2c,
        0xc4, 0x44, 0x49, 0xc5, 0x69, 0x7b, 0x32, 0x69, 0x19, 0x70, 0x3b, 0xac, 0x03, 0x1c, 0xae,
        0x7f, 0x60,
    ];
    const RFC8032_TEST1_SIG: &str =
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b";

    /// A self-consistent sign-over-SHA-256 vector used to pin determinism.
    const VECTOR_SEED: [u8; 32] = [
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e,
        0x0f, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d,
        0x1e, 0x1f,
    ];
    const VECTOR_CONTENT: &[u8] = b"pengyos reproducible artifact payload\n";
    const VECTOR_HASH: &str = "699e82b6d0cde2ae130ee3a26249861ccf04f62e1031cb2c3968c845f5fdd1f4";
    const VECTOR_SIG: &str =
        "17781243318895e1b858d373c7ee1b666e6b8bd1bf0d8b0d7d316925baa8b0f663f8a7068f690b4372d3e25070fbc7a6737ae4eee029953694f4b584ec136509";

    fn write_temp(tag: &str, contents: &[u8]) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("pengyos-sig-{}", tag));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(format!("{}.dat", std::process::id()));
        let mut f = std::fs::File::create(&path).unwrap();
        f.write_all(contents).unwrap();
        path
    }

    #[test]
    fn rfc8032_test1_vector_signs_empty_message() {
        // The raw ed25519 signing primitive must match the published RFC 8032
        // vector for the empty message.
        let key = ed25519_dalek::SigningKey::from_bytes(&RFC8032_TEST1_SEED);
        let sig = key.sign(b"");
        assert_eq!(
            hex(&sig.to_bytes()),
            RFC8032_TEST1_SIG,
            "ed25519 signing must match RFC 8032 7.1 TEST 1"
        );
    }

    #[test]
    fn rfc8032_test1_vector_roundtrips() {
        let key = ed25519_dalek::SigningKey::from_bytes(&RFC8032_TEST1_SEED);
        let vk = key.verifying_key();
        let sig = key.sign(b"");
        assert!(vk.verify(b"", &sig).is_ok());
        assert!(vk.verify(b"x", &sig).is_err());
    }

    #[test]
    fn sign_over_sha256_matches_known_vector() {
        let artifact = write_temp("vector", VECTOR_CONTENT);
        let sig_bytes = sign_file(&artifact, &VECTOR_SEED).unwrap();
        let parsed = from_bytes(&sig_bytes).unwrap();
        assert_eq!(hex(&parsed.hash), VECTOR_HASH);
        assert_eq!(hex(&parsed.signature), VECTOR_SIG);
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());
    }

    #[test]
    fn signatures_are_deterministic() {
        let artifact = write_temp("det", VECTOR_CONTENT);
        let a = sign_file(&artifact, &VECTOR_SEED).unwrap();
        let b = sign_file(&artifact, &VECTOR_SEED).unwrap();
        assert_eq!(a, b, "ed25519 (RFC 8032) signatures must be deterministic");
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());
    }

    #[test]
    fn roundtrip_sign_verify_succeeds() {
        let artifact = write_temp("rt", VECTOR_CONTENT);
        let pubkey = ed25519_dalek::SigningKey::from_bytes(&VECTOR_SEED)
            .verifying_key()
            .to_bytes();
        let sig = sign_file(&artifact, &VECTOR_SEED).unwrap();
        assert!(verify_file(&artifact, &sig, &pubkey).is_ok());
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());
    }

    #[test]
    fn tampered_artifact_fails() {
        let dir = std::env::temp_dir().join("pengyos-sig-tamper");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("artifact.bin");
        std::fs::write(&path, VECTOR_CONTENT).unwrap();

        let pubkey = vector_pubkey();
        let sig = sign_file(&path, &VECTOR_SEED).unwrap();

        // Tamper the artifact; the digest must no longer match.
        std::fs::write(&path, b"pengyos reproducible artifact payload TAMPERED\n")
            .unwrap();
        let err = verify_file(&path, &sig, &pubkey).unwrap_err();
        assert!(matches!(err, SignError::HashMismatch));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The verifying key for [`VECTOR_SEED`] (the key that signs the vectors).
    fn vector_pubkey() -> [u8; 32] {
        ed25519_dalek::SigningKey::from_bytes(&VECTOR_SEED)
            .verifying_key()
            .to_bytes()
    }

    /// A *different* verifying key than the one that signed (VECTOR_SEED).
    const OTHER_SEED: [u8; 32] = [0xff; 32];
    fn other_pubkey() -> [u8; 32] {
        ed25519_dalek::SigningKey::from_bytes(&OTHER_SEED)
            .verifying_key()
            .to_bytes()
    }

    #[test]
    fn wrong_key_fails() {
        let artifact = write_temp("wrongkey", VECTOR_CONTENT);
        let sig = sign_file(&artifact, &VECTOR_SEED).unwrap();
        let err = verify_file(&artifact, &sig, &other_pubkey()).unwrap_err();
        assert!(matches!(err, SignError::InvalidSignature));
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());
    }

    #[test]
    fn header_is_one_line_then_raw_signature() {
        let artifact = write_temp("fmt", VECTOR_CONTENT);
        let sig = sign_file(&artifact, &VECTOR_SEED).unwrap();
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());

        // First line is the header; the remainder is exactly 64 raw bytes.
        let newline = sig.iter().position(|&b| b == b'\n').unwrap();
        let header = std::str::from_utf8(&sig[..newline]).unwrap();
        assert_eq!(header.split_whitespace().count(), 6);
        assert!(header.starts_with(SIG_MAGIC));
        let rest = &sig[newline + 1..];
        assert_eq!(rest.len(), SIGNATURE_LENGTH);
    }

    #[test]
    fn header_fields_are_correct() {
        let artifact = write_temp("hdr", VECTOR_CONTENT);
        let sig = sign_file(&artifact, &VECTOR_SEED).unwrap();
        let parsed = from_bytes(&sig).unwrap();
        assert_eq!(parsed.version, SIG_VERSION);
        assert_eq!(parsed.algorithm, ED25519);
        assert_eq!(parsed.hash_algorithm, SHA256_ALGO);
        assert_eq!(hex(&parsed.hash), VECTOR_HASH);
        // keyid is SHA-256 of the verifying key, 64 lowercase hex chars.
        assert_eq!(parsed.keyid.len(), 64);
        assert!(parsed.keyid.chars().all(|c| c.is_ascii_hexdigit()));
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());
    }

    #[test]
    fn from_bytes_rejects_unknown_magic() {
        let bad = b"wrong-magic 1 ed25519 00 sha256 00\n";
        assert!(matches!(
            from_bytes(bad),
            Err(SignError::Format(_))
        ));
    }

    #[test]
    fn from_bytes_rejects_bad_version() {
        let header = format!("{SIG_MAGIC} 99 ed25519 00 sha256 00");
        let mut data = header.into_bytes();
        data.push(b'\n');
        data.extend(std::iter::repeat(0u8).take(SIGNATURE_LENGTH));
        assert!(matches!(
            from_bytes(&data),
            Err(SignError::Format(_))
        ));
    }

    #[test]
    fn from_bytes_rejects_wrong_signature_length() {
        let artifact = write_temp("len", VECTOR_CONTENT);
        let sig = sign_file(&artifact, &VECTOR_SEED).unwrap();
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());
        // Truncate the raw signature to 10 bytes.
        let mut truncated = sig[..sig.len() - SIGNATURE_LENGTH + 10].to_vec();
        truncated.extend(std::iter::repeat(0u8).take(10));
        assert!(matches!(
            from_bytes(&truncated),
            Err(SignError::SignatureLength(_))
        ));
    }

    #[test]
    fn from_bytes_rejects_unsupported_algorithm() {
        let header = format!("{SIG_MAGIC} 1 ed448 00 sha256 00");
        let mut data = header.into_bytes();
        data.push(b'\n');
        data.extend(std::iter::repeat(0u8).take(SIGNATURE_LENGTH));
        assert!(matches!(
            from_bytes(&data),
            Err(SignError::UnsupportedAlgorithm(_))
        ));
    }

    #[test]
    fn from_bytes_rejects_unsupported_hash() {
        let header = format!("{SIG_MAGIC} 1 ed25519 00 sha512 00");
        let mut data = header.into_bytes();
        data.push(b'\n');
        data.extend(std::iter::repeat(0u8).take(SIGNATURE_LENGTH));
        assert!(matches!(
            from_bytes(&data),
            Err(SignError::UnsupportedHash(_))
        ));
    }

    #[test]
    fn from_bytes_rejects_truncated_header() {
        // Only 3 fields, no signature.
        let data = b"pengyos-sig-v1 1 ed25519";
        assert!(matches!(from_bytes(data), Err(SignError::Format(_))));
    }

    #[test]
    fn hash_file_matches_std_sha256() {
        let artifact = write_temp("hash", VECTOR_CONTENT);
        let digest = hash_file(&artifact).unwrap();
        assert_eq!(hex(&digest), VECTOR_HASH);
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());
    }

    #[test]
    fn hash_file_handles_empty_file() {
        let dir = std::env::temp_dir().join("pengyos-sig-empty");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("empty");
        std::fs::write(&path, b"").unwrap();
        let digest = hash_file(&path).unwrap();
        // SHA-256("") as known constant.
        assert_eq!(
            hex(&digest),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn default_sig_path_appends_dot_sig() {
        assert_eq!(
            default_sig_path(Path::new("pengyos.iso")),
            std::path::PathBuf::from("pengyos.iso.sig")
        );
        assert_eq!(
            default_sig_path(Path::new("/tmp/a/b/artifact")),
            std::path::PathBuf::from("/tmp/a/b/artifact.sig")
        );
    }

    #[test]
    fn version_is_current_and_one() {
        // The versioned format: the current version is 1 and is what we emit.
        assert_eq!(SIG_VERSION, 1);
    }

    #[test]
    fn to_bytes_embeds_current_version() {
        let artifact = write_temp("ver", VECTOR_CONTENT);
        let sig = sign_file(&artifact, &VECTOR_SEED).unwrap();
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());

        // The header's second field must be the current format version.
        let newline = sig.iter().position(|&b| b == b'\n').unwrap();
        let header = std::str::from_utf8(&sig[..newline]).unwrap();
        let fields: Vec<&str> = header.split_whitespace().collect();
        assert_eq!(fields[0], SIG_MAGIC);
        assert_eq!(fields[1], SIG_VERSION.to_string());
    }

    #[test]
    fn signature_version_roundtrips() {
        // A Signature round-trips through to_bytes/from_bytes with its version
        // preserved — this is what "versioning" means at the data level.
        let artifact = write_temp("vrt", VECTOR_CONTENT);
        let sig_bytes = sign_file(&artifact, &VECTOR_SEED).unwrap();
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());

        let parsed = from_bytes(&sig_bytes).unwrap();
        assert_eq!(parsed.version, SIG_VERSION);
        assert_eq!(parsed.version, 1);
    }

    #[test]
    fn encoder_output_matches_documented_header_shape() {
        // Lock the reference encoder to the documented format: exactly six
        // whitespace-separated header fields, magic + current version first,
        // then algorithm, keyid, hash-algorithm, hash.
        let artifact = write_temp("shape", VECTOR_CONTENT);
        let sig_bytes = sign_file(&artifact, &VECTOR_SEED).unwrap();
        let _ = std::fs::remove_dir_all(artifact.parent().unwrap());

        let newline = sig_bytes.iter().position(|&b| b == b'\n').unwrap();
        let header = std::str::from_utf8(&sig_bytes[..newline]).unwrap();
        let fields: Vec<&str> = header.split_whitespace().collect();
        assert_eq!(fields.len(), 6, "documented header has six fields");
        assert_eq!(fields[0], "pengyos-sig-v1");
        assert_eq!(fields[1], "1");
        assert_eq!(fields[2], "ed25519");
        assert_eq!(fields[4], "sha256");
        // keyid and hash are 64 lowercase hex chars.
        for &f in &[fields[3], fields[5]] {
            assert_eq!(f.len(), 64);
            assert!(f.chars().all(|c| c.is_ascii_hexdigit()));
            assert_eq!(f, f.to_lowercase());
        }
    }

    #[test]
    fn sign_verify_large_streaming_file() {
        // A multi-megabyte artifact to exercise the streaming hash path.
        let dir = std::env::temp_dir().join("pengyos-sig-large");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("large.bin");
        {
            let mut f = std::fs::File::create(&path).unwrap();
            let chunk = [0xabu8; READ_CHUNK];
            for _ in 0..400 {
                // ~25 MiB total, spanning many chunks.
                f.write_all(&chunk).unwrap();
            }
        }

        let pubkey = vector_pubkey();
        let sig = sign_file(&path, &VECTOR_SEED).unwrap();
        assert!(
            verify_file(&path, &sig, &pubkey).is_ok(),
            "sign/verify must succeed over a large artifact"
        );

        // Tampering a single byte in the middle of the artifact must be
        // caught once the stream is re-hashed.
        let mut blob = std::fs::read(&path).unwrap();
        blob[5 * 1024 * 1024] ^= 0xff;
        std::fs::write(&path, &blob).unwrap();
        assert!(
            verify_file(&path, &sig, &pubkey).is_err(),
            "tampering a large artifact must be detected"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }
}
