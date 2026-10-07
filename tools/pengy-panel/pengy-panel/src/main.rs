//! # pengy-panel — pure-Rust Wayland layer-shell status bar (binary)
//!
//! This crate is the thin, side-effecting edge of the project: CLI parsing,
//! config loading, the `Backend` seam (task 6), the `Panel` driver, and the
//! real Wayland `layer-shell` backend (task 11, in [`crate::wayland`]). All
//! headless-testable logic lives in the [`widgets`] core crate, and this
//! binary depends on it — never the other way around (see ARCHITECTURE.md).
//!
//! The binary is deliberately *honest*: it parses its arguments, loads and
//! validates the config, and drives the panel on the live compositor. When it
//! cannot attach to a Wayland compositor (or the compositor lacks
//! `layer-shell`) it reports the reason and exits 69 rather than pretending
//! the panel started. The one-shot `--headless --print-layout` mode never
//! touches a compositor.

pub mod backend;
pub mod cli;
pub mod config;
pub mod headless;
pub mod live_rss;
pub mod measure_rss;
pub mod panel;
pub mod print_layout;
pub mod wayland;

/// Exit code for a CLI usage error.
const EXIT_USAGE: i32 = 2;
/// Exit code when the configuration file is unreadable or invalid (EX_DATAERR).
const EXIT_DATAERR: i32 = 65;
/// Exit code when the program cannot do what it was asked to do yet.
const EXIT_NOT_READY: i32 = 69;
/// Exit code when `--measure-rss` reports the idle RSS is over the limit.
const EXIT_MEASURE_EXCEEDED: i32 = 1;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let command = match cli::parse(args.iter().cloned()) {
        Ok(command) => command,
        Err(err) => {
            eprintln!("{}", err.to_diagnostic(&cli::usage()));
            std::process::exit(EXIT_USAGE);
        }
    };

    match command {
        cli::Command::Version => {
            println!("{}", widgets::AppInfo::new().format());
        }
        cli::Command::Help => {
            print!("{}", cli::usage());
        }
        cli::Command::Run(args) => {
            let config = if let Some(path) = &args.config {
                match load_config(path) {
                    Ok(config) => {
                        eprintln!(
                            "pengy-panel: config OK ({} widget(s))",
                            config.widgets.len()
                        );
                        config
                    }
                    Err(err) => {
                        eprintln!("pengy-panel: invalid config {}: {}", path.display(), err);
                        std::process::exit(EXIT_DATAERR);
                    }
                }
            } else {
                widgets::PanelConfig::default()
            };

            if args.print_layout {
                // `--width` is guaranteed present: cli::parse rejects
                // `--print-layout` without it (one-shot mode has no backend
                // to negotiate a size).
                let width = args
                    .width
                    .expect("cli validation guarantees --width with --print-layout");
                println!("{}", print_layout::layout_line(&config, width, args.epoch));
                return;
            }

            if args.measure_rss {
                // No compositor needed: build the real pipeline, run it to
                // steady state holding the ARGB frame buffer, and sample the
                // process's VmRSS. `--width` is optional here (a representative
                // value); the gate limit defaults to the documented 15 MiB
                // target. Exit 1 when the limit is exceeded, else 0.
                let width = args.width.unwrap_or(measure_rss::DEFAULT_MEASURE_WIDTH_CELLS);
                let limit_mib = args.max_mib.unwrap_or(measure_rss::IDLE_RSS_TARGET_MIB);
                match measure_rss::measure_idle_rss(&config, width, args.epoch) {
                    Some(m) => {
                        println!("{}", m.format_line(limit_mib));
                        if !m.within(limit_mib) {
                            std::process::exit(EXIT_MEASURE_EXCEEDED);
                        }
                    }
                    None => {
                        eprintln!("pengy-panel: cannot read the process memory map (VmRSS)");
                        std::process::exit(EXIT_DATAERR);
                    }
                }
                return;
            }

            if args.measure_rss_live {
                // The live complement of `--measure-rss`: run the REAL Wayland
                // backend to steady state against a live compositor and sample
                // the process's resident set size while connected — the
                // headless number plus the Wayland socket/client overhead.
                // `--max-mib` reuses the documented 15 MiB target; exit 1 when
                // over. A failed attach (no compositor / no layer-shell) is
                // reported and exits 69.
                let limit_mib = args.max_mib.unwrap_or(measure_rss::IDLE_RSS_TARGET_MIB);
                match live_rss::measure_live_idle_rss(&config) {
                    Ok(m) => {
                        println!("{}", m.format_line(limit_mib));
                        if !m.within(limit_mib) {
                            std::process::exit(EXIT_MEASURE_EXCEEDED);
                        }
                    }
                    Err(err) => {
                        eprintln!("{}", err.diagnostic());
                        std::process::exit(match &err {
                            // Not attached: the same "can't do this yet" path
                            // the Run path uses for a missing compositor.
                            live_rss::LiveRssError::NoCompositor(_) => EXIT_NOT_READY,
                            live_rss::LiveRssError::VmRSSUnreadable => EXIT_DATAERR,
                        });
                    }
                }
                return;
            }

            // The real Wayland `layer-shell` backend (task 11): build the
            // widget list from the config, drive it through the `Panel` on a
            // live `WaylandBackend`, and let the event loop run until the
            // compositor closes the surface. A failure to attach (no
            // compositor, no layer-shell) is reported and exits 69.
            let widget_list = widgets::build_widgets(&config);
            let source = widgets::SysfsSource::new();
            let panel = panel::Panel::new(widget_list, Box::new(source), config.bar);
            let mut backend = wayland::WaylandBackend::new();
            match panel.run(&mut backend) {
                Ok(_report) => {} // clean Quit: the compositor asked us to go away.
                Err(err) => {
                    eprintln!("pengy-panel: {err}");
                    std::process::exit(EXIT_NOT_READY);
                }
            }
        }
    }
}

