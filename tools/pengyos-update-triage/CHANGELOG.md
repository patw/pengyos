# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.11.0] - 2026-09-14

### Added
- **CVSS v4.0 full BTE scoring (implementation.md item 11):**
  `pengyos_update_triage.cvss` now scores the *complete* CVSS v4.0 metric set
  — Threat (`E`) and Environmental (`CR/IR/AR` + the 12 `M*` modified base
  metrics + `MSI/MSA`) — per spec §8.2, where the single 6-digit MacroVector
  is built from the *effective* metric values (E absent/`X` → worst case `A`,
  CR/IR/AR absent/`X` → worst case `H`, `M*` overrides). New public API:
  `bte_score(value)` (score of the full vector as given; equals `base_score`
  for a Base-only vector and for v3.0/3.1/raw values), `parse_v4_vector(value)`
  (all 32 metrics, per-group value validation), `score_components(value)`
  (which groups the score applies: `CVSS-B`/`CVSS-BT`/`CVSS-BE`/`CVSS-BTE`),
  and the `V4_*_METRICS` group constants. The classification's severity band
  for a v4.0 vector now comes from the **BTE** score and its rationale detail
  names the applied group (e.g. `CVSS-BT score 5.2`); Base-only vectors are
  unchanged. Spec-verified against the official FIRST v4.0 *Examples* corpus
  (every Base/BT/BE/BTE row) in `tests/test_cvss.py`, cross-checked against an
  independent transcription of the reference calculator's JS over 40,000
  random full vectors (0 mismatches).

### Changed
- CVSS v4.0 final rounding now matches the official FIRST calculator
  bit-for-bit: `Math.round(value * 10) / 10` (halves up). On ~500 boundary
  vectors the earlier `Decimal(x + 1e-6)` half-up port disagreed (e.g. raw
  `8.549999999999999` → `8.5` now, was `8.6`); the reference behaviour wins
  because spec §8.3 points at that calculator as authoritative. All
  previously-locked example values unchanged except where they sat on such a
  boundary.
- `cvss.parse_v4_vector` now validates Threat/Environmental/Supplemental
  metric *values* (previously non-base metrics were silently dropped),
  rejects duplicates, and still ignores unknown metrics / metric order
  (lenient where the spec's fixed ordering is strict).

### Fixed
- Two stale hand-typed BASE scores on the FIRST v4.0 Examples page (log4j
  immutable `AV:N/AC:H/AT:P/...` printed 8.2 — actually 9.2; regreSSHion
  `AV:N/AC:L/AT:N/PR:L/.../SC:N/SI:N/SA:N` printed 8.5 — actually 8.7) are
  locked to the reference-calculator values in the test corpus.

## [0.10.0] - 2026-09-14

### Added
- **CVSS severity mapping beyond v3.1 (implementation.md item 10):**
  `pengyos_update_triage.cvss` now computes base scores for four input kinds
  (dispatched by the new `cvss_kind`):
  - **CVSS v3.0** base vectors — the same v3.1 closed form but with the legacy
    2015 Privileges Required multipliers (unchanged L=0.77 / H=0.44, changed
    L=0.77 / H=0.50), so 3.0 is semantically distinct from 3.1;
  - **CVSS v4.0** base vectors — ported from the FIRST reference algorithm
    (MacroVector lookup table §8.3 + interpolated proportional distance
    §8.2); only the Base metric group is scored (Threat/Environmental default
    to worst-case),
  - **raw numeric scores** (a bare `0.0..10.0` on a CVSS-typed severity entry),
  - version-less vectors are inferred as v3.1 or v4.0 from their metric names.
  New `exploitability_source(value)` reports `"cvss"` only for a v3 sub-score
  and `"none"` for a v4.0 vector / raw score; classification records that so a
  v4.0/raw finding is quantified for severity but never over-claims an
  Exploitability sub-score (stays `0.0`). Each kind is spec-verified with a
  test corpus (`tests/test_cvss.py`): v3.0 PR:N values cross-checked against an
  independent v3.1 reference, v4.0 values locked to the reference calculator,
  plus raw-score band mapping.

### Fixed
- CVSS v3.1 (and 3.0) base score no longer pre-rounds the Exploitability term:
  per FIRST spec §7.1 the `8.22 × AV × AC × PR × UI` product is used
  unrounded in the base-score formula and only the final BaseScore is
  `Roundup`-ed. This corrects boundary cases (e.g. an openssl-shape vector
  8.2 → 8.1, zlib 5.4 → 5.3, vim 4.5 → 4.4); the reported Exploitability
  *sub-score* signal is still rounded to one decimal as before, and severity
  bands / ranking are unchanged.

### Changed
- `tests/test_cvss.py` now covers 3.0 / 4.0 / raw and the version dispatch;
  `tests/test_classification.py` and `tests/test_ranking.py` assert the new
  v4.0 / raw / 3.0 classification + exploitability behaviour.

## [0.9.0] - 2026-09-14

### Added
- **`test_github_ready` (implementation.md item 9):** new
  `tests/test_github_ready.py` asserts the spec's *GitHub-ready contract*:
  - README.md exists and is authored (install, usage flags for `run` /
    `explain`, exit codes, MIT license line with the required copyright),
  - LICENSE is MIT with "Copyright (c) 2026 Pat Wendorf" and the core MIT
    clauses; `pyproject.toml` declares the same license,
  - CHANGELOG.md is Keep a Changelog / SemVer formatted and its topmost
    `## [version] - date` entry equals the package's current version (a
    release without a changelog entry fails the suite),
  - `.gitignore` covers Python build/test noise, virtual environments, and
    the tool's runtime artifacts (`proposals.json`), and gitignored
    artifacts are not tracked in the repo,
  - **docs stay in sync:** every `*.py` in the package and every `test_*.py`
    in `tests/` is documented in the README's project-layout tree, and the
    README Status table marks `test_github_ready` as done.
