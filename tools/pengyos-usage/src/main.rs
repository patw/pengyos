//! `pengyos-usage` — the "Disk Usage" window. `pengyos-usage FOLDER` opens it
//! scanning FOLDER; `--report FOLDER` prints the biggest entries instead (no
//! window), for SSH and tests.

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use pengyos_usage::{human, mounts, mounts_inside, percent, scan, Kind, Node, Progress};

fn report(path: &Path) -> ExitCode {
    let skip = mounts_inside(&mounts(), path);
    let s = match scan(path, &skip, &Progress::default()) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("pengyos-usage: {e}");
            return ExitCode::from(1);
        }
    };
    println!("{}  {}  ({} items)", human(s.root.size), s.path.display(), s.root.items);
    fn show(n: &Node, total: u64, depth: usize, max: usize) {
        for c in n.children.iter().take(12) {
            let mark = if c.kind == Kind::Dir { "/" } else { "" };
            println!("{}{:>10}  {:>6}  {}{mark}", "    ".repeat(depth), human(c.size), percent(c.size, total), c.name);
            if depth + 1 < max {
                show(c, total, depth + 1, max);
            }
        }
    }
    show(&s.root, s.root.size, 1, 3);
    for m in &s.mounts {
        println!("not walked (another filesystem): {}", m.display());
    }
    for m in &s.snapshots {
        println!("not counted (btrfs snapshots share their space): {}", m.display());
    }
    if s.unreadable_count > 0 {
        println!("could not read {} folder(s), e.g. {}", s.unreadable_count, s.unreadable[0].display());
    }
    ExitCode::SUCCESS
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Some(i) = args.iter().position(|a| a == "--report") {
        let path = args.get(i + 1).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
        return report(&path.canonicalize().unwrap_or(path));
    }

    #[cfg(feature = "cosmic")]
    {
        let folder = args.iter().find(|a| !a.starts_with("--")).map(PathBuf::from);
        let settings = cosmic::app::Settings::default().size(cosmic::iced::Size::new(980.0, 680.0));
        match cosmic::app::run::<pengyos_usage::app::App>(settings, folder) {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("pengyos-usage: {err}");
                ExitCode::from(1)
            }
        }
    }

    #[cfg(not(feature = "cosmic"))]
    {
        eprintln!("pengyos-usage: built without the window (--features cosmic); use --report FOLDER");
        ExitCode::from(2)
    }
}
