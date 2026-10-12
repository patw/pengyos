//! Printers (`pengyos-printers`): add and remove printers, watch and manage the
//! print queue, and print a test page — over CUPS' own protocol (IPP on its
//! local socket), with no web page.
//!
//! This file is std only and pure: the printer, job, device and driver models
//! read from CUPS' answers, which found printer is offered and how, which
//! driver is recommended, queue-name rules, the status text people read, and
//! every request the app sends. `ipp.rs` is the wire format and `cups.rs` the
//! transport; the window (`app.rs`) is behind the `cosmic` feature.

pub mod cups;
pub mod ipp;

#[cfg(feature = "cosmic")]
pub mod app;

#[cfg(test)]
mod tests;

use ipp::{group, op, Group, Request, Response, Value};

pub const APP_ID: &str = "io.catbee.pengyos.Printers";

/// The page CUPS' own web interface prints: a banner the `bannertopdf` filter
/// fills with the printer's name, driver and settings.
pub const TEST_PAGE: &str = "/usr/share/cups/data/testprint";

/// The document format of a test page, from its first bytes. cups-filters
/// installs its own `testprint` (`#PDF-BANNER`, a PDF template) over CUPS'
/// (`#CUPS-BANNER`), and CUPS refuses a job whose declared format has no
/// filter: sending the PengyOS one as `application/vnd.cups-banner` failed
/// with "Unsupported format" on behoweather.
pub fn test_page_format(page: &[u8]) -> &'static str {
    if page.starts_with(b"#PDF-BANNER") {
        "application/vnd.cups-pdf-banner"
    } else if page.starts_with(b"#CUPS-BANNER") {
        "application/vnd.cups-banner"
    } else if page.starts_with(b"%PDF") {
        "application/pdf"
    } else {
        // Let CUPS type it from its contents.
        "application/octet-stream"
    }
}

// --- printers -------------------------------------------------------------

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum PrinterState {
    #[default]
    Idle,
    Printing,
    Stopped,
}

impl PrinterState {
    fn from_enum(v: Option<i32>) -> PrinterState {
        match v {
            Some(4) => PrinterState::Printing,
            Some(5) => PrinterState::Stopped,
            _ => PrinterState::Idle,
        }
    }
}

/// One supply (toner, ink, waste box…) and its level in percent, when the
/// printer reports one.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Marker {
    pub name: String,
    pub kind: String,
    pub level: Option<u8>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Printer {
    /// The queue name: what CUPS, `lp -d` and every request use.
    pub name: String,
    /// The description people see in print dialogs.
    pub info: String,
    pub location: String,
    pub make_model: String,
    pub device_uri: String,
    pub state: PrinterState,
    pub message: String,
    pub reasons: Vec<String>,
    pub accepting: bool,
    pub shared: bool,
    pub temporary: bool,
    pub queued: i32,
    pub markers: Vec<Marker>,
    pub is_default: bool,
}

/// What CUPS-Get-Printers is asked for.
pub const PRINTER_ATTRS: &[&str] = &[
    "printer-name",
    "printer-info",
    "printer-location",
    "printer-make-and-model",
    "printer-state",
    "printer-state-message",
    "printer-state-reasons",
    "printer-is-accepting-jobs",
    "printer-is-shared",
    "printer-is-temporary",
    "device-uri",
    "queued-job-count",
    "marker-names",
    "marker-levels",
    "marker-types",
];

impl Printer {
    pub fn from_group(g: &Group) -> Printer {
        let names = g.strs("marker-names");
        let kinds = g.strs("marker-types");
        let levels = g.ints("marker-levels");
        let n = levels.len().max(names.len());
        let markers = (0..n)
            .map(|i| Marker {
                name: names.get(i).cloned().unwrap_or_default(),
                kind: kinds.get(i).cloned().unwrap_or_default(),
                // -1 unknown, -2 not available, -3 "some left": no number to show.
                level: levels.get(i).copied().filter(|l| (0..=100).contains(l)).map(|l| l as u8),
            })
            .collect();
        Printer {
            name: g.string("printer-name"),
            info: g.string("printer-info"),
            location: g.string("printer-location"),
            make_model: g.string("printer-make-and-model"),
            device_uri: g.string("device-uri"),
            state: PrinterState::from_enum(g.int("printer-state")),
            message: g.string("printer-state-message"),
            reasons: g.strs("printer-state-reasons").into_iter().filter(|r| r != "none").collect(),
            accepting: g.bool("printer-is-accepting-jobs").unwrap_or(true),
            shared: g.bool("printer-is-shared").unwrap_or(false),
            temporary: g.bool("printer-is-temporary").unwrap_or(false),
            queued: g.int("queued-job-count").unwrap_or(0),
            markers,
            is_default: false,
        }
    }

