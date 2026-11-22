//! `pengyos-themes`: icon themes, colour themes and Looks for COSMIC (see spec.md).

use std::process::ExitCode;

use pengyos_themes::catalogue::{Catalogue, Mode};
use pengyos_themes::colours::{self, Now, Order, Query};
use pengyos_themes::fetch::Curl;
use pengyos_themes::{desktop, icons, looks, Error, Paths};
use serde_json::json;

const HELP: &str = "\
pengyos-themes — icon themes, colour themes and Looks for COSMIC, for you only

  icons list                       the catalogue's icon themes, and which are installed
  icons install <id>               download (checked against the catalogue's sha256) and install
  icons use <theme>                switch every app's icons (any installed theme, or Cosmic)
  icons remove <id>                remove an installed pack (back to Cosmic if it was in use)

  colours search [TEXT] [--order popular|new|name] [--color HEX] [--page N]
                                   browse cosmic-themes.org
  colours apply <id|file.ron>      a site id from the last search, a library or catalogue id, or a file
  colours library                  the colour themes you have applied or saved
  colours forget <id>              drop one from the library (the desktop keeps its colours)

  looks list                       curated Looks and your own
  looks apply <id>                 icons and colours together, as one undo step
  looks save <name>                keep what the desktop shows now as a Look
  looks forget <id>                drop one of your Looks

  undo                             put back what the last change replaced
  reset                            COSMIC's icons and default colours (dark/light stays)
  status                           the icon theme, dark or light, and the colours in use

  --json                           machine-readable output (list, library, search, status)

Everything is per user: ~/.local/share/{icons,pengyos-themes} and ~/.config/cosmic.
Exit status: 0 done, 1 failed, 2 usage.
";

enum Fail {
    Usage(String),
    Run(Error),
}

impl From<Error> for Fail {
    fn from(e: Error) -> Self {
        Fail::Run(e)
    }
}

type R = std::result::Result<(), Fail>;

fn usage<T>(s: &str) -> std::result::Result<T, Fail> {
    Err(Fail::Usage(s.to_string()))
}

struct Args {
    words: Vec<String>,
    json: bool,
    order: Option<String>,
    color: Option<String>,
    page: Option<String>,
}

fn parse_args(raw: Vec<String>) -> std::result::Result<Args, Fail> {
    let mut a = Args { words: vec![], json: false, order: None, color: None, page: None };
    let mut it = raw.into_iter();
    while let Some(w) = it.next() {
        let mut value = |name: &str| it.next().ok_or_else(|| Fail::Usage(format!("{name} needs a value")));
        match w.as_str() {
            "--json" => a.json = true,
            "--order" => a.order = Some(value("--order")?),
            "--color" | "--colour" => a.color = Some(value("--color")?),
            "--page" => a.page = Some(value("--page")?),
            s if s.starts_with("--") => return usage(&format!("unknown option {s}")),
            _ => a.words.push(w),
        }
    }
    Ok(a)
}

fn main() -> ExitCode {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    if raw.is_empty() || matches!(raw[0].as_str(), "-h" | "--help" | "help") {
        print!("{HELP}");
        return if raw.is_empty() { ExitCode::from(2) } else { ExitCode::SUCCESS };
    }
    match run(raw) {
        Ok(()) => ExitCode::SUCCESS,
        Err(Fail::Usage(s)) => {
            eprintln!("pengyos-themes: {s} (see pengyos-themes --help)");
            ExitCode::from(2)
        }
        Err(Fail::Run(e)) => {
            eprintln!("pengyos-themes: {e}");
            ExitCode::from(1)
        }
    }
}

fn running_as_root() -> bool {
    use std::os::unix::fs::MetadataExt;
    std::fs::metadata("/proc/self").map(|m| m.uid() == 0).unwrap_or(false)
}

fn catalogue(paths: &Paths) -> std::result::Result<Catalogue, Fail> {
    Ok(Catalogue::load(&paths.catalogue)?)
}

/// The catalogue when there is one (colours and Looks work without it). A
/// catalogue that is there but does not load is an error, not "no catalogue".
fn catalogue_opt(paths: &Paths) -> std::result::Result<Option<Catalogue>, Fail> {
    if !paths.catalogue.exists() {
        return Ok(None);
    }
    Ok(Some(Catalogue::load(&paths.catalogue)?))
}

fn run(raw: Vec<String>) -> R {
    let a = parse_args(raw)?;
    let w: Vec<&str> = a.words.iter().map(String::as_str).collect();
    let changes = !matches!(
        w.as_slice(),
        ["status"] | ["icons", "list"] | ["colours" | "colors", "search", ..] | ["colours" | "colors", "library"] | ["looks", "list"]
    );
    if changes && running_as_root() {
        return Err(Fail::Run(Error(
            "run this as the user whose desktop it is, not as root: themes are per user".into(),
        )));
    }
    let paths = Paths::from_env()?;
    match w.as_slice() {
        ["icons", "list"] => icons_list(&paths, a.json),
        ["icons", "install", id] => {
            let cat = catalogue(&paths)?;
            let pack = cat.pack(id).ok_or_else(|| Error(format!("no icon theme {id:?} in the catalogue")))?;
            eprintln!(
                "Downloading {} {} ({} MB, {}; {} MB installed)…",
                pack.name,
                pack.version,
                pack.size.div_ceil(1_000_000),
                pack.license,
                pack.installed_size.div_ceil(1_000_000)
            );
            let r = icons::install(&paths, &cat, id, &Curl)?;
            let names: Vec<&str> = pack.themes.iter().map(|t| t.dir.as_str()).collect();
            match &r {
                icons::Installed::AlreadyThere => println!("{} {} is already installed.", pack.name, pack.version),
                icons::Installed::Fresh => println!("Installed {}: {}.", pack.name, names.join(", ")),
                icons::Installed::Updated { from } => {
                    println!("Updated {} from {from} to {}: {}.", pack.name, pack.version, names.join(", "))
                }
            }
            if !matches!(r, icons::Installed::AlreadyThere) {
                println!("Use one with: pengyos-themes icons use {}", names[0]);
            }
            Ok(())
        }
        ["icons", "use", theme] => {
            icons::use_theme(&paths, theme)?;
            println!("Icons: {theme}.");
            Ok(())
        }
        ["icons", "remove", id] => {
            let cat = catalogue(&paths)?;
            let switched = icons::remove(&paths, &cat, id)?;
            if switched {
                println!("It was in use: icons are back to Cosmic.");
            }
            println!("Removed {id}.");
            Ok(())
        }
        ["colours" | "colors", "search", rest @ ..] => {
            let order = match a.order.as_deref() {
                None => Order::Popular,
                Some(o) => match Order::parse(o) {
                    Some(o) => o,
                    None => return usage("--order is popular, new or name"),
                },
            };
            let color = match a.color.as_deref() {
                None => None,
                Some(c) => match colours::parse_hex(c) {
                    Some(c) => Some(c),
                    None => return usage("--color is a hex colour like #3daee9"),
                },
            };
            let page = match a.page.as_deref().map(str::parse::<u32>) {
                None => 1,
                Some(Ok(n)) if n >= 1 => n,
                _ => return usage("--page is a number from 1"),
            };
            let q = Query { text: rest.join(" "), order, color, page };
            let api = std::env::var("PENGYOS_THEMES_API").unwrap_or_else(|_| colours::API.into());
            let (found, dropped) = colours::search(&paths, &Curl, &api, &q)?;
            if a.json {
                let v: Vec<_> = found
                    .iter()
                    .map(|f| {
                        json!({"id": f.id(), "name": f.theme.name, "author": f.theme.author,
                               "downloads": f.theme.downloads, "mode": f.mode.as_str(), "link": f.theme.link})
                    })
                    .collect();
                println!("{}", serde_json::to_string_pretty(&json!({"themes": v, "unsupported": dropped})).unwrap());
                return Ok(());
            }
            if found.is_empty() {
                println!("Nothing found.");
            }
            for f in &found {
                println!(
                    "{:<10} {:<5} {:<36} {}",
                    f.id(),
                    f.mode.as_str(),
                    f.theme.name,
                    f.theme.author.as_deref().unwrap_or("")
                );
            }
            if dropped > 0 {
                println!("({dropped} more on this page are not for this COSMIC version, so not shown)");
            }
            if found.len() as u32 + dropped as u32 >= colours::PAGE {
                println!("More: --page {}", page + 1);
            }
            Ok(())
        }
        ["colours" | "colors", "apply", arg] => {
            let cat = catalogue_opt(&paths)?;
            let (e, mode) = colours::apply(&paths, cat.as_ref(), arg)?;
            println!("Colours: {} ({}).", e.name, mode.as_str());
            Ok(())
        }
        ["colours" | "colors", "library"] => {
            let lib = colours::library(&paths);
            if a.json {
                println!("{}", serde_json::to_string_pretty(&lib).unwrap());
            } else if lib.is_empty() {
                println!("Your colour library is empty: colour themes you apply are kept here.");
            } else {
                for e in lib {
                    println!("{:<24} {:<5} {}", e.id, e.mode.as_str(), e.name);
                }
            }
            Ok(())
        }
        ["colours" | "colors", "forget", id] => {
            let e = colours::forget(&paths, id)?;
            println!("Forgot {}.", e.name);
            Ok(())
        }
        ["looks", "list"] => {
            let cat = catalogue_opt(&paths)?;
            let all = looks::list(&paths, cat.as_ref());
            if a.json {
                let v: Vec<_> = all
                    .iter()
                    .map(|l| {
                        json!({"id": l.id, "name": l.name, "summary": l.summary, "icons": l.icons,
                               "colours": l.colours, "mode": l.mode.as_str(), "curated": l.curated})
                    })
                    .collect();
                println!("{}", serde_json::to_string_pretty(&v).unwrap());
            } else if all.is_empty() {
                println!("No Looks yet: save one with `pengyos-themes looks save <name>`.");
            } else {
                for l in all {
                    let who = if l.curated { "" } else { " (yours)" };
                    println!("{:<20} {:<5} {} — icons {}, colours {}{who}", l.id, l.mode.as_str(), l.name, l.icons, l.colours);
                }
            }
            Ok(())
        }
        ["looks", "apply", id] => {
            let cat = catalogue_opt(&paths)?;
            let l = looks::apply(&paths, cat.as_ref(), id, &Curl)?;
            println!("Look: {} (icons {}, {}). Undo with: pengyos-themes undo", l.name, l.icons, l.mode.as_str());
            Ok(())
        }
        ["looks", "save", name @ ..] if !name.is_empty() => {
            let cat = catalogue_opt(&paths)?;
            let l = looks::save(&paths, cat.as_ref(), &name.join(" "))?;
            println!("Saved the Look {} ({}).", l.name, l.id);
            Ok(())
        }
        ["looks", "forget", id] => {
            let l = looks::forget(&paths, id)?;
            println!("Forgot the Look {}.", l.name);
            Ok(())
        }
        ["undo"] => {
            match desktop::undo(&paths)? {
                Some(label) => println!("Undid: {label}."),
                None => println!("Nothing to undo."),
            }
            Ok(())
        }
        ["reset"] => {
            desktop::reset(&paths)?;
            println!("Icons and colours are COSMIC's defaults again. Undo with: pengyos-themes undo");
            Ok(())
        }
        ["status"] => status(&paths, a.json),
        _ => usage(&format!("unknown command: {}", a.words.join(" "))),
    }
}

fn icons_list(paths: &Paths, json_out: bool) -> R {
    let cat = catalogue(paths)?;
    let active = desktop::icon_theme(paths);
    let rows: Vec<_> = cat
        .icons
        .iter()
        .map(|p| {
            let inst = icons::installed(paths, p);
            json!({
                "id": p.id, "name": p.name, "version": p.version, "summary": p.summary,
                "license": p.license, "download_mb": p.size.div_ceil(1_000_000),
                "installed_mb": p.installed_size.div_ceil(1_000_000),
                "themes": p.themes.iter().map(|t| t.dir.clone()).collect::<Vec<_>>(),
                "installed": inst.as_ref().map(|m| m.version.clone()),
                "update": inst.as_ref().is_some_and(|m| m.version != p.version),
            })
        })
        .collect();
    if json_out {
        println!("{}", serde_json::to_string_pretty(&json!({"active": active, "packs": rows})).unwrap());
        return Ok(());
    }
    println!("Icons in use: {active}");
    for (p, r) in cat.icons.iter().zip(&rows) {
        let state = match (&r["installed"], r["update"].as_bool().unwrap_or(false)) {
            (serde_json::Value::Null, _) => "not installed".to_string(),
            (v, true) => format!("installed {}, update to {}", v.as_str().unwrap_or(""), p.version),
            (_, false) => "installed".to_string(),
        };
        println!(
            "{:<12} {} {} — {} ({}, {} MB) [{}]",
            p.id,
            p.name,
            p.version,
            p.themes.iter().map(|t| t.dir.as_str()).collect::<Vec<_>>().join(", "),
            p.license,
            p.installed_size.div_ceil(1_000_000),
            state
        );
    }
    Ok(())
}

fn status(paths: &Paths, json_out: bool) -> R {
    let icons = desktop::icon_theme(paths);
    let mode = desktop::mode(paths);
    let now = colours::now(paths, mode);
    let undo = desktop::undo_depth(paths);
    let (state, id, name) = match &now {
        Now::Default => ("default", None, None),
        Now::Applied(a) => ("applied", Some(a.id.clone()), Some(a.name.clone())),
        Now::Custom => ("custom", None, None),
    };
    if json_out {
        println!(
            "{}",
            serde_json::to_string_pretty(&json!({
                "icons": icons, "mode": mode.as_str(),
                "colours": {"state": state, "id": id, "name": name},
                "undo": undo,
            }))
            .unwrap()
        );
        return Ok(());
    }
    println!("Icons:   {icons}");
    println!("Mode:    {}", if mode == Mode::Dark { "dark" } else { "light" });
    println!(
        "Colours: {}",
        match now {
            Now::Default => "COSMIC's defaults".to_string(),
            Now::Applied(a) => format!("{} ({})", a.name, a.id),
            Now::Custom => "your own (changed in Settings)".to_string(),
        }
    );
    println!("Undo:    {undo} step(s)");
    Ok(())
}
