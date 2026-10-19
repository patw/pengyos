# Changelog

All notable changes to `pengy-games` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-18

### Added

- `pengy-games`: the eight PengyOS Desktop games in one binary. It runs the game
  named by the name it was called as (`/usr/bin/pengy-<game>` is a symlink to
  it) or by its first argument (`pengy-games pong ...`), and hands the rest of
  the command line to that game's own `cli::main`. `--list`, `--help`,
  `--version`; no game, or an unknown one, exits 2.
- One copy of libcosmic for all eight: fat LTO and one codegen unit fold the
  eight iced runtimes, so the stripped binary is about 37 MB, against about
  31 MB for one game alone and about 250 MB for eight separate binaries.
- Wired into the Desktop: `scripts/build-cosmic.sh` builds it with `--features
  cosmic`; `board/desktop/post-build.sh` installs it and the eight symlinks.
