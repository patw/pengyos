//! The UDisks2 client (zbus, blocking): read the snapshot [`crate::drives`] works
//! from, run [`Step`]s, and write an image through `Block.OpenForRestore`.
//!
//! UDisks does the privileged work and asks polkit; a password prompt (when the
//! policy wants one) comes from COSMIC's polkit agent while the call waits, so
//! these calls block for as long as the user takes. Run them off the UI thread.

use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use zbus::blocking::{Connection, Proxy};
use zbus::zvariant::{self, Array, OwnedObjectPath, OwnedValue, Value};

use crate::{copy_image, explain, plan_write, CountingReader, Compression, Drive, Objects, Prop, Step};

const DEST: &str = "org.freedesktop.UDisks2";
const PREFIX: &str = "org.freedesktop.UDisks2.";
pub fn connect() -> Result<Connection, String> {
    Connection::system().map_err(|e| format!("Cannot reach the disk service (UDisks): {e}"))
}

fn err(e: zbus::Error) -> String {
    match &e {
        zbus::Error::MethodError(name, msg, _) => explain(name.as_str(), msg.as_deref().unwrap_or("")),
        zbus::Error::FDO(f) => f.to_string(),
        _ => e.to_string(),
    }
}

fn bytes(a: &Array<'_>) -> Vec<u8> {
    a.inner().iter().filter_map(|x| if let Value::U8(b) = x { Some(*b) } else { None }).collect()
}

fn prop(v: &Value<'_>) -> Prop {
    match v {
        Value::Str(s) => Prop::Str(s.to_string()),
        Value::ObjectPath(p) => Prop::Str(p.to_string()),
        Value::Bool(b) => Prop::Bool(*b),
        Value::U64(n) => Prop::U64(*n),
        Value::U32(n) => Prop::U64(u64::from(*n)),
        Value::Array(a) => match a.element_signature().to_string().as_str() {
            "y" => Prop::Bytes(bytes(a)),
            "ay" => Prop::ByteArrays(
                a.inner().iter().filter_map(|x| if let Value::Array(i) = x { Some(bytes(i)) } else { None }).collect(),
            ),
            _ => Prop::Other,
        },
        _ => Prop::Other,
    }
}

type Managed = HashMap<OwnedObjectPath, HashMap<String, HashMap<String, OwnedValue>>>;

/// `GetManagedObjects`, reduced to [`Objects`].
pub fn snapshot(conn: &Connection) -> Result<Objects, String> {
    let p = Proxy::new(conn, DEST, "/org/freedesktop/UDisks2", "org.freedesktop.DBus.ObjectManager").map_err(err)?;
    let raw: Managed = p.call("GetManagedObjects", &()).map_err(err)?;
    let mut out = Objects::new();
    for (path, ifaces) in raw {
        let o = out.entry(path.to_string()).or_default();
        for (iface, props) in ifaces {
            let Some(short) = iface.strip_prefix(PREFIX) else { continue };
            o.insert(short.to_string(), props.iter().map(|(k, v)| (k.clone(), prop(v))).collect());
        }
    }
    Ok(out)
}

pub fn drives(conn: &Connection) -> Result<Vec<Drive>, String> {
    snapshot(conn).map(|o| crate::drives(&o))
}

type Opts<'a> = HashMap<&'a str, Value<'a>>;

fn proxy<'c>(conn: &'c Connection, object: &str, iface: &str) -> Result<Proxy<'c>, String> {
    Proxy::new(conn, DEST, object.to_string(), format!("{PREFIX}{iface}")).map_err(err)
}

fn call0(conn: &Connection, object: &str, iface: &str, method: &str) -> Result<(), String> {
    proxy(conn, object, iface)?.call::<_, _, ()>(method, &(Opts::new(),)).map_err(err)
}

fn format_opts(label: &str, take_ownership: bool) -> Opts<'_> {
    let mut o = Opts::new();
    if !label.is_empty() {
        o.insert("label", Value::from(label));
    }
    if take_ownership {
        o.insert("take-ownership", Value::from(true));
    }
    o
}

