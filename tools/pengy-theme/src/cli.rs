//! Argument parsing and command dispatcher for `pengy-theme`.
//!
//! The dispatcher is thin and dependency-free (standard library only): it maps
//! argv onto the documented subcommands (`validate`, `pack`, `contrast`) and
//! returns an exit code per the [`crate::exit`] contract. Subcommands are
//! implemented by follow-up subtasks; until then they report
//! [`crate::exit::UNIMPLEMENTED`].

use crate::{banner, exit, USAGE};

/// Run the CLI with the given arguments (excluding the program name).
///
/// Returns the process exit code (see [`crate::exit`]).
pub fn run(args: &[String]) -> i32 {
    // No arguments: print usage to stdout and succeed (a friendly default).
    if args.is_empty() {
        print!("{USAGE}");
        return exit::OK;
    }

    match args[0].as_str() {
        "-h" | "--help" | "help" => {
            print!("{USAGE}");
            exit::OK
        }
        "-V" | "--version" => {
            println!("{}", banner());
            exit::OK
        }
        "validate" => cmd_validate(&args[1..]),
        "pack" => cmd_pack(&args[1..]),
        "contrast" => cmd_contrast(&args[1..]),
        other => {
            eprintln!(
                "pengy-theme: unknown command `{other}`\n\n{USAGE}",
            );
            exit::USAGE
        }
    }
}

/// `validate <manifest>` — validate a theme manifest.
///
/// Loads the manifest, validates it ([`crate::validate`]), prints the report,
/// and returns `exit::OK` when valid or `exit::ERROR` when it has hard errors.
fn cmd_validate(args: &[String]) -> i32 {
    if args.iter().any(|a| a == "-h" || a == "--help") {
        print_validate_help();
        return exit::OK;
    }

    // Positional: the manifest path. Anything else is a usage error.
    let positional: Vec<&String> =
        args.iter().filter(|a| !a.starts_with('-')).collect();
    let manifest: &String = match positional.as_slice() {
        [manifest] => manifest,
        // No positional argument.
        [] => {
            eprintln!("pengy-theme validate: missing <manifest> argument\n");
            print_validate_help();
            return exit::USAGE;
        }
        // Extra positional args are a usage error.
        _ => {
            eprintln!(
                "pengy-theme validate: too many arguments; expected exactly one <manifest>\n"
            );
            print_validate_help();
            return exit::USAGE;
        }
    };

    // Load the manifest (read + parse errors are runtime errors, exit 1).
    let theme = match crate::manifest::load_manifest(manifest) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("pengy-theme validate: {e}");
            return exit::ERROR;
        }
    };

    // Validate and render the report.
    let report = theme.validate();
    println!("{}", report.render());
    if report.valid {
        exit::OK
    } else {
        exit::ERROR
    }
}

fn print_validate_help() {
    println!("pengy-theme validate — validate a theme manifest");
    println!();
    println!("USAGE:");
    println!("    pengy-theme validate <manifest.toml>");
    println!();
    println!("Validates the required tokens, well-formed colors, and WCAG AA contrast");
    println!("ratios for the key foreground/background pairs.");
    println!();
    println!("EXIT:");
    println!("    0  valid");
    println!("    1  invalid (or the manifest could not be read/parsed)");
    println!("    2  usage error");
}

fn print_pack_help() {
    println!("pengy-theme pack — produce a deterministic archive + manifest hash");
    println!();
    println!("USAGE:");
    println!("    pengy-theme pack <manifest.toml> --out <dir>");
    println!();
    println!("Packs the manifest and its referenced assets into a deterministic");
    println!("`<name>.tar` archive and writes a `manifest.sha256` sidecar into <dir>.");
    println!("Two packs over the same inputs are byte-identical.");
    println!();
    println!("EXIT:");
    println!("    0  packed");
    println!("    1  runtime error (manifest could not be read/parsed, an asset is");
    println!("       missing, or the archive could not be written)");
    println!("    2  usage error");
}

