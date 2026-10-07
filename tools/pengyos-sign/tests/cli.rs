//! Integration tests for the `pengyos-sign` binary.
//!
//! These run the compiled binary via the `CARGO_BIN_EXE_pengyos-sign` env var
//! that Cargo sets for integration tests — no hard-coded paths.

use std::os::unix::fs::PermissionsExt;
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
    dir.push(format!(
        "pengyos-sign-it-{}-{}",
        tag,
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("create temp dir");
    dir
}

#[test]
fn no_args_prints_usage_and_succeeds() {
    let (code, stdout, _stderr) = run(&[]);
    assert_eq!(code, 0, "no-args should exit 0");
    assert!(stdout.contains("pengyos-sign"), "usage should name the tool");
    for cmd in ["keygen", "sign", "verify"] {
        assert!(stdout.contains(cmd), "usage should mention `{cmd}`");
    }
}

#[test]
fn help_flag_prints_usage() {
    let (code, stdout, _stderr) = run(&["--help"]);
    assert_eq!(code, 0);
    assert!(stdout.contains("USAGE"));
}

#[test]
fn version_flag_prints_banner() {
    let (code, stdout, _stderr) = run(&["--version"]);
    assert_eq!(code, 0);
    let expected = format!("{} {}", pengyos_sign::NAME, pengyos_sign::VERSION);
    assert!(
        stdout.trim() == expected,
        "version output should be `{expected}`, got `{}`",
        stdout.trim()
    );
}

#[test]
fn unimplemented_subcommand_reports_cleanly() {
    // `frobnicate` was never implemented; it must exit with the dedicated
    // not-implemented code and print a clear message — never panic or leak
    // anything.
    let (code, _stdout, stderr) = run(&["frobnicate"]);
    assert_eq!(code, 3, "an unimplemented subcommand should exit 3");
    assert!(stderr.contains("not implemented"), "stderr was: {stderr}");
}

#[test]
fn keygen_missing_out_is_usage_error() {
    let (code, _stdout, stderr) = run(&["keygen"]);
    assert_eq!(code, 2, "missing --out should be a usage error");
    assert!(
        stderr.contains("missing required option"),
        "stderr was: {stderr}"
    );
}

#[test]
fn keygen_writes_keys_with_0600_and_no_leak() {
    let dir = temp_dir("keygen");
    let priv_path = dir.join("signing.key");
    let pub_path = dir.join("signing.pub");
    // No --pub: the public key is derived by replacing `.key` with `.pub`.

    let (code, stdout, stderr) = run(&[
        "keygen",
        "--out",
        priv_path.to_str().unwrap(),
    ]);
    assert_eq!(code, 0, "keygen should succeed; stderr: {stderr}");

    // Both key files exist.
    assert!(priv_path.exists(), "private key file missing");
    assert!(pub_path.exists(), "derived public key file missing");

    // Private key must be 0600; public key must NOT be world-readable.
    let priv_mode = std::fs::metadata(&priv_path).unwrap().permissions().mode() & 0o777;
    assert_eq!(priv_mode, 0o600, "private key mode must be 0600, got {priv_mode:#o}");
    let pub_mode = std::fs::metadata(&pub_path).unwrap().permissions().mode() & 0o777;
    assert_eq!(
        pub_mode & 0o007,
        0,
        "public key must not be world-readable, got {pub_mode:#o}"
    );

    // stdout reports the paths and a 64-hex-char key id ...
    assert!(stdout.contains(priv_path.to_str().unwrap()));
    assert!(stdout.contains(pub_path.to_str().unwrap()));
    let keyid_line = stdout.lines().find(|l| l.contains("key id"));
    assert!(keyid_line.is_some(), "stdout should report a key id; was: {stdout}");
    let keyid = keyid_line.unwrap().split_whitespace().last().unwrap();
    assert_eq!(keyid.len(), 64, "key id should be 64 hex chars");
    assert!(keyid.chars().all(|c| c.is_ascii_hexdigit()));

    // ... and no key material leaked to stdout or stderr.
    // The private key file legitimately embeds the 64-hex seed and key id; what
    // must never appear in the command output is the seed itself (or the whole
    // file). stdout is expected to carry the public key id.
    let priv_contents = std::fs::read_to_string(&priv_path).unwrap();
    let seed_hex = priv_contents
        .split_whitespace()
        .last()
        .expect("private key file has at least one field");
    assert!(
        !stdout.contains(seed_hex) && !stderr.contains(seed_hex),
        "seed leaked to output; stdout={stdout} stderr={stderr}"
    );
    assert!(
        !stdout.contains(&priv_contents) && !stderr.contains(&priv_contents),
        "private key contents leaked to output"
    );

    let _ = std::fs::remove_dir_all(&dir);
}

/// Write a small sample artifact to a fresh temp dir and return its path.
fn sample_artifact(tag: &str, contents: &[u8]) -> PathBuf {
    let dir = temp_dir(tag);
    let path = dir.join("artifact.bin");
    std::fs::write(&path, contents).expect("write artifact");
    path
}

#[test]
fn keygen_respects_explicit_pub_path() {
    let dir = temp_dir("keygen-pub");
    let priv_path = dir.join("k.key");
    let pub_path = dir.join("custom.pub");

    let (code, _stdout, stderr) = run(&[
        "keygen",
        "--out",
        priv_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_eq!(code, 0, "keygen should succeed; stderr: {stderr}");
    assert!(priv_path.exists());
    assert!(pub_path.exists());
    // The default-derived path must NOT have been created.
    assert!(
        !dir.join("k.pub").exists(),
        "default pub path should not be created when --pub is given"
    );

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn keygen_sign_verify_roundtrip_succeeds() {
    // keygen -> sign -> verify must round-trip with exit 0.
    let dir = temp_dir("roundtrip");
    let artifact = sample_artifact("roundtrip", b"pengyos reproducible artifact\n");
    let priv_path = dir.join("signing.key");
    let pub_path = dir.join("signing.pub");
    let sig_path = dir.join("artifact.bin.sig");

    let (kg_code, _kg_out, kg_err) = run(&[
        "keygen",
        "--out",
        priv_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_eq!(kg_code, 0, "keygen should succeed; {kg_err}");
    assert!(priv_path.exists() && pub_path.exists());

    let (sign_code, _sign_out, sign_err) = run(&[
        "sign",
        artifact.to_str().unwrap(),
        "--key",
        priv_path.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
    ]);
    assert_eq!(sign_code, 0, "sign should succeed; {sign_err}");
    assert!(sig_path.exists(), "signature file should be created");

    let (verify_code, _verify_out, verify_err) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_eq!(verify_code, 0, "verify should succeed; {verify_err}");

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn sign_defaults_sig_path_to_artifact_dot_sig() {
    // With no --sig, the signature is written next to the artifact as
    // `<artifact>.sig`.
    let dir = temp_dir("default-sig");
    let artifact = sample_artifact("default-sig", b"payload\n");
    let priv_path = dir.join("k.key");
    let pub_path = dir.join("k.pub");
    run(&[
        "keygen",
        "--out",
        priv_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);

    let (sign_code, _sign_out, sign_err) = run(&[
        "sign",
        artifact.to_str().unwrap(),
        "--key",
        priv_path.to_str().unwrap(),
    ]);
    assert_eq!(sign_code, 0, "sign should succeed; {sign_err}");
    let default_sig = dir.join("artifact.bin.sig");
    assert!(default_sig.exists(), "default <artifact>.sig should be created");

    let (verify_code, _v_out, v_err) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        default_sig.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_eq!(verify_code, 0, "verify should succeed; {v_err}");

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn verify_tampered_artifact_fails() {
    let dir = temp_dir("tamper");
    let artifact = sample_artifact("tamper", b"pengyos reproducible artifact\n");
    let priv_path = dir.join("signing.key");
    let pub_path = dir.join("signing.pub");
    let sig_path = dir.join("artifact.bin.sig");

    run(&[
        "keygen",
        "--out",
        priv_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    let (sign_code, _sign_out, sign_err) = run(&[
        "sign",
        artifact.to_str().unwrap(),
        "--key",
        priv_path.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
    ]);
    assert_eq!(sign_code, 0, "sign should succeed; {sign_err}");

    // Tamper the artifact in place, then verify: it must fail.
    let mut contents = std::fs::read(&artifact).unwrap();
    let last = contents.len() - 1;
    contents[last] ^= 0xff;
    std::fs::write(&artifact, &contents).unwrap();

    let (verify_code, _verify_out, verify_err) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_ne!(verify_code, 0, "verifying a tampered artifact must fail");
    let combined = format!("{verify_err}");
    assert!(
        combined.to_lowercase().contains("digest") || combined.to_lowercase().contains("signatur"),
        "failure should mention digest/signature; stderr was: {verify_err}"
    );

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn verify_with_wrong_key_fails() {
    let dir = temp_dir("wrongkey");
    let artifact = sample_artifact("wrongkey", b"pengyos reproducible artifact\n");
    let signer_key = dir.join("signer.key");
    let signer_pub = dir.join("signer.pub");
    let attacker_pub = dir.join("attacker.pub");
    let sig_path = dir.join("artifact.bin.sig");

    // Sign with the real key...
    run(&[
        "keygen",
        "--out",
        signer_key.to_str().unwrap(),
        "--pub",
        signer_pub.to_str().unwrap(),
    ]);
    let (sign_code, _sign_out, sign_err) = run(&[
        "sign",
        artifact.to_str().unwrap(),
        "--key",
        signer_key.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
    ]);
    assert_eq!(sign_code, 0, "sign should succeed; {sign_err}");

    // ...but verify against a *different* key pair's public key. Generate a
    // second, unrelated key pair to act as the wrong verifier.
    run(&[
        "keygen",
        "--out",
        dir.join("attacker.key").to_str().unwrap(),
        "--pub",
        attacker_pub.to_str().unwrap(),
    ]);

    let (verify_code, _verify_out, verify_err) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
        "--pub",
        attacker_pub.to_str().unwrap(),
    ]);
    assert_ne!(verify_code, 0, "verifying with the wrong key must fail");
    assert!(
        verify_err.to_lowercase().contains("signature"),
        "failure should mention signature; stderr was: {verify_err}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn sign_missing_key_is_usage_error() {
    let dir = temp_dir("sign-usage");
    let artifact = sample_artifact("sign-usage", b"payload\n");
    let (code, _out, err) = run(&["sign", artifact.to_str().unwrap()]);
    assert_eq!(code, 2, "sign without --key is a usage error");
    assert!(err.contains("key"), "stderr was: {err}");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn verify_missing_args_are_usage_errors() {
    let dir = temp_dir("verify-usage");
    let artifact = sample_artifact("verify-usage", b"payload\n");
    // Missing --pub.
    let sig = dir.join("a.sig");
    std::fs::write(&sig, b"").unwrap();
    let (code, _out, err) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig.to_str().unwrap(),
    ]);
    assert_eq!(code, 2, "verify without --pub is a usage error");
    assert!(err.contains("pub"), "stderr was: {err}");
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------------------------------------------------------------------------
// Exit-code contract: runtime errors (1) are distinct from usage errors (2).
//
// The CLI's exit-code contract (see src/main.rs) is:
//   0 = success (including --help / --version)
//   1 = generic runtime error (I/O, crypto)
//   2 = usage error (bad/missing arguments)
//   3 = subcommand not yet implemented
//
// These tests lock in that a *runtime* failure (a well-formed invocation that
// fails at I/O or crypto time) exits 1 — not 0, and not 2 — so callers can tell
// "ran and failed" apart from "bad arguments".
// ---------------------------------------------------------------------------

#[test]
fn sign_runtime_error_missing_key_is_exit_1() {
    // A well-formed `sign` invocation (valid positional + --key flag) that fails
    // because the key file does not exist must exit 1, the generic runtime code.
    let dir = temp_dir("sign-runtime");
    let artifact = sample_artifact("sign-runtime", b"payload\n");
    let missing_key = dir.join("does-not-exist.key");
    let (code, _out, err) = run(&[
        "sign",
        artifact.to_str().unwrap(),
        "--key",
        missing_key.to_str().unwrap(),
    ]);
    assert_eq!(code, 1, "a missing key is a runtime error (exit 1); stderr: {err}");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn sign_runtime_error_missing_artifact_is_exit_1() {
    // A well-formed `sign` invocation that fails because the artifact does not
    // exist must exit 1 (I/O error), not 2 (usage).
    let dir = temp_dir("sign-artifact-missing");
    let priv_path = dir.join("signing.key");
    let pub_path = dir.join("signing.pub");
    run(&[
        "keygen",
        "--out",
        priv_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);

    let missing_artifact = dir.join("nope.bin");
    let (code, _out, err) = run(&[
        "sign",
        missing_artifact.to_str().unwrap(),
        "--key",
        priv_path.to_str().unwrap(),
    ]);
    assert_eq!(code, 1, "a missing artifact is a runtime error (exit 1); stderr: {err}");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn verify_runtime_error_missing_pub_is_exit_1() {
    // A well-formed `verify` invocation that fails because the public key does
    // not exist must exit 1 (I/O error), not 2 (usage).
    let dir = temp_dir("verify-runtime");
    let artifact = sample_artifact("verify-runtime", b"payload\n");
    let missing_pub = dir.join("does-not-exist.pub");
    let sig = dir.join("a.sig");
    std::fs::write(&sig, b"").unwrap();
    let (code, _out, err) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig.to_str().unwrap(),
        "--pub",
        missing_pub.to_str().unwrap(),
    ]);
    assert_eq!(code, 1, "a missing public key is a runtime error (exit 1); stderr: {err}");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn verify_runtime_error_malformed_signature_is_exit_1() {
    // A well-formed `verify` invocation that fails because the signature file is
    // malformed (bad magic) must exit 1 (runtime), not 2 (usage).
    let dir = temp_dir("verify-malformed-sig");
    let artifact = sample_artifact("verify-malformed-sig", b"payload\n");
    let pub_path = dir.join("signing.pub");
    let sig_path = dir.join("bad.sig");
    std::fs::write(&sig_path, b"wrong-magic 1 ed25519 00 sha256 00\n").unwrap();
    run(&[
        "keygen",
        "--out",
        dir.join("signing.key").to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);

    let (code, _out, err) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_eq!(
        code, 1,
        "a malformed signature is a runtime error (exit 1); stderr: {err}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn runtime_and_usage_exit_codes_are_distinct() {
    // The distinguishing property of the exit-code contract: the same kind of
    // invocation that is a usage error under one argument shape is a runtime
    // error under another, and the two must not collide. A missing --key is a
    // usage error (2); a present-but-missing key file is a runtime error (1).
    let dir = temp_dir("exit-distinct");
    let artifact = sample_artifact("exit-distinct", b"payload\n");

    let (usage_code, _u_out, u_err) = run(&["sign", artifact.to_str().unwrap()]);
    assert_eq!(
        usage_code, 2,
        "sign without --key is a usage error (exit 2); stderr: {u_err}"
    );

    let (runtime_code, _r_out, r_err) =
        run(&["sign", artifact.to_str().unwrap(), "--key", dir.join("missing.key").to_str().unwrap()]);
    assert_eq!(
        runtime_code, 1,
        "sign with a missing key file is a runtime error (exit 1); stderr: {r_err}"
    );

    assert_ne!(usage_code, runtime_code, "usage and runtime exit codes must differ");
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------------------------------------------------------------------------
// Errors never print key material.
//
// Beyond the keygen leak check, every error path reachable from the CLI must
// never surface a seed, public key, or raw key-file line. These tests drive
// sign/verify through both success and error paths and assert that the
// recognizable key material never appears in stdout or stderr.
// ---------------------------------------------------------------------------

#[test]
fn sign_and_verify_success_never_leaks_seed() {
    // keygen -> sign -> verify: the seed must never appear in any command's
    // stdout/stderr, on the happy path.
    let dir = temp_dir("no-leak-success");
    let artifact = sample_artifact("no-leak-success", b"pengyos reproducible artifact\n");
    let priv_path = dir.join("signing.key");
    let pub_path = dir.join("signing.pub");
    let sig_path = dir.join("artifact.bin.sig");

    let (kg_code, _kg_out, kg_err) = run(&[
        "keygen",
        "--out",
        priv_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_eq!(kg_code, 0, "keygen should succeed; {kg_err}");

    let priv_contents = std::fs::read_to_string(&priv_path).expect("read private key");
    let seed_hex = priv_contents
        .split_whitespace()
        .last()
        .expect("private key file has a seed field");

    let (sign_code, sign_out, sign_err) = run(&[
        "sign",
        artifact.to_str().unwrap(),
        "--key",
        priv_path.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
    ]);
    assert_eq!(sign_code, 0, "sign should succeed; {sign_err}");
    assert!(
        !sign_out.contains(seed_hex) && !sign_err.contains(seed_hex),
        "seed leaked on sign; out={sign_out} err={sign_err}"
    );

    let (verify_code, verify_out, verify_err) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig_path.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_eq!(verify_code, 0, "verify should succeed; {verify_err}");
    assert!(
        !verify_out.contains(seed_hex) && !verify_err.contains(seed_hex),
        "seed leaked on verify; out={verify_out} err={verify_err}"
    );

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn sign_error_path_never_leaks_key_material() {
    // A private key with a recognizable all-0x42 seed. When `sign` fails after
    // loading that key (the artifact is a directory, so hashing fails), the
    // recognizable seed must NOT be echoed to stderr.
    let dir = temp_dir("no-leak-sign-error");
    let marker = "4242424242424242424242424242424242424242424242424242424242424242";
    let priv_path = dir.join("marker.key");
    std::fs::write(
        &priv_path,
        format!("pengyos-key-v1 ed25519 {marker} {marker}\n"),
    )
    .expect("write marker key");

    // Create a directory to use as the "artifact" so hashing fails after the
    // key is loaded.
    let bad_artifact = dir.join("a_directory");
    std::fs::create_dir_all(&bad_artifact).expect("create marker dir");

    let (_code, out, err) = run(&[
        "sign",
        bad_artifact.to_str().unwrap(),
        "--key",
        priv_path.to_str().unwrap(),
    ]);
    let combined = format!("{out}{err}");
    assert!(
        !combined.contains(marker),
        "recognizable seed leaked on sign error; output was: {combined}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn verify_error_path_never_leaks_public_key() {
    // A public key with a recognizable all-0xAB payload. When `verify` fails
    // (bad signature over that key), the recognizable public-key material must
    // NOT be echoed to stderr.
    let dir = temp_dir("no-leak-verify-error");
    let marker = "abababababababababababababababababababababababababababababababab";
    let pub_path = dir.join("marker.pub");
    std::fs::write(&pub_path, format!("pengyos-key-v1 ed25519 {marker} {marker}\n"))
        .expect("write marker pubkey");

    let artifact = sample_artifact("no-leak-verify-error", b"payload\n");
    let sig = dir.join("bad.sig");
    std::fs::write(&sig, b"wrong-magic 1 ed25519 00 sha256 00\n").expect("write bad sig");

    let (code, out, err) = run(&[
        "verify",
        artifact.to_str().unwrap(),
        "--sig",
        sig.to_str().unwrap(),
        "--pub",
        pub_path.to_str().unwrap(),
    ]);
    assert_ne!(code, 0, "verify against a malformed signature must fail");
    let combined = format!("{out}{err}");
    assert!(
        !combined.contains(marker),
        "recognizable public key leaked on verify error; output was: {combined}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}
