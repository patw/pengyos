# pengy-games — the PengyOS Desktop games in one binary

The eight games that ship on PengyOS Desktop and Desktop NVIDIA are each their
own crate in `tools/pengy-<game>`, with their own rules, tests and docs. This
crate links all eight into **one binary**, so the image carries one copy of
libcosmic instead of eight: about 37 MB stripped for all eight games, against
about 250 MB as eight separate binaries.

| Game | Crate | Launcher |
|---|---|---|
| Frostbite Solitaire | [`pengy-solitaire`](../pengy-solitaire/) | `io.catbee.pengyos.Solitaire` |
| Icefloe 2048 | [`pengy-merge`](../pengy-merge/) | `io.catbee.pengyos.Icefloe` |
| Glacier Blocks | [`pengy-blocks`](../pengy-blocks/) | `io.catbee.pengyos.GlacierBlocks` |
| Flappy Pengy | [`pengy-floe`](../pengy-floe/) | `io.catbee.pengyos.FlappyPengy` |
| Penguin Pong | [`pengy-pong`](../pengy-pong/) | `io.catbee.pengyos.PenguinPong` |
| Thaw Invaders | [`pengy-invaders`](../pengy-invaders/) | `io.catbee.pengyos.ThawInvaders` |
| Ice Lander | [`pengy-lander`](../pengy-lander/) | `io.catbee.pengyos.IceLander` |
| Ice Rogue | [`pengy-rogue`](../pengy-rogue/) | `io.catbee.pengyos.IceRogue` |

## How it picks the game

```sh
pengy-pong                     # /usr/bin/pengy-pong is a symlink: Penguin Pong
pengy-pong --headless          # every argument is the game's own
pengy-games pong --headless    # the same, naming the game
pengy-games --list             # the games
```

Run under a game's binary name (the symlinks the Desktop post-build makes), it
runs that game with every argument. Run as `pengy-games`, the first argument
names the game (`pong` or `pengy-pong`) and the rest are the game's. Each game's
program is its library's `cli::main`, so a game behaves exactly as its own
`pengy-<game>` binary does: same flags, same headless harness, same record file.
The launchers run `pengy-<game>`, and each window keeps its game's own app id,
so the dock and the App Library tell them apart.

## Building

```sh
cd tools/pengy-games
cargo test                                       # dispatch + wiring, no display
cargo build --release --features cosmic          # the windows (libcosmic)
```

`scripts/build-cosmic.sh` builds it into the shared libcosmic cache with the
other PengyOS apps; `board/desktop/post-build.sh` installs `/usr/bin/pengy-games`
and the eight `pengy-<game>` symlinks. The release profile is fat LTO with one
codegen unit: that is what folds the eight copies of the iced runtime (one per
game's app type) and keeps the binary near the size of one game. The cost is a
slow release link (about 8 minutes on the build host); while working on one game,
build that game's own crate instead.

## Adding a game

Give the game a crate whose program is `pub mod cli` with `pub fn main(Vec<String>)`
and whose window is behind a `cosmic` feature (see any `tools/pengy-<game>`),
then add it to this crate's `[dependencies]` and `cosmic` feature, to `GAMES` in
`src/lib.rs`, and to the post-build `for game in` list, with a launcher and an
icon in the Desktop overlay. `tests/github_ready.rs` checks all of that.

## Layout

| Path | What is in it |
|---|---|
| `src/lib.rs` | The game table (`GAMES`) and the dispatch rule (`dispatch`). |
| `src/main.rs` | Dispatch, `--list`, `--help`, `--version`. |
| `tests/dispatch.rs` | The binary: by first argument, by symlink name, every game's headless harness, the errors. |
| `tests/github_ready.rs` | The tool contract, and the wiring: every game linked with its window, launched, symlinked, and no game built alone. |

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
