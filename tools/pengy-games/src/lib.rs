//! The game table and the dispatch rule for `pengy-games`.
//!
//! One binary carries every PengyOS Desktop game. It picks the game from the
//! name it was run as (`/usr/bin/pengy-pong` is a symlink to `pengy-games`, and
//! a launcher's `Exec=pengy-pong` runs Penguin Pong) or, run as `pengy-games`,
//! from its first argument (`pengy-games pong --headless`). Whatever follows
//! goes to that game's own command line, unchanged.

/// One game: its short name, its binary name, its title, and its program.
pub struct Game {
    pub name: &'static str,
    pub binary: &'static str,
    pub title: &'static str,
    pub main: fn(Vec<String>),
}

/// Every game, in the order `pengy-games --list` prints them.
pub const GAMES: [Game; 8] = [
    Game { name: "solitaire", binary: "pengy-solitaire", title: "Frostbite Solitaire", main: pengy_solitaire::cli::main },
    Game { name: "merge", binary: "pengy-merge", title: "Icefloe 2048", main: pengy_merge::cli::main },
    Game { name: "blocks", binary: "pengy-blocks", title: "Glacier Blocks", main: pengy_blocks::cli::main },
    Game { name: "floe", binary: "pengy-floe", title: "Flappy Pengy", main: pengy_floe::cli::main },
    Game { name: "pong", binary: "pengy-pong", title: "Penguin Pong", main: pengy_pong::cli::main },
    Game { name: "invaders", binary: "pengy-invaders", title: "Thaw Invaders", main: pengy_invaders::cli::main },
    Game { name: "lander", binary: "pengy-lander", title: "Ice Lander", main: pengy_lander::cli::main },
    Game { name: "rogue", binary: "pengy-rogue", title: "Ice Rogue", main: pengy_rogue::cli::main },
];

/// A game by its short name (`pong`) or its binary name (`pengy-pong`).
pub fn find(name: &str) -> Option<&'static Game> {
    GAMES.iter().find(|g| g.name == name || g.binary == name)
}

/// What to run for a command line: the game, and the arguments it gets.
///
/// `argv0` is the name the binary was run as. If its file name is a game's
/// binary name, that is the game and it gets every argument. Otherwise the
/// first argument names the game and the rest are its own. `None` when no game
/// is named.
pub fn dispatch(argv0: &str, args: &[String]) -> Option<(&'static Game, Vec<String>)> {
    let called = argv0.rsplit('/').next().unwrap_or(argv0);
    if let Some(g) = GAMES.iter().find(|g| g.binary == called) {
        return Some((g, args.to_vec()));
    }
    let first = args.first()?;
    find(first).map(|g| (g, args[1..].to_vec()))
}

/// The text `pengy-games` prints when it is not told which game to run.
pub fn usage() -> String {
    let mut s = String::from(
        "pengy-games — the PengyOS Desktop games in one binary.\n\n\
         USAGE\n  pengy-games GAME [ARGS]...   run GAME with its own arguments\n  \
         pengy-GAME [ARGS]...          the same, through the pengy-GAME symlink\n  \
         pengy-games --list            the games, one per line\n\n\
         GAMES\n",
    );
    for g in &GAMES {
        s.push_str(&format!("  {:<10} {:<16} {}\n", g.name, g.binary, g.title));
    }
    s.push_str("\nEach game's own --help lists its flags and controls: pengy-games pong --help\n");
    s
}
