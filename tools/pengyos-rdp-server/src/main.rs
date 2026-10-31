//! pengyos-rdp-server: share the running COSMIC session over RDP.
//!
//! Portal RemoteDesktop + ScreenCast session -> PipeWire frames -> ironrdp-server.
//! Input goes back through the portal's Notify* calls (cosmic-portal forwards them to
//! cosmic-comp over libei). Started by the user unit pengyos-rdp-server.service, which
//! `pengyos-remote on` enables; the configuration is that tool's (spec.md).
//!
//! Everything is read from ~/.config/pengyos-remote (the user's own, 0700):
//!   password   the RDP password (0600; not the login password)
//!   cert.pem / key.pem   the TLS identity, made by `pengyos-remote on`
//! The RDP user name is the login name. Env overrides (tests, debugging):
//!   RDP_ADDR (default 0.0.0.0:3389), RDP_USER, RDP_PASS_FILE, RDP_CERT, RDP_KEY.

use std::num::{NonZeroU16, NonZeroUsize};
use std::os::fd::OwnedFd;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use ashpd::desktop::PersistMode;
use ashpd::desktop::remote_desktop::{
    Axis, DeviceType, KeyState, RemoteDesktop, SelectDevicesOptions,
};
use ashpd::desktop::screencast::{CursorMode, Screencast, SelectSourcesOptions, SourceType};
use ironrdp_server::{
    BitmapUpdate, Credentials, DesktopSize, DisplayUpdate, KeyboardEvent, MouseEvent, PixelFormat,
    RGBAPointer, RdpServer, RdpServerDisplay, RdpServerDisplayUpdates, RdpServerInputHandler,
    TlsIdentityCtx,
};
use ashpd::enumflags2::BitFlags;
use pipewire as pw;
use pw::spa;
use tokio::sync::{mpsc, watch};

struct Frame {
    width: u16,
    height: u16,
    stride: usize,
    format: PixelFormat,
    data: bytes::Bytes,
}

/// SPA video format -> IronRDP pixel format with the same byte order in memory.
fn rdp_format(f: spa::param::video::VideoFormat) -> Option<PixelFormat> {
    use spa::param::video::VideoFormat as V;
    Some(match f {
        V::RGBA => PixelFormat::RgbA32,
        V::RGBx => PixelFormat::RgbX32,
        V::BGRA => PixelFormat::BgrA32,
        V::BGRx => PixelFormat::BgrX32,
        _ => return None,
    })
}

type FrameRx = watch::Receiver<Option<Arc<Frame>>>;

/// Cursor shape from the portal's cursor metadata, already in RDP pointer layout
/// (32bpp BGRA, rows bottom-up). `None` = hidden.
#[derive(PartialEq)]
struct Cursor {
    width: u16,
    height: u16,
    hot_x: u16,
    hot_y: u16,
    data: Vec<u8>,
}

type CursorRx = watch::Receiver<Option<Arc<Cursor>>>;

/// RGBAPointer carries at most 96x96 (MS-RDPBCGR 2.2.9.1.1.4.4).
const MAX_POINTER: u32 = 96;

// ---------------------------------------------------------------- display

struct Display {
    size: DesktopSize,
    frames: FrameRx,
    cursor: CursorRx,
}

struct Updates {
    frames: FrameRx,
    cursor: CursorRx,
}

#[async_trait::async_trait]
impl RdpServerDisplay for Display {
    async fn size(&mut self) -> DesktopSize {
        self.size
    }

    async fn updates(&mut self) -> anyhow::Result<Box<dyn RdpServerDisplayUpdates>> {
        let mut frames = self.frames.clone();
        let mut cursor = self.cursor.clone();
        // Hand the current frame and cursor out immediately on (re)connect.
        frames.mark_changed();
        cursor.mark_changed();
        Ok(Box::new(Updates { frames, cursor }))
    }
}

