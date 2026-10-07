# pengyos-update-triage

**Advisories + changelogs → update proposals** for
[PengyOS](../../README.md) — the "flagship" of the PengyOS
SPEC §3: let the agent watch upstream and propose what matters, while a human
ratifies the boundary.

Given a **pins manifest** (the pinned ``name, version`` pairs that make a build
reproducible) plus directories of **OSV/NVD advisory fixtures** and **upstream
changelog fixtures**, the tool:

* ingests the advisories and matches them against the pinned components,
* scans the changelogs for security-relevant signals (keyword / version-bump
  detection),
* classifies each finding as ``security`` / ``relevant`` / ``ignore`` with a
  human-readable rationale and the evidence (source ids) that drove it,
* ranks the surviving findings (severity, exploitability signal, reachability)
  with a documented, no-ML heuristic, and
* emits ``proposals.json`` plus a human summary; ``explain <id>`` prints the
  full rationale chain for one proposal.

## Status

| Subsystem | State |
|-----------|-------|
| Project scaffold (packaging, entry point, pytest) | ✅ done |
| CLI (`--version`, `run`, `explain`) — parsing **and** logic | ✅ done |
| Pins / advisories / changelogs input schema (fixtures) | ✅ done |
| OSV JSON ingestion + matching against pinned components | ✅ done |
| Changelog scanning (security keywords + version bumps) | ✅ done |
| Classification (security / relevant / ignore) + rationale engine + CVSS 3.1 severity | ✅ done |
| Ranking (severity, exploitability, reachability) — documented heuristic | ✅ done |
| Emit `proposals.json` + human summary; `explain` rationale chain | ✅ done |
| Committed realistic fixtures (curation-guarded) | ✅ done |
| `test_github_ready` (GitHub-ready contract) | ✅ done |
| CVSS severity mapping beyond 3.1 (3.0 / 4.0 / raw) | ✅ done |
| CVSS v4.0 full BTE scoring (Threat + Environmental) | ✅ done |

See `spec.md` for the contract.

### Committed fixtures (realism contract)

`tests/fixtures/` is curated to be *realistic*: every advisory id **and** alias
is a real, registry-verified CVE/GHSA identifier (never a placeholder number),
every advisory carries a non-empty summary/details, http(s) references to
official sources, ISO-8601 dates, and a parseable CVSS 3.1 vector; four of the
six pinned components are covered (glibc `CVE-2023-4911`, openssl
`CVE-2024-4741`, curl `CVE-2024-2392` + already-fixed `CVE-2023-38545`, zlib
`CVE-2023-45853`) and one unpinned component (vim `CVE-2019-20079`) exercises
the not-applicable path. Where a storyline intentionally diverges from the
upstream registry, the advisory's `database_specific.pengyos_fixture_note`
says so. `tests/test_fixtures.py` enforces the contract and re-locks the
end-to-end counts (13 proposals: 4 security + 9 relevant, 32 ignored).

## Install

