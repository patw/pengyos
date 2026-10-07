# PengyOS cosmic-pkg

The COSMIC packaging engine: turn a git Rust component (a component manifest —
URL + pinned tag + `Cargo.lock`) into a Buildroot cargo package with a
vendored, git-dependency-aware source tree.

COSMIC's workspace pulls crates from git (`libcosmic`, `smithay`, …) that
Buildroot's crates.io-shaped infra does not vendor directly. This engine turns
each component into **data**, so the ~20-crate COSMIC stack can be packaged from
source without hand-writing each recipe.

## What it does

Three subcommands, each delegated to a focused module:

| Subcommand | Module | Purpose |
|------------|--------|---------|
| `vendor`   | `pengyos_cosmic_pkg.vendor`   | Deterministic vendored source tree from a manifest + `Cargo.lock`, rewriting `git = "..."` sources to the vendored path and emitting the source `.hash`. |
| `recipe`   | `pengyos_cosmic_pkg.recipe`   | Emit `package/<pkg>/{Config.in,<pkg>.mk,<pkg>.hash}` in Buildroot cargo form, with version + license pinned. |
| `lint`     | `pengyos_cosmic_pkg.lint`     | Reject an unpinned version, a missing `.hash`, an undeclared license, and a floating git branch/ref. |

## Install

```sh
pip install -e .            # provides the `pengyos-cosmic-pkg` command
# or run without installing:
python -m pengyos_cosmic_pkg --help
```

Requires Python ≥ 3.11. Standard library only at runtime.

## Usage

```sh
# Vendor a component's source tree (rewrites git deps to the vendored path).
pengyos-cosmic-pkg vendor --manifest m.toml --cargo-lock Cargo.lock --out DIR

# Emit a Buildroot cargo package. The recipe pins the sha256 of the vendored
# tarball the `vendor` run produced, so pass that run's `<pkg>.hash` back in.
pengyos-cosmic-pkg recipe --manifest m.toml --out package \
    --hash-file DIR/cosmic-text.hash
```

`recipe` requires `--hash-file` (the `<pkg>.hash` the `vendor` run emits) so the
emitted recipe pins the exact digest of the vendored tarball it names. `--site`
overrides where Buildroot finds that tarball (defaults to the placeholder
`file://<VENDORED_SITE>`). Add `--json` to any subcommand for a machine-readable
summary.

# Lint a package/ directory: require a present .hash, a pinned version, a
# declared license, and no floating git branch/ref (exit 1 with the violations
# on stderr when any is violated).
pengyos-cosmic-pkg lint --dir package/cosmic-demo
```

A ready-to-read sample manifest lives at [`examples/cosmic-text.toml`](examples/cosmic-text.toml)
(it documents the manifest schema by example). The manifest schema itself is
defined and validated by `pengyos_cosmic_pkg.manifest`.

Exit codes:

| Code | Meaning                                             |
|------|-----------------------------------------------------|
| `0`  | Success                                             |
| `1`  | A violation / unimplemented feature / runtime error |
| `2`  | Bad command-line arguments (argparse)               |

## Development

```sh
pip install -e ".[test]"   # or: pip install -e ".[test]" --break-system-packages
pytest -q                  # run the offline test suite
```

Tests need no network, root, real display, or hardware — they run against
committed fixtures under `tests/fixtures/` (see `tests/fixtures/README.md`).
Real system integration is opt-in behind `PENGYOS_IT=1`.

## Docs

- `spec.md` — the full specification.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [`LICENSE`](LICENSE).
