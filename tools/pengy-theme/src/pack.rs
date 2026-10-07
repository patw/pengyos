//! Deterministic packaging: archive a theme (manifest + referenced assets) and
//! emit a reproducible manifest hash.
//!
//! [`pack`](pack) turns a parsed [`Theme`](crate::manifest::Theme) — together with
//! the manifest file it came from and the asset files it references — into two
//! outputs in an output directory:
//!
//! - **`<name>.tar`** — a deterministic (POSIX *ustar*) archive of the manifest
//!   and its referenced assets, with fixed metadata (mtime, uid, gid, uname,
//!   gname, mode) and entries in sorted path order, so two runs over the same
//!   inputs produce byte-identical bytes.
//! - **`manifest.sha256`** — the canonical file listing (one `<sha256hex>  <path>`
//!   line per file), whose hash is the SHA-256 over that listing. The listing is
//!   sha256sum-compatible, so the hash is verifiable with `sha256sum -c` when that
//!   tool is available at install time.
//!
//! ## Determinism
//!
//! Two packs over the same inputs are byte-identical because every source of
//! nondeterminism is removed:
//!
//! - **File order** is fixed by sorting entries by their archive-relative path.
//! - **Per-file metadata** is fixed: `mtime = PACK_EPOCH`, `uid = gid = 0`,
//!   `uname = gname = ""`, `mode = 0o644` (regular files).
//! - **The manifest content** is part of the hashed listing, so a changed
//!   manifest changes the hash; the archive embeds the manifest bytes verbatim.
//!
//! ## The archive layout
//!
//! Everything is scoped under a single root directory named after the manifest
//! file stem (`/path/to/Catbee Drift.toml` → `catbee-drift/`), so the archive is
//! self-contained and unpacks cleanly:
//!
//! ```text
//! catbee-drift/
//!   Catbee Drift.toml   # the manifest
//!   wallpapers/…        # the referenced wallpaper assets
//! ```
//!
//! The root dir entry itself is *not* written (only files are), keeping the
//! listing a flat, sorted list of file records.
//!
//! ## The manifest hash
//!
//! The hash is the SHA-256 over a canonical listing: one `<sha256hex><space><space><path>`
//! line per file, sorted by path, each line newline-terminated. The manifest file
//! is included in the listing under its archive-relative path, so the hash covers
//! the manifest *and* its assets.

use crate::manifest::Theme;
use std::fs::File;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

/// The fixed modification time baked into every archived file, so archives are
/// reproducible regardless of when they are built. This is the Unix epoch.
pub const PACK_EPOCH: u64 = 0;

/// The fixed file mode for archived regular files (`rw-r--r--`).
const ARCHIVE_FILE_MODE: u32 = 0o644;

/// The fixed uname baked into every archive header (empty, per the determinism
/// contract).
const ARCHIVE_UNAME: &str = "";

/// The fixed gname baked into every archive header (empty).
const ARCHIVE_GNAME: &str = "";

/// The archive file extension.
const ARCHIVE_EXT: &str = "tar";

/// The manifest-hash sidecar file name.
const MANIFEST_HASH_NAME: &str = "manifest.sha256";

/// The number of zero bytes at the end of a tar stream that marks the end of
/// archive (two 512-byte blocks).
const EOA_MARKERS: usize = 1024;

/// The ustar `name` field width: 100 bytes (offset 0).
const NAME_FIELD: usize = 100;
/// The ustar `prefix` field width: 155 bytes (offset 329).
const PREFIX_FIELD: usize = 155;

/// A single file to archive, with its archive-relative path and its bytes.
///
/// The bytes are materialized when the archive is built: the manifest comes from
/// the theme's canonical serialization (in memory), and each referenced asset is
/// read from disk. See [`collect_items`].
#[derive(Debug, Clone, PartialEq, Eq)]
struct ArchiveEntry {
    /// The path of this file *inside* the archive (e.g. `catbee.toml/theme.toml`).
    archive_path: String,
    /// The bytes archived at `archive_path`.
    bytes: Vec<u8>,
}

/// The result of a successful [`pack`]: the paths of the artifacts written and the
/// manifest hash that describes them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackResult {
    /// Path to the written `.tar` archive.
    pub archive_path: PathBuf,
    /// Path to the written `manifest.sha256` sidecar.
    pub manifest_hash_path: PathBuf,
    /// The overall listing digest (SHA-256 over the canonical listing),
    /// lowercase hex. Covers the manifest AND its referenced assets, so a
    /// changed asset changes it — this is the single digest that identifies the
    /// whole package.
    pub manifest_hash: String,
    /// The manifest file's own per-file SHA-256 (lowercase hex): the line in the
    /// listing that names the manifest, verifiable with `sha256sum -c`.
    pub manifest_file_hash: String,
    /// The canonical listing the hash was computed over (one line per file).
    pub listing: String,
    /// The archive-relative paths, in the order they appear in the archive.
    pub entries: Vec<String>,
}