    /// The name people see: the description someone gave it, else its model,
    /// else the queue name made readable. cups-browsed sets the description to
    /// the queue name ("Brother_MFC_L3720CDW_series"), which is no description.
    pub fn title(&self) -> String {
        let info = self.info.trim();
        if !info.is_empty() && info != self.name {
            return info.to_string();
        }
        let model = self.model();
        if !model.is_empty() {
            return model;
        }
        self.name.replace('_', " ")
    }

    /// The make and model without the driver CUPS appends ("…, driverless,
    /// cups-filters 1.28.17", "… - CUPS+Gutenprint v5.2.14").
    pub fn model(&self) -> String {
        model_without_driver(&self.make_model)
    }

    /// cups-browsed made this queue for a printer it saw on the network (or
    /// CUPS made a temporary one for a print dialog). It comes back on its own
    /// while the printer is on the network, so removing it does nothing lasting.
    pub fn automatic(&self) -> bool {
        self.temporary || self.device_uri.starts_with("implicitclass://")
    }

    pub fn connection(&self) -> &'static str {
        if self.automatic() {
            return "Network";
        }
        connection_of(&self.device_uri)
    }

    pub fn paused(&self) -> bool {
        self.state == PrinterState::Stopped
    }

    /// "Ready", "Printing", "Paused", plus whatever the printer reports:
    /// "Ready · Toner low", "Paused · Out of paper".
    pub fn status(&self) -> String {
        let mut parts = vec![match self.state {
            PrinterState::Idle => "Ready".to_string(),
            PrinterState::Printing => "Printing".to_string(),
            PrinterState::Stopped if self.reasons.iter().any(|r| r == "paused") => "Paused".to_string(),
            PrinterState::Stopped => "Stopped".to_string(),
        }];
        if !self.accepting {
            parts.push("Not accepting documents".into());
        }
        for r in &self.reasons {
            if let Some(t) = reason_text(r) {
                if !parts.contains(&t) {
                    parts.push(t);
                }
            }
        }
        // CUPS stops a queue when a job fails (a printer that went away, a
        // filter that crashed) and says why only in the message.
        if self.state == PrinterState::Stopped && !self.reasons.iter().any(|r| r == "paused") {
            let m = self.message.trim();
            if !m.is_empty() {
                parts.push(m.to_string());
            }
        }
        parts.join(" · ")
    }

    /// "Toner 90 %" or "Black 80 % · Cyan 15 %": the supplies with a level.
    pub fn supplies(&self) -> Option<String> {
        let known: Vec<&Marker> = self.markers.iter().filter(|m| m.level.is_some()).collect();
        if known.is_empty() {
            return None;
        }
        let named = known.iter().all(|m| !m.name.trim().is_empty());
        if named {
            return Some(
                known
                    .iter()
                    .map(|m| format!("{} {} %", m.name.trim(), m.level.unwrap_or(0)))
                    .collect::<Vec<_>>()
                    .join(" · "),
            );
        }
        // No names (the Brother sends four toner levels and nothing else):
        // one word for the kind, then the levels.
        let kind = known
            .iter()
            .map(|m| supply_word(&m.kind))
            .fold(None::<&str>, |acc, k| match acc {
                None => Some(k),
                Some(a) if a == k => Some(a),
                Some(_) => Some("Supplies"),
            })
            .unwrap_or("Supplies");
        let levels: Vec<String> = known.iter().map(|m| format!("{} %", m.level.unwrap_or(0))).collect();
        Some(format!("{kind} {}", levels.join(", ")))
    }
}

fn supply_word(kind: &str) -> &'static str {
    match kind {
        k if k.starts_with("toner") => "Toner",
        k if k.starts_with("ink") => "Ink",
        k if k.contains("waste") => "Waste",
        "opc" | "developer" | "fuser" => "Parts",
        _ => "Supplies",
    }
}

/// How a device URI reaches the printer, in one word.
pub fn connection_of(uri: &str) -> &'static str {
    let scheme = uri.split(':').next().unwrap_or("");
    // ipp-usb serves a driverless USB printer on localhost:60000 and up.
    let local_ipp = matches!(scheme, "ipp" | "ipps") && (uri.contains("://localhost:6") || uri.contains("://127.0.0.1:6"));
    match scheme {
        "usb" => "USB",
        _ if local_ipp => "USB",
        "hp" | "hpfax" if uri.contains(":/usb/") => "USB",
        "hp" | "hpfax" => "Network",
        "ipp" | "ipps" | "http" | "https" | "dnssd" | "socket" | "lpd" | "smb" => "Network",
        "parallel" => "Parallel port",
        "serial" => "Serial port",
        _ => "Other",
    }
}