/// Read and parse a config file, reporting I/O problems as config errors.
fn load_config(
    path: &std::path::Path,
) -> Result<widgets::PanelConfig, config::ConfigError> {
    let text = std::fs::read_to_string(path).map_err(|e| {
        config::ConfigError::Syntax(format!("cannot read {}: {e}", path.display()))
    })?;
    config::parse(&text)
}

#[cfg(test)]
mod tests {
    use super::cli::*;
    use std::path::PathBuf;

    fn parse_str(args: &[&str]) -> Result<Command, CliError> {
        parse(args.iter().map(|s| s.to_string()))
    }

    /// The `RunArgs` shape in tests: the default "start the panel" command.
    fn default_run() -> Command {
        Command::Run(RunArgs {
            config: None,
            headless: false,
            print_layout: false,
            measure_rss: false,
            measure_rss_live: false,
            width: None,
            epoch: None,
            max_mib: None,
        })
    }

    #[test]
    fn no_args_is_run_with_no_config() {
        assert_eq!(parse_str(&[]).unwrap(), default_run());
    }

    #[test]
    fn version_flag() {
        assert_eq!(parse_str(&["--version"]).unwrap(), Command::Version);
    }

    #[test]
    fn help_flags() {
        assert_eq!(parse_str(&["--help"]).unwrap(), Command::Help);
        assert_eq!(parse_str(&["-h"]).unwrap(), Command::Help);
    }

    #[test]
    fn config_flag_long_and_short() {
        let with_config = |p: &str| {
            let mut run = default_run();
            if let Command::Run(args) = &mut run {
                args.config = Some(PathBuf::from(p));
            }
            run
        };
        assert_eq!(parse_str(&["--config", "panel.toml"]).unwrap(), with_config("panel.toml"));
        assert_eq!(parse_str(&["-c", "panel.toml"]).unwrap(), with_config("panel.toml"));
    }

    #[test]
    fn config_flag_equals_form() {
        assert_eq!(
            parse_str(&["--config=/etc/pengy/panel.toml"]).unwrap(),
            with_config_helper("/etc/pengy/panel.toml")
        );
    }

    fn with_config_helper(p: &str) -> Command {
        let mut run = default_run();
        if let Command::Run(args) = &mut run {
            args.config = Some(PathBuf::from(p));
        }
        run
    }

    #[test]
    fn version_wins_even_if_it_comes_after_config() {
        assert_eq!(
            parse_str(&["-c", "panel.toml", "--version"]).unwrap(),
            Command::Version
        );
    }

    #[test]
    fn config_without_value_is_an_error() {
        assert_eq!(
            parse_str(&["--config"]).unwrap_err(),
            CliError::FlagRequiresValue {
                flag: "--config".to_string()
            }
        );
    }