#[async_trait::async_trait]
impl RdpServerDisplayUpdates for Updates {
    async fn next_update(&mut self) -> anyhow::Result<Option<DisplayUpdate>> {
        loop {
            tokio::select! {
                biased;
                changed = self.cursor.changed() => {
                    if changed.is_err() {
                        return Ok(None);
                    }
                    let update = match self.cursor.borrow_and_update().as_deref() {
                        None => DisplayUpdate::HidePointer,
                        Some(c) => DisplayUpdate::RGBAPointer(RGBAPointer {
                            cache_index: 0,
                            width: c.width,
                            height: c.height,
                            hot_x: c.hot_x,
                            hot_y: c.hot_y,
                            data: c.data.clone(),
                        }),
                    };
                    return Ok(Some(update));
                }
                changed = self.frames.changed() => {
                    if changed.is_err() {
                        return Ok(None);
                    }
                }
            }
            let Some(f) = self.frames.borrow_and_update().clone() else { continue };
            tracing::debug!(w = f.width, h = f.height, stride = f.stride, len = f.data.len(), "frame -> rdp");
            return Ok(Some(DisplayUpdate::Bitmap(BitmapUpdate {
                x: 0,
                y: 0,
                width: NonZeroU16::new(f.width).context("zero width")?,
                height: NonZeroU16::new(f.height).context("zero height")?,
                format: f.format,
                data: f.data.clone(),
                stride: NonZeroUsize::new(f.stride).context("zero stride")?,
            })));
        }
    }
}

// ---------------------------------------------------------------- input

enum Input {
    Key { code: i32, pressed: bool },
    Keysym { sym: i32, pressed: bool },
    Motion { x: f64, y: f64 },
    Button { button: i32, pressed: bool },
    Scroll { axis: Axis, steps: i32 },
}

struct InputHandler {
    tx: mpsc::UnboundedSender<Input>,
}

/// RDP set-1 scancode -> evdev keycode. Non-extended codes are identical; the
/// 0xE0-prefixed ones need a table.
fn evdev_key(code: u8, extended: bool) -> Option<i32> {
    if !extended {
        return Some(code as i32);
    }
    Some(match code {
        0x1C => 96,  // KP_ENTER
        0x1D => 97,  // RIGHTCTRL
        0x35 => 98,  // KP_SLASH
        0x37 => 99,  // SYSRQ
        0x38 => 100, // RIGHTALT
        0x47 => 102, // HOME
        0x48 => 103, // UP
        0x49 => 104, // PAGEUP
        0x4B => 105, // LEFT
        0x4D => 106, // RIGHT
        0x4F => 107, // END
        0x50 => 108, // DOWN
        0x51 => 109, // PAGEDOWN
        0x52 => 110, // INSERT
        0x53 => 111, // DELETE
        0x5B => 125, // LEFTMETA
        0x5C => 126, // RIGHTMETA
        0x5D => 127, // COMPOSE (menu)
        _ => return None,
    })
}

fn unicode_keysym(cp: u16) -> i32 {
    // Latin-1 keysyms equal the code point; everything else uses the Unicode range.
    if (0x20..=0x7e).contains(&cp) || (0xa0..=0xff).contains(&cp) {
        cp as i32
    } else {
        0x0100_0000 | cp as i32
    }
}

impl RdpServerInputHandler for InputHandler {
    fn keyboard(&mut self, event: KeyboardEvent) {
        let ev = match event {
            KeyboardEvent::Pressed { code, extended } => {
                evdev_key(code, extended).map(|code| Input::Key { code, pressed: true })
            }
            KeyboardEvent::Released { code, extended } => {
                evdev_key(code, extended).map(|code| Input::Key { code, pressed: false })
            }
            KeyboardEvent::UnicodePressed(cp) => {
                Some(Input::Keysym { sym: unicode_keysym(cp), pressed: true })
            }
            KeyboardEvent::UnicodeReleased(cp) => {
                Some(Input::Keysym { sym: unicode_keysym(cp), pressed: false })
            }
            KeyboardEvent::Synchronize(_) => None,
        };
        if let Some(ev) = ev {
            let _ = self.tx.send(ev);
        }
    }