/// `printer-state-reasons` keywords in words. A known reason shows whatever its
/// severity suffix (printers send "toner-low-report" as often as
/// "-warning"); unknown informational `-report` ones and CUPS' internal
/// bookkeeping are not shown.
pub fn reason_text(reason: &str) -> Option<String> {
    let base = reason
        .strip_suffix("-error")
        .or_else(|| reason.strip_suffix("-warning"))
        .or_else(|| reason.strip_suffix("-report"))
        .unwrap_or(reason);
    let known = match base {
        "none" | "paused" => return None,
        "media-empty" | "media-needed" => "Out of paper",
        "media-jam" => "Paper jam",
        "media-low" => "Paper low",
        "door-open" | "cover-open" | "interlock-open" => "Cover open",
        "toner-low" | "marker-supply-low" => "Toner or ink low",
        "toner-empty" | "marker-supply-empty" => "Out of toner or ink",
        "marker-waste-almost-full" => "Waste toner nearly full",
        "marker-waste-full" => "Waste toner full",
        "input-tray-missing" => "Paper tray missing",
        "output-area-full" | "output-tray-missing" => "Output tray full",
        "offline-report" | "offline" => "Offline",
        "shutdown" => "Switched off",
        "connecting-to-device" => "Connecting…",
        "timed-out" => "Not answering",
        "cups-missing-filter" => "A driver filter is missing",
        "cups-insecure-filter" => "A driver filter is not trusted",
        _ => {
            // Reports are informational; CUPS' own cups-* keywords are bookkeeping.
            if reason.ends_with("-report") || base.starts_with("cups-") || base.starts_with("com.") {
                return None;
            }
            return Some(humanize_keyword(base));
        }
    };
    Some(known.into())
}

