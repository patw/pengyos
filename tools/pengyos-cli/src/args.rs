//! clap-style argument parsing, standard-library only.
//!
//! This module is the framework's front door: it turns a raw `argv` slice into a
//! structured [`ParsedArgs`] according to a declarative [`ArgSpec`]. It is
//! intentionally small and predictable so it can be unit-tested without spawning
//! any process or touching the network.
//!
//! Supported syntax (a useful, predictable subset of clap):
//!
//! * `--help` / `-h` short-circuits parsing (clap prints help and stops).
//! * `--` ends option parsing; the remainder are positionals.
//! * Long flags: `--verbose` / `-v`.
//! * Long options: `--output FILE`, `--output=FILE`.
//! * Short flags: `-v` (and bundled, e.g. `-vq` == `-v -q`).
//! * Short options: `-o FILE`, `-oFILE`.
//! * Positionals fill declared slots; an over-fill is an error.

use std::collections::BTreeMap;
use std::fmt;

/// A declared flag: a boolean switch such as `--verbose` / `-v`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Flag {
    pub long: &'static str,
    pub short: Option<char>,
    pub help: &'static str,
}

/// A declared option that takes a value, such as `--output FILE`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OptionDef {
    pub long: &'static str,
    pub short: Option<char>,
    pub help: &'static str,
}

/// A declared positional argument.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PositionalDef {
    pub name: &'static str,
    /// When `true` the slot accepts zero or more values (variadic).
    pub variadic: bool,
    pub help: &'static str,
}

/// The declarative argument surface of a single subcommand.
///
/// Built with the [`ArgSpec::builder`] chain; [`ArgSpec::parse`] validates an
/// `argv` against it.
#[derive(Debug, Clone, Default)]
pub struct ArgSpec {
    flags: Vec<Flag>,
    options: Vec<OptionDef>,
    positionals: Vec<PositionalDef>,
    min_positionals: usize,
    max_positionals: Option<usize>,
}

impl ArgSpec {
    /// Start building a spec.
    pub fn builder() -> ArgSpecBuilder {
        ArgSpecBuilder::default()
    }

