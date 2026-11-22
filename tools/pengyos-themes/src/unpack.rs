//! Unpack only the declared theme directories of a `.tar.gz` into a staging
//! directory, refusing anything that could write outside it.
//!
//! The rules (spec.md, *Icons*), learnt from the cosmic-files zip fix (patch 0002):
//!
//! * an entry whose path is absolute or has a `..` component refuses the whole
//!   archive, wherever it is;
//! * nothing is ever written through a symlink: every directory between the
//!   staging root and a new entry must be a real directory;
//! * a symlink entry must be relative and must point inside the themes being
//!   installed (Papirus-Dark links into Papirus, which is fine);
//! * only files, directories and symlinks; hard links, devices and fifos refuse;
//! * a byte and an entry budget stop an archive that unpacks to far more than the
//!   catalogue says.
//!
//! Entries outside the declared directories are skipped.

use std::fs;
use std::io::{self, Read};
use std::path::{Component, Path, PathBuf};

use tar::EntryType;

use crate::{err, io_at, Result};

/// One theme to take from the archive: everything under `from` (a path inside
/// the archive) lands in `staging/<to>`.
#[derive(Debug, Clone)]
pub struct Take {
    pub from: PathBuf,
    pub to: String,
}

#[derive(Debug, Clone, Copy)]
pub struct Budget {
    pub bytes: u64,
    pub entries: u64,
}

/// What was unpacked.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Unpacked {
    pub files: u64,
    pub links: u64,
    pub dirs: u64,
    pub bytes: u64,
}

/// Split a relative path into its normal components; `None` if it is absolute or
/// has a `..` (or a Windows prefix).
pub fn plain_components(p: &Path) -> Option<Vec<&std::ffi::OsStr>> {
    let mut out = Vec::new();
    for c in p.components() {
        match c {
            Component::Normal(s) => out.push(s),
            Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => return None,
        }
    }
    Some(out)
}

/// Resolve `target` (a symlink's text) relative to `dir` (the link's directory,
/// relative to the staging root), lexically. `None` if it is absolute or climbs
/// above the staging root.
pub fn resolve_link(dir: &Path, target: &Path) -> Option<PathBuf> {
    let mut parts: Vec<std::ffi::OsString> =
        plain_components(dir)?.into_iter().map(|s| s.to_owned()).collect();
    for c in target.components() {
        match c {
            Component::Normal(s) => parts.push(s.to_owned()),
            Component::CurDir => {}
            Component::ParentDir => {
                parts.pop()?;
            }
            Component::RootDir | Component::Prefix(_) => return None,
        }
    }
    Some(parts.iter().collect())
}