/// Errors produced while packaging a theme.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PackError {
    /// A referenced asset could not be read from disk.
    Read {
        /// The archive-relative path of the file that failed to read.
        path: String,
        /// The underlying I/O error message.
        source: String,
    },
    /// A referenced asset does not exist on disk.
    Missing {
        /// The archive-relative path of the missing asset.
        path: String,
    },
    /// Writing the archive or sidecar failed.
    Write {
        /// The underlying I/O error message.
        source: String,
    },
}

impl std::fmt::Display for PackError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PackError::Read { path, source } => {
                write!(f, "could not read asset `{path}`: {source}")
            }
            PackError::Missing { path } => write!(f, "referenced asset `{path}` is missing"),
            PackError::Write { source } => write!(f, "could not write archive: {source}"),
        }
    }
}

impl std::error::Error for PackError {}

/// A single file to archive before its bytes are materialized.
///
/// Every item carries its archive-relative path (used for sorting and for the
/// canonical listing). The manifest is held in memory (the theme's canonical
/// serialization); each referenced asset is read from disk at its
/// manifest-relative path. See [`collect_items`].
#[derive(Debug, Clone, PartialEq, Eq)]
enum CollectionItem {
    /// The manifest, with its canonical serialized bytes and archive path.
    Manifest {
        /// The path of the manifest inside the archive (e.g. `catbee.toml/catbee.toml`).
        archive_path: String,
        /// The manifest's canonical serialized bytes.
        bytes: Vec<u8>,
    },
    /// A referenced asset: the on-disk source path and its archive-relative path.
    Asset {
        /// Path on disk to read the asset from.
        source: PathBuf,
        /// The path of this asset inside the archive (e.g. `catbee.toml/wallpapers/x.svg`).
        archive_path: String,
    },
}

/// Collect the manifest and every asset it references into a list of
/// [`CollectionItem`]s, scoped under `<stem>/`.
///
/// `manifest_path` is the on-disk path of the manifest; `manifest_bytes` is the
/// manifest's canonical bytes (the theme's serialization, in memory); `theme`
/// supplies the referenced wallpapers. Returns the items sorted by archive-relative
/// path, or a [`PackError`] if a referenced wallpaper path is empty.
fn collect_items(
    manifest_path: &Path,
    manifest_bytes: &[u8],
    theme: &Theme,
) -> Result<Vec<CollectionItem>, PackError> {
    let stem = archive_root_stem(manifest_path);

    let mut items: Vec<CollectionItem> = Vec::new();

    // The manifest itself, scoped under the root dir. Its bytes come from the
    // theme's canonical serialization (in memory), so a changed manifest changes
    // the hash even when the on-disk file is untouched.
    let manifest_name = manifest_path
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| "theme.toml".to_string());
    items.push(CollectionItem::Manifest {
        archive_path: format!("{stem}/{manifest_name}"),
        bytes: manifest_bytes.to_vec(),
    });

    // Every referenced wallpaper asset, with a path relative to the manifest dir.
    for wallpaper in &theme.wallpapers {
        if wallpaper.file.trim().is_empty() {
            return Err(PackError::Read {
                path: format!("{stem}/wallpapers/<empty>"),
                source: "wallpaper `file` is empty".to_string(),
            });
        }
        let archive_path = format!("{stem}/{}", wallpaper.file);
        let source = match manifest_path.parent() {
            Some(dir) => dir.join(&wallpaper.file),
            // No parent (a bare filename): resolve relative to CWD.
            None => PathBuf::from(&wallpaper.file),
        };
        items.push(CollectionItem::Asset { source, archive_path });
    }

    // Deterministic: sort by archive-relative path.
    items.sort_by(|a, b| archive_path_of(a).cmp(archive_path_of(b)));
    Ok(items)
}

/// The archive-relative path of a [`CollectionItem`], for sorting.
fn archive_path_of(item: &CollectionItem) -> &str {
    match item {
        CollectionItem::Manifest { archive_path, .. } => archive_path,
        CollectionItem::Asset { archive_path, .. } => archive_path,
    }
}

/// The archive root directory name for a manifest: its file stem, slugified so
/// it is filesystem- and path-safe.
///
/// `/path/to/Catbee Drift.toml` → `catbee-drift`.
fn archive_root_stem(manifest_path: &Path) -> String {
    let stem = manifest_path
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();
    let slug = slugify(&stem);
    // A manifest with no usable stem (e.g. the filesystem root `/`, whose
    // `file_stem()` is the empty string) must still get a non-empty, path-safe
    // archive root — an empty root would drop the leading `/` from every entry
    // path and produce a broken archive. Fall back to the crate name.
    if slug.is_empty() {
        return "pengy-theme".to_string();
    }
    slug
}

