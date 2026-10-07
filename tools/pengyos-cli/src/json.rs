//! A tiny, standard-library-only JSON value + serializer.
//!
//! The meta-CLI is intentionally standard-library only (it must build and test
//! fully offline), and JSON is not part of `std`. This module provides just
//! enough of a JSON model to emit the stable per-subcommand `--json` schema
//! (see `dispatch.rs`): an object of string keys to [`Value`]s, where a value is
//! a string, number, boolean, array, or nested object.
//!
//! This is an *emitter* only — there is no parser. Input comes from a
//! hand-built [`Value`] tree, so the output is always well-formed JSON.
//!
//! ## The `--json` schema
//!
//! Every subcommand emits the same top-level object:
//!
//! ```json
//! {
//!   "command": "build",
//!   "executable": "pengyos-build",
//!   "args": ["x86_64", "--verbose"],
//!   "exit_code": 0,
//!   "stdout": "...",
//!   "stderr": "...",
//!   "ok": true
//! }
//! ```
//!
//! * `command` — the canonical subcommand name.
//! * `executable` — the resolved sibling tool (per `Config::tool_for`).
//! * `args` — the forwarded argv (positionals then options/flags).
//! * `exit_code` — the tool's exit code (or `doctor`'s readiness code).
//! * `stdout` / `stderr` — captured output as strings.
//! * `ok` — `true` when `exit_code == 0`.
//!
//! The `doctor` subcommand additionally embeds its readiness matrix under a
//! `checks` array (see `src/doctor.rs`), but keeps the same top-level shape so
//! callers can always read `exit_code` / `ok` uniformly.

use std::fmt::Write as _;

/// A JSON value: object, array, string, number, or boolean.
///
/// Derives `PartialEq` (not `Eq`) because the [`Value::Number`] arm holds an
/// `f64`, which does not implement `Eq`. For the integer-valued numbers this
/// crate emits this is immaterial — equal integer values compare equal.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    /// A JSON object (`{...}`) with string keys.
    Object(Vec<(&'static str, Value)>),
    /// A JSON array (`[...]`).
    Array(Vec<Value>),
    /// A JSON string.
    Str(String),
    /// A JSON number (integer or floating point).
    Number(f64),
    /// A JSON boolean.
    Bool(bool),
}

impl Value {
    /// Serialize this value to a compact JSON string (no insignificant
    /// whitespace). The result is always well-formed.
    pub fn to_json(&self) -> String {
        let mut out = String::new();
        self.write_json(&mut out);
        out
    }

    fn write_json(&self, out: &mut String) {
        match self {
            Value::Object(pairs) => {
                out.push('{');
                for (i, (key, value)) in pairs.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    // Object keys are JSON strings: quote them (escaping the
                    // contents with the same rules as string values).
                    write_string_literal(*key, out);
                    out.push(':');
                    value.write_json(out);
                }
                out.push('}');
            }
            Value::Array(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    item.write_json(out);
                }
                out.push(']');
            }
            Value::Str(s) => write_string_literal(s, out),
            Value::Number(n) => {
                if n.fract() == 0.0 && n.is_finite() {
                    let _ = write!(out, "{}", *n as i64);
                } else {
                    let _ = write!(out, "{n}");
                }
            }
            Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        }
    }
}

/// Write a JSON string literal: escape the contents, then wrap them in quotes.
fn write_string_literal(s: &str, out: &mut String) {
    out.push('"');
    escape_string_contents(s, out);
    out.push('"');
}

/// Escape the *contents* of a JSON string (no surrounding quotes).
///
/// Handles the mandatory escapes (`"`, `\`, control chars) plus the common
/// shorthand escapes (`\n`, `\t`, `\r`, `\b`, `\f`) so output stays readable.
/// Used for both string values and object keys.
fn escape_string_contents(s: &str, out: &mut String) {
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_control_chars_in_strings() {
        let v = Value::Str("line1\nline2\t\"q\"".to_string());
        assert_eq!(v.to_json(), "\"line1\\nline2\\t\\\"q\\\"\"");
    }

    #[test]
    fn escapes_backslash() {
        let v = Value::Str("a\\b".to_string());
        assert_eq!(v.to_json(), "\"a\\\\b\"");
    }

    #[test]
    fn renders_object() {
        let v = Value::Object(vec![
            ("command", Value::Str("build".to_string())),
            ("exit_code", Value::Number(0.0)),
        ]);
        assert_eq!(v.to_json(), "{\"command\":\"build\",\"exit_code\":0}");
    }

    #[test]
    fn renders_array() {
        let v = Value::Array(vec![
            Value::Str("a".to_string()),
            Value::Str("b".to_string()),
        ]);
        assert_eq!(v.to_json(), "[\"a\",\"b\"]");
    }

    #[test]
    fn renders_nested_object() {
        let v = Value::Object(vec![(
            "checks",
            Value::Array(vec![Value::Object(vec![(
                "id",
                Value::Str("kvm".to_string()),
            )])]),
        )]);
        assert_eq!(v.to_json(), "{\"checks\":[{\"id\":\"kvm\"}]}");
    }

    #[test]
    fn renders_bool() {
        assert_eq!(Value::Bool(true).to_json(), "true");
        assert_eq!(Value::Bool(false).to_json(), "false");
    }

    #[test]
    fn renders_integer_and_float_numbers() {
        assert_eq!(Value::Number(123.0).to_json(), "123");
        assert_eq!(Value::Number(1.5).to_json(), "1.5");
    }

    #[test]
    fn empty_object_and_array() {
        assert_eq!(
            Value::Object(vec![]).to_json(),
            "{}"
        );
        assert_eq!(Value::Array(vec![]).to_json(), "[]");
    }
}
