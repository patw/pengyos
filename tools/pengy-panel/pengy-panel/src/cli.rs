//! Command-line parsing for pengy-panel.
//!
//! Kept as a pure, dependency-free `Iterator`-to-`Result` mapping so it can be
//! unit-tested headlessly with any argument list (see the tests in
//! `main.rs::tests`, which exercise `cli::parse`).

use std::path::PathBuf;

/// What the user asked the program to do, fully resolved after parsing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// Print `pengy-panel <version>` and exit 0.
    Version,
    /// Print usage and exit 0.
    Help,
    /// Start the panel, optionally with a config file, or print the layout
    /// once in headless mode.
    Run(RunArgs),
}

/// Arguments for starting the panel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunArgs {
    /// Path to a TOML config file (`panel.toml`).
    pub config: Option<PathBuf>,
    /// Run without a compositor (paired with `--print-layout`).
    pub headless: bool,
    /// One-shot mode: print the layout as one text line and exit 0
    /// (paired with `--headless`; requires `--width`).
    pub print_layout: bool,
    /// One-shot mode: measure the release binary's idle RSS and exit
    /// (paired with `--headless`; no compositor needed).
    pub measure_rss: bool,
    /// One-shot mode: run the **real** Wayland backend to steady state against
    /// a live compositor and measure the process's idle RSS (the headless
    /// number plus the Wayland socket/client overhead). Requires a compositor,
    /// so it is *not* paired with `--headless`.
    pub measure_rss_live: bool,
    /// Bar width in text cells, for `--print-layout` (there is no backend to
    /// negotiate a size) or a representative width for `--measure-rss`.
    pub width: Option<u32>,
    /// Whole seconds since the epoch to pin the wall clock at, for
    /// `--print-layout` (deterministic output for CI assertions).
    pub epoch: Option<i64>,
    /// The idle-RSS gate limit in MiB, for `--measure-rss` (defaults to the
    /// documented target, 15 MiB, when omitted).
    pub max_mib: Option<u32>,
}

/// Errors that can happen while parsing the command line.
///
/// Kept `Copy`-free and dependency-free: each variant is exactly the
/// information the caller needs to print a one-line diagnostic.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CliError {
    /// A flag was given a value but `--config` is not one.
    FlagRequiresValue { flag: String },
    /// A flag or positional argument was not recognised.
    UnknownArg { arg: String },
    /// A numeric flag value was not a (signed) integer.
    BadNumber { flag: String, value: String },
    /// `--print-layout` was given without `--headless`.
    PrintLayoutRequiresHeadless,
    /// `--measure-rss` was given without `--headless`.
    MeasureRssRequiresHeadless,
    /// More than one one-shot mode (`--print-layout`, `--measure-rss`,
    /// `--measure-rss-live`) was given at once.
    OneShotModesMutuallyExclusive,
    /// `--measure-rss-live` was given together with `--headless` (the live
    /// mode *needs* a compositor, so the two are contradictory).
    MeasureRssLiveIncompatibleWithHeadless,
    /// A headless-only flag (`--width` or `--epoch`) was given to
    /// `--measure-rss-live` (the live width comes from the compositor and the
    /// clock is live, so neither applies).
    MeasureRssLiveRejectsFlag { flag: String },
    /// A headless-only flag (`--headless`, `--width`, `--epoch`, `--max-mib`)
    /// was given without a one-shot mode (`--print-layout` or `--measure-rss`).
    HeadlessFlagWithoutPrintLayout { flag: String },
    /// `--print-layout` was given without `--width`.
    PrintLayoutRequiresWidth,
}

