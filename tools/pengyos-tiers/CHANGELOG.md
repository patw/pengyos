# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `tests/test_repo.py::test_github_ready`: the unified GitHub-ready contract
  assertion (spec.md "Verification"). It verifies README.md (authored, links
  the license), LICENSE (MIT + the exact "Copyright (c) 2026 Pat Wendorf"
  line), CHANGELOG.md (authored with the 0.1.0 section), and .gitignore, then
  runs the full test suite in a fresh subprocess and asserts it is green. The
  sub-run deselects `test_github_ready` itself (via `-k`) to avoid infinite
  recursion; a non-zero return code (any failure, skip, or collection error)
  fails the check with a tail of the pytest output for debuggability. The
  pre-existing per-file repo checks remain as focused unit tests.
- `classify` / `check` CLI subcommands (pengyos_tiers.cli), wiring the spec
  interface. `classify --action <a> --path <p> [--target <t>] [--rules <f>]
  [--json]` classifies a change given on the command line and prints the tier +
  approvals (informational: exits 0 on success). `check <change.json> [--rules
  <f>] [--json]` classifies a change described in a JSON file (or `-` for
  stdin) and **gates**: it exits with a distinct code (3) when the resolved
  tier needs a human signature (`needs_human`), so it can gate a pipeline, and
  exit 1 on any error (bad schema, unreadable/invalid rules file). Both default
  to the shipped `tiers.toml`; `--rules` selects another. Exit codes: 0 =
  success (and, for `check`, not needs-human), 1 = error, 3 = `check` gate
  tripped on a human-sign tier.
- Change schema (`pengyos_tiers.change.Change`): an immutable, validated
  `{action, path, target?}` model with strict `from_dict` validation (action
  lowercase-identifier, path non-empty no-whitespace, target optional).
- Declarative rules file (`pengyos_tiers.rules.Rule` / `RulesFile`): a TOML
  `[[rule]]` array where each rule maps glob `path` patterns to a `tier`
  (0/1/2), optionally narrowed by `action`/`target` filters; `RulesFile.load`
  parses and validates from disk, with a `deny_tier` default (2).
- `tiers.toml` default ruleset at the repo root: 9 rules covering SPEC's
  examples — `/etc` config → T1, bootloader entries/cmdline → T1, bootloader
  binaries/keys and kernel images/modules → T2, crypto/identity and the
  update trust root → T2, everything else default-denied to T2. Critical
  rules are listed first so first-match precedence keeps them from being
  absorbed by the broad `/etc/*` (T1) rule; a non-critical `/etc/pengyos/*`
  path still falls through to T1.
- Tests for the change schema and rules-file schema/validation.
- Classifier (`pengyos_tiers.classifier.classify`): first-match walk over a
  `RulesFile`, explicit default-deny to Tier 2 (fail safe) when no rule
  matches; `Classification` carries `tier`, `matched`, `rule_name`,
  `needs_human`, and a human `reason`. `normalize_path` lexically collapses
  `//`, `.`, and `..` before matching. The glob→regex engine
  (`_glob_to_regex`) lives in `rules.py` and is the single matcher shared by
  `Rule.matches` and the classifier (`*` crosses `/`, `?` = one char, `[seq]`
  class), so the two can never diverge.
- Tests for the classifier (first-match precedence, action/target filters,
  default-deny, glob semantics, path normalization, `needs_human`).
- Tests for the committed default ruleset (`tests/test_ruleset.py`): pins the
  SPEC Verification examples (/etc → T1, bootloader binary/keys → T2, kernel
  → T2, unknown → T2 default-deny) and the precedence/specificity behaviour
  (a critical `/etc/pengyos/*` target is T2 even though `/etc/*` is broadly
  T1).
- Required approvals per tier (`pengyos_tiers.approvals`): `approvals_for(tier)`
  maps Tier 0 → `witness` (verified/measured build), Tier 1 → `agent-auto`,
  Tier 2 → `human-sign`; unknown tiers fail safe to `human-sign`. `Approval`
  is a `str`-enum so it serialises as its value. `Classification.approvals`
  exposes the mapping for its tier and `to_dict()` includes `approvals`.

## [0.1.0] - 2026-09-15

### Added
- Initial repo scaffold: `pyproject.toml` (setuptools, stdlib-only runtime,
  `pengyos-tiers` console script), `pengyos_tiers/` package with an argparse
  CLI (`--version`, `version` subcommand) and `__main__`.
- pytest harness (no network, no root; integration tests opt-in via
  `PENGYOS_IT=1`).
- README, MIT LICENSE, CHANGELOG, .gitignore.
