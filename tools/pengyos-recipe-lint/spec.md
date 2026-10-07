# PengyOS recipe-lint — linter for our Buildroot recipes

## Summary
Lint Buildroot package recipes we author: hash present, license declared, version pinned, no unverified downloads.

## Purpose
Keep our recipes (uutils, dbus-broker, signify, pengy…) honest and reproducible.

## Deliverable
A Python CLI `pengyos-recipe-lint` that parses `.mk`/`.hash` recipe pairs and reports violations with a non-zero exit.

## Interface
- pengyos-recipe-lint check <dir> [--json OUT]
- pengyos-recipe-lint fix-hints <recipe.mk>

## Verification (tests are first-class)
- a compliant recipe → no violations.
- missing .hash / unpinned version / missing license → each flagged with location.
- non-zero exit when violations exist.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Rules

Each rule evaluates one recipe and reports pass/fail with a `file:line`
location and a fix hint (the `report` layer renders these and drives the exit
code). All four rules are implemented.

- **version_pinned** — `_VERSION` is present, non-empty, and a concrete release
  pin. It fails when `_VERSION` is absent, empty, a branch/alias name
  (`git`, `HEAD`, `auto`, `master`, `main`, `latest`, `trunk`, `stable`,
  `snapshot`, `current`), or a build-time make expansion (`$(...)`).
- **hash_present** — a `.hash` sibling exists and carries at least one `sha256`
  entry whose filename matches the recipe's `_SOURCE` (when `_SOURCE` is set).
  It fails when the `.hash` sibling is missing, has no `sha256` entry, or its
  `sha256` entries do not cover the source.
- **license_declared** — `_LICENSE` is present and non-empty.
- **no_unverified_downloads** — the recipe's download site (`_SITE`) resolves to
  a *pinned, verifiable* origin. It fails when `_SITE` is absent/empty, is not an
  absolute URL, uses an insecure `http://` scheme, or uses a scheme other than
  `https`. It passes only for an absolute `https://` site.

  "Verifiable" is a static, offline property here: `https` pins the download to
  the site's TLS-authenticated origin, so the source cannot be silently swapped
  by a man-in-the-middle. We deliberately do **not** hard-code an allow-list of
  hostnames (that would require network access and be brittle); any absolute
  `https` mirror is accepted. Recipes that fetch over a non-HTTP transport
  (e.g. a git `_SITE`) are out of scope for this linter — see the non-goals.

## Acceptance criteria
- Actionable output; stable ordering.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Full Makefile parsing

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
