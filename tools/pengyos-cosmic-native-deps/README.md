# pengyos-cosmic-native-deps

Buildroot recipes for the native (non-Rust) dependencies COSMIC needs that
**2026.08 does not ship** — the closing BR2_EXTERNAL glue for the PengyOS
desktop.

This is part of the [PengyOS](../../README.md) project. COSMIC, its desktop,
needs a handful of native dependencies that the pinned Buildroot `2026.08`
release does not provide: a login daemon (`greetd`), a desktop portal
(`xdg-desktop-portal` + the `cosmic` backend), seat management (`seatd`/
`libseat`), and its fonts (Fira + Noto). `pengyos-cosmic-native-deps` authors a
pinned, hashed BR2_EXTERNAL recipe for each missing one, and emits a
`cosmic-native.fragment` that enables the corresponding kconfig symbols. The
rendered recipes are committed to this repo — the repo root *is* the BR2_EXTERNAL
tree, so `package/<dep>/{Config.in,<dep>.mk,<dep>.hash}` and
`fragments/cosmic-native.fragment` are the versioned artifact the PengyOS build
consumes. A gate test (`test_committed_recipes_match_the_emitted_output`) fails
the build if the committed recipes ever drift from a fresh `emit` run.

It **never builds** anything and **never touches the network** at test time: the
recipe catalogue, emit templates, and linter are all exercised against committed
fixtures and in-memory data.

## What it does

Three subcommands (the interface from `spec.md`):

```bash
pengyos-cosmic-native-deps list [--json]
pengyos-cosmic-native-deps classify [--json] [--symbols PATH]
pengyos-cosmic-native-deps recipes [--json]
pengyos-cosmic-native-deps verify [--names NAME ...] [--json OUT]
pengyos-cosmic-native-deps emit --out DIR [--json]
pengyos-cosmic-native-deps lint --dir DIR [--json]
```

* `list` enumerates the native dependencies COSMIC may need, each tagged as
  **shipped** (2026.08 already provides it — no recipe) or **recipe-needed**
  (2026.08 does not — a recipe is required), with the kconfig symbol(s) it
  enables. The status is the classification against the vendored 2026.08 symbol
  list.
* `classify [--json] [--symbols PATH]` classifies every candidate against the
  pinned 2026.08 symbol list and prints the per-candidate status and reason.
  `--symbols` overrides the list to classify against (default: the committed
  `data/buildroot-2026.08-symbols.txt`). The machine view is the committed
  `data/classification.json`.
* `recipes [--json]` shows the authored recipe catalogue (`data/recipes.json`):
  for every dependency that needs a recipe, its pinned version, upstream URL,
  build system, license, source filename, and sha256. The machine view is the
  committed `data/recipes.json`.
* `verify [--names NAME ...] [--json OUT]` re-downloads each buildable recipe's
  upstream tarball (needs network) and re-checks its pinned sha256 against the
  digest in `data/recipes.json`. It exits non-zero if any recipe is
  unverified (hash mismatch, or the endpoint answered with a non-gzip challenge
  page rather than a tarball) — this is the "source + hash" rung of the
  verification ladder, and it never compiles.
* `emit --out DIR` writes the BR2_EXTERNAL recipes
  (`package/<dep>/{Config.in,<dep>.mk,<dep>.hash}`) and
  `fragments/cosmic-native.fragment` into `DIR`, mirroring the existing
  BR2_EXTERNAL recipe style.
* `lint --dir DIR` checks every recipe: a hash must be present, the version
  pinned, the license declared, and the build-system infra correct. It exits
  non-zero on any violation so it can gate CI.

Only the recipe authoring is in scope; the `cosmic-*` Rust components are a
sibling task.

## Install

```bash
pip install -e ".[dev]"
```

This exposes the `pengyos-cosmic-native-deps` console script. The runtime
depends only on the Python standard library; `pytest` is a dev-only dependency.

## Usage

```bash
# 1. Enumerate the COSMIC native deps (no side effects).
pengyos-cosmic-native-deps list
#    ...or emit the machine view:
pengyos-cosmic-native-deps list --json

# 1b. Classify each dep against the pinned 2026.08 symbol list.
pengyos-cosmic-native-deps classify
#    ...or the machine view (identical to data/classification.json):
pengyos-cosmic-native-deps classify --json

# 1c. Inspect the authored recipe catalogue (version, URL, license, hash).
pengyos-cosmic-native-deps recipes
#    ...or the machine view (identical to data/recipes.json):
pengyos-cosmic-native-deps recipes --json

# 1d. Re-check each buildable recipe's pinned sha256 vs its upstream tarball
#     (needs network; greetd/xdg-desktop-portal download from their GitHub
#     mirrors — their GitLab archives are Cloudflare-challenged).
pengyos-cosmic-native-deps verify
#    ...or just one recipe, as a JSON report:
pengyos-cosmic-native-deps verify --names cosmic-portal --json /tmp/verify.json

# 2. Write the recipes + fragment into a BR2_EXTERNAL tree.
pengyos-cosmic-native-deps emit --out <BR2_EXTERNAL>

# 3. Lint a directory of recipes (non-zero exit on any violation).
pengyos-cosmic-native-deps lint --dir <BR2_EXTERNAL>/package
```

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Integration tests (against a real Buildroot checkout) are opt-in behind
`PENGYOS_IT=1`; the unit suite needs no network, root, display, or hardware.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
