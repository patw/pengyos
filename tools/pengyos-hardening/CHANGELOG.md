# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.4] — 2026-09-25

### Added

- **The GitHub-ready gate now asserts the full package surface.**
  `test_docs_in_sync` previously required only the test files, the package entry
  points (`pengyos_hardening`, `cli.py`), and the GitHub-ready deliverables to
  appear in the README's file tree. It now also requires every shipped source
  file under `pengyos_hardening/` (`baseline.py`, `errors.py`, `data/baseline.json`,
  the `check/` readers, …) to be named — a shipped module that is never mapped is
  drift. The check is robust to the README's nested tree: a file matches by its
  parent-joined path (`check/readers.py`) **or** its bare basename (`readers.py`,
  since the tree nests the `check/` readers under `check/`). `__init__.py` files
  and `__pycache__` artefacts are excluded (a package's `__init__` is implied by
  the directory; the tree omits the nested `check/__init__.py` by convention).
- `test_docs_package_surface_detects_missing_doc` — the regression that proves
  the new check actually catches a missing doc: it strips a shipped package
  filename from the README tree and asserts the naming logic rejects it (both the
  basename and parent-joined path forms).

### Changed

- Bumped the package version to `0.7.4` (package-surface docs gate). 176
  tests green.

## [0.7.3] — 2026-09-25

### Added

- The **GitHub-ready gate** (`tests/test_github_ready.py`) now enforces the two
  parts of spec.md's contract that a presence check alone could not:
  * **Tracking, not just presence.** `test_required_files_tracked` asserts the
    GitHub-ready files (`README.md`, `LICENSE`, `CHANGELOG.md`, `.gitignore`)
    are *tracked in the git index* (`git ls-files --error-unmatch`), not merely
    present on disk. A deliverable left un-`git add`-ed would pass a presence
    check but never reach GitHub; this closes that gap. A new helper
    (`_is_tracked`, plus `_git_root`) makes the assertion, and
    `test_is_tracked_detects_untracked_file` is the regression that proves the
    gate actually catches an untracked file (staged → tracked, fresh → not).
  * **Docs in sync against the full deliverable set.** `test_docs_in_sync` now
    requires every GitHub-ready deliverable (and `pyproject.toml`) to appear in
    the README's file tree, not just the test files and package entry points —
    a deliverable omitted from the map is now drift.
  * The README's file tree was corrected to list the previously-missing
    `baseline.py` and `errors.py` module files.

### Changed

- Bumped the package version to `0.7.3` (hardened GitHub-ready gate). 175
  tests green.

## [0.7.2] — 2026-09-25

### Added

- **Grounding against the committed fixtures** (`tests/test_grounding.py`), the
  spec.md acceptance criterion expressed as a single test group that is *driven
  by the committed baseline* rather than a hardcoded rule list. It enumerates
  `load_baseline().ids()` and asserts, for every rule the baseline declares:
  * a single-rule mutation fixture exists under
    `tests/fixtures/mutations/<rule_id>`;
  * auditing that tree (unfiltered) yields exactly one finding, and it is the
    rule the mutation targets;
  * the hardened fixture tree audits clean (zero findings, zero skipped), and
    renders as a clean report (`OK — no deviations` / JSON `finding_count == 0`).
  This closes the drift gap the per-domain tests left open: a rule added to
  `data/baseline.json` without a matching mutation fixture now fails here
  (`test_every_rule_id_has_a_committed_mutation_directory`,
  `test_mutation_count_matches_rule_count`). Every mutation finding carries a
  path; the only matcher that legitimately reports no source line is
  `unit_enabled` (a systemd `.wants` existence check).

### Changed

- Bumped the package version to `0.7.2` (new test group). 170 tests green.

## [0.7.1] — 2026-09-25

### Fixed

- The `report` submodule's public contract is now accurate. The README claimed
  `check` emits findings "sorted by rule id then path", but the auditor sorts by
  **severity, then path, then rule id** (critical deviations surface first — the
  right order for triaging a security audit). The README now describes the
  severity-first order, and `tests/test_report.py` gains two tests that pin the
  *emitted* order of `render_text` / `render_json` directly against
  `sorted_findings()` (feeding the findings out of arrival order and asserting
  the report re-orders them), closing the doc/impl drift.

## [0.7.0] — 2026-09-25

### Added

