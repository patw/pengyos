//! The `pengyos-install image` command line the Desktop installer runs.
//!
//! Pure (std only), so the flags the wizard's answers turn into are tested in
//! the default `cargo test`, without libcosmic. Secrets never go on argv: the
//! password (and the Pengy API key, when there is one) go to the engine on stdin;
//! see the `cosmic` app's `start_install`.

/// What the wizard answered, as the engine call needs it.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct InstallOptions {
    pub disk: String,
    pub hostname: String,
    pub username: String,
    /// "Log in automatically": skip the login screen at boot.
    pub autologin: bool,
    /// "Allow SSH logins with a password": SSH accepts the user's password as
    /// well as keys (less safe; root still needs a key). The engine runs the
    /// image's `pengyos-ssh-password on --offline` in the new system.
    pub ssh_password: bool,
    /// The assistant's endpoint; empty means the Pengy screen was skipped.
    pub pengy_base_url: String,
    /// True when an API key was typed (it is sent on stdin, line 2).
    pub pengy_api_key: bool,
    pub pengy_model: String,
}

/// `pengyos-install repair` for an installed PengyOS disk: the system is
/// rewritten, the data partition kept (no answers, no secrets).
pub fn repair_args(disk: &str) -> Vec<String> {
    ["repair", "--target", disk, "--yes", "--progress", "json"]
        .iter()
        .map(|s| s.to_string())
        .collect()
}

/// The engine's argv after the program name.
pub fn engine_args(o: &InstallOptions) -> Vec<String> {
    let mut args: Vec<String> = [
        "image", "--target", &o.disk, "--hostname", &o.hostname, "--user", &o.username,
        "--yes", "--password-stdin", "--progress", "json",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    if o.autologin {
        args.push("--autologin".into());
    }
    if o.ssh_password {
        args.push("--ssh-password".into());
    }
    // The assistant's endpoint, only when the screen was filled in. The key
    // rides on stdin (line 2, after the password), never on argv.
    let base_url = o.pengy_base_url.trim();
    if !base_url.is_empty() {
        args.push("--pengy-base-url".into());
        args.push(base_url.to_string());
        if o.pengy_api_key {
            args.push("--pengy-api-key-stdin".into());
        }
        let model = o.pengy_model.trim();
        if !model.is_empty() {
            args.push("--pengy-model".into());
            args.push(model.to_string());
        }
    }
    args
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> InstallOptions {
        InstallOptions {
            disk: "/dev/nvme0n1".into(),
            hostname: "desk".into(),
            username: "alex".into(),
            ..Default::default()
        }
    }

    #[test]
    fn repair_needs_only_the_disk() {
        assert_eq!(
            repair_args("/dev/nvme0n1"),
            ["repair", "--target", "/dev/nvme0n1", "--yes", "--progress", "json"]
        );
    }

    #[test]
    fn defaults_are_keys_only_and_the_login_screen() {
        let a = engine_args(&base());
        assert_eq!(
            a,
            [
                "image", "--target", "/dev/nvme0n1", "--hostname", "desk", "--user", "alex",
                "--yes", "--password-stdin", "--progress", "json",
            ]
        );
        assert!(!a.iter().any(|s| s == "--ssh-password" || s == "--autologin"));
    }

    #[test]
    fn ssh_password_is_passed_only_when_chosen() {
        let a = engine_args(&InstallOptions { ssh_password: true, ..base() });
        assert_eq!(a.iter().filter(|s| *s == "--ssh-password").count(), 1);
        assert!(!a.iter().any(|s| s == "--autologin"));
    }

    #[test]
    fn both_switches_together() {
        let a = engine_args(&InstallOptions { ssh_password: true, autologin: true, ..base() });
        assert!(a.iter().any(|s| s == "--autologin") && a.iter().any(|s| s == "--ssh-password"));
    }

    #[test]
    fn pengy_endpoint_and_key_flag_but_never_the_key() {
        let a = engine_args(&InstallOptions {
            pengy_base_url: " http://127.0.0.1:11434/v1 ".into(),
            pengy_api_key: true,
            pengy_model: "qwen3:8b".into(),
            ..base()
        });
        let tail: Vec<&str> = a[11..].iter().map(String::as_str).collect();
        assert_eq!(
            tail,
            ["--pengy-base-url", "http://127.0.0.1:11434/v1", "--pengy-api-key-stdin", "--pengy-model", "qwen3:8b"]
        );
    }

    #[test]
    fn a_skipped_pengy_screen_adds_nothing() {
        let a = engine_args(&InstallOptions {
            pengy_api_key: true,
            pengy_model: "qwen3:8b".into(),
            ..base()
        });
        assert!(!a.iter().any(|s| s.starts_with("--pengy")));
    }
}
