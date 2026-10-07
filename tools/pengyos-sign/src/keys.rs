//! ed25519 key generation and key-file I/O for `pengyos_sign`.
//!
//! This module implements the `keygen` surface. It generates an ed25519 key
//! pair, writes the private key with `0600` permissions, and derives the public
//! key. Key files use a small, versioned, self-describing text format (see
//! [`PRIVATE_MAGIC`] / [`PUBLIC_MAGIC`]) so both humans and later subtasks
//! (`sign`/`verify`) can read them back deterministically.
//!
//! [`crate`]: crate

use std::fs;
use std::io;
use std::path::Path;

use ed25519_dalek::SigningKey;
use sha2::{Digest, Sha256};

/// Algorithm identifier for ed25519 signing keys.
pub const ED25519: &str = "ed25519";

/// Header magic for the private-key file format (`pengyos-key-v1`).
pub const PRIVATE_MAGIC: &str = "pengyos-key-v1";

/// Header magic for the public-key file format (`pengyos-key-v1`).
pub const PUBLIC_MAGIC: &str = "pengyos-key-v1";

/// Length in bytes of an ed25519 signing seed (and the private key material).
pub const SEED_LENGTH: usize = 32;

/// Length in bytes of an ed25519 public (verifying) key.
pub const PUBLIC_KEY_LENGTH: usize = 32;

/// Length in bytes of a SHA-256 key id.
pub const KEYID_LENGTH: usize = 32;

/// The `pengyos-sign` exit code for a generic runtime error (key I/O, format).
pub const EXIT_RUNTIME_ERROR: u8 = 1;

/// Errors produced by key generation and key-file I/O.
///
/// The variant messages are intentionally generic and never contain key
/// material: a failed `keygen`/`load` must not leak a seed or public key to
/// stderr.
#[derive(Debug)]
pub enum KeyError {
    /// An underlying filesystem / OS error.
    Io(io::Error),
    /// A key file is malformed or not a recognized `pengyos` key.
    Format(String),
    /// A key file names an algorithm this build does not support.
    UnsupportedAlgorithm(String),
}

impl std::fmt::Display for KeyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            KeyError::Io(err) => write!(f, "key I/O error: {err}"),
            KeyError::Format(msg) => write!(f, "malformed key file: {msg}"),
            KeyError::UnsupportedAlgorithm(algo) => {
                write!(f, "unsupported key algorithm: {algo}")
            }
        }
    }
}

impl std::error::Error for KeyError {}

impl From<io::Error> for KeyError {
    fn from(err: io::Error) -> Self {
        KeyError::Io(err)
    }
}

/// A generated ed25519 key pair.
///
/// The [`SigningKey`] from `ed25519-dalek` already zeroes its secret scalar on
/// drop; this wrapper simply bundles the signing key with derived views
/// (public key, key id) and the key-file serialization used by `keygen`.
pub struct KeyPair {
    signing_key: SigningKey,
}

impl KeyPair {
    /// Generate a fresh random ed25519 key pair.
    ///
    /// The 32-byte seed is drawn from the operating system CSPRNG
    /// (`getrandom`); the signing key is derived from it via
    /// [`SigningKey::from_bytes`] (RFC 8032).
    pub fn generate() -> Result<Self, KeyError> {
        let mut seed = [0u8; SEED_LENGTH];
        getrandom::getrandom(&mut seed)
            .map_err(|e| KeyError::Format(format!("failed to read CSPRNG bytes: {e}")))?;
        Ok(Self {
            signing_key: SigningKey::from_bytes(&seed),
        })
    }

    /// The 32-byte signing seed (the raw private key material).
    pub fn seed(&self) -> [u8; SEED_LENGTH] {
        self.signing_key.to_bytes()
    }

    /// The 32-byte ed25519 public (verifying) key.
    pub fn public_key(&self) -> [u8; PUBLIC_KEY_LENGTH] {
        self.signing_key.verifying_key().to_bytes()
    }

    /// The 64-hex-char key id: `SHA-256(public_key)` rendered as lowercase hex.
    ///
    /// The key id binds a signature to the key that produced it, so a verifier
    /// holding several public keys can select the right one.
    pub fn keyid(&self) -> String {
        hex(&Self::keyid_bytes(&self.public_key()))
    }

