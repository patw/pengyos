# PengyOS update-triage — advisories + changelogs → proposals

## Summary
Ingest OSV/NVD advisories and upstream changelogs for pinned components; classify security/relevant/ignore with rationale; emit proposals.

## Purpose
The flagship (SPEC §3): let the agent watch upstream and propose what matters, human ratifies the boundary.

## Deliverable
A Python tool `pengyos-update-triage` that, given a pins manifest and advisory fixtures, produces ranked, justified proposals (with severity, affected versions, and a suggested action).

## Interface
- pengyos-update-triage run --pins pins.toml --advisories DIR --changelogs DIR --out proposals.json [--min-severity X]
- pengyos-update-triage explain <proposal-id> [--proposals PATH]  (PATH defaults to `proposals.json`)

### CLI conventions (fixed at scaffold)
- **Severity vocabulary:** `--min-severity X` accepts `low | medium | high |
  critical` (lowest → highest). The default minimum is `low`. These are the
  levels emitted on proposals; the *ordering* used by the filter and by ranking
  is this same order (see the classification/ranking tasks).
- **Exit codes:** `0` success · `1` ran but failed (e.g. a proposal set could
  not be produced) · `2` usage error (argparse) · `3` subcommand recognised
  but not yet implemented (scaffold stage). A `run` that produces zero
  proposals (nothing matched / all filtered) is still a *success* (`0`) —
  "no action needed" is a valid outcome, not an error.
- `run` writes `proposals.json` to `--out` and prints a short human summary to
  stdout; the summary lists, per proposal, its `id`, `severity`, classification
  (`security`/`relevant`/`ignore`), affected pinned version → suggested target,
  and a one-line rationale. `explain <id>` prints the full rationale chain.

## Input schemas

The three inputs have a concrete, fixture-friendly schema. Each loader maps its
wire format to a canonical internal model (`pengyos_update_triage.models`) and
raises a structured `InputError` subclass on violation (→ exit code `1`).

* **Pins manifest** — TOML (parsed with stdlib `tomllib`, hence **Python ≥ 3.11**;
  `requires-python` bumped from `>=3.9` to `>=3.11`). Components are
  `[[component]]` tables; `name` and `version` are required non-empty strings,
  `ecosystem` (default `"generic"`), `aliases` (list of strings, each must
  differ from `name`), `reachability` (one of `opt|std|core`, default `std`),
  and `note` are optional. Component `name`s must be unique. An empty manifest
  (zero components) is legal. Top-level sibling keys are ignored (the manifest
  is a subset of a larger build file).
* **Advisories** — OSV-format JSON. A fixture file may hold a single advisory,
  a top-level array, or `{"results": [...]}`. Recognised fields: `id` (required),
  `summary`, `details`, `aliases` (e.g. CVE ids), `published`/`modified`,
  `severity[]` `{type,score}`, `affected[]` `{package{name,ecosystem},
  ranges[{type,events}], versions[], database_specific}`, `references[]`,
  `database_specific`. Unknown fields are ignored (forward-compatible). Each
  loaded `Advisory` records its `source_file` — the evidence provenance the
  "every proposal carries source ids" criterion depends on.
* **Changelogs** — plain text, one file per component. A file is attributed to a
  component by a `component:` directive on its first non-blank line, else by its
  file-name stem. Lines are grouped into version sections at recognised headers
  (`## 1.2.3`, `Version 1.2.3`, Debian-style `== 1.2.3 ==`); only lines whose
  *entire* content is a version count as headers, so a bullet that merely
  mentions a version is not a section break. Keyword scanning of those sections
  is the changelog-scanning subtask.

## Matching semantics

Matching an advisory against a pinned component is defined as follows
(`pengyos_update_triage.matching`); the classification and ranking subtasks
build on its evidence strings.

* **Name** — an `affected` entry applies to a pin if its package `name` equals
  the pin's `name` or any pin `aliases` (advisories and distro pins may use
  different names for the same component).
