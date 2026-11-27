//! A small RFB (VNC) 3.8 client for QEMU's VNC server, written for this app.
//!
//! QEMU is local and the screen is a Unix socket only its owner can open
//! (pengyos-vm 0.5.0), so this needs only: security type None, the Raw encoding,
//! DesktopSize (the guest changes resolution) and QEMU's extended key event
//! (scancodes, so every keyboard layout reaches the guest as typed). It is a
//! state machine over bytes: [`Client::feed`] takes what the socket gave and
//! returns events, and every message to send is queued in [`Client::take_output`].
//! No I/O here, so it is tested against byte streams.

/// Encodings and pseudo-encodings we ask for.
const ENC_RAW: i32 = 0;
const ENC_DESKTOP_SIZE: i32 = -223;
const ENC_QEMU_EXT_KEY: i32 = -258;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Event {
    /// Connected: the screen's size and the name QEMU gives it.
    Ready { width: u16, height: u16, name: String },
    /// The guest changed resolution; the framebuffer was cleared to this size.
    Resized { width: u16, height: u16 },
    /// A framebuffer update finished: redraw.
    Frame,
    Bell,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Phase {
    Version,
    SecurityTypes,
    SecurityResult,
    ServerInit,
    Normal,
    /// Inside a FramebufferUpdate with this many rectangles to go.
    Rects(u16),
}

pub struct Client {
    phase: Phase,
    buf: Vec<u8>,
    out: Vec<u8>,
    pub width: u16,
    pub height: u16,
    /// RGBA, width * height * 4.
    pub framebuffer: Vec<u8>,
    /// The server takes QEMU extended key events (scancodes).
    pub qemu_keys: bool,
}

impl Default for Client {
    fn default() -> Self {
        Self::new()
    }
}

fn be16(b: &[u8]) -> u16 {
    u16::from_be_bytes([b[0], b[1]])
}

fn be32(b: &[u8]) -> u32 {
    u32::from_be_bytes([b[0], b[1], b[2], b[3]])
}

impl Client {
    pub fn new() -> Self {
        Client {
            phase: Phase::Version,
            buf: Vec::new(),
            out: Vec::new(),
            width: 0,
            height: 0,
            framebuffer: Vec::new(),
            qemu_keys: false,
        }
    }

    /// Bytes to write to the socket (and forget them).
    pub fn take_output(&mut self) -> Vec<u8> {
        std::mem::take(&mut self.out)
    }

    pub fn connected(&self) -> bool {
        matches!(self.phase, Phase::Normal | Phase::Rects(_))
    }

    /// Bytes from the socket. Err is fatal (the message to show).
    pub fn feed(&mut self, data: &[u8]) -> Result<Vec<Event>, String> {
        let mut buf = std::mem::take(&mut self.buf);
        buf.extend_from_slice(data);
        let mut events = Vec::new();
        let mut at = 0;
        let result = loop {
            match self.step(&buf[at..], &mut events) {
                Ok(Some(used)) => at += used,
                Ok(None) => break Ok(events),
                Err(e) => break Err(e),
            }
        };
        buf.drain(..at);
        self.buf = buf;
        result
    }

    /// One message from the front of `b`: Some(bytes used), or None to wait for more.
    fn step(&mut self, b: &[u8], events: &mut Vec<Event>) -> Result<Option<usize>, String> {
        match self.phase {
            Phase::Version => {
                if b.len() < 12 {
                    return Ok(None);
                }
                if &b[..4] != b"RFB " {
                    return Err("this is not a VNC server".into());
                }
                let minor: u32 = std::str::from_utf8(&b[8..11]).ok().and_then(|s| s.parse().ok()).unwrap_or(0);
                if &b[4..7] != b"003" || minor < 8 {
                    return Err(format!("VNC version {} is too old", String::from_utf8_lossy(&b[..11])));
                }
                self.out.extend_from_slice(b"RFB 003.008\n");
                self.phase = Phase::SecurityTypes;
                Ok(Some(12))
            }
            Phase::SecurityTypes => {
                if b.is_empty() {
                    return Ok(None);
                }
                let n = b[0] as usize;
                if n == 0 {
                    // A failure: u32 length and the reason.
                    if b.len() < 5 {
                        return Ok(None);
                    }
                    let len = be32(&b[1..5]) as usize;
                    if b.len() < 5 + len {
                        return Ok(None);
                    }
                    return Err(format!("the VM refused: {}", String::from_utf8_lossy(&b[5..5 + len])));
                }
                if b.len() < 1 + n {
                    return Ok(None);
                }
                if !b[1..1 + n].contains(&1) {
                    return Err("the VM's screen wants a password; Machines only opens pengyos-vm screens".into());
                }
                self.out.push(1);
                self.phase = Phase::SecurityResult;
                Ok(Some(1 + n))
            }
            Phase::SecurityResult => {
                if b.len() < 4 {
                    return Ok(None);
                }
                if be32(b) != 0 {
                    if b.len() < 8 {
                        return Ok(None);
                    }
                    let len = be32(&b[4..8]) as usize;
                    if b.len() < 8 + len {
                        return Ok(None);
                    }
                    return Err(format!("the VM refused: {}", String::from_utf8_lossy(&b[8..8 + len])));
                }
                self.out.push(1); // ClientInit: shared, so a second viewer does not kick this one
                self.phase = Phase::ServerInit;
                Ok(Some(4))
            }
            Phase::ServerInit => {
                if b.len() < 24 {
                    return Ok(None);
                }
                let len = be32(&b[20..24]) as usize;
                if b.len() < 24 + len {
                    return Ok(None);
                }
                let (w, h) = (be16(&b[0..2]), be16(&b[2..4]));
                let name = String::from_utf8_lossy(&b[24..24 + len]).into_owned();
                self.resize(w, h);
                self.set_pixel_format();
                self.set_encodings();
                self.request(false);
                self.phase = Phase::Normal;
                events.push(Event::Ready { width: w, height: h, name });
                Ok(Some(24 + len))
            }
            Phase::Normal => {
                if b.is_empty() {
                    return Ok(None);
                }
                match b[0] {
                    0 => {
                        if b.len() < 4 {
                            return Ok(None);
                        }
                        let n = be16(&b[2..4]);
                        self.phase = Phase::Rects(n);
                        if n == 0 {
                            self.end_update(events);
                        }
                        Ok(Some(4))
                    }
                    1 => {
                        // SetColourMapEntries: never asked for (true colour), skipped.
                        if b.len() < 6 {
                            return Ok(None);
                        }
                        let n = be16(&b[4..6]) as usize;
                        if b.len() < 6 + n * 6 {
                            return Ok(None);
                        }
                        Ok(Some(6 + n * 6))
                    }
                    2 => {
                        events.push(Event::Bell);
                        Ok(Some(1))
                    }
                    3 => {
                        if b.len() < 8 {
                            return Ok(None);
                        }
                        let len = be32(&b[4..8]) as usize;
                        if b.len() < 8 + len {
                            return Ok(None);
                        }
                        Ok(Some(8 + len)) // ServerCutText: no clipboard in v1
                    }
                    t => Err(format!("the VM sent an unknown VNC message ({t})")),
                }
            }
            Phase::Rects(left) => {
                if b.len() < 12 {
                    return Ok(None);
                }
                let (x, y, w, h) = (be16(&b[0..2]), be16(&b[2..4]), be16(&b[4..6]), be16(&b[6..8]));
                let enc = be32(&b[8..12]) as i32;
                let used = match enc {
                    ENC_RAW => {
                        let n = w as usize * h as usize * 4;
                        if b.len() < 12 + n {
                            return Ok(None);
                        }
                        self.blit(x, y, w, h, &b[12..12 + n]);
                        12 + n
                    }
                    ENC_DESKTOP_SIZE => {
                        self.resize(w, h);
                        events.push(Event::Resized { width: w, height: h });
                        12
                    }
                    ENC_QEMU_EXT_KEY => {
                        self.qemu_keys = true;
                        12
                    }
                    e => return Err(format!("the VM sent an encoding Machines did not ask for ({e})")),
                };
                if left <= 1 {
                    self.end_update(events);
                } else {
                    self.phase = Phase::Rects(left - 1);
                }
                Ok(Some(used))
            }
        }
    }

    fn end_update(&mut self, events: &mut Vec<Event>) {
        self.phase = Phase::Normal;
        events.push(Event::Frame);
        self.request(true);
    }

    fn resize(&mut self, w: u16, h: u16) {
        self.width = w;
        self.height = h;
        let mut fb = vec![0u8; w as usize * h as usize * 4];
        fb.chunks_exact_mut(4).for_each(|p| p[3] = 255);
        self.framebuffer = fb;
    }

    /// Raw pixels in our format (R, G, B, unused) into the RGBA framebuffer.
    fn blit(&mut self, x: u16, y: u16, w: u16, h: u16, px: &[u8]) {
        let (fw, fh) = (self.width as usize, self.height as usize);
        let (x, y, w, h) = (x as usize, y as usize, w as usize, h as usize);
        for row in 0..h {
            if y + row >= fh {
                break;
            }
            let cols = w.min(fw.saturating_sub(x));
            let src = &px[row * w * 4..row * w * 4 + cols * 4];
            let start = ((y + row) * fw + x) * 4;
            let dst = &mut self.framebuffer[start..start + cols * 4];
            dst.copy_from_slice(src);
            dst.chunks_exact_mut(4).for_each(|p| p[3] = 255);
        }
    }

    /// 32 bits per pixel, little-endian, red in the low byte: the bytes land as
    /// R, G, B, x, so a Raw rectangle is RGBA once the fourth byte is set.
    fn set_pixel_format(&mut self) {
        let mut m = vec![0u8, 0, 0, 0];
        m.extend_from_slice(&[32, 24, 0, 1]); // bpp, depth, big-endian no, true colour
        m.extend_from_slice(&255u16.to_be_bytes());
        m.extend_from_slice(&255u16.to_be_bytes());
        m.extend_from_slice(&255u16.to_be_bytes());
        m.extend_from_slice(&[0, 8, 16, 0, 0, 0]); // shifts R G B, padding
        self.out.extend_from_slice(&m);
    }

    fn set_encodings(&mut self) {
        let encs = [ENC_RAW, ENC_DESKTOP_SIZE, ENC_QEMU_EXT_KEY];
        self.out.extend_from_slice(&[2, 0]);
        self.out.extend_from_slice(&(encs.len() as u16).to_be_bytes());
        for e in encs {
            self.out.extend_from_slice(&e.to_be_bytes());
        }
    }

    fn request(&mut self, incremental: bool) {
        self.out.extend_from_slice(&[3, incremental as u8, 0, 0, 0, 0]);
        self.out.extend_from_slice(&self.width.to_be_bytes());
        self.out.extend_from_slice(&self.height.to_be_bytes());
    }

    /// A key, by scancode when QEMU takes them (the guest's own layout decides
    /// what it means), else by keysym.
    pub fn key(&mut self, down: bool, keysym: u32, scancode: u32) {
        if self.qemu_keys && scancode != 0 {
            self.out.extend_from_slice(&[255, 0]);
            self.out.extend_from_slice(&(down as u16).to_be_bytes());
            self.out.extend_from_slice(&keysym.to_be_bytes());
            self.out.extend_from_slice(&scancode.to_be_bytes());
        } else if keysym != 0 {
            self.out.extend_from_slice(&[4, down as u8, 0, 0]);
            self.out.extend_from_slice(&keysym.to_be_bytes());
        }
    }

    /// The pointer at framebuffer (x, y) with these buttons down (bit 0 left,
    /// 1 middle, 2 right, 3 wheel up, 4 wheel down).
    pub fn pointer(&mut self, buttons: u8, x: u16, y: u16) {
        self.out.extend_from_slice(&[5, buttons]);
        self.out.extend_from_slice(&x.min(self.width.saturating_sub(1)).to_be_bytes());
        self.out.extend_from_slice(&y.min(self.height.saturating_sub(1)).to_be_bytes());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn server_init(w: u16, h: u16, name: &str) -> Vec<u8> {
        let mut b = Vec::new();
        b.extend_from_slice(&w.to_be_bytes());
        b.extend_from_slice(&h.to_be_bytes());
        b.extend_from_slice(&[32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0]);
        b.extend_from_slice(&(name.len() as u32).to_be_bytes());
        b.extend_from_slice(name.as_bytes());
        b
    }

    fn handshake(c: &mut Client, w: u16, h: u16) {
        assert!(c.feed(b"RFB 003.008\n").unwrap().is_empty());
        assert_eq!(c.take_output(), b"RFB 003.008\n");
        c.feed(&[1, 1]).unwrap();
        assert_eq!(c.take_output(), [1]);
        c.feed(&[0, 0, 0, 0]).unwrap();
        assert_eq!(c.take_output(), [1]);
        let ev = c.feed(&server_init(w, h, "QEMU (deb)")).unwrap();
        assert_eq!(ev, vec![Event::Ready { width: w, height: h, name: "QEMU (deb)".into() }]);
    }

    #[test]
    fn handshake_sets_format_encodings_and_asks_for_the_screen() {
        let mut c = Client::new();
        handshake(&mut c, 4, 2);
        let out = c.take_output();
        assert_eq!(&out[..4], &[0, 0, 0, 0]); // SetPixelFormat
        assert_eq!(&out[4..8], &[32, 24, 0, 1]);
        assert_eq!(&out[14..17], &[0, 8, 16]); // R low byte: RGBA in memory
        assert_eq!(&out[20..24], &[2, 0, 0, 3]); // SetEncodings, 3 of them
        assert_eq!(&out[24..28], &0i32.to_be_bytes());
        assert_eq!(&out[28..32], &(-223i32).to_be_bytes());
        assert_eq!(&out[32..36], &(-258i32).to_be_bytes());
        assert_eq!(&out[36..], &[3, 0, 0, 0, 0, 0, 0, 4, 0, 2]); // full update request
        assert!(c.connected() && c.framebuffer.len() == 4 * 2 * 4);
    }

    #[test]
    fn handshake_byte_by_byte() {
        let mut stream = b"RFB 003.008\n".to_vec();
        stream.extend_from_slice(&[1, 1, 0, 0, 0, 0]);
        stream.extend_from_slice(&server_init(8, 8, "x"));
        let mut c = Client::new();
        let mut events = vec![];
        for b in stream {
            events.extend(c.feed(&[b]).unwrap());
        }
        assert_eq!(events.len(), 1);
        assert!(c.connected());
    }

    #[test]
    fn password_servers_and_refusals_are_errors() {
        let mut c = Client::new();
        c.feed(b"RFB 003.008\n").unwrap();
        assert!(c.feed(&[1, 2]).unwrap_err().contains("password"));
        let mut c = Client::new();
        c.feed(b"RFB 003.008\n").unwrap();
        let mut refuse = vec![0, 0, 0, 0, 4];
        refuse.extend_from_slice(b"busy");
        assert!(c.feed(&refuse).unwrap_err().contains("busy"));
        assert!(Client::new().feed(b"HTTP/1.1 200").unwrap_err().contains("not a VNC"));
        assert!(Client::new().feed(b"RFB 003.003\n").unwrap_err().contains("too old"));
    }

    fn rect(x: u16, y: u16, w: u16, h: u16, enc: i32) -> Vec<u8> {
        let mut b = Vec::new();
        for v in [x, y, w, h] {
            b.extend_from_slice(&v.to_be_bytes());
        }
        b.extend_from_slice(&enc.to_be_bytes());
        b
    }

    #[test]
    fn raw_rectangles_land_in_the_framebuffer_as_rgba() {
        let mut c = Client::new();
        handshake(&mut c, 4, 2);
        c.take_output();
        let mut upd = vec![0, 0, 0, 2];
        upd.extend(rect(1, 0, 2, 1, ENC_RAW));
        upd.extend_from_slice(&[10, 20, 30, 0, 40, 50, 60, 0]);
        upd.extend(rect(0, 0, 0, 0, ENC_QEMU_EXT_KEY));
        // Split mid-rectangle: nothing is drawn until it is complete.
        assert!(c.feed(&upd[..10]).unwrap().is_empty());
        let ev = c.feed(&upd[10..]).unwrap();
        assert_eq!(ev, vec![Event::Frame]);
        assert_eq!(&c.framebuffer[4..12], &[10, 20, 30, 255, 40, 50, 60, 255]);
        assert_eq!(&c.framebuffer[0..4], &[0, 0, 0, 255]);
        assert!(c.qemu_keys);
        assert_eq!(c.take_output(), vec![3, 1, 0, 0, 0, 0, 0, 4, 0, 2]); // the next, incremental
    }

    #[test]
    fn desktop_size_resizes_and_other_messages_are_skipped() {
        let mut c = Client::new();
        handshake(&mut c, 4, 2);
        let mut s = vec![0, 0, 0, 1];
        s.extend(rect(0, 0, 640, 480, ENC_DESKTOP_SIZE));
        s.push(2); // bell
        s.extend_from_slice(&[3, 0, 0, 0, 0, 0, 0, 2, b'h', b'i']); // cut text
        s.extend_from_slice(&[0, 0, 0, 0]); // an empty update
        let ev = c.feed(&s).unwrap();
        assert_eq!(ev, vec![Event::Resized { width: 640, height: 480 }, Event::Frame, Event::Bell, Event::Frame]);
        assert_eq!(c.framebuffer.len(), 640 * 480 * 4);
        assert!(c.feed(&[9]).unwrap_err().contains("unknown"));
    }

    #[test]
    fn keys_use_scancodes_once_qemu_says_so() {
        let mut c = Client::new();
        handshake(&mut c, 4, 2);
        c.take_output();
        c.key(true, 0x61, 0x1e);
        assert_eq!(c.take_output(), vec![4, 1, 0, 0, 0, 0, 0, 0x61]);
        c.qemu_keys = true;
        c.key(false, 0x61, 0x1e);
        assert_eq!(c.take_output(), vec![255, 0, 0, 0, 0, 0, 0, 0x61, 0, 0, 0, 0x1e]);
        c.pointer(1, 100, 1);
        assert_eq!(c.take_output(), vec![5, 1, 0, 3, 0, 1]); // clamped to the screen
    }
}