    fn mouse(&mut self, event: MouseEvent) {
        const BTN_LEFT: i32 = 0x110;
        const BTN_RIGHT: i32 = 0x111;
        const BTN_MIDDLE: i32 = 0x112;
        const BTN_SIDE: i32 = 0x113;
        const BTN_EXTRA: i32 = 0x114;
        let b = |button, pressed| Some(Input::Button { button, pressed });
        let ev = match event {
            MouseEvent::Move { x, y } => Some(Input::Motion { x: x as f64, y: y as f64 }),
            MouseEvent::LeftPressed => b(BTN_LEFT, true),
            MouseEvent::LeftReleased => b(BTN_LEFT, false),
            MouseEvent::RightPressed => b(BTN_RIGHT, true),
            MouseEvent::RightReleased => b(BTN_RIGHT, false),
            MouseEvent::MiddlePressed => b(BTN_MIDDLE, true),
            MouseEvent::MiddleReleased => b(BTN_MIDDLE, false),
            MouseEvent::Button4Pressed => b(BTN_SIDE, true),
            MouseEvent::Button4Released => b(BTN_SIDE, false),
            MouseEvent::Button5Pressed => b(BTN_EXTRA, true),
            MouseEvent::Button5Released => b(BTN_EXTRA, false),
            // RDP: positive = away from the user (up); portal: positive = down.
            MouseEvent::VerticalScroll { value } if value != 0 => {
                Some(Input::Scroll { axis: Axis::Vertical, steps: -(value.signum() as i32) })
            }
            MouseEvent::Scroll { y, .. } if y != 0 => {
                Some(Input::Scroll { axis: Axis::Vertical, steps: -y.signum() })
            }
            MouseEvent::Scroll { x, .. } if x != 0 => {
                Some(Input::Scroll { axis: Axis::Horizontal, steps: x.signum() })
            }
            _ => None,
        };
        if let Some(ev) = ev {
            let _ = self.tx.send(ev);
        }
    }
}

// ---------------------------------------------------------------- pipewire

/// Convert the portal's cursor bitmap to an RDP pointer; `None` for an empty (hidden) cursor.
fn rdp_cursor(meta: &spa::buffer::meta::MetaCursor) -> Option<Option<Cursor>> {
    use spa::param::video::VideoFormat as V;
    let bitmap = meta.bitmap()?;
    let size = bitmap.size();
    if size.width == 0 || size.height == 0 {
        return Some(None);
    }
    let data = bitmap.bitmap_data()?;
    let stride = bitmap.stride().unsigned_abs() as usize;
    // Byte offsets of B, G, R, A within a source pixel.
    let (b, g, r, a) = match bitmap.format() {
        V::BGRA | V::BGRx => (0, 1, 2, 3),
        V::RGBA | V::RGBx => (2, 1, 0, 3),
        V::ARGB | V::xRGB => (3, 2, 1, 0),
        V::ABGR | V::xBGR => (1, 2, 3, 0),
        _ => return None,
    };
    let has_alpha = matches!(bitmap.format(), V::BGRA | V::RGBA | V::ARGB | V::ABGR);
    // Crop anything past the RDP limit; cursor art is drawn from the top-left.
    let (w, h) = (size.width.min(MAX_POINTER) as usize, size.height.min(MAX_POINTER) as usize);
    let mut out = Vec::with_capacity(w * h * 4);
    for y in (0..h).rev() {
        let row = data.get(y * stride..y * stride + w * 4)?;
        for px in row.chunks_exact(4) {
            out.extend_from_slice(&[px[b], px[g], px[r], if has_alpha { px[a] } else { 0xff }]);
        }
    }
    let hot = meta.hotspot();
    Some(Some(Cursor {
        width: w as u16,
        height: h as u16,
        hot_x: (hot.x.max(0) as u16).min(w as u16 - 1),
        hot_y: (hot.y.max(0) as u16).min(h as u16 - 1),
        data: out,
    }))
}

