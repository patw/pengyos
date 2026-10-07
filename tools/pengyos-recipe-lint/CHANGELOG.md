# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`no_unverified_downloads` rule (spec check #4)** — the fourth and final lint
  rule. It fails a recipe whose download site (`_SITE`) is absent/empty, not an
  absolute URL, uses an insecure `http://` scheme, or uses any non-`https`
  scheme (e.g. `git`, `ftp`, `file`); it passes only for an absolute `https`
  mirror. "Verifiable" is a *static, offline* property here: `https` pins the
  download to the site's TLS-authenticated origin, so a man-in-the-middle cannot
  silently swap the source. We deliberately do **not** hard-code an allow-list of
  hostnames (that would need network access and be brittle) — any absolute `https`
  mirror is accepted; non-HTTP transports (git) are out of scope for this linter.
  Implemented in `pengyos_recipe_lint.rules` as `no_unverified_downloads` plus a
  pure `site_is_verified()` helper (new `RuleId.NO_UNVERIFIED_DOWNLOADS`, appended
  to `RULE_ORDER` and `check_recipe`). Ships a new committed bad fixture
  `fixtures/bad/unverified_site.mk` (+ `.hash`) that fails **only** this rule,
  `test_fixtures.py`'s `EXPECTED_FAILURES` table, and 13 new tests in
  `test_rules.py` (rule + `site_is_verified` behaviour). This completes the four
  checks named in `spec.md`; the full suite is 128 tests (126 run + 2 opt-in
  integration skipped), all green.

- **`test_github_ready`** — asserts the `spec.md` GitHub-ready contract: the repo
  ships authored, non-empty `README.md`, `CHANGELOG.md`, `.gitignore` and a
  `LICENSE` that is MIT and credits "Copyright (c) 2026 Pat Wendorf", plus a
  `pyproject.toml` that wires the `pengyos-recipe-lint` console script. Also pins
  that the package imports and its CLI runs via `python -m pengyos_recipe_lint`
  from a clean checkout (no install). This is the first item in `implementation.md`
  that is a verification/meta task rather than a feature; the repo already met the
  contract from the scaffold, so this subtask added the assertion that locks it
  in. The next feature subtask (spec check #4, "no unverified downloads") follows.

### Added

- Tolerant `.mk` parser (`pengyos_recipe_lint.recipe_parser`): parses Buildroot
  recipe variable assignments (`=`, `:=`, `?=`, `+=`, `!=`; optional
  `override`/`export` prefixes), tolerating comments (full-line and inline),
  leading whitespace, quoted values, `include`/`ifeq`/`endef`/`define` noise,
  and GNU-Make continuation lines (backslash-newline → single space). Each
  assignment records its 1-based source line number. The linter's five variables
  (`_VERSION`, `_SITE`, `_SOURCE`, `_LICENSE`, `_LICENSE_FILES`) are matched by
  name suffix.

### Added

- Lint **rules** (`pengyos_recipe_lint.rules`): `version_pinned` (an explicit,
  non-auto `_VERSION` — flags absent/empty/branch-name/`$(...)` versions),
  `hash_present` (a `.hash` sibling with a `sha256` covering the source), and
  `license_declared` (a non-empty `_LICENSE`). Each rule returns a structured
  `RuleResult` (rule, ok, message, `line_no`, fix `hint`); `check_recipe` runs
  them in a stable order. The "no unverified downloads" rule (spec check #4) is
  deferred to a follow-up subtask.

### Added

- **Committed good/bad recipe fixtures** (`fixtures/good`, `fixtures/bad`,
  `fixtures/README.md`). `fixtures/good` holds three fully-compliant recipes
  (`uutils`, `dbus-broker`, a `PENGYOS_`-branded `signify`) that pass all three
  rules; `fixtures/bad` holds five recipes, each engineered to fail exactly one
  rule (`version_pinned`, `hash_present` x2, `license_declared`) plus one that
  fails all three. `test_fixtures.py` asserts every good recipe passes all rules
  and every bad recipe fails exactly its expected rule set (and that the on-disk
  files and the expected-failure table stay in sync, covering every rule). The
  `check`/`fix-hints` CLI runs end-to-end against these committed files, with the
  end-to-end checks gated behind `PENGYOS_IT=1`.

- **Report layer** (`pengyos_recipe_lint.report`): turns the structured
  `RuleResult` rows into actionable output. `lint_recipes` runs every rule
  against every discovered recipe and collects the failures, ordered by recipe
  path then rule order (stable regardless of filesystem walk order). Each
  `Violation` carries a `file:line` location (``file`` alone when the rule can
  not point at a line) and a fix `hint`; `Report.render_text()` groups them by
  recipe, and `Report.exit_code` is `1` when any violation is present. The
  `check` subcommand now gates CI — it prints the report (or `--json`) and
  exits non-zero on violations; `fix-hints` prints each recipe's violations
  with their `file:line` and hint and returns `1` when any are found.

## [0.1.0] — 2026-09-14

### Added

- Scaffold the repository: GitHub-ready layout (README, LICENSE (MIT),
  CHANGELOG, `.gitignore`, `pyproject.toml`).
- Standard-library-only Python package `pengyos_recipe_lint` with a working CLI
  (`check` and `fix-hints` subcommands; `check` supports `--json`).
- Recipe discovery primitive (`discover_recipes`) that walks a directory tree,
  collects `.mk` recipes, and resolves each one's `.hash` sibling.
- Offline test suite (discovery + CLI) covering discovery, CLI exit codes,
  JSON output shape, and `python -m` invocation.
