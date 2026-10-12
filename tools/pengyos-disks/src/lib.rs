//! Disks (`pengyos-disks`) — format, write images to and eject removable drives.
//!
//! This file is the decision logic, std only and `cargo test`-able with no display
//! and no D-Bus: which drives are offered (from a UDisks2 `GetManagedObjects`
//! snapshot, reduced to [`Objects`]), the filesystem choices and their name rules,
//! the UDisks2 calls each action makes ([`Step`]), and the image copy loop. The
//! zbus side ([`udisks`], feature `udisks`) only reads the snapshot and runs the
//! steps; the window ([`app`], feature `cosmic`) only shows them.

use std::collections::BTreeMap;
use std::io::{self, Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

#[cfg(feature = "cosmic")]
pub mod app;
#[cfg(feature = "udisks")]
pub mod udisks;

pub const APP_ID: &str = "io.catbee.pengyos.Disks";

/// One D-Bus property, reduced to the shapes UDisks2 uses for what we read.
#[derive(Clone, Debug, PartialEq)]
pub enum Prop {
    Str(String),
    Bool(bool),
    U64(u64),
    /// `ay`: UDisks' NUL-terminated paths (Block.Device).
    Bytes(Vec<u8>),
    /// `aay`: Filesystem.MountPoints.
    ByteArrays(Vec<Vec<u8>>),
    Other,
}

/// property name → value
pub type Props = BTreeMap<String, Prop>;
/// object path → interface (without the `org.freedesktop.UDisks2.` prefix) → properties
pub type Objects = BTreeMap<String, BTreeMap<String, Props>>;

fn bytes_str(b: &[u8]) -> String {
    let end = b.iter().position(|&c| c == 0).unwrap_or(b.len());
    String::from_utf8_lossy(&b[..end]).into_owned()
}

fn get_s(p: &Props, k: &str) -> String {
    match p.get(k) {
        Some(Prop::Str(s)) => s.clone(),
        Some(Prop::Bytes(b)) => bytes_str(b),
        _ => String::new(),
    }
}

fn get_b(p: &Props, k: &str) -> bool {
    matches!(p.get(k), Some(Prop::Bool(true)))
}

fn get_u(p: &Props, k: &str) -> u64 {
    match p.get(k) {
        Some(Prop::U64(n)) => *n,
        _ => 0,
    }
}

/// A filesystem (or partition, or encrypted container) on a drive.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Volume {
    /// The UDisks block object.
    pub object: String,
    pub device: String,
    pub size: u64,
    /// `IdType`: vfat, exfat, ntfs, ext4, crypto_LUKS, iso9660, …
    pub fs_type: String,
    pub label: String,
    pub mount_points: Vec<String>,
    pub swap_active: bool,
    /// An encrypted container that is unlocked (has a cleartext device).
    pub unlocked: bool,
    /// Partition number; 0 for a filesystem on the whole drive or a cleartext device.
    pub number: u64,
}

impl Volume {
    /// "KINGSTON (FAT, 31.9 GB)"
    pub fn describe(&self) -> String {
        if self.fs_type.is_empty() && self.label.is_empty() {
            return format!("Unformatted ({})", human(self.size));
        }
        let name = if self.label.is_empty() { "Untitled" } else { &self.label };
        format!("{name} ({}, {})", fs_name(&self.fs_type), human(self.size))
    }
}

/// What a filesystem type is called in the window.
pub fn fs_name(id_type: &str) -> &str {
    match id_type {
        "vfat" => "FAT",
        "exfat" => "exFAT",
        "ntfs" => "NTFS",
        "ext2" | "ext3" | "ext4" | "btrfs" | "xfs" | "f2fs" => id_type,
        "crypto_LUKS" => "encrypted",
        "iso9660" => "disk image",
        "swap" => "swap",
        "" => "empty",
        other => other,
    }
}

/// A removable drive (USB stick, SD card, external disk) with media in it.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Drive {
    /// The UDisks drive object.
    pub object: String,
    /// The whole-disk block object (what is formatted / written).
    pub block: String,
    pub device: String,
    pub vendor: String,
    pub model: String,
    pub size: u64,
    pub bus: String,
    pub read_only: bool,
    pub can_power_off: bool,
    pub ejectable: bool,
    /// The partition table type: dos, gpt, or empty for none.
    pub table: String,
    pub volumes: Vec<Volume>,
}

/// Why a drive cannot be formatted or written.
#[derive(Clone, Debug, PartialEq)]
pub enum Lock {
    /// Mounted by the system, not by the user (e.g. the stick this session runs from).
    InUse(String),
    Swap,
    ReadOnly,
}

impl Lock {
    pub fn text(&self) -> String {
        match self {
            Lock::InUse(at) => format!("In use by the system ({at})."),
            Lock::Swap => "In use by the system (swap).".into(),
            Lock::ReadOnly => "Write-protected. Check the lock switch on the side of the card.".into(),
        }
    }
}

/// Where UDisks mounts drives for the user; anything else is the system's.
fn user_mount(at: &str) -> bool {
    at.starts_with("/run/media/") || at.starts_with("/media/")
}

