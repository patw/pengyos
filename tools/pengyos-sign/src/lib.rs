//! `pengyos_sign` — minimal ed25519 artifact signing for PengyOS.
//!
//! This crate provides a small, dependency-light library plus a `pengyos-sign`
//! CLI for signing and verifying build artifacts with ed25519 signatures.
//!
//! ## Modules
//!
//! - [`keys`] — ed25519 key generation and key-file I/O (the `keygen` surface).
//!   Key files use a small, versioned, self-describing text format.
//! - [`signature`] — detached, versioned ed25519 signatures over a file's
//!   SHA-256 digest (the `sign` / `verify` surface) and the documented wire
//!   format (see [`signature`]).
//!
//! ## Current scope
//!
//! `keygen` lives in [`keys`](keys); `sign` / `verify` in
//! [`signature`](signature).

/// The `keys` module: ed25519 key generation and key-file I/O.
pub mod keys;

/// The `signature` module: detached ed25519 signatures and the wire format.
pub mod signature;

/// Public-key file loaded by `verify` (see [`keys::load_public_key`]).
pub use keys::load_public_key;

/// Signing (private) key seed loaded by `sign` (see [`keys::load_signing_seed`]).
pub use keys::load_signing_seed;

/// Key generation errors (see [`keys::KeyError`]).
pub use keys::KeyError;

/// An ed25519 key pair (see [`keys::KeyPair`]).
pub use keys::KeyPair;

/// Detached signature type and wire format (see [`signature::Signature`]).
pub use signature::Signature;

/// Sign an artifact (see [`signature::sign_file`]).
pub use signature::sign_file;

/// Verify an artifact (see [`signature::verify_file`]).
pub use signature::verify_file;

/// Sign an artifact from a key file (see [`signature::sign_keyfile`]).
pub use signature::sign_keyfile;

/// Verify an artifact from a signature file and public key (see
/// [`signature::verify_keyfile`]).
pub use signature::verify_keyfile;

/// Default signature file path for an artifact (see
/// [`signature::default_sig_path`]).
pub use signature::default_sig_path;

/// Canonical package name (the binary name).
pub const NAME: &str = "pengyos-sign";

/// Crate version, pinned from `Cargo.toml` at build time.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// One-line human-readable banner, e.g. `pengyos-sign 0.2.0`.
pub fn banner() -> String {
    format!("{NAME} {VERSION}")
}

/// Usage text shown by `pengyos-sign --help`.
///
/// Kept as a single const so the CLI and the README can share the exact
/// wording (docs stay in sync with code).
pub const USAGE: &str = "\
pengyos-sign — minimal ed25519 artifact signing

USAGE:
    pengyos-sign [COMMAND] [OPTIONS]

COMMANDS:
    keygen    Generate an ed25519 key pair (private key, 0600; derived public key)
    sign      Produce a detached signature over a file
    verify    Verify a detached signature over a file

GLOBAL OPTIONS:
    -h, --help      Print help
    -V, --version   Print version

Subcommands and their flags are documented in the README.
";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn banner_contains_name_and_version() {
        let b = banner();
        assert!(
            b == format!("{} {}", NAME, VERSION),
            "unexpected banner: {b}"
        );
    }

    #[test]
    fn version_matches_manifest() {
        // VERSION is baked from CARGO_PKG_VERSION; sanity-check it parses as
        // a semver-ish string and is non-empty.
        assert_eq!(VERSION, env!("CARGO_PKG_VERSION"));
        assert!(!VERSION.is_empty());
        assert!(VERSION.contains('.'), "VERSION should be semver-like: {VERSION}");
    }

    #[test]
    fn usage_documents_all_subcommands() {
        for cmd in ["keygen", "sign", "verify"] {
            assert!(
                USAGE.contains(cmd),
                "USAGE text must mention the `{cmd}` subcommand"
            );
        }
    }

    #[test]
    fn keys_are_reexported() {
        // The key API must be reachable at the crate root for CLI/README wiring.
        assert!(std::path::Path::new("src/keys.rs").exists());
        // KeyPair is constructible and generates without error.
        let _kp = KeyPair::generate().unwrap();
    }
}