/// `pack <manifest> --out <dir>` — produce a deterministic archive + manifest
/// hash from a theme manifest.
///
/// Loads the manifest, packs it (plus its referenced assets) into a
/// `<name>.tar` archive and writes a `manifest.sha256` sidecar into `--out`.
///
/// EXIT:
///     0  packed
///     1  runtime error (manifest could not be read/parsed, an asset is
///        missing, or the archive could not be written)
///     2  usage error (missing `--out`, too many arguments)
fn cmd_pack(args: &[String]) -> i32 {
    if args.iter().any(|a| a == "-h" || a == "--help") {
        print_pack_help();
        return exit::OK;
    }

    // Parse: one positional (the manifest) and `--out <dir>`.
    let mut positional: Vec<&String> = Vec::new();
    let mut out_dir: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        let a = &args[i];
        match a.as_str() {
            "--out" => {
                match args.get(i + 1) {
                    Some(dir) => {
                        out_dir = Some(dir.clone());
                        i += 2;
                    }
                    None => {
                        eprintln!("pengy-theme pack: `--out` requires a directory argument\n");
                        print_pack_help();
                        return exit::USAGE;
                    }
                }
            }
            other if other.starts_with("--out=") => {
                out_dir = Some(other["--out=".len()..].to_string());
                i += 1;
            }
            other if other.starts_with('-') && other != "-" => {
                eprintln!("pengy-theme pack: unknown option `{other}`\n");
                print_pack_help();
                return exit::USAGE;
            }
            _ => {
                // A positional argument. This arm MUST advance `i`: without it
                // the loop spins forever pushing into `positional`, and the
                // process grows until the kernel OOM-kills it (2026-09-23: a
                // plain `pack <man> --out <dir>` smoke test allocated 33 GB in
                // ~10 min and killed the build host).
                positional.push(a);
                i += 1;
            }
        }
    }

    let manifest: &String = match positional.as_slice() {
        [manifest] => manifest,
        [] => {
            eprintln!("pengy-theme pack: missing <manifest> argument\n");
            print_pack_help();
            return exit::USAGE;
        }
        _ => {
            eprintln!("pengy-theme pack: too many arguments; expected exactly one <manifest>\n");
            print_pack_help();
            return exit::USAGE;
        }
    };

    let out_dir: String = match out_dir {
        Some(dir) => dir,
        None => {
            eprintln!("pengy-theme pack: missing required `--out <dir>` argument\n");
            print_pack_help();
            return exit::USAGE;
        }
    };

    // Load the manifest (read + parse errors are runtime errors, exit 1).
    let theme = match crate::manifest::load_manifest(manifest) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("pengy-theme pack: {e}");
            return exit::ERROR;
        }
    };

    // Pack into the output directory.
    match crate::pack::pack(
        std::path::Path::new(manifest),
        &theme,
        std::path::Path::new(&out_dir),
    ) {
        Ok(result) => {
            println!(
                "packed {} -> {}",
                result.archive_path.display(),
                result.manifest_file_hash
            );
            // The manifest hash is the manifest file's own per-file digest: the
            // first line of the `manifest.sha256` sidecar, verifiable with
            // `sha256sum -c`.
            println!("manifest hash: {}", result.manifest_file_hash);
            println!("wrote: {}", result.manifest_hash_path.display());
            exit::OK
        }
        Err(e) => {
            eprintln!("pengy-theme pack: {e}");
            exit::ERROR
        }
    }
}

/// `contrast <fg> <bg>` — compute the WCAG relative contrast ratio of two colors.
///
/// Parses the two `#rrggbb` color arguments, computes the WCAG 2.x relative
/// contrast ratio ([`crate::contrast::contrast_ratio`]), and prints it along with
/// the AA pass/fail verdict for the normal-text (4.5:1) and large-text (3.0:1)
/// thresholds.
///
/// EXIT:
///     0  success (the ratio was computed and printed)
///     1  runtime error (an argument was not a valid `#rrggbb` color)
///     2  usage error (not exactly two positional arguments)
fn cmd_contrast(args: &[String]) -> i32 {
    if args.iter().any(|a| a == "-h" || a == "--help") {
        print_contrast_help();
        return exit::OK;
    }

    // Positional: exactly two colors, <fg> and <bg>.
    let positional: Vec<&String> =
        args.iter().filter(|a| !a.starts_with('-')).collect();
    let (fg_arg, bg_arg): (&String, &String) = match positional.as_slice() {
        [fg, bg] => (fg, bg),
        [] => {
            eprintln!("pengy-theme contrast: missing <fg> and <bg> arguments\n");
            print_contrast_help();
            return exit::USAGE;
        }
        _ => {
            eprintln!(
                "pengy-theme contrast: expected exactly two colors <fg> <bg>, got {}\n",
                positional.len()
            );
            print_contrast_help();
            return exit::USAGE;
        }
    };

    // Parse each argument as a #rrggbb color. A malformed color is a runtime
    // error (exit 1), not a usage error: the argument is well-formed syntactically
    // but not a valid color.
    let fg = match crate::manifest::Color::from_hex(fg_arg) {
        Ok(c) => c,
        Err(_) => {
            eprintln!(
                "pengy-theme contrast: invalid foreground color `{fg_arg}` (expected #rrggbb)\n"
            );
            return exit::ERROR;
        }
    };
    let bg = match crate::manifest::Color::from_hex(bg_arg) {
        Ok(c) => c,
        Err(_) => {
            eprintln!(
                "pengy-theme contrast: invalid background color `{bg_arg}` (expected #rrggbb)\n"
            );
            return exit::ERROR;
        }
    };

    // Compute the WCAG relative contrast ratio and render the verdict.
    let ratio = crate::contrast::contrast_ratio(fg, bg);
    println!("{}", render_contrast(fg, bg, ratio));
    exit::OK
}