impl Drive {
    pub fn name(&self) -> String {
        let n = format!("{} {}", self.vendor.trim(), self.model.trim()).trim().to_string();
        if n.is_empty() {
            let dev = self.device.rsplit('/').next().unwrap_or("");
            format!("Drive {dev}").trim().to_string()
        } else {
            n
        }
    }

    pub fn lock(&self) -> Option<Lock> {
        for v in &self.volumes {
            if let Some(at) = v.mount_points.iter().find(|m| !user_mount(m)) {
                return Some(Lock::InUse(at.clone()));
            }
            if v.swap_active {
                return Some(Lock::Swap);
            }
        }
        self.read_only.then_some(Lock::ReadOnly)
    }

    /// What is on the drive, one entry per partition (or the whole-drive filesystem).
    pub fn contents(&self) -> Vec<String> {
        self.volumes.iter().filter(|v| v.number > 0 || self.table.is_empty()).map(Volume::describe).collect()
    }

    /// "USB · 32.0 GB · KINGSTON (FAT, 31.9 GB)"
    pub fn summary(&self) -> String {
        let bus = match self.bus.as_str() {
            "usb" => "USB",
            "sdio" | "mmc" => "Memory card",
            "" => "Removable",
            other => other,
        };
        let mut s = format!("{bus} · {}", human(self.size));
        let vols = self.contents();
        if vols.is_empty() {
            s.push_str(" · empty");
        } else {
            s.push_str(" · ");
            s.push_str(&vols.join(", "));
        }
        s
    }

    /// The confirmation text: "Everything on SanDisk Cruzer Blade (32.0 GB) will be
    /// erased: KINGSTON (FAT, 31.9 GB). This cannot be undone."
    pub fn erase_warning(&self) -> String {
        let vols = self.contents();
        let on = if vols.is_empty() { String::new() } else { format!(": {}", vols.join(", ")) };
        format!("Everything on {} ({}) will be erased{on}. This cannot be undone.", self.name(), human(self.size))
    }
}

/// The drives the app offers, from a UDisks2 snapshot: removable or USB/SD/MMC,
/// with media. Internal disks are not returned at all.
pub fn drives(objects: &Objects) -> Vec<Drive> {
    let mut out = Vec::new();
    for (path, ifaces) in objects {
        let Some(d) = ifaces.get("Drive") else { continue };
        let bus = get_s(d, "ConnectionBus");
        let removable = get_b(d, "Removable")
            || get_b(d, "MediaRemovable")
            || get_b(d, "Ejectable")
            || matches!(bus.as_str(), "usb" | "sdio" | "mmc");
        if !removable {
            continue;
        }
        // The whole-disk block: points at this drive and is not a partition.
        let Some((bpath, b)) = objects.iter().find_map(|(p, i)| {
            let b = i.get("Block")?;
            (get_s(b, "Drive") == *path && !i.contains_key("Partition")).then_some((p, b))
        }) else {
            continue;
        };
        let size = get_u(b, "Size");
        if size == 0 {
            continue; // an empty card reader
        }
        let table = objects[bpath].get("PartitionTable").map(|t| get_s(t, "Type")).unwrap_or_default();
        let mut volumes = Vec::new();
        for (p, i) in objects {
            let Some(vb) = i.get("Block") else { continue };
            let number = match i.get("Partition") {
                Some(part) if get_s(part, "Table") == *bpath => get_u(part, "Number"),
                Some(_) => continue,
                None if p == bpath && i.contains_key("Filesystem") => 0,
                None => continue,
            };
            volumes.push(volume(objects, p, i, vb, number));
        }
        // Cleartext devices of unlocked containers: their mounts count too.
        let backing: Vec<String> = volumes.iter().map(|v| v.object.clone()).collect();
        for (p, i) in objects {
            let Some(vb) = i.get("Block") else { continue };
            if backing.contains(&get_s(vb, "CryptoBackingDevice")) {
                volumes.push(volume(objects, p, i, vb, 0));
            }
        }
        out.push(Drive {
            object: path.clone(),
            block: bpath.clone(),
            device: Some(get_s(b, "PreferredDevice")).filter(|d| !d.is_empty()).unwrap_or_else(|| get_s(b, "Device")),
            vendor: get_s(d, "Vendor"),
            model: get_s(d, "Model"),
            size,
            bus,
            read_only: get_b(b, "ReadOnly"),
            can_power_off: get_b(d, "CanPowerOff"),
            ejectable: get_b(d, "Ejectable"),
            table,
            volumes,
        });
    }
    out
}

fn volume(objects: &Objects, path: &str, i: &BTreeMap<String, Props>, b: &Props, number: u64) -> Volume {
    let mount_points = match i.get("Filesystem").and_then(|f| f.get("MountPoints")) {
        Some(Prop::ByteArrays(m)) => m.iter().map(|x| bytes_str(x)).collect(),
        _ => Vec::new(),
    };
    let unlocked = i.contains_key("Encrypted")
        && objects.values().any(|o| o.get("Block").is_some_and(|c| get_s(c, "CryptoBackingDevice") == path));
    Volume {
        object: path.to_string(),
        device: get_s(b, "Device"),
        size: get_u(b, "Size"),
        fs_type: get_s(b, "IdType"),
        label: get_s(b, "IdLabel"),
        mount_points,
        swap_active: i.get("Swapspace").is_some_and(|s| get_b(s, "Active")),
        unlocked,
        number,
    }
}

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