Requires Python ≥ 3.11 (the pins manifest is TOML, parsed with the standard
library's `tomllib`). No third-party runtime dependencies. Install from the
repository root:

```sh
pip install -e .
```

That provides the `pengyos-update-triage` console script. You can also run it
directly:

```sh
python -m pengyos_update_triage --version
```

## Usage

```
pengyos-update-triage [--version] {run,explain} ...

pengyos-update-triage run
    --pins PINS.TOML
    --advisories ADVISORIES_DIR
    --changelogs CHANGELOGS_DIR
    --out PROPOSALS.JSON
    [--min-severity {low,medium,high,critical}]

pengyos-update-triage explain PROPOSAL-ID [--proposals PROPOSALS.JSON]
```

* `run` — ingest the advisories and changelogs for the pinned components,
  classify each finding with a transparent rationale, rank the results, and
  write `PROPOSALS.JSON` plus a human summary to stdout. The file holds the
  actionable proposals (`security` + `relevant` that clear `--min-severity`,
  default `low`) plus a separate `ignored` array for the transparent
  not-applicable / not-affected decisions. A run with zero proposals is still
  a success (exit `0`).
* `explain PROPOSAL-ID [--proposals PATH]` — read a previously-written
  `proposals.json` (default `proposals.json`) and print the full rule-by-rule
  rationale chain and the evidence (source ids) behind one proposal, with the
  category-deciding rule marked `FIRED`.

### Exit codes

| Code | Meaning |
|:----:|---------|
| `0` | Success |
| `1` | The command ran but produced a failure result |
| `2` | Usage error (bad/missing arguments) |
| `3` | Reserved (not currently returned by any subcommand) |

## Matching rules (advisory → pin)

An advisory applies to a pinned component when an `affected` entry names it
(`name` or an `alias`), and the entry's ecosystem is compatible (the default
`generic` pin matches any ecosystem; two differing specific ecosystems do not
match). The pinned version is *affected* when it is exactly listed in the
entry's `versions[]` or falls inside one of its `ranges[]`, evaluated with
OSV event semantics: `introduced` starts an interval inclusive (a repeated
`introduced` closes the previous interval exclusive of the new value and
reopens), `vulnerable` is a point interval, `fixed` ends exclusive (and is
recorded as a suggested fix), `last_affected` ends inclusive; an interval with
no `introduced` is unbounded below. Version comparison is stdlib-only and
dotted-numeric (so `9 < 10` and `9.1.0444 == 9.1.444`), ignores a leading
`v` and build metadata, and sorts pre-releases below their release
(`1.0-rc1 < 1.0 < 1.0.1`). Every match — affected, not-affected, or
not-applicable — carries a human-readable evidence string; that evidence is
the seed of the classification rationale (a later subtask).

## Changelog scanning (changelog → signals)

`pengyos_update_triage/changelog_scan.py` scans each parsed version section
for security signals and compares the changelog's newest release against the
pin. It emits `ChangelogFinding` records of two kinds:

* `security` — one per section where any entry line matches the documented
  keyword table (CVE/GHSA identifiers, overflow/OOB/UAF/DoS/RCE/privilege-
  escalation and similar vulnerability classes, then generic terms such as
  `security`, `hardening`, `bypass`, `exploit`). The finding records the
  fired keyword names (deduped, first-seen order), the matched entry lines
  verbatim, and the actual CVE/GHSA ids referenced — the evidence the
  classification subtask consumes.
* `bump` — at most one per changelog, present only when the changelog is
  attributed to a pinned component (name or alias) and its newest release
  (same `compare_versions` order as advisory matching) is strictly newer
  than the pin. It names the newest release, the pinned version, and every
  newer release in changelog order.

Keyword scanning also runs for *unpinned* components (an unpinned CVE mention
is still an input worth classifying); only the bump check needs a pin.

## Classification (findings → proposals)

`pengyos_update_triage/classification.py` is a **transparent rule engine** over
the matching and changelog evidence. Each signal gets one of three categories, a
severity, a suggested action, and a rule-by-rule rationale chain (the seed of
`explain`):

* **`security`** — an *advisory* confirms the pinned version is affected.
  Severity comes from a CVSS base score (3.0 / 3.1 / 4.0 / raw, below); the
  suggested action is
  `update`, targeting the advisory's highest `fixed` version (or "a patched
  build" when the range is `last_affected`).
* **`relevant`** — a *changelog* signal (a security-keyword finding, or a version
  bump past the pin) that no advisory quantifies. Severity is unquantified
  (`none`); the action is `monitor` (a bump names the newer release; a security
  finding carries the referenced CVE/GHSA ids).
* **`ignore`** — an advisory that does not apply to the pin (different component
  or ecosystem mismatch) or names a version the pin is not inside. Action `noop`.

Every classification records an ordered `rationale_chain` of `(rule, outcome,
detail)` steps with exactly **one** rule marked `fire` — the rule that decided
the category, which `explain` highlights.

