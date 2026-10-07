//! Doc-consistency contract for the signature wire format.
//!
//! The `.sig` wire format is defined in `FORMAT.md` (the authoritative spec) and
//! implemented by `src/signature.rs` (the reference encoder/decoder). This test
//! locks the documentation to the code: it asserts that `FORMAT.md` exists and
//! documents the fields, magic, and version that the implementation actually
//! emits. This is the Rust analog of the GitHub-ready `test_github_ready`
//! pattern — a fast, offline, source-level check that the shipped docs describe
//! the shipped format.

use std::path::PathBuf;

/// Directory containing Cargo.toml / FORMAT.md — the crate root.
fn repo_root() -> PathBuf {
    std::env::var("CARGO_MANIFEST_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

#[test]
fn format_spec_exists() {
    let path = repo_root().join("FORMAT.md");
    assert!(
        path.is_file(),
        "FORMAT.md (the signature wire-format spec) must exist at {}",
        path.display()
    );
}

#[test]
fn format_spec_documents_the_magic() {
    let text = std::fs::read_to_string(repo_root().join("FORMAT.md"))
        .expect("FORMAT.md must be readable");
    // The magic is the single most identifying byte of the format.
    assert!(
        text.contains("pengyos-sig-v1"),
        "FORMAT.md must document the signature magic `pengyos-sig-v1`"
    );
}

#[test]
fn format_spec_documents_all_six_fields() {
    let text = std::fs::read_to_string(repo_root().join("FORMAT.md"))
        .expect("FORMAT.md must be readable");
    // The six header fields, in the order they appear on the wire.
    for field in ["magic", "version", "algorithm", "keyid", "hash-algorithm", "hash"] {
        assert!(
            text.contains(field),
            "FORMAT.md must document the `{field}` header field"
        );
    }
}

#[test]
fn format_spec_documents_the_current_version() {
    // The documented version must equal the implementation's current version.
    assert_eq!(pengyos_sign::signature::SIG_VERSION, 1);
    let text = std::fs::read_to_string(repo_root().join("FORMAT.md"))
        .expect("FORMAT.md must be readable");
    assert!(
        text.contains("version 1") || text.contains("version `1`") || text.contains("`1`"),
        "FORMAT.md must document the current format version 1"
    );
}

#[test]
fn format_spec_documents_the_versioning_policy() {
    let text = std::fs::read_to_string(repo_root().join("FORMAT.md"))
        .expect("FORMAT.md must be readable");
    assert!(
        text.to_lowercase().contains("versioning"),
        "FORMAT.md must describe the versioning policy"
    );
    // A key part of the policy: verifiers reject unknown versions.
    assert!(
        text.contains("reject"),
        "FORMAT.md must state that unknown versions are rejected"
    );
}

#[test]
fn format_spec_matches_the_reference_encoder() {
    // The documented header shape (magic + version + algorithm + hash-algorithm)
    // must match what the reference encoder actually produces.
    use pengyos_sign::signature::{from_bytes, SIG_MAGIC, SIG_VERSION};

    let dir = std::env::temp_dir().join("pengyos-format-doc");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("artifact.bin");
    std::fs::write(&path, b"pengyos reproducible artifact payload\n").unwrap();

    let seed = [
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e,
        0x0f, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d,
        0x1e, 0x1f,
    ];
    let sig = pengyos_sign::signature::sign_file(&path, &seed).unwrap();
    let parsed = from_bytes(&sig).unwrap();

    assert_eq!(parsed.version, SIG_VERSION);
    assert_eq!(parsed.algorithm, "ed25519");
    assert_eq!(parsed.hash_algorithm, "sha256");
    assert_eq!(SIG_MAGIC, "pengyos-sig-v1");

    let _ = std::fs::remove_dir_all(&dir);
}