/// Lowercase a string and replace any run of non-`[a-z0-9]` characters with a
/// single `-`, trimming leading/trailing dashes.
///
/// Keeps archive root names stable and path-safe. A single leading dot is
/// preserved (so a hidden-file-style stem like `.hidden` stays `.hidden` rather
/// than being dropped or turned into a leading dash).
fn slugify(s: &str) -> String {
    let mut out = String::new();
    let mut chars = s.chars();
    // Preserve a single leading dot (hidden-file-style stem like `.hidden`)
    // without treating it as a run of separators.
    if chars.clone().next() == Some('.') {
        out.push('.');
        chars.next();
    }
    let mut pending_dash = false;
    for c in chars {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
            pending_dash = false;
        } else if pending_dash {
            // Collapse consecutive separators into a single dash.
        } else if !out.is_empty() {
            out.push('-');
            pending_dash = true;
        }
    }
    // Trim a trailing dash produced by trailing separators.
    if pending_dash {
        out.pop();
    }
    out
}

/// Build the deterministic ustar header for a regular file.
///
/// Implements the POSIX *ustar* format: a 512-byte block with fixed metadata
/// (`mtime = PACK_EPOCH`, `uid = gid = 0`, empty `uname`/`gname`, the given
/// `mode`) and a correctly computed checksum. Long names are split across the
/// `prefix`/`name` fields to stay within the 255-byte ustar limit.
/// A single 512-byte POSIX *ustar* header block, laid out by field per the
/// specification. Offsets are the canonical ustar ones: `name`@0, `mode`@100,
/// `uid`@108, `gid`@116, `size`@124, `mtime`@136, `checksum`@148, `typeflag`@156,
/// `linkname`@157, `ustar` magic@257, version@263, `uname`@265, `gname`@297,
/// `prefix`@329. Building the block as a flat array of `(offset, value)` pairs
/// keeps the layout explicit and easy to audit against the spec.
fn build_ustar_header(name: &str, size: u64, mode: u32) -> Vec<u8> {
    let mut header = vec![0u8; 512];

    // Split the path across the ustar `prefix`/`name` fields so neither overflows
    // its fixed-width slot (`name` <= 100 bytes, `prefix` <= 155 bytes).
    let (prefix, file_name) = split_ustar_name(name);

    // The `name` field is a C string (NUL-terminated) at offset 0; the `prefix`
    // field is a C string at offset 329. Both are at most their field width.
    write_cstr(&mut header[0..100], &file_name);
    write_cstr(&mut header[329..484], &prefix);

    // Numeric fields are octal, space-padded, right-aligned, and exactly the
    // field width (no NUL terminator). `write_octal` fills the whole slice.
    write_octal(&mut header[100..108], mode as u64); // mode (8)
    write_octal(&mut header[108..116], 0); // uid (8)
    write_octal(&mut header[116..124], 0); // gid (8)
    write_octal(&mut header[124..136], size); // size (12)
    write_octal(&mut header[136..148], PACK_EPOCH); // mtime (12)

    // typeflag: '0' = regular file (offset 156).
    header[156] = b'0';

    // ustar magic "ustar\0" (6 bytes) and version "00" (2 bytes).
    header[257..263].copy_from_slice(b"ustar\0");
    header[263..265].copy_from_slice(b"00");

    // uname / gname (32 bytes each), left at their fixed offsets.
    write_cstr(&mut header[265..297], ARCHIVE_UNAME);
    write_cstr(&mut header[297..329], ARCHIVE_GNAME);

    // Checksum: the sum of all 512 bytes with the 8-byte checksum field (offset
    // 148, treated as 8 space characters, 0x20). Compute the raw sum over the
    // whole block, then subtract the field's own bytes and add 8 spaces so the
    // stored value equals the sum with the field as spaces.
    let raw: u64 = header.iter().map(|b| *b as u64).sum();
    let field_bytes: u64 = header[148..156].iter().map(|b| *b as u64).sum();
    let checksum = raw - field_bytes + (8 * 0x20);

    write_octal(&mut header[148..156], checksum);
    header
}

/// Split a path into `(prefix, name)` that fits the ustar `prefix`/`name` fields.
///
/// Returns `(String::new(), full_path)` when the path fits in the 100-byte name
/// field. Otherwise it finds the *last* `/` at index `p` such that the prefix
/// `path[..p+1]` fits its 155-byte field and the name `path[p+1..]` fits the
/// 100-byte field, so neither side overflows. If no such split exists (the path
/// is too long to divide cleanly), the whole path is truncated to fit the name
/// field — paths are slug-safe here, so truncation cannot corrupt the format.
fn split_ustar_name(path: &str) -> (String, String) {
    if path.len() <= NAME_FIELD {
        return (String::new(), path.to_string());
    }
    let total = path.len();
    // A valid split point `split` (= prefix length) must satisfy:
    //   split <= PREFIX_FIELD           (prefix fits its field)
    //   total - split <= NAME_FIELD     (name fits its field)
    // i.e. split in [total - NAME_FIELD, PREFIX_FIELD]. The split sits right
    // after a `/` so neither side is torn mid-component.
    let min_split = total.saturating_sub(NAME_FIELD);
    let max_split = PREFIX_FIELD;
    let split = if min_split <= max_split {
        (min_split..=max_split)
            .rev()
            .find(|&split| path.as_bytes()[split - 1] == b'/')
            .unwrap_or(max_split)
    } else {
        // Path longer than both fields combined: truncate to the name field.
        max_split
    };
    // Guard against a split beyond the string (only reachable when no '/' was
    // found and `max_split` exceeds the length).
    let split = std::cmp::min(split, total);
    let prefix = &path[..split];
    // The name is `path[split..]`; it is guaranteed <= NAME_FIELD by the split
    // bounds, but hard-truncate defensively.
    let name = &path[split..];
    let name = if name.len() > NAME_FIELD { &name[..NAME_FIELD] } else { name };
    (prefix.to_string(), name.to_string())
}