- The "full green test suite" clause is the outer `pytest -q` gate itself;
  an **opt-in integration test** (behind `PENGYOS_IT=1`, the pattern's first
  use in this suite) additionally re-runs the whole suite in a subprocess —
  with `PENGYOS_IT` removed from the child environment so it cannot
  recurse — and asserts exit 0.

### Changed
- README.md: Status row for `test_github_ready` flipped to ✅ done; the
  project-layout tree lists `tests/test_github_ready.py`.

## [0.8.0] - 2026-09-14

### Added
- **Committed realistic fixtures (implementation.md item 8):**
  `tests/fixtures/advisories/*.json` are now curated to *real,
  registry-verified* identifiers (verified against NVD / OSV / GitHub
  advisories on 2026-09-14): every advisory `id` and alias is a real
  CVE/GHSA, each entry carries a plausible summary/details, ISO-8601
  dates, http(s) references to official sources, and a parseable CVSS 3.1
  vector. Where a storyline intentionally diverges from the upstream
  registries, the advisory records it in
  `database_specific.pengyos_fixture_note`.
- `tests/test_fixtures.py` — curation guard: well-formed real ids (no
  placeholder numbers), non-empty summary/details, http(s) references,
  ISO dates, CVSS-3.1-parseable vectors, provenance (`source_file`),
  coverage of 4+ pinned components, exactly one unpinned component,
  changelogs attributed to pins with sections + entries, and the spec's
  end-to-end counts (13 proposals: 4 security + 9 relevant; 32 ignored)
  re-locked on the curated set.

