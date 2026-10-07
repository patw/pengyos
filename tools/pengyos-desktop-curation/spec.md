# PengyOS desktop-curation — the curated default-app set as declarative data (US-DSK-2/5)

## Summary
The curated Desktop default-application set as declarative data → a Flatpak ref list + a self-hosted-mirror manifest + a .desktop/mime integration plan, validated against US-DSK-2's 'everyday apps covered' list — human-ratified, never AI-decided (Principle 8).

## Purpose
USE-CASES US-DSK-2 (Flatpak primary lane + curated defaults) and US-DSK-5 (human taste): the default app set is a curated, declarative, reproducible decision, and the everyday-coverage list is a testable acceptance criterion.

## Deliverable
A Python tool `pengyos-desktop-curation` that stores the curated set as data (category -> app -> lane (flatpak/appimage/recipe) -> ref -> rationale -> human_signed) and emits a flatpak ref list, a mirror manifest (for pengyos-flatpak-mirror), and a coverage report against the US-DSK-2 everyday list (browser, terminal, files, editor, media, office, PDF, archive, calculator, screenshots).

## Interface
- pengyos-desktop-curation list [--json]
- pengyos-desktop-curation coverage [--json]
- pengyos-desktop-curation emit --out DIR [--json]
- pengyos-desktop-curation emit --out DIR --pin [--refresh] [--json]
- pengyos-desktop-curation emit --out DIR --mime [--json] — also emit the
  .desktop/mime integration plan: a `mimeapps.list` (the freedesktop
  `xdg-mime` default-associations file) and the machine-readable
  `integration-plan.json`, alongside the Flatpak ref list and mirror manifest.
- pengyos-desktop-curation lint [--json] — enforce the human-artistry rule
  (every curated entry has a rationale and is `human_signed`); clean passes
  (exit 0), any violation fails (exit 1).

## Verification (tests are first-class)
- coverage flags a missing everyday category and passes a complete set.
- emit is deterministic; every entry carries a lane + rationale.
- emit --pin resolves the UNPINNED commit placeholder to a real Flathub commit,
  yielding a manifest the mirror tool accepts; a ref with no known commit fails.
- emit --refresh fetches every curated ref's current Flathub commit live, merges
  those into the committed `flathub-commits.toml`, and persists it back (the
  committed database self-updates with real commits); the merge is additive
  (non-curated refs preserved) and a ref whose fetched commit is not a real
  64-hex commit fails loudly (exit 1) before anything is written.
- emit --mime writes a valid `mimeapps.list` (parseable INI with
  `[Default Applications]` + `[Added Applications]` sections) and a valid
  `integration-plan.json`; the plan covers every everyday category and its
  per-category app/ref match the curated set.
- an entry without a rationale, or with `human_signed != true`, fails lint (exit 1); a clean set passes (exit 0). The loader validates *structure*; `lint` validates the *artistry rule* (the loader does not reject an unsigned entry — `lint` catches it).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Emits a complete, deterministic coverage report.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Installing or mirroring apps (pengyos-flatpak-mirror does)
- Choosing the app set unaided (human artistry, Principle 8)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