/// Write `s` into `dst` as a C string (null-terminated, truncated to fit).
fn write_cstr(dst: &mut [u8], s: &str) {
    let bytes = s.as_bytes();
    let take = bytes.len().min(dst.len().saturating_sub(1));
    dst[..take].copy_from_slice(&bytes[..take]);
    dst[take] = 0;
}

/// Write `value` into `dst` as a space-padded octal field, right-aligned and
/// filled to exactly `dst.len()` bytes.
///
/// ustar numeric fields (mode/uid/gid/size/mtime/checksum) are fixed-width and
/// space-padded — they are *not* NUL-terminated, so a value that fills the field
/// must not have its last digit overwritten.
fn write_octal(dst: &mut [u8], value: u64) {
    let octal = format!("{value:o}");
    // A value wider than the field overflows leftward (the high digits spill off
    // the left edge); that is the correct behaviour for the fixed ustar fields.
    let start = dst.len().saturating_sub(octal.len());
    for (i, out) in dst.iter_mut().enumerate() {
        if i < start {
            *out = b' ';
        } else {
            *out = octal.as_bytes()[i - start];
        }
    }
}

/// Pad `data` to a multiple of 512 bytes with zero bytes.
fn pad_to_512(data: &[u8]) -> Vec<u8> {
    let rem = data.len() % 512;
    if rem == 0 {
        data.to_vec()
    } else {
        let mut out = data.to_vec();
        out.extend_from_slice(&vec![0u8; 512 - rem]);
        out
    }
}

/// Write a file record: header block, data (zero-padded to 512), then the
/// 1024-byte end-of-archive markers when `is_last`.
fn write_file_record<W: Write>(
    w: &mut W,
    entry: &ArchiveEntry,
    bytes: &[u8],
    is_last: bool,
) -> Result<(), PackError> {
    let header = build_ustar_header(&entry.archive_path, bytes.len() as u64, ARCHIVE_FILE_MODE);
    w.write_all(&header).map_err(|e| PackError::Write {
        source: e.to_string(),
    })?;
    w.write_all(&pad_to_512(bytes)).map_err(|e| PackError::Write {
        source: e.to_string(),
    })?;
    if is_last {
        w.write_all(&[0u8; EOA_MARKERS]).map_err(|e| PackError::Write {
            source: e.to_string(),
        })?;
    }
    Ok(())
}