- The **defconfig reader** for the `check` submodule, backing the three
  `defconfig` rules (spec.md deliverable: "no debug/telnet surfaces" + the
  OpenSSH-server allow-list):
  * `readers.py` — `parse_defconfig` / `Defconfig`: a Buildroot defconfig
    parser that records only the *set* symbols (`BR2_FOO=y`), with each set
    symbol mapped to its 1-based source line. The whole ballgame is the
    Buildroot quirk: a *disabled* symbol is written
    `# CONFIG_BR2_FOO is not set` (a comment, WITH the `CONFIG_` prefix), so
    the parser matches only real `BR2_…=y` set-lines and never mistakes a
    disabled symbol for a set one. Symbol names may contain lowercase letters
    (e.g. the `BR2_x86_64` architecture symbol); a value bound to anything but
    `y` (`BR2_FOO="str"`, `BR2_FOO=(choice)`) is not a set symbol.
  * `matchers.py` — `SymbolAbsentMatcher` (backs `symbol_absent`; the symbol
    must NOT be set in any `configs/*_defconfig`, evidence is the offending
    file:line) and `SymbolPresentMatcher` (backs `symbol_present`; the symbol
    must be set in *every* defconfig, evidence is the first that omits it).
    Both resolve their `files` glob against the tree root (the defconfigs live
    at `configs/*_defconfig`, not under an overlay), and both register in
    `IMPLEMENTED_KINDS`.
  * `audit.py` — a `SYMBOL_KINDS` registry; `_bases_for()` resolves symbol
    kinds against each external tree root.
- Three single-rule mutation fixtures under `tests/fixtures/mutations/` —
  `defconfig-no-telnet` (telnet set to `y`), `defconfig-no-debug` (valgrind
  set to `y`), and `defconfig-openssh-server` (OpenSSH server disabled) — each
  a full copy of the hardened tree with exactly one line flipped so that rule
  fires (and only it).

### Changed

- The auditor now evaluates all 3 defconfig rules; the hardened tree audits
  clean with **zero skipped** (down from 3). The golden report counts in
  `tests/test_report.py` and the skip-count assertions in `tests/test_check.py`
  were updated accordingly; the now-unreachable skipped-list layout is pinned
  against a synthetic result.
- Bumped the package version to `0.7.0` (new feature). Fixed a pre-existing
  version skew (`__init__.py`/`pyproject.toml` disagreed at 0.5.0/0.6.0).

## [0.6.0] — 2026-09-25

### Added

- The **firewall reader** for the `check` submodule, backing the two firewall
  rules (spec.md deliverable: "a default-deny firewall that is actually
  enabled"):
  * `readers.py` — `parse_nftables` / `Nftables` / `NftChain`: a pragmatic
    parser for the `nft` ruleset that walks `chain <name> { … }` blocks and
    reads each chain's default `policy` (which may sit alone, or trail the hook
    declaration on the same line), returning the token *as written* with its
    1-based source line. Last chain declared with a given name wins.
  * `matchers.py` — `NftPolicyMatcher` (backs `nft_policy`; the named chain's
    default policy must match `expected`) and `UnitEnabledMatcher` (backs
    `unit_enabled`; a unit must be present in a `*.target.wants` dir so the
    policy actually loads). Both register in `IMPLEMENTED_KINDS`.
  * `deny`/`drop` canonicalisation: nft's canonical default-deny is `drop`; the
    baseline expresses it as `deny`. `_canonical_nft_policy` collapses both to
    `drop` so a `deny` baseline rule passes a `drop` ruleset (and vice versa) —
    the comparison is spelling-agnostic.
- Two single-rule mutation fixtures under `tests/fixtures/mutations/` —
  `firewall-input-policy-deny` (input chain `policy accept;`) and
  `firewall-unit-enabled` (the `multi-user.target.wants/nftables.service`
  symlink removed) — each a full copy of the hardened tree with exactly one
  change so that rule fires (and only it).

### Changed

- The auditor now evaluates the 4 firewall rules (`nft_policy` + `unit_enabled`);
  the hardened tree still audits clean. Only the 3 defconfig symbol matchers
  remain skipped (skipped count dropped from 5 to 3), and the golden report
  counts in `tests/test_report.py` were updated accordingly.
- Bumped the package version to `0.6.0` (new feature).

## [0.5.0] — 2026-09-25

### Added

- Two more readers for the `check` submodule, backing the `sysctl_value` and
  `mount_option` matchers (spec.md "Implement the sysctl + mount reader"):
  * `readers.py` — `parse_sysctl_file` / `parse_sysctl_d`: parses
    `sysctl.d/*.conf` into an effective last-wins key/value view (later files
    override earlier ones, exactly as the kernel applies `sysctl.d`), with
    `file:line` source tracking; and `parse_fstab` / `Fstab` / `FstabEntry`:
    parses `/etc/fstab` into six-column mount entries with an `option_set()`
    helper (the `options` column split on `,`).
  * `matchers.py` — `SysctlValueMatcher` (globs `etc/sysctl.d/*.conf`, merges
    last-wins, compares the effective value against `expected`) and
    `MountOptionMatcher` (looks up the mount entry for the rule's `mountpoint`
    and requires *all* the comma-separated `option`s). Both register in
    `IMPLEMENTED_KINDS`.
- Seven single-rule mutation fixtures under `tests/fixtures/mutations/` — one
  per sysctl/mount rule — each a full copy of the hardened tree with exactly
  one line changed so that rule fires (and only it).