// --- Format ------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Fs {
    Fat32,
    Exfat,
    Ntfs,
    Ext4,
}

const GPT_BASIC_DATA: &str = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7";
const GPT_LINUX: &str = "0fc63daf-8483-4772-8e79-3d69d8477de4";
/// MBR addresses at most 2 TiB with 512-byte sectors.
pub const MBR_MAX: u64 = 2 << 40;

impl Fs {
    pub const ALL: [Fs; 4] = [Fs::Fat32, Fs::Exfat, Fs::Ntfs, Fs::Ext4];

    pub fn title(self) -> &'static str {
        match self {
            Fs::Fat32 => "FAT32",
            Fs::Exfat => "exFAT",
            Fs::Ntfs => "NTFS",
            Fs::Ext4 => "ext4",
        }
    }

    pub fn about(self) -> &'static str {
        match self {
            Fs::Fat32 => "Works with every computer, TV, car stereo and camera. Files up to 4 GB.",
            Fs::Exfat => "Works with Windows, macOS, Linux and most cameras. No file size limit.",
            Fs::Ntfs => "For Windows. macOS can read it but not write to it.",
            Fs::Ext4 => "For Linux only. Keeps file permissions.",
        }
    }

    /// The UDisks format type (mkfs.<this>).
    pub fn mkfs(self) -> &'static str {
        match self {
            Fs::Fat32 => "vfat",
            Fs::Exfat => "exfat",
            Fs::Ntfs => "ntfs",
            Fs::Ext4 => "ext4",
        }
    }

    pub fn partition_type(self, table: &str) -> &'static str {
        match (self, table) {
            (Fs::Ext4, "gpt") => GPT_LINUX,
            (_, "gpt") => GPT_BASIC_DATA,
            (Fs::Fat32, _) => "0x0c",
            (Fs::Ext4, _) => "0x83",
            _ => "0x07",
        }
    }

    pub fn max_name(self) -> usize {
        match self {
            // libblockdev's checks (UDisks runs them only after the table is
            // rewritten, so a name it refuses would leave an empty drive): vfat
            // 11 bytes, exFAT 22 bytes of UTF-16, NTFS 128 bytes, ext 16 bytes.
            Fs::Fat32 => 11,
            Fs::Exfat => 11,
            Fs::Ntfs => 32,
            Fs::Ext4 => 16,
        }
    }

    /// Check a drive name, returning what will be written (FAT32: in capitals).
    pub fn check_name(self, name: &str) -> Result<String, String> {
        let name = name.trim();
        if name.chars().any(char::is_control) {
            return Err("The name cannot contain control characters.".into());
        }
        let (n, len) = match self {
            Fs::Fat32 => {
                if !name.is_ascii() {
                    return Err("FAT32 names can only use English letters, digits and simple punctuation.".into());
                }
                if let Some(c) = name.chars().find(|c| "\"*/:<>?\\|+,.;=[]".contains(*c)) {
                    return Err(format!("FAT32 names cannot contain “{c}”."));
                }
                (name.to_ascii_uppercase(), name.len())
            }
            Fs::Exfat | Fs::Ntfs => (name.to_string(), name.encode_utf16().count()),
            Fs::Ext4 => (name.to_string(), name.len()),
        };
        if len > self.max_name() {
            return Err(format!("{} names can be at most {} characters long.", self.title(), self.max_name()));
        }
        Ok(n)
    }
}

/// One UDisks2 call. `object` is always a UDisks object path.
#[derive(Clone, Debug, PartialEq)]
pub enum Step {
    /// Filesystem.Unmount
    Unmount { object: String },
    /// Encrypted.Lock
    Lock { object: String },
    /// Block.Format(table) on the whole drive: wipes it and writes an empty table.
    FormatTable { object: String, table: &'static str },
    /// PartitionTable.CreatePartitionAndFormat(1 MiB, 0 = the rest, ptype, "", {}, fs, {label, take-ownership})
    CreatePartition { object: String, ptype: &'static str, fs: &'static str, label: String, take_ownership: bool },
    /// Block.OpenForRestore, then the image is copied (see [`copy_image`]).
    Restore { object: String },
    /// Block.Rescan
    Rescan { object: String },
    /// Drive.PowerOff
    PowerOff { object: String },
    /// Drive.Eject
    Eject { object: String },
}

/// Unmount everything the user has mounted from the drive, then lock unlocked
/// containers (cleartext devices come after their containers in `volumes`, so
/// unmounts go first for all of them).
pub fn teardown(drive: &Drive) -> Vec<Step> {
    let mut steps: Vec<Step> = drive
        .volumes
        .iter()
        .filter(|v| !v.mount_points.is_empty())
        .map(|v| Step::Unmount { object: v.object.clone() })
        .collect();
    steps.extend(drive.volumes.iter().filter(|v| v.unlocked).map(|v| Step::Lock { object: v.object.clone() }));
    steps
}

pub fn table_for(size: u64) -> &'static str {
    if size > MBR_MAX { "gpt" } else { "dos" }
}