/// Make every directory from `root` down to `root/rel` (inclusive), refusing to
/// pass through anything that is not a real directory.
fn real_dirs(root: &Path, rel: &[&std::ffi::OsStr]) -> Result<()> {
    let mut at = root.to_path_buf();
    for part in rel {
        at.push(part);
        match fs::symlink_metadata(&at) {
            Ok(m) if m.file_type().is_symlink() => {
                return Err(err!(
                    "refusing the archive: it writes through the symlink {}",
                    at.strip_prefix(root).unwrap_or(&at).display()
                ))
            }
            Ok(m) if m.is_dir() => {}
            Ok(_) => {
                return Err(err!(
                    "refusing the archive: {} is a file and a directory",
                    at.strip_prefix(root).unwrap_or(&at).display()
                ))
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => {
                io_at(fs::create_dir(&at), "cannot create", &at)?
            }
            Err(e) => return Err(err!("cannot inspect {}: {e}", at.display())),
        }
    }
    Ok(())
}

/// Unpack `archive` (gzip-compressed tar) into `staging`, which must exist and
/// be empty. On error, whatever was written stays in `staging` for the caller to
/// remove.
pub fn unpack_tar_gz(archive: &Path, staging: &Path, take: &[Take], budget: Budget) -> Result<Unpacked> {
    let f = io_at(fs::File::open(archive), "cannot open", archive)?;
    unpack_tar(flate2::read::GzDecoder::new(f), staging, take, budget)
}

pub fn unpack_tar<R: Read>(reader: R, staging: &Path, take: &[Take], budget: Budget) -> Result<Unpacked> {
    let tos: Vec<&str> = take.iter().map(|t| t.to.as_str()).collect();
    let mut ar = tar::Archive::new(reader);
    let mut done = Unpacked::default();
    let mut seen: u64 = 0;
    let bad = |e: io::Error| err!("the archive is damaged: {e}");
    for entry in ar.entries().map_err(bad)? {
        let mut entry = entry.map_err(bad)?;
        seen += 1;
        if seen > budget.entries {
            return Err(err!("refusing the archive: more than {} entries", budget.entries));
        }
        let kind = entry.header().entry_type();
        if matches!(kind, EntryType::XGlobalHeader | EntryType::XHeader) {
            continue;
        }
        let path = entry.path().map_err(bad)?.into_owned();
        let Some(parts) = plain_components(&path) else {
            return Err(err!("refusing the archive: unsafe path {}", path.display()));
        };
        // Which declared theme is it in, and where below it?
        let Some((to, rest)) = take.iter().find_map(|t| {
            let from = plain_components(&t.from)?;
            (parts.len() >= from.len() && parts[..from.len()] == from[..])
                .then(|| (t.to.as_str(), parts[from.len()..].to_vec()))
        }) else {
            continue;
        };
        let mut rel: Vec<&std::ffi::OsStr> = vec![std::ffi::OsStr::new(to)];
        rel.extend(rest);
        let dest: PathBuf = rel.iter().fold(staging.to_path_buf(), |p, c| p.join(c));
        let parent = &rel[..rel.len() - 1];
        match kind {
            EntryType::Directory => {
                real_dirs(staging, &rel)?;
                done.dirs += 1;
            }
            EntryType::Regular | EntryType::Continuous => {
                real_dirs(staging, parent)?;
                let left = budget.bytes.saturating_sub(done.bytes);
                let mut out = fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&dest)
                    .map_err(|e| {
                        err!("refusing the archive: cannot create {} ({e})", rel_display(&rel))
                    })?;
                let n = io::copy(&mut (&mut entry).take(left + 1), &mut out).map_err(bad)?;
                if n > left {
                    return Err(err!(
                        "refusing the archive: it unpacks to more than {} MB",
                        budget.bytes / 1_000_000
                    ));
                }
                done.bytes += n;
                done.files += 1;
            }
            EntryType::Symlink => {
                let target = entry
                    .link_name()
                    .map_err(bad)?
                    .ok_or_else(|| err!("the archive is damaged: a link with no target"))?
                    .into_owned();
                let parent_path: PathBuf = parent.iter().collect();
                let lands = resolve_link(&parent_path, &target);
                let inside = lands.as_ref().is_some_and(|l| {
                    l.components().next().is_some_and(|c| {
                        tos.iter().any(|t| c.as_os_str() == std::ffi::OsStr::new(t))
                    })
                });
                if !inside {
                    return Err(err!(
                        "refusing the archive: {} links outside the theme ({})",
                        rel_display(&rel),
                        target.display()
                    ));
                }
                real_dirs(staging, parent)?;
                std::os::unix::fs::symlink(&target, &dest).map_err(|e| {
                    err!("refusing the archive: cannot create {} ({e})", rel_display(&rel))
                })?;
                done.links += 1;
            }
            other => {
                return Err(err!(
                    "refusing the archive: {} is a {:?}, not a file, directory or symlink",
                    rel_display(&rel),
                    other
                ))
            }
        }
    }
    Ok(done)
}

fn rel_display(rel: &[&std::ffi::OsStr]) -> String {
    rel.iter().collect::<PathBuf>().display().to_string()
}

#[cfg(test)]
pub mod testing {
    //! Build small tarballs in memory, including the hostile ones the `tar`
    //! crate's safe builder refuses to make.
    use std::io::Write;

    pub enum E<'a> {
        File(&'a str, &'a [u8]),
        Dir(&'a str),
        Link(&'a str, &'a str),
        Hard(&'a str, &'a str),
    }

    fn header(name: &str, kind: u8, size: u64, link: &str) -> [u8; 512] {
        let mut h = [0u8; 512];
        h[..name.len()].copy_from_slice(name.as_bytes());
        let mode = if kind == b'5' { "0000755\0" } else { "0000644\0" };
        h[100..108].copy_from_slice(mode.as_bytes());
        h[108..116].copy_from_slice(b"0000000\0");
        h[116..124].copy_from_slice(b"0000000\0");
        h[124..136].copy_from_slice(format!("{size:011o}\0").as_bytes());
        h[136..148].copy_from_slice(b"00000000000\0");
        h[156] = kind;
        h[157..157 + link.len()].copy_from_slice(link.as_bytes());
        h[257..263].copy_from_slice(b"ustar\0");
        h[263..265].copy_from_slice(b"00");
        h[148..156].copy_from_slice(b"        ");
        let sum: u32 = h.iter().map(|&b| b as u32).sum();
        h[148..156].copy_from_slice(format!("{sum:06o}\0 ").as_bytes());
        h
    }

    pub fn tar(entries: &[E]) -> Vec<u8> {
        let mut out = Vec::new();
        for e in entries {
            match e {
                E::File(n, data) => {
                    out.extend(header(n, b'0', data.len() as u64, ""));
                    out.extend(*data);
                    out.resize(out.len().div_ceil(512) * 512, 0);
                }
                E::Dir(n) => out.extend(header(n, b'5', 0, "")),
                E::Link(n, t) => out.extend(header(n, b'2', 0, t)),
                E::Hard(n, t) => out.extend(header(n, b'1', 0, t)),
            }
        }
        out.extend([0u8; 1024]);
        out
    }