    /// Parse `argv` against this spec, returning a [`ParsedArgs`] or a
    /// [`ParseError`].
    pub fn parse(&self, argv: &[String]) -> Result<ParsedArgs, ParseError> {
        let mut parsed = ParsedArgs::default();
        let mut i = 0;

        // Fast lookups by long / short name.
        let long_flag: BTreeMap<&str, &Flag> =
            self.flags.iter().map(|f| (f.long, f)).collect();
        let short_flag: BTreeMap<char, &Flag> =
            self.flags.iter().filter_map(|f| f.short.map(|s| (s, f))).collect();
        let long_opt: BTreeMap<&str, &OptionDef> =
            self.options.iter().map(|o| (o.long, o)).collect();
        let short_opt: BTreeMap<char, &OptionDef> =
            self.options.iter().filter_map(|o| o.short.map(|s| (s, o))).collect();

        while i < argv.len() {
            let tok = &argv[i];

            // `--help` / `-h` short-circuits, exactly like clap.
            if tok == "--help" || tok == "-h" {
                parsed.help_requested = true;
                break;
            }

            // `--` terminates option parsing; everything after is positional.
            if tok == "--" {
                parsed.positionals.extend(argv[i + 1..].iter().cloned());
                break;
            }

            if tok.starts_with("--") {
                // Long option / flag.
                let body = &tok[2..];
                match body.split_once('=') {
                    Some((key, value)) => {
                        if let Some(flag) = long_flag.get(key) {
                            return Err(ParseError::FlagTakesValue(flag.long));
                        }
                        if let Some(opt) = long_opt.get(key) {
                            parsed.options.insert(opt.long.to_string(), value.to_string());
                        } else {
                            return Err(ParseError::UnknownOption(format!("--{key}")));
                        }
                    }
                    None => {
                        if let Some(flag) = long_flag.get(body) {
                            parsed.flags.insert(flag.long.to_string(), true);
                        } else if let Some(opt) = long_opt.get(body) {
                            if i + 1 < argv.len() {
                                i += 1;
                                parsed
                                    .options
                                    .insert(opt.long.to_string(), argv[i].clone());
                            } else {
                                return Err(ParseError::MissingValue(opt.long));
                            }
                        } else {
                            return Err(ParseError::UnknownOption(format!("--{body}")));
                        }
                    }
                }
            } else if tok.len() > 1 && tok.starts_with('-') {
                // Short option / flag, possibly bundled: `-vq`, `-oFILE`.
                // Walk the characters with an index so a value option can
                // consume the rest of the token and bundling can continue.
                let inner: Vec<char> = tok.chars().skip(1).collect();
                let mut idx = 0;
                loop {
                    let c = inner[idx];
                    if let Some(opt) = short_opt.get(&c) {
                        // A value option consumes the rest of the token, or the
                        // next token when the value is not attached.
                        if idx + 1 < inner.len() {
                            let value: String = inner[idx + 1..].iter().collect();
                            parsed.options.insert(opt.long.to_string(), value);
                        } else if i + 1 < argv.len() {
                            i += 1;
                            parsed.options.insert(opt.long.to_string(), argv[i].clone());
                        } else {
                            return Err(ParseError::MissingValue(opt.long));
                        }
                        break; // a value option is terminal for this token
                    } else if let Some(flag) = short_flag.get(&c) {
                        parsed.flags.insert(flag.long.to_string(), true);
                        idx += 1;
                        if idx >= inner.len() {
                            break;
                        }
                        // otherwise keep bundling the next short flag
                    } else {
                        return Err(ParseError::UnknownOption(format!("-{c}")));
                    }
                }
            } else {
                // Positional. A variadic slot absorbs everything; otherwise
                // values fill declared slots in order and over-fill errors.
                if self.positionals.iter().any(|d| d.variadic) {
                    parsed.positionals.push(tok.clone());
                } else {
                    let idx = parsed.positionals.len();
                    if self.positionals.get(idx).is_some() {
                        parsed.positionals.push(tok.clone());
                    } else if let Some(max) = self.max_positionals {
                        if idx >= max {
                            return Err(ParseError::TooManyPositionals);
                        }
                        parsed.positionals.push(tok.clone());
                    } else {
                        return Err(ParseError::TooManyPositionals);
                    }
                }
            }

            i += 1;
        }

        let got = parsed.positionals.len();
        // Help short-circuits: clap prints help and ignores arity, so a
        // `--help` on a command with required args must not error.
        if parsed.help_requested {
            return Ok(parsed);
        }

        if got < self.min_positionals {
            return Err(ParseError::MissingPositional(self.min_positionals, got));
        }
        if let Some(max) = self.max_positionals {
            if got > max {
                return Err(ParseError::TooManyPositionals);
            }
        }

        Ok(parsed)
    }

    /// Render the declared args/flags/options as indented help lines.
    pub fn format_items(&self) -> String {
        let mut lines: Vec<String> = Vec::new();
        for def in &self.positionals {
            let kind = if def.variadic { "ARGS" } else { "ARG" };
            lines.push(format!("    {:<8} {}", kind, def.help));
        }
        for opt in &self.options {
            let prefix = match opt.short {
                Some(s) => format!("    -{s}, "),
                None => "        ".to_string(),
            };
            lines.push(format!("{prefix}--{}  {}", opt.long, opt.help));
        }
        for flag in &self.flags {
            let prefix = match flag.short {
                Some(s) => format!("    -{s}, "),
                None => "        ".to_string(),
            };
            lines.push(format!("{prefix}--{}  {}", flag.long, flag.help));
        }
        lines.join("\n")
    }
}

/// Builder for an [`ArgSpec`].
#[derive(Default)]
pub struct ArgSpecBuilder {
    spec: ArgSpec,
}

impl ArgSpecBuilder {
    /// Add a boolean flag (`--long` / `-s`).
    pub fn flag(mut self, long: &'static str, short: Option<char>, help: &'static str) -> Self {
        self.spec.flags.push(Flag { long, short, help });
        self
    }

