//! Disks (`pengyos-disks`) — see a drive and its partitions, partition and format
//! it, mount it; and format, write images to and eject removable drives.
//!
//! This file is the decision logic, std only and `cargo test`-able with no display
//! and no D-Bus: which drives there are (from a UDisks2 `GetManagedObjects`
//! snapshot, reduced to [`Objects`]), how each is laid out (partitions and free
//! space), what is locked and why, the filesystem choices and their name rules, the
//! UDisks2 calls each action makes ([`Step`]), and the image copy loop. The zbus
//! side ([`udisks`], feature `udisks`) only reads the snapshot and runs the steps;
//! the window ([`app`], feature `cosmic`) only shows them.

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

/// An object path property; UDisks says "none" with `/`.
fn get_o(p: &Props, k: &str) -> String {
    let s = get_s(p, k);
    if s == "/" { String::new() } else { s }
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

pub const MIB: u64 = 1 << 20;

fn align_up(n: u64) -> u64 {
    n.div_ceil(MIB) * MIB
}

fn align_down(n: u64) -> u64 {
    n / MIB * MIB
}

/// A partition, a filesystem on a whole drive, or the cleartext side of an
/// unlocked encrypted container.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Volume {
    /// The UDisks block object.
    pub object: String,
    pub device: String,
    pub size: u64,
    /// `IdType`: vfat, exfat, ntfs, ext4, crypto_LUKS, iso9660, swap, …
    pub fs_type: String,
    pub label: String,
    pub mount_points: Vec<String>,
    pub swap_active: bool,
    /// An encrypted container that is unlocked (has a cleartext device).
    pub unlocked: bool,
    pub read_only: bool,
    /// Partition number; 0 for a filesystem on the whole drive or a cleartext device.
    pub number: u64,
    /// `Partition.Offset`, bytes from the start of the drive.
    pub offset: u64,
    /// `Partition.Type`: a GUID on GPT, `0x83` and the like on MBR.
    pub part_type: String,
    /// `Partition.Name`: the GPT partition name (the installer names its partitions).
    pub part_name: String,
    /// An MBR extended partition, and the logical partitions inside one.
    pub container: bool,
    pub contained: bool,
    /// For a cleartext device: the encrypted container it is the inside of.
    pub backing: String,
}

impl Volume {
    /// "KINGSTON (FAT, 31.9 GB)"
    pub fn describe(&self) -> String {
        if self.container {
            return format!("Extended partition ({})", human(self.size));
        }
        if self.fs_type.is_empty() && self.label.is_empty() {
            return format!("Unformatted ({})", human(self.size));
        }
        format!("{} ({}, {})", self.title(), fs_name(&self.fs_type), human(self.size))
    }

    /// The name to show: the filesystem's label, else the GPT name, else "Untitled".
    pub fn title(&self) -> String {
        if self.container {
            return "Extended partition".into();
        }
        [&self.label, &self.part_name]
            .into_iter()
            .find(|s| !s.is_empty())
            .cloned()
            .unwrap_or_else(|| match self.fs_type.as_str() {
                "" => "Unformatted".into(),
                "swap" => "Swap".into(),
                "crypto_LUKS" => "Encrypted".into(),
                _ if self.number > 0 => format!("Partition {}", self.number),
                _ => "Untitled".into(),
            })
    }

    pub fn end(&self) -> u64 {
        self.offset + self.size
    }

