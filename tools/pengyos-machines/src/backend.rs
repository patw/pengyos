//! Running the tools, and the VNC connection to a VM's screen. std only.

use std::io::{ErrorKind, Read, Write};
use std::os::unix::net::UnixStream;
use std::process::{Command, Stdio};
use std::sync::mpsc::{Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::rfb;

/// Run a command; Ok(stdout), or Err(what it said: stderr, else stdout).
pub fn run(argv: &[String]) -> Result<String, String> {
    let (cmd, args) = argv.split_first().ok_or("nothing to run")?;
    let out = Command::new(cmd)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("{cmd}: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
    if out.status.success() {
        Ok(if stdout.is_empty() { stderr } else { stdout })
    } else if !stderr.is_empty() {
        Err(stderr)
    } else if !stdout.is_empty() {
        Err(stdout)
    } else {
        Err(format!("{cmd} failed ({})", out.status))
    }
}

/// Start a program and leave it running (a terminal window). A thread waits
/// for it, so it does not linger as a zombie once it is closed.
pub fn spawn(argv: &[String]) -> Result<(), String> {
    let (cmd, args) = argv.split_first().ok_or("nothing to run")?;
    let mut child = Command::new(cmd)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("{cmd}: {e}"))?;
    std::thread::spawn(move || child.wait());
    Ok(())
}

/// Does an SSH server answer at `addr` (host:port)? QEMU's NAT forward accepts
/// the connection whether or not the guest runs sshd, and ssh then waits
/// forever for a greeting; this waits three seconds for "SSH-".
pub fn ssh_answers(addr: &str) -> bool {
    use std::net::{TcpStream, ToSocketAddrs};
    let Some(sa) = addr.to_socket_addrs().ok().and_then(|mut a| a.next()) else { return false };
    let Ok(mut s) = TcpStream::connect_timeout(&sa, Duration::from_secs(2)) else { return false };
    s.set_read_timeout(Some(Duration::from_secs(3))).ok();
    let mut b = [0u8; 4];
    s.read_exact(&mut b).is_ok() && &b == b"SSH-"
}

pub fn list_vms() -> Result<Vec<crate::Vm>, String> {
    crate::Vm::parse_list(&run(&[crate::VM.into(), "list".into(), "--json".into()])?)
}

pub fn list_isos() -> Result<Vec<crate::Iso>, String> {
    crate::parse_isos(&run(&[crate::VM.into(), "iso".into(), "list".into(), "--json".into()])?)
}

// ------------------------------------------- dev boxes, containers, services

use crate::containers::{self, Container, Image, QuadletSpec, RunForm, Service};

pub fn home() -> String {
    std::env::var("HOME").unwrap_or_else(|_| "/".into())
}

fn sv(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|p| p.to_string()).collect()
}

pub fn list_containers() -> Result<Vec<Container>, String> {
    containers::parse_ps(&run(&sv(&["podman", "ps", "--all", "--format", "json"]))?)
}

pub fn list_images() -> Result<Vec<Image>, String> {
    containers::parse_images(&run(&sv(&["podman", "images", "--format", "json"]))?)
}

/// NVIDIA's container device is set up (Desktop NVIDIA): offer the GPU.
pub fn has_nvidia() -> bool {
    std::path::Path::new("/dev/nvidiactl").exists()
}

/// Is linger on (the user's services run without a login)?
pub fn lingering() -> bool {
    let user = std::env::var("USER").unwrap_or_default();
    std::path::Path::new("/var/lib/systemd/linger").join(user).exists()
}

