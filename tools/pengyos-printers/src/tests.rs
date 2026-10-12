//! The decision logic, against answers captured from a real cupsd (CUPS 2.4
//! with cups-filters 1.28 on behoweather, 2026-10-12: one Brother
//! MFC-L3720CDW on the network, added by cups-browsed) and against
//! hand-built ones.

use super::*;
use crate::cups::{parse_http, user_for_uid};
use crate::ipp::{tag, Response, Value};

const PRINTERS: &[u8] = include_bytes!("../tests/data/get-printers.ipp");
const DEFAULT_NONE: &[u8] = include_bytes!("../tests/data/get-default.ipp");
const JOBS: &[u8] = include_bytes!("../tests/data/get-jobs.ipp");
const DEVICES: &[u8] = include_bytes!("../tests/data/get-devices.ipp");
const PPDS: &[u8] = include_bytes!("../tests/data/get-ppds.ipp");

const BROTHER_IPPS: &str = "ipps://Brother%20MFC-L3720CDW%20series._ipps._tcp.local/";

fn decode(b: &[u8]) -> Response {
    Response::decode(b).expect("a real CUPS answer decodes")
}

fn printers() -> Vec<Printer> {
    read_printers(&decode(PRINTERS), None)
}

// --- real answers --------------------------------------------------------------

#[test]
fn test_real_printer_list() {
    let p = printers();
    assert_eq!(p.len(), 1);
    let b = &p[0];
    assert_eq!(b.name, "Brother_MFC_L3720CDW_series");
    assert_eq!(b.title(), "Brother MFC-L3720CDW series", "the model, not the queue name");
    assert_eq!(b.model(), "Brother MFC-L3720CDW series");
    assert_eq!(b.state, PrinterState::Idle);
    assert!(b.accepting && !b.shared && !b.temporary);
    assert!(b.reasons.is_empty(), "\"none\" is not a reason");
    assert_eq!(b.status(), "Ready");
    // cups-browsed's queue: it comes back by itself, so it is not ours to remove.
    assert!(b.automatic());
    assert_eq!(b.connection(), "Network");
    assert_eq!(b.markers.len(), 4);
    assert_eq!(b.supplies().as_deref(), Some("Toner 90 %, 90 %, 90 %, 90 %"));
}

#[test]
fn test_no_default_printer_is_not_an_error() {
    let r = decode(DEFAULT_NONE);
    assert_eq!(r.status, ipp::status::NOT_FOUND);
    assert_eq!(r.message().as_deref(), Some("No default printer."));
    assert_eq!(read_default(&r), None);
    assert!(printers().iter().all(|p| !p.is_default));
    let marked = read_printers(&decode(PRINTERS), Some("brother_mfc_l3720cdw_series"));
    assert!(marked[0].is_default, "queue names are case-insensitive");
}

#[test]
fn test_real_job_history() {
    let jobs = read_jobs(&decode(JOBS));
    assert_eq!(jobs.len(), 1);
    let j = &jobs[0];
    assert_eq!(j.id, 1);
    assert_eq!(j.printer, "Brother_MFC_L3720CDW_series");
    assert_eq!(j.title(), "Test Page");
    assert_eq!(j.state, JobState::Completed);
    assert!(!j.state.active());
    assert_eq!(j.status(None), "Printed, 2 pages");
    assert_eq!(j.kbytes, 1);
    assert!(j.created > 1_700_000_000);
}

#[test]
fn test_real_devices_offer_one_brother_driverless() {
    let devices = read_devices(&decode(DEVICES));
    assert_eq!(devices.len(), 10, "eight bare backends and two URIs for the Brother");
    let found = found_printers(&devices, &[]);
    assert_eq!(found.len(), 1, "the dnssd and ipps URIs are one printer: {found:#?}");
    let f = &found[0];
    assert_eq!(f.device.uri, BROTHER_IPPS, "driverless IPPS wins over dnssd");
    assert!(f.device.driverless());
    assert_eq!(f.device.title(), "Brother MFC-L3720CDW series");
    assert_eq!(f.connection(), "Network");
    assert_eq!(f.added_as, None);
}

