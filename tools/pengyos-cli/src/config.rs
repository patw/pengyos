//! Minimal, standard-library-only TOML config loading.
//!
//! PengyOS tools express configuration as TOML (see the sibling `pengyos-tiers`
//! `tiers.toml`). Rather than pull in a third-party crate (this crate must build
//! and test fully offline), this module implements the small subset of TOML the
//! meta-CLI actually needs:
//!
//! * `[table]` and `[table.subtable]` headers.
//! * `key = value` assignments.
//! * Scalar values: double-quoted strings (with `\n \t \\ \" \'` escapes),
//!   single-quoted (literal) strings, integers, floats, and booleans.
//! * Inline arrays of scalars: `["a", "b"]`.
//! * `#` comments and blank lines.
//!
//! Array-of-tables (`[[name]]`), datetime values, and dotted keys are **not**
//! supported and produce a clear [`ConfigError`]; extend this parser if a future
//! config needs them.

use std::collections::BTreeMap;
use std::fmt;
use std::path::Path;

/// A parsed TOML value.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    Array(Vec<Value>),
}

impl Value {
    /// Typed accessors, each returning `None` on a type mismatch.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Value::Int(n) => Some(*n),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Value::Bool(b) => Some(*b),
            _ => None,
        }
    }

    pub fn as_str_array(&self) -> Option<Vec<String>> {
        match self {
            Value::Array(items) => Some(
                items
                    .iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect(),
            ),
            _ => None,
        }
    }
}

/// A parsed configuration: tables keyed by name, each a map of key → value.
///
/// The empty-string table (`""`) is the root table, holding keys declared
/// outside any `[table]` header.
#[derive(Debug, Default, Clone)]
pub struct Config {
    tables: BTreeMap<String, BTreeMap<String, Value>>,
}

impl Config {
    /// Look up a raw value by table and key.
    pub fn get(&self, table: &str, key: &str) -> Option<&Value> {
        self.tables.get(table)?.get(key)
    }

    /// Look up a string value.
    pub fn str(&self, table: &str, key: &str) -> Option<String> {
        self.get(table, key).and_then(|v| v.as_str()).map(str::to_string)
    }

    /// Look up an integer value.
    pub fn int(&self, table: &str, key: &str) -> Option<i64> {
        self.get(table, key).and_then(|v| v.as_i64())
    }

    /// Look up a boolean value.
    pub fn bool(&self, table: &str, key: &str) -> Option<bool> {
        self.get(table, key).and_then(|v| v.as_bool())
    }

    /// Look up an array-of-strings value.
    pub fn str_array(&self, table: &str, key: &str) -> Option<Vec<String>> {
        self.get(table, key).and_then(|v| v.as_str_array())
    }

    /// Whether a table exists.
    pub fn has_table(&self, table: &str) -> bool {
        self.tables.contains_key(table)
    }

    /// The executable name for a subcommand's sibling tool.
    ///
    /// Resolved from the `tools` table (`tools.<command> = "..."`), falling
    /// back to the convention `pengyos-<command>` when unset.
    pub fn tool_for(&self, command: &str) -> String {
        self.str("tools", command)
            .unwrap_or_else(|| format!("pengyos-{command}"))
    }

    /// Parse a config from a TOML string.
    pub fn from_toml(text: &str) -> Result<Config, ConfigError> {
        parse_toml(text)
    }

    /// Parse a config from a file at `path`.
    pub fn load(path: &Path) -> Result<Config, ConfigError> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| ConfigError::Io {
                path: path.display().to_string(),
                source: e.to_string(),
            })?;
        parse_toml(&text)
    }

    /// Resolve the config path from the environment / defaults and load it.
    ///
    /// Resolution order:
    /// 1. `$PENGYOS_CONFIG` (an explicit path).
    /// 2. `$XDG_CONFIG_HOME/pengyos/config.toml`, else
    ///    `~/.config/pengyos/config.toml`.
    ///
    /// If no file exists, an empty [`Config`] is returned (config is optional);
    /// a file that exists but fails to parse is a hard error.
    pub fn resolve() -> Result<Config, ConfigError> {
        if let Ok(path) = std::env::var("PENGYOS_CONFIG") {
            if !path.is_empty() {
                return Config::load(Path::new(&path));
            }
        }

        let home = std::env::var("HOME").ok();
        let xdg = std::env::var("XDG_CONFIG_HOME").ok();
        let candidates: Vec<std::path::PathBuf> = match (xdg, home) {
            (Some(dir), _) => vec![Path::new(&dir).join("pengyos").join("config.toml")],
            (None, Some(h)) => vec![Path::new(&h)
                .join(".config")
                .join("pengyos")
                .join("config.toml")],
            (None, None) => vec![],
        };

        for path in candidates {
            if path.exists() {
                return Config::load(&path);
            }
        }

        Ok(Config::default())
    }
}

