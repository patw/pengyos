# pengy-theme

Minimal, modern **theme & desktop-defaults packager/validator** for
[PengyOS](../../README.md).

`pengy-theme` is a small Rust library (`pengy_theme`) plus a CLI binary
(`pengy-theme`) that validates a theme manifest, checks contrast/consistency, and
emits installable artifacts + a hash. Its purpose (PengyOS SPEC §2.3 / Principle
8) is to make human artistry — themes, keybindings, desktop defaults —
**reproducible**: a manifest is validated against a schema, checked for
accessible foreground/background contrast, and packaged into a deterministic
archive bound by a content hash.

---

## Why

PengyOS rebuilds the same system from source and checks the result. A theme is
part of that system: its colors, fonts, wallpapers, keybindings, and defaults
must build identically every time. Trust comes from **verification, not
authority** — every packaged theme carries a manifest hash over its content, so a
downstream verifier can trust what it can't re-derive. `pengy-theme` is that
packager and validator.

## Features

- **Manifest validation** — required tokens are present, colors are well-formed,
  and key foreground/background pairs meet WCAG contrast ratios.
- **WCAG contrast math** — relative contrast ratio for a `fg`/`bg` color pair,
  exposed both as a library function and a `contrast` CLI helper.
- **Deterministic packaging** — `pack` emits an archive + manifest hash such that
  two runs over the same inputs produce byte-identical output.
- **Dependency-light** — the scaffold builds with the standard library alone
  (no network, no root). Per-subtask dependencies (a TOML parser, a SHA-256) are
  added and declared in [`Cargo.toml`](Cargo.toml) as the tasks require them.

> The theme surface (`validate`, `pack`, `contrast`) is layered in by follow-up
> subtasks. All three are implemented. This crate also implements the crate, the
> CLI dispatcher, the theme manifest schema (`pengy_theme::manifest`), and the
> GitHub-ready contract.

## Theme manifest schema

A theme is described by a `theme.toml` manifest, loaded into a [`Theme`](src/manifest.rs)
via `pengy_theme::manifest::load_manifest` (or `Theme::from_toml`). The schema
covers the full theme surface:

| Field | Type | Notes |
| ----- | ---- | ----- |
| `name` | `String` | required |
| `version` | `String` | required |
| `description` | `Option<String>` | optional |
| `colors` | `Palette` | required — `foreground`, `background`, `accent` + optional `warning`/`error`/`success` |
| `fonts` | `Fonts` | required — `mono`, `sans`, optional `serif`, `base_size`, optional `mono_size` |
| `wallpapers` | `Vec<Wallpaper>` | optional — `name`, `file`, optional `caption` |
| `keybindings` | `Vec<Keybinding>` | optional — `key`, `action` |
| `defaults` | `Defaults` | required — optional `session`, `cursor_theme`, `icon_theme`, `color_scheme` |

Colors are 8-bit sRGB and parse/serialize as lowercase `#rrggbb` hex
(`Color::from_hex`, `Color::to_hex`). A missing required field or a malformed
color is reported as a [`ManifestError`](src/manifest.rs) / [`ColorError`](src/manifest.rs)
rather than a panic.

## Install

This crate builds with a standard Rust toolchain (no network, no root):

```console
$ cargo build --release --offline
$ cargo install --path .
```

The binary is installed as `pengy-theme`.

## Usage

```
pengy-theme [COMMAND] [OPTIONS]

COMMANDS:
    validate  Validate a theme manifest (required tokens, valid colors,
              WCAG contrast ratios for key pairs)
    pack      Produce a deterministic archive + manifest hash from a manifest
    contrast  Compute the WCAG relative contrast ratio of two colors

GLOBAL OPTIONS:
    -h, --help      Print help
    -V, --version   Print version
```

### validate

Validate a theme manifest. Reports missing required tokens, malformed colors, and
any key foreground/background pair that fails its WCAG AA contrast threshold
(`4.5:1` for normal text). The `accent` role is checked too, but a low-contrast
accent is a soft warning that does not fail validation; a low-contrast required
pair is a hard error. Prints the report and exits `0` when valid, `1` when it has
hard errors, `2` for a usage error.

```console
$ pengy-theme validate theme.toml
OK: all checks passed
$ echo $?
0
```

A failing manifest lists every hard issue:

```console
$ pengy-theme validate low-contrast.toml
[error] colors.foreground — contrast 1.14:1 against background is below WCAG AA normal (4.5:1)
$ echo $?
1
```

### pack

Produce a deterministic archive + manifest hash from a manifest (and its
referenced assets). Output is byte-identical across runs over the same inputs.

```console
$ pengy-theme pack theme.toml --out dist/
```

### contrast

Compute the WCAG relative contrast ratio of two `#rrggbb` colors. Colors are
echoed in canonical lowercase hex, the ratio is shown to two decimals, and each
AA threshold gets an independent pass/fail verdict.

```console
$ pengy-theme contrast "#1a1a1a" "#f5f5f5"
#1a1a1a on #f5f5f5: contrast 15.96:1 — AA normal (4.5): pass; AA large (3.0): pass
$ echo $?
0
```

A malformed color is a runtime error (exit `1`); not exactly two color arguments
is a usage error (exit `2`).

## Exit codes

| Code | Meaning                                             |
| ---- | --------------------------------------------------- |
| `0`  | Success (including `--help` / `--version`).         |
| `1`  | Generic runtime error (I/O, bad manifest, contrast).|
| `2`  | Usage error (bad or missing arguments).             |
| `3`  | Subcommand not yet implemented.                     |

## Development

Run the test suite (no network, no root):

```console
$ cargo test
```

The suite covers library units, the CLI dispatcher, and the GitHub-ready contract
(`tests/github_ready.rs`). Real integration tests are opt-in behind
`PENGYOS_IT=1`.

## License

This project is licensed under the [MIT license](LICENSE),
**Copyright (c) 2026 Pat Wendorf**.