/// The unit states, one systemctl call for all of them.
fn unit_states(user: bool, units: &[String]) -> std::collections::HashMap<String, String> {
    let mut out = std::collections::HashMap::new();
    if units.is_empty() {
        return out;
    }
    let mut a = vec!["systemctl".to_string()];
    if user {
        a.push("--user".into());
    }
    a.extend(sv(&["show", "--property=Id,ActiveState"]));
    a.extend(units.iter().cloned());
    let text = run(&a).unwrap_or_default();
    let (mut id, mut state) = (String::new(), String::new());
    for line in text.lines().chain(std::iter::once("")) {
        if let Some(v) = line.strip_prefix("Id=") {
            id = v.to_string();
        } else if let Some(v) = line.strip_prefix("ActiveState=") {
            state = v.to_string();
        } else if line.is_empty() && !id.is_empty() {
            out.insert(std::mem::take(&mut id), std::mem::take(&mut state));
        }
    }
    out
}

/// Quadlet `.container` services: yours (~/.config/containers/systemd), then the
/// system's (/etc/containers/systemd, shown read-only).
pub fn list_services() -> Vec<Service> {
    let mut out = Vec::new();
    for (dir, system) in [(containers::quadlet_dir(&home()), false), ("/etc/containers/systemd".to_string(), true)] {
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        let mut found: Vec<Service> = rd
            .flatten()
            .filter_map(|e| {
                let p = e.path();
                let name = p.file_name()?.to_str()?.strip_suffix(".container")?.to_string();
                let text = std::fs::read_to_string(&p).ok()?;
                Some(containers::parse_quadlet(&name, &p.display().to_string(), &text, system))
            })
            .collect();
        found.sort_by(|a, b| a.name.cmp(&b.name));
        let units: Vec<String> = found.iter().map(|s| format!("{}.service", s.name)).collect();
        let states = unit_states(!system, &units);
        for s in &mut found {
            s.state = states.get(&format!("{}.service", s.name)).cloned().unwrap_or_else(|| "inactive".into());
        }
        out.extend(found);
    }
    out
}

/// Every command run, as lines for the result ("ran: ...").
pub type Log = Vec<String>;

fn step(log: &mut Log, argv: &[String]) -> Result<String, String> {
    log.push(crate::shell_line(argv));
    run(argv)
}

fn write_quadlet(spec: &QuadletSpec, log: &mut Log) -> Result<std::path::PathBuf, String> {
    let dir = std::path::PathBuf::from(containers::quadlet_dir(&home()));
    std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let path = dir.join(spec.file_name());
    if path.exists() {
        return Err(format!("{} already exists", path.display()));
    }
    std::fs::write(&path, spec.render()).map_err(|e| format!("{}: {e}", path.display()))?;
    log.push(format!("wrote {}", path.display()));
    Ok(path)
}

/// Why a service failed to start, from its journal (systemctl itself says little).
fn why_failed(unit: &str, e: String) -> String {
    let tail = run(&sv(&["journalctl", "--user", "-u", unit, "-n", "6", "--no-pager", "-o", "cat"])).unwrap_or_default();
    let lines: Vec<&str> = tail.lines().filter(|l| !l.trim().is_empty()).collect();
    if lines.is_empty() {
        e
    } else {
        format!("{e}\n{}", lines.join("\n"))
    }
}

fn ensure_linger(log: &mut Log) {
    if !lingering() {
        let user = std::env::var("USER").unwrap_or_default();
        let _ = step(log, &sv(&["loginctl", "enable-linger", &user]));
    }
}

/// Run a container, as a Quadlet service or a plain `podman run`.
pub fn run_container(form: &RunForm) -> (Result<String, String>, Log) {
    let mut log = Log::new();
    let r = (|| {
        let spec = form.spec();
        // Pull first: a service start times out long before a big image is in.
        step(&mut log, &sv(&["podman", "pull", &spec.image]))?;
        if !form.service {
            step(&mut log, &form.run_argv())?;
            return Ok(format!("{} is running.", spec.name));
        }
        let path = write_quadlet(&spec, &mut log)?;
        step(&mut log, &sv(&["systemctl", "--user", "daemon-reload"]))?;
        if let Err(e) = step(&mut log, &containers::service_action("start", &spec.unit())) {
            let e = why_failed(&spec.unit(), e);
            let _ = std::fs::remove_file(&path);
            let _ = run(&sv(&["systemctl", "--user", "daemon-reload"]));
            log.push(format!("removed {} again", path.display()));
            return Err(e);
        }
        ensure_linger(&mut log);
        Ok(format!("{} is running as a service, and starts with this computer.", spec.name))
    })();
    (r, log)
}

