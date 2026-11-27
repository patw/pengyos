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

/// Start a program and leave it running (a terminal window).
pub fn spawn(argv: &[String]) -> Result<(), String> {
    let (cmd, args) = argv.split_first().ok_or("nothing to run")?;
    Command::new(cmd)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("{cmd}: {e}"))
}

pub fn list_vms() -> Result<Vec<crate::Vm>, String> {
    crate::Vm::parse_list(&run(&[crate::VM.into(), "list".into(), "--json".into()])?)
}

pub fn list_isos() -> Result<Vec<crate::Iso>, String> {
    crate::parse_isos(&run(&[crate::VM.into(), "iso".into(), "list".into(), "--json".into()])?)
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