    /// A filesystem UDisks can mount.
    pub fn mountable(&self) -> bool {
        !matches!(self.fs_type.as_str(), "" | "swap" | "crypto_LUKS") && !self.container
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

/// A drive with media in it: an internal disk, an external disk, a USB stick, a
/// memory card.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Drive {
    /// The UDisks drive object.
    pub object: String,
    /// The whole-disk block object (what is formatted / written / partitioned).
    pub block: String,
    pub device: String,
    pub vendor: String,
    pub model: String,
    pub size: u64,
    pub bus: String,
    /// Removable media or on USB/SD/MMC: a stick, a card, an external disk.
    pub removable: bool,
    pub read_only: bool,
    pub can_power_off: bool,
    pub ejectable: bool,
    /// The partition table type: dos, gpt, or empty for none.
    pub table: String,
    /// Partitions (by offset), a whole-drive filesystem (number 0), then the
    /// cleartext devices of unlocked containers.
    pub volumes: Vec<Volume>,
}

/// Why a drive or a partition cannot be changed.
#[derive(Clone, Debug, PartialEq)]
pub enum Lock {
    /// Mounted by the system, not by the user (e.g. the stick this session runs from).
    InUse(String),
    Swap,
    ReadOnly,
    /// One of the running PengyOS system's own partitions (its name).
    System(String),
}

impl Lock {
    pub fn text(&self) -> String {
        match self {
            Lock::InUse(at) => format!("In use by the system ({at})."),
            Lock::Swap => "In use by the system (swap).".into(),
            Lock::ReadOnly => "Write-protected. Check the lock switch on the side of the card.".into(),
            Lock::System(name) => format!("Part of this PengyOS system ({name})."),
        }
    }
}

/// Where UDisks mounts drives for the user; anything else is the system's.
fn user_mount(at: &str) -> bool {
    at.starts_with("/run/media/") || at.starts_with("/media/")
}

/// The GPT names the installer gives PengyOS's partitions, and the filesystem
/// labels it (and the installer stick) writes. On the drive the system runs from
/// these are locked even when not mounted: the inactive root slot never is.
const SYSTEM_PART_NAMES: [&str; 4] = ["PENGYOS-ESP", "pengyos-root-a", "pengyos-root-b", "pengyos-data"];
const SYSTEM_LABELS: [&str; 5] = ["PENGYOS-ESP", "pengyos-root-a", "pengyos-root-b", "pengyos-data", "PENGYOS-PAYLOAD"];
/// Partition types that are the system's on the drive it runs from, whatever they
/// are called: the EFI System Partition the firmware boots (the installer stick's
/// is just "boot"), and a Linux x86-64 root.
const SYSTEM_TYPES: [(&str, &str); 3] = [
    ("c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "EFI system partition"),
    ("0xef", "EFI system partition"),
    ("44479540-f297-41b2-9af7-d131d5f0458a", "system root"),
];

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

    /// The system has something on this drive mounted (outside `/run/media`) or
    /// swapping: the disk it boots from, the live stick, a data disk.
    pub fn in_use_by_system(&self) -> Option<Lock> {
        for v in &self.volumes {
            if let Some(at) = v.mount_points.iter().find(|m| !user_mount(m)) {
                return Some(Lock::InUse(at.clone()));
            }
            if v.swap_active {
                return Some(Lock::Swap);
            }
        }
        None
    }

    /// Why the whole drive cannot be formatted, repartitioned or written.
    pub fn lock(&self) -> Option<Lock> {
        self.in_use_by_system().or_else(|| self.read_only.then_some(Lock::ReadOnly))
    }

    /// The partitions, by offset (no whole-drive filesystem, no cleartext devices).
    pub fn partitions(&self) -> impl Iterator<Item = &Volume> {
        self.volumes.iter().filter(|v| v.number > 0)
    }

    pub fn volume(&self, object: &str) -> Option<&Volume> {
        self.volumes.iter().find(|v| v.object == object)
    }

    /// The cleartext device of an unlocked container.
    pub fn inside(&self, v: &Volume) -> Option<&Volume> {
        self.volumes.iter().find(|c| c.backing == v.object)
    }

    /// Why this partition cannot be formatted, deleted, grown or unmounted.
    pub fn volume_lock(&self, v: &Volume) -> Option<Lock> {
        for x in std::iter::once(v).chain(self.inside(v)) {
            if let Some(at) = x.mount_points.iter().find(|m| !user_mount(m)) {
                return Some(Lock::InUse(at.clone()));
            }
            if x.swap_active {
                return Some(Lock::Swap);
            }
        }
        if v.container {
            // Locked when anything inside it is.
            if let Some(l) = self.partitions().filter(|p| p.contained).find_map(|p| self.volume_lock(p)) {
                return Some(l);
            }
        }
        if self.read_only || v.read_only {
            return Some(Lock::ReadOnly);
        }
        if self.in_use_by_system().is_some() {
            let name = [&v.part_name, &v.label]
                .into_iter()
                .find(|n| SYSTEM_PART_NAMES.contains(&n.as_str()) || SYSTEM_LABELS.contains(&n.as_str()));
            if let Some(n) = name {
                return Some(Lock::System(n.clone()));
            }
            let ty = v.part_type.to_ascii_lowercase();
            if let Some((_, what)) = SYSTEM_TYPES.iter().find(|(t, _)| *t == ty) {
                return Some(Lock::System((*what).into()));
            }
        }
        None
    }

    /// What is on the drive, one entry per partition (or the whole-drive filesystem).
    pub fn contents(&self) -> Vec<String> {
        self.volumes
            .iter()
            .filter(|v| (v.number > 0 && !v.container) || (self.table.is_empty() && v.backing.is_empty()))
            .map(Volume::describe)
            .collect()
    }

    /// "USB", "Memory card", "Internal"
    pub fn kind(&self) -> &str {
        match self.bus.as_str() {
            "usb" => "USB",
            "sdio" | "mmc" => "Memory card",
            _ if self.removable => "Removable",
            "" => "Internal",
            other => other,
        }
    }

    pub fn table_name(&self) -> &str {
        match self.table.as_str() {
            "gpt" => "GPT",
            "dos" => "MBR",
            "" => "no partition table",
            other => other,
        }
    }

    /// "USB · 32.0 GB · KINGSTON (FAT, 31.9 GB)"
    pub fn summary(&self) -> String {
        let mut s = format!("{} · {}", self.kind(), human(self.size));
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

    /// The bytes partitions can occupy: from 1 MiB (where UDisks starts the first
    /// one) to the end, less GPT's backup table, and never past 2 TiB on MBR.
    pub fn usable(&self) -> (u64, u64) {
        let end = match self.table.as_str() {
            "gpt" => align_down(self.size.saturating_sub(33 * 512)),
            _ => align_down(self.size.min(MBR_MAX)),
        };
        (MIB, end)
    }

    /// The drive from start to end: partitions (outside any extended partition)
    /// and the free space between them. Empty when there is no partition table.
    pub fn layout(&self) -> Vec<Slot> {
        if self.table.is_empty() {
            return Vec::new();
        }
        let (start, end) = self.usable();
        let mut parts: Vec<(usize, &Volume)> =
            self.volumes.iter().enumerate().filter(|(_, v)| v.number > 0 && !v.contained).collect();
        parts.sort_by_key(|(_, v)| v.offset);
        let mut out = Vec::new();
        let mut at = start;
        for (i, p) in parts {
            if p.offset > at && p.offset - at >= MIN_FREE {
                out.push(Slot::Free(Gap { offset: at, size: align_down(p.offset - at) }));
            }
            out.push(Slot::Part(i));
            at = at.max(align_up(p.end()));
        }
        if end > at && end - at >= MIN_FREE {
            out.push(Slot::Free(Gap { offset: at, size: end - at }));
        }
        out
    }

    pub fn free(&self) -> Vec<Gap> {
        self.layout().into_iter().filter_map(|s| if let Slot::Free(g) = s { Some(g) } else { None }).collect()
    }

    /// The free space directly after a partition: what it could grow into.
    pub fn room_after(&self, v: &Volume) -> Option<Gap> {
        if v.contained || v.container {
            return None;
        }
        self.free().into_iter().find(|g| g.offset == align_up(v.end()))
    }
}

/// Free space smaller than this is alignment slack, not worth showing.
pub const MIN_FREE: u64 = 4 * MIB;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Gap {
    pub offset: u64,
    pub size: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Slot {
    /// An index into [`Drive::volumes`].
    Part(usize),
    Free(Gap),
}

/// Every drive with media, from a UDisks2 snapshot: internal disks first, then
/// removable ones, each by device name.
pub fn drives(objects: &Objects) -> Vec<Drive> {
    let mut out = Vec::new();
    for (path, ifaces) in objects {
        let Some(d) = ifaces.get("Drive") else { continue };
        let bus = get_s(d, "ConnectionBus");
        let removable = get_b(d, "Removable")
            || get_b(d, "MediaRemovable")
            || get_b(d, "Ejectable")
            || matches!(bus.as_str(), "usb" | "sdio" | "mmc");
        // The whole-disk block: points at this drive and is not a partition.
        let Some((bpath, b)) = objects.iter().find_map(|(p, i)| {
            let b = i.get("Block")?;
            (get_o(b, "Drive") == *path && !i.contains_key("Partition") && get_o(b, "CryptoBackingDevice").is_empty())
                .then_some((p, b))
        }) else {
            continue;
        };
        let size = get_u(b, "Size");
        if size == 0 {
            continue; // an empty card reader or optical drive
        }
        let table = objects[bpath].get("PartitionTable").map(|t| get_s(t, "Type")).unwrap_or_default();
        let mut volumes = Vec::new();
        for (p, i) in objects {
            let Some(vb) = i.get("Block") else { continue };
            let number = match i.get("Partition") {
                Some(part) if get_o(part, "Table") == *bpath => get_u(part, "Number"),
                Some(_) => continue,
                None if p == bpath && i.contains_key("Filesystem") => 0,
                None if p == bpath && !table.is_empty() => continue,
                None if p == bpath && !get_s(vb, "IdType").is_empty() => 0,
                None => continue,
            };
            volumes.push(volume(objects, p, i, vb, number));
        }
        volumes.sort_by_key(|v| (v.number == 0, v.offset));
        // Cleartext devices of unlocked containers: their mounts count too.
        let backing: Vec<String> = volumes.iter().map(|v| v.object.clone()).collect();
        for (p, i) in objects {
            let Some(vb) = i.get("Block") else { continue };
            if backing.contains(&get_o(vb, "CryptoBackingDevice")) {
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
            removable,
            read_only: get_b(b, "ReadOnly"),
            can_power_off: get_b(d, "CanPowerOff"),
            ejectable: get_b(d, "Ejectable"),
            table,
            volumes,
        });
    }
    out.sort_by(|a, b| (a.removable, &a.device).cmp(&(b.removable, &b.device)));
    out
}

fn volume(objects: &Objects, path: &str, i: &BTreeMap<String, Props>, b: &Props, number: u64) -> Volume {
    let mount_points = match i.get("Filesystem").and_then(|f| f.get("MountPoints")) {
        Some(Prop::ByteArrays(m)) => m.iter().map(|x| bytes_str(x)).collect(),
        _ => Vec::new(),
    };
    let unlocked = i.contains_key("Encrypted")
        && objects.values().any(|o| o.get("Block").is_some_and(|c| get_o(c, "CryptoBackingDevice") == path));
    let empty = Props::new();
    let part = i.get("Partition").unwrap_or(&empty);
    Volume {
        object: path.to_string(),
        device: get_s(b, "Device"),
        size: get_u(b, "Size"),
        fs_type: get_s(b, "IdType"),
        label: get_s(b, "IdLabel"),
        mount_points,
        swap_active: i.get("Swapspace").is_some_and(|s| get_b(s, "Active")),
        unlocked,
        read_only: get_b(b, "ReadOnly"),
        number,
        offset: get_u(part, "Offset"),
        part_type: get_s(part, "Type"),
        part_name: get_s(part, "Name"),
        container: get_b(part, "IsContainer"),
        contained: get_b(part, "IsContained"),
        backing: get_o(b, "CryptoBackingDevice"),
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

/// A size typed by the user: "500M", "2 GB", "1.5T", "300MiB". Decimal units like
/// the sizes shown, binary with an `i`; a bare number is GB. Rounded down to the
/// 1 MiB UDisks aligns to.
pub fn parse_size(text: &str) -> Result<u64, String> {
    let t = text.trim().to_ascii_lowercase();
    let bad = || format!("“{}” is not a size. Use e.g. 500 MB, 20 GB, 1.5 TB.", text.trim());
    let split = t.find(|c: char| !c.is_ascii_digit() && c != '.').unwrap_or(t.len());
    let (num, unit) = t.split_at(split);
    let value: f64 = num.parse().map_err(|_| bad())?;
    let mult: f64 = match unit.trim() {
        "" | "g" | "gb" => 1e9,
        "k" | "kb" => 1e3,
        "m" | "mb" => 1e6,
        "t" | "tb" => 1e12,
        "kib" => 1024.0,
        "mib" => MIB as f64,
        "gib" => (1u64 << 30) as f64,
        "tib" => (1u64 << 40) as f64,
        _ => return Err(bad()),
    };
    let bytes = value * mult;
    if !bytes.is_finite() || bytes < 0.0 || bytes > u64::MAX as f64 {
        return Err(bad());
    }
    Ok(align_down(bytes as u64))
}

// --- Filesystems -------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Fs {
    Fat32,
    Exfat,
    Ntfs,
    Ext4,
    Btrfs,
    Xfs,
    Swap,
}

const GPT_BASIC_DATA: &str = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7";
const GPT_LINUX: &str = "0fc63daf-8483-4772-8e79-3d69d8477de4";
const GPT_SWAP: &str = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f";
/// MBR addresses at most 2 TiB with 512-byte sectors.
pub const MBR_MAX: u64 = 2 << 40;

impl Fs {
    /// What "Format…" offers for a whole removable drive.
    pub const DRIVE: [Fs; 4] = [Fs::Fat32, Fs::Exfat, Fs::Ntfs, Fs::Ext4];
    /// What a partition can be formatted as.
    pub const ALL: [Fs; 7] = [Fs::Ext4, Fs::Btrfs, Fs::Xfs, Fs::Fat32, Fs::Exfat, Fs::Ntfs, Fs::Swap];

    pub fn title(self) -> &'static str {
        match self {
            Fs::Fat32 => "FAT32",
            Fs::Exfat => "exFAT",
            Fs::Ntfs => "NTFS",
            Fs::Ext4 => "ext4",
            Fs::Btrfs => "btrfs",
            Fs::Xfs => "XFS",
            Fs::Swap => "Swap",
        }
    }

    pub fn about(self) -> &'static str {
        match self {
            Fs::Fat32 => "Works with every computer, TV, car stereo and camera. Files up to 4 GB.",
            Fs::Exfat => "Works with Windows, macOS, Linux and most cameras. No file size limit.",
            Fs::Ntfs => "For Windows. macOS can read it but not write to it.",
            Fs::Ext4 => "For Linux only. Keeps file permissions. The safe choice for a Linux disk.",
            Fs::Btrfs => "For Linux only. Snapshots, compression and checksums, like PengyOS's own data.",
            Fs::Xfs => "For Linux only. Fast with very large files. Can grow but never shrink.",
            Fs::Swap => "Extra memory on disk for when the RAM is full. Holds no files.",
        }
    }

    /// The UDisks format type (mkfs.<this>).
    pub fn mkfs(self) -> &'static str {
        match self {
            Fs::Fat32 => "vfat",
            Fs::Exfat => "exfat",
            Fs::Ntfs => "ntfs",
            Fs::Ext4 => "ext4",
            Fs::Btrfs => "btrfs",
            Fs::Xfs => "xfs",
            Fs::Swap => "swap",
        }
    }

    pub fn partition_type(self, table: &str) -> &'static str {
        match (self, table) {
            (Fs::Ext4 | Fs::Btrfs | Fs::Xfs, "gpt") => GPT_LINUX,
            (Fs::Swap, "gpt") => GPT_SWAP,
            (_, "gpt") => GPT_BASIC_DATA,
            (Fs::Fat32, _) => "0x0c",
            (Fs::Ext4 | Fs::Btrfs | Fs::Xfs, _) => "0x83",
            (Fs::Swap, _) => "0x82",
            _ => "0x07",
        }
    }