/// Keep running at boot: the container becomes a Quadlet service with the same
/// image, ports, folders and environment; the original goes once the service runs.
pub fn keep_at_boot(name: &str) -> (Result<String, String>, Log) {
    let mut log = Log::new();
    let r = (|| {
        let inspect = step(&mut log, &sv(&["podman", "container", "inspect", name]))?;
        let mut spec = QuadletSpec::from_inspect(&inspect, &[])?;
        let img = run(&sv(&["podman", "image", "inspect", &spec.image])).unwrap_or_default();
        spec = QuadletSpec::from_inspect(&inspect, &containers::image_env(&img))?;
        let was_running = run(&sv(&["podman", "container", "inspect", "--format", "{{.State.Running}}", name]))
            .map(|o| o.trim() == "true")
            .unwrap_or(false);
        step(&mut log, &sv(&["podman", "stop", name]))?;
        // The service's container takes the same name: the old one is renamed aside.
        let aside = format!("{name}-before-machines");
        step(&mut log, &sv(&["podman", "rename", name, &aside]))?;
        let undo = |log: &mut Log| {
            let _ = step(log, &sv(&["podman", "rename", &aside, name]));
            if was_running {
                let _ = step(log, &sv(&["podman", "start", name]));
            }
        };
        let path = match write_quadlet(&spec, &mut log) {
            Ok(p) => p,
            Err(e) => {
                undo(&mut log);
                return Err(e);
            }
        };
        let started = step(&mut log, &sv(&["systemctl", "--user", "daemon-reload"]))
            .and_then(|_| step(&mut log, &containers::service_action("start", &spec.unit())));
        if let Err(e) = started {
            let e = why_failed(&spec.unit(), e);
            let _ = std::fs::remove_file(&path);
            let _ = run(&sv(&["systemctl", "--user", "daemon-reload"]));
            log.push(format!("removed {} again", path.display()));
            undo(&mut log);
            return Err(format!("The service did not start, so {name} was put back as it was: {e}"));
        }
        let _ = step(&mut log, &sv(&["podman", "rm", "--force", &aside]));
        ensure_linger(&mut log);
        Ok(format!("{name} is now a service: it starts with this computer. Its file: {}", path.display()))
    })();
    (r, log)
}

/// Stop a service and delete its file (yours only); volumes are kept.
pub fn remove_service(svc: &Service) -> (Result<String, String>, Log) {
    let mut log = Log::new();
    let r = (|| {
        if svc.system {
            return Err("a system service: remove it with sudo".into());
        }
        let unit = format!("{}.service", svc.name);
        let _ = step(&mut log, &containers::service_action("stop", &unit));
        std::fs::remove_file(&svc.path).map_err(|e| format!("{}: {e}", svc.path))?;
        log.push(format!("deleted {}", svc.path));
        step(&mut log, &sv(&["systemctl", "--user", "daemon-reload"]))?;
        Ok(format!("{} is removed. Its data (volumes and folders) is kept.", svc.name))
    })();
    (r, log)
}

// ------------------------------------------------------------- the screen

#[derive(Debug, Clone)]
pub enum ScreenEvent {
    Ready { width: u16, height: u16, name: String },
    /// The whole framebuffer, RGBA.
    Frame { width: u16, height: u16, rgba: Vec<u8> },
    Bell,
    /// The connection ended: None when the VM went away, Some(why) on an error.
    Closed(Option<String>),
}

#[derive(Debug, Clone)]
pub enum Input {
    Key { down: bool, keysym: u32, scancode: u32 },
    Pointer { buttons: u8, x: u16, y: u16 },
    Close,
}