    pub fn tar_gz(entries: &[E]) -> Vec<u8> {
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        gz.write_all(&tar(entries)).unwrap();
        gz.finish().unwrap()
    }
}

#[cfg(test)]
mod tests {
    use super::testing::{tar, E};
    use super::*;

    const BIG: Budget = Budget { bytes: 1 << 20, entries: 1000 };

    fn take() -> Vec<Take> {
        vec![
            Take { from: "pack-1/Ice".into(), to: "Ice".into() },
            Take { from: "pack-1/Ice-Dark".into(), to: "Ice-Dark".into() },
        ]
    }

    fn run(entries: &[E], budget: Budget) -> (tempfile::TempDir, Result<Unpacked>) {
        let d = tempfile::tempdir().unwrap();
        let r = unpack_tar(&tar(entries)[..], d.path(), &take(), budget);
        (d, r)
    }

    #[test]
    fn takes_only_the_declared_themes_with_their_links() {
        let (d, r) = run(
            &[
                E::Dir("pack-1/"),
                E::File("pack-1/README.md", b"x"),
                E::File("pack-1/Ice/index.theme", b"[Icon Theme]\n"),
                E::File("pack-1/Ice/48x48/apps/a.svg", b"<svg/>"),
                E::Link("pack-1/Ice/48x48/apps/b.svg", "a.svg"),
                E::Link("pack-1/Ice-Dark/48x48", "../Ice/48x48"),
                E::File("pack-1/Ice-Dark/index.theme", b"[Icon Theme]\n"),
                E::File("pack-1/Other/index.theme", b"x"),
            ],
            BIG,
        );
        let u = r.unwrap();
        assert_eq!((u.files, u.links), (3, 2));
        let p = d.path();
        assert!(p.join("Ice/48x48/apps/a.svg").is_file());
        assert_eq!(fs::read_link(p.join("Ice/48x48/apps/b.svg")).unwrap(), Path::new("a.svg"));
        assert!(p.join("Ice-Dark/48x48/apps/a.svg").is_file(), "the cross-theme link works");
        assert!(!p.join("README.md").exists() && !p.join("Other").exists());
    }

    #[test]
    fn a_dotdot_or_absolute_path_anywhere_refuses_the_archive() {
        for bad in ["pack-1/../evil", "/etc/evil", "pack-1/Ice/../../evil"] {
            let (d, r) = run(&[E::File(bad, b"x")], BIG);
            assert!(r.unwrap_err().0.contains("unsafe path"), "{bad}");
            assert_eq!(fs::read_dir(d.path()).unwrap().count(), 0);
        }
    }

    #[test]
    fn nothing_is_written_through_a_symlink() {
        // The link itself is allowed (it stays inside Ice), but writing below it
        // would land wherever it points.
        let (_d, r) = run(
            &[
                E::Dir("pack-1/Ice/real/"),
                E::Link("pack-1/Ice/sneaky", "real"),
                E::File("pack-1/Ice/sneaky/x.svg", b"x"),
            ],
            BIG,
        );
        assert!(r.unwrap_err().0.contains("writes through the symlink Ice/sneaky"));
    }

    #[test]
    fn links_must_stay_inside_the_themes() {
        for t in ["../../../etc/passwd", "/etc/passwd", "../Other/x", "../../x"] {
            let (_d, r) = run(&[E::Link("pack-1/Ice/x.svg", t)], BIG);
            assert!(r.unwrap_err().0.contains("links outside the theme"), "{t}");
        }
    }

    #[test]
    fn hard_links_and_duplicates_refuse() {
        let (_d, r) = run(&[E::File("pack-1/Ice/a", b"x"), E::Hard("pack-1/Ice/b", "pack-1/Ice/a")], BIG);
        assert!(r.unwrap_err().0.contains("not a file, directory or symlink"));
        let (_d, r) = run(&[E::File("pack-1/Ice/a", b"x"), E::File("pack-1/Ice/a", b"y")], BIG);
        assert!(r.unwrap_err().0.contains("cannot create Ice/a"));
    }

    #[test]
    fn budgets_stop_a_bomb() {
        let big = vec![b'x'; 5000];
        let (_d, r) = run(&[E::File("pack-1/Ice/a", &big)], Budget { bytes: 4000, entries: 10 });
        assert!(r.unwrap_err().0.contains("unpacks to more than"));
        let many: Vec<String> = (0..20).map(|i| format!("pack-1/Ice/{i}")).collect();
        let es: Vec<E> = many.iter().map(|n| E::File(n, b"x")).collect();
        let (_d, r) = run(&es, Budget { bytes: 1 << 20, entries: 10 });
        assert!(r.unwrap_err().0.contains("more than 10 entries"));
    }

    #[test]
    fn link_resolution() {
        assert_eq!(resolve_link(Path::new("A/b"), Path::new("../c")), Some("A/c".into()));
        assert_eq!(resolve_link(Path::new("A"), Path::new("../../c")), None);
        assert_eq!(resolve_link(Path::new("A"), Path::new("/c")), None);
    }
}