**Severity (CVSS 3.0 / 3.1 / 4.0 / raw).** `pengyos_update_triage/cvss.py`
computes the CVSS **BTE score** (for a v4.0 vector the score of the full
vector as given — equal to the base score when only Base metrics are present;
for the other kinds, the base score) and maps it to
`low / medium / high / critical` on the NVD scale (low 0.1–3.9, medium 4.0–6.9,
high 7.0–8.9, critical 9.0–10.0). `cvss_kind` dispatches on four kinds: CVSS
**v3.1** (the FIRST closed form), CVSS **v3.0** (same form, legacy 2015
Privileges-Required multipliers), CVSS **v4.0** (the FIRST MacroVector lookup
+ interpolation algorithm over the *full* metric set — Base, Threat `E`, and
Environmental `CR/IR/AR` + `M*` modified metrics — `base_score` scores the
Base metrics only, `bte_score` the full vector), and **raw numeric**
scores (a bare `0.0..10.0`). A value that is malformed or an unsupported
version (e.g. CVSS 2.0) is rejected and reported as an *unquantified* severity
(`none`), so an affected finding is never dropped. A v4.0 vector or raw score is
quantified for severity but carries no v3 Exploitability sub-score (never
over-claimed). `--min-severity` filters quantified severities by rank; an
unquantified severity always passes the default `low` minimum.

**Ranking (documented heuristic, no ML).** `pengyos_update_triage/ranking.py`
orders classified findings lexicographically by

1. **severity** — `critical > high > medium > low`; an unquantified severity
   (`none`) sorts below `low`,
2. **exploitability signal** — the CVSS v3 *Exploitability sub-score*
   `Roundup(8.22 × AV × AC × PR × UI)` (0.2–3.9) recorded on the
   classification (0.0 when unquantified), so within one band the easier
   attack surface ranks first,
3. **component reachability** — the pin's `core > std > opt`; a component with
   no pin degrades to rank 0 and is labelled `unpinned`.

Two deterministic tie-breakers (category `security < relevant < ignore`, then
the classification id) make the order *total*: input order never affects the
output. Each result is a `RankedClassification` carrying its 1-based position,
the three signals as plain values, and a one-line `rank_rationale` for the
item-7 emitter.

## Testing

The test suite needs no network, root, display, or hardware — it uses committed
fixtures and pure functions. Real system integration is opt-in behind
`PENGYOS_IT=1`.

```sh
# full unit suite (must be green)
pytest -q
```

## Project layout

```
pengyos-update-triage/
├── pengyos_update_triage/
│   ├── __init__.py      # package metadata (__version__)
│   ├── __main__.py      # python -m entry point
│   ├── cli.py           # argparse surface + dispatch
│   ├── models.py        # canonical input data model (Pin, Advisory, Changelog, …)
│   ├── matching.py      # advisory ↔ pin matching (name, ecosystem, version ranges)
│   ├── errors.py        # structured input/schema error types
│   ├── pins.py          # pins manifest (TOML) schema + loader
│   ├── advisories.py    # OSV JSON advisory schema + loader
│   ├── changelogs.py    # changelog (plain text) schema + loader
│   ├── changelog_scan.py  # changelog scanning (security keywords + version bumps)
│   ├── cvss.py          # CVSS 3.0/3.1/4.0 (full BTE: Threat+Environmental) + raw numeric + exploitability + band
│   ├── classification.py # security/relevant/ignore rule engine + rationale chain
│   ├── ranking.py       # (severity, exploitability, reachability) heuristic
│   └── proposal.py      # item-7 emitter: proposals.json + summary + explain
├── tests/
│   ├── conftest.py      # makes the package importable without install
│   ├── fixtures/        # committed pins / advisories / changelogs fixtures
│   ├── test_scaffold.py # scaffold-level tests
│   ├── test_models.py   # data-model + reachability + severity ordering
│   ├── test_pins.py     # pins schema + loader
│   ├── test_advisories.py   # OSV schema + loader
│   ├── test_matching.py     # version comparison + advisory↔pin matching
│   ├── test_changelogs.py   # changelog schema + loader
│   ├── test_changelog_scan.py  # keyword true/false positives + bump detection
│   ├── test_cvss.py         # CVSS 3.0/3.1/4.0 (incl. BTE corpus) + raw base score, exploitability, dispatch, band
│   ├── test_classification.py # rule engine, severity, min-severity, fixtures
│   ├── test_ranking.py      # documented ranking heuristic + fixture end-to-end
│   ├── test_proposal.py     # emitter: pipeline, JSON, summary, explain, commands
│   ├── test_cli_run.py      # end-to-end run + explain through main()
│   └── test_github_ready.py # GitHub-ready contract: files, doc sync, opt-in suite gate
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── LICENSE              # MIT
└── .gitignore
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