* **Ecosystem** — a pin whose ecosystem is the default `"generic"` matches
  any advisory ecosystem, and an advisory with no ecosystem matches any pin.
  When both sides carry a specific ecosystem and they differ (case-insensitive
  compare), the entry is skipped and the skip is recorded in the evidence.
* **Version** — the pinned version is *affected* if it is exactly listed in
  `versions[]` or falls in any `ranges[]` interval, with OSV event semantics:
  `introduced X` opens an interval inclusive at X (no `introduced` ⇒ unbounded
  below; a repeated `introduced` closes the open interval exclusive of the new
  X and reopens there); `vulnerable X` is a point interval; `fixed X` closes
  exclusive of X (X is not vulnerable and is a suggested fix);
  `last_affected X` closes inclusive of X.
* **Version comparison** is stdlib-only (`compare_versions`): numeric
  dotted components (`9 < 10`, `9.1.0444 == 9.1.444`), leading `v` and
  `+build` metadata ignored, missing components count as `0` (`1.0 == 1.0.0`),
  pre-releases sort below their release (`1.0-rc1 < 1.0 < 1.0.1`). It is not
  a full PEP 440 implementation; unorderable oddities still compare as plain
  strings, so the ordering is total and deterministic.
* **Evidence** — every match outcome (affected / not-affected / not-applicable)
  carries a non-empty human-readable `evidence` list plus the advisory's full
  identifier set (`id` + aliases). A not-affected match describes the checked
  intervals; a not-applicable match says which name/ecosystem check failed.

## Changelog scanning

`pengyos_update_triage.changelog_scan` operates on the parsed `Changelog`
structure and emits `ChangelogFinding` records, each with a `kind`, a
non-empty `rationale` and non-empty `evidence` (the seeds of the item 5
rationale chain and item 7 `explain` output):

* **`security`** — one finding per *version section* in which at least one
  entry line matches the security-keyword table. The table is case-insensitive
  and word-bounded: identifier patterns first (`CVE-\d{4}-\d{4,7}`,
  `GHSA-xxxx-xxxx-xxxx`), then vulnerability classes (buffer/
  heap/stack/integer overflow, out-of-bounds, use-after-free/UAF, denial of
  service, remote code execution/RCE, privilege escalation, path traversal,
  cross-site/XSS/CSRF, sandbox escape, race condition, credential/password/
  secret leak), then generic terms (bypass, exploit, vulnerable, vulnerability,
  malicious, injection, unauthorized, hardening, security). A finding records
  the fired canonical keyword names (deduped, **first-seen order across the
  section's entries**), the matched entry lines **verbatim as parsed** (bullet
  markers included), and the actual CVE/GHSA ids mentioned. A line like
  "Regular release", "bug fixes" or "DOS legacy filesystems" must NOT match.
* **`bump`** — at most one finding per changelog, present only when the
  changelog is attributed to a **pinned** component (name or alias; the first
  pin in manifest order wins) **and** the changelog's newest release —
  ordered by the same stdlib `compare_versions` comparator as advisory
  matching (pre-releases sort below their GA; on a tie of notation-equivalent
  versions the first section wins) — is **strictly newer** than the pinned
  version. The finding's `version` is the newest release, `pin_version` the
  pin; the evidence lists every newer release in changelog order. A changelog
  whose newest release is equal to or older than the pin yields no bump
  finding.

Keyword scanning also runs for **unpinned** components (`pin_version` is
`None`); only the bump check needs a pin. Output order is deterministic:
changelogs in the order given; within a changelog, security findings in
section order, then the bump finding.

## Classification

`pengyos_update_triage.classification` is a **transparent rule engine** that
turns the matching / changelog evidence into one of three categories per signal,
each carrying a severity, a suggested action, and a rule-by-rule rationale chain
(the seed of the item 7 `explain` output):

* **`security`** — an *advisory* confirms the pinned version is affected.
  Severity is CVSS-derived; action `update`; the target is the advisory's
  highest `fixed` version, or "a patched build" when the range is
  `last_affected` (no fixed release).
