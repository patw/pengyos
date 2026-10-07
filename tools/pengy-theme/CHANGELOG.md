# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **WCAG contrast-ratio math** (`pengy_theme::contrast`): relative-luminance and
  the WCAG 2.x relative contrast ratio for an `fg`/`bg` color pair, including the
  canonical reference values (white-on-black `21.0`, `#767676`-on-white `4.54`)
  and the AA thresholds (`WCAG_AA_NORMAL = 4.5`, `WCAG_AA_LARGE = 3.0`).
  Dependency-free (standard library only).
- **Manifest validation** (`pengy_theme::validate`): `validate(theme)` turns a
  parsed [`Theme`](src/manifest.rs) into a [`ValidationReport`](src/validate.rs)
  that confirms the required tokens are present, every color role is a
  well-formed `#rrggbb`, and each key foreground/background pair meets the WCAG AA
  contrast threshold. `foreground`/`background` is always required; the optional
  semantic roles (`warning`/`error`/`success`) are checked only when present. A
  low-contrast `accent` is a soft `warn` (does not fail); a low-contrast required
  pair is a hard `error`. `Theme::validate` and the `validate` subcommand surface
  it.

### Changed

- The `validate` subcommand is now implemented: it loads a manifest, validates it,
  prints the report, and exits `0` when valid or `1` when it has hard errors
  (`2` for a usage error, e.g. a missing manifest argument).

## [0.5.1] - 2026-09-23

### Fixed

- **Deterministic packaging** (`pengy_theme::pack`): the POSIX *ustar* writer
  (`build_ustar_header`) now places every header field at its canonical ustar
  offset (`name`@0, `mode`@100, `uid`@108, `gid`@116, `size`@124, `mtime`@136,
  `checksum`@148, `typeflag`@156, `linkname`@157, `ustar` magic@257, version@263,
  `uname`@265, `gname`@297, `prefix`@329) and computes the 8-byte checksum with
  the field treated as spaces. Previously the fields were written at the wrong
  offsets and the checksum was computed over a 7-byte field at the wrong
  location, so the produced `<name>.tar` was a *malformed* ustar that GNU tar
  rejected ("does not look like a tar archive"). The archives are now valid POSIX
  ustar streams that `tar` lists, extracts, and round-trips. The `split_ustar_name`
  field widths are corrected to `name` = 100 / `prefix` = 155 to match the ustar
  layout. See AGENTS.md.

## [0.5.0] - 2026-09-23

### Added

- **`contrast` CLI subcommand** (`pengy_theme::cli::cmd_contrast`): computes the
  WCAG 2.x relative contrast ratio of two `#rrggbb` colors and prints it along
  with an independent AA pass/fail verdict for the normal-text (4.5:1) and
  large-text (3.0:1) thresholds. The math (`contrast::contrast_ratio`) was done
  in 0.3.0; this wires it into a human-facing entry point. The `contrast`
  subcommand parses exactly two positional colors, echoes them in canonical
  lowercase `#rrggbb` form (so `1A2B3C` and `#1a2b3c` render identically),
  formats the ratio to two decimals, and exits `0` on success (`1` for a
  malformed color, `2` for a usage error).

## [0.4.0] - 2026-09-23

### Added

- **Deterministic packaging** (`pengy_theme::pack`): `pack` turns a parsed
  [`Theme`](src/manifest.rs) — together with the manifest file it came from and
  the asset files it references — into a deterministic POSIX *ustar* `<name>.tar`
  archive (fixed `mtime`/`uid`/`gid`/`uname`/`gname`/`mode`, entries sorted by
  path) plus a `manifest.sha256` sidecar (the sha256sum-compatible canonical file
  listing, whose digest is the SHA-256 over that listing). Two packs over the
  same inputs are byte-identical. The manifest is archived as the theme's
  canonical serialization, so a changed manifest changes the hash even when the
  on-disk file is untouched. The `pack` CLI subcommand is now implemented: it
  loads a manifest, packs it, prints the manifest file hash, and exits `0` on
  success (`1` on runtime error, `2` on usage error). The ustar writer is
  standard-library only; the `sha2`/`hex` crates only hash the bytes.

## [0.1.0] - 2026-09-22

### Added

- Crate scaffold for `pengy-theme`: a `pengy_theme` library plus the
  `pengy-theme` CLI binary, built from a single package.
- CLI dispatcher with `--help` / `--version`, a documented exit-code contract
  (`0` success, `1` runtime error, `2` usage error, `3` subcommand not yet
  implemented), and the `validate` / `pack` / `contrast` command surface
  (dispatcher wired; subcommands implemented by follow-up subtasks).
- GitHub-ready contract: MIT LICENSE (Copyright (c) 2026 Pat Wendorf), authored
  README, Keep-a-Changelog `CHANGELOG.md`, and `.gitignore`.
- Rust test suite: unit tests for the crate identity and CLI dispatcher, plus an
  integration test for the binary and the GitHub-ready contract
  (`tests/github_ready.rs`).