pub fn plan_format(drive: &Drive, fs: Fs, name: &str) -> Result<Vec<Step>, String> {
    if let Some(l) = drive.lock() {
        return Err(l.text());
    }
    let label = fs.check_name(name)?;
    let table = table_for(drive.size);
    let mut steps = teardown(drive);
    steps.push(Step::FormatTable { object: drive.block.clone(), table });
    steps.push(Step::CreatePartition {
        object: drive.block.clone(),
        ptype: fs.partition_type(table),
        fs: fs.mkfs(),
        label,
        take_ownership: fs == Fs::Ext4,
    });
    Ok(steps)
}

/// Teardown, restore (the copy), rescan. `image_size` is the decompressed size
/// when it is known up front.
pub fn plan_write(drive: &Drive, image_size: Option<u64>) -> Result<Vec<Step>, String> {
    if let Some(l) = drive.lock() {
        return Err(l.text());
    }
    if let Some(n) = image_size.filter(|&n| n > drive.size) {
        return Err(format!("The image ({}) is bigger than {} ({}).", human(n), drive.name(), human(drive.size)));
    }
    let mut steps = teardown(drive);
    steps.push(Step::Restore { object: drive.block.clone() });
    steps.push(Step::Rescan { object: drive.block.clone() });
    Ok(steps)
}

pub fn plan_eject(drive: &Drive) -> Vec<Step> {
    let mut steps = teardown(drive);
    if drive.can_power_off {
        steps.push(Step::PowerOff { object: drive.object.clone() });
    } else {
        steps.push(Step::Eject { object: drive.object.clone() });
    }
    steps
}

// --- Images ------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Compression {
    None,
    Xz,
    Zstd,
    Gzip,
}

impl Compression {
    pub fn of(path: &Path) -> Compression {
        match path.extension().and_then(|e| e.to_str()).map(str::to_ascii_lowercase).as_deref() {
            Some("xz") => Compression::Xz,
            Some("zst" | "zstd") => Compression::Zstd,
            Some("gz") => Compression::Gzip,
            _ => Compression::None,
        }
    }

    /// The decompressor to pipe the file through (stdin → stdout).
    pub fn command(self) -> Option<(&'static str, &'static [&'static str])> {
        match self {
            Compression::None => None,
            Compression::Xz => Some(("xz", &["-dc"])),
            Compression::Zstd => Some(("zstd", &["-dcq"])),
            Compression::Gzip => Some(("gzip", &["-dc"])),
        }
    }
}

/// The uncompressed size from `xz --robot --list FILE`.
pub fn parse_xz_list(out: &str) -> Option<u64> {
    out.lines().find_map(|l| {
        let f: Vec<&str> = l.split('\t').collect();
        (f.first() == Some(&"totals")).then(|| f.get(4)?.parse().ok())?
    })
}

/// The image's size once written, when it can be known up front: the file
/// itself, or what `xz --list` reads from an `.xz` index. Other compressed
/// images are only found to be too big while writing.
pub fn image_size(path: &Path) -> Option<u64> {
    match Compression::of(path) {
        Compression::None => std::fs::metadata(path).ok().map(|m| m.len()),
        Compression::Xz => {
            let out = std::process::Command::new("xz").args(["--robot", "--list"]).arg(path).output().ok()?;
            parse_xz_list(&String::from_utf8_lossy(&out.stdout))
        }
        Compression::Zstd | Compression::Gzip => None,
    }
}

/// The file chooser filter and the .desktop MIME types cover these.
pub const IMAGE_EXTENSIONS: [&str; 8] = ["img", "iso", "raw", "xz", "zst", "zstd", "gz", "bin"];

/// A reader that counts the bytes read through it into a shared counter (the
/// progress of a compressed image is the compressed bytes consumed).
pub struct CountingReader<R> {
    pub inner: R,
    pub count: Arc<AtomicU64>,
}

impl<R: Read> Read for CountingReader<R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let n = self.inner.read(buf)?;
        self.count.fetch_add(n as u64, Ordering::Relaxed);
        Ok(n)
    }
}

pub const BLOCK: usize = 4 << 20;
/// Flush to the device this often, so progress follows the stick, not the page cache.
pub const FLUSH_EVERY: u64 = 64 << 20;

#[derive(Debug)]
pub enum CopyError {
    Cancelled { written: u64 },
    /// The device ran out of room.
    Full { written: u64 },
    Read(io::Error),
    Write(io::Error),
}

impl CopyError {
    pub fn text(&self) -> String {
        match self {
            CopyError::Cancelled { .. } => {
                "Stopped. The drive now holds part of the image; format it to use it again.".into()
            }
            CopyError::Full { .. } => "The image is bigger than the drive.".into(),
            CopyError::Read(e) => format!("Could not read the image: {e}"),
            CopyError::Write(e) => format!("Could not write to the drive: {e}"),
        }
    }
}

fn full(e: &io::Error) -> bool {
    e.raw_os_error() == Some(28) || e.kind() == io::ErrorKind::WriteZero
}

