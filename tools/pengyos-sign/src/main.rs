//! `pengyos-sign` CLI entrypoint.
//!
//! Thin wrapper around [`pengyos_sign`]: it parses arguments, dispatches to the
//! matching subcommand, and maps outcomes to exit codes. Subcommand
//! implementations live in the library.
//!
//! Exit-code contract (shared by every subcommand):
//! - `0` success (including `--help` / `--version`)
//! - `1` generic runtime error
//! - `2` usage error (bad or missing arguments)
//! - `3` subcommand not yet implemented
//!
//! Error messages never contain key material.

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use pengyos_sign::{
    banner, default_sig_path, sign_keyfile, verify_keyfile, KeyPair, NAME, USAGE,
};

/// Exit code for a clean, successful run (help/version).
const OK: u8 = 0;
/// Exit code for a generic runtime error.
const ERR: u8 = 1;
/// Exit code for a usage error (bad or missing arguments).
const USAGE_ERR: u8 = 2;
/// Exit code for a subcommand that has not been implemented yet.
const NOT_IMPLEMENTED: u8 = 3;

/// A failure that should be reported to the user, with the right exit code.
#[derive(Debug)]
enum RunError {
    /// Bad/missing arguments -> exit code [`USAGE_ERR`].
    Usage(String),
    /// A runtime error (I/O, crypto) -> exit code [`ERR`].
    Runtime(String),
}

fn main() -> ExitCode {
    // Drop the program name; keep the remaining args.
    let args: Vec<String> = std::env::args().skip(1).collect();

    match dispatch(&args) {
        Ok(code) => ExitCode::from(code),
        Err(RunError::Usage(msg)) => {
            eprintln!("pengyos-sign: {msg}");
            eprintln!("Run `pengyos-sign --help` for usage.");
            ExitCode::from(USAGE_ERR)
        }
        Err(RunError::Runtime(msg)) => {
            eprintln!("pengyos-sign: {msg}");
            ExitCode::from(ERR)
        }
    }
}

/// Dispatch the parsed argument vector to the matching subcommand.
fn dispatch(args: &[String]) -> Result<u8, RunError> {
    match args.first().map(String::as_str) {
        // No command, or an explicit help/version request -> print, succeed.
        None | Some("-h") | Some("--help") | Some("help") => {
            println!("{USAGE}");
            Ok(OK)
        }
        Some("-V") | Some("--version") | Some("version") => {
            println!("{}", banner());
            Ok(OK)
        }
        Some("keygen") => cmd_keygen(&args[1..]),
        Some("sign") => cmd_sign(&args[1..]),
        Some("verify") => cmd_verify(&args[1..]),
        // Unknown top-level flag (e.g. `--foo`): a usage error, reported before
        // the generic "subcommand not implemented" arm below.
        Some(other) if other.starts_with('-') => Err(RunError::Usage(format!(
            "unknown option `{other}`"
        ))),
        // A subcommand was requested but not yet implemented. Report cleanly
        // (never panic, never leak key material) with a distinct exit code so
        // callers can tell "not implemented" apart from "ran and failed".
        Some(cmd) => {
            eprintln!("pengyos-sign: subcommand `{cmd}` is not implemented yet");
            eprintln!("Run `pengyos-sign --help` for usage.");
            Ok(NOT_IMPLEMENTED)
        }
    }
}

/// Options parsed from the `keygen` subcommand arguments.
#[derive(Debug)]
struct KeygenOptions {
    /// Path to write the private key (required).
    out: PathBuf,
    /// Optional explicit public-key path; `None` means derive from `out`.
    public: Option<PathBuf>,
}

