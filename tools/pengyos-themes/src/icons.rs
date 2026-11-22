//! Icon themes: install a catalogue pack into `~/.local/share/icons`, use any
//! installed theme, remove a pack.
//!
//! `install` downloads the pack, refuses it unless its sha256 matches the
//! catalogue, unpacks only the declared theme directories into a staging
//! directory next to their destination (see [`crate::unpack`]), checks each has
//! an `index.theme`, and only then moves them into place. A failed install leaves
//! nothing behind. Each installed theme directory carries a marker
//! (`.pengyos-themes.json`), and `remove` only removes directories that have it.

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::catalogue::{valid_theme_dir, Catalogue, IconPack, COSMIC_ICONS};
use crate::fetch::Fetch;
use crate::unpack::{unpack_tar_gz, Budget, Take};
use crate::{desktop, err, hex, io_at, write_atomic, Paths, Result};

pub const MARKER: &str = ".pengyos-themes.json";
/// Time allowed for a pack download.
pub const DOWNLOAD_TIMEOUT_S: u64 = 900;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Marker {
    pub pack: String,
    pub version: String,
    pub sha256: String,
}

fn marker(paths: &Paths, dir: &str) -> Option<Marker> {
    let b = fs::read(paths.icons().join(dir).join(MARKER)).ok()?;
    serde_json::from_slice(&b).ok()
}

/// The version of `pack` installed, if every one of its themes is there.
pub fn installed(paths: &Paths, pack: &IconPack) -> Option<Marker> {
    let ms: Vec<Option<Marker>> = pack.themes.iter().map(|t| marker(paths, &t.dir)).collect();
    let first = ms.first()?.clone()?;
    ms.iter().all(|m| m.as_ref().is_some_and(|m| m.pack == pack.id && m.version == first.version)).then_some(first)
}

/// Where a theme called `name` is installed (the user's, then the system's), if
/// anywhere. Hidden themes count: COSMIC uses them when asked.
pub fn find_theme(paths: &Paths, name: &str) -> Option<PathBuf> {
    if !valid_theme_dir(name) {
        return None;
    }
    std::iter::once(paths.icons())
        .chain(paths.system_data.iter().map(|d| d.join("icons")))
        .map(|d| d.join(name))
        .find(|d| d.join("index.theme").is_file())
}

