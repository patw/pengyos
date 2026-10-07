# PengyOS cosmic-build

The **wiring lane** that closes the gap between the COSMIC recipes' placeholder
*'shapes'* and a real Buildroot build.

The `pengyos-cosmic-pkg` engine and the `pengyos-cosmic-recipes` lane proved the
engine, but the recipes lane stops at placeholders (`SITE=file://<VENDORED_SITE>`,
`hash=0x64`) and uses a different symbol prefix than the desktop defconfig — so
nothing the Desktop target references actually resolves and no COSMIC image can
build. This tool drives the vendoring engine per component to obtain a **real**
source tarball, its sha256, and a real SITE; emits recipes that pin those; and
wires the recipes + the desktop defconfig + board overlay + the sibling
native-dep recipes into a `BR2_EXTERNAL` tree, registers the `pengyos-desktop`
target, and lints the tree buildable-shaped.

## What it does

Four subcommands, each delegated to a focused module:

| Subcommand   | Module                         | Purpose |
|--------------|--------------------------------|---------|
| `vendor-all` | `pengyos_cosmic_build.vendor_all` | Per component: locate the checked-out sources + its `Cargo.lock`, run the pkg `vendor` engine with the git-source mirror, capture the tarball, its sha256 and a real SITE. |
| `emit`       | `pengyos_cosmic_build.emit`      | Emit recipes pinning the REAL digest + SITE (never the placeholder), with the defconfig's symbol prefix. |
| `wire`       | `pengyos_cosmic_build.wire`      | Install the emitted recipes + the desktop defconfig + board overlay + the sibling native-dep recipes into a `BR2_EXTERNAL` tree, wire `Config.in`, and register the `pengyos-desktop` target idempotently. |
| `lint`       | `pengyos_cosmic_build.lint`      | Reject a leftover placeholder SITE/hash, an unresolved `BR2_PACKAGE_*` referenced by the defconfig, a missing native dep, or an unregistered target. |

## Install

```sh
pip install -e .            # provides the `pengyos-cosmic-build` command
# or run without installing:
python -m pengyos_cosmic_build --help
```

Requires Python ≥ 3.11. Standard library only at runtime. The engine
(`pengyos-cosmic-pkg`) and the recipes/native-deps lanes are sibling tools
imported from their own `tools/` dirs (see `conftest.py`), never pip-installed.

## Usage

```sh
# Vendor every component's source (tarball + sha256 + real SITE).
pengyos-cosmic-build vendor-all --manifest cosmic.toml --sources DIR --dl DIR --out vendor/

# Emit recipes pinning the real digest + SITE, for one group.
pengyos-cosmic-build emit --manifest cosmic.toml --vendor vendor/ --out package/ --group core

# Wire the recipes + desktop defconfig + board overlay + native deps into a BR2_EXTERNAL tree.
pengyos-cosmic-build wire --repo <BR2_EXTERNAL_DIR> --sources DIR

# Lint the tree buildable-shaped.
pengyos-cosmic-build lint --repo <BR2_EXTERNAL_DIR>
```

Add `--json` to any subcommand for a machine-readable summary.

## Verification

- `vendor-all` records a real sha256 + SITE per component (fake engine); a
  missing `Cargo.lock` is reported, not a crash.
- `emit` never writes the placeholder SITE/hash; symbols match the defconfig
  prefix.
- `lint` fails on a placeholder SITE, an unresolved defconfig symbol, a missing
  native dep, and an unregistered target (one mutation each).
- `wire` is idempotent.

## Development

```sh
pip install -e ".[test]"   # or: pip install -e ".[test]" --break-system-packages
pytest -q                  # run the offline test suite
```

Tests need no network, root, real display, or hardware — they run against
committed fixtures under `tests/fixtures/`. Real system integration is opt-in
behind `PENGYOS_IT=1`.

## Docs

- `spec.md` — the full specification.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [`LICENSE`](LICENSE).