    #[test]
    fn unknown_flag_is_an_error() {
        assert_eq!(
            parse_str(&["--nope"]).unwrap_err(),
            CliError::UnknownArg { arg: "--nope".to_string() }
        );
    }

    #[test]
    fn headless_print_layout_pair_is_accepted() {
        let mut run = default_run();
        if let Command::Run(args) = &mut run {
            args.headless = true;
            args.print_layout = true;
            args.width = Some(60);
            args.epoch = Some(1_000_000_000);
        }
        assert_eq!(
            parse_str(&[
                "--headless",
                "--print-layout",
                "--width",
                "60",
                "--epoch",
                "1000000000",
            ])
            .unwrap(),
            run
        );
    }

    #[test]
    fn width_and_epoch_accept_the_equals_form() {
        let mut run = default_run();
        if let Command::Run(args) = &mut run {
            args.headless = true;
            args.print_layout = true;
            args.width = Some(42);
            args.epoch = Some(-5);
        }
        assert_eq!(
            parse_str(&[
                "--headless",
                "--print-layout",
                "--width=42",
                "--epoch=-5",
            ])
            .unwrap(),
            run
        );
    }

    #[test]
    fn measure_rss_with_headless_is_accepted() {
        let mut run = default_run();
        if let Command::Run(args) = &mut run {
            args.headless = true;
            args.measure_rss = true;
            args.width = Some(200);
            args.max_mib = Some(20);
        }
        assert_eq!(
            parse_str(&["--headless", "--measure-rss", "--width", "200", "--max-mib", "20"]) .unwrap(),
            run
        );
    }

    #[test]
    fn measure_rss_max_mib_equals_form_and_defaults() {
        // `--max-mib=` form is accepted and parsed.
        let mut run = default_run();
        if let Command::Run(args) = &mut run {
            args.headless = true;
            args.measure_rss = true;
            args.max_mib = Some(8);
        }
        assert_eq!(parse_str(&["--headless", "--measure-rss", "--max-mib=8"]).unwrap(), run);

        // `--width` and `--max-mib` are optional for --measure-rss: a bare
        // `--headless --measure-rss` is valid.
        let mut bare = default_run();
        if let Command::Run(args) = &mut bare {
            args.headless = true;
            args.measure_rss = true;
        }
        assert_eq!(parse_str(&["--headless", "--measure-rss"]).unwrap(), bare);
    }

    #[test]
    fn measure_rss_without_headless_is_an_error() {
        assert_eq!(
            parse_str(&["--measure-rss"]).unwrap_err(),
            CliError::MeasureRssRequiresHeadless
        );
    }

    #[test]
    fn print_layout_and_measure_rss_are_mutually_exclusive() {
        assert_eq!(
            parse_str(&["--headless", "--print-layout", "--width", "5", "--measure-rss"]).unwrap_err(),
            CliError::OneShotModesMutuallyExclusive
        );
    }

    /// The live RSS mode is accepted on its own (it needs a compositor, not
    /// `--headless`), and may carry a `--max-mib` gate.
    #[test]
    fn measure_rss_live_is_accepted_with_optional_max_mib() {
        let bare = parse_str(&["--measure-rss-live"]).unwrap();
        let mut expected = default_run();
        if let Command::Run(args) = &mut expected {
            args.measure_rss_live = true;
        }
        assert_eq!(bare, expected);

        let with_limit = parse_str(&["--measure-rss-live", "--max-mib", "15"]).unwrap();
        let mut expected = default_run();
        if let Command::Run(args) = &mut expected {
            args.measure_rss_live = true;
            args.max_mib = Some(15);
        }
        assert_eq!(with_limit, expected);
    }

    /// `--measure-rss-live` is a *live* mode, so `--headless` is a
    /// contradiction (it needs a compositor).
    #[test]
    fn measure_rss_live_is_incompatible_with_headless() {
        assert_eq!(
            parse_str(&["--headless", "--measure-rss-live"]).unwrap_err(),
            CliError::MeasureRssLiveIncompatibleWithHeadless
        );
    }