    /// `SHA-256(public_key)` as raw bytes (helper shared with `keyid`).
    fn keyid_bytes(public_key: &[u8; PUBLIC_KEY_LENGTH]) -> [u8; KEYID_LENGTH] {
        let mut hasher = Sha256::new();
        hasher.update(public_key);
        let digest = hasher.finalize();
        let mut out = [0u8; KEYID_LENGTH];
        out.copy_from_slice(&digest);
        out
    }

    /// Serialize this key pair to the private-key file format (one line).
    pub fn to_private_bytes(&self) -> Vec<u8> {
        format!(
            "{magic} {algo} {keyid} {seed}\n",
            magic = PRIVATE_MAGIC,
            algo = ED25519,
            keyid = self.keyid(),
            seed = hex(&self.seed()),
        )
        .into_bytes()
    }

    /// Serialize this key pair to the public-key file format (one line).
    pub fn to_public_bytes(&self) -> Vec<u8> {
        format!(
            "{magic} {algo} {keyid} {pubkey}\n",
            magic = PUBLIC_MAGIC,
            algo = ED25519,
            keyid = self.keyid(),
            pubkey = hex(&self.public_key()),
        )
        .into_bytes()
    }

    /// Write the private key to `path` with `0600` permissions.
    ///
    /// The file is created/truncated, its contents written, and its mode forced
    /// to `0600` (owner read/write only) so the seed is never world-readable.
    pub fn write_private<P: AsRef<Path>>(&self, path: P) -> Result<(), KeyError> {
        write_private_key(path.as_ref(), &self.to_private_bytes())
    }

    /// Write the public key to `path`.
    ///
    /// The public key is not secret, but the file is still written with a
    /// conservative mode: on unix it is forced to `0640` (owner read/write,
    /// group read, no "other" bits) so it is never world-readable or writable.
    pub fn write_public<P: AsRef<Path>>(&self, path: P) -> Result<(), KeyError> {
        let bytes = self.to_public_bytes();
        fs::write(&path, &bytes)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(path.as_ref())?.permissions();
            perms.set_mode(0o640);
            fs::set_permissions(path.as_ref(), perms)?;
        }
        Ok(())
    }
}

/// Write private-key bytes to `path`, forcing `0600` permissions.
///
/// The file is created (truncating any existing file) and its mode is set to
/// `0600`. The write-then-chmod ordering is the portable, dependency-free way to
/// enforce the mode with the standard library; a short, unavoidable window
/// exists between creation and the chmod.
fn write_private_key(path: &Path, bytes: &[u8]) -> Result<(), KeyError> {
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)?;
    std::io::Write::write_all(&mut file, bytes)?;
    drop(file);

    // Force 0600 on unix so the seed is never world-readable. Gated for
    // portability; on non-unix the file keeps its default (umask) perms.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(path)?.permissions();
        perms.set_mode(0o600);
        fs::set_permissions(path, perms)?;
    }
    Ok(())
}

/// Load a signing (private) key from a `pengyos` key file.
///
/// Used by later subtasks (`sign`); returns the raw 32-byte seed so callers can
/// reconstruct a [`SigningKey`].
pub fn load_signing_seed<P: AsRef<Path>>(path: P) -> Result<[u8; SEED_LENGTH], KeyError> {
    let line = read_first_nonempty_line(path.as_ref())?;
    let fields: Vec<&str> = line.split_whitespace().collect();
    let (seed_hex, algo, _magic, keyid) = parse_key_fields(&fields, PRIVATE_MAGIC)?;

    let seed = decode_seed(seed_hex)?;
    // Defensively verify the on-disk key matches the derived public key so a
    // tampered or mismatched file is rejected before any signing attempt.
    let signing_key = SigningKey::from_bytes(&seed);
    let derived_keyid = KeyPair { signing_key }.keyid();
    if keyid.map_or(false, |k| k != derived_keyid) {
        return Err(KeyError::Format(
            "key id does not match the provided seed".to_string(),
        ));
    }
    let _ = algo;
    Ok(seed)
}