#[test]
fn test_a_found_printer_cups_browsed_already_added_is_marked() {
    let devices = read_devices(&decode(DEVICES));
    let found = found_printers(&devices, &printers());
    assert_eq!(found[0].added_as.as_deref(), Some("Brother MFC-L3720CDW series"));
}

#[test]
fn test_real_drivers_recommend_driverless_and_never_fax() {
    let devices = read_devices(&decode(DEVICES));
    let dev = found_printers(&devices, &[])[0].device.clone();
    let all = read_drivers(&decode(PPDS));
    assert_eq!(all.len(), 2);
    let offered = drivers_for(&dev, &all);
    assert_eq!(offered.len(), 1, "{offered:#?}");
    assert_eq!(offered[0].name, format!("driverless:{BROTHER_IPPS}"));
    assert!(offered[0].is_driverless());
    assert!(all.iter().any(Driver::is_fax), "the fixture has the fax driver to refuse");
}

// --- the wire format ---------------------------------------------------------------

#[test]
fn test_requests_encode_as_ipp_2_with_charset_first() {
    let b = req_printers("pat").encode();
    assert_eq!(&b[..2], &[2, 0]);
    assert_eq!(u16::from_be_bytes([b[2], b[3]]), ipp::op::CUPS_GET_PRINTERS);
    assert_eq!(u32::from_be_bytes([b[4], b[5], b[6], b[7]]), 1);
    assert_eq!(b[8], ipp::group::OPERATION);
    assert_eq!(b[9], tag::CHARSET);
    assert_eq!(&b[12..30], b"attributes-charset");
    assert_eq!(*b.last().unwrap(), ipp::group::END);
}

#[test]
fn test_requests_decode_back_with_sets_folded() {
    // A request and a response share the layout (op id where the status is),
    // so the decoder reads back what the encoder wrote.
    let r = Response::decode(&req_printers("pat").encode()).unwrap();
    let g = &r.groups[0];
    assert_eq!(g.str("requesting-user-name"), Some("pat"));
    assert_eq!(g.strs("requested-attributes"), PRINTER_ATTRS.iter().map(|s| s.to_string()).collect::<Vec<_>>());
}

#[test]
fn test_add_request_has_the_printer_group() {
    let req = req_add("pat", "Office Laser", BROTHER_IPPS, "driverless:x", "Office", "Upstairs");
    let r = Response::decode(&req.encode()).unwrap();
    assert_eq!(r.status, ipp::op::CUPS_ADD_MODIFY_PRINTER);
    let op = &r.groups[0];
    assert_eq!(op.str("printer-uri"), Some("ipp://localhost/printers/Office%20Laser"));
    assert_eq!(op.get("ppd-name").unwrap().values[0], Value::Str(tag::NAME, "driverless:x".into()));
    let p = r.groups(ipp::group::PRINTER).next().expect("a printer group");
    assert_eq!(p.str("device-uri"), Some(BROTHER_IPPS));
    assert_eq!(p.str("printer-info"), Some("Office"));
    assert_eq!(p.str("printer-location"), Some("Upstairs"));
    assert_eq!(p.bool("printer-is-accepting-jobs"), Some(true));
    assert_eq!(p.bool("printer-is-shared"), Some(false), "a new printer is not shared on the network");
    assert_eq!(p.int("printer-state"), Some(3), "enabled (idle), like lpadmin -E");
}

#[test]
fn test_test_page_carries_the_banner_after_the_attributes() {
    let page = b"#PDF-BANNER\nTemplate default-testpage.pdf\n".to_vec();
    let req = req_test_page("pat", "Office", page.clone());
    let b = req.encode();
    assert!(b.ends_with(&[&[3u8][..], &page].concat()), "document data follows the end tag");
    let r = Response::decode(&b).unwrap();
    assert_eq!(r.groups[0].str("document-format"), Some("application/vnd.cups-pdf-banner"));
    assert_eq!(r.groups[0].str("job-name"), Some("Test Page"));
}