/// Frames are sent at most this often; a guest redrawing flat out would
/// otherwise copy megabytes per update into the window.
const FRAME_EVERY: Duration = Duration::from_millis(33);

/// Connect to a VM's `vnc.sock`. `on` gets every event (from a background
/// thread); the Sender takes the keyboard and pointer. Dropping it, or
/// sending `Close`, ends the connection.
pub fn open_screen(
    socket: &str,
    on: impl Fn(ScreenEvent) + Send + 'static,
) -> Result<Sender<Input>, String> {
    let sock = UnixStream::connect(socket).map_err(|e| match e.kind() {
        ErrorKind::NotFound | ErrorKind::ConnectionRefused => "the VM is not running".to_string(),
        ErrorKind::PermissionDenied => "this VM's screen belongs to another account".to_string(),
        _ => format!("cannot open the screen: {e}"),
    })?;
    sock.set_read_timeout(Some(Duration::from_millis(30))).ok();
    let client = Arc::new(Mutex::new(rfb::Client::new()));
    let (tx, rx) = std::sync::mpsc::channel::<Input>();

    let writer = sock.try_clone().map_err(|e| e.to_string())?;
    let c2 = client.clone();
    std::thread::spawn(move || input_loop(rx, writer, c2));

    std::thread::spawn(move || {
        let mut sock = sock;
        let mut buf = vec![0u8; 1 << 16];
        let mut last = Instant::now() - FRAME_EVERY;
        let mut pending = false;
        let end = loop {
            match sock.read(&mut buf) {
                Ok(0) => break None,
                Ok(n) => {
                    let mut c = client.lock().unwrap();
                    let events = match c.feed(&buf[..n]) {
                        Ok(e) => e,
                        Err(e) => break Some(e),
                    };
                    let out = c.take_output();
                    if !out.is_empty() && sock.write_all(&out).is_err() {
                        break None;
                    }
                    for ev in events {
                        match ev {
                            rfb::Event::Ready { width, height, name } => on(ScreenEvent::Ready { width, height, name }),
                            rfb::Event::Resized { .. } | rfb::Event::Frame => pending = true,
                            rfb::Event::Bell => on(ScreenEvent::Bell),
                        }
                    }
                }
                Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut | ErrorKind::Interrupted) => {}
                Err(_) => break None,
            }
            if pending && last.elapsed() >= FRAME_EVERY {
                let c = client.lock().unwrap();
                on(ScreenEvent::Frame { width: c.width, height: c.height, rgba: c.framebuffer.clone() });
                pending = false;
                last = Instant::now();
            }
        };
        let _ = sock.shutdown(std::net::Shutdown::Both);
        on(ScreenEvent::Closed(end));
    });
    Ok(tx)
}