/// Render a single `contrast` result line.
///
/// Format:
///
/// ```text
/// #1a1a1a on #f5f5f5: contrast 16.10:1 — AA normal (4.5): pass; AA large (3.0): pass
/// ```
///
/// The colors are echoed in canonical lowercase `#rrggbb` form (so `1A2B3C` and
/// `#1a2b3c` both render identically), the ratio is fixed to two decimals, and
/// each AA threshold (shown to one decimal, in parentheses) gets an independent
/// `pass`/`fail` verdict.
fn render_contrast(fg: crate::manifest::Color, bg: crate::manifest::Color, ratio: f64) -> String {
    let aa_normal = ratio >= crate::contrast::WCAG_AA_NORMAL;
    let aa_large = ratio >= crate::contrast::WCAG_AA_LARGE;
    format!(
        "{fg} on {bg}: contrast {ratio:.2}:1 \u{2014} AA normal ({:.1}): {}; AA large ({:.1}): {}",
        crate::contrast::WCAG_AA_NORMAL,
        verdict(aa_normal),
        crate::contrast::WCAG_AA_LARGE,
        verdict(aa_large),
    )
}
/// The lowercase verdict label for a boolean AA check.
fn verdict(pass: bool) -> &'static str {
    if pass {
        "pass"
    } else {
        "fail"
    }
}

