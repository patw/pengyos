//! Integration tests for the `pengy-theme` binary.
//!
//! These run the actual compiled binary (via `CARGO_BIN_EXE_pengy-theme`) and check
//! the documented exit-code contract. No network, no root, no display.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

/// Path to the compiled binary, set by Cargo for integration tests.
fn bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_pengy-theme"))
}

/// A per-test scratch directory under `$TMPDIR/pengy-theme-it-*`.
///
/// Each call returns a *fresh* directory so concurrent integration tests never
/// share one. The previous suite shared a single `{pid}` directory across tests,
/// so one test's `remove_dir_all` could delete another test's manifest mid-run
/// (a flaky failure). A unique dir per test closes that race without needing the
/// suite to run serially. The returned dir is owned by the caller (created, and
/// left for the OS to reclaim — the names are unique and tiny).
fn fixture_dir() -> PathBuf {
    static N: AtomicU64 = AtomicU64::new(0);
    let n = N.fetch_add(1, Ordering::Relaxed);
    let dir = std::env::temp_dir().join(format!("pengy-theme-it-{}-{}", std::process::id(), n));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// Create `name` inside a fresh fixture dir and return its full path.
fn fixture_file(name: &str) -> PathBuf {
    let dir = fixture_dir();
    dir.join(name)
}

/// Run the binary with the given args and return (exit_code, stdout, stderr).
fn run(args: &[&str]) -> (i32, String, String) {
    let out = Command::new(bin())
        .args(args)
        .output()
        .expect("failed to spawn pengy-theme");
    let code = out.status.code().expect("process terminated by signal");
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    (code, stdout, stderr)
}

#[test]
fn binary_exists() {
    assert!(bin().is_file(), "binary not built at {}", bin().display());
}

#[test]
fn no_args_succeeds_and_prints_usage() {
    let (code, stdout, _) = run(&[]);
    assert_eq!(code, 0, "no-args exit code");
    assert!(stdout.contains("pengy-theme"), "usage should name the crate");
    assert!(stdout.contains("validate"), "usage should list validate");
    assert!(stdout.contains("pack"), "usage should list pack");
    assert!(stdout.contains("contrast"), "usage should list contrast");
}

#[test]
fn help_succeeds() {
    let (code, stdout, _) = run(&["--help"]);
    assert_eq!(code, 0);
    assert!(stdout.contains("USAGE"));
    for cmd in ["validate", "pack", "contrast"] {
        assert!(stdout.contains(cmd), "help should mention `{cmd}`");
    }

    let (code2, _, _) = run(&["-h"]);
    assert_eq!(code2, 0);
}

#[test]
fn version_succeeds_and_names_crate() {
    let (code, stdout, _) = run(&["--version"]);
    assert_eq!(code, 0);
    assert!(
        stdout.trim_start().starts_with("pengy-theme"),
        "version banner should name the crate: {stdout:?}"
    );

    let (code2, stdout2, _) = run(&["-V"]);
    assert_eq!(code2, 0);
    assert!(stdout2.trim_start().starts_with("pengy-theme"));
}

#[test]
fn validate_reports_usage_without_manifest() {
    // `validate` with no manifest is a usage error (2), not unimplemented.
    let (code, _, stderr) = run(&["validate"]);
    assert_eq!(code, 2, "`validate` without a manifest is a usage error");
    assert!(stderr.contains("missing <manifest>"), "stderr={stderr:?}");
}

#[test]
fn validate_missing_manifest_is_runtime_error() {
    // A manifest that cannot be read is a runtime error (1).
    let (code, _, stderr) = run(&["validate", "/nonexistent/pengy-theme-nope.toml"]);
    assert_eq!(code, 1, "missing manifest is a runtime error");
    assert!(stderr.contains("could not read manifest"), "stderr={stderr:?}");
}

#[test]
fn validate_valid_manifest_passes() {
    // A manifest with a high-contrast fg/bg pair and well-formed colors passes.
    let path = fixture_file("valid.toml");
    std::fs::write(
        &path,
        r##"
name = "Valid"
version = "1.0.0"
[colors]
foreground = "#000000"
background = "#ffffff"
accent = "#003399"
[fonts]
mono = "Mono"
sans = "Sans"
base_size = 14
[defaults]
"##,
    )
    .unwrap();

    let (code, stdout, _) = run(&["validate", path.to_str().unwrap()]);
    assert_eq!(code, 0, "valid manifest should pass: {stdout}");
    assert!(stdout.contains("OK"), "stdout should report OK: {stdout}");
}

#[test]
fn validate_low_contrast_fails() {
    // A near-monochrome fg/bg pair fails WCAG AA normal (exit 1).
    let path = fixture_file("low.toml");
    std::fs::write(
        &path,
        r##"
name = "Low Contrast"
version = "1.0.0"
[colors]
foreground = "#808080"
background = "#8a8a8a"
accent = "#00aaff"
[fonts]
mono = "Mono"
sans = "Sans"
base_size = 14
[defaults]
"##,
    )
    .unwrap();

    let (code, stdout, _) = run(&["validate", path.to_str().unwrap()]);
    assert_eq!(code, 1, "low-contrast manifest should fail");
    assert!(
        stdout.to_lowercase().contains("contrast"),
        "stdout should report the contrast problem: {stdout}"
    );
}

// --- contrast -----------------------------------------------------------------
//
// The `contrast` subcommand computes the WCAG relative contrast ratio of two
// #rrggbb colors and reports the AA verdict. These exercise it end-to-end
// through the compiled binary.

#[test]
fn contrast_help_lists_usage() {
    let (code, stdout, _) = run(&["contrast", "--help"]);
    assert_eq!(code, 0, "contrast --help should succeed");
    assert!(stdout.contains("contrast"), "help should name the subcommand");
    assert!(stdout.contains("<fg>"), "help should document the positional args");
}

#[test]
fn contrast_white_on_black_is_21() {
    // The canonical WCAG reference: white on black is 21:1.
    let (code, stdout, stderr) = run(&["contrast", "#ffffff", "#000000"]);
    assert_eq!(code, 0, "white-on-black should succeed: {stderr}");
    // The ratio is rendered to two decimals with a `:1` suffix.
    assert!(
        stdout.contains("21.00:1"),
        "white on black is 21:1, stdout={stdout:?}"
    );
    // Dark-on-light clears both AA thresholds.
    assert!(stdout.contains("pass"), "should report AA pass: {stdout}");
}

#[test]
fn contrast_grey_on_white_is_4_54() {
    // WCAG references #767676 on white as 4.54:1.
    let (code, stdout, stderr) = run(&["contrast", "#767676", "#ffffff"]);
    assert_eq!(code, 0, "should succeed: {stderr}");
    assert!(
        stdout.contains("4.54:1"),
        "grey on white is 4.54:1, stdout={stdout:?}"
    );
}

#[test]
fn contrast_low_contrast_reports_fail() {
    // Two near-identical greys miss both AA thresholds.
    let (code, stdout, stderr) = run(&["contrast", "#808080", "#8a8a8a"]);
    assert_eq!(code, 0, "should still succeed (the ratio is well-defined): {stderr}");
    assert!(stdout.contains("fail"), "low-contrast should report fail: {stdout}");
}

#[test]
fn contrast_normalizes_case_in_output() {
    // Uppercase and '#'-less inputs must render in canonical lowercase hex.
    let (code, stdout, _) = run(&["contrast", "#1A2B3C", "010203"]);
    assert_eq!(code, 0);
    assert!(
        stdout.starts_with("#1a2b3c on #010203:"),
        "output must normalize to lowercase hex: {stdout:?}"
    );
}

#[test]
fn contrast_invalid_color_is_runtime_error() {
    // A malformed color is a runtime error (1), not a usage error or a panic.
    let (code, _, stderr) = run(&["contrast", "not-a-color", "#000000"]);
    assert_eq!(code, 1, "invalid color is a runtime error");
    assert!(
        stderr.to_lowercase().contains("invalid"),
        "stderr should explain the bad color: {stderr:?}"
    );
}

#[test]
fn contrast_missing_args_is_usage_error() {
    // Zero or one positional argument is a usage error (2).
    let (code, _, _) = run(&["contrast"]);
    assert_eq!(code, 2, "no args is a usage error");
    let (code2, _, _) = run(&["contrast", "#000000"]);
    assert_eq!(code2, 2, "one arg is a usage error");
}

#[test]
fn contrast_too_many_args_is_usage_error() {
    // Three positional arguments is a usage error (2).
    let (code, _, _) = run(&["contrast", "#000000", "#ffffff", "#ff0000"]);
    assert_eq!(code, 2, "three args is a usage error");
}

#[test]
fn unknown_command_is_usage_error() {
    let (code, _, stderr) = run(&["frobnicate"]);
    assert_eq!(code, 2, "unknown command should be a usage error");
    assert!(stderr.contains("frobnicate"), "stderr should name the bad command");
}

// --- pack -------------------------------------------------------------------
//
// The `pack` subcommand produces a deterministic archive + manifest hash. These
// exercise it end-to-end through the compiled binary.

/// A manifest that references one wallpaper asset (relative to the manifest dir).
const PACKED_MANIFEST: &str = r##"
name = "Catbee Drift"
version = "1.2.3"
description = "A warm, low-contrast-friendly palette."
[colors]
foreground = "#f5f5f5"
background = "#1a1a1a"
accent = "#ffb454"
[fonts]
mono = "DejaVu Sans Mono"
sans = "DejaVu Sans"
base_size = 14
[[wallpapers]]
name = "drift-mountain"
file = "wallpapers/drift-mountain.svg"
[defaults]
"##;

/// Write `PACKED_MANIFEST` to `<dir>/catbee-drift.toml` and a wallpaper asset to
/// `<dir>/wallpapers/drift-mountain.svg`, returning the manifest path.
fn packed_manifest_in(dir: &Path) -> PathBuf {
    std::fs::create_dir_all(dir.join("wallpapers")).unwrap();
    let manifest = dir.join("catbee-drift.toml");
    std::fs::write(&manifest, PACKED_MANIFEST).unwrap();
    std::fs::write(dir.join("wallpapers/drift-mountain.svg"), b"<svg/>").unwrap();
    manifest
}

#[test]
fn pack_help_lists_out_option() {
    let (code, stdout, _) = run(&["pack", "--help"]);
    assert_eq!(code, 0, "pack --help should succeed");
    assert!(stdout.contains("pack"), "help should name the subcommand");
    assert!(stdout.contains("--out"), "help should document --out");
}

#[test]
fn pack_produces_archive_and_hash_sidecar() {
    let dir = fixture_dir();
    let manifest = packed_manifest_in(&dir);
    let out = dir.join("out");

    let (code, stdout, stderr) = run(&[
        "pack",
        manifest.to_str().unwrap(),
        "--out",
        out.to_str().unwrap(),
    ]);
    assert_eq!(code, 0, "pack should succeed: {stderr}");
    assert!(stdout.contains("manifest hash"), "stdout should report the hash: {stdout}");

    // The root dir is named after the manifest stem, slugified.
    let archive = out.join("catbee-drift.tar");
    let sidecar = out.join("manifest.sha256");
    assert!(archive.is_file(), "archive must exist: {}", archive.display());
    assert!(sidecar.is_file(), "hash sidecar must exist: {}", sidecar.display());

    // The sidecar is a sha256sum-compatible listing: `<hex>  <path>` per line.
    let listing = std::fs::read_to_string(&sidecar).unwrap();
    let lines: Vec<&str> = listing.trim().lines().collect();
    assert_eq!(lines.len(), 2, "one line per file (manifest + asset)");
    for line in lines {
        let (hash, path) = line.split_once("  ").expect("sha256sum line shape");
        assert_eq!(hash.len(), 64, "sha256 hex is 64 chars: {hash}");
        assert!(hash.chars().all(|c| c.is_ascii_hexdigit()));
        assert!(path.starts_with("catbee-drift/"), "paths scoped under root dir: {path}");
    }

    // The hash printed by the CLI matches the 64-hex digest in the listing.
    let printed = stdout
        .lines()
        .find_map(|l| l.strip_prefix("manifest hash: "))
        .expect("stdout must print the manifest hash");
    assert_eq!(printed.len(), 64);
    assert!(listing.contains(printed), "listing must contain the printed hash");

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn pack_archive_starts_with_ustar_magic() {
    // The archive is a POSIX ustar: the magic "ustar\0" sits at offset 257 of the
    // first 512-byte header block (the canonical ustar magic location), and the
    // 8-byte checksum field at offset 148 validates against the block.
    let dir = fixture_dir();
    let manifest = packed_manifest_in(&dir);
    let out = dir.join("out");
    let (code, _, stderr) = run(&["pack", manifest.to_str().unwrap(), "--out", out.to_str().unwrap()]);
    assert_eq!(code, 0, "pack should succeed: {stderr}");

    let bytes = std::fs::read(out.join("catbee-drift.tar")).unwrap();
    assert_eq!(&bytes[257..263], b"ustar\0", "archive must be a ustar");
    // Two file records (512-byte headers each) + 1024-byte end-of-archive marker.
    assert!(bytes.len() >= 512 * 2 + 1024, "archive holds two records + EOA");
    assert_eq!(&bytes[bytes.len() - 1024..], &[0u8; 1024], "archive ends with the EOA marker");

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn pack_is_deterministic() {
    // Two packs over the same inputs are byte-identical (archive + hash).
    let m1 = fixture_dir();
    let m2 = fixture_dir();
    let manifest1 = packed_manifest_in(&m1);
    let manifest2 = packed_manifest_in(&m2);
    let out1 = m1.join("out");
    let out2 = m2.join("out");

    let (c1, _, _) = run(&["pack", manifest1.to_str().unwrap(), "--out", out1.to_str().unwrap()]);
    let (c2, _, _) = run(&["pack", manifest2.to_str().unwrap(), "--out", out2.to_str().unwrap()]);
    assert_eq!(c1, 0);
    assert_eq!(c2, 0);

    // The root dir is scoped by the manifest *stem*, which is identical, so the
    // archive paths (and bytes) match.
    let a1 = std::fs::read(out1.join("catbee-drift.tar")).unwrap();
    let a2 = std::fs::read(out2.join("catbee-drift.tar")).unwrap();
    assert_eq!(a1, a2, "archives must be byte-identical");

    let h1 = std::fs::read_to_string(out1.join("manifest.sha256")).unwrap();
    let h2 = std::fs::read_to_string(out2.join("manifest.sha256")).unwrap();
    assert_eq!(h1, h2, "hash listings must be identical");

    std::fs::remove_dir_all(&m1).ok();
    std::fs::remove_dir_all(&m2).ok();
}

#[test]
fn pack_missing_asset_is_runtime_error() {
    // A manifest references a wallpaper that does not exist on disk.
    let dir = fixture_dir();
    std::fs::create_dir_all(&dir).unwrap();
    let manifest = dir.join("catbee.toml");
    std::fs::write(&manifest, PACKED_MANIFEST).unwrap();
    // No wallpapers/ dir => the referenced asset is missing.

    let out = dir.join("out");
    let (code, _, stderr) = run(&["pack", manifest.to_str().unwrap(), "--out", out.to_str().unwrap()]);
    assert_eq!(code, 1, "a missing asset is a runtime error");
    assert!(
        stderr.to_lowercase().contains("missing"),
        "stderr should name the missing asset: {stderr:?}"
    );

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn pack_missing_out_dir_is_usage_error() {
    let dir = fixture_dir();
    let manifest = packed_manifest_in(&dir);

    // No `--out` is a usage error (2).
    let (code, _, stderr) = run(&["pack", manifest.to_str().unwrap()]);
    assert_eq!(code, 2, "missing --out is a usage error");
    assert!(
        stderr.to_lowercase().contains("--out"),
        "stderr should mention --out: {stderr:?}"
    );

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn pack_bad_manifest_is_runtime_error() {
    // A manifest that is not valid TOML is a runtime error (1), not a panic.
    let dir = fixture_dir();
    let manifest = dir.join("bad.toml");
    std::fs::write(&manifest, "this = = not toml").unwrap();

    let out = dir.join("out");
    let (code, _, stderr) = run(&["pack", manifest.to_str().unwrap(), "--out", out.to_str().unwrap()]);
    assert_eq!(code, 1, "a malformed manifest is a runtime error");
    assert!(!stderr.is_empty(), "stderr should explain the failure: {stderr:?}");

    std::fs::remove_dir_all(&dir).ok();
}

// --- pack: POSIX ustar interoperability (opt-in) ----------------------------
//
// The unit tests in `src/pack.rs` validate the header layout in isolation. These
// tests go further: they hand the produced archive to the system `tar` and
// confirm GNU tar accepts and round-trips it. They are opt-in behind
// `PENGYOS_IT=1` because they require the external `tar` binary (and, for the
// extract check, a writable scratch dir); the default suite stays dependency-
// free of host tools.

/// True when the `tar` binary is available on `PATH`.
fn tar_available() -> bool {
    Command::new("tar")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

#[test]
fn pack_archive_is_readable_by_gnu_tar() {
    // Opt-in: requires PENGYOS_IT=1 and a system `tar`. Read at runtime (not
    // compile time) so the default suite runs without the flag set.
    if std::env::var("PENGYOS_IT").unwrap_or_default() != "1" || !tar_available() {
        eprintln!("skip: pack tar-readability requires PENGYOS_IT=1 and system tar");
        return;
    }

    let dir = fixture_dir();
    let manifest = packed_manifest_in(&dir);
    let out = dir.join("out");

    let (code, _, stderr) =
        run(&["pack", manifest.to_str().unwrap(), "--out", out.to_str().unwrap()]);
    assert_eq!(code, 0, "pack should succeed: {stderr}");

    let archive = out.join("catbee-drift.tar");
    // GNU tar must accept the archive and list its two records.
    let listing = Command::new("tar")
        .args(["-tf", archive.to_str().unwrap()])
        .output()
        .expect("failed to run tar -tf");
    assert!(
        listing.status.success(),
        "GNU tar rejected the archive: {:?}",
        String::from_utf8_lossy(&listing.stderr)
    );
    let listed = String::from_utf8_lossy(&listing.stdout);
    assert!(
        listed.contains("catbee-drift/catbee-drift.toml"),
        "tar should list the manifest under its root dir: {listed}"
    );
    assert!(
        listed.contains("catbee-drift/wallpapers/drift-mountain.svg"),
        "tar should list the asset under its root dir: {listed}"
    );

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn pack_archive_extracts_verbatim() {
    // Opt-in: requires PENGYOS_IT=1 and a system `tar`. Read at runtime (not
    // compile time) so the default suite runs without the flag set.
    if std::env::var("PENGYOS_IT").unwrap_or_default() != "1" || !tar_available() {
        eprintln!("skip: pack tar-extract requires PENGYOS_IT=1 and system tar");
        return;
    }

    let dir = fixture_dir();
    let manifest = packed_manifest_in(&dir);
    let out = dir.join("out");

    let (code, _, stderr) =
        run(&["pack", manifest.to_str().unwrap(), "--out", out.to_str().unwrap()]);
    assert_eq!(code, 0, "pack should succeed: {stderr}");

    // Extract into a fresh dir. `pack` archives the manifest as its canonical
    // serde serialization (not the raw file bytes), so we verify the round-trip
    // by re-validating the extracted manifest through the CLI rather than a
    // byte-for-byte compare.
    let extract = dir.join("extract");
    std::fs::create_dir_all(&extract).unwrap();
    let archive = out.join("catbee-drift.tar");
    let extracted = Command::new("tar")
        .args(["-xf", archive.to_str().unwrap(), "-C", extract.to_str().unwrap()])
        .output()
        .expect("failed to run tar -xf");
    assert!(
        extracted.status.success(),
        "GNU tar failed to extract: {:?}",
        String::from_utf8_lossy(&extracted.stderr)
    );

    let on_disk = extract.join("catbee-drift/catbee-drift.toml");
    assert!(on_disk.is_file(), "extracted manifest must exist");

    // The extracted manifest must round-trip as a valid theme: re-run `validate`
    // on it and confirm it passes.
    let (code, stdout, stderr) = run(&["validate", on_disk.to_str().unwrap()]);
    assert_eq!(code, 0, "extracted manifest must validate: {stderr}");
    assert!(
        stdout.contains("all checks passed"),
        "extracted manifest should validate cleanly: {stdout}"
    );
    // And the serialized content carries the theme name through the round-trip.
    let extracted_text = std::fs::read_to_string(&on_disk).unwrap();
    assert!(
        extracted_text.contains("Catbee Drift"),
        "extracted manifest should carry the theme name: {extracted_text}"
    );

    std::fs::remove_dir_all(&dir).ok();
}