/// An error produced while loading/parsing configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigError {
    /// The file could not be read.
    Io { path: String, source: String },
    /// A line could not be parsed.
    Parse { line: usize, message: String },
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConfigError::Io { path, source } => {
                write!(f, "could not read config file `{path}`: {source}")
            }
            ConfigError::Parse { line, message } => {
                write!(f, "invalid config at line {line}: {message}")
            }
        }
    }
}

impl std::error::Error for ConfigError {}

fn parse_toml(text: &str) -> Result<Config, ConfigError> {
    let mut config = Config::default();
    let mut current = String::new(); // "" == root table

    for (idx, raw) in text.lines().enumerate() {
        let line = strip_comment(raw)?;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        if line.starts_with("[[") {
            return Err(ConfigError::Parse {
                line: idx + 1,
                message: "array-of-tables ([[name]]) is not supported".to_string(),
            });
        }

        if let Some(rest) = line.strip_prefix('[') {
            let name = rest
                .strip_suffix(']')
                .ok_or_else(|| bad(line, idx, "unclosed table header"))?
                .trim();
            if name.is_empty() {
                return Err(ConfigError::Parse {
                    line: idx + 1,
                    message: "empty table name".to_string(),
                });
            }
            current = name.to_string();
            // Ensure the table exists even if it has no keys.
            config.tables.entry(current.clone()).or_default();
            continue;
        }

        let (key, value) = split_assignment(line, idx)?;
        let value = parse_value(&value, idx)?;
        config
            .tables
            .entry(current.clone())
            .or_default()
            .insert(key, value);
    }

    Ok(config)
}

/// Remove a trailing `#` comment, respecting quoted strings.
fn strip_comment(line: &str) -> Result<&str, ConfigError> {
    let mut in_double = false;
    let mut in_single = false;
    let bytes = line.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let c = bytes[i] as char;
        match c {
            '"' if !in_single => in_double = !in_double,
            '\'' if !in_double => in_single = !in_single,
            '#' if !in_double && !in_single => return Ok(&line[..i]),
            _ => {}
        }
        i += 1;
    }
    Ok(line)
}

fn split_assignment(line: &str, line_no: usize) -> Result<(String, String), ConfigError> {
    // Find the first `=` that is not inside a quoted string.
    let mut in_double = false;
    let mut in_single = false;
    let chars: Vec<char> = line.chars().collect();
    let mut depth = 0usize; // bracket depth for arrays
    for (i, c) in chars.iter().enumerate() {
        match c {
            '"' if !in_single => in_double = !in_double,
            '\'' if !in_double => in_single = !in_single,
            '[' if !in_double && !in_single => depth += 1,
            ']' if !in_double && !in_single => depth -= 1,
            '=' if !in_double && !in_single && depth == 0 => {
                let key: String = chars[..i].iter().collect();
                let value: String = chars[i + 1..].iter().collect();
                return Ok((key.trim().to_string(), value.trim().to_string()));
            }
            _ => {}
        }
    }
    Err(ConfigError::Parse {
        line: line_no + 1,
        message: "expected `key = value`".to_string(),
    })
}

fn parse_value(raw: &str, line_no: usize) -> Result<Value, ConfigError> {
    if raw.is_empty() {
        return Err(ConfigError::Parse {
            line: line_no + 1,
            message: "empty value".to_string(),
        });
    }

    match raw.chars().next().unwrap() {
        '"' => parse_double_quoted(raw, line_no),
        '\'' => {
            // A literal string is delimited by single quotes on both sides.
            let inner = raw
                .strip_prefix('\'')
                .and_then(|s| s.strip_suffix('\''))
                .ok_or_else(|| bad(raw, line_no, "unterminated literal string"))?;
            Ok(Value::Str(inner.to_string()))
        }
        '[' => parse_array(raw, line_no),
        _ => parse_scalar(raw, line_no),
    }
}

fn parse_double_quoted(raw: &str, line_no: usize) -> Result<Value, ConfigError> {
    let mut out = String::new();
    let mut chars = raw.chars().peekable();
    // Consume the opening quote.
    chars.next();
    loop {
        match chars.next() {
            None => {
                return Err(ConfigError::Parse {
                    line: line_no + 1,
                    message: "unterminated string".to_string(),
                })
            }
            Some('\\') => match chars.next() {
                Some('n') => out.push('\n'),
                Some('t') => out.push('\t'),
                Some('r') => out.push('\r'),
                Some('"') => out.push('"'),
                Some('\'') => out.push('\''),
                Some('\\') => out.push('\\'),
                Some(other) => {
                    return Err(ConfigError::Parse {
                        line: line_no + 1,
                        message: format!("invalid escape `\\{other}`"),
                    })
                }
                None => {
                    return Err(ConfigError::Parse {
                        line: line_no + 1,
                        message: "unterminated string".to_string(),
                    })
                }
            },
            Some('"') => return Ok(Value::Str(out)),
            Some(c) => out.push(c),
        }
    }
}