fn sha256_file(p: &Path) -> Result<String> {
    let mut f = io_at(fs::File::open(p), "cannot open", p)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    loop {
        let n = io_at(f.read(&mut buf), "cannot read", p)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(hex(&h.finalize()))
}

/// What `install` did.
#[derive(Debug, PartialEq)]
pub enum Installed {
    Fresh,
    Updated { from: String },
    AlreadyThere,
}

pub fn install(paths: &Paths, cat: &Catalogue, id: &str, fetch: &dyn Fetch) -> Result<Installed> {
    let pack = cat.pack(id).ok_or_else(|| err!("no icon theme {id:?} in the catalogue"))?;
    let had = installed(paths, pack);
    if had.as_ref().is_some_and(|m| m.version == pack.version && m.sha256 == pack.sha256) {
        return Ok(Installed::AlreadyThere);
    }
    let icons = paths.icons();
    // Never replace a theme this tool did not install.
    for t in &pack.themes {
        let dest = icons.join(&t.dir);
        if fs::symlink_metadata(&dest).is_ok() && marker(paths, &t.dir).is_none_or(|m| m.pack != pack.id) {
            return Err(err!(
                "{} is already there and was not installed by pengyos-themes: move it away first",
                dest.display()
            ));
        }
    }

    // Download and check.
    let cache = paths.cache.join("pengyos-themes");
    io_at(fs::create_dir_all(&cache), "cannot create", &cache)?;
    let archive = cache.join(format!("{}-{}.tar.gz", pack.id, pack.version));
    let part = cache.join(format!("{}-{}.tar.gz.part", pack.id, pack.version));
    let _ = fs::remove_file(&part);
    fetch.download(&pack.url, &part, pack.size + pack.size / 10 + 4096, DOWNLOAD_TIMEOUT_S).inspect_err(|_| {
        let _ = fs::remove_file(&part);
    })?;
    let got = sha256_file(&part)?;
    if got != pack.sha256 {
        let _ = fs::remove_file(&part);
        return Err(err!(
            "refusing {}: the download's sha256 is {got}, the catalogue says {}",
            pack.name,
            pack.sha256
        ));
    }
    io_at(fs::rename(&part, &archive), "cannot keep", &archive)?;

    // Unpack beside the destination (same filesystem, so the moves are renames).
    io_at(fs::create_dir_all(&icons), "cannot create", &icons)?;
    let staging = icons.join(format!(".pengyos-themes-{}.partial", pack.id));
    let _ = fs::remove_dir_all(&staging);
    io_at(fs::create_dir(&staging), "cannot create", &staging)?;
    let result = (|| {
        let take: Vec<Take> =
            pack.themes.iter().map(|t| Take { from: PathBuf::from(&t.path), to: t.dir.clone() }).collect();
        let budget = Budget { bytes: pack.installed_size + pack.installed_size / 5, entries: 2_000_000 };
        unpack_tar_gz(&archive, &staging, &take, budget)?;
        for t in &pack.themes {
            if !staging.join(&t.dir).join("index.theme").is_file() {
                return Err(err!("refusing {}: {} has no index.theme in the archive", pack.name, t.dir));
            }
        }
        let m = Marker { pack: pack.id.clone(), version: pack.version.clone(), sha256: pack.sha256.clone() };
        for t in &pack.themes {
            write_atomic(&staging.join(&t.dir).join(MARKER), serde_json::to_string_pretty(&m).unwrap().as_bytes())?;
        }
        // Move into place: the old copy (ours, an earlier version) aside first.
        for t in &pack.themes {
            let dest = icons.join(&t.dir);
            if fs::symlink_metadata(&dest).is_ok() {
                io_at(fs::rename(&dest, staging.join(format!(".old-{}", t.dir))), "cannot replace", &dest)?;
            }
            io_at(fs::rename(staging.join(&t.dir), &dest), "cannot install", &dest)?;
        }
        Ok(())
    })();
    let _ = fs::remove_dir_all(&staging);
    let _ = fs::remove_file(&archive);
    result?;
    Ok(match had {
        Some(m) => Installed::Updated { from: m.version },
        None => Installed::Fresh,
    })
}

/// `icons use <theme>`: any installed theme, or `Cosmic`.
pub fn use_theme(paths: &Paths, name: &str) -> Result<()> {
    if name != COSMIC_ICONS && find_theme(paths, name).is_none() {
        return Err(err!("no icon theme {name:?} is installed (`pengyos-themes icons list`)"));
    }
    desktop::snapshot(paths, &format!("icons {name}"))?;
    desktop::set_icon_theme(paths, name)
}

/// `icons remove <pack>`. If the desktop is using one of its themes, it goes
/// back to Cosmic first.
pub fn remove(paths: &Paths, cat: &Catalogue, id: &str) -> Result<bool> {
    let pack = cat.pack(id).ok_or_else(|| err!("no icon theme {id:?} in the catalogue"))?;
    let ours: Vec<&str> = pack
        .themes
        .iter()
        .map(|t| t.dir.as_str())
        .filter(|d| marker(paths, d).is_some_and(|m| m.pack == pack.id))
        .collect();
    if ours.is_empty() {
        return Err(err!("{} is not installed", pack.name));
    }
    let active = desktop::icon_theme(paths);
    let switched = ours.contains(&active.as_str());
    if switched {
        desktop::snapshot(paths, &format!("icons {COSMIC_ICONS}"))?;
        desktop::set_icon_theme(paths, COSMIC_ICONS)?;
    }
    for d in ours {
        let p = paths.icons().join(d);
        io_at(fs::remove_dir_all(&p), "cannot remove", &p)?;
    }
    Ok(switched)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalogue::tests::GOOD;
    use crate::fetch::stub::Stub;
    use crate::unpack::testing::{tar_gz, E};

    const URL: &str = "https://example.org/ice-1.tar.gz";

    fn pack_bytes() -> Vec<u8> {
        tar_gz(&[
            E::File("pack-1/Ice/index.theme", b"[Icon Theme]\nName=Ice\n"),
            E::File("pack-1/Ice/48x48/apps/a.svg", b"<svg/>"),
            E::File("pack-1/Ice-Dark/index.theme", b"[Icon Theme]\nName=Ice-Dark\n"),
            E::Link("pack-1/Ice-Dark/48x48", "../Ice/48x48"),
        ])
    }

    fn setup(bytes: &[u8]) -> (tempfile::TempDir, Paths, Catalogue, Stub) {
        let d = tempfile::tempdir().unwrap();
        let p = Paths::under(d.path());
        let text = GOOD
            .replace("0000000000000000000000000000000000000000000000000000000000000000", &crate::sha256_hex(bytes))
            .replace("size = 100", &format!("size = {}", bytes.len()));
        let cat = Catalogue::parse(&text).unwrap();
        (d, p, cat, Stub::with(URL, bytes.to_vec()))
    }

    fn left_behind(p: &Paths) -> Vec<String> {
        let mut v: Vec<String> = fs::read_dir(p.icons())
            .map(|rd| rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect())
            .unwrap_or_default();
        v.extend(
            fs::read_dir(p.cache.join("pengyos-themes"))
                .map(|rd| rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect::<Vec<_>>())
                .unwrap_or_default(),
        );
        v.sort();
        v
    }

    #[test]
    fn install_use_remove() {
        let (_d, p, cat, stub) = setup(&pack_bytes());
        assert_eq!(install(&p, &cat, "ice", &stub).unwrap(), Installed::Fresh);
        assert_eq!(left_behind(&p), vec!["Ice", "Ice-Dark"], "no staging, no download left");
        assert!(p.icons().join("Ice-Dark/48x48/apps/a.svg").is_file());
        assert_eq!(installed(&p, cat.pack("ice").unwrap()).unwrap().version, "1");
        assert_eq!(install(&p, &cat, "ice", &stub).unwrap(), Installed::AlreadyThere);
        assert_eq!(stub.asked.borrow().len(), 1);

        use_theme(&p, "Ice-Dark").unwrap();
        assert_eq!(desktop::icon_theme(&p), "Ice-Dark");
        assert!(use_theme(&p, "Nope").is_err());
        assert!(use_theme(&p, "../Ice").is_err());

        assert!(remove(&p, &cat, "ice").unwrap(), "was active, so back to Cosmic");
        assert_eq!(desktop::icon_theme(&p), "Cosmic");
        assert_eq!(left_behind(&p), Vec::<String>::new());
        assert!(remove(&p, &cat, "ice").is_err());
        // Both changes are undoable.
        desktop::undo(&p).unwrap();
        assert_eq!(desktop::icon_theme(&p), "Ice-Dark");
    }

    #[test]
    fn a_wrong_checksum_leaves_nothing() {
        let (_d, p, cat, _) = setup(&pack_bytes());
        let mut tampered = pack_bytes();
        let n = tampered.len();
        tampered[n - 10] ^= 1;
        let e = install(&p, &cat, "ice", &Stub::with(URL, tampered)).unwrap_err();
        assert!(e.0.contains("sha256"), "{e}");
        assert_eq!(left_behind(&p), Vec::<String>::new());
    }

    #[test]
    fn hostile_archives_leave_nothing() {
        let hostile = [
            tar_gz(&[E::File("pack-1/Ice/index.theme", b"x"), E::File("pack-1/../../evil", b"x")]),
            tar_gz(&[E::File("pack-1/Ice/index.theme", b"x"), E::File("/tmp/evil", b"x")]),
            tar_gz(&[
                E::File("pack-1/Ice/index.theme", b"x"),
                E::Link("pack-1/Ice/l", "../../../../tmp"),
            ]),
            tar_gz(&[
                E::File("pack-1/Ice/index.theme", b"x"),
                E::Dir("pack-1/Ice/d/"),
                E::Link("pack-1/Ice/l", "d"),
                E::File("pack-1/Ice/l/x", b"x"),
            ]),
            // Missing a declared theme's index.theme.
            tar_gz(&[E::File("pack-1/Ice/index.theme", b"x")]),
        ];
        for bytes in hostile {
            let (_d, p, cat, stub) = setup(&bytes);
            assert!(install(&p, &cat, "ice", &stub).is_err());
            assert_eq!(left_behind(&p), Vec::<String>::new());
        }
    }

    #[test]
    fn someone_elses_theme_is_never_replaced_or_removed() {
        let (_d, p, cat, stub) = setup(&pack_bytes());
        fs::create_dir_all(p.icons().join("Ice")).unwrap();
        fs::write(p.icons().join("Ice/index.theme"), "mine").unwrap();
        assert!(install(&p, &cat, "ice", &stub).unwrap_err().0.contains("not installed by pengyos-themes"));
        assert!(remove(&p, &cat, "ice").is_err());
        assert_eq!(fs::read_to_string(p.icons().join("Ice/index.theme")).unwrap(), "mine");
    }

    #[test]
    fn a_new_catalogue_version_updates_in_place() {
        let (_d, p, cat, stub) = setup(&pack_bytes());
        install(&p, &cat, "ice", &stub).unwrap();
        let v2 = tar_gz(&[
            E::File("pack-1/Ice/index.theme", b"[Icon Theme]\nName=Ice\n"),
            E::File("pack-1/Ice/48x48/apps/new.svg", b"<svg/>"),
            E::File("pack-1/Ice-Dark/index.theme", b"[Icon Theme]\n"),
        ]);
        let (_e, _q, mut cat2, _) = setup(&v2);
        cat2.icons[0].version = "2".into();
        assert_eq!(
            install(&p, &cat2, "ice", &Stub::with(URL, v2)).unwrap(),
            Installed::Updated { from: "1".into() }
        );
        assert!(p.icons().join("Ice/48x48/apps/new.svg").is_file());
        assert!(!p.icons().join("Ice/48x48/apps/a.svg").exists());
        assert_eq!(left_behind(&p), vec!["Ice", "Ice-Dark"]);
    }

    #[test]
    fn offline_install_fails_cleanly() {
        let (_d, p, cat, _) = setup(&pack_bytes());
        let e = install(&p, &cat, "ice", &Stub::default()).unwrap_err();
        assert!(e.0.contains("no network"), "{e}");
        assert_eq!(left_behind(&p), Vec::<String>::new());
    }
}