* **`relevant`** — a *changelog* signal (a security-keyword finding, or a version
  bump past the pin) with no advisory quantifying it. Severity unquantified
  (`none`); action `monitor`. A `bump` finding carries the newer release as its
  target; a security finding carries the referenced CVE/GHSA ids.
* **`ignore`** — an advisory that does not apply to the pin (different component,
  or an ecosystem mismatch), or that names a version the pin is *not* inside.
  Action `noop`; severity `none`.

**Rule chain & the "fired rule":** every classification records an ordered
`rationale_chain` of `(rule, outcome, detail)` steps. Exactly **one** rule has
outcome `fire` — the rule that decided the `category` (this is what `explain`
highlights); the rest are `pass` (the condition held, so evaluation continued)
or `skip` (did not apply). The category-deciding rules are `advisory-applicable`
and `advisory-affected` (advisory path) and `changelog-signal` (changelog path).

**Severity source (CVSS 3.0 / 3.1 / 4.0 / raw):** `pengyos_update_triage.cvss`
computes the CVSS **BTE score** (for a v4.0 vector the score of the full
vector as given — equal to the Base score when only Base metrics are present;
for everything else the base score) and maps it to the
`low/medium/high/critical` band on the NVD scale (none 0, low 0.1–3.9, medium
4.0–6.9, high 7.0–8.9, critical 9.0–10.0). `cvss_kind` dispatches on four input
kinds:

* **CVSS v3.1** base vectors — the FIRST CVSS v3.1 closed form (spec §7.1/§7.4);
  verified against the canonical worked examples (9.8/10.0/8.8/7.5/6.5). In the
  base-score formula the Exploitability term is the *unrounded* product
  `8.22 × AV × AC × PR × UI`; only the final BaseScore is `Roundup`-ed (spec
  §7.1). A version-less vector is inferred as v3.1 from its metric names.
* **CVSS v3.0** base vectors — the *same* closed form, but with the **legacy
  2015 Privileges Required multipliers** (Table 16): unchanged-scope L=0.77 /
  H=0.44, changed-scope L=0.77 / H=0.50 (3.1 uses L=0.62 / H=0.27 unchanged and
  L=0.68 changed). PR:N vectors therefore score identically in 3.0 and 3.1;
  PR:L / PR:H(unchanged) vectors differ. Note: FIRST's re-issued *CVSS 3.0
  Specification (v1.9)* restates the PR values to match 3.1, but this tool uses
  the legacy values so 3.0 remains semantically distinct.
