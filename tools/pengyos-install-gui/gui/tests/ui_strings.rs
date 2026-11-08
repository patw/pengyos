//! The libcosmic shell is behind the `cosmic` feature and is not compiled by the
//! default `cargo test`, so its on-screen text is checked from the source.
//!
//! The Pengy screen once showed "Leave it empty to          choose later.": a
//! reformat joined the lines of a `\`-continued string literal and kept the
//! indentation inside it. A single-line literal never needs a run of spaces in
//! the middle of its text.

use std::path::Path;

fn literal_spans(line: &str) -> Option<&str> {
    let first = line.find('"')?;
    let last = line.rfind('"')?;
    (last > first).then(|| &line[first + 1..last])
}

fn has_word_gap(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b' ' {
            let start = i;
            while i < bytes.len() && bytes[i] == b' ' {
                i += 1;
            }
            if i - start >= 4 && start > 0 && i < bytes.len() {
                return true;
            }
        } else {
            i += 1;
        }
    }
    false
}

#[test]
fn word_gap_detector_works() {
    assert!(has_word_gap("Leave it empty to                      choose later."));
    assert!(!has_word_gap("{}  —  {}"));
    assert!(!has_word_gap("Leave it empty to choose later."));
}

#[test]
fn no_screen_text_has_a_gap_between_words() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/cosmic_app/cosmic_app_impl.rs");
    let text = std::fs::read_to_string(&src).expect("read cosmic_app_impl.rs");
    let bad: Vec<String> = text
        .lines()
        .enumerate()
        .filter(|(_, l)| literal_spans(l).is_some_and(has_word_gap))
        .map(|(n, l)| format!("{}:{}: {}", src.display(), n + 1, l.trim()))
        .collect();
    assert!(bad.is_empty(), "string literals with a run of spaces between words:\n{}", bad.join("\n"));
}
