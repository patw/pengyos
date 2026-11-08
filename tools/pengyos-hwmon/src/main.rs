//! PengyOS Hardware Monitor.
//!
//! Three entry points, one data path:
//!   * the libcosmic window (`--features cosmic`, the Desktop image);
//!   * the terminal view (`--features tui`, the Server image; `--tui` anywhere);
//!   * `--check`, one collection pass as text, which needs neither.
//!
//! Everything that reads hardware lives in upstream HWall's `hwall-core`
//! (collectors) and `hwall-app` (settings, rows, alerts), and the limit/colour
//! policy is in `tjmax`. The front-ends are views, so they cannot drift apart.

mod check;

#[cfg(feature = "cosmic")]
mod app;
// tjmax is plain data + policy over hwall-core types, so it stays testable
// without a display (and without libcosmic) - `cargo test` covers it. A build
// with neither front-end uses only part of it (`--check`).
#[cfg_attr(not(any(feature = "cosmic", feature = "tui")), allow(dead_code))]
mod tjmax;
#[cfg(feature = "tui")]
mod tui;

const USAGE: &str =
    "usage: pengyos-hwmon [--tui [--hardware] | --check | --view sensors|hardware|limits]

  (no flag)   the window on a desktop session, the terminal view otherwise
  --tui       the terminal view, even on a desktop session
  --hardware  start the terminal view on the hardware inventory
  --check     one collection pass printed as text, then exit";

#[derive(Debug, PartialEq, Eq)]
enum Mode {
    Check,
    Terminal { hardware: bool },
    Window,
    Usage,
}

/// Which front-end to start. The window needs both the GUI build and a display;
/// anything else that can show the terminal view does, so `pengyos-hwmon` alone
/// does the useful thing over ssh and on the console.
fn mode(args: &[String], has_display: bool, cosmic: bool, tui: bool) -> Mode {
    let has = |flag: &str| args.iter().any(|arg| arg == flag);
    if has("--help") || has("-h") {
        return Mode::Usage;
    }
    if has("--check") {
        return Mode::Check;
    }
    let hardware = has("--hardware");
    if tui && (has("--tui") || hardware || !cosmic || !has_display) {
        return Mode::Terminal { hardware };
    }
    if cosmic {
        return Mode::Window;
    }
    Mode::Usage
}

fn has_display() -> bool {
    ["WAYLAND_DISPLAY", "DISPLAY"]
        .iter()
        .any(|name| std::env::var_os(name).is_some_and(|value| !value.is_empty()))
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let chosen = mode(
        &args,
        has_display(),
        cfg!(feature = "cosmic"),
        cfg!(feature = "tui"),
    );
    match chosen {
        Mode::Check => check::run(),
        #[cfg(feature = "tui")]
        Mode::Terminal { hardware } => {
            if let Err(error) = tui::run(hardware) {
                eprintln!("pengyos-hwmon: {error}");
                std::process::exit(1);
            }
        }
        #[cfg(feature = "cosmic")]
        Mode::Window => {
            if let Err(error) = app::run(&args) {
                eprintln!("pengyos-hwmon: {error}");
                std::process::exit(1);
            }
        }
        Mode::Usage => {
            println!("{USAGE}");
            if !args.iter().any(|arg| arg == "--help" || arg == "-h") {
                std::process::exit(2);
            }
        }
        #[allow(unreachable_patterns)]
        _ => {
            eprintln!("pengyos-hwmon: built without that front-end.\n{USAGE}");
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn desktop_build_opens_the_window_on_a_desktop() {
        assert_eq!(mode(&args(&[]), true, true, true), Mode::Window);
    }

    #[test]
    fn desktop_build_falls_back_to_the_terminal_over_ssh() {
        assert_eq!(
            mode(&args(&[]), false, true, true),
            Mode::Terminal { hardware: false }
        );
    }

    #[test]
    fn tui_flag_wins_on_a_desktop() {
        assert_eq!(
            mode(&args(&["--tui"]), true, true, true),
            Mode::Terminal { hardware: false }
        );
        assert_eq!(
            mode(&args(&["--hardware"]), true, true, true),
            Mode::Terminal { hardware: true }
        );
    }

    #[test]
    fn server_build_is_always_the_terminal() {
        assert_eq!(
            mode(&args(&[]), true, false, true),
            Mode::Terminal { hardware: false }
        );
        assert_eq!(
            mode(&args(&[]), false, false, true),
            Mode::Terminal { hardware: false }
        );
    }

    #[test]
    fn check_works_in_every_build() {
        for (cosmic, tui) in [(false, false), (true, false), (false, true), (true, true)] {
            assert_eq!(mode(&args(&["--check"]), false, cosmic, tui), Mode::Check);
        }
    }

    #[test]
    fn a_bare_build_prints_usage() {
        assert_eq!(mode(&args(&[]), false, false, false), Mode::Usage);
    }
}
