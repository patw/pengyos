//! The CLI end to end, over a temporary home and the shipped catalogue. No network:
//! nothing here downloads (icon installs and site searches are covered in the
//! library's tests, with the network stubbed).

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use pengyos_themes::catalogue::Catalogue;

const BIN: &str = env!("CARGO_BIN_EXE_pengyos-themes");

fn shipped() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("catalogue/catalogue.toml")
}

fn run(home: &Path, args: &[&str]) -> Output {
    Command::new(BIN)
        .args(args)
        .env_clear()
        .env("HOME", home)
        .env("XDG_DATA_DIRS", home.join("system"))
        .env("PENGYOS_THEMES_CATALOGUE", shipped())
        // Never reach the network from a test, whatever happens.
        .env("PENGYOS_THEMES_API", "https://127.0.0.1:9/api")
        .env("PATH", "/nonexistent")
        .output()
        .unwrap()
}

fn text(o: &Output) -> String {
    format!("{}{}", String::from_utf8_lossy(&o.stdout), String::from_utf8_lossy(&o.stderr))
}

#[test]
fn the_shipped_catalogue_is_valid() {
    let c = Catalogue::load(&shipped()).unwrap();
    c.check_files().unwrap();
    let papirus = c.pack("papirus").unwrap();
    assert!(papirus.url.contains(&papirus.version), "the URL is pinned to the listed version");
    assert!(c.looks.len() >= 2, "curated Looks ship at launch");
    assert!(c.looks.iter().any(|l| l.mode.is_dark()) && c.looks.iter().any(|l| !l.mode.is_dark()),
            "a curated Look for each mode");
    for l in &c.looks {
        assert_ne!(l.colours, "default", "{}: a curated Look carries its own colours", l.id);
    }
    // Our own colour themes, so they can ship in the image.
    assert!(c.colours.iter().all(|x| x.author == "PengyOS" && x.license == "MIT"));
}

#[test]
fn a_broken_catalogue_is_reported_not_ignored() {
    let h = tempfile::tempdir().unwrap();
    let bad = h.path().join("catalogue.toml");
    std::fs::write(&bad, "version = 9\n").unwrap();
    let o = Command::new(BIN)
        .args(["looks", "list"])
        .env_clear()
        .env("HOME", h.path())
        .env("PENGYOS_THEMES_CATALOGUE", &bad)
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&o.stderr).contains("version 9 is not supported"));
}

#[test]
fn usage_errors_exit_2() {
    let h = tempfile::tempdir().unwrap();
    for args in [&[][..], &["nope"], &["icons"], &["colours", "search", "--order", "x"], &["colours", "search", "--color", "red"], &["--frob"]] {
        let o = run(h.path(), args);
        assert_eq!(o.status.code(), Some(2), "{args:?}: {}", text(&o));
    }
    assert_eq!(run(h.path(), &["--help"]).status.code(), Some(0));
}

#[test]
fn failures_exit_1_with_one_line() {
    let h = tempfile::tempdir().unwrap();
    for args in [&["icons", "use", "Nope"][..], &["colours", "apply", "site-1"], &["looks", "apply", "nope"], &["icons", "remove", "papirus"]] {
        let o = run(h.path(), args);
        assert_eq!(o.status.code(), Some(1), "{args:?}: {}", text(&o));
        assert!(String::from_utf8_lossy(&o.stderr).starts_with("pengyos-themes: "));
    }
    // No network: a search fails cleanly (curl is not even on PATH here).
    let o = run(h.path(), &["colours", "search"]);
    assert_eq!(o.status.code(), Some(1), "{}", text(&o));
}

#[test]
fn apply_status_undo_reset() {
    let h = tempfile::tempdir().unwrap();
    let cosmic = h.path().join(".config/cosmic");
    let o = run(h.path(), &["status", "--json"]);
    let s: serde_json::Value = serde_json::from_slice(&o.stdout).unwrap();
    assert_eq!((s["icons"].as_str(), s["colours"]["state"].as_str()), (Some("Cosmic"), Some("default")));

    // A catalogue colour theme applies with no network.
    let o = run(h.path(), &["colours", "apply", "meadow-light"]);
    assert!(o.status.success(), "{}", text(&o));
    let s: serde_json::Value = serde_json::from_slice(&run(h.path(), &["status", "--json"]).stdout).unwrap();
    assert_eq!((s["mode"].as_str(), s["colours"]["id"].as_str()), (Some("light"), Some("meadow-light")));
    assert!(cosmic.join("com.system76.CosmicTheme.Light.Builder/v2/accent").is_file());

    // A file, kept in the library; then a Look saved from it.
    let f = h.path().join("Glacier copy.ron");
    std::fs::copy(shipped().parent().unwrap().join("colours/glacier-dark.ron"), &f).unwrap();
    assert!(run(h.path(), &["colours", "apply", f.to_str().unwrap()]).status.success());
    let lib: serde_json::Value = serde_json::from_slice(&run(h.path(), &["colours", "library", "--json"]).stdout).unwrap();
    assert_eq!(lib[0]["id"], "file-glacier-copy");
    assert!(run(h.path(), &["looks", "save", "My", "Glacier"]).status.success());
    let looks: serde_json::Value = serde_json::from_slice(&run(h.path(), &["looks", "list", "--json"]).stdout).unwrap();
    assert!(looks.as_array().unwrap().iter().any(|l| l["id"] == "my-glacier" && l["curated"] == false));

    // Undo walks back: the file, then the catalogue theme.
    assert!(text(&run(h.path(), &["undo"])).contains("Undid: colours Glacier copy"));
    assert!(text(&run(h.path(), &["undo"])).contains("Undid: colours Meadow"));
    assert!(text(&run(h.path(), &["undo"])).contains("Nothing to undo"));
    assert!(!cosmic.join("com.system76.CosmicTheme.Light.Builder").exists());

    assert!(run(h.path(), &["reset"]).status.success());
    let s: serde_json::Value = serde_json::from_slice(&run(h.path(), &["status", "--json"]).stdout).unwrap();
    assert_eq!(s["colours"]["state"], "default");
}