fn print_contrast_help() {
    println!("pengy-theme contrast — compute the WCAG contrast ratio of two colors");
    println!();
    println!("USAGE:");
    println!("    pengy-theme contrast <fg> <bg>");
    println!();
    println!("Computes the WCAG 2.x relative contrast ratio of two #rrggbb colors and");
    println!("reports whether each clears the AA thresholds (4.5:1 normal text, 3.0:1");
    println!("large text / graphics). The ratio is always >= 1 (the lighter color is");
    println!("placed in the numerator).");
    println!();
    println!("EXIT:");
    println!("    0  the ratio was computed and printed");
    println!("    1  an argument was not a valid #rrggbb color");
    println!("    2  usage error (not exactly two color arguments)");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(s: &str) -> String {
        s.to_string()
    }

    #[test]
    fn no_args_prints_usage_and_succeeds() {
        assert_eq!(run(&[]), exit::OK);
    }

    #[test]
    fn help_succeeds() {
        assert_eq!(run(&[v("--help")]), exit::OK);
        assert_eq!(run(&[v("-h")]), exit::OK);
        assert_eq!(run(&[v("help")]), exit::OK);
    }

    #[test]
    fn version_succeeds() {
        assert_eq!(run(&[v("--version")]), exit::OK);
        assert_eq!(run(&[v("-V")]), exit::OK);
    }

    #[test]
    fn validate_is_implemented() {
        // `validate` now loads + validates a manifest. With no manifest it is a
        // usage error; with a missing manifest file it is a runtime error (1).
        assert_eq!(run(&[v("validate")]), exit::USAGE);
        assert_eq!(
            run(&[v("validate"), v("/nonexistent/pengy-theme-nope.toml")]),
            exit::ERROR,
            "missing manifest is a runtime error"
        );
    }

    #[test]
    fn contrast_help_succeeds() {
        assert_eq!(run(&[v("contrast"), v("--help")]), exit::OK);
        assert_eq!(run(&[v("contrast"), v("-h")]), exit::OK);
    }

    #[test]
    fn contrast_valid_pair_succeeds() {
        // White on black is the canonical passing pair (21:1); the subcommand
        // parses both colors and exits 0.
        assert_eq!(
            run(&[v("contrast"), v("#ffffff"), v("#000000")]),
            exit::OK
        );
    }

    #[test]
    fn contrast_invalid_fg_is_runtime_error() {
        // A malformed color is a runtime error (exit 1), not a usage error.
        assert_eq!(
            run(&[v("contrast"), v("not-a-color"), v("#000000")]),
            exit::ERROR
        );
    }

    #[test]
    fn contrast_invalid_bg_is_runtime_error() {
        assert_eq!(
            run(&[v("contrast"), v("#ffffff"), v("nope")]),
            exit::ERROR
        );
    }

    #[test]
    fn contrast_missing_args_is_usage_error() {
        // Zero positional arguments is a usage error (exit 2).
        assert_eq!(run(&[v("contrast")]), exit::USAGE);
        // One positional argument is also a usage error (needs exactly two).
        assert_eq!(run(&[v("contrast"), v("#000000")]), exit::USAGE);
    }

    #[test]
    fn contrast_too_many_args_is_usage_error() {
        // Three positional arguments is a usage error.
        assert_eq!(
            run(&[v("contrast"), v("#000000"), v("#ffffff"), v("#ff0000")]),
            exit::USAGE
        );
    }

    #[test]
    fn render_contrast_formats_ratio_and_verdicts() {
        // The rendered line must name both canonical colors, the ratio to two
        // decimals with a `:1` suffix, and a pass/fail verdict for each AA
        // threshold.
        use crate::manifest::Color;
        use std::str::FromStr;
        let fg = Color::from_str("#1a1a1a").unwrap();
        let bg = Color::from_str("#f5f5f5").unwrap();
        let ratio = crate::contrast::contrast_ratio(fg, bg);
        let line = render_contrast(fg, bg, ratio);
        assert!(line.starts_with("#1a1a1a on #f5f5f5: "), "got: {line}");
        assert!(line.contains("contrast "), "got: {line}");
        assert!(line.contains(":1 "), "ratio must carry a :1 suffix: {line}");
        assert!(line.contains("AA normal (4.5)"), "got: {line}");
        assert!(line.contains("AA large (3.0)"), "got: {line}");
        // Dark-on-light clears both AA thresholds.
        assert!(line.contains("pass"), "should report pass: {line}");
    }

    #[test]
    fn render_contrast_labels_low_contrast_as_fail() {
        // Two near-identical greys miss both AA thresholds and must be "fail".
        use crate::manifest::Color;
        use std::str::FromStr;
        let fg = Color::from_str("#808080").unwrap();
        let bg = Color::from_str("#8a8a8a").unwrap();
        let ratio = crate::contrast::contrast_ratio(fg, bg);
        let line = render_contrast(fg, bg, ratio);
        assert!(line.contains("fail"), "low-contrast should fail: {line}");
    }

    #[test]
    fn render_contrast_normalizes_case() {
        // The canonical lowercase hex must appear regardless of input case.
        use crate::manifest::Color;
        use std::str::FromStr;
        let fg = Color::from_str("#1A2B3C").unwrap();
        let bg = Color::from_str("010203").unwrap(); // no '#', uppercase
        let ratio = crate::contrast::contrast_ratio(fg, bg);
        let line = render_contrast(fg, bg, ratio);
        assert!(line.starts_with("#1a2b3c on #010203: "), "got: {line}");
    }

    #[test]
    fn pack_help_succeeds() {
        // `pack --help` exits 0. (The help *text* is asserted end-to-end in
        // tests/cli.rs, which spawns the binary and captures stdout; this unit
        // test only owns the exit code, since `run` here returns `i32`.)
        assert_eq!(run(&[v("pack"), v("--help")]), exit::OK);
    }

    #[test]
    fn unknown_command_is_usage_error() {
        assert_eq!(run(&[v("frobnicate")]), exit::USAGE);
    }

    #[test]
    fn pack_argument_parse_terminates() {
        // Regression (2026-09-23): the positional arm of `cmd_pack`'s parser
        // must advance the index. Without `i += 1` a bare positional made the
        // parser spin forever pushing into `positional` — unbounded growth that
        // reached 33 GB and got the build host OOM-killed. These cases
        // must TERMINATE and reach the loader/usage checks, not hang.
        assert_eq!(
            run(&[v("pack"), v("/nonexistent/pengy-theme-nope.toml"), v("--out"), v("/tmp")]),
            exit::ERROR,
            "positional before --out must parse through to the loader"
        );
        assert_eq!(
            run(&[v("pack"), v("--out=/tmp"), v("/nonexistent/pengy-theme-nope.toml")]),
            exit::ERROR,
            "positional after --out=DIR must parse through to the loader"
        );
        assert_eq!(
            run(&[v("pack"), v("/nonexistent/pengy-theme-nope.toml")]),
            exit::USAGE,
            "a missing --out is a usage error, reached (not hung) by the parser"
        );
    }
}