/// Run one step. [`Step::Restore`] is not a single call; see [`write_image`].
pub fn run(conn: &Connection, step: &Step) -> Result<(), String> {
    match step {
        Step::Unmount { object } => call0(conn, object, "Filesystem", "Unmount"),
        Step::Mount { object } => {
            proxy(conn, object, "Filesystem")?.call::<_, _, String>("Mount", &(Opts::new(),)).map(|_| ()).map_err(err)
        }
        Step::Lock { object } => call0(conn, object, "Encrypted", "Lock"),
        Step::SwapOn { object } => call0(conn, object, "Swapspace", "Start"),
        Step::SwapOff { object } => call0(conn, object, "Swapspace", "Stop"),
        Step::Rescan { object } => call0(conn, object, "Block", "Rescan"),
        Step::PowerOff { object } => call0(conn, object, "Drive", "PowerOff"),
        Step::Eject { object } => call0(conn, object, "Drive", "Eject"),
        Step::FormatTable { object, table } => {
            proxy(conn, object, "Block")?.call::<_, _, ()>("Format", &(*table, Opts::new())).map_err(err)
        }
        Step::CreatePartition { object, offset, size, ptype, fs, label, take_ownership } => {
            let table = proxy(conn, object, "PartitionTable")?;
            if fs.is_empty() {
                table.call::<_, _, OwnedObjectPath>("CreatePartition", &(*offset, *size, *ptype, "", Opts::new()))
            } else {
                table.call::<_, _, OwnedObjectPath>(
                    "CreatePartitionAndFormat",
                    &(*offset, *size, *ptype, "", Opts::new(), *fs, format_opts(label, *take_ownership)),
                )
            }
            .map(|_| ())
            .map_err(err)
        }
        Step::FormatVolume { object, fs, label, take_ownership } => {
            let mut o = format_opts(label, *take_ownership);
            // Keep the partition's type in step with what is on it (Linux
            // filesystem, swap, basic data).
            o.insert("update-partition-type", Value::from(true));
            proxy(conn, object, "Block")?.call::<_, _, ()>("Format", &(*fs, o)).map_err(err)
        }
        Step::Delete { object } => {
            let mut o = Opts::new();
            o.insert("tear-down", Value::from(true));
            proxy(conn, object, "Partition")?.call::<_, _, ()>("Delete", &(o,)).map_err(err)
        }
        Step::ResizePartition { object, size } => {
            proxy(conn, object, "Partition")?.call::<_, _, ()>("Resize", &(*size, Opts::new())).map_err(err)
        }
        Step::ResizeFilesystem { object } => {
            proxy(conn, object, "Filesystem")?.call::<_, _, ()>("Resize", &(0u64, Opts::new())).map_err(err)
        }
        Step::Restore { .. } => Err("internal: Restore is run by write_image".into()),
    }
}

/// Run every step in order, stopping at the first failure.
pub fn run_all(conn: &Connection, steps: &[Step]) -> Result<(), String> {
    steps.iter().try_for_each(|s| run(conn, s))
}

fn open_for_restore(conn: &Connection, object: &str) -> Result<File, String> {
    let fd: zvariant::OwnedFd = proxy(conn, object, "Block")?.call("OpenForRestore", &(Opts::new(),)).map_err(err)?;
    Ok(File::from(std::os::fd::OwnedFd::from(fd)))
}

/// Write `image` to the whole drive. `progress(done, total)` counts bytes written
/// to the drive when the unpacked size is known (a raw image, or `.xz` from its
/// index), else bytes of the image file read: compressed bytes track the written
/// ones badly (a run of zeros is a few bytes of `.xz`), so they are the fallback.
pub fn write_image(
    conn: &Connection,
    drive: &Drive,
    image: &Path,
    cancel: &AtomicBool,
    mut progress: impl FnMut(u64, u64),
) -> Result<(), String> {
    let total = std::fs::metadata(image).map_err(|e| format!("Cannot open {}: {e}", image.display()))?.len();
    let unpacked = crate::image_size(image);
    let steps = plan_write(drive, unpacked)?;
    for s in steps.iter().take_while(|s| !matches!(s, Step::Restore { .. })) {
        run(conn, s)?;
    }
    let file = File::open(image).map_err(|e| format!("Cannot open {}: {e}", image.display()))?;
    let mut dev = open_for_restore(conn, &drive.block)?;
    let count = Arc::new(AtomicU64::new(0));
    let input = CountingReader { inner: file, count: count.clone() };
    let report = |written: u64| match unpacked {
        Some(n) => progress(written, n),
        None => progress(count.load(Ordering::Relaxed), total),
    };
    let sync = |d: &mut File| d.sync_data();

    let result = match Compression::of(image).command() {
        None => copy_image(input, &mut dev, sync, report, cancel).map(|_| ()).map_err(|e| e.text()),
        Some((cmd, args)) => {
            let mut child = Command::new(cmd)
                .args(args)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
                .map_err(|e| format!("Cannot run {cmd}: {e}"))?;
            let mut stdin = child.stdin.take().expect("piped");
            let feeder = std::thread::spawn(move || {
                let mut input = input;
                // A broken pipe means the decompressor stopped; its exit status says why.
                let _ = io::copy(&mut input, &mut stdin);
            });
            let stdout = child.stdout.take().expect("piped");
            let copied = copy_image(stdout, &mut dev, sync, report, cancel);
            if copied.is_err() {
                let _ = child.kill();
            }
            let mut stderr = String::new();
            if let Some(mut e) = child.stderr.take() {
                let _ = e.read_to_string(&mut stderr);
            }
            let status = child.wait().map_err(|e| e.to_string())?;
            let _ = feeder.join();
            match copied {
                Err(e) => Err(e.text()),
                Ok(_) if !status.success() => Err(format!(
                    "The image could not be unpacked ({cmd}: {}). The download may be incomplete.",
                    stderr.trim().lines().last().unwrap_or("failed")
                )),
                Ok(_) => Ok(()),
            }
        }
    };
    let _ = dev.flush();
    drop(dev);
    // Let the kernel and UDisks see the new partitions, whatever happened.
    let _ = run(conn, &Step::Rescan { object: drive.block.clone() });
    result
}