    /// The smallest partition its mkfs accepts (xfsprogs refuses under 300 MB,
    /// mkfs.btrfs under ~114 MiB), checked before a partition is created so a
    /// refused mkfs cannot leave an unformatted partition behind.
    pub fn min_size(self) -> u64 {
        match self {
            Fs::Xfs => 300 * MIB,
            Fs::Btrfs => 128 * MIB,
            Fs::Fat32 | Fs::Exfat | Fs::Ntfs => 8 * MIB,
            Fs::Ext4 | Fs::Swap => MIB,
        }
    }

    /// Ext4, btrfs and XFS made by root would be root's: hand them to the user.
    pub fn take_ownership(self) -> bool {
        matches!(self, Fs::Ext4 | Fs::Btrfs | Fs::Xfs)
    }

    pub fn max_name(self) -> usize {
        match self {
            // libblockdev's checks (UDisks runs them only after the table is
            // rewritten, so a name it refuses would leave an empty drive): vfat
            // 11 bytes, exFAT 22 bytes of UTF-16, NTFS 128 bytes, ext 16 bytes,
            // XFS 12 bytes, btrfs 255 bytes, swap 15 bytes.
            Fs::Fat32 => 11,
            Fs::Exfat => 11,
            Fs::Ntfs => 32,
            Fs::Ext4 => 16,
            Fs::Btrfs => 255,
            Fs::Xfs => 12,
            Fs::Swap => 15,
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
            Fs::Ext4 | Fs::Btrfs | Fs::Xfs | Fs::Swap => {
                if self == Fs::Xfs && name.contains(' ') {
                    return Err("XFS names cannot contain spaces.".into());
                }
                (name.to_string(), name.len())
            }
        };
        if len > self.max_name() {
            return Err(format!("{} names can be at most {} characters long.", self.title(), self.max_name()));
        }
        Ok(n)
    }
}