### Changed
- **Advisory fixtures re-curated (ids/aliases/vectors):**
  - curl: the 8.9.0 SOCKS5 heap overflow now uses its real id
    `CVE-2024-2392` (per curl's release notes; the old `CVE-2024-2398`
    label was a registry conflation — 2398 is the HTTP/2 memory leak);
    the fabricated `GHSA-pmw3-j92q-jvvj` alias is gone.
    `CVE-2023-38545` now uses the real NVD vector (`UI:R`, 9.4) and the
    real `GHSA-7xw9-w465-6x42` cross-reference.
  - glibc `CVE-2023-4911`: real description (ld.so
    `GLIBC_TUNABLES` buffer overflow), real `GHSA-m77w-6vjw-wh2f` alias
    (the old `GHSA-9c85-796x-qm7c` did not exist), and the real
    7.8-high vector (was a fabricated 5.5 medium) — glibc now ranks in
    the high band.
  - openssl: the fabricated `GHSA-8hc4-vh54-5h27` was replaced by the
    real `CVE-2024-4741` (use-after-free in `SSL_free_buffers`, fixed
    3.0.14, 7.5 high); the fixture file was renamed
    `openssl_ghsa.json` → `openssl_cve.json`.
  - zlib `CVE-2023-45853`: real `GHSA-mq29-j5xf-cjwr` cross-reference.
  - vim (unpinned): the placeholder `CVE-2024-12345` was replaced by the
    real `CVE-2019-20079` (autocmd use-after-free, fixed 8.1.2136, 7.8
    high).
- **Changelog fixtures** aligned with the curated ids/wording (curl
  8.9.0 → `CVE-2024-2392`, glibc 2.40.0 → ld.so wording, openssl 3.0.14
  → `CVE-2024-4741`).
- **Fixture-driven test expectations** updated to the curated ids and the
  new bands: `--min-severity high` now keeps **3** security proposals
  (curl critical + glibc 7.8 + openssl 7.5); the top security order is
  curl → openssl → glibc → zlib (within the high band, exploitability
  3.9 vs 1.9 decides, since the severity key is the band, not the exact
  base score).

### Unchanged
- The spec's end-to-end counts on the fixtures (13 proposals /
  32 ignored) and the top proposal (`advisory:CVE-2024-2392:curl`).

## [0.7.0] - 2026-09-14

### Added
- **Emission: `proposals.json` + human summary + `explain` (implementation.md
  item 7):**
  - `pengyos_update_triage/proposal.py` — new module, still stdlib-only.
    `build_proposal_set(pins, advisories, changelogs, min_severity)` drives the
    full pipeline (`match_all` → `scan_changelogs` → `classify` → `rank`),
    splits it into **actionable proposals** (`security` + `relevant` that clear
    `--min-severity`) and **ignored** findings (`ignore`), and returns a
    `ProposalSet`. `write_proposals` serializes it as `proposals.json`
    (schema `pengyos.proposals/v1`) with `counts`, `inputs`, and a full
    `rationale_chain` per proposal; `render_summary` prints the per-proposal
    human summary (id/relevant line, severity, action, `affected → target`,
    one-line rationale) plus an ignored tally.
  - `Proposal` / `ProposalSet` — frozen dataclasses with `to_dict` /
    `from_dict` (round-trips so `explain` is self-contained against the file).
    A `last_affected` advisory with no fixed release renders its target as
    "a patched build".
  - `run` / `explain` CLI subcommands are now **real**: `run --pins … 
    --advisories … --changelogs … --out … [--min-severity …]` writes the file
    and prints the summary (a zero-proposal run is a success, exit `0`;
    bad/missing input is a clean exit `1`); `explain <id> [--proposals PATH]`
    reads a previously-written `proposals.json` (default `proposals.json`) and
    prints the full rationale chain with the category-deciding rule marked
    `FIRED`.
- Tests: `tests/test_proposal.py` (pipeline counts, ranking order, justification
  invariants, min-severity filtering, empty/degenerate inputs, JSON shape +
  round-trip, summary, explain, and the `run`/`explain` command entry points)
  and `tests/test_cli_run.py` (end-to-end `run` + `explain` through `main`).
  The two scaffold "not-implemented" tests were updated to assert the new
  ran-but-failed behaviour of the now-real subcommands.

### Docs
- spec.md gains the "Emission" contract (proposal vs. ignored split, the
  min-severity policy on the emitter, `proposals.json` shape, summary format,
  `explain` semantics, exit codes). README status table + layout updated.

## [0.6.0] - 2026-09-14

