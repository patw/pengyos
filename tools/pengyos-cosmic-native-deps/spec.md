# PengyOS cosmic-native-deps — Buildroot recipes for COSMIC's non-Rust dependencies

## Summary
Buildroot recipes for the native (non-Rust) dependencies COSMIC needs that 2026.08 does not ship — greetd (login), xdg-desktop-portal (+ the cosmic backend), libdisplay-info, seatd/libseat, and the Fira/Noto fonts — each pinned + hashed, with a recipe lint.

## Purpose
SPEC 2.3: COSMIC needs a login daemon (greetd), a portal (xdg-desktop-portal for sandbox/screencast), libdisplay-info (cosmic-comp EDID), seat management, and its fonts — none in 2026.08. These are standard Buildroot-style (autotools/meson) packages, distinct from the cargo cosmic-* ones.

> **Reality note (discovered 2026-09-21):** the vendored 2026.08 kconfig symbol
> list (`data/buildroot-2026.08-symbols.txt`, tag 2026.08) **does** contain
> `BR2_PACKAGE_LIBDISPLAY_INFO` and `BR2_PACKAGE_SEATD`. Per the classification
> rule below ("skip anything 2026.08 already ships"), those two are **skipped**
> and get no recipe; only greetd, xdg-desktop-portal, the cosmic portal, and the
> Fira/Noto fonts are "recipe needed". The spec's "none in 2026.08" is the
> intent; the classification rule is the source of truth and handles the
> discrepancy.
>
> **Multi-symbol rule (discovered 2026-09-21):** a candidate is "shipped" only
> when **every** symbol it enables is present in 2026.08; it is "recipe needed"
> if **any** symbol it enables is absent. This matters for the `fonts`
> candidate, which enables ten font-family symbols — if 2026.08 shipped nine of
> them, PengyOS would still need a recipe for the missing family. (In reality all
> ten are absent, so `fonts` is "recipe needed" either way; the rule is recorded
> so a future bump does not silently pick the opposite interpretation.)

## Deliverable
A Python package `pengyos_cosmic_native_deps` that emits + lints our BR2_EXTERNAL recipes for greetd, xdg-desktop-portal, libdisplay-info, seatd, and the fonts, plus fragments/cosmic-native.fragment enabling the symbols — each verified against the vendored 2026.08 symbol list (skip anything 2026.08 already ships).

## Interface
- pengyos-cosmic-native-deps list [--json]
- pengyos-cosmic-native-deps emit --out DIR [--json]
- pengyos-cosmic-native-deps lint --dir DIR [--json]

## Verification (tests are first-class)
- classification: a dep 2026.08 ships is skipped, a missing one yields a recipe.
- emit is deterministic + golden per dep.
- lint rejects a missing hash / unpinned version / undeclared license.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Every missing native dep has a deterministic, lint-clean recipe.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Building the packages
- The cosmic-* Rust components (a sibling task)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