/// Copy `src` to `dst` in [`BLOCK`]s, calling `flush` every [`FLUSH_EVERY`] bytes
/// and at the end, and `progress(written)` after each flush. Checks `cancel`
/// before each block.
pub fn copy_image<R: Read, W: Write>(
    mut src: R,
    dst: &mut W,
    mut flush: impl FnMut(&mut W) -> io::Result<()>,
    mut progress: impl FnMut(u64),
    cancel: &AtomicBool,
) -> Result<u64, CopyError> {
    let mut buf = vec![0u8; BLOCK];
    let mut written = 0u64;
    let mut since_flush = 0u64;
    loop {
        if cancel.load(Ordering::Relaxed) {
            let _ = flush(dst);
            return Err(CopyError::Cancelled { written });
        }
        // Fill the whole block (pipes return short reads).
        let mut n = 0;
        while n < buf.len() {
            match src.read(&mut buf[n..]) {
                Ok(0) => break,
                Ok(k) => n += k,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
                Err(e) => return Err(CopyError::Read(e)),
            }
        }
        if n == 0 {
            break;
        }
        match dst.write_all(&buf[..n]) {
            Ok(()) => {}
            Err(e) if full(&e) => return Err(CopyError::Full { written }),
            Err(e) => return Err(CopyError::Write(e)),
        }
        written += n as u64;
        since_flush += n as u64;
        if since_flush >= FLUSH_EVERY {
            flush(dst).map_err(|e| if full(&e) { CopyError::Full { written } } else { CopyError::Write(e) })?;
            since_flush = 0;
            progress(written);
        }
        if n < buf.len() {
            break;
        }
    }
    flush(dst).map_err(|e| if full(&e) { CopyError::Full { written } } else { CopyError::Write(e) })?;
    progress(written);
    Ok(written)
}

// --- Errors ------------------------------------------------------------------

/// What to tell the user about a failed UDisks call (`name` is the D-Bus error name).
pub fn explain(name: &str, message: &str) -> String {
    let short = name.rsplit('.').next().unwrap_or(name);
    match short {
        "NotAuthorizedDismissed" => "Cancelled.".into(),
        "NotAuthorized" | "NotAuthorizedCanObtain" => "Only an administrator of this computer can do that.".into(),
        "DeviceBusy" => busy(),
        _ if message.contains("target is busy") || message.contains("Device or resource busy") => busy(),
        _ => {
            // UDisks prefixes its messages with the call context:
            // "Error formatting /dev/sdb: Command-line `…' exited with non-zero exit status 1: …"
            let m = message.trim();
            if m.is_empty() { format!("The operation failed ({short}).") } else { m.to_string() }
        }
    }
}