#[test]
fn test_test_page_format_follows_the_file() {
    // The image's testprint is cups-filters' PDF banner; CUPS' own is
    // #CUPS-BANNER. Declaring the wrong one is refused ("Unsupported format").
    assert_eq!(test_page_format(b"#PDF-BANNER\nTemplate x.pdf"), "application/vnd.cups-pdf-banner");
    assert_eq!(test_page_format(b"#CUPS-BANNER\nShow printer-name"), "application/vnd.cups-banner");
    assert_eq!(test_page_format(b"%PDF-1.7"), "application/pdf");
    assert_eq!(test_page_format(b"anything"), "application/octet-stream");
}

#[test]
fn test_job_requests_name_the_job() {
    for (req, op) in [
        (req_cancel_job("pat", 42), ipp::op::CANCEL_JOB),
        (req_hold_job("pat", 42), ipp::op::HOLD_JOB),
        (req_release_job("pat", 42), ipp::op::RELEASE_JOB),
    ] {
        let r = Response::decode(&req.encode()).unwrap();
        assert_eq!(r.status, op);
        assert_eq!(r.groups[0].int("job-id"), Some(42));
    }
    let r = Response::decode(&req_jobs("pat", "not-completed", None).encode()).unwrap();
    assert_eq!(r.groups[0].bool("my-jobs"), Some(false), "an admin sees everyone's jobs");
    assert_eq!(r.groups[0].str("which-jobs"), Some("not-completed"));
}

#[test]
fn test_decode_skips_collections_and_unknown_tags() {
    let mut b = vec![2, 0, 0, 0, 0, 0, 0, 7, ipp::group::PRINTER];
    let mut put = |t: u8, name: &str, v: &[u8]| {
        b.push(t);
        b.extend_from_slice(&(name.len() as u16).to_be_bytes());
        b.extend_from_slice(name.as_bytes());
        b.extend_from_slice(&(v.len() as u16).to_be_bytes());
        b.extend_from_slice(v);
    };
    put(tag::KEYWORD, "printer-name", b"before");
    put(tag::BEG_COLLECTION, "media-col-default", b"");
    put(tag::MEMBER_NAME, "", b"media-size");
    put(tag::BEG_COLLECTION, "", b"");
    put(tag::MEMBER_NAME, "", b"x-dimension");
    put(tag::INTEGER, "", &21000i32.to_be_bytes());
    put(tag::END_COLLECTION, "", b"");
    put(tag::END_COLLECTION, "", b"");
    put(tag::RESOLUTION, "printer-resolution-default", &[0, 0, 2, 88, 0, 0, 2, 88, 3]);
    put(tag::TEXT, "printer-info", b"after");
    b.push(ipp::group::END);
    let r = Response::decode(&b).unwrap();
    let g = &r.groups[0];
    assert_eq!(g.str("printer-info"), Some("after"));
    assert!(g.get("printer-resolution-default").is_some());
    assert_eq!(g.attrs.len(), 4, "{:#?}", g.attrs);
}

#[test]
fn test_decode_refuses_a_cut_short_answer() {
    assert!(Response::decode(&PRINTERS[..PRINTERS.len() - 10]).is_err());
    assert!(Response::decode(&[2, 0, 0]).is_err());
}

