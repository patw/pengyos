use super::*;

const ON: &str = r#"{
  "enabled": true, "state": "running", "user": "pat", "port": 3389, "host": "behoweather",
  "addresses": ["10.0.23.4", "fd00::4"], "password_set": true,
  "fingerprint": "AB:CD", "consent_saved": true, "log": ["rdp: listening on 0.0.0.0:3389"]
}"#;

#[test]
fn parses_status() {
    let s = Status::parse(ON).unwrap();
    assert!(s.enabled && s.running() && s.password_set && s.consent_saved);
    assert_eq!(s.user, "pat");
    assert_eq!(s.fingerprint.as_deref(), Some("AB:CD"));
    assert_eq!(s.log.len(), 1);
}

#[test]
fn missing_fields_are_defaults_not_errors() {
    let s = Status::parse(r#"{"enabled": false, "fingerprint": null}"#).unwrap();
    assert!(!s.enabled && !s.running());
    assert_eq!(s.port, 3389);
    assert_eq!(s.fingerprint, None);
    assert!(Status::parse("not json").is_err());
}

#[test]
fn connect_to_lists_the_name_first_and_brackets_ipv6() {
    let s = Status::parse(ON).unwrap();
    assert_eq!(s.connect_to(), vec!["behoweather", "10.0.23.4", "[fd00::4]"]);
    let s = Status { port: 3390, ..s };
    assert_eq!(s.connect_to(), vec!["behoweather:3390", "10.0.23.4:3390", "[fd00::4]:3390"]);
}

#[test]
fn headlines() {
    let s = Status::parse(ON).unwrap();
    assert_eq!(s.headline(), "Remote desktop is on");
    assert_eq!(Status { state: "stopped".into(), ..s.clone() }.headline(),
               "Remote desktop is on, and starts when you log in");
    assert_eq!(Status { state: "failed".into(), ..s.clone() }.headline(),
               "Remote desktop is on, but its server stopped");
    assert_eq!(Status { enabled: false, ..s }.headline(), "Remote desktop is off");
}

#[test]
fn password_rules_match_the_cli() {
    assert!(password_problem("short", "short").unwrap().contains("at least 8"));
    assert!(password_problem("longenough", "longenougH").unwrap().contains("different"));
    assert!(password_problem("line\nbreak!", "line\nbreak!").unwrap().contains("line break"));
    assert!(password_problem(&"x".repeat(129), &"x".repeat(129)).unwrap().contains("at most"));
    assert_eq!(password_problem("correct horse", "correct horse"), None);
}

#[test]
fn the_password_never_goes_on_the_command_line() {
    let argv = password_argv();
    assert_eq!(argv, vec![REMOTE, "password", "--stdin"]);
    for a in [on_argv(), off_argv(), forget_argv(), status_argv()] {
        assert_eq!(a[0], REMOTE);
    }
}

#[test]
fn failures_read_as_sentences() {
    assert_eq!(failure("pengyos-remote: set an RDP password first: pengyos-remote password\n"),
               "Set an RDP password first: pengyos-remote password");
    assert_eq!(failure(""), "Pengyos-remote failed");
}

#[test]
fn run_reports_the_tools_own_words() {
    let sh = |script: &str| vec!["/bin/sh".to_string(), "-c".into(), script.into()];
    assert_eq!(run(&sh("cat"), Some("hi")).unwrap(), "hi");
    assert_eq!(run(&sh("echo 'pengyos-remote: nope' >&2; exit 2"), None).unwrap_err(), "Nope");
}
