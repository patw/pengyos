//! Headless integration tests for the wizard state machine.
//!
//! These exercise the whole machine the same way the `--answers` smoke path
//! does: no display, no network, just the pure model.

use pengyos_install_model::{Layout, Model, Step};

const SCRIPTED: &[(&str, &str)] = &[
    ("accepted_terms", "true"),
    ("selected_disk", "/dev/sda"),
    ("layout", "wipe"),
    ("hostname", "pengyos-box"),
    ("username", "alice"),
    ("password", "hunter2"),
    ("network_ssid", "home"),
];

#[test]
fn scripted_run_completes_headlessly() {
    let model = Model::run_answers(SCRIPTED).expect("scripted run should complete");
    assert!(model.is_complete());
    assert_eq!(model.step(), Step::Progress);
}

#[test]
fn scripted_run_derives_golden_plan() {
    let model = Model::run_answers(SCRIPTED).expect("scripted run should complete");
    let toml = model.install_toml().expect("plan should be complete");
    assert!(toml.contains("target = \"/dev/sda\""));
    assert!(toml.contains("layout = \"wipe\""));
    assert!(toml.contains("hostname = \"pengyos-box\""));
    assert!(toml.contains("user = \"alice\""));
    assert!(toml.contains("network_ssid = \"home\""));
}

#[test]
fn scripted_run_rejects_missing_required_field() {
    // No password supplied.
    let answers: &[(&str, &str)] = &[
        ("accepted_terms", "true"),
        ("selected_disk", "/dev/sda"),
        ("layout", "preserve"),
        ("hostname", "host"),
        ("username", "bob"),
    ];
    let err = Model::run_answers(answers).unwrap_err();
    let msg = format!("{}", err);
    assert!(msg.contains("password"), "expected a password blocker, got: {msg}");
}

#[test]
fn scripted_run_without_accepting_terms_blocks() {
    let answers: &[(&str, &str)] = &[
        ("selected_disk", "/dev/sda"),
        ("layout", "wipe"),
        ("hostname", "host"),
        ("username", "bob"),
        ("password", "hunter2"),
    ];
    let err = Model::run_answers(answers).unwrap_err();
    assert!(format!("{}", err).contains("Welcome"));
}

#[test]
fn scripted_run_escapes_special_characters() {
    // A scripted answer set whose hostname / SSID contain a quote, backslash
    // and newline must still derive a valid, escaped install.toml.
    let answers: &[(&str, &str)] = &[
        ("accepted_terms", "true"),
        ("selected_disk", "/dev/sda"),
        ("layout", "wipe"),
        ("hostname", "a\"b\\c"),
        ("username", "alice"),
        ("password", "hunter2"),
        ("network_ssid", "ho\"me\nx"),
    ];
    let model = Model::run_answers(answers).expect("scripted run should complete");
    let toml = model.install_toml().expect("plan should be complete");
    // The escapes appear literally in the derived plan.
    assert!(toml.contains("hostname = \"a\\\"b\\\\c\""));
    assert!(toml.contains("network_ssid = \"ho\\\"me\\nx\""));
}

#[test]
fn every_layout_key_parses() {
    for (raw, expected) in [
        ("wipe", Layout::Wipe),
        ("preserve", Layout::Preserve),
        ("home-only", Layout::HomeOnly),
    ] {
        let mut m = Model::new();
        m.apply_answer("layout", raw);
        assert_eq!(m.layout(), Some(expected));
    }
}