* **CVSS v4.0** vectors — **full CVSS-BTE** (Base + Threat + Environmental).
  CVSS v4.0 has **no closed-form score**. Per the FIRST CVSS v4.0
  Specification (specification-document v1.2, §8.2/§8.3) the score of a vector
  is the **MacroVector** (6-digit equivalence class) value from the
  authoritative lookup table (§8.3 points at the FIRST reference calculator's
  `cvss_lookup.js`) minus an **interpolated** proportional severity-distance
  within the MacroVector (§8.2). Per §8.2 the MacroVector is computed from the
  *effective* metric values, so the single table already scores the full
  metric set: the **Threat** metric (`E`; `E:X`/absent → worst case `E:A`) and
  the **Environmental** group (`CR/IR/AR` → worst case `H` when absent, the 12
  `M*` modified base metrics overriding their base counterparts when not `X`,
  and `MSI/MSA` feeding EQ4) shape the same 6-digit MacroVector. The final
  spec has **no** separate environmental/threat tables and **no**
  0.4/0.4/0.2 blend — the published per-group scores (`CVSS-B` / `CVSS-BT` /
  `CVSS-BE` / `CVSS-BTE`) are simply the score of the vector with the
  corresponding groups applied. The module exposes both ends: `base_score`
  = score of the Base metrics only (non-base metrics treated as absent →
  worst-case defaults); `bte_score` = score of the *full* vector as given
  (equal to the base score for a Base-only vector; for v3.0/v3.1/raw values it
  is the base score — v3.x temporal scoring is a non-goal). All **32** metrics
  parse and validate (`parse_v4_vector`): 11 Base + `E` + 15 Environmental +
  6 Supplemental (`S/AU/R/V/RE/U` accepted, never scored — spec §5). Metric
  order is not enforced and unknown metrics are ignored (the tool stays
  lenient where the spec's fixed ordering is strict). Rounding matches the
  official calculator's `Math.round(value*10)/10` bit-for-bit (~500 boundary
  vectors differ from the epsilon-based Python ports; the official behaviour
  wins). A version-less vector is inferred as v4.0 when it carries the v4
  metric names (`AT/VC/VI/VA/SC/SI/SA`).

  *Severity source:* for any v4.0 vector the classification's severity band
  comes from the **BTE** score (a Base-only vector's BTE score *is* its Base
  score, so published CVSS-B values are unchanged); the rationale detail names
  the applied group with the spec nomenclature (`CVSS-B` / `CVSS-BT` /
  `CVSS-BE` / `CVSS-BTE` score N.N). The Exploitability sub-score signal stays
  `0.0`/`"none"` for v4 (no v3-style sub-score exists). Spec-verified with the
  official FIRST v4.0 *Examples* corpus (every Base/BT/BE/BTE row) in
  `tests/test_cvss.py` — two BASE rows of that page carry stale hand-typed
  values (printed 8.2 / actually 9.2; printed 8.5 / actually 8.7); the
  reference calculator's values are locked instead.
* **Raw numeric scores** — a bare number in `0.0..10.0` carried on a CVSS-typed
  `severity` entry is used directly as the base score.

A value that is none of the above, a malformed vector, or a CVSS version the
tool does not compute (e.g. CVSS 2.0) is *rejected* with `CvssError`; an
affected finding with no quantifiable score is then reported at severity `none`
(`severity_source` = `none` or `unquantified`) — it is never dropped and always
clears the default `low` minimum. The Exploitability **sub-score** signal
(recorded on a classification and used by ranking) is `Roundup(8.22 × AV × AC ×
PR × UI)` for a v3.0/v3.1 vector and `0.0`/`"none"` for a v4.0 vector, a raw
numeric score, or an unquantified value (never over-claimed).

**`--min-severity` filter (`meets_min_severity`):** a quantified severity passes
when its rank is at least the minimum's rank (`low < medium < high < critical`);
an unquantified severity (`none` / absent) **always** passes, so an
affected-but-unquantified finding is never silently filtered out.

The severity vocabulary and its ordering live in `pengyos_update_triage.models`
(`SEVERITY_LEVELS`, `SEVERITY_NONE`, `severity_rank`); the CLI re-exports
`SEVERITY_LEVELS` for the `--min-severity` choices.

## Ranking

`pengyos_update_triage.ranking.rank(classifications, pins)` orders classified
findings with a **documented, no-ML heuristic**: the lexicographic key
**(severity, exploitability signal, component reachability)**, highest first,
plus two deterministic tie-breakers that make the order *total* (input order
never affects output):

* **severity** — the classification's band on the fixed scale
  (`critical > high > medium > low`); an unquantified severity (`none` / absent)
  sorts *below* `low`, mirroring `severity_rank`.
* **exploitability signal** — the CVSS v3 **Exploitability sub-score**
  `Roundup(8.22 × AV × AC × PR × UI)` (0.2–3.9, spec §7.2; `cvss.exploitability`),
  recorded on the classification by the rule engine (`Classification.
  exploitability`, source `"cvss"`). Within one severity band, the finding
  whose attack surface is easier to reach ranks first (e.g. 3.9 for
  AV:N/AC:L/PR:N/UI:N before 1.9 for a local-privilege vector). A CVSS v4.0
  vector or a raw numeric score has no v3-style sub-score, and an unquantified
  signal likewise: both carry `0.0` (source `"none"`) and are never over-claimed
  — the same policy as unquantified severity. Changelog classifications always
  carry `0.0`.
