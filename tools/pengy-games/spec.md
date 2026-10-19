# pengy-games — spec

One binary for every PengyOS Desktop game. The games' rules and behaviour are
their own crates' contracts; this file is the contract of the binary that
carries them.

## Purpose

Each game's window links libcosmic (and wgpu), about 30 MB of a stripped
binary. Eight games as eight binaries cost about 250 MB of the Desktop's fixed
root image; one binary costs about 37 MB. The root image is shared with
everything else the Desktop will grow, so the games must cost one toolkit, not
eight.

## Interface

- `GAMES`: every game, with its short name (`pong`), its binary name
  (`pengy-pong`), its title and its program (`pengy_pong::cli::main`).
- **Dispatch.** If the file name the binary was run as is a game's binary name,
  run that game with every argument. Otherwise the first argument names a game
  (short or binary name) and the remaining arguments are the game's. A game runs
  exactly as its own binary would: same flags, harness, exit codes, record file.
- With no game named: `--list` prints `name<TAB>binary<TAB>title` per game and
  exits 0; `--help` prints the usage and exits 0; `--version` prints
  `pengy-games X.Y.Z` and exits 0; nothing prints the usage on stderr and exits
  2; an unknown name says so and exits 2.

## Constraints

1. **No code of its own beyond dispatch.** No game logic, no window code: a game
   is reached only through its crate's `cli::main`.
2. **The window is behind `cosmic`**, which turns on each game's `cosmic`
   feature. Without it the crate depends only on the game crates (std only), so
   `cargo test` needs no network and no display.
3. **One toolkit copy.** The release profile is fat LTO with one codegen unit.
4. **Desktop only.** `scripts/build-cosmic.sh` builds it; the Desktop post-build
   installs `/usr/bin/pengy-games` and a `/usr/bin/pengy-<game>` symlink per
   game. No game is built or installed on its own. The launchers
   (`Exec=pengy-<game>`) and icons are in the Desktop overlay.

## Acceptance criteria

1. `cargo test` is green, with no display, in under a minute.
2. The first argument dispatches by short and by binary name, and the rest of
   the line reaches the game (`the_first_argument_names_the_game_and_the_rest_are_its_own`).
3. Every game in `GAMES` runs its headless harness through the binary
   (`every_game_runs_its_headless_harness`).
4. A symlink named for a game runs that game with every argument
   (`a_symlink_named_for_a_game_runs_that_game`).
5. No game and an unknown game exit 2, the first listing every game
   (`no_game_lists_the_games_and_exits_2`); `--list`, `--help`, `--version`
   exit 0 (`list_help_and_version_answer_on_stdout`).
6. Every game in `GAMES` is linked with its window, launched by an overlay
   entry, and symlinked by post-build; none is built or installed alone
   (`every_game_is_linked_with_its_window_and_wired_into_the_desktop`).
