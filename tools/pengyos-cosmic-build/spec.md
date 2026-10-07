# PengyOS cosmic-build — vendored, buildable COSMIC recipes + Desktop target wiring

## Summary
Close the gap between the COSMIC recipes' placeholder 'shapes' and a real build: drive the vendoring engine per component (from the pinned sources + each component's Cargo.lock) to obtain a real source tarball, its sha256, and a real SITE; emit recipes that pin those; align the emitted BR2 symbols with the desktop defconfig; install the recipes + the desktop defconfig + board overlay + native-dep recipes into a BR2_EXTERNAL tree, wire Config.in; register the target; and lint that the tree is buildable-shaped.

## Purpose
pengyos-cosmic-pkg/-recipes proved the engine, but the recipes lane stops at placeholders (SITE=file://<VENDORED_SITE>, hash=0x64) and uses a different symbol prefix than the defconfig — so nothing the Desktop target references actually resolves and no COSMIC image can build. This is the missing wiring between the engine and a real Buildroot build.

## Deliverable
A Python package `pengyos_cosmic_build`: `vendor-all` (per component: locate the checked-out sources + its Cargo.lock, run the pkg `vendor` engine with the git-source mirror, capture the tarball, its sha256 and a real SITE), `emit` (recipes pinning the real digest + SITE, symbols matching the desktop defconfig convention), `wire` (install recipes + defconfig + board overlay + native deps into a BR2_EXTERNAL tree; register the target in targets.toml), and `lint` (no placeholder SITE/hash remains; every BR2_PACKAGE_* the defconfig references resolves to a recipe; the target is registered; the native deps are present). Tested against committed fixtures / a fake vendor engine — the real sources + build are operator steps.

## Interface
- pengyos-cosmic-build vendor-all --manifest cosmic.toml --sources DIR --dl DIR --out vendor/ [--json]
- pengyos-cosmic-build emit --manifest cosmic.toml --vendor vendor/ --out package/ [--group core|login|shell|apps]
- pengyos-cosmic-build wire --repo BR2_EXTERNAL_DIR [--json]
- pengyos-cosmic-build lint --repo BR2_EXTERNAL_DIR [--json]

## Verification (tests are first-class)
- vendor-all records a real sha256 + SITE per component (fake engine); a missing Cargo.lock is reported.
- emit never writes the placeholder SITE/hash; symbols match the defconfig prefix.
- `lint` fails on a placeholder SITE/hash, an unresolved defconfig symbol
  (`BR2_PACKAGE_COSMIC_PORTAL` — the Desktop defconfig references it, but the
  `cosmic-portal` recipe enables `BR2_PACKAGE_XDG_DESKTOP_PORTAL_BACKEND_COSMIC`),
  a missing native dep the defconfig references, and an unregistered target (one
  mutation each).
- wire is idempotent.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- After `emit`+`wire`, `lint` is clean and every symbol the desktop defconfig references resolves to an emitted recipe.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Fetching COSMIC sources or running the multi-hour build (operator)
- The desktop defconfig/board overlay content itself (pengyos-desktop-defconfig owns it)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