* **component reachability** — the covering pin's reachability
  (`core > std > opt`, `reachability_rank`). A finding whose component names
  no pin (name or alias; first pin in manifest order covers, same rule as
  changelog attribution) degrades to rank 0 and is labelled `"unpinned"`.

Tie-breakers, applied only when all three signals are equal: **category**
(`security` before `relevant` before `ignore`), then the classification
**id** lexicographically.

`rank` returns a list of `RankedClassification` (one per input, none dropped;
the input list is never mutated): the underlying `Classification`, its 1-based
`position`, the three signals as plain serializable values, and a one-line
`rank_rationale`. The `rank_key(cls, label, rank)` function is exposed and
tested on its own so the ordering contract is inspectable independently.
Ranking never re-categorises or filters — the `--min-severity` filter and the
choice of *which* classifications to rank are the item-7 emitter's concerns.

## Emission

`pengyos_update_triage.proposal` is the terminal stage: it drives
`match_all` → `scan_changelogs` → `classify` → `rank`, applies the
`--min-severity` filter, writes `proposals.json` and prints a human summary.
`explain` reads a previously-written `proposals.json` and prints the full
rationale chain for one id.

* **Actionable proposals vs. ignored findings.** The *proposals* are the
  classified findings in the `security` or `relevant` categories that clear
  `--min-severity`; these are the things a human should act on or monitor
  (actions `update` / `monitor`). The full `ignore` cross-product (action
  `noop`) is **not** a proposal — it is triage *decisions*, kept separately in
  an `ignored` array for transparency and still explainable by id. Over the
  committed fixtures the default `run` emits **13 proposals** (4 security +
  9 relevant) and **32 ignored**.
* **`--min-severity` on the emitter.** The filter is `meets_min_severity`:
  quantified severities must rank at/above the minimum; unquantified
  severities (all `relevant`/changelog findings and any affected-but-
  unquantified `security`) **always** pass — a `relevant` signal is never
  dropped by the filter, so `--min-severity critical` still surfaces the
  `relevant` monitor notes alongside the critical `security` proposals.
* **`proposals.json`** (schema tag `pengyos.proposals/v1`): top-level
  `schema`, `generator` (`pengyos-update-triage <version>`), `min_severity`,
  `counts` (`security` / `relevant` / `ignore` / `proposals`), `inputs`
  (`pins` / `advisories` / `changelogs`), `proposals[]`, `ignored[]`. Each
  proposal object carries `id`, `component`, `category`, `severity`,
  `severity_source`, `action`, `pin_version`, `target_version`, `rationale`,
  `identifiers` (source ids), `evidence`, `source`, `exploitability`,
  `exploitability_source`, `order` (1-based re-index within the emitted list),
  `rank_signals` (severity_rank / exploitability / reachability /
  reachability_rank), `rank_rationale`, `fired_rule`, and the full
  `rationale_chain` (`[{rule, outcome, detail}, …]` — the data `explain`
  renders, so `explain` is self-contained against the file).
* **Human summary (stdout).** A header line with the proposal count and the
  min-severity, then one block per proposal: its rank, category, component and
  source ids; its severity / action / `affected → target` range; and the
  one-line rationale. The `target` prints as the fixed version, or "a patched
  build" for a `last_affected` advisory with no fixed release, or "newer
  release" / "release review" for a `relevant` bump / security signal. A
  compact tally of the `ignored` findings (each explainable by id) closes it.
* **`explain <id>`.** Reads `--proposals PATH` (default `proposals.json`),
  finds the id among `proposals` **and** `ignored`, and prints the full
  rationale chain with the single rule whose outcome is `fire` — the rule that
  decided the category — explicitly marked `FIRED`. Also prints the evidence
  and source ids. A missing file or unknown id is a clean ran-but-failed
  (`1`).