/// Pack a theme into a deterministic archive + manifest hash.
///
/// - `manifest_path` is the on-disk manifest path (used to scope the archive and
///   name the manifest entry); `theme` is the already-parsed [`Theme`] whose
///   canonical serialization is the manifest content and whose wallpapers define
///   the referenced assets.
/// - `out_dir` is where the `.tar` and `manifest.sha256` are written.
///
/// Returns a [`PackResult`] with the artifact paths, the manifest hash, and the
/// canonical listing.
///
/// ## The manifest content
///
/// The archived manifest is the theme's **canonical serialization** (in memory),
/// not the raw bytes read from disk. Two manifests that parse to the same
/// [`Theme`] therefore archive and hash identically, and a changed manifest
/// (e.g. a version bump) changes the hash even if the on-disk file is untouched.
///
/// ```no_run
/// use pengy_theme::manifest::{load_manifest, Theme};
/// use pengy_theme::pack::pack;
/// use std::path::Path;
///
/// let theme = load_manifest("theme.toml")?;
/// let result = pack(Path::new("theme.toml"), &theme, Path::new("dist"))?;
/// println!("packed {} => {}", result.manifest_hash, result.archive_path.display());
/// # Ok::<(), Box<dyn std::error::Error>>(())
/// ```
pub fn pack(
    manifest_path: &Path,
    theme: &Theme,
    out_dir: &Path,
) -> Result<PackResult, PackError> {
    std::fs::create_dir_all(out_dir).map_err(|e| PackError::Write {
        source: e.to_string(),
    })?;

    // 1. Materialize the manifest's canonical bytes (the theme's serialization)
    //    and collect the manifest + referenced assets.
    let manifest_bytes = serialize_manifest(theme)?;
    let items = collect_items(manifest_path, &manifest_bytes, theme)?;
    let entries = materialize_items(&items)?;

    // 2. Compute each file's hash and build the canonical listing.
    let mut listing = String::new();
    let mut listing_hasher = Sha256::new();
    let mut archive_paths: Vec<String> = Vec::with_capacity(entries.len());
    // The manifest file's own per-file hash (a line in the listing, verifiable
    // with `sha256sum -c`). Captured by matching the manifest's archive path so
    // it is correct even if a short-named asset sorts before it.
    let manifest_archive_path = format!(
        "{}/{}",
        archive_root_stem(manifest_path),
        manifest_path
            .file_name()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_else(|| "theme.toml".to_string())
    );
    let mut manifest_file_hash: Option<String> = None;

    for entry in &entries {
        let file_hash = hex::encode(Sha256::digest(&entry.bytes));
        if entry.archive_path == manifest_archive_path {
            manifest_file_hash = Some(file_hash.clone());
        }
        // Canonical line: `<hex>  <archive-path>\n` (sha256sum-compatible).
        listing.push_str(&format!("{}  {}\n", file_hash, entry.archive_path));
        listing_hasher.update(file_hash.as_bytes());
        listing_hasher.update(b" ");
        listing_hasher.update(entry.archive_path.as_bytes());
        listing_hasher.update(b"\n");
        archive_paths.push(entry.archive_path.clone());
    }
    // The overall listing digest (covers the manifest AND its assets).
    let manifest_hash = hex::encode(listing_hasher.finalize());
    // The manifest file's own hash — unwrap is safe: the manifest is always an
    // entry (see `collect_items`).
    let manifest_file_hash = manifest_file_hash.expect("the manifest is always archived");

    // 3. Write the deterministic archive (entries are already sorted by path).
    let stem = archive_root_stem(manifest_path);
    let archive_path = out_dir.join(format!("{stem}.{ARCHIVE_EXT}"));
    let mut archive = File::create(&archive_path).map_err(|e| PackError::Write {
        source: e.to_string(),
    })?;
    let last = entries.len().saturating_sub(1);
    for (i, entry) in entries.iter().enumerate() {
        write_file_record(&mut archive, entry, &entry.bytes, i == last)?;
    }
    archive
        .flush()
        .map_err(|e| PackError::Write { source: e.to_string() })?;

    // 4. Write the manifest hash sidecar (the listing itself).
    let manifest_hash_path = out_dir.join(MANIFEST_HASH_NAME);
    File::create(&manifest_hash_path)
        .and_then(|mut f| f.write_all(listing.as_bytes()))
        .map_err(|e| PackError::Write { source: e.to_string() })?;

    Ok(PackResult {
        archive_path,
        manifest_hash_path,
        manifest_hash,
        manifest_file_hash,
        listing,
        entries: archive_paths,
    })
}

/// Serialize a [`Theme`] to its canonical manifest bytes (TOML).
///
/// This is the manifest content archived and hashed by [`pack`] /
/// [`manifest_hash`]. Serialization is deterministic (serde preserves struct
/// field order), so two themes that parse to the same [`Theme`] produce identical
/// bytes — and a changed manifest changes the hash.
fn serialize_manifest(theme: &Theme) -> Result<Vec<u8>, PackError> {
    let toml = toml::to_string(theme).map_err(|e| PackError::Write {
        source: format!("could not serialize manifest: {e}"),
    })?;
    Ok(toml.into_bytes())
}

/// Materialize a sorted list of [`CollectionItem`]s into byte-bearing
/// [`ArchiveEntry`]s, reading each referenced asset from disk.
///
/// Returns a [`PackError::Missing`] (or [`PackError::Read`]) if an asset cannot
/// be read. The manifest item's bytes come from memory (already materialized).
fn materialize_items(items: &[CollectionItem]) -> Result<Vec<ArchiveEntry>, PackError> {
    let mut entries: Vec<ArchiveEntry> = Vec::with_capacity(items.len());
    for item in items {
        let entry = match item {
            // The manifest's bytes are already materialized (in memory).
            CollectionItem::Manifest { archive_path, bytes } => ArchiveEntry {
                archive_path: archive_path.clone(),
                bytes: bytes.clone(),
            },
            // Read the referenced asset from disk.
            CollectionItem::Asset { source, archive_path } => {
                let bytes = std::fs::read(source).map_err(|e| match e.kind() {
                    io::ErrorKind::NotFound => PackError::Missing {
                        path: archive_path.clone(),
                    },
                    _ => PackError::Read {
                        path: archive_path.clone(),
                        source: e.to_string(),
                    },
                })?;
                ArchiveEntry {
                    archive_path: archive_path.clone(),
                    bytes,
                }
            }
        };
        entries.push(entry);
    }
    Ok(entries)
}