    /// Add an option that takes a value (`--long VALUE` / `-s VALUE`).
    pub fn option(mut self, long: &'static str, short: Option<char>, help: &'static str) -> Self {
        self.spec.options.push(OptionDef { long, short, help });
        self
    }

    /// Add a single (non-variadic) positional slot.
    pub fn positional(mut self, name: &'static str, help: &'static str) -> Self {
        self.spec.positionals.push(PositionalDef {
            name,
            variadic: false,
            help,
        });
        self
    }

    /// Add a variadic positional slot (accepts zero or more values).
    pub fn positionals(mut self, name: &'static str, help: &'static str) -> Self {
        self.spec.positionals.push(PositionalDef {
            name,
            variadic: true,
            help,
        });
        self
    }

    /// Require at least `n` positionals.
    pub fn min_positionals(mut self, n: usize) -> Self {
        self.spec.min_positionals = n;
        self
    }

    /// Allow at most `n` positionals.
    pub fn max_positionals(mut self, n: usize) -> Self {
        self.spec.max_positionals = Some(n);
        self
    }

    /// Finish building.
    ///
    /// A non-variadic positional is implicitly *required*, so the minimum
    /// positional count is the number of non-variadic slots (raised, never
    /// lowered, by an explicit [`ArgSpecBuilder::min_positionals`] call).
    pub fn build(self) -> ArgSpec {
        let spec = self.spec;
        let required = spec.positionals.iter().filter(|d| !d.variadic).count();
        ArgSpec {
            min_positionals: required.max(spec.min_positionals),
            ..spec
        }
    }
}

/// The result of parsing an `argv` against an [`ArgSpec`].
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct ParsedArgs {
    /// Long flag names that were set (all values are `true`).
    pub flags: BTreeMap<String, bool>,
    /// Long option names mapped to their values.
    pub options: BTreeMap<String, String>,
    /// Positional values, in order.
    pub positionals: Vec<String>,
    /// Whether `--help` / `-h` was requested.
    pub help_requested: bool,
}

impl ParsedArgs {
    /// The first positional value, if any.
    pub fn first_positional(&self) -> Option<&str> {
        self.positionals.first().map(String::as_str)
    }

    /// Convenience: is this flag set?
    pub fn flag(&self, name: &str) -> bool {
        self.flags.get(name).is_some()
    }

    /// Convenience: get an option value by long name.
    pub fn option(&self, name: &str) -> Option<&str> {
        self.options.get(name).map(String::as_str)
    }
}

/// An error produced while parsing `argv`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// A long flag was given a value (`--flag=value`).
    FlagTakesValue(&'static str),
    /// An option was given no value.
    MissingValue(&'static str),
    /// An unknown option was supplied.
    UnknownOption(String),
    /// Too many positionals were supplied.
    TooManyPositionals,
    /// Fewer positionals than required.
    MissingPositional(usize, usize),
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParseError::FlagTakesValue(name) => {
                write!(f, "flag `--{name}` does not take a value")
            }
            ParseError::MissingValue(name) => write!(f, "option `--{name}` requires a value"),
            ParseError::UnknownOption(name) => write!(f, "unknown option `{name}`"),
            ParseError::TooManyPositionals => {
                write!(f, "too many positional arguments")
            }
            ParseError::MissingPositional(required, found) => {
                write!(f, "missing {required} positional argument(s) (found {found})")
            }
        }
    }
}

