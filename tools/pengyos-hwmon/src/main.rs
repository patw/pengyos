//! PengyOS Hardware Monitor.
//!
//! Two entry points, one data path:
//!   * the libcosmic GUI (`--features cosmic`, built into the Desktop image);
//!   * `--check`, one collection pass as text, which needs no display and is
//!     what a Server TUI will grow from.
//!
//! Everything that reads hardware lives in upstream HWall's `hwall-core`
//! (collectors) and `hwall-app` (settings, rows, alerts). This crate is the
//! view, so the two front-ends cannot drift apart.

mod check;

#[cfg(feature = "cosmic")]
mod app;
// tjmax is plain data + policy over hwall-core types, so it stays testable
// without a display (and without libcosmic) - `cargo test` covers it.
mod tjmax;

#[cfg(feature = "cosmic")]
fn main() -> cosmic::iced::Result {
    let args: Vec<String> = std::env::args().skip(1).collect();
    // --check stays available in the GUI build: it is the same data path with
    // the widgets left out, and it is what a rescue shell or an ssh session uses.
    if args.iter().any(|arg| arg == "--check") {
        check::run();
        return Ok(());
    }
    app::run(&args)
}

#[cfg(not(feature = "cosmic"))]
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|arg| arg == "--check") {
        check::run();
        return;
    }
    eprintln!(
        "pengyos-hwmon: built without --features cosmic (no GUI).\n\
         Try: pengyos-hwmon --check"
    );
    std::process::exit(2);
}
