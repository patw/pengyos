//! Disk Usage (`pengyos-usage`) — what is taking the space on a drive.
//!
//! This file is everything but the window, std + libc only and `cargo test`-able
//! with no display: the scanner (a `du`-style walk into a [`Node`] tree), the
//! filesystems worth offering (from `/proc/self/mountinfo` and `statvfs`), and the
//! ring chart's geometry and hit testing. The window ([`app`], feature `cosmic`)
//! only draws what is here.
//!
//! The numbers are the disk's, not the files': disk usage (`st_blocks × 512`), a
//! hard-linked file counted once, symlinks never followed, other filesystems
//! mounted inside the scanned one not walked, and btrfs snapshots not counted
//! (they share their blocks with what they copy). All of it read-only.

use std::collections::HashSet;
use std::ffi::CString;
use std::fs;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;

#[cfg(feature = "cosmic")]
pub mod app;

pub const APP_ID: &str = "io.catbee.pengyos.DiskUsage";

// --- The tree ----------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Dir,
    File,
    /// The small entries of a directory past [`KEEP`], summed into one.
    Rest,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Node {
    pub name: String,
    /// Bytes on disk, this entry and everything below it.
    pub size: u64,
    pub kind: Kind,
    /// Files and folders, this one and everything below it.
    pub items: u64,
    /// Biggest first.
    pub children: Vec<Node>,
}

impl Node {
    pub fn leaf(name: impl Into<String>, size: u64, kind: Kind) -> Node {
        Node { name: name.into(), size, kind, items: 1, children: Vec::new() }
    }

    /// The node at `path` (child indices from this one).
    pub fn at(&self, path: &[usize]) -> Option<&Node> {
        path.iter().try_fold(self, |n, &i| n.children.get(i))
    }

    /// Recompute `size` and `items` from the children (folder's own blocks kept in `own`).
    fn add(&mut self, child: Node) {
        self.size += child.size;
        self.items += child.items;
        self.children.push(child);
    }
}

/// Entries kept by name in each folder; smaller ones are summed into one
/// "N smaller items" entry, so a folder of a million files costs one node.
pub const KEEP: usize = 100;

/// Sort biggest first (then by name, so the result is stable) and fold everything
/// past [`KEEP`] into one [`Kind::Rest`] node.
pub fn finish(children: &mut Vec<Node>) {
    children.sort_by(|a, b| b.size.cmp(&a.size).then_with(|| a.name.cmp(&b.name)));
    if children.len() > KEEP {
        let rest: Vec<Node> = children.split_off(KEEP);
        let n = rest.len();
        let size = rest.iter().map(|c| c.size).sum();
        let items = rest.iter().map(|c| c.items).sum();
        children.push(Node { name: format!("{n} smaller items"), size, kind: Kind::Rest, items, children: Vec::new() });
        children.sort_by(|a, b| b.size.cmp(&a.size).then_with(|| a.name.cmp(&b.name)));
    }
}

// --- The scan ----------------------------------------------------------------

/// Shared with the window while a scan runs.
#[derive(Default)]
pub struct Progress {
    pub items: AtomicU64,
    pub bytes: AtomicU64,
    pub cancel: AtomicBool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Scan {
    pub path: PathBuf,
    pub root: Node,
    /// Folders that could not be read (the first [`NOTE_MAX`]), and how many in all.
    pub unreadable: Vec<PathBuf>,
    pub unreadable_count: u64,
    /// Other filesystems mounted inside, not walked.
    pub mounts: Vec<PathBuf>,
    /// btrfs snapshot folders, not counted.
    pub snapshots: Vec<PathBuf>,
}

pub const NOTE_MAX: usize = 50;

#[derive(Default)]
struct Notes {
    unreadable: Vec<PathBuf>,
    unreadable_count: u64,
    mounts: Vec<PathBuf>,
    snapshots: Vec<PathBuf>,
}

struct Ctx<'a> {
    skip: &'a HashSet<PathBuf>,
    progress: &'a Progress,
    /// (device, inode) of every multiply-linked file already counted.
    seen: Mutex<HashSet<(u64, u64)>>,
    notes: Mutex<Notes>,
}

fn usage(m: &fs::Metadata) -> u64 {
    m.blocks() * 512
}

const BTRFS_SUPER_MAGIC: i64 = 0x9123_683E;