impl std::error::Error for ParseError {}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> ArgSpec {
        // A variadic positional keeps this helper's arity at zero so the
        // flag/option tests focus on those, not on positional requirements.
        ArgSpec::builder()
            .positionals("target", "the target triple")
            .flag("verbose", Some('v'), "verbose output")
            .flag("quiet", Some('q'), "quiet output")
            .option("output", Some('o'), "output file")
            .build()
    }

    #[test]
    fn parses_positionals() {
        let argv = vec!["x86_64".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert_eq!(p.positionals, vec!["x86_64".to_string()]);
        assert!(!p.help_requested);
    }

    #[test]
    fn parses_long_flag() {
        let argv = vec!["x86_64".to_string(), "--verbose".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert!(p.flag("verbose"));
        assert_eq!(p.positionals, vec!["x86_64".to_string()]);
    }

    #[test]
    fn parses_short_flag() {
        let argv = vec!["--verbose".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert!(p.flag("verbose"));
    }

    #[test]
    fn bundles_short_flags() {
        let argv = vec!["-vq".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert!(p.flag("verbose"));
        assert!(p.flag("quiet"));
    }

    #[test]
    fn parses_long_option_with_equals() {
        let argv = vec!["--output=file.txt".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert_eq!(p.option("output"), Some("file.txt"));
    }

    #[test]
    fn parses_long_option_with_space() {
        let argv = vec!["--output".to_string(), "file.txt".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert_eq!(p.option("output"), Some("file.txt"));
    }

    #[test]
    fn parses_short_option_with_attached_value() {
        let argv = vec!["-ofile.txt".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert_eq!(p.option("output"), Some("file.txt"));
    }

    #[test]
    fn parses_short_option_with_space() {
        let argv = vec!["-o".to_string(), "file.txt".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert_eq!(p.option("output"), Some("file.txt"));
    }

    #[test]
    fn help_short_circuits() {
        let argv = vec!["x86_64".to_string(), "--help".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert!(p.help_requested);
    }

    #[test]
    fn double_dash_rest_is_positional() {
        let argv = vec!["--".to_string(), "--weird".to_string(), "-x".to_string()];
        let p = spec().parse(&argv).unwrap();
        assert_eq!(
            p.positionals,
            vec!["--weird".to_string(), "-x".to_string()]
        );
    }

    #[test]
    fn unknown_long_option_errors() {
        let argv = vec!["--bogus".to_string()];
        assert_eq!(
            spec().parse(&argv),
            Err(ParseError::UnknownOption("--bogus".to_string()))
        );
    }

    #[test]
    fn unknown_short_option_errors() {
        let argv = vec!["-z".to_string()];
        assert!(matches!(
            spec().parse(&argv),
            Err(ParseError::UnknownOption(_))
        ));
    }

    #[test]
    fn flag_with_value_errors() {
        let argv = vec!["--verbose=true".to_string()];
        assert_eq!(
            spec().parse(&argv),
            Err(ParseError::FlagTakesValue("verbose"))
        );
    }

    #[test]
    fn missing_value_errors() {
        let argv = vec!["--output".to_string()];
        assert_eq!(
            spec().parse(&argv),
            Err(ParseError::MissingValue("output"))
        );
    }

    #[test]
    fn missing_positional_errors() {
        // This spec requires exactly one positional.
        let s = ArgSpec::builder()
            .positional("target", "the target")
            .build();
        assert_eq!(
            s.parse(&[]),
            Err(ParseError::MissingPositional(1, 0))
        );
    }

    #[test]
    fn too_many_positionals_errors() {
        let s = ArgSpec::builder()
            .max_positionals(1)
            .positional("target", "the target")
            .build();
        let argv = vec!["a".to_string(), "b".to_string()];
        assert_eq!(s.parse(&argv), Err(ParseError::TooManyPositionals));
    }

    #[test]
    fn variadic_positionals_accepted() {
        let s = ArgSpec::builder().positionals("files", "files").build();
        let argv = vec!["a".to_string(), "b".to_string(), "c".to_string()];
        let p = s.parse(&argv).unwrap();
        assert_eq!(p.positionals, vec!["a", "b", "c"]);
    }

    #[test]
    fn positional_and_options_interleave() {
        let argv = vec![
            "x86_64".to_string(),
            "--verbose".to_string(),
            "-o".to_string(),
            "out".to_string(),
        ];
        let p = spec().parse(&argv).unwrap();
        assert_eq!(p.positionals, vec!["x86_64".to_string()]);
        assert!(p.flag("verbose"));
        assert_eq!(p.option("output"), Some("out"));
    }
}