/// Parse `keygen` arguments (`--out PATH [--pub PATH]`).
fn parse_keygen_args(args: &[String]) -> Result<KeygenOptions, RunError> {
    let mut out: Option<PathBuf> = None;
    let mut pub_opt: Option<PathBuf> = None;

    let mut i = 0;
    while i < args.len() {
        let arg = &args[i];
        match arg.as_str() {
            "-h" | "--help" => {
                return Err(RunError::Usage(
                    "keygen: --help is not available here; see `pengyos-sign --help`".to_string(),
                ));
            }
            "--out" => {
                i += 1;
                let val = take_value(args, i, "--out")?;
                out = Some(PathBuf::from(&val));
            }
            "--pub" => {
                i += 1;
                let val = take_value(args, i, "--pub")?;
                pub_opt = Some(PathBuf::from(&val));
            }
            other if other.starts_with("--out=") => {
                out = Some(PathBuf::from(&other["--out=".len()..]));
            }
            other if other.starts_with("--pub=") => {
                pub_opt = Some(PathBuf::from(&other["--pub=".len()..]));
            }
            other if other.starts_with('-') => {
                return Err(RunError::Usage(format!(
                    "keygen: unknown option `{other}`"
                )));
            }
            _ => {
                return Err(RunError::Usage(format!(
                    "keygen: unexpected argument `{arg}`"
                )));
            }
        }
        i += 1;
    }

    let out = out.ok_or_else(|| {
        RunError::Usage("keygen: missing required option `--out <path>`".to_string())
    })?;

    Ok(KeygenOptions { out, public: pub_opt })
}

/// Return the value following a `--flag`-style option, or a usage error.
fn take_value(args: &[String], i: usize, flag: &str) -> Result<String, RunError> {
    match args.get(i) {
        Some(val) => Ok(val.clone()),
        None => Err(RunError::Usage(format!(
            "keygen: option `{flag}` requires a value"
        ))),
    }
}

/// Run the `keygen` subcommand.
fn cmd_keygen(args: &[String]) -> Result<u8, RunError> {
    let opts = parse_keygen_args(args)?;

    let kp = KeyPair::generate().map_err(|e| RunError::Runtime(e.to_string()))?;

    kp.write_private(&opts.out).map_err(|e| RunError::Runtime(e.to_string()))?;

    let pub_path = opts.public.unwrap_or_else(|| default_pub_path(&opts.out));
    kp.write_public(&pub_path).map_err(|e| RunError::Runtime(e.to_string()))?;

    // Report success without leaking any key material: only the paths and the
    // (non-secret) key id are printed.
    println!("{NAME}: wrote private key to {}", display(&opts.out));
    println!("{NAME}: wrote public key to {}", display(&pub_path));
    println!("{NAME}: key id {}", kp.keyid());

    Ok(OK)
}

/// Options parsed from the `sign` subcommand arguments.
#[derive(Debug)]
struct SignOptions {
    /// Path to the artifact to sign (required positional).
    file: PathBuf,
    /// Path to the signing (private) key (required).
    key: PathBuf,
    /// Optional explicit signature path; `None` means `<file>.sig`.
    sig: Option<PathBuf>,
}

/// Parse `sign` arguments (`sign <file> --key K [--sig F]`).
fn parse_sign_args(args: &[String]) -> Result<SignOptions, RunError> {
    let mut file: Option<PathBuf> = None;
    let mut key: Option<PathBuf> = None;
    let mut sig: Option<PathBuf> = None;

    let mut i = 0;
    while i < args.len() {
        let arg = &args[i];
        match arg.as_str() {
            "--key" => {
                i += 1;
                key = Some(PathBuf::from(take_value(args, i, "--key")?));
            }
            "--sig" => {
                i += 1;
                sig = Some(PathBuf::from(take_value(args, i, "--sig")?));
            }
            other if other.starts_with("--key=") => {
                key = Some(PathBuf::from(&other["--key=".len()..]));
            }
            other if other.starts_with("--sig=") => {
                sig = Some(PathBuf::from(&other["--sig=".len()..]));
            }
            other if other.starts_with('-') => {
                return Err(RunError::Usage(format!("sign: unknown option `{other}`")));
            }
            // Positional: the artifact to sign. Only one is allowed.
            other => {
                if file.is_some() {
                    return Err(RunError::Usage(
                        "sign: multiple input files given".to_string(),
                    ));
                }
                file = Some(PathBuf::from(other));
            }
        }
        i += 1;
    }

    let file = file
        .ok_or_else(|| RunError::Usage("sign: missing required input file".to_string()))?;
    let key = key
        .ok_or_else(|| RunError::Usage("sign: missing required option `--key <path>`".to_string()))?;

    Ok(SignOptions { file, key, sig })
}

/// Run the `sign` subcommand.
fn cmd_sign(args: &[String]) -> Result<u8, RunError> {
    let opts = parse_sign_args(args)?;

    // Sign from the key file (loads + validates the seed inside the library).
    let sig_bytes = sign_keyfile(&opts.file, &opts.key).map_err(|e| RunError::Runtime(e.to_string()))?;

    let sig_path = opts.sig.clone().unwrap_or_else(|| default_sig_path(&opts.file));
    std::fs::write(&sig_path, &sig_bytes).map_err(|e| RunError::Runtime(e.to_string()))?;

    println!("{NAME}: wrote signature to {}", display(&sig_path));
    Ok(OK)
}