/// Compute the manifest hash for a theme without writing any artifacts.
///
/// Uses the same canonical form as [`pack`]: the manifest is the theme's
/// serialization and each referenced asset is read from disk. Handy for tests
/// and for callers that only want the digest.
pub fn manifest_hash(manifest_path: &Path, theme: &Theme) -> Result<String, PackError> {
    let manifest_bytes = serialize_manifest(theme)?;
    let items = collect_items(manifest_path, &manifest_bytes, theme)?;
    let entries = materialize_items(&items)?;
    let mut hasher = Sha256::new();
    for entry in &entries {
        let file_hash = Sha256::digest(&entry.bytes);
        hasher.update(file_hash);
        hasher.update(b"  ");
        hasher.update(entry.archive_path.as_bytes());
        hasher.update(b"\n");
    }
    Ok(hex::encode(hasher.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::manifest::Theme;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    // A manifest that references a wallpaper asset.
    const EXAMPLE: &str = r##"
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

    fn theme() -> Theme {
        Theme::from_toml(EXAMPLE).unwrap()
    }

    /// A fresh, unique scratch directory for a single test.
    ///
    /// Each pack test writes into its own `{pid}/pengy-pack-<n>-<counter>` dir so
    /// tests that run concurrently (cargo runs lib tests in parallel) never
    /// clobber one another's manifest or assets mid-run. A shared `{pid}` dir
    /// alone is not enough — a concurrent test's `remove_dir_all` would delete
    /// another test's inputs. The atomic counter guarantees uniqueness even
    /// across repeated runs within the same process.
    fn unique_dir(tag: &str) -> PathBuf {
        static COUNTER: AtomicUsize = AtomicUsize::new(0);
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!(
            "pengy-pack-{}-{}-{}",
            std::process::id(),
            tag,
            n
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn slugify_normalizes_and_lowercases() {
        assert_eq!(slugify("Catbee Drift"), "catbee-drift");
        assert_eq!(slugify("Foo_Bar baz"), "foo-bar-baz");
        assert_eq!(slugify("  leading and trailing  "), "leading-and-trailing");
        assert_eq!(archive_root_stem(Path::new("/a/b/Catbee Drift.toml")), "catbee-drift");
    }

    #[test]
    fn archive_root_stem_handles_edge_cases() {
        assert_eq!(archive_root_stem(Path::new("/no-stem")), "no-stem");
        assert_eq!(archive_root_stem(Path::new("/.hidden")), ".hidden");
        // A stem that slugifies to nothing falls back to a stable default.
        assert_eq!(archive_root_stem(Path::new("/")), "pengy-theme");
    }

    #[test]
    fn octal_writer_right_aligns_and_null_terminates() {
        let mut buf = [0u8; 8];
        write_octal(&mut buf, 0o644);
        // Space-padded, octal, right-aligned, filled to the field width.
        assert_eq!(&buf[..], b"     644");
    }

    #[test]
    fn octal_writer_handles_large_values() {
        let mut buf = [0u8; 8];
        write_octal(&mut buf, 512); // 0o1000 (4 octal digits)
        // Space-padded to the full 8-byte field width: 4 spaces + "1000".
        assert_eq!(&buf[..], b"    1000");
    }

    #[test]
    fn ustar_header_checksum_matches_manual() {
        let header = build_ustar_header("theme.toml", 12, 0o644);
        // Recover the checksum field (offset 148, 8 bytes) and compare to the
        // manual space-sum.
        let stored: u64 =
            u64::from_str_radix(std::str::from_utf8(&header[148..156]).unwrap().trim(), 8).unwrap();
        // The ustar checksum is the sum of all 512 header bytes with the 8-byte
        // checksum field itself treated as spaces (0x20). Only that field is
        // space-substituted — every other byte (including the NUL padding that
        // fills the name field) keeps its real value.
        let manual: u64 = header
            .iter()
            .enumerate()
            .map(|(i, &b)| {
                if (148..156).contains(&i) {
                    0x20
                } else {
                    b as u64
                }
            })
            .sum::<u64>();
        assert_eq!(stored, manual, "ustar checksum must equal the space-sum");
        // Magic + version + typeflag are at their canonical offsets.
        assert_eq!(&header[257..263], b"ustar\0");
        assert_eq!(&header[263..265], b"00");
        assert_eq!(header[156], b'0');
        // The size field (offset 124) is the octal size of the payload.
        let size_str = std::str::from_utf8(&header[124..136]).unwrap().trim();
        assert_eq!(size_str, format!("{:o}", 12u64), "size must be octal 14");
    }

    #[test]
    fn ustar_header_splits_long_names() {
        // A name longer than the 100-byte `name` field must split into prefix +
        // name, each fitting its field (prefix <= 155, name <= 100), with the
        // split landing on a '/' boundary. This path is 176 chars with a component
        // boundary (`/`) at index 80, which lies inside the valid split range
        // [total-100, 155] = [76, 155], so the split honors it: prefix = 81 chars
        // (80 x's + '/'), name = 95 chars, each within its field.
        let long = format!("{}{}{}", "x".repeat(80), "/".to_owned(), "y".repeat(95));
        assert_eq!(long.len(), 176);
        let (prefix, name) = split_ustar_name(&long);
        assert!(!prefix.is_empty(), "long names must use the prefix field");
        assert!(prefix.len() <= PREFIX_FIELD, "prefix fits its field: {}", prefix.len());
        assert!(name.len() <= NAME_FIELD, "name fits its field: {}", name.len());
        // The split sits right after a '/', so the prefix ends with '/'.
        assert_eq!(prefix.as_bytes()[prefix.len() - 1], b'/');
        // prefix + name reconstructs the original path exactly.
        assert_eq!(format!("{}{}", prefix, name), long);
    }

    #[test]
    fn ustar_header_name_fits_without_prefix() {
        // A name that fits in the 100-byte `name` field uses no prefix field.
        let (prefix, name) = split_ustar_name("short-name.toml");
        assert!(prefix.is_empty(), "short names must not use the prefix field");
        assert_eq!(name, "short-name.toml");
    }

    #[test]
    fn pad_to_512_rounds_up_to_multiple() {
        assert_eq!(pad_to_512(&[1u8; 512]).len(), 512);
        assert_eq!(pad_to_512(&[1u8; 511]).len(), 512);
        assert_eq!(pad_to_512(&[1u8; 513]).len(), 1024);
        assert_eq!(pad_to_512(&[]).len(), 0);
    }

    #[test]
    fn collect_items_includes_manifest_and_assets_sorted() {
        let theme = theme();
        let dir = unique_dir("collect");
        let manifest = dir.join("catbee-drift.toml");
        std::fs::write(&manifest, EXAMPLE).unwrap();
        std::fs::create_dir_all(dir.join("wallpapers")).unwrap();
        std::fs::write(dir.join("wallpapers/drift-mountain.svg"), b"<svg/>").unwrap();

        // The manifest bytes are only used for content; the archive paths come from
        // the manifest path and the theme's wallpapers.
        let items = collect_items(&manifest, EXAMPLE.as_bytes(), &theme).unwrap();
        let paths: Vec<&str> = items.iter().map(|i| archive_path_of(i)).collect();
        assert_eq!(
            paths,
            vec![
                "catbee-drift/catbee-drift.toml",
                "catbee-drift/wallpapers/drift-mountain.svg",
            ]
        );

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn pack_produces_deterministic_archive_and_hash() {
        // Pack twice into two dirs and assert byte-identical archives + hashes.
        let theme = theme();
        let base = unique_dir("deterministic");
        std::fs::create_dir_all(base.join("wallpapers")).unwrap();
        std::fs::write(base.join("wallpapers/drift-mountain.svg"), b"<svg/>").unwrap();
        let manifest = base.join("catbee-drift.toml");
        std::fs::write(&manifest, EXAMPLE).unwrap();

        let out1 = base.join("out1");
        let out2 = base.join("out2");
        let r1 = pack(&manifest, &theme, &out1).unwrap();
        let r2 = pack(&manifest, &theme, &out2).unwrap();

        assert_eq!(r1.manifest_hash, r2.manifest_hash, "hash must be deterministic");
        let a1 = std::fs::read(&r1.archive_path).unwrap();
        let a2 = std::fs::read(&r2.archive_path).unwrap();
        assert_eq!(a1, a2, "archives must be byte-identical");
        assert!(!a1.is_empty(), "archive must not be empty");

        std::fs::remove_dir_all(&base).ok();
    }

    #[test]
    fn pack_hash_covers_manifest_and_assets() {
        // Changing the manifest OR an asset changes the hash.
        let base = unique_dir("hash");
        std::fs::create_dir_all(base.join("wallpapers")).unwrap();
        std::fs::write(base.join("wallpapers/drift-mountain.svg"), b"<svg/>").unwrap();
        let manifest = base.join("catbee.toml");

        let good = theme();
        std::fs::write(&manifest, EXAMPLE).unwrap();
        let h_manifest = manifest_hash(&manifest, &good).unwrap();

        // Same manifest, different theme bytes => different hash.
        let mut other = good.clone();
        other.version = "9.9.9".to_string();
        let h_other = manifest_hash(&manifest, &other).unwrap();
        assert_ne!(h_manifest, h_other, "a changed manifest must change the hash");

        // Same manifest, different asset bytes => different hash.
        std::fs::write(base.join("wallpapers/drift-mountain.svg"), b"<svg CHANGED/>").unwrap();
        let h_asset = manifest_hash(&manifest, &good).unwrap();
        assert_ne!(h_manifest, h_asset, "a changed asset must change the hash");

        std::fs::remove_dir_all(&base).ok();
    }

    #[test]
    fn pack_missing_asset_errors() {
        let theme = theme();
        let dir = unique_dir("missing");
        let manifest = dir.join("catbee.toml");
        std::fs::write(&manifest, EXAMPLE).unwrap();
        // No wallpapers/ dir created => the referenced asset is missing.
        let err = pack(&manifest, &theme, &dir.join("out")).unwrap_err();
        assert!(matches!(err, PackError::Missing { .. }));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn pack_writes_sidecar_listing() {
        let theme = theme();
        // A unique dir per test (see `unique_dir`) so a concurrent test's cleanup
        // cannot delete this test's manifest or assets mid-pack.
        let dir = unique_dir("sidecar");
        std::fs::create_dir_all(dir.join("wallpapers")).unwrap();
        std::fs::write(dir.join("wallpapers/drift-mountain.svg"), b"<svg/>").unwrap();
        let manifest = dir.join("catbee.toml");
        std::fs::write(&manifest, EXAMPLE).unwrap();

        let result = pack(&manifest, &theme, &dir).unwrap();
        // The archive root is the manifest's slugified file stem (`catbee.toml` ->
        // `catbee`), so the archive is `catbee.tar`.
        assert!(result.archive_path.ends_with("catbee.tar"));
        assert!(result.manifest_hash_path.ends_with("manifest.sha256"));
        // The sidecar equals the listing, and the hash covers both files. The
        // manifest entry path (`catbee/catbee.toml`) still names the manifest file.
        let sidecar = std::fs::read_to_string(&result.manifest_hash_path).unwrap();
        assert_eq!(sidecar, result.listing);
        assert!(sidecar.contains("catbee.toml"));
        assert!(sidecar.contains("wallpapers/drift-mountain.svg"));
        // Hash is 64 lowercase hex chars.
        assert_eq!(result.manifest_hash.len(), 64);
        assert!(result.manifest_hash.chars().all(|c| c.is_ascii_hexdigit()));

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn pack_archive_is_valid_ustar_with_two_records() {
        // The archive must be a well-formed POSIX *ustar* stream: each 512-byte
        // header block has the canonical field layout (name@0, mode@100, uid@108,
        // gid@116, size@124, mtime@136, checksum@148, typeflag@156, linkname@157,
        // magic@257, version@263, uname@265, gname@297, prefix@329) and a checksum
        // that GNU tar would accept. It must contain two file records (manifest +
        // wallpaper) plus the 1024-byte end marker.
        let theme = theme();
        // A unique dir per test (see `unique_dir`) so a concurrent test's cleanup
        // cannot delete this test's manifest or assets mid-pack.
        let dir = unique_dir("ustar");
        std::fs::create_dir_all(dir.join("wallpapers")).unwrap();
        std::fs::write(dir.join("wallpapers/drift-mountain.svg"), b"<svg/>").unwrap();
        let manifest = dir.join("catbee.toml");
        std::fs::write(&manifest, EXAMPLE).unwrap();

        let result = pack(&manifest, &theme, &dir).unwrap();
        let bytes = std::fs::read(&result.archive_path).unwrap();

        // Two 512-byte headers + padded data + 1024 end-of-archive markers.
        assert!(bytes.len() >= 512 * 2 + 1024);
        assert_eq!(&bytes[bytes.len() - 1024..], &[0u8; 1024]);

        // Validate each 512-byte header block with the canonical ustar layout.
        let mut offset = 0;
        let mut records = 0;
        while offset + 512 <= bytes.len() {
            let block = &bytes[offset..offset + 512];
            // An all-zero block marks the end of the archive.
            if block.iter().all(|&b| b == 0) {
                break;
            }
            // ustar magic "ustar\0" at offset 257, version "00" at 263.
            assert_eq!(&block[257..263], b"ustar\0", "ustar magic at offset 257");
            assert_eq!(&block[263..265], b"00", "ustar version at offset 263");
            // typeflag '0' = regular file at offset 156.
            assert_eq!(block[156], b'0', "typeflag at offset 156");
            // Checksum at offset 148: sum of the block with the 8-byte checksum
            // field treated as spaces.
            let stored: u64 =
                u64::from_str_radix(std::str::from_utf8(&block[148..156]).unwrap().trim(), 8)
                    .expect("octal checksum");
            let raw: u64 = block.iter().map(|&b| b as u64).sum();
            let field: u64 = block[148..156].iter().map(|&b| b as u64).sum();
            let computed = raw - field + (8 * 0x20);
            assert_eq!(stored, computed, "checksum at offset 148");
            // The size field at offset 124 is a valid octal number.
            let size_str = std::str::from_utf8(&block[124..136]).unwrap().trim();
            assert!(!size_str.is_empty(), "size field at offset 124 must be set");

            records += 1;
            // Advance past the header + zero-padded data (rounded up to 512).
            let size: usize = u64::from_str_radix(size_str, 8).unwrap() as usize;
            let data_padded = (size + 511) / 512 * 512;
            offset += 512 + data_padded;
        }
        assert_eq!(records, 2, "archive must contain exactly two file records");

        std::fs::remove_dir_all(&dir).ok();
    }
}
