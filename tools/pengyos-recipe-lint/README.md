# PengyOS recipe-lint

A linter for the Buildroot package recipes PengyOS authors. It parses
`.mk`/`.hash` recipe pairs and reports violations with a non-zero exit code so
broken recipes fail CI before they reach the build.

The four things it checks (see `spec.md`):

1. **Version pinned** — the recipe pins an explicit `_VERSION` (no `git` HEAD or
   auto version).
2. **Hash present** — a `.hash` file exists carrying a `sha256` for the source.
3. **License declared** — the recipe declares an `_LICENSE`.
4. **No unverified downloads** — the download site (`_SITE`) is a pinned,
   verifiable `https` mirror (see `spec.md` for the full rule set). An `http://`
   site, a non-`https` scheme, or an absent/relative site is flagged.

## Install

```sh
pip install -e .            # provides the `pengyos-recipe-lint` command
# or run without installing:
python -m pengyos_recipe_lint --help
```

Requires Python ≥ 3.11. Standard library only at runtime.

## Usage

```sh
# Lint every recipe under a directory tree. Exit code is non-zero if any
# violation is found.
pengyos-recipe-lint check path/to/recipes

# Emit a machine-readable report to OUT (JSON).
pengyos-recipe-lint check path/to/recipes --json report.json

# Print fix hints for a single recipe.
pengyos-recipe-lint fix-hints package.mk
```

Exit codes:

| Code | Meaning                                             |
|------|-----------------------------------------------------|
| `0`  | No violations (or `check` ran with nothing to flag) |
| `1`  | Violations found, or a runtime/usage error          |
| `2`  | Bad command-line arguments (argparse)               |

## Development

```sh
pip install -e ".[test]"   # or: pip install -e ".[test]" --break-system-packages
pytest -q                  # run the offline test suite
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [`LICENSE`](LICENSE).