fn is_btrfs(path: &Path) -> bool {
    let Ok(c) = CString::new(path.as_os_str().as_bytes()) else { return false };
    let mut s: libc::statfs = unsafe { std::mem::zeroed() };
    // SAFETY: c is a valid NUL-terminated path and s a statfs to fill.
    (unsafe { libc::statfs(c.as_ptr(), &mut s) } == 0) && s.f_type as i64 == BTRFS_SUPER_MAGIC
}

impl Ctx<'_> {
    fn unreadable(&self, p: &Path) {
        let mut n = self.notes.lock().unwrap();
        n.unreadable_count += 1;
        if n.unreadable.len() < NOTE_MAX {
            n.unreadable.push(p.to_path_buf());
        }
    }

    /// A file's share: its blocks, or nothing if another link to it was counted.
    fn file_size(&self, m: &fs::Metadata) -> u64 {
        if m.nlink() > 1 && !self.seen.lock().unwrap().insert((m.dev(), m.ino())) {
            return 0;
        }
        usage(m)
    }

    /// Whether to walk into a folder; notes why not.
    fn enter(&self, p: &Path, name: &str) -> bool {
        if self.skip.contains(p) {
            self.notes.lock().unwrap().mounts.push(p.to_path_buf());
            return false;
        }
        if name == ".snapshots" && is_btrfs(p) {
            self.notes.lock().unwrap().snapshots.push(p.to_path_buf());
            return false;
        }
        true
    }

    fn walk(&self, path: &Path, name: String, own: u64) -> Node {
        let mut node = Node::leaf(name, own, Kind::Dir);
        self.progress.items.fetch_add(1, Ordering::Relaxed);
        self.progress.bytes.fetch_add(own, Ordering::Relaxed);
        let rd = match fs::read_dir(path) {
            Ok(r) => r,
            Err(_) => {
                self.unreadable(path);
                return node;
            }
        };
        for e in rd {
            if self.progress.cancel.load(Ordering::Relaxed) {
                break;
            }
            let Ok(e) = e else { continue };
            // DirEntry::metadata does not follow symlinks.
            let Ok(m) = e.metadata() else { continue };
            let name = e.file_name().to_string_lossy().into_owned();
            let p = e.path();
            if m.is_dir() {
                if self.enter(&p, &name) {
                    node.add(self.walk(&p, name, usage(&m)));
                }
            } else {
                let s = self.file_size(&m);
                self.progress.items.fetch_add(1, Ordering::Relaxed);
                self.progress.bytes.fetch_add(s, Ordering::Relaxed);
                node.add(Node::leaf(name, s, Kind::File));
            }
        }
        finish(&mut node.children);
        node
    }
}

/// Walk `path`, not entering the folders in `skip` (mount points). The folders
/// directly inside `path` are walked in parallel. Stops early, with an error, when
/// `progress.cancel` is set.
pub fn scan(path: &Path, skip: &HashSet<PathBuf>, progress: &Progress) -> Result<Scan, String> {
    let meta = fs::metadata(path).map_err(|e| format!("Cannot open {}: {e}", path.display()))?;
    if !meta.is_dir() {
        return Err(format!("{} is not a folder.", path.display()));
    }
    let ctx = Ctx { skip, progress, seen: Mutex::default(), notes: Mutex::default() };
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.display().to_string());
    let mut root = Node::leaf(name, usage(&meta), Kind::Dir);
    progress.items.fetch_add(1, Ordering::Relaxed);
    let rd = fs::read_dir(path).map_err(|e| format!("Cannot read {}: {e}", path.display()))?;
    let mut dirs: Vec<(PathBuf, String, u64)> = Vec::new();
    for e in rd.flatten() {
        let Ok(m) = e.metadata() else { continue };
        let name = e.file_name().to_string_lossy().into_owned();
        let p = e.path();
        if m.is_dir() {
            if ctx.enter(&p, &name) {
                dirs.push((p, name, usage(&m)));
            }
        } else {
            let s = ctx.file_size(&m);
            progress.items.fetch_add(1, Ordering::Relaxed);
            progress.bytes.fetch_add(s, Ordering::Relaxed);
            root.add(Node::leaf(name, s, Kind::File));
        }
    }
    let queue = Mutex::new(dirs);
    let done = Mutex::new(Vec::new());
    let threads = std::thread::available_parallelism().map_or(4, |n| n.get()).min(8);
    std::thread::scope(|s| {
        for _ in 0..threads {
            std::thread::Builder::new()
                // Deep trees recurse; give each walker room.
                .stack_size(64 << 20)
                .spawn_scoped(s, || loop {
                    let Some((p, name, own)) = queue.lock().unwrap().pop() else { break };
                    let n = ctx.walk(&p, name, own);
                    done.lock().unwrap().push(n);
                })
                .expect("spawn a scan thread");
        }
    });
    for n in done.into_inner().unwrap() {
        root.add(n);
    }
    finish(&mut root.children);
    if progress.cancel.load(Ordering::Relaxed) {
        return Err("Stopped.".into());
    }
    let notes = ctx.notes.into_inner().unwrap();
    let mut mounts = notes.mounts;
    mounts.sort();
    let mut snapshots = notes.snapshots;
    snapshots.sort();
    Ok(Scan {
        path: path.to_path_buf(),
        root,
        unreadable: notes.unreadable,
        unreadable_count: notes.unreadable_count,
        mounts,
        snapshots,
    })
}