fn input_loop(rx: Receiver<Input>, mut sock: UnixStream, client: Arc<Mutex<rfb::Client>>) {
    while let Ok(input) = rx.recv() {
        let out = {
            let mut c = client.lock().unwrap();
            if !c.connected() {
                continue;
            }
            match input {
                Input::Key { down, keysym, scancode } => c.key(down, keysym, scancode),
                Input::Pointer { buttons, x, y } => c.pointer(buttons, x, y),
                Input::Close => break,
            }
            c.take_output()
        };
        if sock.write_all(&out).is_err() {
            return;
        }
    }
    let _ = sock.shutdown(std::net::Shutdown::Both);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::sync::mpsc;

    fn fake_qemu(path: &std::path::Path) -> std::thread::JoinHandle<Vec<u8>> {
        let l = UnixListener::bind(path).unwrap();
        std::thread::spawn(move || {
            let (mut c, _) = l.accept().unwrap();
            c.write_all(b"RFB 003.008\n").unwrap();
            let mut b = [0u8; 12];
            c.read_exact(&mut b).unwrap();
            c.write_all(&[1, 1]).unwrap();
            c.read_exact(&mut b[..1]).unwrap();
            c.write_all(&[0, 0, 0, 0]).unwrap();
            c.read_exact(&mut b[..1]).unwrap();
            let mut init = vec![0, 2, 0, 1, 32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0, 0, 0, 0, 1, b'q'];
            // A whole update right behind it: two pixels, and the QEMU key pseudo-rect.
            init.extend_from_slice(&[0, 0, 0, 2, 0, 0, 0, 0, 0, 2, 0, 1, 0, 0, 0, 0, 1, 2, 3, 0, 4, 5, 6, 0]);
            init.extend_from_slice(&[0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xff, 0xfe, 0xfe]);
            c.write_all(&init).unwrap();
            // Read what the client sends until it hangs up.
            let mut got = Vec::new();
            let _ = c.read_to_end(&mut got);
            got
        })
    }

    #[test]
    fn screen_connects_draws_and_sends_input() {
        let dir = std::env::temp_dir().join(format!("machines-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("vnc.sock");
        let _ = std::fs::remove_file(&path);
        let server = fake_qemu(&path);
        let (etx, erx) = mpsc::channel();
        let tx = open_screen(path.to_str().unwrap(), move |e| {
            let _ = etx.send(e);
        })
        .unwrap();
        let mut frame = None;
        for _ in 0..50 {
            match erx.recv_timeout(Duration::from_millis(200)) {
                Ok(ScreenEvent::Frame { width, height, rgba }) => {
                    frame = Some((width, height, rgba));
                    break;
                }
                Ok(_) => {}
                Err(_) => {}
            }
        }
        let (w, h, rgba) = frame.expect("a frame");
        assert_eq!((w, h), (2, 1));
        assert_eq!(rgba, vec![1, 2, 3, 255, 4, 5, 6, 255]);
        tx.send(Input::Key { down: true, keysym: 0x61, scancode: 0x1e }).unwrap();
        tx.send(Input::Pointer { buttons: 1, x: 1, y: 0 }).unwrap();
        tx.send(Input::Close).unwrap();
        let got = server.join().unwrap();
        // After the format, encodings and update requests: our key (QEMU form) and pointer.
        let tail = &got[got.len() - 18..];
        assert_eq!(&tail[..12], &[255, 0, 0, 1, 0, 0, 0, 0x61, 0, 0, 0, 0x1e]);
        assert_eq!(&tail[12..], &[5, 1, 0, 1, 0, 0]);
        assert!(matches!(erx.recv_timeout(Duration::from_secs(2)), Ok(ScreenEvent::Closed(None)) | Ok(ScreenEvent::Frame { .. })));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn ssh_answers_only_with_a_greeting() {
        use std::net::TcpListener;
        let quiet = TcpListener::bind("127.0.0.1:0").unwrap();
        let qa = quiet.local_addr().unwrap().to_string();
        let talks = TcpListener::bind("127.0.0.1:0").unwrap();
        let ta = talks.local_addr().unwrap().to_string();
        std::thread::spawn(move || {
            let (mut c, _) = talks.accept().unwrap();
            c.write_all(b"SSH-2.0-OpenSSH_10\r\n").unwrap();
            std::thread::sleep(Duration::from_millis(200));
        });
        assert!(ssh_answers(&ta));
        assert!(!ssh_answers(&qa)); // accepts, says nothing: like QEMU's forward with no sshd
        drop(quiet);
        assert!(!ssh_answers("127.0.0.1:1"));
    }

    #[test]
    fn a_missing_socket_says_the_vm_is_not_running() {
        let e = open_screen("/nonexistent/vnc.sock", |_| {}).unwrap_err();
        assert!(e.contains("not running"));
    }

    #[test]
    fn run_returns_stdout_or_the_error_text() {
        assert_eq!(run(&["sh".into(), "-c".into(), "echo hi".into()]).unwrap(), "hi");
        assert_eq!(run(&["sh".into(), "-c".into(), "echo nope >&2; exit 2".into()]).unwrap_err(), "nope");
        assert!(run(&["/no/such/tool".into()]).is_err());
    }
}