/// "spool-area-full" → "Spool area full".
pub fn humanize_keyword(k: &str) -> String {
    let s = k.replace('-', " ");
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

pub fn model_without_driver(mm: &str) -> String {
    let mm = mm.split(" - CUPS+Gutenprint").next().unwrap_or(mm);
    let mm = mm.split(", driverless").next().unwrap_or(mm);
    let mm = mm.split(", hpcups").next().unwrap_or(mm);
    mm.trim().to_string()
}

// --- jobs ---------------------------------------------------------------

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum JobState {
    #[default]
    Pending,
    Held,
    Processing,
    Stopped,
    Canceled,
    Aborted,
    Completed,
}

impl JobState {
    fn from_enum(v: Option<i32>) -> JobState {
        match v {
            Some(4) => JobState::Held,
            Some(5) => JobState::Processing,
            Some(6) => JobState::Stopped,
            Some(7) => JobState::Canceled,
            Some(8) => JobState::Aborted,
            Some(9) => JobState::Completed,
            _ => JobState::Pending,
        }
    }

    /// Still in the queue (it can be cancelled).
    pub fn active(self) -> bool {
        matches!(self, JobState::Pending | JobState::Held | JobState::Processing | JobState::Stopped)
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Job {
    pub id: i32,
    /// The queue it was sent to.
    pub printer: String,
    pub title: String,
    pub user: String,
    pub state: JobState,
    pub reasons: Vec<String>,
    pub kbytes: i32,
    pub created: i64,
    pub sheets: i32,
    pub printer_message: String,
}

pub const JOB_ATTRS: &[&str] = &[
    "job-id",
    "job-name",
    "job-originating-user-name",
    "job-state",
    "job-state-reasons",
    "job-k-octets",
    "time-at-creation",
    "job-printer-uri",
    "job-media-sheets-completed",
    "job-printer-state-message",
];

impl Job {
    pub fn from_group(g: &Group) -> Job {
        Job {
            id: g.int("job-id").unwrap_or(0),
            printer: queue_from_uri(&g.string("job-printer-uri")),
            title: g.string("job-name"),
            user: g.string("job-originating-user-name"),
            state: JobState::from_enum(g.int("job-state")),
            reasons: g.strs("job-state-reasons"),
            kbytes: g.int("job-k-octets").unwrap_or(0),
            created: i64::from(g.int("time-at-creation").unwrap_or(0)),
            sheets: g.int("job-media-sheets-completed").unwrap_or(0),
            printer_message: g.string("job-printer-state-message"),
        }
    }

    pub fn title(&self) -> String {
        let t = self.title.trim();
        if t.is_empty() {
            format!("Document {}", self.id)
        } else {
            t.to_string()
        }
    }

    pub fn held(&self) -> bool {
        self.state == JobState::Held
    }

    /// What the queue says about this job, given its printer.
    pub fn status(&self, printer: Option<&Printer>) -> String {
        match self.state {
            JobState::Pending if printer.is_some_and(Printer::paused) => "Waiting: the printer is paused".into(),
            JobState::Pending => "Waiting".into(),
            JobState::Held => "On hold".into(),
            JobState::Processing => {
                let m = self.printer_message.trim();
                if m.is_empty() || m.eq_ignore_ascii_case("ready to print.") {
                    "Printing".into()
                } else {
                    format!("Printing: {m}")
                }
            }
            JobState::Stopped => "Stopped".into(),
            JobState::Canceled => "Cancelled".into(),
            JobState::Aborted => "Failed".into(),
            JobState::Completed if self.sheets > 0 => {
                format!("Printed, {} {}", self.sheets, if self.sheets == 1 { "page" } else { "pages" })
            }
            JobState::Completed => "Printed".into(),
        }
    }
}

/// `ipp://localhost:631/printers/Office_Laser` → `Office_Laser`.
pub fn queue_from_uri(uri: &str) -> String {
    let last = uri.trim_end_matches('/').rsplit('/').next().unwrap_or("");
    percent_decode(last)
}

// --- devices (printers CUPS can find) -----------------------------------------

/// One answer of CUPS-Get-Devices: a printer a backend found, or a bare scheme
/// (`socket`, `lpd`, …) that only says the backend exists.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Device {
    pub uri: String,
    pub class: String,
    pub info: String,
    pub make_model: String,
    pub id: String,
    pub location: String,
}

impl Device {
    pub fn from_group(g: &Group) -> Device {
        Device {
            uri: g.string("device-uri"),
            class: g.string("device-class"),
            info: g.string("device-info"),
            make_model: g.string("device-make-and-model"),
            id: g.string("device-id"),
            location: g.string("device-location"),
        }
    }

    /// A found printer, not a bare backend name.
    pub fn is_printer(&self) -> bool {
        self.uri.contains(":/") && !self.uri.starts_with("implicitclass:") && !self.uri.starts_with("beh:")
    }

    /// IPP Everywhere / AirPrint: cups-filters can make its driver from what
    /// the printer reports about itself. CUPS lists those with "(driverless)".
    pub fn driverless(&self) -> bool {
        self.info.contains("(driverless)") || (self.uri.starts_with("ipp") && connection_of(&self.uri) == "USB")
    }

    pub fn title(&self) -> String {
        let mm = self.make_model.trim();
        if !mm.is_empty() && !mm.eq_ignore_ascii_case("unknown") {
            return mm.to_string();
        }
        let info = self.info.trim().trim_end_matches("(driverless)").trim();
        if info.is_empty() {
            self.uri.clone()
        } else {
            info.to_string()
        }
    }

    /// The Bonjour service name ("Brother MFC-L3720CDW series") for a
    /// network printer found by mDNS, the same in its `dnssd://`, `ipp://`
    /// and `ipps://` forms.
    fn service(&self) -> Option<String> {
        let host = self.uri.split("://").nth(1)?.split('/').next()?;
        let host = percent_decode(host);
        ["._ipp._tcp", "._ipps._tcp", "._pdl-datastream._tcp", "._printer._tcp"]
            .iter()
            .find_map(|s| host.find(s).map(|i| host[..i].to_string()))
    }

    /// Which of several URIs for one printer to use: driverless IPPS first,
    /// then IPP, then the rest.
    fn rank(&self) -> u8 {
        let scheme = self.uri.split(':').next().unwrap_or("");
        match (self.driverless(), scheme) {
            (true, "ipps") => 0,
            (true, _) => 1,
            (false, "ipps") => 2,
            (false, "ipp") => 3,
            (false, "dnssd") => 4,
            (false, "usb") => 5,
            _ => 6,
        }
    }
}

/// A printer offered on the Add page.
#[derive(Clone, Debug, PartialEq)]
pub struct Found {
    pub device: Device,
    /// An existing queue already prints to it (same URI, or the queue
    /// cups-browsed made for it).
    pub added_as: Option<String>,
}

impl Found {
    pub fn connection(&self) -> &'static str {
        connection_of(&self.device.uri)
    }
}

/// The printers worth offering, one per printer: bare backends dropped, the
/// several URIs a network printer answers on merged (driverless first), and
/// the ones already set up marked.
pub fn found_printers(devices: &[Device], printers: &[Printer]) -> Vec<Found> {
    let mut out: Vec<Device> = Vec::new();
    for d in devices.iter().filter(|d| d.is_printer()) {
        let same = out.iter().position(|o| match (o.service(), d.service()) {
            (Some(a), Some(b)) => a == b,
            _ => o.uri == d.uri,
        });
        match same {
            Some(i) if d.rank() < out[i].rank() => out[i] = d.clone(),
            Some(_) => {}
            None => out.push(d.clone()),
        }
    }
    out.sort_by(|a, b| connection_of(&a.uri).cmp(connection_of(&b.uri)).then(a.title().cmp(&b.title())));
    out.into_iter()
        .map(|device| {
            let added_as = printers
                .iter()
                .find(|p| {
                    p.device_uri == device.uri
                        || (p.automatic() && !device.make_model.is_empty() && p.model() == model_without_driver(&device.make_model))
                        || device.service().is_some_and(|s| p.device_uri.contains(&percent_encode(&s)))
                })
                .map(Printer::title);
            Found { device, added_as }
        })
        .collect()
}

// --- drivers ----------------------------------------------------------------

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Driver {
    /// `ppd-name`: what CUPS-Add-Modify-Printer is given.
    pub name: String,
    pub make_model: String,
    pub device_id: String,
}

pub const DRIVER_ATTRS: &[&str] = &["ppd-name", "ppd-make-and-model", "ppd-device-id"];

impl Driver {
    pub fn from_group(g: &Group) -> Driver {
        Driver {
            name: g.string("ppd-name"),
            make_model: g.string("ppd-make-and-model"),
            device_id: g.string("ppd-device-id"),
        }
    }

    /// A driver for this exact printer that cups-filters makes from what the
    /// printer reports: no model-specific files involved.
    pub fn driverless_for(uri: &str) -> Driver {
        Driver {
            name: format!("driverless:{uri}"),
            make_model: "Driverless (IPP Everywhere)".into(),
            device_id: String::new(),
        }
    }

    pub fn is_driverless(&self) -> bool {
        self.name.starts_with("driverless:")
    }

    /// A fax queue (cups-filters lists one per multifunction printer):
    /// printing to it would send a fax.
    pub fn is_fax(&self) -> bool {
        self.name.starts_with("driverless-fax:") || self.make_model.contains(", Fax") || self.name.contains("fax")
    }

    pub fn title(&self) -> String {
        if self.is_driverless() {
            "Driverless (IPP Everywhere): made from what the printer reports".into()
        } else {
            self.make_model.clone()
        }
    }
}

/// `MFG:Brother;MDL:MFC-L3720CDW series;CMD:…` → ("brother", "mfcl3720cdwseries").
fn ieee_mfg_mdl(id: &str) -> Option<(String, String)> {
    let mut mfg = None;
    let mut mdl = None;
    for part in id.split(';') {
        let (k, v) = part.split_once(':')?;
        match k.trim().to_ascii_uppercase().as_str() {
            "MFG" | "MANUFACTURER" => mfg = Some(norm(v)),
            "MDL" | "MODEL" => mdl = Some(norm(v)),
            _ => {}
        }
    }
    Some((maker(&mfg?), mdl?))
}

/// Lower-case letters and digits only: "MFC-L3720CDW series" → "mfcl3720cdwseries".
fn norm(s: &str) -> String {
    s.chars().filter(char::is_ascii_alphanumeric).flat_map(char::to_lowercase).collect()
}

fn maker(m: &str) -> String {
    match m {
        "hewlettpackard" => "hp".into(),
        "kyoceramita" => "kyocera".into(),
        other => other.into(),
    }
}

/// The drivers to offer for a found printer, best first: driverless when the
/// printer supports it, then the ones that name its exact model, then the
/// ones for its model family. Fax drivers are never offered.
pub fn drivers_for(device: &Device, all: &[Driver]) -> Vec<Driver> {
    let mut scored: Vec<(u32, &Driver)> = Vec::new();
    let want = ieee_mfg_mdl(&device.id);
    let mm = norm(&model_without_driver(&device.make_model));
    for d in all.iter().filter(|d| !d.is_fax()) {
        let score = if d.is_driverless() {
            // cups-filters lists one per driverless printer it can see, keyed
            // by URI: only the one for this printer applies.
            if d.name == format!("driverless:{}", device.uri) {
                1000
            } else {
                0
            }
        } else if let (Some((mfg, mdl)), Some((dm, dmdl))) = (&want, ieee_mfg_mdl(&d.device_id)) {
            if &dm == mfg && &dmdl == mdl {
                500
            } else {
                0
            }
        } else {
            0
        };
        let score = if score > 0 {
            score
        } else if !mm.is_empty() && norm(&model_without_driver(&d.make_model)) == mm {
            400
        } else if let Some((mfg, mdl)) = &want {
            // The model family: "mfcl3720cdw" names a driver for "MFC-L3720CDW
            // series"; a "series" suffix is how makers name a family.
            let dmm = norm(&d.make_model);
            let core = mdl.trim_end_matches("series");
            if dmm.starts_with(&maker(mfg)) && core.len() >= 4 && dmm.contains(core) {
                300
            } else {
                0
            }
        } else {
            0
        };
        if score > 0 {
            scored.push((score, d));
        }
    }
    // Gutenprint lists a "simplified" and an "expert" flavour of each driver:
    // the simplified one first.
    scored.sort_by(|a, b| {
        b.0.cmp(&a.0)
            .then(a.1.name.contains("/expert").cmp(&b.1.name.contains("/expert")))
            .then(a.1.make_model.cmp(&b.1.make_model))
    });
    let mut out: Vec<Driver> = Vec::new();
    if device.driverless() && !scored.iter().any(|(_, d)| d.is_driverless()) {
        // CUPS had not listed the driverless driver for this URI yet (its list
        // comes from its own mDNS browse): cups-filters makes it on request.
        if matches!(device.uri.split(':').next(), Some("ipp" | "ipps")) {
            out.push(Driver::driverless_for(&device.uri));
        }
    }
    for (_, d) in scored {
        if !out.iter().any(|o| o.name == d.name) {
            out.push(d.clone());
        }
    }
    out
}

/// Drivers whose description has every word of `query`, for a printer no
/// driver was matched to. At most `limit`.
pub fn search_drivers(all: &[Driver], query: &str, limit: usize) -> Vec<Driver> {
    let words: Vec<String> = query.split_whitespace().map(norm).filter(|w| !w.is_empty()).collect();
    if words.is_empty() {
        return Vec::new();
    }
    let mut hits: Vec<&Driver> = all
        .iter()
        .filter(|d| !d.is_fax() && !d.is_driverless())
        .filter(|d| {
            let mm = norm(&d.make_model);
            words.iter().all(|w| mm.contains(w.as_str()))
        })
        .collect();
    hits.sort_by(|a, b| {
        a.name
            .contains("/expert")
            .cmp(&b.name.contains("/expert"))
            .then(a.make_model.len().cmp(&b.make_model.len()))
            .then(a.make_model.cmp(&b.make_model))
    });
    hits.into_iter().take(limit).cloned().collect()
}

// --- names and addresses ---------------------------------------------------------

/// A CUPS queue name from a printer's model: CUPS refuses spaces, `/`, `\`,
/// `#`, `?`, quotes and control characters, and anything over 127 bytes. Queue
/// names are case-insensitive, so `taken` is compared that way and a clash
/// gets `_2`, `_3`…
pub fn queue_name(title: &str, taken: &[String]) -> String {
    let mut base: String = title
        .trim()
        .chars()
        .map(|c| if c.is_alphanumeric() || "-_.".contains(c) { c } else { '_' })
        .collect();
    while base.contains("__") {
        base = base.replace("__", "_");
    }
    let base = base.trim_matches('_').chars().take(100).collect::<String>();
    let base = if base.is_empty() { "Printer".to_string() } else { base };
    let clash = |n: &str| taken.iter().any(|t| t.eq_ignore_ascii_case(n));
    if !clash(&base) {
        return base;
    }
    (2..).map(|i| format!("{base}_{i}")).find(|n| !clash(n)).unwrap_or(base)
}

/// A printer someone types the address of: a host name or IP address (with an
/// optional port) becomes the standard IPP Everywhere URI, and a full
/// `ipp://`, `ipps://`, `socket://` or `lpd://` URI is used as typed.
pub fn uri_from_address(input: &str) -> Result<String, String> {
    let a = input.trim();
    if a.is_empty() {
        return Err("Type the printer's address, for example 192.168.1.20.".into());
    }
    if let Some((scheme, rest)) = a.split_once("://") {
        let scheme = scheme.to_ascii_lowercase();
        if !matches!(scheme.as_str(), "ipp" | "ipps" | "http" | "https" | "socket" | "lpd") {
            return Err(format!("“{scheme}://” is not a printer address the app knows (ipp, ipps, socket, lpd)."));
        }
        if rest.is_empty() || rest.starts_with('/') {
            return Err("The address has no host name.".into());
        }
        return Ok(format!("{scheme}://{rest}"));
    }
    let host = a.trim_end_matches('/');
    let ok = host
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || "-.:[]".contains(c));
    if !ok {
        return Err("That is not a host name or IP address.".into());
    }
    Ok(format!("ipp://{host}/ipp/print"))
}