/// Options parsed from the `verify` subcommand arguments.
#[derive(Debug)]
struct VerifyOptions {
    /// Path to the artifact to verify (required positional).
    file: PathBuf,
    /// Path to the signature file (required).
    sig: PathBuf,
    /// Path to the public key file (required).
    public: PathBuf,
}

/// Parse `verify` arguments (`verify <file> --sig F --pub PUB`).
fn parse_verify_args(args: &[String]) -> Result<VerifyOptions, RunError> {
    let mut file: Option<PathBuf> = None;
    let mut sig: Option<PathBuf> = None;
    let mut public: Option<PathBuf> = None;

    let mut i = 0;
    while i < args.len() {
        let arg = &args[i];
        match arg.as_str() {
            "--sig" => {
                i += 1;
                sig = Some(PathBuf::from(take_value(args, i, "--sig")?));
            }
            "--pub" => {
                i += 1;
                public = Some(PathBuf::from(take_value(args, i, "--pub")?));
            }
            other if other.starts_with("--sig=") => {
                sig = Some(PathBuf::from(&other["--sig=".len()..]));
            }
            other if other.starts_with("--pub=") => {
                public = Some(PathBuf::from(&other["--pub=".len()..]));
            }
            other if other.starts_with('-') => {
                return Err(RunError::Usage(format!("verify: unknown option `{other}`")));
            }
            // Positional: the artifact to verify. Only one is allowed.
            other => {
                if file.is_some() {
                    return Err(RunError::Usage(
                        "verify: multiple input files given".to_string(),
                    ));
                }
                file = Some(PathBuf::from(other));
            }
        }
        i += 1;
    }

    let file = file
        .ok_or_else(|| RunError::Usage("verify: missing required input file".to_string()))?;
    let sig = sig
        .ok_or_else(|| RunError::Usage("verify: missing required option `--sig <path>`".to_string()))?;
    let public = public.ok_or_else(|| {
        RunError::Usage("verify: missing required option `--pub <path>`".to_string())
    })?;

    Ok(VerifyOptions {
        file,
        sig,
        public,
    })
}

/// Run the `verify` subcommand.
fn cmd_verify(args: &[String]) -> Result<u8, RunError> {
    let opts = parse_verify_args(args)?;

    // Load the public key and signature file, then verify. Any failure (bad
    // key file, malformed signature, digest mismatch, invalid signature) is a
    // runtime error and never prints key material.
    verify_keyfile(&opts.file, &opts.sig, &opts.public)
        .map_err(|e| RunError::Runtime(e.to_string()))?;

    println!("{NAME}: signature verified for {}", display(&opts.file));
    Ok(OK)
}

/// Derive the default public-key path from the private-key path.
///
/// If `out` ends in `.key`, it is replaced with `.pub`; otherwise `.pub` is
/// appended. e.g. `signing.key` -> `signing.pub`, `signing` -> `signing.pub`.
fn default_pub_path(out: &Path) -> PathBuf {
    let stem = out.as_os_str();
    match stem.to_str().and_then(|s| s.strip_suffix(".key")) {
        Some(base) => PathBuf::from(format!("{base}.pub")),
        None => PathBuf::from(format!("{}.pub", stem.to_string_lossy())),
    }
}