// --- Filesystems -------------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Mount {
    /// major:minor
    pub dev: String,
    /// The path inside the filesystem that is mounted here ("/" for the top).
    pub root: String,
    pub point: PathBuf,
    pub fstype: String,
    pub source: String,
}

/// mountinfo escapes space, tab, newline and backslash as `\ooo`.
fn unescape(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\\' && i + 3 < b.len() && b[i + 1..i + 4].iter().all(|c| (b'0'..=b'7').contains(c)) {
            out.push((b[i + 1] - b'0') * 64 + (b[i + 2] - b'0') * 8 + (b[i + 3] - b'0'));
            i += 4;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `/proc/self/mountinfo`, one [`Mount`] per line.
pub fn parse_mountinfo(text: &str) -> Vec<Mount> {
    text.lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.split(' ').collect();
            let dash = f.iter().position(|x| *x == "-")?;
            Some(Mount {
                dev: f.get(2)?.to_string(),
                root: unescape(f.get(3)?),
                point: PathBuf::from(unescape(f.get(4)?)),
                fstype: f.get(dash + 1)?.to_string(),
                source: unescape(f.get(dash + 2).unwrap_or(&"")),
            })
        })
        .collect()
}

pub fn mounts() -> Vec<Mount> {
    fs::read_to_string("/proc/self/mountinfo").map(|t| parse_mountinfo(&t)).unwrap_or_default()
}

/// Every mount point strictly inside `root`: the scan does not cross into them.
pub fn mounts_inside(mounts: &[Mount], root: &Path) -> HashSet<PathBuf> {
    mounts.iter().filter(|m| m.point != root && m.point.starts_with(root)).map(|m| m.point.clone()).collect()
}

/// The mount points of filesystems that hold files (not /proc, /sys, tmpfs, …):
/// the ones worth naming when a scan leaves them out.
pub fn real_mount_points(mounts: &[Mount]) -> HashSet<PathBuf> {
    mounts.iter().filter(|m| REAL.contains(&m.fstype.as_str())).map(|m| m.point.clone()).collect()
}

/// Filesystems that hold files people make.
const REAL: [&str; 12] = ["ext2", "ext3", "ext4", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "f2fs", "bcachefs"];