/// Ask for cursor metadata on every buffer. The size is a range: a fixed size must match
/// the producer's exactly (cosmic-portal offers a 64x64 bitmap) or the meta is dropped.
fn cursor_meta_param() -> Result<Vec<u8>> {
    use spa::pod::{ChoiceValue, Object, Property, PropertyFlags, Value};
    use spa::utils::{Choice, ChoiceEnum, ChoiceFlags};
    let size_of = |w: usize, h: usize| {
        (std::mem::size_of::<spa::sys::spa_meta_cursor>()
            + std::mem::size_of::<spa::sys::spa_meta_bitmap>()
            + w * h * 4) as i32
    };
    let obj = Object {
        type_: spa::utils::SpaTypes::ObjectParamMeta.as_raw(),
        id: spa::param::ParamType::Meta.as_raw(),
        properties: vec![
            Property {
                key: spa::sys::SPA_PARAM_META_type,
                flags: PropertyFlags::empty(),
                value: Value::Id(spa::utils::Id(spa::sys::SPA_META_Cursor)),
            },
            Property {
                key: spa::sys::SPA_PARAM_META_size,
                flags: PropertyFlags::empty(),
                value: Value::Choice(ChoiceValue::Int(Choice(
                    ChoiceFlags::empty(),
                    ChoiceEnum::Range { default: size_of(64, 64), min: size_of(1, 1), max: size_of(1024, 1024) },
                ))),
            },
        ],
    };
    Ok(spa::pod::serialize::PodSerializer::serialize(std::io::Cursor::new(Vec::new()), &Value::Object(obj))
        .map_err(|e| anyhow!("meta pod: {e:?}"))?
        .0
        .into_inner())
}