/// How a filesystem can grow into the space after its partition.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Grow {
    /// Mounted or not (ext4), or there is no filesystem to grow.
    Any,
    /// Only while mounted (btrfs, XFS).
    Mounted,
    /// Only while unmounted (NTFS).
    Unmounted,
    No,
}

pub fn grow_rule(fs_type: &str) -> Grow {
    match fs_type {
        "" | "ext2" | "ext3" | "ext4" => Grow::Any,
        "btrfs" | "xfs" => Grow::Mounted,
        "ntfs" => Grow::Unmounted,
        _ => Grow::No,
    }
}

// --- Plans -------------------------------------------------------------------

/// One UDisks2 call. `object` is always a UDisks object path.
#[derive(Clone, Debug, PartialEq)]
pub enum Step {
    /// Filesystem.Unmount
    Unmount { object: String },
    /// Filesystem.Mount
    Mount { object: String },
    /// Encrypted.Lock
    Lock { object: String },
    /// Swapspace.Start / Swapspace.Stop
    SwapOn { object: String },
    SwapOff { object: String },
    /// Block.Format(table) on the whole drive: wipes it and writes an empty table.
    FormatTable { object: String, table: &'static str },
    /// PartitionTable.CreatePartitionAndFormat(offset, size (0 = the rest), ptype, "",
    /// {}, fs, {label, take-ownership}); PartitionTable.CreatePartition when `fs` is "".
    CreatePartition { object: String, offset: u64, size: u64, ptype: &'static str, fs: &'static str, label: String, take_ownership: bool },
    /// Block.Format(fs, {label, take-ownership, update-partition-type}) on a partition.
    FormatVolume { object: String, fs: &'static str, label: String, take_ownership: bool },
    /// Partition.Delete({tear-down})
    Delete { object: String },
    /// Partition.Resize(size): moves the end only.
    ResizePartition { object: String, size: u64 },
    /// Filesystem.Resize(0): fill the partition.
    ResizeFilesystem { object: String },
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

/// The same for one partition (and, for a container, what is inside it).
pub fn teardown_volume(drive: &Drive, v: &Volume) -> Vec<Step> {
    let mut steps = Vec::new();
    let mut scope: Vec<&Volume> = vec![v];
    if v.container {
        scope.extend(drive.partitions().filter(|p| p.contained));
    }
    for x in &scope {
        for y in drive.inside(x).into_iter().chain(std::iter::once(*x)) {
            if !y.mount_points.is_empty() {
                steps.push(Step::Unmount { object: y.object.clone() });
            }
        }
    }
    steps.extend(scope.iter().filter(|x| x.unlocked).map(|x| Step::Lock { object: x.object.clone() }));
    steps
}

pub fn table_for(size: u64) -> &'static str {
    if size > MBR_MAX { "gpt" } else { "dos" }
}

/// Format a whole removable drive: one partition, filling it.
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
        offset: MIB,
        size: 0,
        ptype: fs.partition_type(table),
        fs: fs.mkfs(),
        label,
        take_ownership: fs.take_ownership(),
    });
    Ok(steps)
}