#[test]
fn test_http_content_length_and_chunked() {
    let plain = b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\nContent-Type: application/ipp\r\n\r\nabcEXTRA";
    assert_eq!(parse_http(plain).unwrap(), (200, b"abc".to_vec()));
    let chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\na;x=y\r\n0123456789\r\n0\r\n\r\n";
    assert_eq!(parse_http(chunked).unwrap(), (200, b"abc0123456789".to_vec()));
    assert_eq!(parse_http(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n").unwrap().0, 401);
    assert!(parse_http(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nab").is_err());
    assert!(parse_http(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc").is_err());
    assert!(parse_http(b"").is_err());
}

#[test]
fn test_user_from_passwd() {
    let passwd = "root:x:0:0::/root:/bin/sh\nbeholder:x:1000:1000::/home/beholder:/bin/zsh\n";
    assert_eq!(user_for_uid(passwd, "1000").as_deref(), Some("beholder"));
    assert_eq!(user_for_uid(passwd, "0").as_deref(), Some("root"));
    assert_eq!(user_for_uid(passwd, "1001"), None);
}

// --- what people read --------------------------------------------------------------

fn printer(state: PrinterState, reasons: &[&str], message: &str) -> Printer {
    Printer {
        name: "Office".into(),
        state,
        reasons: reasons.iter().map(|r| r.to_string()).collect(),
        message: message.into(),
        accepting: true,
        device_uri: "usb://Brother/HL-L2350DW?serial=X".into(),
        ..Default::default()
    }
}

#[test]
fn test_printer_status_text() {
    assert_eq!(printer(PrinterState::Idle, &["media-empty-error"], "").status(), "Ready · Out of paper");
    assert_eq!(printer(PrinterState::Stopped, &["paused"], "").status(), "Paused");
    assert_eq!(
        printer(PrinterState::Stopped, &[], "The printer is not responding.").status(),
        "Stopped · The printer is not responding."
    );
    assert_eq!(
        printer(PrinterState::Printing, &["toner-low-report", "cups-waiting-for-job-completed"], "").status(),
        "Printing · Toner or ink low"
    );
    let mut p = printer(PrinterState::Idle, &[], "");
    p.accepting = false;
    assert_eq!(p.status(), "Ready · Not accepting documents");
    assert_eq!(p.title(), "Office", "no description and no model: the queue name");
    p.make_model = "HP LaserJet 1020, hpcups 3.24".into();
    assert_eq!(p.title(), "HP LaserJet 1020");
    p.info = "Kitchen printer".into();
    assert_eq!(p.title(), "Kitchen printer");
    assert_eq!(p.connection(), "USB");
    assert!(!p.automatic());
}

#[test]
fn test_reason_words() {
    assert_eq!(reason_text("media-jam-error").as_deref(), Some("Paper jam"));
    assert_eq!(reason_text("cover-open-warning").as_deref(), Some("Cover open"));
    assert_eq!(reason_text("cups-missing-filter-warning").as_deref(), Some("A driver filter is missing"));
    assert_eq!(reason_text("cups-waiting-for-job-completed"), None);
    assert_eq!(reason_text("other-report"), None);
    assert_eq!(reason_text("spool-area-full").as_deref(), Some("Spool area full"));
    assert_eq!(reason_text("none"), None);
}

#[test]
fn test_supplies_with_names() {
    let mut p = printer(PrinterState::Idle, &[], "");
    p.markers = vec![
        Marker { name: "Black".into(), kind: "ink-cartridge".into(), level: Some(80) },
        Marker { name: "Cyan".into(), kind: "ink-cartridge".into(), level: Some(15) },
        Marker { name: "Waste".into(), kind: "waste-ink".into(), level: None },
    ];
    assert_eq!(p.supplies().as_deref(), Some("Black 80 % · Cyan 15 %"));
    p.markers.clear();
    assert_eq!(p.supplies(), None);
}

#[test]
fn test_job_status_follows_the_printer() {
    let j = Job { id: 7, state: JobState::Pending, ..Default::default() };
    assert_eq!(j.title(), "Document 7");
    assert_eq!(j.status(None), "Waiting");
    assert_eq!(j.status(Some(&printer(PrinterState::Stopped, &["paused"], ""))), "Waiting: the printer is paused");
    let j = Job { state: JobState::Processing, printer_message: "Printing page 2, 50%".into(), ..j };
    assert_eq!(j.status(None), "Printing: Printing page 2, 50%");
    assert!(JobState::Held.active() && JobState::Stopped.active());
    assert!(!JobState::Canceled.active() && !JobState::Aborted.active());
}

#[test]
fn test_small_formatting() {
    assert_eq!(ago(1000, 990), "just now");
    assert_eq!(ago(10_000, 10_000 - 300), "5 min ago");
    assert_eq!(ago(100_000, 100_000 - 7200), "2 h ago");
    assert_eq!(ago(200_000, 200_000 - 90_000), "yesterday");
    assert_eq!(ago(1_000_000, 1_000_000 - 3 * 86_400), "3 days ago");
    assert_eq!(ago(5, 0), "");
    assert_eq!(size_text(0), "");
    assert_eq!(size_text(12), "12 KB");
    assert_eq!(size_text(2048), "2.0 MB");
}

#[test]
fn test_uris_and_names_round_trip() {
    assert_eq!(queue_from_uri("ipp://localhost:631/printers/Office%20Laser/"), "Office Laser");
    assert_eq!(percent_decode("a%2"), "a%2");
    assert_eq!(percent_decode("caf%C3%A9"), "café");
    assert_eq!(percent_encode("Office Laser#2"), "Office%20Laser%232");
    assert_eq!(printer_uri("HP_LaserJet"), "ipp://localhost/printers/HP_LaserJet");
}

// --- choosing ------------------------------------------------------------------------

#[test]
fn test_queue_names() {
    assert_eq!(queue_name("Brother MFC-L3720CDW series", &[]), "Brother_MFC-L3720CDW_series");
    assert_eq!(queue_name("  HP / LaserJet #1 ", &[]), "HP_LaserJet_1");
    assert_eq!(queue_name("", &[]), "Printer");
    let taken = vec!["brother_mfc-l3720cdw_series".to_string(), "Brother_MFC-L3720CDW_series_2".to_string()];
    assert_eq!(queue_name("Brother MFC-L3720CDW series", &taken), "Brother_MFC-L3720CDW_series_3");
    assert!(queue_name(&"x".repeat(300), &[]).len() <= 127);
}

#[test]
fn test_addresses() {
    assert_eq!(uri_from_address(" 192.168.1.20 ").unwrap(), "ipp://192.168.1.20/ipp/print");
    assert_eq!(uri_from_address("printer.local:631").unwrap(), "ipp://printer.local:631/ipp/print");
    assert_eq!(uri_from_address("[fe80::1]").unwrap(), "ipp://[fe80::1]/ipp/print");
    assert_eq!(uri_from_address("IPPS://p.lan/ipp/print").unwrap(), "ipps://p.lan/ipp/print");
    assert_eq!(uri_from_address("socket://10.0.0.9:9100").unwrap(), "socket://10.0.0.9:9100");
    assert!(uri_from_address("smb://server/share").is_err());
    assert!(uri_from_address("ipp:///x").is_err());
    assert!(uri_from_address("my printer").is_err());
    assert!(uri_from_address("").is_err());
    assert!(speaks_ipp("ipp://x/ipp/print") && !speaks_ipp("socket://x:9100"));
}

fn ppd(name: &str, mm: &str, id: &str) -> Driver {
    Driver { name: name.into(), make_model: mm.into(), device_id: id.into() }
}

#[test]
fn test_usb_printer_gets_its_model_driver_simplified_first() {
    let dev = Device {
        uri: "usb://EPSON/Stylus%20Photo%20R300?serial=1".into(),
        class: "direct".into(),
        info: "EPSON Stylus Photo R300".into(),
        make_model: "EPSON Stylus Photo R300".into(),
        id: "MFG:EPSON;MDL:Stylus Photo R300;CMD:ESCPL2;".into(),
        location: String::new(),
    };
    let all = vec![
        ppd("gutenprint.5.2://escp2-r300/expert", "Epson Stylus Photo R300 - CUPS+Gutenprint v5.2.14", ""),
        ppd("gutenprint.5.2://escp2-r300/simple", "Epson Stylus Photo R300 - CUPS+Gutenprint v5.2.14 Simplified", ""),
        ppd("gutenprint.5.2://escp2-r200/expert", "Epson Stylus Photo R200 - CUPS+Gutenprint v5.2.14", ""),
        ppd("drv:///sample.drv/generic.ppd", "Generic PostScript Printer", ""),
    ];
    let offered = drivers_for(&dev, &all);
    assert!(!dev.driverless());
    assert_eq!(
        offered.iter().map(|d| d.name.as_str()).collect::<Vec<_>>(),
        ["gutenprint.5.2://escp2-r300/simple", "gutenprint.5.2://escp2-r300/expert"]
    );
}

#[test]
fn test_hp_device_id_matches_hewlett_packard_drivers() {
    let dev = Device {
        uri: "hp:/usb/HP_LaserJet_1020?serial=X".into(),
        make_model: "HP LaserJet 1020".into(),
        id: "MFG:Hewlett-Packard;MDL:HP LaserJet 1020;".into(),
        ..Default::default()
    };
    let all = vec![
        ppd("lsb/usr/hplip/hp-laserjet_1020.ppd", "HP LaserJet 1020, hpcups 3.24", "MFG:HP;MDL:HP LaserJet 1020;"),
        ppd("lsb/usr/hplip/hp-laserjet_1022.ppd", "HP LaserJet 1022, hpcups 3.24", "MFG:HP;MDL:HP LaserJet 1022;"),
        ppd("lsb/usr/hplip/hpfax.ppd", "HP Fax, hpcups 3.24", "MFG:HP;MDL:Fax;"),
    ];
    let offered = drivers_for(&dev, &all);
    assert_eq!(offered.len(), 1, "{offered:#?}");
    assert_eq!(offered[0].name, "lsb/usr/hplip/hp-laserjet_1020.ppd");
    assert_eq!(connection_of(&dev.uri), "USB");
}

#[test]
fn test_ipp_usb_printer_is_usb_and_driverless() {
    let dev = Device {
        uri: "ipp://localhost:60000/ipp/print".into(),
        info: "Canon G3010 series".into(),
        make_model: "Canon G3010 series".into(),
        ..Default::default()
    };
    assert_eq!(connection_of(&dev.uri), "USB");
    assert!(dev.driverless());
    // CUPS has not listed a driverless driver for it yet: one is made on request.
    let offered = drivers_for(&dev, &[]);
    assert_eq!(offered[0].name, "driverless:ipp://localhost:60000/ipp/print");
}

#[test]
fn test_driver_search() {
    let all = vec![
        ppd("a", "Generic PCL 6/PCL XL Printer Foomatic/pxlcolor", ""),
        ppd("b/expert", "Generic PCL 5 Printer - CUPS+Gutenprint v5.2.14", ""),
        ppd("b/simple", "Generic PCL 5 Printer - CUPS+Gutenprint v5.2.14 Simplified", ""),
        ppd("c", "Generic PostScript Printer", ""),
        ppd("driverless:ipp://x", "Driverless x", ""),
    ];
    let hits = search_drivers(&all, "generic pcl 5", 10);
    assert_eq!(hits.iter().map(|d| d.name.as_str()).collect::<Vec<_>>(), ["b/simple", "b/expert"]);
    assert_eq!(search_drivers(&all, "postscript", 10)[0].name, "c");
    assert!(search_drivers(&all, "   ", 10).is_empty());
    assert_eq!(search_drivers(&all, "generic", 2).len(), 2);
}

#[test]
fn test_bare_backends_and_cups_browsed_queues_are_not_found_printers() {
    for uri in ["socket", "ipp", "beh", "implicitclass://Office/"] {
        let d = Device { uri: uri.into(), ..Default::default() };
        assert!(!d.is_printer(), "{uri}");
    }
    assert!(Device { uri: "socket://10.0.0.5".into(), ..Default::default() }.is_printer());
}

#[test]
fn test_explain_auth_failures_name_the_group() {
    let e = explain(ipp::status::NOT_AUTHORIZED, None, "add the printer");
    assert!(e.contains("wheel") && e.starts_with("Could not add the printer"));
    let e = explain(0x0500, Some("Unable to copy PPD file."), "add the printer");
    assert_eq!(e, "Could not add the printer: Unable to copy PPD file.");
}

#[test]
fn test_snapshot_listing() {
    let s = Snapshot { printers: printers(), queue: Vec::new(), history: read_jobs(&decode(JOBS)) };
    let out = format_snapshot(&s, s.history[0].created + 120);
    assert!(out.contains("Brother MFC-L3720CDW series  [Brother_MFC_L3720CDW_series]  Network · Ready"), "{out}");
    assert!(out.contains("queue: empty"));
    assert!(out.contains("Test Page  Brother_MFC_L3720CDW_series · 2 min ago · Printed, 2 pages"), "{out}");
}