/// Load a public key from a `pengyos` key file.
///
/// Returns the raw 32-byte ed25519 public key.
pub fn load_public_key<P: AsRef<Path>>(path: P) -> Result<[u8; PUBLIC_KEY_LENGTH], KeyError> {
    let line = read_first_nonempty_line(path.as_ref())?;
    let fields: Vec<&str> = line.split_whitespace().collect();
    let (_pubkey_hex, algo, magic, _keyid) = parse_key_fields(&fields, PUBLIC_MAGIC)?;

    // The payload field of a public key file is the 32-byte public key itself.
    decode_seed(_pubkey_hex)
        .map(|bytes| {
            let mut out = [0u8; PUBLIC_KEY_LENGTH];
            out.copy_from_slice(&bytes);
            out
        })
        .and_then(|pubkey| {
            // Sanity-check the key is a valid curve point.
            ed25519_dalek::VerifyingKey::from_bytes(&pubkey)
                .map(|_| pubkey)
                .map_err(|e| {
                    KeyError::Format(format!("invalid ed25519 public key: {e}"))
                })
        })
        .and_then(|pubkey| {
            let _ = algo;
            let _ = magic;
            Ok(pubkey)
        })
}

/// Read the first non-empty, trimmed line from a key file.
fn read_first_nonempty_line<P: AsRef<Path>>(path: P) -> Result<String, KeyError> {
    let text = fs::read_to_string(path)?;
    text.lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .ok_or_else(|| KeyError::Format("key file is empty".to_string()))
        .map(str::to_string)
}

/// Validate the leading fields of a key file line and return the payload.
///
/// Returns `(payload_field, algorithm, magic, keyid_or_none)`. The `expected`
/// magic must match, and the algorithm must be [`ED25519`].
fn parse_key_fields<'a>(
    fields: &'a [&'a str],
    expected: &str,
) -> Result<(&'a str, &'a str, &'a str, Option<&'a str>), KeyError> {
    // magic algo keyid <payload>
    if fields.len() < 4 {
        return Err(KeyError::Format(format!(
            "expected at least 4 fields, found {}",
            fields.len()
        )));
    }
    let magic = fields[0];
    let algo = fields[1];
    let keyid = fields[2];
    let payload = fields[3];

    if magic != expected {
        return Err(KeyError::Format(format!(
            "unexpected key magic `{magic}` (expected `{expected}`)"
        )));
    }
    if algo != ED25519 {
        return Err(KeyError::UnsupportedAlgorithm(algo.to_string()));
    }
    Ok((payload, algo, magic, Some(keyid)))
}

/// Decode a 64-hex-char string into 32 bytes (seed or public key).
fn decode_seed(hex_str: &str) -> Result<[u8; SEED_LENGTH], KeyError> {
    if hex_str.len() != 2 * SEED_LENGTH {
        return Err(KeyError::Format(format!(
            "expected {} hex chars, found {}",
            2 * SEED_LENGTH,
            hex_str.len()
        )));
    }
    let bytes = hex_str.as_bytes();
    let mut out = [0u8; SEED_LENGTH];
    for i in 0..SEED_LENGTH {
        out[i] = hex_nibble(bytes[i * 2])? << 4 | hex_nibble(bytes[i * 2 + 1])?;
    }
    Ok(out)
}