### Added
- Ranking: documented, no-ML heuristic (implementation.md item 6):
  - `pengyos_update_triage/ranking.py` — new module, still stdlib-only.
    `rank(classifications, pins)` orders findings lexicographically by
    **(severity, exploitability signal, component reachability)** with two
    deterministic tie-breakers (category `security < relevant < ignore`, then
    id) so the order is total and independent of input order. Unquantified
    severity sorts below `low`; a component with no pin degrades to rank 0,
    labelled `"unpinned"`.
  - `RankedClassification` — the classification plus its 1-based `position`,
    the three ranked signals as plain values, and a one-line `rank_rationale`
    (the seed of the item-7 emitter / `explain` output). `rank_key` is exposed
    and tested on its own.
- `cvss.exploitability(vector)` — the CVSS v3.1 **Exploitability sub-score**
  `Roundup(8.22 × AV × AC × PR × UI)` (0.2–3.9, spec §7.2), the documented
  exploitability signal ranking orders by after severity; plus
  `cvss.EXPLOITABILITY_MAX` (3.9). Non-3.1 vectors and raw numeric scores are
  rejected with `CvssError` like `base_score`.
- `Classification.exploitability` / `Classification.exploitability_source` —
  the rule engine now records the exploitability sub-score of the exact CVSS
  3.1 vector its severity came from (also quoted in the `severity` rationale
  step); `0.0` / `"none"` when unquantified (never over-claimed).

### Docs
- spec.md gains the "Ranking" contract; README status table + layout updated.

## [0.5.0] - 2026-09-14

### Added
- Classification: security / relevant / ignore with a transparent rule engine
  (implementation.md item 5):
  - `pengyos_update_triage/cvss.py` — new module, still stdlib-only. Computes the
    **CVSS v3.1 base score** from a vector string (formulae and metric values
    taken from the FIRST CVSS v3.1 specification, verified against its canonical
    worked examples 9.8 / 10.0 / 8.8 / 7.5 / 6.5) and maps the score to the
    `low/medium/high/critical` band on the NVD scale. `roundup` follows the spec's
    Section 7.5 float-safe ceiling. Non-3.1 vectors and raw numeric scores are
    rejected with `CvssError` (treated as an *unquantified* severity, never a run
    failure).
  - `pengyos_update_triage/classification.py` — new module, still stdlib-only. A
    rule engine over `AdvisoryMatch` / `ChangelogFinding`:
    - `derive_severity(advisory)` → `(severity, source, detail)`, preferring the
      first quantifiable CVSS 3.1 vector; otherwise `none` (`unquantified` when a
      CVSS value was present but not a 3.1 vector).
    - `classify_advisory_match` → `security` (affected) or `ignore`
      (not-applicable / not-affected), with a suggested `update` action and the
      highest `fixed` version as target (or "a patched build" for
      `last_affected`).
    - `classify_changelog_finding` → `relevant` for security-signal and bump
      findings (action `monitor`; a bump carries the newer release as target, a
      security finding carries the referenced CVE/GHSA ids).
    - `classify(matches, findings)` → `(security, relevant, ignore)`;
      `meets_min_severity(severity, min)` implements the `--min-severity`
      semantics (unquantified severities always pass).
    - Each `Classification` carries a one-line `rationale`, an ordered
      `rationale_chain` of `(rule, outcome, detail)` steps with exactly **one**
      rule `fire` (the category-deciding rule the item 7 `explain` will
      highlight), the source `identifiers`, the `evidence`, and `severity_source`.
  - `pengyos_update_triage/models.py` — the severity scale is now a first-class
    domain concept: `SEVERITY_LEVELS` (`low/medium/high/critical`),
    `SEVERITY_NONE` (an unquantified finding, sorting below `low`), and
    `severity_rank()`.
  - `tests/test_cvss.py` (~40 tests) and `tests/test_classification.py`
    (~45 tests): CVSS roundup/parsing/base-score/band coverage, every
    classification path (security with/without CVSS, unsupported CVSS scheme,
    no-target `last_affected`, multi-fix target, ignore not-applicable /
    not-affected / ecosystem-mismatch, relevant security / bump /
    corroboration), the one-firing-rule contract, the min-severity filter, and
    end-to-end classification of the committed fixtures (4 security, 9 relevant,
    32 ignore).
- `spec.md` gains a "Classification" section fixing the categories, the
  one-firing-rule contract, the CVSS 3.1 severity source (and its 3.1-only scope),
  and the min-severity filter; `README.md` gains a matching user-facing section.