/// Erase the drive and write an empty partition table ("gpt" or "dos").
pub fn plan_new_table(drive: &Drive, table: &'static str) -> Result<Vec<Step>, String> {
    if let Some(l) = drive.lock() {
        return Err(l.text());
    }
    if table == "dos" && drive.size > MBR_MAX {
        return Err(format!("MBR can use only the first 2.2 TB of {}. Choose GPT.", drive.name()));
    }
    let mut steps = teardown(drive);
    steps.push(Step::FormatTable { object: drive.block.clone(), table });
    Ok(steps)
}

/// A new partition at the start of `gap`. `size` None fills the gap; `fs` None
/// leaves it unformatted.
pub fn plan_create(drive: &Drive, gap: Gap, size: Option<u64>, fs: Option<Fs>, name: &str) -> Result<Vec<Step>, String> {
    if drive.read_only {
        return Err(Lock::ReadOnly.text());
    }
    if drive.table.is_empty() {
        return Err(format!("{} has no partition table. Create one first.", drive.name()));
    }
    if !drive.free().contains(&gap) {
        return Err("That free space is no longer there. The drive has changed.".into());
    }
    if drive.table == "dos" && drive.partitions().filter(|p| !p.contained).count() >= 4 {
        return Err("An MBR drive holds at most four partitions, and this one has four. \
                    Delete one, or give the drive a GPT partition table."
            .into());
    }
    let size = match size {
        None => gap.size,
        Some(n) if n > gap.size => return Err(format!("There is only {} free there.", human(gap.size))),
        Some(n) if n < MIB => return Err("A partition must be at least 1 MB.".into()),
        Some(n) => align_down(n),
    };
    let (ptype, mkfs, label, own) = match fs {
        Some(f) => {
            if size < f.min_size() {
                return Err(format!("{} needs at least {}.", f.title(), human(f.min_size())));
            }
            (f.partition_type(&drive.table), f.mkfs(), f.check_name(name)?, f.take_ownership())
        }
        None => (if drive.table == "gpt" { GPT_LINUX } else { "0x83" }, "", String::new(), false),
    };
    Ok(vec![Step::CreatePartition {
        object: drive.block.clone(),
        offset: gap.offset,
        size,
        ptype,
        fs: mkfs,
        label,
        take_ownership: own,
    }])
}

/// Format one partition in place.
pub fn plan_format_volume(drive: &Drive, v: &Volume, fs: Fs, name: &str) -> Result<Vec<Step>, String> {
    if let Some(l) = drive.volume_lock(v) {
        return Err(l.text());
    }
    if v.container {
        return Err("An extended partition holds other partitions; it cannot be formatted.".into());
    }
    if v.size < fs.min_size() {
        return Err(format!("{} needs at least {}; this partition is {}.", fs.title(), human(fs.min_size()), human(v.size)));
    }
    let label = fs.check_name(name)?;
    let mut steps = teardown_volume(drive, v);
    steps.push(Step::FormatVolume { object: v.object.clone(), fs: fs.mkfs(), label, take_ownership: fs.take_ownership() });
    Ok(steps)
}

pub fn plan_delete(drive: &Drive, v: &Volume) -> Result<Vec<Step>, String> {
    if let Some(l) = drive.volume_lock(v) {
        return Err(l.text());
    }
    if v.number == 0 {
        return Err("This is not a partition.".into());
    }
    let mut steps = teardown_volume(drive, v);
    steps.push(Step::Delete { object: v.object.clone() });
    Ok(steps)
}

/// Grow a partition, and its filesystem, into the free space right after it.
pub fn plan_grow(drive: &Drive, v: &Volume) -> Result<Vec<Step>, String> {
    if let Some(l) = drive.volume_lock(v) {
        return Err(l.text());
    }
    let Some(gap) = drive.room_after(v) else {
        return Err("There is no free space right after this partition.".into());
    };
    let fs_inside = drive.inside(v);
    let mounted = !v.mount_points.is_empty() || fs_inside.is_some_and(|c| !c.mount_points.is_empty());
    let mut steps = Vec::new();
    match grow_rule(&v.fs_type) {
        Grow::No => {
            return Err(format!("{} cannot be grown here.", fs_name(&v.fs_type)));
        }
        Grow::Mounted if !mounted => {
            return Err(format!("Mount it first: {} can only grow while it is mounted.", fs_name(&v.fs_type)));
        }
        Grow::Unmounted => steps.extend(teardown_volume(drive, v)),
        Grow::Mounted | Grow::Any => {}
    }
    steps.push(Step::ResizePartition { object: v.object.clone(), size: gap.offset + gap.size - v.offset });
    if !v.fs_type.is_empty() {
        steps.push(Step::ResizeFilesystem { object: v.object.clone() });
    }
    Ok(steps)
}