fn run_pipewire(
    fd: OwnedFd,
    node: u32,
    tx: watch::Sender<Option<Arc<Frame>>>,
    cursor_tx: watch::Sender<Option<Arc<Cursor>>>,
) -> Result<()> {
    let meta_param = cursor_meta_param()?;
    pw::init();
    let mainloop = pw::main_loop::MainLoopRc::new(None)?;
    let context = pw::context::ContextRc::new(&mainloop, None)?;
    let core = context.connect_fd_rc(fd, None)?;

    let stream = pw::stream::StreamBox::new(
        &core,
        "pengyos-remote",
        pw::properties::properties! {
            *pw::keys::MEDIA_TYPE => "Video",
            *pw::keys::MEDIA_CATEGORY => "Capture",
            *pw::keys::MEDIA_ROLE => "Screen",
        },
    )?;

    let _listener = stream
        .add_local_listener_with_user_data(spa::param::video::VideoInfoRaw::default())
        .state_changed(|_, _, old, new| eprintln!("pipewire: {old:?} -> {new:?}"))
        .param_changed(move |stream, info, id, param| {
            let Some(param) = param else { return };
            if id != spa::param::ParamType::Format.as_raw() {
                return;
            }
            if let Some(pod) = spa::pod::Pod::from_bytes(&meta_param) {
                if let Err(e) = stream.update_params(&mut [pod]) {
                    eprintln!("pipewire: cursor meta: {e}");
                }
            }
            if info.parse(param).is_ok() {
                eprintln!(
                    "pipewire: format {:?} {}x{}",
                    info.format(),
                    info.size().width,
                    info.size().height
                );
            }
        })
        .process(move |stream, info| {
            let Some(mut buffer) = stream.dequeue_buffer() else { return };
            // Cursor metadata comes on its own and also on cursor-only buffers with no frame.
            if let Some(meta) = buffer.find_meta::<spa::buffer::meta::MetaCursor>() {
                if meta.is_valid() {
                    if let Some(cursor) = rdp_cursor(meta) {
                        let cursor = cursor.map(Arc::new);
                        cursor_tx.send_if_modified(|cur| {
                            if *cur != cursor {
                                match cursor.as_deref() {
                                    Some(c) => tracing::debug!(w = c.width, h = c.height, hot_x = c.hot_x, hot_y = c.hot_y, "cursor shape"),
                                    None => tracing::debug!("cursor hidden"),
                                }
                                *cur = cursor;
                                true
                            } else {
                                false
                            }
                        });
                    }
                }
            }
            let datas = buffer.datas_mut();
            let Some(d) = datas.first_mut() else { return };
            let (offset, size, stride) = {
                let c = d.chunk();
                (c.offset() as usize, c.size() as usize, c.stride())
            };
            let (w, h) = (info.size().width, info.size().height);
            if size == 0 || stride <= 0 || w == 0 || h == 0 {
                return;
            }
            let Some(format) = rdp_format(info.format()) else { return };
            let Some(bytes) = d.data() else { return };
            let end = (offset + stride as usize * h as usize).min(bytes.len());
            let frame = Frame {
                width: w as u16,
                height: h as u16,
                stride: stride as usize,
                format,
                data: bytes::Bytes::copy_from_slice(&bytes[offset..end]),
            };
            tx.send_replace(Some(Arc::new(frame)));
        })
        .register()?;

    use spa::param::video::VideoFormat;
    let obj = spa::pod::object!(
        spa::utils::SpaTypes::ObjectParamFormat,
        spa::param::ParamType::EnumFormat,
        spa::pod::property!(
            spa::param::format::FormatProperties::MediaType,
            Id,
            spa::param::format::MediaType::Video
        ),
        spa::pod::property!(
            spa::param::format::FormatProperties::MediaSubtype,
            Id,
            spa::param::format::MediaSubtype::Raw
        ),
        spa::pod::property!(
            spa::param::format::FormatProperties::VideoFormat,
            Choice,
            Enum,
            Id,
            // cosmic-portal offers SHM as RGBA only (BGRA/RGBA with modifiers = dmabuf).
            VideoFormat::RGBA,
            VideoFormat::RGBA,
            VideoFormat::RGBx,
            VideoFormat::BGRA,
            VideoFormat::BGRx
        ),
        spa::pod::property!(
            spa::param::format::FormatProperties::VideoSize,
            Choice,
            Range,
            Rectangle,
            spa::utils::Rectangle { width: 1920, height: 1080 },
            spa::utils::Rectangle { width: 1, height: 1 },
            spa::utils::Rectangle { width: 8192, height: 8192 }
        ),
        spa::pod::property!(
            spa::param::format::FormatProperties::VideoFramerate,
            Choice,
            Range,
            Fraction,
            spa::utils::Fraction { num: 60, denom: 1 },
            spa::utils::Fraction { num: 0, denom: 1 },
            spa::utils::Fraction { num: 240, denom: 1 }
        ),
    );
    let values: Vec<u8> = spa::pod::serialize::PodSerializer::serialize(
        std::io::Cursor::new(Vec::new()),
        &spa::pod::Value::Object(obj),
    )
    .map_err(|e| anyhow!("pod: {e:?}"))?
    .0
    .into_inner();
    let mut params = [spa::pod::Pod::from_bytes(&values).context("pod bytes")?];

    stream.connect(
        spa::utils::Direction::Input,
        Some(node),
        pw::stream::StreamFlags::AUTOCONNECT | pw::stream::StreamFlags::MAP_BUFFERS,
        &mut params,
    )?;
    mainloop.run();
    Ok(())
}

// ---------------------------------------------------------------- main

