use super::*;

const STATUS: &str = r#"{
  "service": "ksmbd.service",
  "active": true,
  "enabled": true,
  "host": "behodesk",
  "shares": [
    {"name": "media", "path": "/home/pat/Music", "read_only": false, "users": ["pat"], "comment": ""},
    {"name": "Photos", "path": "/data/photos", "read_only": true, "users": ["pat", "sam"], "comment": "Family"}
  ],
  "users": ["pat"]
}"#;

#[test]
fn status_is_read() {
    let s = State::parse(STATUS).unwrap();
    assert!(s.active && s.enabled);
    assert_eq!(s.host, "behodesk");
    assert_eq!(s.shares.len(), 2);
    assert_eq!(s.shares[1], Share {
        name: "Photos".into(),
        path: "/data/photos".into(),
        read_only: true,
        users: vec!["pat".into(), "sam".into()],
        comment: "Family".into(),
    });
    assert_eq!(s.has_password("pat"), Some(true));
    assert_eq!(s.has_password("sam"), Some(false));
}

#[test]
fn status_without_root_does_not_know_the_passwords() {
    let s = State::parse(r#"{"active": false, "host": "h", "shares": [], "users": null}"#).unwrap();
    assert_eq!(s.users, None);
    assert_eq!(s.has_password("pat"), None);
    assert!(State::parse("not json").is_err());
}

#[test]
fn addresses_for_windows_and_the_rest() {
    assert_eq!(addresses("behodesk", "media"), ("\\\\behodesk\\media".into(), "smb://behodesk/media".into()));
}

#[test]
fn describe_says_mode_and_people() {
    let s = State::parse(STATUS).unwrap();
    assert_eq!(describe(&s.shares[0]), "Read and write · pat");
    assert_eq!(describe(&s.shares[1]), "Read-only · pat, sam");
}

#[test]
fn names_follow_the_cli_rules() {
    let s = State::parse(STATUS).unwrap().shares;
    assert_eq!(name_problem("music", &s), None);
    assert_eq!(name_problem("media", &s), None, "changing an existing share keeps its name");
    assert!(name_problem("MEDIA", &s).unwrap().contains("already a share name"));
    assert!(name_problem("", &s).is_some());
    assert!(name_problem("-x", &s).is_some());
    assert!(name_problem("has space", &s).is_some());
    assert!(name_problem(&"a".repeat(33), &s).is_some());
    assert!(name_problem("IPC$", &s).is_some());
    assert!(name_problem("Homes", &s).unwrap().contains("reserves"));
}

#[test]
fn paths_must_be_real_non_system_folders() {
    assert!(path_problem("").is_some());
    assert!(path_problem("relative/dir").is_some());
    assert!(path_problem("/").unwrap().contains("System"));
    assert!(path_problem("/etc").unwrap().contains("System"));
    assert!(path_problem("/usr/share").unwrap().contains("System"));
    let dir = std::env::temp_dir();
    assert_eq!(path_problem(dir.to_str().unwrap()), None);
    assert!(path_problem("/no/such/folder/here").unwrap().contains("not a folder"));
}

#[test]
fn suggested_names_are_valid() {
    let none: Vec<Share> = Vec::new();
    for (path, want) in [
        ("/home/pat/Music", "Music"),
        ("/home/pat/My Photos", "My-Photos"),
        ("/home/pat/.hidden", "hidden"),
        ("/data/Ünïcode", "n-code"),
        ("/", "share"),
        ("/home/pat/homes", "share"),
    ] {
        let got = suggest_name(path);
        assert_eq!(got, want, "{path}");
        assert_eq!(name_problem(&got, &none), None, "{path} -> {got}");
    }
    assert_eq!(suggest_name(&format!("/x/{}", "a".repeat(40))).len(), NAME_MAX);
}

#[test]
fn passwords() {
    assert!(password_problem("short", "short").is_some());
    assert!(password_problem("longenough", "different").unwrap().contains("different"));
    assert!(password_problem("line\nbreak", "line\nbreak").is_some());
    assert!(password_problem(&"x".repeat(129), &"x".repeat(129)).is_some());
    assert_eq!(password_problem("longenough", "longenough"), None);
}

#[test]
fn command_lines() {
    let users = vec!["pat".to_string(), "sam".to_string()];
    assert_eq!(
        add_argv("media", "/home/pat/Music", true, &users),
        ["pkexec", "/usr/bin/pengyos-shares", "add", "media", "/home/pat/Music", "--read-only", "--user", "pat", "--user", "sam"]
    );
    assert_eq!(add_argv("m", "/p", false, &users[..1])[5..], ["--user", "pat"]);
    assert_eq!(remove_argv("media"), ["pkexec", "/usr/bin/pengyos-shares", "remove", "media"]);
    assert_eq!(passwd_argv("pat"), ["pkexec", "/usr/bin/pengyos-shares", "passwd", "pat", "--stdin"]);
    assert_eq!(status_argv(true)[..2], ["pkexec", "/usr/bin/pengyos-shares"]);
    assert_eq!(status_argv(false), ["/usr/bin/pengyos-shares", "status", "--json"]);
}

#[test]
fn failures_in_words() {
    assert!(failure(Some(126), "Error executing command as another user: Not authorized").contains("administrator"));
    assert!(failure(Some(127), "").contains("administrator"));
    assert_eq!(
        failure(Some(2), "pengyos-shares: /etc is a system folder; share something under /home or /data\n"),
        "/etc is a system folder; share something under /home or /data"
    );
    assert_eq!(failure(Some(1), "pengyos-shares: could not start ksmbd"), "Could not start ksmbd");
    assert_eq!(failure(Some(1), ""), "Pengyos-shares failed");
}

#[test]
fn run_feeds_stdin_and_reports_stderr() {
    let argv = ["sh".to_string(), "-c".into(), "cat".into()];
    assert_eq!(run(&argv, Some("secret123\n")).unwrap(), "secret123\n");
    let argv = ["sh".to_string(), "-c".into(), "echo 'pengyos-shares: no such share: x' >&2; exit 2".into()];
    assert_eq!(run(&argv, None).unwrap_err(), "No such share: x");
    assert!(run(&["/no/such/program".to_string()], None).unwrap_err().starts_with("Could not run"));
}

#[test]
fn groups() {
    let group = "root:x:0:\nwheel:x:10:pat\nusers:x:100:\n";
    assert_eq!(group_id(group, "wheel"), Some(10));
    assert_eq!(group_id(group, "nope"), None);
    let status = "Name:\tx\nGroups:\t10 28 1000 \nUid:\t1000\n";
    assert!(has_group(status, 10));
    assert!(!has_group(status, 0));
    assert!(!has_group("Groups:\n", 10));
}