### Changed
- `cli.py` now re-exports `SEVERITY_LEVELS` from `models` (the vocabulary and its
  ordering are a shared domain concept for classification and ranking) rather than
  defining them locally; the public name and value are unchanged.

## [0.4.0] - 2026-09-14

### Added
- Changelog scanning for security keywords and version bumps
  (implementation.md item 4):
  - `pengyos_update_triage/changelog_scan.py` — new module, still
    stdlib-only, operating on the parsed `Changelog` structure:
    - `KEYWORD_TABLE` + `scan_entry(line)` — a documented, case-insensitive,
      word-bounded keyword table (CVE/GHSA identifiers first, then
      vulnerability classes such as buffer/heap/stack/integer overflow,
      out-of-bounds, use-after-free, denial of service, RCE, privilege
      escalation, path traversal, XSS/CSRF, sandbox escape, race condition,
      credential leak, then generic terms like `security`, `hardening`,
      `bypass`, `exploit`); returns the fired canonical keyword names plus
      the actual CVE/GHSA ids found in the line.
    - `scan_changelog(changelog, pin)` → `ChangelogFinding` records of two
      kinds: `security` (one per version section with a matching entry;
      carries the deduped first-seen keyword names, the matched entry lines
      verbatim, and the referenced ids as evidence) and `bump` (at most one;
      only when the changelog is attributed to a pinned component and its
      newest release, per the shared `compare_versions` order, is strictly
      newer than the pin; lists every newer release in changelog order).
    - `find_pin_for` / `scan_changelogs` — deterministic attribution by name
      or alias (first pin in manifest order wins) and the multi-changelog
      entry point for the upcoming pipeline. Unpinned components are still
      keyword-scanned; only the bump check needs a pin.
  - `tests/test_changelog_scan.py` — 50+ tests: keyword true positives
    (33 parametrised lines), false-positive guards (11 clean lines, incl.
    "DOS legacy filesystems" and "secure"), per-section findings, first-seen
    keyword dedupe, id extraction order, bump semantics (newer / equal /
    older / unpinned / no sections / numeric ordering / pre-release below
    GA / tie-break), alias attribution, determinism, and end-to-end scans
    of the committed pins + changelog fixtures (glibc/openssl/curl/zlib
    security findings; glibc/openssl/curl bumps; zlib bump correctly
    absent).
- `spec.md` gains a "Changelog scanning" section fixing the keyword-table
  contract, the verbatim-entry and first-seen-order semantics, and the
  bump rules; `README.md` gains a matching user-facing section.

## [0.3.0] - 2026-09-14

### Added
- Advisory ↔ pin matching engine (implementation.md item 3 — the
  "matching against pinned (name, version-range)" half of OSV ingestion):
  - `pengyos_update_triage/matching.py` — new module, still stdlib-only:
    - `compare_versions(a, b)` — a documented stdlib dotted-version
      comparator: numeric components (`9 < 10`, `9.1.0444 == 9.1.444`),
      leading `v`/`V` and `+build` metadata ignored, pre-releases sort below
      their release (`1.0-rc1 < 1.0 < 1.0.1`), missing components count as
      `0`, and odd input still orders deterministically (never raises).
    - `version_in_ranges(version, ranges)` — OSV range-event evaluation
      (`introduced` inclusive start; repeated `introduced` closes the previous
      interval exclusive of the new value; `vulnerable` point interval;
      `fixed` exclusive end; `last_affected` inclusive end; unbounded below
      without `introduced`) returning a human-readable evidence string naming
      the range, its events, and the covering interval.
    - `match_advisory(pin, advisory)` → `AdvisoryMatch` — name/alias matching,
      ecosystem compatibility (a `generic` pin matches any ecosystem; two
      differing specific ecosystems skip the entry), explicit `versions[]`
      listing, and range evaluation; collects `suggested_fixes` from `fixed`
      events and keeps the advisory's full identifier set for evidence.
    - `match_all(pins, advisories)` — deterministic (advisory-outer,
      pin-inner) cross-product used by the upcoming pipeline.
  - `tests/test_matching.py` — 40+ tests: comparator parametrisation,
    every event-semantics case, alias/ecosystem/versions paths, and
    end-to-end cross-matches of the committed fixture pins against the
    committed advisory fixtures (openssl affected, glibc affected via both
    range and explicit versions, curl one-affected/one-not, zlib
    `last_affected` inclusive with no fix, non-pinned `vim` not applicable).