/// Whether the driverless driver can be made for this URI: only for printers
/// that speak IPP.
pub fn speaks_ipp(uri: &str) -> bool {
    matches!(uri.split(':').next(), Some("ipp" | "ipps" | "http" | "https"))
}

// --- small formatting -------------------------------------------------------

/// "just now", "4 min ago", "3 h ago", "yesterday", "5 days ago".
pub fn ago(now: i64, then: i64) -> String {
    if then <= 0 {
        return String::new();
    }
    let s = (now - then).max(0);
    match s {
        0..=59 => "just now".into(),
        60..=3599 => format!("{} min ago", s / 60),
        3600..=86399 => format!("{} h ago", s / 3600),
        86400..=172799 => "yesterday".into(),
        _ => format!("{} days ago", s / 86400),
    }
}

pub fn size_text(kbytes: i32) -> String {
    match kbytes {
        k if k <= 0 => String::new(),
        k if k < 1024 => format!("{k} KB"),
        k => format!("{:.1} MB", f64::from(k) / 1024.0),
    }
}

pub fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            let hex = std::str::from_utf8(&b[i + 1..i + 3]).ok();
            if let Some(v) = hex.and_then(|h| u8::from_str_radix(h, 16).ok()) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

pub fn percent_encode(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

pub fn printer_uri(queue: &str) -> String {
    format!("ipp://localhost/printers/{}", percent_encode(queue))
}

// --- the requests the app sends ---------------------------------------------

/// Every request names its user: CUPS records it as the job's owner and checks
/// it against the peer credentials of the socket.
fn base(op: u16, user: &str) -> Request {
    Request::new(op).attr("requesting-user-name", Value::name(user))
}

pub fn req_printers(user: &str) -> Request {
    base(op::CUPS_GET_PRINTERS, user).want(PRINTER_ATTRS)
}

pub fn req_default(user: &str) -> Request {
    base(op::CUPS_GET_DEFAULT, user).want(&["printer-name"])
}

/// `which` is "not-completed" (the queue) or "completed" (the history).
pub fn req_jobs(user: &str, which: &str, limit: Option<i32>) -> Request {
    let mut r = Request::new(op::GET_JOBS)
        .attr("printer-uri", Value::uri("ipp://localhost/"))
        .attr("requesting-user-name", Value::name(user))
        .attr("which-jobs", Value::keyword(which))
        .attr("my-jobs", Value::Boolean(false));
    if let Some(l) = limit {
        r = r.attr("limit", Value::Integer(l));
    }
    r.want(JOB_ATTRS)
}

fn job_req(op: u16, user: &str, id: i32) -> Request {
    Request::new(op)
        .attr("printer-uri", Value::uri("ipp://localhost/"))
        .attr("job-id", Value::Integer(id))
        .attr("requesting-user-name", Value::name(user))
}

pub fn req_cancel_job(user: &str, id: i32) -> Request {
    job_req(op::CANCEL_JOB, user, id)
}

pub fn req_hold_job(user: &str, id: i32) -> Request {
    job_req(op::HOLD_JOB, user, id)
}

pub fn req_release_job(user: &str, id: i32) -> Request {
    job_req(op::RELEASE_JOB, user, id)
}

fn printer_req(op: u16, user: &str, queue: &str) -> Request {
    Request::new(op)
        .attr("printer-uri", Value::uri(&printer_uri(queue)))
        .attr("requesting-user-name", Value::name(user))
}

pub fn req_pause(user: &str, queue: &str) -> Request {
    printer_req(op::PAUSE_PRINTER, user, queue)
}

pub fn req_resume(user: &str, queue: &str) -> Request {
    printer_req(op::RESUME_PRINTER, user, queue)
}

pub fn req_set_default(user: &str, queue: &str) -> Request {
    printer_req(op::CUPS_SET_DEFAULT, user, queue)
}

pub fn req_delete(user: &str, queue: &str) -> Request {
    printer_req(op::CUPS_DELETE_PRINTER, user, queue)
}

/// The test page CUPS' web interface prints, sent as a job.
pub fn req_test_page(user: &str, queue: &str, page: Vec<u8>) -> Request {
    let mut r = printer_req(op::PRINT_JOB, user, queue)
        .attr("job-name", Value::name("Test Page"))
        .attr("document-format", Value::mime(test_page_format(&page)));
    r.data = page;
    r
}

/// Add a printer — what `lpadmin -p NAME -v URI -m DRIVER -D INFO -L WHERE -E`
/// sends: the queue, its connection and driver, enabled and accepting, and
/// (unlike lpadmin's default) not shared on the network.
pub fn req_add(user: &str, queue: &str, uri: &str, driver: &str, info: &str, location: &str) -> Request {
    printer_req(op::CUPS_ADD_MODIFY_PRINTER, user, queue)
        .attr("ppd-name", Value::name(driver))
        .printer_attr("device-uri", Value::uri(uri))
        .printer_attr("printer-info", Value::text(info))
        .printer_attr("printer-location", Value::text(location))
        .printer_attr("printer-is-accepting-jobs", Value::Boolean(true))
        .printer_attr("printer-is-shared", Value::Boolean(false))
        .printer_attr("printer-state", Value::Enum(3))
}

/// The printers found on USB and the network. `timeout` is how long CUPS lets
/// its backends look (the network ones wait for mDNS answers).
pub fn req_devices(user: &str, timeout_s: i32) -> Request {
    base(op::CUPS_GET_DEVICES, user).attr("timeout", Value::Integer(timeout_s))
}

pub fn req_drivers(user: &str) -> Request {
    base(op::CUPS_GET_PPDS, user).want(DRIVER_ATTRS)
}

// --- reading the answers ---------------------------------------------------------

/// Printers sorted by title, the default one marked.
pub fn read_printers(r: &Response, default: Option<&str>) -> Vec<Printer> {
    let mut v: Vec<Printer> = r.groups(group::PRINTER).map(Printer::from_group).filter(|p| !p.name.is_empty()).collect();
    for p in &mut v {
        p.is_default = default.is_some_and(|d| d.eq_ignore_ascii_case(&p.name));
    }
    v.sort_by_key(|p| p.title().to_lowercase());
    v
}

/// The default queue's name; CUPS answers "not found" when there is none.
pub fn read_default(r: &Response) -> Option<String> {
    if !r.ok() {
        return None;
    }
    r.groups(group::PRINTER).find_map(|g| g.str("printer-name")).map(str::to_string)
}

/// Jobs, newest first.
pub fn read_jobs(r: &Response) -> Vec<Job> {
    let mut v: Vec<Job> = r.groups(group::JOB).map(Job::from_group).filter(|j| j.id > 0).collect();
    v.sort_by(|a, b| b.id.cmp(&a.id));
    v
}

pub fn read_devices(r: &Response) -> Vec<Device> {
    r.groups(group::PRINTER).map(Device::from_group).collect()
}

pub fn read_drivers(r: &Response) -> Vec<Driver> {
    r.groups(group::PRINTER).map(Driver::from_group).filter(|d| !d.name.is_empty()).collect()
}

/// Why a request failed, in words people can act on. `what` is the action
/// ("remove the printer").
pub fn explain(status: u16, message: Option<&str>, what: &str) -> String {
    use ipp::status::*;
    let detail = message.map(str::trim).filter(|m| !m.is_empty());
    match status {
        FORBIDDEN | NOT_AUTHENTICATED | NOT_AUTHORIZED | CUPS_AUTHENTICATION_CANCELED => format!(
            "Could not {what}: only administrators (members of the wheel group) can change printers."
        ),
        NOT_FOUND => format!("Could not {what}: CUPS no longer has it (it may have been removed)."),
        _ => match detail {
            Some(m) => format!("Could not {what}: {m}"),
            None => format!("Could not {what} (CUPS status 0x{status:04x})."),
        },
    }
}

/// Everything the main window shows, read in one go.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Snapshot {
    pub printers: Vec<Printer>,
    pub queue: Vec<Job>,
    pub history: Vec<Job>,
}