/// Mount or unmount, start or stop swap: whichever the partition offers now.
pub fn plan_toggle(drive: &Drive, v: &Volume) -> Result<Step, String> {
    let object = v.object.clone();
    if v.fs_type == "swap" {
        if drive.read_only {
            return Err(Lock::ReadOnly.text());
        }
        return Ok(if v.swap_active { Step::SwapOff { object } } else { Step::SwapOn { object } });
    }
    if !v.mountable() {
        return Err("There is nothing on it to mount.".into());
    }
    if v.mount_points.is_empty() {
        Ok(Step::Mount { object })
    } else {
        match drive.volume_lock(v) {
            Some(l @ Lock::InUse(_)) => Err(l.text()),
            _ => Ok(Step::Unmount { object }),
        }
    }
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
                    // UDisks' "none" for an object path.
                    ("CryptoBackingDevice", s("/")),
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
                    ("CryptoBackingDevice", s("/")),
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
    fn empty_readers_are_not_listed() {
        let o = Snap::default().drive("reader", "usb", true).disk("sdc", "reader", 0, None).0;
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
                Step::CreatePartition { object: blk.clone(), offset: MIB, size: 0, ptype: "0x0c", fs: "vfat", label: "PHOTOS".into(), take_ownership: false },
            ]
        );
        let ext4 = plan_format(d, Fs::Ext4, "backup").unwrap();
        assert_eq!(ext4[2], Step::CreatePartition { object: blk.clone(), offset: MIB, size: 0, ptype: "0x83", fs: "ext4", label: "backup".into(), take_ownership: true });
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

    // --- 0.2.0: every drive, the layout, the locks, the partition plans ----------

    const GB: u64 = 1_000_000_000;

    impl Snap {
        /// A partition with a real place on the drive.
        #[allow(clippy::too_many_arguments)]
        fn partx(self, blk: &str, disk: &str, n: u64, offset: u64, size: u64, fs: &str, label: &str, name: &str, at: &[&str]) -> Self {
            let p = format!("/org/freedesktop/UDisks2/block_devices/{blk}");
            let s2 = self.part(blk, disk, n, fs, label, at);
            s2.add(&p, "Block", &[("Size", Prop::U64(size))]).add(
                &p,
                "Partition",
                &[("Offset", Prop::U64(offset)), ("Size", Prop::U64(size)), ("Name", s(name))],
            )
        }
    }

    /// An installed PengyOS: ESP, root-a (mounted at /), root-b (not mounted), data.
    fn pengyos_nvme() -> Snap {
        Snap::default()
            .drive("nvme", "", false)
            .disk("nvme0n1", "nvme", 512 * GB, Some("gpt"))
            .partx("nvme0n1p1", "nvme0n1", 1, MIB, 256 * MIB, "vfat", "PENGYOS-ESP", "PENGYOS-ESP", &["/boot/efi"])
            .partx("nvme0n1p2", "nvme0n1", 2, 257 * MIB, 8 << 30, "ext4", "pengyos-root-a", "pengyos-root-a", &["/"])
            .partx("nvme0n1p3", "nvme0n1", 3, 257 * MIB + (8 << 30), 8 << 30, "ext4", "pengyos-root-b", "pengyos-root-b", &[])
            .partx("nvme0n1p4", "nvme0n1", 4, 257 * MIB + (16 << 30), 400 * GB, "btrfs", "pengyos-data", "pengyos-data", &["/data"])
    }

    /// A second internal disk: a 100 GB ext4 partition, then free space.
    fn spare_disk() -> Snap {
        Snap::default()
            .drive("sata", "", false)
            .disk("sda", "sata", 1000 * GB, Some("gpt"))
            .partx("sda1", "sda", 1, MIB, 100 * GB, "ext4", "backup", "", &[])
    }

    fn one(o: Snap) -> Drive {
        drives(&o.0).remove(0)
    }

    #[test]
    fn internal_drives_are_listed_first() {
        let mut o = pengyos_nvme();
        o.0.extend(stick(&[]));
        let d = drives(&o.0);
        assert_eq!(d.iter().map(|d| d.device.as_str()).collect::<Vec<_>>(), ["/dev/nvme0n1", "/dev/sdb"]);
        assert!(!d[0].removable && d[1].removable);
        assert_eq!(d[0].kind(), "Internal");
        assert_eq!(d[0].table_name(), "GPT");
        assert_eq!(d[0].partitions().map(|v| v.number).collect::<Vec<_>>(), [1, 2, 3, 4]);
    }

    #[test]
    fn the_running_systems_partitions_are_locked_even_unmounted() {
        let d = one(pengyos_nvme());
        assert_eq!(d.lock(), Some(Lock::InUse("/boot/efi".into())));
        let v = |n: u64| d.partitions().find(|v| v.number == n).unwrap().clone();
        assert_eq!(d.volume_lock(&v(2)), Some(Lock::InUse("/".into())));
        // root-b is never mounted; it is still this system's other slot.
        assert_eq!(d.volume_lock(&v(3)), Some(Lock::System("pengyos-root-b".into())));
        assert!(plan_delete(&d, &v(3)).unwrap_err().contains("Part of this PengyOS system"));
        assert!(plan_format_volume(&d, &v(3), Fs::Ext4, "").is_err());
        assert!(plan_grow(&d, &v(4)).is_err());
    }

    #[test]
    fn an_old_pengyos_install_on_another_disk_can_be_erased() {
        // Nothing on it mounted: it is not the running system.
        let mut o = pengyos_nvme();
        for p in ["nvme0n1p1", "nvme0n1p2", "nvme0n1p4"] {
            o = o.add(&format!("/org/freedesktop/UDisks2/block_devices/{p}"), "Filesystem", &[("MountPoints", mounts(&[]))]);
        }
        let d = one(o);
        assert_eq!(d.lock(), None);
        assert!(d.partitions().all(|v| d.volume_lock(v).is_none()));
        assert!(plan_new_table(&d, "gpt").is_ok());
    }

    #[test]
    fn the_stick_a_session_runs_from_keeps_its_boot_partition() {
        // What the Desktop installer stick looks like from inside its own session:
        // an ESP called "boot", the root, the payload.
        let esp = "/org/freedesktop/UDisks2/block_devices/sda1";
        let o = Snap::default()
            .drive("stick", "usb", true)
            .disk("sda", "stick", 5_300_000_000, Some("gpt"))
            .partx("sda1", "sda", 1, MIB, 64 * MIB, "vfat", "boot", "boot", &[])
            .add(esp, "Partition", &[("Type", s("C12A7328-F81F-11D2-BA4B-00A0C93EC93B"))])
            .partx("sda2", "sda", 2, 65 * MIB, 4 * GB, "ext4", "rootfs", "root", &["/"])
            .partx("sda3", "sda", 3, 65 * MIB + 4 * GB, GB, "ext4", "PENGYOS-PAYLOAD", "payload", &["/run/pengyos/payload"]);
        let d = one(o);
        assert_eq!(d.volume_lock(d.volume(esp).unwrap()), Some(Lock::System("EFI system partition".into())));
        // The same partition on a stick nothing is running from is the user's to erase.
        let mut other = d.clone();
        for v in &mut other.volumes {
            v.mount_points.clear();
        }
        assert_eq!(other.volume_lock(other.volume(esp).unwrap()), None);
    }

    #[test]
    fn the_live_stick_is_locked_whole() {
        let o = Snap::default()
            .drive("stick", "usb", true)
            .disk("sdb", "stick", 16 * GB, Some("gpt"))
            .partx("sdb1", "sdb", 1, MIB, 256 * MIB, "vfat", "PENGYOS-ESP", "", &[])
            .partx("sdb2", "sdb", 2, 257 * MIB, 4 * GB, "ext4", "PENGYOS-PAYLOAD", "", &["/run/pengyos/payload"]);
        let d = one(o);
        assert!(d.lock().is_some());
        assert_eq!(d.volume_lock(&d.volumes[0]), Some(Lock::System("PENGYOS-ESP".into())));
        assert!(plan_format(&d, Fs::Fat32, "").is_err());
        assert!(plan_write(&d, None).is_err());
    }

    #[test]
    fn layout_shows_free_space_aligned_and_inside_the_gpt() {
        let d = one(spare_disk());
        let free = d.free();
        // 100 GB is not a whole number of MiB: the next partition starts at the
        // next MiB boundary after it.
        let after = align_up(MIB + 100 * GB);
        assert_eq!(free.len(), 1);
        assert_eq!(free[0].offset, after);
        assert_eq!(free[0].offset + free[0].size, align_down(1000 * GB - 33 * 512));
        assert_eq!(d.layout(), vec![Slot::Part(0), Slot::Free(free[0])]);
        assert_eq!(d.room_after(&d.volumes[0]), Some(free[0]));
    }

    #[test]
    fn layout_finds_holes_between_partitions_and_ignores_slack() {
        let o = Snap::default()
            .drive("sata", "", false)
            .disk("sda", "sata", 100 * GB, Some("gpt"))
            .partx("sda1", "sda", 1, MIB, 10 * GB, "ext4", "", "", &[])
            // a 2 MiB sliver, then sda2
            .partx("sda2", "sda", 2, align_up(MIB + 10 * GB) + 2 * MIB, 10 * GB, "ext4", "", "", &[])
            // a real 20 GB hole, then sda3 to the end
            .partx("sda3", "sda", 3, 50 * GB, 50 * GB - 2 * MIB, "ext4", "", "", &[]);
        let d = one(o);
        let free = d.free();
        assert_eq!(free.len(), 1, "{free:?}");
        assert_eq!(free[0].offset, align_up(align_up(MIB + 10 * GB) + 2 * MIB + 10 * GB));
        assert_eq!(free[0].offset + free[0].size, align_down(50 * GB));
        assert_eq!(d.room_after(&d.volumes[0]), None);
        assert_eq!(d.room_after(&d.volumes[1]), Some(free[0]));
    }

    #[test]
    fn an_empty_table_is_all_free_and_no_table_is_none() {
        let d = one(Snap::default().drive("sata", "", false).disk("sda", "sata", 1000 * GB, Some("gpt")));
        assert_eq!(d.free(), vec![Gap { offset: MIB, size: align_down(1000 * GB - 33 * 512) - MIB }]);
        let bare = one(Snap::default().drive("sata", "", false).disk("sda", "sata", 1000 * GB, None));
        assert!(bare.layout().is_empty());
        assert!(plan_create(&bare, Gap { offset: MIB, size: GB }, None, Some(Fs::Ext4), "").unwrap_err().contains("no partition table"));
    }

    #[test]
    fn mbr_stops_at_2_tib() {
        let d = one(Snap::default().drive("big", "usb", true).disk("sdc", "big", 4_000 * GB, Some("dos")));
        assert_eq!(d.free()[0].offset + d.free()[0].size, MBR_MAX);
        assert!(plan_new_table(&d, "dos").unwrap_err().contains("Choose GPT"));
        assert!(plan_new_table(&d, "gpt").is_ok());
    }

    #[test]
    fn create_checks_size_name_and_room() {
        let d = one(spare_disk());
        let gap = d.free()[0];
        let steps = plan_create(&d, gap, Some(200 * GB), Some(Fs::Ext4), "media").unwrap();
        assert_eq!(
            steps,
            vec![Step::CreatePartition {
                object: d.block.clone(),
                offset: gap.offset,
                size: align_down(200 * GB),
                ptype: GPT_LINUX,
                fs: "ext4",
                label: "media".into(),
                take_ownership: true,
            }]
        );
        // None = the whole gap; unformatted = CreatePartition with no fs.
        assert!(matches!(&plan_create(&d, gap, None, None, "ignored").unwrap()[0],
            Step::CreatePartition { size, fs: "", label, .. } if *size == gap.size && label.is_empty()));
        assert!(plan_create(&d, gap, Some(2000 * GB), Some(Fs::Ext4), "").unwrap_err().contains("only"));
        assert!(plan_create(&d, gap, Some(100 * MIB), Some(Fs::Xfs), "").unwrap_err().contains("XFS needs at least"));
        assert!(plan_create(&d, gap, Some(GB), Some(Fs::Xfs), "has space").is_err());
        assert!(matches!(&plan_create(&d, gap, Some(GB), Some(Fs::Swap), "").unwrap()[0], Step::CreatePartition { ptype: GPT_SWAP, fs: "swap", .. }));
        // A gap that is not (or no longer) on the drive.
        assert!(plan_create(&d, Gap { offset: MIB, size: GB }, None, None, "").unwrap_err().contains("changed"));
    }

    #[test]
    fn mbr_holds_four_primaries() {
        let mut o = Snap::default().drive("old", "", false).disk("sda", "old", 500 * GB, Some("dos"));
        for n in 1..=4u64 {
            o = o.partx(&format!("sda{n}"), "sda", n, n * 10 * GB, 5 * GB, "ext4", "", "", &[]);
        }
        let d = one(o);
        let gap = d.free()[0];
        assert!(plan_create(&d, gap, None, Some(Fs::Ext4), "").unwrap_err().contains("at most four"));
    }

    #[test]
    fn logical_partitions_live_inside_the_extended_one() {
        let ext = "/org/freedesktop/UDisks2/block_devices/sda2";
        let o = Snap::default()
            .drive("old", "", false)
            .disk("sda", "old", 100 * GB, Some("dos"))
            .partx("sda1", "sda", 1, MIB, 10 * GB, "ext4", "", "", &[])
            .partx("sda2", "sda", 2, 20 * GB, 30 * GB, "", "", "", &[])
            .add(ext, "Partition", &[("IsContainer", Prop::Bool(true))])
            .partx("sda5", "sda", 5, 20 * GB + MIB, 10 * GB, "ext4", "", "", &["/run/media/pat/x"])
            .add("/org/freedesktop/UDisks2/block_devices/sda5", "Partition", &[("IsContained", Prop::Bool(true))]);
        let d = one(o);
        let slots = d.layout();
        // sda1, a hole, sda2 (the extended one, not sda5 inside it), the rest.
        assert_eq!(slots.iter().filter(|s| matches!(s, Slot::Part(_))).count(), 2);
        assert_eq!(slots.iter().filter(|s| matches!(s, Slot::Free(_))).count(), 2);
        let e = d.volume(ext).unwrap();
        assert_eq!(e.describe(), "Extended partition (30.0 GB)");
        assert!(plan_format_volume(&d, e, Fs::Ext4, "").unwrap_err().contains("extended"));
        // Deleting it unmounts what is inside first.
        let steps = plan_delete(&d, e).unwrap();
        assert_eq!(steps[0], Step::Unmount { object: "/org/freedesktop/UDisks2/block_devices/sda5".into() });
        assert_eq!(steps.last(), Some(&Step::Delete { object: ext.into() }));
        assert!(!d.contents().iter().any(|c| c.contains("Extended")));
    }

    #[test]
    fn format_volume_unmounts_then_formats() {
        let o = Snap(spare_disk().0).add(
            "/org/freedesktop/UDisks2/block_devices/sda1",
            "Filesystem",
            &[("MountPoints", mounts(&["/run/media/pat/backup"]))],
        );
        let d = one(o);
        let v = &d.volumes[0];
        assert_eq!(
            plan_format_volume(&d, v, Fs::Btrfs, "Media").unwrap(),
            vec![
                Step::Unmount { object: v.object.clone() },
                Step::FormatVolume { object: v.object.clone(), fs: "btrfs", label: "Media".into(), take_ownership: true },
            ]
        );
        assert!(plan_format_volume(&d, v, Fs::Fat32, "Media").unwrap().contains(&Step::FormatVolume {
            object: v.object.clone(),
            fs: "vfat",
            label: "MEDIA".into(),
            take_ownership: false
        }));
    }

    #[test]
    fn grow_follows_what_each_filesystem_allows() {
        let d = one(spare_disk());
        let v = d.volumes[0].clone();
        let gap = d.free()[0];
        let end = gap.offset + gap.size;
        assert_eq!(
            plan_grow(&d, &v).unwrap(),
            vec![
                Step::ResizePartition { object: v.object.clone(), size: end - MIB },
                Step::ResizeFilesystem { object: v.object.clone() },
            ]
        );
        let with = |fs: &str, at: &[&str]| {
            let mut d = d.clone();
            d.volumes[0].fs_type = fs.into();
            d.volumes[0].mount_points = at.iter().map(|s| s.to_string()).collect();
            d
        };
        let d2 = with("btrfs", &[]);
        assert!(plan_grow(&d2, &d2.volumes[0]).unwrap_err().contains("Mount it first"));
        let d2 = with("btrfs", &["/run/media/pat/b"]);
        assert_eq!(plan_grow(&d2, &d2.volumes[0]).unwrap().len(), 2);
        let d2 = with("ntfs", &["/run/media/pat/n"]);
        assert_eq!(plan_grow(&d2, &d2.volumes[0]).unwrap()[0], Step::Unmount { object: v.object.clone() });
        let d2 = with("exfat", &[]);
        assert!(plan_grow(&d2, &d2.volumes[0]).is_err());
        let d2 = with("", &[]);
        assert_eq!(plan_grow(&d2, &d2.volumes[0]).unwrap().len(), 1, "no filesystem: the partition only");
    }

    #[test]
    fn toggles_mount_and_swap() {
        let d = one(spare_disk());
        let v = d.volumes[0].clone();
        assert_eq!(plan_toggle(&d, &v), Ok(Step::Mount { object: v.object.clone() }));
        let mut m = v.clone();
        m.mount_points = vec!["/run/media/pat/backup".into()];
        assert_eq!(plan_toggle(&d, &m), Ok(Step::Unmount { object: v.object.clone() }));
        m.mount_points = vec!["/srv".into()];
        assert!(plan_toggle(&d, &m).is_err(), "a system mount is not the window's to unmount");
        let mut sw = v.clone();
        sw.fs_type = "swap".into();
        assert_eq!(plan_toggle(&d, &sw), Ok(Step::SwapOn { object: v.object.clone() }));
        sw.swap_active = true;
        assert_eq!(plan_toggle(&d, &sw), Ok(Step::SwapOff { object: v.object.clone() }));
        let mut none = v.clone();
        none.fs_type.clear();
        assert!(plan_toggle(&d, &none).is_err());
    }

    #[test]
    fn sizes_parse() {
        assert_eq!(parse_size("500M"), Ok(align_down(500_000_000)));
        assert_eq!(parse_size(" 2 GB "), Ok(align_down(2_000_000_000)));
        assert_eq!(parse_size("1.5t"), Ok(align_down(1_500_000_000_000)));
        assert_eq!(parse_size("300MiB"), Ok(300 * MIB));
        assert_eq!(parse_size("20"), Ok(align_down(20 * GB)));
        assert!(parse_size("lots").is_err());
        assert!(parse_size("5 parsecs").is_err());
        assert!(parse_size("").is_err());
    }

    #[test]
    fn volume_titles() {
        let mut v = Volume { size: GB, ..Default::default() };
        assert_eq!(v.title(), "Unformatted");
        v.fs_type = "swap".into();
        assert_eq!(v.title(), "Swap");
        v.fs_type = "ext4".into();
        assert_eq!(v.title(), "Untitled");
        v.number = 3;
        assert_eq!(v.title(), "Partition 3");
        v.part_name = "pengyos-root-b".into();
        v.fs_type = "ext4".into();
        assert_eq!(v.title(), "pengyos-root-b");
        v.label = "Photos".into();
        assert_eq!(v.describe(), "Photos (ext4, 1.0 GB)");
    }
}