### Changed

- The auditor now evaluates the 5 sysctl rules and 2 mount rules (12 rules
  audited instead of 7); the hardened tree still audits clean. Only the
  firewall/units/defconfig rules remain skipped.
- Bumped the package version to `0.5.0` (new feature).

## [0.4.0] — 2026-09-25

### Added

- The `report` submodule now owns a **stable, deterministic layout** for both
  output forms (spec.md "Refine the report module" subtask):
  * A one-line **summary header** — rules audited, findings, skipped, passed,
    plus a per-severity breakdown (all five levels, zero-filled).
  * `summary()` — a public helper returning the headline counts
    (`rules_audited`, `findings`, `rules_with_findings`, `passed`, `skipped`,
    `by_severity`, `by_domain`). `passed` is derived: audited − skipped −
    rules that fired, so the numbers always reconcile.
  * `by_domain` findings-per-domain, resolved against the baseline's domain
    declaration order (never depends on finding arrival order).
- Golden layouts for the report: `tests/test_report.py` pins the exact text
  and JSON for a clean tree and a mutation tree (the tree path is normalised so
  the golden is portable across machines).

### Changed

- `render_text` header is now a title line underlined with `=` (width = title),
  followed by the summary block; findings are emitted under a `Findings (N):`
  heading and skipped rules under a `Skipped …` heading.
- `render_json` gains a `summary` block mirroring the text header.
- Bumped the package version to `0.4.0` (new feature).

## [0.3.0] — 2026-09-25

### Added

- The `check` submodule (`pengyos_hardening/check/`) — the linter core — with
  its first reader, the **sshd_config reader** (the `key_value` matcher).
  * `readers.py` — the sshd_config parser: an effective last-wins key/value
    view with canonical key names and `file:line` evidence.
  * `matchers.py` — the `key_value` matcher (ssh rules) plus a matcher-kind
    registry; kinds with no evaluator yet are skipped, not failed.
  * `audit.py` — walks a BR2_EXTERNAL tree, discovers each external tree root
    and its `*/rootfs-overlay/` dirs, and evaluates each implemented rule
    against its overlay root.
  * `report.py` — renders the audit as stable text or JSON, and is the
    `check` CLI entry point.
- `check --tree REPO_ROOT [--rule ID] [--json [OUT]]` now runs (ssh rules);
  the hardened fixture audits clean and each single ssh mutation fires exactly
  its named finding.
- Committed single-rule mutation fixtures under `tests/fixtures/mutations/`
  (one per ssh rule).

### Fixed

- The committed baseline's defconfig symbols dropped the erroneous
  `CONFIG_` prefix (Buildroot set-lines are `BR2_…=y`; only disabled comments
  carry `CONFIG_BR2_…`), so the symbol matchers can match a *set* line.

### Changed

- Bumped the package version to `0.3.0` (new feature).

## [0.2.0] — 2026-09-25

### Added

- The committed hardening **baseline as data** (`pengyos_hardening/data/baseline.json`):
  18 rules, each with a stable kebab-case `id`, a `severity`
  (`critical`/`high`/`medium`/`low`/`info`), a `domain`
  (`ssh`/`sysctl`/`firewall`/`defconfig`/`units`), and a declarative `matcher`
  (`key_value`, `sysctl_value`, `mount_option`, `nft_policy`, `unit_enabled`,
  `symbol_absent`, `symbol_present`) that describes *how* the rule is evaluated.
  The data is the contract; the rest of the package is the reader.
- The `baseline` submodule (`pengyos_hardening/baseline.py`) — loads the
  committed baseline and **structurally validates** it: a malformed rule
  (missing/invalid `id`, unknown `severity`/`domain`, or a matcher that does
  not conform to the schema) raises `BaselineError` rather than being silently
  tolerated. Includes `Rule`/`Matcher`/`Baseline` value objects, `by_id` /
  `by_domain` lookups, severity ordering, and JSON serialisation.
- The `errors` submodule (`pengyos_hardening/errors.py`) — the
  `PengyOSHardeningError` / `BaselineError` / `RuleError` hierarchy.
- `baseline --path FILE` to audit an alternate baseline; a malformed baseline
  exits `3` with a clean message.

### Changed

- Bumped the package version to `0.2.0` (new feature).

## [0.1.0] — 2026-09-25

### Added

- Scaffold the `pengyos-hardening` repo: `pyproject.toml` (stdlib-only package,
  `pengyos-hardening` console script, pytest config), the `pengyos_hardening/`
  package (`__init__`, `__main__`, argparse `cli` dispatching the `baseline` /
  `check` subcommands), `README.md`, `CHANGELOG.md`, MIT `LICENSE`
  (Copyright (c) 2026 Pat Wendorf), and `.gitignore`.
- Hermetic, opt-in integration test-suite (`pytest -q`): structural scaffold
  checks, CLI dispatch checks, and the GitHub-ready contract gate.