fn busy() -> String {
    "The drive is in use. Close the files and apps that are using it, then try again.".into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    /// A tiny builder for UDisks snapshots.
    #[derive(Default)]
    struct Snap(Objects);

    fn s(v: &str) -> Prop {
        Prop::Str(v.into())
    }
    fn dev(v: &str) -> Prop {
        let mut b = v.as_bytes().to_vec();
        b.push(0);
        Prop::Bytes(b)
    }
    fn mounts(v: &[&str]) -> Prop {
        Prop::ByteArrays(v.iter().map(|m| [m.as_bytes(), b"\0"].concat()).collect())
    }

    impl Snap {
        fn add(mut self, path: &str, iface: &str, props: &[(&str, Prop)]) -> Self {
            let e = self.0.entry(path.into()).or_default().entry(iface.into()).or_default();
            for (k, v) in props {
                e.insert((*k).into(), v.clone());
            }
            self
        }
        fn drive(self, name: &str, bus: &str, removable: bool) -> Self {
            self.add(
                &format!("/org/freedesktop/UDisks2/drives/{name}"),
                "Drive",
                &[
                    ("Vendor", s("SanDisk")),
                    ("Model", s("Cruzer Blade")),
                    ("ConnectionBus", s(bus)),
                    ("Removable", Prop::Bool(removable)),
                    ("CanPowerOff", Prop::Bool(bus == "usb")),
                ],
            )
        }
        fn disk(self, blk: &str, drive: &str, size: u64, table: Option<&str>) -> Self {
            let p = format!("/org/freedesktop/UDisks2/block_devices/{blk}");
            let s2 = self.add(
                &p,
                "Block",
                &[
                    ("Device", dev(&format!("/dev/{blk}"))),
                    ("Drive", s(&format!("/org/freedesktop/UDisks2/drives/{drive}"))),
                    ("Size", Prop::U64(size)),
                ],
            );
            match table {
                Some(t) => s2.add(&p, "PartitionTable", &[("Type", s(t))]),
                None => s2,
            }
        }
        fn part(self, blk: &str, disk: &str, n: u64, fs: &str, label: &str, at: &[&str]) -> Self {
            let p = format!("/org/freedesktop/UDisks2/block_devices/{blk}");
            let drive = match &self.0[&format!("/org/freedesktop/UDisks2/block_devices/{disk}")]["Block"]["Drive"] {
                Prop::Str(d) => d.clone(),
                _ => unreachable!(),
            };
            self.add(
                &p,
                "Block",
                &[
                    ("Device", dev(&format!("/dev/{blk}"))),
                    ("Drive", s(&drive)),
                    ("Size", Prop::U64(31_900_000_000)),
                    ("IdType", s(fs)),
                    ("IdLabel", s(label)),
                ],
            )
            .add(
                &p,
                "Partition",
                &[("Table", s(&format!("/org/freedesktop/UDisks2/block_devices/{disk}"))), ("Number", Prop::U64(n))],
            )
            .add(&p, "Filesystem", &[("MountPoints", mounts(at))])
        }
    }

    fn stick(at: &[&str]) -> Objects {
        Snap::default()
            .drive("stick", "usb", true)
            .disk("sdb", "stick", 32_010_928_128, Some("dos"))
            .part("sdb1", "sdb", 1, "vfat", "KINGSTON", at)
            .0
    }

    #[test]
    fn a_usb_stick_is_offered_with_its_volume() {
        let d = drives(&stick(&[]));
        assert_eq!(d.len(), 1);
        let d = &d[0];
        assert_eq!(d.block, "/org/freedesktop/UDisks2/block_devices/sdb");
        assert_eq!(d.device, "/dev/sdb");
        assert_eq!(d.name(), "SanDisk Cruzer Blade");
        assert_eq!(d.table, "dos");
        assert_eq!(d.volumes.len(), 1);
        assert_eq!(d.lock(), None);
        assert_eq!(d.summary(), "USB · 32.0 GB · KINGSTON (FAT, 31.9 GB)");
        assert_eq!(
            d.erase_warning(),
            "Everything on SanDisk Cruzer Blade (32.0 GB) will be erased: KINGSTON (FAT, 31.9 GB). This cannot be undone."
        );
    }

    #[test]
    fn a_user_mount_does_not_lock_but_a_system_mount_does() {
        assert_eq!(drives(&stick(&["/run/media/pat/KINGSTON"]))[0].lock(), None);
        // The live installer stick this session runs from.
        assert_eq!(drives(&stick(&["/run/pengyos/payload"]))[0].lock(), Some(Lock::InUse("/run/pengyos/payload".into())));
        assert_eq!(drives(&stick(&["/"]))[0].lock(), Some(Lock::InUse("/".into())));
    }

    #[test]
    fn a_partition_without_a_filesystem_is_unformatted() {
        let d = &drives(&stick(&[]))[0];
        let mut v = d.volumes[0].clone();
        v.fs_type.clear();
        v.label.clear();
        assert_eq!(v.describe(), "Unformatted (31.9 GB)");
    }

    #[test]
    fn internal_disks_and_empty_readers_are_not_offered() {
        let o = Snap::default()
            .drive("nvme", "", false)
            .disk("nvme0n1", "nvme", 512_000_000_000, Some("gpt"))
            .drive("reader", "usb", true)
            .disk("sdc", "reader", 0, None)
            .0;
        assert!(drives(&o).is_empty());
    }

    #[test]
    fn sd_cards_count_even_when_not_flagged_removable() {
        let o = Snap::default().drive("sd", "sdio", false).disk("mmcblk0", "sd", 64_000_000_000, None).0;
        let d = drives(&o);
        assert_eq!(d.len(), 1);
        assert!(d[0].summary().starts_with("Memory card · 64.0 GB · empty"));
    }

    #[test]
    fn read_only_and_swap_lock() {
        let mut o = stick(&[]);
        o.get_mut("/org/freedesktop/UDisks2/block_devices/sdb").unwrap().get_mut("Block").unwrap().insert("ReadOnly".into(), Prop::Bool(true));
        assert_eq!(drives(&o)[0].lock(), Some(Lock::ReadOnly));
        let o = Snap(stick(&[])).add("/org/freedesktop/UDisks2/block_devices/sdb1", "Swapspace", &[("Active", Prop::Bool(true))]).0;
        assert_eq!(drives(&o)[0].lock(), Some(Lock::Swap));
        assert!(plan_format(&drives(&o)[0], Fs::Fat32, "").unwrap_err().contains("swap"));
    }

    #[test]
    fn a_whole_disk_filesystem_is_a_volume() {
        let p = "/org/freedesktop/UDisks2/block_devices/sdb";
        let o = Snap::default()
            .drive("stick", "usb", true)
            .disk("sdb", "stick", 8_000_000_000, None)
            .add(p, "Block", &[("IdType", s("iso9660")), ("IdLabel", s("UBUNTU"))])
            .add(p, "Filesystem", &[("MountPoints", mounts(&["/run/media/pat/UBUNTU"]))])
            .0;
        let d = &drives(&o)[0];
        assert_eq!(d.volumes.len(), 1);
        assert_eq!(d.volumes[0].number, 0);
        assert!(d.summary().ends_with("UBUNTU (disk image, 8.0 GB)"));
        assert_eq!(teardown(d), vec![Step::Unmount { object: p.into() }]);
    }

    #[test]
    fn unlocked_containers_are_unmounted_then_locked() {
        let luks = "/org/freedesktop/UDisks2/block_devices/sdb1";
        let clear = "/org/freedesktop/UDisks2/block_devices/dm_2d0";
        let o = Snap(stick(&[]))
            .add(luks, "Block", &[("IdType", s("crypto_LUKS"))])
            .add(luks, "Encrypted", &[])
            .add(clear, "Block", &[("Device", dev("/dev/dm-0")), ("CryptoBackingDevice", s(luks)), ("IdType", s("ext4"))])
            .add(clear, "Filesystem", &[("MountPoints", mounts(&["/run/media/pat/secret"]))])
            .0;
        let d = &drives(&o)[0];
        assert_eq!(d.lock(), None);
        assert_eq!(teardown(d), vec![Step::Unmount { object: clear.into() }, Step::Lock { object: luks.into() }]);
    }

    #[test]
    fn format_plans() {
        let d = &drives(&stick(&["/run/media/pat/KINGSTON"]))[0];
        let blk = d.block.clone();
        assert_eq!(
            plan_format(d, Fs::Fat32, "photos").unwrap(),
            vec![
                Step::Unmount { object: "/org/freedesktop/UDisks2/block_devices/sdb1".into() },
                Step::FormatTable { object: blk.clone(), table: "dos" },
                Step::CreatePartition { object: blk.clone(), ptype: "0x0c", fs: "vfat", label: "PHOTOS".into(), take_ownership: false },
            ]
        );
        let ext4 = plan_format(d, Fs::Ext4, "backup").unwrap();
        assert_eq!(ext4[2], Step::CreatePartition { object: blk.clone(), ptype: "0x83", fs: "ext4", label: "backup".into(), take_ownership: true });
        assert!(matches!(plan_format(d, Fs::Exfat, "").unwrap()[2], Step::CreatePartition { ptype: "0x07", fs: "exfat", .. }));
        assert!(matches!(plan_format(d, Fs::Ntfs, "").unwrap()[2], Step::CreatePartition { ptype: "0x07", fs: "ntfs", .. }));
        assert!(plan_format(d, Fs::Fat32, "far too long a name").is_err());
    }

    #[test]
    fn big_drives_get_gpt() {
        assert_eq!(table_for(MBR_MAX), "dos");
        assert_eq!(table_for(MBR_MAX + 1), "gpt");
        let mut d = drives(&stick(&[]))[0].clone();
        d.size = 4_000_000_000_000;
        let p = plan_format(&d, Fs::Exfat, "").unwrap();
        assert_eq!(p[0], Step::FormatTable { object: d.block.clone(), table: "gpt" });
        assert!(matches!(p[1], Step::CreatePartition { ptype: GPT_BASIC_DATA, .. }));
        assert!(matches!(plan_format(&d, Fs::Ext4, "").unwrap()[1], Step::CreatePartition { ptype: GPT_LINUX, .. }));
    }

    #[test]
    fn name_rules() {
        assert_eq!(Fs::Fat32.check_name("  Photos 2026 ").unwrap(), "PHOTOS 2026");
        assert_eq!(Fs::Fat32.check_name("").unwrap(), "");
        assert_eq!(Fs::Fat32.check_name("ABCDEFGHIJK").unwrap(), "ABCDEFGHIJK");
        assert!(Fs::Fat32.check_name("ABCDEFGHIJKL").is_err());
        assert!(Fs::Fat32.check_name("a/b").unwrap_err().contains('/'));
        assert!(Fs::Fat32.check_name("v1.2").is_err());
        assert!(Fs::Fat32.check_name("Café").is_err());
        assert_eq!(Fs::Exfat.check_name("Café Photos").unwrap(), "Café Photos");
        assert!(Fs::Exfat.check_name("Café Photos 26").is_err()); // 14 UTF-16 units > 11
        assert!(Fs::Exfat.check_name("😀😀😀😀😀😀").is_err()); // 12 units (surrogate pairs)
        assert_eq!(Fs::Ext4.check_name("0123456789abcdef").unwrap().len(), 16);
        assert!(Fs::Ext4.check_name("ééééééééé").is_err()); // 18 bytes
        assert!(Fs::Ntfs.check_name(&"x".repeat(33)).is_err());
        assert!(Fs::Ntfs.check_name("tab\there").is_err());
    }

    #[test]
    fn write_plan_checks_size_and_lock() {
        let d = &drives(&stick(&[]))[0];
        assert_eq!(
            plan_write(d, Some(4_000_000_000)).unwrap(),
            vec![Step::Restore { object: d.block.clone() }, Step::Rescan { object: d.block.clone() }]
        );
        assert!(plan_write(d, None).is_ok());
        let e = plan_write(d, Some(64_000_000_000)).unwrap_err();
        assert_eq!(e, "The image (64.0 GB) is bigger than SanDisk Cruzer Blade (32.0 GB).");
        assert!(plan_write(&drives(&stick(&["/run/pengyos/payload"]))[0], None).is_err());
    }

    #[test]
    fn eject_plan() {
        let d = &drives(&stick(&["/run/media/pat/KINGSTON"]))[0];
        assert_eq!(plan_eject(d)[1], Step::PowerOff { object: d.object.clone() });
        let mut d = d.clone();
        d.can_power_off = false;
        assert_eq!(plan_eject(&d)[1], Step::Eject { object: d.object.clone() });
    }

    #[test]
    fn sizes() {
        assert_eq!(human(999), "999 bytes");
        assert_eq!(human(1000), "1.0 kB");
        assert_eq!(human(31_914_983_424), "31.9 GB");
        assert_eq!(human(999_999_999), "1.0 GB");
        assert_eq!(human(2_000_398_934_016), "2.0 TB");
    }

    #[test]
    fn decompressors() {
        assert_eq!(Compression::of(Path::new("pengyos-desktop-26.10.24-x86_64.img.xz")), Compression::Xz);
        assert_eq!(Compression::of(Path::new("a.img.ZST")), Compression::Zstd);
        assert_eq!(Compression::of(Path::new("a.iso.gz")), Compression::Gzip);
        assert_eq!(Compression::of(Path::new("ubuntu.iso")), Compression::None);
        assert_eq!(Compression::Xz.command(), Some(("xz", &["-dc"][..])));
        assert_eq!(Compression::None.command(), None);
    }

    #[test]
    fn xz_list() {
        let out = "name\tp.img.xz\nfile\t1\t1\t812345678\t4294967296\t0.189\tCRC64\t0\ntotals\t1\t1\t812345678\t4294967296\t0.189\tCRC64\t0\t1\n";
        assert_eq!(parse_xz_list(out), Some(4_294_967_296));
        assert_eq!(parse_xz_list("garbage"), None);
    }

    /// A device of fixed size that records flushes.
    struct Dev {
        data: Vec<u8>,
        cap: usize,
        flushes: usize,
    }
    impl Write for Dev {
        fn write(&mut self, b: &[u8]) -> io::Result<usize> {
            let room = self.cap - self.data.len();
            if room == 0 {
                return Err(io::Error::from_raw_os_error(28));
            }
            let n = b.len().min(room);
            self.data.extend_from_slice(&b[..n]);
            Ok(n)
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn dev_of(cap: usize) -> Dev {
        Dev { data: Vec::new(), cap, flushes: 0 }
    }

    #[test]
    fn copy_writes_everything_and_flushes_on_cadence() {
        let len = (FLUSH_EVERY as usize) * 2 + 12345;
        let src: Vec<u8> = (0..len).map(|i| (i % 251) as u8).collect();
        let mut d = dev_of(len * 2);
        let mut seen = Vec::new();
        let n = copy_image(Cursor::new(&src), &mut d, |d| { d.flushes += 1; Ok(()) }, |w| seen.push(w), &AtomicBool::new(false)).unwrap();
        assert_eq!(n, len as u64);
        assert_eq!(d.data, src);
        assert_eq!(d.flushes, 3); // at 64 MiB, 128 MiB, and the end
        assert_eq!(seen, vec![FLUSH_EVERY, FLUSH_EVERY * 2, len as u64]);
    }

    #[test]
    fn copy_reports_a_full_device() {
        let src = vec![7u8; BLOCK * 2];
        let mut d = dev_of(BLOCK + 10);
        let e = copy_image(Cursor::new(&src), &mut d, |_| Ok(()), |_| {}, &AtomicBool::new(false)).unwrap_err();
        assert!(matches!(e, CopyError::Full { written } if written == BLOCK as u64));
        assert_eq!(e.text(), "The image is bigger than the drive.");
    }

    #[test]
    fn copy_stops_when_cancelled() {
        let cancel = AtomicBool::new(true);
        let mut d = dev_of(100);
        let e = copy_image(Cursor::new(vec![1u8; 10]), &mut d, |_| Ok(()), |_| {}, &cancel).unwrap_err();
        assert!(matches!(e, CopyError::Cancelled { written: 0 }));
        assert!(d.data.is_empty());
    }

    #[test]
    fn copy_fills_blocks_from_short_reads() {
        // A pipe hands out a few KiB at a time.
        struct Trickle(Vec<u8>, usize);
        impl Read for Trickle {
            fn read(&mut self, b: &mut [u8]) -> io::Result<usize> {
                let n = b.len().min(4096).min(self.0.len() - self.1);
                b[..n].copy_from_slice(&self.0[self.1..self.1 + n]);
                self.1 += n;
                Ok(n)
            }
        }
        let src: Vec<u8> = (0..BLOCK + 5000).map(|i| (i % 13) as u8).collect();
        let mut d = dev_of(src.len());
        let count = Arc::new(AtomicU64::new(0));
        let r = CountingReader { inner: Trickle(src.clone(), 0), count: count.clone() };
        copy_image(r, &mut d, |_| Ok(()), |_| {}, &AtomicBool::new(false)).unwrap();
        assert_eq!(d.data, src);
        assert_eq!(count.load(Ordering::Relaxed), src.len() as u64);
    }

    #[test]
    fn error_text() {
        assert_eq!(explain("org.freedesktop.UDisks2.Error.NotAuthorizedDismissed", "x"), "Cancelled.");
        assert_eq!(explain("org.freedesktop.UDisks2.Error.NotAuthorizedCanObtain", "x"), "Only an administrator of this computer can do that.");
        assert!(explain("org.freedesktop.UDisks2.Error.DeviceBusy", "").starts_with("The drive is in use"));
        assert!(explain("org.freedesktop.UDisks2.Error.Failed", "Error unmounting /dev/sdb1: target is busy").starts_with("The drive is in use"));
        assert_eq!(explain("org.freedesktop.UDisks2.Error.Failed", "Error formatting: boom"), "Error formatting: boom");
        assert_eq!(explain("org.freedesktop.UDisks2.Error.Failed", ""), "The operation failed (Failed).");
    }
}