impl CliError {
    /// Render a one-line, stdout-friendly diagnostic (no backtraces).
    pub fn to_diagnostic(&self, usage: &str) -> String {
        let line = match self {
            CliError::FlagRequiresValue { flag } => format!("{flag} requires a value"),
            CliError::UnknownArg { arg } => format!("unknown argument `{arg}`"),
            CliError::BadNumber { flag, value } => {
                format!("{flag} must be an integer (got `{value}`)")
            }
            CliError::PrintLayoutRequiresHeadless => {
                "--print-layout requires --headless".to_string()
            }
            CliError::MeasureRssRequiresHeadless => {
                "--measure-rss requires --headless".to_string()
            }
            CliError::OneShotModesMutuallyExclusive => {
                "--print-layout, --measure-rss, and --measure-rss-live are mutually exclusive"
                    .to_string()
            }
            CliError::MeasureRssLiveIncompatibleWithHeadless => {
                "--measure-rss-live requires a compositor and cannot be used with --headless"
                    .to_string()
            }
            CliError::MeasureRssLiveRejectsFlag { flag } => {
                format!("{flag} is not supported with --measure-rss-live")
            }
            CliError::HeadlessFlagWithoutPrintLayout { flag } => {
                // `--max-mib` is also a gate for `--measure-rss-live`, so mention
                // it there; the other headless-only flags are not.
                if flag == "--max-mib" {
                    "--max-mib is only supported with --print-layout or --measure-rss (or --measure-rss-live)"
                        .to_string()
                } else {
                    format!("{flag} is only supported with --print-layout or --measure-rss")
                }
            }
            CliError::PrintLayoutRequiresWidth => {
                "--print-layout requires --width <CELLS>".to_string()
            }
        };
        format!("error: {line}\n{usage}")
    }
}

/// Usage text. Kept as a function (not a const) so `--help` and error
/// paths render from one source of truth.
pub fn usage() -> String {
    String::from(
        "usage: pengy-panel [OPTIONS]\n",
    ) + "\n"
        + "  (no options)            start the panel on the current Wayland compositor\n"
        + "  -c, --config <F>        read configuration from <F> (default: none)\n"
        + "  --headless              run without a compositor (with a one-shot mode)\n"
        + "  --print-layout          print the layout as one text line and exit\n"
        + "  --measure-rss           measure the release idle RSS and exit (CI gate)\n"
        + "  --measure-rss-live      measure idle RSS under a live compositor and exit\n"
        + "  --width <CELLS>         bar width in cells (for the one-shot modes)\n"
        + "  --epoch <SECONDS>       pin the wall clock (for --print-layout)\n"
        + "  --max-mib <MIB>         idle-RSS gate limit in MiB (for --measure-rss)\n"
        + "  -h, --help              print this help and exit\n"
        + "      --version           print the version and exit\n"
}