/// The filesystems worth offering, each once: a filesystem mounted in several
/// places (PengyOS binds `/data`'s subvolumes on `/home`, `/var`, `/root`) is
/// listed at its top mount, else at its shortest path.
pub fn filesystems(mounts: &[Mount]) -> Vec<Mount> {
    let hidden = |p: &Path| {
        ["/proc", "/sys", "/dev", "/snap"].iter().any(|h| p.starts_with(h))
            || (p.starts_with("/run") && !p.starts_with("/run/media"))
    };
    let mut out: Vec<Mount> = Vec::new();
    for m in mounts.iter().filter(|m| REAL.contains(&m.fstype.as_str()) && !hidden(&m.point)) {
        let better = |o: &Mount| {
            (m.root == "/", std::cmp::Reverse(m.point.as_os_str().len()))
                > (o.root == "/", std::cmp::Reverse(o.point.as_os_str().len()))
        };
        match out.iter_mut().find(|o| o.dev == m.dev) {
            Some(o) if better(o) => *o = m.clone(),
            Some(_) => {}
            None => out.push(m.clone()),
        }
    }
    out.sort_by(|a, b| a.point.cmp(&b.point));
    out
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Space {
    pub total: u64,
    pub used: u64,
    /// What an ordinary user can still write.
    pub free: u64,
}

pub fn space(path: &Path) -> Option<Space> {
    let c = CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut s: libc::statvfs = unsafe { std::mem::zeroed() };
    // SAFETY: c is a valid NUL-terminated path and s a statvfs to fill.
    if unsafe { libc::statvfs(c.as_ptr(), &mut s) } != 0 {
        return None;
    }
    let f = s.f_frsize as u64;
    Some(Space {
        total: s.f_blocks as u64 * f,
        used: (s.f_blocks as u64).saturating_sub(s.f_bfree as u64) * f,
        free: s.f_bavail as u64 * f,
    })
}

/// What to call a filesystem in the window.
pub fn place_name(m: &Mount, home: &Path) -> String {
    if m.point == Path::new("/") {
        "This computer's system".into()
    } else if home.starts_with(&m.point) && m.point != Path::new("/") {
        format!("{} (your files)", m.point.display())
    } else if let Some(n) = m.point.strip_prefix("/run/media").ok().and_then(|p| p.iter().nth(1)) {
        n.to_string_lossy().into_owned()
    } else {
        m.point.display().to_string()
    }
}

// --- Sizes -------------------------------------------------------------------

/// Decimal sizes, as drives are sold: "32.0 GB".
pub fn human(bytes: u64) -> String {
    const UNITS: [&str; 5] = ["kB", "MB", "GB", "TB", "PB"];
    if bytes < 1000 {
        return format!("{bytes} bytes");
    }
    let mut v = bytes as f64 / 1000.0;
    let mut u = 0;
    while v >= 999.95 && u < UNITS.len() - 1 {
        v /= 1000.0;
        u += 1;
    }
    format!("{v:.1} {}", UNITS[u])
}

/// "12,427,594"
pub fn count(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// "12.3%", or "<0.1%" for something that is there but tiny.
pub fn percent(part: u64, whole: u64) -> String {
    if whole == 0 || part == 0 {
        return "0%".into();
    }
    let p = part as f64 * 100.0 / whole as f64;
    if p < 0.1 { "<0.1%".into() } else { format!("{p:.1}%") }
}

// --- The ring chart ----------------------------------------------------------

/// Rings drawn around the centre.
pub const RINGS: usize = 4;
/// The centre circle's radius, as a fraction of the chart's.
pub const CENTRE: f64 = 0.28;
/// Slices thinner than this (a fraction of a turn) are not drawn.
pub const MIN_TURN: f64 = 0.003;

#[derive(Clone, Debug, PartialEq)]
pub struct Segment {
    /// Child indices from the node at the centre.
    pub path: Vec<usize>,
    /// 1 = the first ring.
    pub depth: usize,
    /// Fractions of a turn, clockwise from twelve o'clock.
    pub start: f64,
    pub end: f64,
    pub kind: Kind,
}

/// The slices around `centre`: its children in the first ring, theirs in the
/// second, and so on, each in proportion to `centre`'s size.
pub fn segments(centre: &Node) -> Vec<Segment> {
    fn go(n: &Node, total: f64, start: f64, depth: usize, path: &mut Vec<usize>, out: &mut Vec<Segment>) {
        let mut at = start;
        for (i, c) in n.children.iter().enumerate() {
            let turn = c.size as f64 / total;
            if turn >= MIN_TURN {
                path.push(i);
                out.push(Segment { path: path.clone(), depth, start: at, end: at + turn, kind: c.kind });
                if depth < RINGS && c.kind == Kind::Dir {
                    go(c, total, at, depth + 1, path, out);
                }
                path.pop();
            }
            at += turn;
        }
    }
    let mut out = Vec::new();
    if centre.size > 0 {
        go(centre, centre.size as f64, 0.0, 1, &mut Vec::new(), &mut out);
    }
    out
}

/// The inner and outer radius of ring `depth` (1-based), as fractions of the chart's.
pub fn ring_radii(depth: usize) -> (f64, f64) {
    let w = (1.0 - CENTRE) / RINGS as f64;
    (CENTRE + w * (depth - 1) as f64, CENTRE + w * depth as f64)
}

/// What is under a point `(dx, dy)` from the centre (y down, as on screen) of a
/// chart of radius `r`: `Some(None)` for the centre circle, `Some(Some(i))` for
/// `segments[i]`, `None` for empty space.
pub fn hit(segments: &[Segment], dx: f64, dy: f64, r: f64) -> Option<Option<usize>> {
    let d = (dx * dx + dy * dy).sqrt() / r;
    if d <= CENTRE {
        return Some(None);
    }
    if d > 1.0 {
        return None;
    }
    let depth = ((d - CENTRE) / ((1.0 - CENTRE) / RINGS as f64)).floor() as usize + 1;
    // atan2 from twelve o'clock, clockwise: x is the sine, -y the cosine.
    let turn = (dx.atan2(-dy) / std::f64::consts::TAU).rem_euclid(1.0);
    segments.iter().position(|s| s.depth == depth.min(RINGS) && s.start <= turn && turn < s.end).map(Some)
}

/// A slice's colour: one hue per slice of the first ring, lighter outwards;
/// files a little greyer than folders; the summed rest grey.
pub fn colour(top: usize, depth: usize, kind: Kind) -> [f32; 3] {
    const HUES: [[f32; 3]; 8] = [
        [0.20, 0.52, 0.89],
        [0.95, 0.55, 0.15],
        [0.30, 0.69, 0.31],
        [0.86, 0.26, 0.36],
        [0.58, 0.40, 0.78],
        [0.10, 0.70, 0.70],
        [0.85, 0.70, 0.10],
        [0.75, 0.40, 0.60],
    ];
    if kind == Kind::Rest {
        return [0.55, 0.55, 0.58];
    }
    let base = HUES[top % HUES.len()];
    let light = 0.16 * (depth.saturating_sub(1)) as f32;
    let grey = if kind == Kind::File { 0.35 } else { 0.0 };
    base.map(|c| {
        let c = c + (0.62 - c) * grey;
        c + (1.0 - c) * light
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    /// A fresh folder under the temp dir, removed when dropped.
    struct Tree(PathBuf);
    impl Tree {
        fn new() -> Tree {
            static N: AtomicUsize = AtomicUsize::new(0);
            let p = std::env::temp_dir()
                .join(format!("pengyos-usage-test-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
            fs::create_dir_all(&p).unwrap();
            Tree(p)
        }
        fn file(&self, rel: &str, bytes: usize) -> PathBuf {
            let p = self.0.join(rel);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            fs::write(&p, vec![7u8; bytes]).unwrap();
            p
        }
        fn du(&self, rel: &str) -> u64 {
            usage(&fs::symlink_metadata(self.0.join(rel)).unwrap())
        }
    }
    impl Drop for Tree {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn run(t: &Tree) -> Scan {
        scan(&t.0, &HashSet::new(), &Progress::default()).unwrap()
    }

    #[test]
    fn sizes_add_up_biggest_first() {
        let t = Tree::new();
        t.file("big.iso", 300_000);
        t.file("photos/a.jpg", 100_000);
        t.file("photos/2026/b.jpg", 150_000);
        t.file("notes.txt", 10);
        let s = run(&t);
        let r = &s.root;
        let names: Vec<&str> = r.children.iter().map(|c| c.name.as_str()).collect();
        assert_eq!(names, ["big.iso", "photos", "notes.txt"]);
        let photos = &r.children[1];
        assert_eq!(photos.kind, Kind::Dir);
        assert_eq!(photos.size, t.du("photos") + t.du("photos/a.jpg") + t.du("photos/2026") + t.du("photos/2026/b.jpg"));
        assert_eq!(r.size, t.du("") + t.du("big.iso") + photos.size + t.du("notes.txt"));
        assert_eq!(r.items, 7, "the root, big.iso, photos, a.jpg, 2026, b.jpg, notes.txt");
    }

    #[test]
    fn hard_links_count_once_and_symlinks_are_not_followed() {
        let t = Tree::new();
        let f = t.file("a/data.bin", 200_000);
        fs::create_dir_all(t.0.join("b")).unwrap();
        fs::hard_link(&f, t.0.join("b/same.bin")).unwrap();
        std::os::unix::fs::symlink(t.0.join("a"), t.0.join("link-to-a")).unwrap();
        let r = run(&t).root;
        let total_files: u64 = r.children.iter().filter(|c| c.name != "link-to-a").map(|c| c.size).sum();
        assert_eq!(total_files, t.du("a") + t.du("b") + t.du("a/data.bin"), "the second link adds nothing");
        let link = r.children.iter().find(|c| c.name == "link-to-a").unwrap();
        assert_eq!(link.kind, Kind::File);
        assert!(link.size < 200_000, "the link's own blocks, not its target's");
    }

    #[test]
    fn skipped_mount_points_are_noted_not_walked() {
        let t = Tree::new();
        t.file("home/pat/x", 50_000);
        t.file("data/huge", 400_000);
        let skip: HashSet<PathBuf> = [t.0.join("data")].into();
        let s = scan(&t.0, &skip, &Progress::default()).unwrap();
        assert!(s.root.children.iter().all(|c| c.name != "data"));
        assert_eq!(s.mounts, vec![t.0.join("data")]);
    }

    #[test]
    fn unreadable_folders_are_counted_and_the_scan_goes_on() {
        if unsafe { libc::geteuid() } == 0 {
            return; // root reads everything
        }
        let t = Tree::new();
        t.file("open/x", 1000);
        t.file("closed/y", 1000);
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(t.0.join("closed"), fs::Permissions::from_mode(0o000)).unwrap();
        let s = run(&t);
        fs::set_permissions(t.0.join("closed"), fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(s.unreadable, vec![t.0.join("closed")]);
        assert_eq!(s.unreadable_count, 1);
        assert!(s.root.children.iter().any(|c| c.name == "open"));
    }

    #[test]
    fn a_cancelled_scan_says_so() {
        let t = Tree::new();
        t.file("a/b", 10);
        let p = Progress::default();
        p.cancel.store(true, Ordering::Relaxed);
        assert_eq!(scan(&t.0, &HashSet::new(), &p), Err("Stopped.".into()));
        assert!(scan(&t.0.join("a/b"), &HashSet::new(), &Progress::default()).unwrap_err().contains("not a folder"));
    }

    #[test]
    fn a_crowded_folder_keeps_the_biggest_and_sums_the_rest() {
        let mut v: Vec<Node> = (0..250u64).map(|i| Node::leaf(format!("f{i}"), i * 10, Kind::File)).collect();
        finish(&mut v);
        assert_eq!(v.len(), KEEP + 1);
        // Together the 150 small ones outweigh any single file, so they sort first.
        assert_eq!(v[0].kind, Kind::Rest);
        assert_eq!(v[1].name, "f249");
        let rest = v.iter().find(|n| n.kind == Kind::Rest).unwrap();
        assert_eq!(rest.name, "150 smaller items");
        assert_eq!(rest.size, (0..150u64).map(|i| i * 10).sum::<u64>());
        assert_eq!(rest.items, 150);
        assert!(v.windows(2).all(|w| w[0].size >= w[1].size));
    }

    const MOUNTINFO: &str = "\
22 1 259:4 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw
23 22 0:21 / /proc rw - proc proc rw
24 22 259:3 / /boot/efi rw - vfat /dev/nvme0n1p1 rw
30 22 259:5 / /data rw - btrfs /dev/nvme0n1p4 rw
31 22 259:5 /home /home rw - btrfs /dev/nvme0n1p4 rw
32 22 259:5 /var /var rw - btrfs /dev/nvme0n1p4 rw
40 22 8:17 / /run/media/pat/My\\040Stick rw - exfat /dev/sdb1 rw
41 22 0:50 / /run/user/1000 rw - tmpfs tmpfs rw
42 22 7:3 / /snap/core/1 ro - squashfs /dev/loop3 ro
";

    #[test]
    fn mountinfo_parses_and_unescapes() {
        let m = parse_mountinfo(MOUNTINFO);
        assert_eq!(m.len(), 9);
        assert_eq!(m[6].point, PathBuf::from("/run/media/pat/My Stick"));
        assert_eq!(m[4].root, "/home");
        assert_eq!(m[3].fstype, "btrfs");
    }

    #[test]
    fn filesystems_are_listed_once_at_their_top_mount() {
        let m = parse_mountinfo(MOUNTINFO);
        let f = filesystems(&m);
        let points: Vec<&Path> = f.iter().map(|m| m.point.as_path()).collect();
        assert_eq!(points, [Path::new("/"), Path::new("/boot/efi"), Path::new("/data"), Path::new("/run/media/pat/My Stick")]);
        let home = Path::new("/home/pat");
        assert_eq!(place_name(&f[0], home), "This computer's system");
        assert_eq!(place_name(&f[3], home), "My Stick");
    }

    #[test]
    fn a_scan_of_root_does_not_cross_into_other_mounts() {
        let m = parse_mountinfo(MOUNTINFO);
        let skip = mounts_inside(&m, Path::new("/"));
        assert!(skip.contains(Path::new("/data")) && skip.contains(Path::new("/proc")) && skip.contains(Path::new("/home")));
        assert!(!skip.contains(Path::new("/")));
        let skip = mounts_inside(&m, Path::new("/home/pat"));
        assert!(skip.is_empty());
        let real = real_mount_points(&m);
        assert!(real.contains(Path::new("/data")) && !real.contains(Path::new("/proc")));
    }

    fn sample() -> Node {
        let mut docs = Node::leaf("docs", 0, Kind::Dir);
        docs.add(Node::leaf("a", 300, Kind::File));
        docs.add(Node::leaf("b", 100, Kind::File));
        let mut root = Node::leaf("home", 0, Kind::Dir);
        root.add(docs);
        root.add(Node::leaf("video", 500, Kind::File));
        root.add(Node::leaf("tiny", 1, Kind::File));
        finish(&mut root.children);
        root
    }

    #[test]
    fn segments_follow_sizes_and_nest() {
        let r = sample();
        assert_eq!(r.size, 901);
        let s = segments(&r);
        // video, docs, and docs' a and b; "tiny" is under MIN_TURN.
        assert_eq!(s.len(), 4);
        assert_eq!(s[0].path, vec![0]);
        assert!((s[0].end - 500.0 / 901.0).abs() < 1e-9);
        let docs = &s[1];
        assert_eq!((docs.depth, docs.path.clone()), (1, vec![1]));
        let a = &s[2];
        assert_eq!((a.depth, a.path.clone()), (2, vec![1, 0]));
        assert!((a.start - docs.start).abs() < 1e-12, "children start where their parent does");
        assert!(s.iter().filter(|x| x.depth == 2).all(|x| x.end <= docs.end + 1e-12));
        assert_eq!(r.at(&[1, 0]).unwrap().name, "a");
    }

    #[test]
    fn hits_land_on_the_slice_under_the_pointer() {
        let r = sample();
        let s = segments(&r);
        let (i1, o1) = ring_radii(1);
        let mid1 = (i1 + o1) / 2.0 * 100.0;
        // Straight up, first ring: the start of "video".
        assert_eq!(hit(&s, 0.0, -mid1, 100.0), Some(Some(0)));
        // Straight down is half a turn: video spans 0..0.555, so still video.
        assert_eq!(hit(&s, 0.0, mid1, 100.0), Some(Some(0)));
        // Left (three quarters of a turn): docs.
        assert_eq!(hit(&s, -mid1, 0.0, 100.0), Some(Some(1)));
        // Second ring, left: inside docs, "a" (docs starts at 0.555, a covers 0.555..0.888).
        let (i2, o2) = ring_radii(2);
        assert_eq!(hit(&s, -(i2 + o2) / 2.0 * 100.0, 0.0, 100.0), Some(Some(2)));
        // Second ring, straight up: video has no children there.
        assert_eq!(hit(&s, 0.0, -(i2 + o2) / 2.0 * 100.0, 100.0), None);
        assert_eq!(hit(&s, 1.0, 1.0, 100.0), Some(None), "the centre");
        assert_eq!(hit(&s, 0.0, -150.0, 100.0), None, "outside");
    }

    #[test]
    fn words() {
        assert_eq!(human(999), "999 bytes");
        assert_eq!(human(31_914_983_424), "31.9 GB");
        assert_eq!(percent(1, 2), "50.0%");
        assert_eq!(count(12_427_594), "12,427,594");
        assert_eq!(count(999), "999");
        assert_eq!(count(1000), "1,000");
        assert_eq!(percent(1, 100_000), "<0.1%");
        assert_eq!(percent(0, 5), "0%");
        assert_eq!(colour(0, 1, Kind::Rest), [0.55, 0.55, 0.58]);
        assert!(colour(1, 3, Kind::Dir)[0] > colour(1, 1, Kind::Dir)[0], "lighter outwards");
    }
}
