//! `pengy-games` — run the game this binary was asked for (see `pengy_games`).

use std::process;

fn main() {
    let mut argv = std::env::args();
    let argv0 = argv.next().unwrap_or_default();
    let args: Vec<String> = argv.collect();
    if let Some((game, rest)) = pengy_games::dispatch(&argv0, &args) {
        (game.main)(rest);
        return;
    }
    match args.first().map(String::as_str) {
        Some("--list") => {
            for g in &pengy_games::GAMES {
                println!("{}\t{}\t{}", g.name, g.binary, g.title);
            }
        }
        Some("--help") | Some("-h") => print!("{}", pengy_games::usage()),
        Some("--version") => println!("pengy-games {}", env!("CARGO_PKG_VERSION")),
        Some(other) => {
            eprintln!("pengy-games: no game called {other:?}; try pengy-games --list");
            process::exit(2);
        }
        None => {
            eprint!("{}", pengy_games::usage());
            process::exit(2);
        }
    }
}