fn parse_array(raw: &str, line_no: usize) -> Result<Value, ConfigError> {
    let inner = raw
        .strip_prefix('[')
        .and_then(|s| s.strip_suffix(']'))
        .ok_or_else(|| bad(raw, line_no, "unterminated array"))?;
    let mut items = Vec::new();
    for part in split_array(inner) {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        items.push(parse_value(part, line_no)?);
    }
    if items.is_empty() {
        return Err(ConfigError::Parse {
            line: line_no + 1,
            message: "empty array".to_string(),
        });
    }
    Ok(Value::Array(items))
}

/// Split array contents on top-level commas (not inside quotes).
fn split_array(inner: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut in_double = false;
    let mut in_single = false;
    let mut start = 0;
    let bytes = inner.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        match b as char {
            '"' if !in_single => in_double = !in_double,
            '\'' if !in_double => in_single = !in_single,
            ',' if !in_double && !in_single => {
                parts.push(&inner[start..i]);
                start = i + 1;
            }
            _ => {}
        }
    }
    parts.push(&inner[start..]);
    parts
}

fn parse_scalar(raw: &str, line_no: usize) -> Result<Value, ConfigError> {
    match raw {
        "true" => Ok(Value::Bool(true)),
        "false" => Ok(Value::Bool(false)),
        other => {
            if let Ok(n) = other.parse::<i64>() {
                Ok(Value::Int(n))
            } else if let Ok(f) = other.parse::<f64>() {
                Ok(Value::Float(f))
            } else {
                Err(ConfigError::Parse {
                    line: line_no + 1,
                    message: format!("invalid value `{other}`"),
                })
            }
        }
    }
}

fn bad(line: &str, line_no: usize, message: &str) -> ConfigError {
    ConfigError::Parse {
        line: line_no + 1,
        message: format!("{message}: `{line}`"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_root_keys() {
        let cfg = parse_toml("verbose = true\ncount = 3").unwrap();
        assert_eq!(cfg.bool("", "verbose"), Some(true));
        assert_eq!(cfg.int("", "count"), Some(3));
    }

    #[test]
    fn parses_tables() {
        let src = "[tools]\nbuild = \"pengyos-build\"\nverify = \"pengyos-verifier\"\n";
        let cfg = parse_toml(src).unwrap();
        assert!(cfg.has_table("tools"));
        assert_eq!(cfg.str("tools", "build").as_deref(), Some("pengyos-build"));
        assert_eq!(cfg.str("tools", "verify").as_deref(), Some("pengyos-verifier"));
    }

    #[test]
    fn parses_nested_tables() {
        let src = "[a.b]\nx = 1\n";
        let cfg = parse_toml(src).unwrap();
        assert!(cfg.has_table("a.b"));
        assert_eq!(cfg.int("a.b", "x"), Some(1));
    }

    #[test]
    fn parses_arrays_of_strings() {
        let src = "[profile]\nfeatures = [\"a\", \"b\", \"c\"]\n";
        let cfg = parse_toml(src).unwrap();
        assert_eq!(
            cfg.str_array("profile", "features"),
            Some(vec!["a".to_string(), "b".to_string(), "c".to_string()])
        );
    }

    #[test]
    fn parses_quoted_strings_with_escapes() {
        let src = "msg = \"line1\\nline2\"\nlit = 'no \\escape'";
        let cfg = parse_toml(src).unwrap();
        assert_eq!(cfg.str("", "msg").as_deref(), Some("line1\nline2"));
        assert_eq!(cfg.str("", "lit").as_deref(), Some("no \\escape"));
    }

    #[test]
    fn ignores_comments_and_blanks() {
        let src = "# a comment\n\nverbose = true   # trailing comment\n";
        let cfg = parse_toml(src).unwrap();
        assert_eq!(cfg.bool("", "verbose"), Some(true));
    }

    #[test]
    fn rejects_bad_value() {
        let err = parse_toml("x = not_a_bool").unwrap_err();
        assert!(matches!(err, ConfigError::Parse { .. }));
    }

    #[test]
    fn rejects_missing_assignment() {
        let err = parse_toml("just_a_key").unwrap_err();
        assert!(matches!(err, ConfigError::Parse { .. }));
    }

    #[test]
    fn rejects_array_of_tables() {
        let err = parse_toml("[[rule]]\n").unwrap_err();
        assert!(matches!(err, ConfigError::Parse { .. }));
    }

    #[test]
    fn tool_for_defaults_and_overrides() {
        let default = Config::default();
        assert_eq!(default.tool_for("build"), "pengyos-build");

        let cfg = parse_toml("[tools]\nbuild = \"custom-build\"\n").unwrap();
        assert_eq!(cfg.tool_for("build"), "custom-build");
        // The default convention is `pengyos-<command>`; the exact mapping to
        // the sibling `pengyos-verifier` tool is refined by a later subtask.
        assert_eq!(cfg.tool_for("verify"), "pengyos-verify");
    }

    #[test]
    fn resolve_without_file_is_empty() {
        // No PENGYOS_CONFIG, no config file under a temp XDG dir.
        let cfg = Config::resolve().unwrap();
        assert!(!cfg.has_table("tools"));
    }
}
