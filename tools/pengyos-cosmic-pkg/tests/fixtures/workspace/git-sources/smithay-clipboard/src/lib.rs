//! Fixture copy of `smithay-clipboard` (demo data — not the real crate).
//!
//! This is the checked-out source of the git dependency that the `vendor`
//! subtask copies into the vendored tree. It exists purely so the git-rewrite
//! path can be exercised offline.

/// A marker the golden test can assert on (proves the right crate was copied).
pub const VENDORED_FIXTURE_NAME: &str = "smithay-clipboard";

/// Just enough body to be a real source file.
pub fn greeting() -> &'static str {
    "hello from vendored smithay-clipboard"
}