## [0.2.0] - 2026-09-14

### Added
- Input schema + fixture-friendly loaders (implementation.md item 2):
  - `pengyos_update_triage/models.py` — the canonical internal data model
    (`Pin`, `Advisory`/`Affected`/`Range`/`RangeEvent`/`Severity`,
    `Changelog`/`ChangelogSection`) and the documented reachability ordering
    (`opt < std < core`) that ranking will build on.
  - `pengyos_update_triage/pins.py` — the pins manifest schema (TOML
    `[[component]]` tables with `name`, `version`, `ecosystem`, `aliases`,
    `reachability`, `note`) plus `load_pins` / `parse_pins` and a
    `PinsManifest` with `find()` by name or alias. Parsed with stdlib `tomllib`.
  - `pengyos_update_triage/advisories.py` — OSV JSON ingestion supporting all
    three wire shapes (single object, top-level array, `{"results": [...]}`),
    normalising severities, affected ranges/events/versions, references and
    aliases; each `Advisory` records its `source_file` for evidence provenance.
  - `pengyos_update_triage/changelogs.py` — the changelog input schema
    (one file per component, attributed by a `component:` directive or the
    file-name stem) and a version-section parser recognising `## 1.2.3`,
    `Version 1.2.3` and Debian-style `== 1.2.3 ==` headers.
  - `pengyos_update_triage/errors.py` — structured `InputError` hierarchy
    (`PinsError`, `AdvisoryError`, `ChangelogError`, each with `Format`/`Schema`
    sub-classes and a `.path` provenance field) so bad input maps cleanly to
    the documented exit code `1`.
- Committed fixtures under `tests/fixtures/` (pins manifests, OSV advisory JSON
  — single/array/results-list plus a `advisories_bad/` set, and plain-text
  changelogs).
- Test suites `tests/test_models.py`, `tests/test_pins.py`,
  `tests/test_advisories.py`, `tests/test_changelogs.py` covering valid
  loading, defaults, ordering, provenance, and every format/schema violation.

### Changed
- `requires-python` bumped `>=3.9` → `>=3.11`: the pins manifest is TOML and is
  parsed with the standard library's `tomllib`, which requires Python 3.11+.
  Still zero third-party runtime dependencies.

## [0.1.0] - 2026-09-14

### Added
- Project scaffold (implementation.md item 1): `pyproject.toml` (PEP 621, MIT,
  `setuptools` build backend, standard-library-only runtime), the
  `pengyos_update_triage` package, and the `pengyos-update-triage` console
  script.
- CLI argument surface in `pengyos_update_triage/cli.py`: `--version`, plus the
  `run` and `explain` subcommands with the documented flags (`--pins`,
  `--advisories`, `--changelogs`, `--out`, `--min-severity`, and the
  `PROPOSAL-ID` positional). The argument parsing is real and tested; the
  subcommand *logic* (OSV/NVD ingestion, changelog scanning, classification,
  ranking, proposal emission, and the `explain` rationale chain) is tracked as
  its own subtasks in `implementation.md` and currently returns a clear
  `not implemented yet` message with a distinct exit code (`3`).
- Severity vocabulary (`SEVERITY_LEVELS = low/medium/high/critical`) and the
  `--min-severity` default (`low`), fixing the interface the later
  classification/ranking subtasks will build on.
- `python -m pengyos_update_triage` entry point alongside the console script.
- `README.md`, `CHANGELOG.md`, `LICENSE` (MIT, Copyright (c) 2026 Pat Wendorf),
  and `.gitignore`.
- Scaffold test suite (`tests/test_scaffold.py`) covering package metadata,
  entry-point behaviour, the CLI argument surface, and the declared entry
  point.