/// Parse raw argument strings (everything after `argv[0]`) into a
/// [`Command`].
///
/// The function is a pure `Iterator`-to-`Result` mapping so it can be
/// unit-tested headlessly with any argument list. Flag combinations are
/// validated here (not in `main`), so the whole contract is testable
/// without touching the process.
pub fn parse<I>(args: I) -> Result<Command, CliError>
where
    I: IntoIterator<Item = String>,
{
    let mut run = RunArgs {
        config: None,
        headless: false,
        print_layout: false,
        measure_rss: false,
        measure_rss_live: false,
        width: None,
        epoch: None,
        max_mib: None,
    };
    let mut iter = args.into_iter().peekable();

    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--help" | "-h" => return Ok(Command::Help),
            "--version" => return Ok(Command::Version),
            "-c" | "--config" => {
                let value = iter
                    .next()
                    .ok_or_else(|| CliError::FlagRequiresValue {
                        flag: arg.clone(),
                    })?;
                run.config = Some(PathBuf::from(value));
            }
            "--headless" => run.headless = true,
            "--print-layout" => run.print_layout = true,
            "--measure-rss" => run.measure_rss = true,
            "--measure-rss-live" => run.measure_rss_live = true,
            "--width" | "--epoch" | "--max-mib" => {
                let value = iter
                    .next()
                    .ok_or_else(|| CliError::FlagRequiresValue {
                        flag: arg.clone(),
                    })?;
                set_number(&mut run, &arg, &value)?;
            }
            other if other.starts_with('-') && other.len() > 1 => {
                match other.strip_prefix("--config=") {
                    Some(value) => run.config = Some(PathBuf::from(value)),
                    None => match other.strip_prefix("--width=") {
                        Some(value) => set_number(&mut run, "--width", value)?,
                        None => match other.strip_prefix("--epoch=") {
                            Some(value) => set_number(&mut run, "--epoch", value)?,
                            None => match other.strip_prefix("--max-mib=") {
                                Some(value) => set_number(&mut run, "--max-mib", value)?,
                                None => {
                                    return Err(CliError::UnknownArg {
                                        arg: other.to_string(),
                                    })
                                }
                            },
                        },
                    },
                }
            }
            other => {
                return Err(CliError::UnknownArg {
                    arg: other.to_string(),
                });
            }
        }
    }

    // Flag-combination rules. There are three one-shot modes: two headless
    // ones (--print-layout, --measure-rss) that pair with --headless, and one
    // live one (--measure-rss-live) that needs a compositor. They are mutually
    // exclusive, and each mode's own flags must be consistent with it.
    let has_mode = run.print_layout || run.measure_rss || run.measure_rss_live;
    let mode_count = [run.print_layout, run.measure_rss, run.measure_rss_live]
        .into_iter()
        .filter(|m| *m)
        .count();
    if mode_count > 1 {
        return Err(CliError::OneShotModesMutuallyExclusive);
    }
    if run.print_layout && !run.headless {
        return Err(CliError::PrintLayoutRequiresHeadless);
    }
    if run.measure_rss && !run.headless {
        return Err(CliError::MeasureRssRequiresHeadless);
    }
    // The live mode needs a compositor, so it cannot be paired with --headless,
    // and it takes neither --width (the width comes from the compositor) nor
    // --epoch (the clock is live).
    if run.measure_rss_live {
        if run.headless {
            return Err(CliError::MeasureRssLiveIncompatibleWithHeadless);
        }
        let rejected = if run.width.is_some() {
            Some("--width")
        } else if run.epoch.is_some() {
            Some("--epoch")
        } else {
            None
        };
        if let Some(flag) = rejected {
            return Err(CliError::MeasureRssLiveRejectsFlag {
                flag: flag.to_string(),
            });
        }
    }
    if !has_mode
        && (run.headless
            || run.width.is_some()
            || run.epoch.is_some()
            || run.max_mib.is_some())
    {
        let flag = if run.headless {
            "--headless"
        } else if run.width.is_some() {
            "--width"
        } else if run.epoch.is_some() {
            "--epoch"
        } else {
            "--max-mib"
        }
        .to_string();
        return Err(CliError::HeadlessFlagWithoutPrintLayout { flag });
    }
    if run.print_layout && run.width.is_none() {
        return Err(CliError::PrintLayoutRequiresWidth);
    }

    Ok(Command::Run(run))
}

/// Parse and store a `--width`/`--epoch` value (also in the `--flag=value`
/// form, where `flag` is the flag name without its value).
fn set_number(run: &mut RunArgs, flag: &str, value: &str) -> Result<(), CliError> {
    match flag {
        "--width" => {
            run.width = Some(
                value
                    .parse::<u32>()
                    .map_err(|_| CliError::BadNumber {
                        flag: flag.to_string(),
                        value: value.to_string(),
                    })?,
            );
        }
        "--epoch" => {
            run.epoch = Some(
                value
                    .parse::<i64>()
                    .map_err(|_| CliError::BadNumber {
                        flag: flag.to_string(),
                        value: value.to_string(),
                    })?,
            );
        }
        "--max-mib" => {
            run.max_mib = Some(
                value
                    .parse::<u32>()
                    .map_err(|_| CliError::BadNumber {
                        flag: flag.to_string(),
                        value: value.to_string(),
                    })?,
            );
        }
        _ => unreachable!("only --width, --epoch, and --max-mib reach here"),
    }
    Ok(())
}