/// Render a path for display, falling back to its string form.
fn display(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_pub_path_replaces_key_extension() {
        let p = default_pub_path(Path::new("signing.key"));
        assert_eq!(p, PathBuf::from("signing.pub"));
    }

    #[test]
    fn default_pub_path_appends_when_no_key_extension() {
        let p = default_pub_path(Path::new("/tmp/keys/signing"));
        assert_eq!(p, PathBuf::from("/tmp/keys/signing.pub"));
    }

    #[test]
    fn default_pub_path_handles_nested_dirs() {
        let p = default_pub_path(Path::new("/a/b/c/signing.key"));
        assert_eq!(p, PathBuf::from("/a/b/c/signing.pub"));
    }

    #[test]
    fn keygen_requires_out() {
        let err = parse_keygen_args(&[]).unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn keygen_parses_out_and_pub() {
        let opts = parse_keygen_args(&["--out".into(), "k.key".into(), "--pub".into(), "k.pub".into()])
            .unwrap();
        assert_eq!(opts.out, PathBuf::from("k.key"));
        assert_eq!(opts.public, Some(PathBuf::from("k.pub")));
    }

    #[test]
    fn keygen_parses_equals_form() {
        let opts = parse_keygen_args(&["--out=k.key".into()]).unwrap();
        assert_eq!(opts.out, PathBuf::from("k.key"));
        assert!(opts.public.is_none());
    }

    #[test]
    fn keygen_unknown_flag_is_usage_error() {
        let err = parse_keygen_args(&["--bogus".into()]).unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn keygen_missing_value_is_usage_error() {
        let err = parse_keygen_args(&["--out".into()]).unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn dispatch_version_succeeds() {
        assert_eq!(dispatch(&["--version".into()]).unwrap(), OK);
    }

    #[test]
    fn dispatch_unknown_flag_is_usage_error() {
        assert!(matches!(
            dispatch(&["--nope".into()]),
            Err(RunError::Usage(_))
        ));
    }

    #[test]
    fn dispatch_unimplemented_reports_not_implemented() {
        // A command that was never implemented must still exit with
        // NOT_IMPLEMENTED (keygen/sign/verify are all implemented now).
        assert_eq!(dispatch(&["frobnicate".into()]).unwrap(), NOT_IMPLEMENTED);
    }

    #[test]
    fn sign_parses_file_key_and_sig() {
        let opts = parse_sign_args(&[
            "artifact".into(),
            "--key".into(),
            "k.key".into(),
            "--sig".into(),
            "a.sig".into(),
        ])
        .unwrap();
        assert_eq!(opts.file, PathBuf::from("artifact"));
        assert_eq!(opts.key, PathBuf::from("k.key"));
        assert_eq!(opts.sig, Some(PathBuf::from("a.sig")));
    }

    #[test]
    fn sign_parses_equals_forms() {
        let opts = parse_sign_args(&["artifact".into(), "--key=k.key".into()]).unwrap();
        assert_eq!(opts.file, PathBuf::from("artifact"));
        assert_eq!(opts.key, PathBuf::from("k.key"));
        assert!(opts.sig.is_none());
    }

    #[test]
    fn sign_defaults_sig_to_none() {
        let opts = parse_sign_args(&["artifact".into(), "--key".into(), "k.key".into()]).unwrap();
        assert_eq!(opts.file, PathBuf::from("artifact"));
        assert_eq!(opts.key, PathBuf::from("k.key"));
        assert!(opts.sig.is_none());
    }

    #[test]
    fn sign_requires_file() {
        let err = parse_sign_args(&["--key".into(), "k.key".into()]).unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn sign_requires_key() {
        let err = parse_sign_args(&["artifact".into()]).unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn sign_unknown_flag_is_usage_error() {
        let err = parse_sign_args(&["artifact".into(), "--bogus".into()]).unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn sign_two_files_is_usage_error() {
        let err = parse_sign_args(&["a".into(), "b".into()]).unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn verify_parses_all() {
        let opts = parse_verify_args(&[
            "artifact".into(),
            "--sig".into(),
            "a.sig".into(),
            "--pub".into(),
            "k.pub".into(),
        ])
        .unwrap();
        assert_eq!(opts.file, PathBuf::from("artifact"));
        assert_eq!(opts.sig, PathBuf::from("a.sig"));
        assert_eq!(opts.public, PathBuf::from("k.pub"));
    }

    #[test]
    fn verify_requires_file() {
        let err = parse_verify_args(&["--sig".into(), "a.sig".into(), "--pub".into(), "k.pub".into()])
            .unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn verify_requires_sig() {
        let err = parse_verify_args(&["artifact".into(), "--pub".into(), "k.pub".into()])
            .unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn verify_requires_pub() {
        let err = parse_verify_args(&["artifact".into(), "--sig".into(), "a.sig".into()])
            .unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }

    #[test]
    fn verify_unknown_flag_is_usage_error() {
        let err = parse_verify_args(&["artifact".into(), "--sig".into(), "a.sig".into(), "--bogus".into()])
            .unwrap_err();
        assert!(matches!(err, RunError::Usage(_)));
    }
}