fn state_dir() -> PathBuf {
    let base = std::env::var_os("XDG_STATE_HOME").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".local/state")
    });
    base.join("pengyos-remote")
}

fn config_dir() -> PathBuf {
    let base = std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".config")
    });
    base.join("pengyos-remote")
}

/// The password file must be the user's alone: an RDP password others can read is
/// a login to this desktop for anyone with an account here.
fn read_password(path: &std::path::Path) -> Result<String> {
    use std::os::unix::fs::MetadataExt;
    let meta = std::fs::metadata(path)
        .with_context(|| format!("{}: no RDP password set (pengyos-remote password)", path.display()))?;
    if meta.mode() & 0o077 != 0 {
        return Err(anyhow!("{} is readable by others (mode {:o}); chmod 600 it", path.display(), meta.mode() & 0o777));
    }
    let pass = std::fs::read_to_string(path)?.trim_end_matches(['\n', '\r']).to_string();
    if pass.is_empty() {
        return Err(anyhow!("{} is empty (pengyos-remote password)", path.display()));
    }
    Ok(pass)
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .with_ansi(false)
        .init();
    let conf = config_dir();
    let path_env = |name: &str, file: &str| {
        std::env::var_os(name).map(PathBuf::from).unwrap_or_else(|| conf.join(file))
    };
    let user = std::env::var("RDP_USER")
        .or_else(|_| std::env::var("USER"))
        .context("no RDP_USER or USER")?;
    let pass = read_password(&path_env("RDP_PASS_FILE", "password"))?;
    let addr: std::net::SocketAddr =
        std::env::var("RDP_ADDR").unwrap_or_else(|_| "0.0.0.0:3389".into()).parse()?;
    let cert = path_env("RDP_CERT", "cert.pem");
    let key = path_env("RDP_KEY", "key.pem");

    // Portal session. A saved restore token skips the consent dialog after the first run.
    let dir = state_dir();
    std::fs::create_dir_all(&dir)?;
    let token_path = dir.join("restore-token");
    let token = std::fs::read_to_string(&token_path).ok();

    let rd = RemoteDesktop::new().await?;
    let sc = Screencast::new().await?;
    let session = rd.create_session(Default::default()).await?;
    rd.select_devices(
        &session,
        SelectDevicesOptions::default()
            .set_devices(DeviceType::Keyboard | DeviceType::Pointer)
            .set_persist_mode(PersistMode::ExplicitlyRevoked)
            .set_restore_token(token.as_deref()),
    )
    .await?;
    sc.select_sources(
        &session,
        SelectSourcesOptions::default()
            .set_cursor_mode(CursorMode::Metadata)
            .set_sources(BitFlags::from(SourceType::Monitor))
            .set_multiple(false),
    )
    .await?;
    eprintln!("portal: starting (approve the dialog on the machine if it appears)");
    let started = rd.start(&session, None, Default::default()).await?.response()?;
    if let Some(t) = started.restore_token() {
        std::fs::write(&token_path, t)?;
    }
    eprintln!("portal: devices {:?}", started.devices());
    let stream = started.streams().first().context("portal returned no stream")?.clone();
    let node = stream.pipe_wire_node_id();
    let (sw, sh) = stream.size().unwrap_or((1920, 1080));
    eprintln!("portal: stream node {node} size {sw}x{sh}");
    let fd = sc.open_pipe_wire_remote(&session, Default::default()).await?;

    let (frame_tx, frame_rx) = watch::channel(None);
    let (cursor_tx, cursor_rx) = watch::channel(None);
    std::thread::spawn(move || {
        if let Err(e) = run_pipewire(fd, node, frame_tx, cursor_tx) {
            eprintln!("pipewire: {e:#}");
        }
    });

    // Input pump: RDP handler (sync) -> channel -> portal calls (async).
    let (in_tx, mut in_rx) = mpsc::unbounded_channel::<Input>();
    tokio::spawn(async move {
        while let Some(ev) = in_rx.recv().await {
            let state = |p| if p { KeyState::Pressed } else { KeyState::Released };
            let r = match ev {
                Input::Key { code, pressed } => {
                    rd.notify_keyboard_keycode(&session, code, state(pressed), Default::default())
                        .await
                }
                Input::Keysym { sym, pressed } => {
                    rd.notify_keyboard_keysym(&session, sym, state(pressed), Default::default())
                        .await
                }
                Input::Motion { x, y } => {
                    rd.notify_pointer_motion_absolute(&session, node, x, y, Default::default())
                        .await
                }
                Input::Button { button, pressed } => {
                    rd.notify_pointer_button(&session, button, state(pressed), Default::default())
                        .await
                }
                Input::Scroll { axis, steps } => {
                    rd.notify_pointer_axis_discrete(&session, axis, steps, Default::default())
                        .await
                }
            };
            if let Err(e) = r {
                eprintln!("input: {e}");
            }
        }
    });

    let tls = TlsIdentityCtx::init_from_paths(&cert, &key)?;
    let mut server = RdpServer::builder()
        .with_addr(addr)
        .with_hybrid(tls.make_acceptor()?, tls.pub_key.clone())
        .with_input_handler(InputHandler { tx: in_tx })
        .with_display_handler(Display {
            size: DesktopSize { width: sw as u16, height: sh as u16 },
            frames: frame_rx,
            cursor: cursor_rx,
        })
        // Activate at the client's requested size first; the vendored server then reactivates
        // at the real size, which is what makes FreeRDP clients (Remmina) resize their surface.
        .with_honor_client_desktop_size(true)
        .build();
    server.set_credentials(Some(Credentials { username: user, password: pass, domain: None }));
    eprintln!("rdp: listening on {addr}");
    server.run().await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn plain_scancodes_are_evdev_codes_and_extended_ones_are_mapped() {
        assert_eq!(evdev_key(0x1E, false), Some(0x1E)); // A
        assert_eq!(evdev_key(0x48, true), Some(103)); // UP
        assert_eq!(evdev_key(0x5B, true), Some(125)); // left Super
        assert_eq!(evdev_key(0x01, true), None);
    }

    #[test]
    fn unicode_keysyms() {
        assert_eq!(unicode_keysym('a' as u16), 'a' as i32);
        assert_eq!(unicode_keysym(0xE9), 0xE9); // é: Latin-1 keysym
        assert_eq!(unicode_keysym(0x20AC), 0x0100_20AC); // €: Unicode keysym
    }

    fn file(mode: u32, text: &str) -> (tempdir::Dir, PathBuf) {
        let d = tempdir::Dir::new();
        let p = d.0.join("password");
        std::fs::write(&p, text).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(mode)).unwrap();
        (d, p)
    }

    #[test]
    fn the_password_file_must_be_private_and_not_empty() {
        let (_d, p) = file(0o600, "correct horse\n");
        assert_eq!(read_password(&p).unwrap(), "correct horse");
        let (_d, p) = file(0o644, "correct horse\n");
        assert!(read_password(&p).unwrap_err().to_string().contains("readable by others"));
        let (_d, p) = file(0o600, "\n");
        assert!(read_password(&p).unwrap_err().to_string().contains("empty"));
        assert!(read_password(std::path::Path::new("/nonexistent/password"))
            .unwrap_err()
            .to_string()
            .contains("pengyos-remote password"));
    }

    /// A temp dir without a crate for it.
    mod tempdir {
        pub struct Dir(pub std::path::PathBuf);
        impl Dir {
            pub fn new() -> Dir {
                static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
                let n = N.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                let p = std::env::temp_dir().join(format!("pengyos-rdp-test-{}-{n}", std::process::id()));
                std::fs::create_dir_all(&p).unwrap();
                Dir(p)
            }
        }
        impl Drop for Dir {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
    }
}