* **Exit codes:** `run` returns `0` on success (a zero-proposal run *is* a
  success), `1` when an input is missing/unparseable (an `InputError`) or the
  output cannot be written; `explain` returns `0` on success, `1` on a missing
  file or unknown id. Usage errors are `2` (argparse).

## Committed fixtures

The fixtures under `tests/fixtures/` are the reference inputs for the counts
above and are **curated to be realistic**:

* Every advisory `id` **and alias** is a real, registry-verified
  `CVE-\d{4}-\d{4,7}` / `GHSA-xxxx-xxxx-xxxx` identifier — never a placeholder
  number (the old `CVE-2024-12345` example is gone).
* Each advisory carries a non-empty summary and details, at least one http(s)
  `reference` to an official source, ISO-8601 `published`/`modified` stamps,
  and `severity` entries that are **parseable CVSS 3.1 base vectors** (the
  fixtures never fall back to the unquantified severity path).
* A few CVEs span the pinned set: four of the six pins are covered — glibc
  `CVE-2023-4911`, openssl `CVE-2024-4741` (fixed 3.0.14), curl
  `CVE-2024-2392` (critical, fixed 8.9.0) plus the already-fixed
  `CVE-2023-38545` (the not-affected/ignore path), and zlib `CVE-2023-45853`
  (the `last_affected` / no-fixed-release path). Exactly one advisory targets
  an **unpinned** component (vim `CVE-2019-20079`) for the not-applicable
  path.
* **Divergences from the upstream registries are declared, not silent:** where
  a storyline intentionally differs (glibc keeps pin 2.39 affected; curl's
  8.9.0 SOCKS5 overflow is `CVE-2024-2392` per curl's release notes even
  though some registries map that number to unrelated products), the advisory
  records it in `database_specific.pengyos_fixture_note` (an OSV field the
  loader already recognises).
* The invariants are enforced by `tests/test_fixtures.py`, which also re-locks
  the end-to-end counts (13 proposals = 4 security + 9 relevant, 32 ignored)
  on the curated set. Over that set the top four security proposals rank
  curl (critical 9.8) → openssl (high 7.5, exploitability 3.9) → glibc (high
  7.8, exploitability 1.9) → zlib (medium 5.4) — the band, not the exact base
  score, is the severity key.

## Verification (tests are first-class)
- a CVE affecting a pinned version → proposal classified security with correct rationale.
- a CVE for a non-pinned/older version → ignored with reason.
- severity ordering respected; min-severity filter works.
- changelog keyword detection true/false positives on fixtures.
- explain output includes the rule that fired.

**GitHub-ready contract** (assert with `tests/test_github_ready.py`):

* **README.md** — authored (install instructions, usage of *both* subcommands
  with their flags, the exit-code table, the MIT license line with the
  required copyright).
* **LICENSE** — MIT, "Copyright (c) 2026 Pat Wendorf", core MIT clauses
  intact; `pyproject.toml` declares the same license.
* **CHANGELOG.md** — Keep a Changelog / Semantic Versioning format, and its
  *topmost* `## [version] - date` entry equals the package's current version
  (a release without a changelog entry fails the suite).
* **`.gitignore`** — covers Python build/test noise, virtual environments,
  and the tool's runtime artifacts (emitted `proposals.json`), and none of
  those artifacts are tracked by git.
* **Docs in sync** — every `*.py` in the package and every `test_*.py` in
  `tests/` is documented in the README's project-layout tree; the README
  Status table is current.
* **Full green test suite** — enforced in two layers: the *outer* `pytest -q`
  gate is the suite itself (the in-suite tests deliberately do NOT re-run the
  suite from inside — that would be quadratic and fragile); additionally an
  **opt-in integration test** behind `PENGYOS_IT=1` re-runs the suite in a
  subprocess (with `PENGYOS_IT` removed from the child environment so it
  cannot recurse) and asserts exit `0`.

## Acceptance criteria
- Every proposal carries a human-readable rationale and evidence (source ids).
- No network required at test time.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Auto-applying updates
- Being a full CVE scanner

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