/// Convert a single ASCII hex digit to its value.
fn hex_nibble(c: u8) -> Result<u8, KeyError> {
    match c {
        b'0'..=b'9' => Ok(c - b'0'),
        b'a'..=b'f' => Ok(c - b'a' + 10),
        b'A'..=b'F' => Ok(c - b'A' + 10),
        _ => Err(KeyError::Format("non-hex character".to_string())),
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    // `sign`/`verify` live on these traits; bring them into scope for the
    // round-trip test below.
    use ed25519_dalek::{Signer, Verifier};

    #[test]
    fn hex_roundtrip() {
        let bytes = [0x00, 0x0f, 0xf0, 0xab, 0xcd, 0xef];
        let h = hex(&bytes);
        assert_eq!(h, "000ff0abcdef");
        // decode_seed expects 32 bytes -> 64 hex chars; build one.
        let seed = [42u8; 32];
        let h = hex(&seed);
        assert_eq!(h.len(), 64);
        let decoded = decode_seed(&h).unwrap();
        assert_eq!(decoded, seed);
    }

    #[test]
    fn hex_nibble_accepts_upper_and_lower() {
        assert_eq!(hex_nibble(b'a').unwrap(), 10);
        assert_eq!(hex_nibble(b'F').unwrap(), 15);
        assert!(hex_nibble(b'x').is_err());
    }

    #[test]
    fn generate_produces_valid_keypair() {
        let kp = KeyPair::generate().unwrap();
        assert_eq!(kp.seed().len(), SEED_LENGTH);
        assert_eq!(kp.public_key().len(), PUBLIC_KEY_LENGTH);
        // The public key must be a valid ed25519 point.
        ed25519_dalek::VerifyingKey::from_bytes(&kp.public_key())
            .expect("generated public key must verify as a curve point");
        // keyid is 64 lowercase hex chars.
        assert_eq!(kp.keyid().len(), 64);
        assert!(kp.keyid().chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn generate_is_deterministic_given_seed() {
        // Same seed -> same key pair (ed25519 is deterministic under RFC 8032).
        let seed = [7u8; 32];
        let mut a = [0u8; 32];
        a.copy_from_slice(&seed);
        let mut b = [0u8; 32];
        b.copy_from_slice(&seed);
        let ka = SigningKey::from_bytes(&a);
        let kb = SigningKey::from_bytes(&b);
        assert_eq!(ka.to_bytes(), kb.to_bytes());
        assert_eq!(ka.verifying_key().to_bytes(), kb.verifying_key().to_bytes());
    }

    #[test]
    fn two_generations_differ() {
        let a = KeyPair::generate().unwrap();
        let b = KeyPair::generate().unwrap();
        assert_ne!(
            a.seed(),
            b.seed(),
            "two independent generations should differ"
        );
    }

    #[test]
    fn keyid_is_sha256_of_public_key() {
        let kp = KeyPair::generate().unwrap();
        let expected = hex(&KeyPair::keyid_bytes(&kp.public_key()));
        assert_eq!(kp.keyid(), expected);
    }

    #[test]
    fn sign_and_verify_roundtrip() {
        let kp = KeyPair::generate().unwrap();
        let msg = b"pengyos artifact payload";
        let sig = kp.signing_key.sign(msg);
        let vk = ed25519_dalek::VerifyingKey::from_bytes(&kp.public_key()).unwrap();
        assert!(vk.verify(msg, &sig).is_ok());
        assert!(vk.verify(b"tampered", &sig).is_err());
    }

    #[test]
    fn private_bytes_format_is_one_line() {
        let kp = KeyPair::generate().unwrap();
        let bytes = kp.to_private_bytes();
        let text = String::from_utf8(bytes).unwrap();
        let line = text.trim();
        let fields: Vec<&str> = line.split_whitespace().collect();
        assert_eq!(fields.len(), 4, "expected magic algo keyid seed");
        assert_eq!(fields[0], PRIVATE_MAGIC);
        assert_eq!(fields[1], ED25519);
        assert_eq!(fields[2], kp.keyid());
        assert_eq!(fields[3], hex(&kp.seed()));
    }

    #[test]
    fn public_bytes_format_is_one_line() {
        let kp = KeyPair::generate().unwrap();
        let text = String::from_utf8(kp.to_public_bytes()).unwrap();
        let fields: Vec<&str> = text.trim().split_whitespace().collect();
        assert_eq!(fields.len(), 4);
        assert_eq!(fields[0], PUBLIC_MAGIC);
        assert_eq!(fields[1], ED25519);
        assert_eq!(fields[3], hex(&kp.public_key()));
    }

    #[test]
    fn load_public_key_matches_generated() {
        let dir = std::env::temp_dir().join(format!("pengyos-pub-{}", unique()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("signing.pub");
        let kp = KeyPair::generate().unwrap();
        kp.write_public(&path).unwrap();
        let loaded = load_public_key(&path).unwrap();
        assert_eq!(loaded, kp.public_key());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn load_signing_seed_matches_generated() {
        let dir = std::env::temp_dir().join(format!("pengyos-priv-{}", unique()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("signing.key");
        let kp = KeyPair::generate().unwrap();
        kp.write_private(&path).unwrap();
        let loaded = load_signing_seed(&path).unwrap();
        assert_eq!(loaded, kp.seed());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn load_rejects_wrong_magic() {
        let dir = std::env::temp_dir().join(format!("pengyos-bad-{}", unique()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("bad.key");
        std::fs::write(&path, b"wrong-magic ed25519 00 00\n").unwrap();
        assert!(matches!(
            load_signing_seed(&path),
            Err(KeyError::Format(_))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn load_rejects_short_file() {
        let dir = std::env::temp_dir().join(format!("pengyos-short-{}", unique()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("short.key");
        std::fs::write(&path, b"pengyos-key-v1\n").unwrap();
        assert!(matches!(
            load_signing_seed(&path),
            Err(KeyError::Format(_))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A process-unique suffix for temp dirs so parallel tests don't collide.
    fn unique() -> u64 {
        use std::time::{SystemTime, UNIX_EPOCH};
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0)
    }
}
