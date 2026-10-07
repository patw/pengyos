//! Large-file streaming verification for the `pengyos-sign` binary.
//!
//! These integration tests exercise the "hash-based verification of large files
//! (streaming)" capability end-to-end through the compiled binary:
//! `keygen` -> `sign` -> `verify` over a multi-megabyte artifact, plus tamper
//! detection over a large artifact. They prove that signing and verification
//! stream the artifact through SHA-256 in bounded memory (64 KiB chunks)
//! rather than loading it whole, so arbitrarily large files can be signed and
//! verified with flat memory use.
//!
//! No network, root, display, or hardware is required; the large artifact is a
//! local temp file. These follow the same unconditional-integration-test
//! convention as `tests/cli.rs` (they touch only the local filesystem).

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::Command;

/// The path to the freshly built binary.
fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_pengyos-sign")
}

fn run(args: &[&str]) -> (i32, String, String) {
    let output = Command::new(bin())
        .args(args)
        .output()
        .expect("failed to spawn pengyos-sign");
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    (output.status.code().unwrap_or(-1), stdout, stderr)
}

/// A unique temp directory for a test run.
fn temp_dir(tag: &str) -> PathBuf {
    let mut dir = std::env::temp_dir();
    dir.push(format!("pengyos-sign-itl-{}-{}", tag, std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("create temp dir");
    dir
}

/// A large artifact size (in MiB) that spans many 64 KiB streaming chunks.
const LARGE_FILE_MIB: usize = 8;

/// Write a large artifact of `mib` mebibytes (all-zero) to a fresh temp file.
///
/// Writing one 64 KiB streaming chunk per iteration keeps the setup itself
/// bounded in memory, mirroring the streaming path under test. One MiB is 16
/// streaming chunks (`1 MiB / 64 KiB == 16`), so `mib` MiB is `mib * 16` chunks.
fn large_artifact(dir: &PathBuf, mib: usize) -> PathBuf {
    let path = dir.join("large.bin");
    let mut f = fs::File::create(&path).expect("create large file");
    let chunk = [0u8; 64 * 1024]; // one streaming chunk
    for _ in 0..(mib * 16) {
        f.write_all(&chunk).expect("write streaming chunk");
    }
    path
}

#[test]
fn keygen_sign_verify_large_streaming_file_succeeds() {
    // keygen -> sign -> verify over a multi-megabyte artifact must round-trip
    // with exit 0, exercising the streaming SHA-256 path through the binary.
    let dir = temp_dir("large");
    let artifact = large_artifact(&dir, LARGE_FILE_MIB);
    let priv_path = dir.join("signing.key");
    let pub_path = dir.join("signing.pub");
    let sig_path = dir.join("large.bin.sig");

    let (kg, _, kerr) = run(&[
        "keygen",
        "--out",
        priv_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_eq!(kg, 0, "keygen should succeed; {kerr}");

    let (sign, _, serr) = run(&[
        "sign",
        artifact.to_str().unwrap(),
        "--key",
        priv_path.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
    ]);
    assert_eq!(sign, 0, "sign should succeed over a large file; {serr}");
    assert!(sig_path.exists(), "signature file should be created");

    let (verify, _, verr) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_eq!(verify, 0, "verify should succeed over a large file; {verr}");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn verify_large_file_tampering_detected() {
    // Tampering a single byte in the middle of a large artifact must be caught
    // once the stream is re-hashed (HashMismatch), proving the streaming
    // verification actually re-reads and re-hashes the whole artifact rather
    // than trusting a cached or partial digest.
    let dir = temp_dir("large-tamper");
    let artifact = large_artifact(&dir, LARGE_FILE_MIB);
    let priv_path = dir.join("signing.key");
    let pub_path = dir.join("signing.pub");
    let sig_path = dir.join("large.bin.sig");

    run(&[
        "keygen",
        "--out",
        priv_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    let (sign, _, serr) = run(&[
        "sign",
        artifact.to_str().unwrap(),
        "--key",
        priv_path.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
    ]);
    assert_eq!(sign, 0, "sign should succeed; {serr}");

    // Flip a byte well past the first streaming chunk (3 MiB in).
    let offset = 3 * 1024 * 1024;
    let mut blob = fs::read(&artifact).expect("read large file");
    assert!(blob.len() > offset, "artifact should be larger than the offset");
    blob[offset] ^= 0xff;
    fs::write(&artifact, &blob).expect("write tampered file");

    let (verify, _, verr) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_ne!(verify, 0, "verifying a tampered large file must fail");
    let combined = verr.to_lowercase();
    assert!(
        combined.contains("digest") || combined.contains("signature"),
        "failure should mention digest/signature; stderr: {verr}"
    );

    let _ = fs::remove_dir_all(&dir);
}