    /// `--width` and `--epoch` are headless-only: the live width comes from the
    /// compositor and the clock is live, so both are rejected for the live mode.
    #[test]
    fn measure_rss_live_rejects_width_and_epoch() {
        assert_eq!(
            parse_str(&["--measure-rss-live", "--width", "10"]).unwrap_err(),
            CliError::MeasureRssLiveRejectsFlag {
                flag: "--width".to_string()
            }
        );
        assert_eq!(
            parse_str(&["--measure-rss-live", "--epoch", "5"]).unwrap_err(),
            CliError::MeasureRssLiveRejectsFlag {
                flag: "--epoch".to_string()
            }
        );
    }

    /// Any two one-shot modes together are mutually exclusive (the live mode
    /// vs. either headless one, and the two headless ones among themselves).
    #[test]
    fn any_two_one_shot_modes_are_mutually_exclusive() {
        for args in [
            vec!["--measure-rss-live", "--print-layout", "--width", "5"],
            vec!["--measure-rss-live", "--measure-rss", "--headless"],
            vec!["--headless", "--print-layout", "--width", "5", "--measure-rss"],
        ] {
            assert_eq!(
                parse_str(&args).unwrap_err(),
                CliError::OneShotModesMutuallyExclusive,
                "unexpected for {args:?}"
            );
        }
    }

    #[test]
    fn max_mib_without_a_mode_is_an_error() {
        assert_eq!(
            parse_str(&["--max-mib", "5"]).unwrap_err(),
            CliError::HeadlessFlagWithoutPrintLayout {
                flag: "--max-mib".to_string()
            }
        );
    }

    #[test]
    fn non_numeric_max_mib_is_an_error() {
        assert_eq!(
            parse_str(&["--headless", "--measure-rss", "--max-mib", "abc"]).unwrap_err(),
            CliError::BadNumber {
                flag: "--max-mib".to_string(),
                value: "abc".to_string()
            }
        );
    }

    #[test]
    fn print_layout_without_headless_is_an_error() {
        assert_eq!(
            parse_str(&["--print-layout", "--width", "5"]).unwrap_err(),
            CliError::PrintLayoutRequiresHeadless
        );
    }

    #[test]
    fn headless_flag_without_print_layout_is_an_error() {
        assert_eq!(
            parse_str(&["--headless"]).unwrap_err(),
            CliError::HeadlessFlagWithoutPrintLayout {
                flag: "--headless".to_string()
            }
        );
        assert_eq!(
            parse_str(&["--width", "10"]).unwrap_err(),
            CliError::HeadlessFlagWithoutPrintLayout {
                flag: "--width".to_string()
            }
        );
        assert_eq!(
            parse_str(&["--epoch", "1"]).unwrap_err(),
            CliError::HeadlessFlagWithoutPrintLayout {
                flag: "--epoch".to_string()
            }
        );
    }

    #[test]
    fn print_layout_without_width_is_an_error() {
        assert_eq!(
            parse_str(&["--headless", "--print-layout"]).unwrap_err(),
            CliError::PrintLayoutRequiresWidth
        );
    }

    #[test]
    fn non_numeric_width_or_epoch_is_an_error() {
        assert_eq!(
            parse_str(&["--headless", "--print-layout", "--width", "abc"]).unwrap_err(),
            CliError::BadNumber {
                flag: "--width".to_string(),
                value: "abc".to_string()
            }
        );
        // Negative width is not a u32; negative epoch is fine.
        assert_eq!(
            parse_str(&["--headless", "--print-layout", "--width", "-3"]).unwrap_err(),
            CliError::BadNumber {
                flag: "--width".to_string(),
                value: "-3".to_string()
            }
        );
    }

    #[test]
    fn width_without_value_is_an_error() {
        assert_eq!(
            parse_str(&["--headless", "--print-layout", "--width"]).unwrap_err(),
            CliError::FlagRequiresValue { flag: "--width".to_string() }
        );
    }

    #[test]
    fn bare_positional_is_an_error() {
        assert_eq!(
            parse_str(&["panel.toml"]).unwrap_err(),
            CliError::UnknownArg {
                arg: "panel.toml".to_string()
            }
        );
    }

    #[test]
    fn diagnostics_mention_the_offending_arg_and_usage() {
        let usage = usage();
        let diag = CliError::UnknownArg {
            arg: "--nope".to_string(),
        }
        .to_diagnostic(&usage);
        assert!(diag.contains("`--nope`"));
        assert!(diag.contains("usage: pengy-panel"));
    }
}