/// `pengyos-printers --list`.
pub fn format_snapshot(s: &Snapshot, now: i64) -> String {
    let mut out = String::new();
    if s.printers.is_empty() {
        out.push_str("no printers\n");
    }
    for p in &s.printers {
        out.push_str(&format!(
            "{}{}  [{}]  {} · {}\n",
            p.title(),
            if p.is_default { " (default)" } else { "" },
            p.name,
            p.connection(),
            p.status()
        ));
        if !p.model().is_empty() {
            out.push_str(&format!("    {}\n", p.model()));
        }
        if let Some(sup) = p.supplies() {
            out.push_str(&format!("    {sup}\n"));
        }
        out.push_str(&format!("    {}\n", p.device_uri));
    }
    out.push_str(if s.queue.is_empty() { "queue: empty\n" } else { "queue:\n" });
    for j in &s.queue {
        let p = s.printers.iter().find(|p| p.name == j.printer);
        out.push_str(&format!(
            "  {}  {}  {} · {} · {} · {}\n",
            j.id,
            j.title(),
            j.printer,
            j.user,
            ago(now, j.created),
            j.status(p)
        ));
    }
    if !s.history.is_empty() {
        out.push_str("recent:\n");
        for j in &s.history {
            out.push_str(&format!("  {}  {}  {} · {} · {}\n", j.id, j.title(), j.printer, ago(now, j.created), j.status(None)));
        }
    }
    out
}

pub fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}
