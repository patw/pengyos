# PengyOS license-audit — verify claims & enforce freedom policy

## Summary
Verify each component's license claim and enforce the freedom policy; emit LICENSES.md.

## Purpose
SPEC §1: every license cell is a claim until checked; this checks them and enforces the posture.

## Deliverable
A Python tool that ingests the pins/license manifest, applies the policy (permissive preferred; copyleft-in-core flagged; documented exceptions allowed), and emits LICENSES.md + a machine report.

## Policy schema
The policy (`policy.toml`) is a TOML table with exactly three axes:

- `allowed_families` — list of SPDX id **prefixes** allowed outright (permissive
  preferred). A component whose claim starts with one of these passes. May be
  empty (nothing allowed outright). Elements are trimmed, non-empty, and unique.
- `copyleft_in_core` — boolean. When `false` (default) a weak/strong-copyleft
  claim in the `core` tier is **flagged**; leaves follow `allowed_families`.
- `exceptions` — list of component ids exempt from the policy entirely.
  Elements are trimmed, non-empty, and unique.

The license *family* vocabulary (permissive / weak-copyleft / strong-copyleft /
proprietary / unknown) is defined in `pengyos_license_audit.families`; the
SPDX-id → family mapping is implemented by the classification subtask.

## Policy evaluation

`pengyos_license_audit.policy_eval.evaluate(pins, policy) -> list[VerdictResult]`
decides, for every component, whether its license claim is allowed. Each result
is a `VerdictResult(component_id, family, verdict, reason)` where `verdict` is a
`Verdict` (`pass` / `warn` / `fail`):

* **pass** — allowed outright: the claim starts with a declared
  `allowed_families` prefix, or copyleft is permitted in the core
  (`copyleft_in_core = true`), or the component id is a documented exception.
* **warn** — *flagged*: not outright allowed, but the concern is soft enough to
  warn rather than fail. The canonical case is a copyleft claim in the core
  while `copyleft_in_core` is false **and** the component is only **dynamically**
  linked (the linking exception keeps the downstream binary from being pulled
  in).
* **fail** — a hard violation: a copyleft claim **statically** linked into the
  core (static linking of copyleft into core is the thing the policy exists to
  catch), a proprietary/unknown claim in the core, a disallowed copyleft leaf,
  or any other disallowed family.

**Static-vs-dynamic link flag (SPEC §1 note):** `Pin.link` is the deciding factor
for a copyleft-in-core claim while `copyleft_in_core` is false — dynamic linking
yields `warn`, static linking yields `fail`. A malformed claim is reported as a
hard `fail` (rather than raising). `evaluate` preserves manifest order;
`has_violations(results)` is `true` iff any verdict is `fail`.

The CLI (`run`) renders the per-component verdicts into `LICENSES.md`, adds them
to `report.json` under the now-populated `verdicts` key (previously reserved),
and exits non-zero (code 1) when `has_violations` is true.

## License classification

`pengyos_license_audit.classification` implements the SPDX-id → family mapping
and a small SPDX-ish expression parser.

- `classify(claim) -> LicenseFamily` — convenience entry point: normalises the
  claim (trim, collapse whitespace, drop a trailing parenthetical note) and
  classifies it.
- `classify_expression(claim) -> LicenseFamily` — classifies a full expression.
- `parse_expression(claim) -> LicenseExpression` — parses into an AST of
  `_License` leaves and `_BinaryOp` (`OR`/`AND`) nodes.

The parser understands a single SPDX id, an optional `WITH <exception>` clause
(the exception does not change the family), and `OR`/`AND` combinations with
parentheses for grouping. Combined operands fold over the freedom ordering:
`OR` yields the *freest* family, `AND` the *least free*. An unmapped but
well-formed claim classifies as `unknown`; malformed input raises
`ValidationError`.

## Interface
- pengyos-license-audit run --pins pins.toml --policy policy.toml --out LICENSES.md --json report.json

## Verification (tests are first-class)
- classification of MIT/Apache-2.0/LGPL-2.1/GPL-2.0/proprietary fixtures.
- policy: GPL/LGPL component in Core → flagged (warn when dynamically linked,
  fail when statically linked); allowed as standalone via exception.
- LICENSES.md stable ordering with per-component verdict annotations.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Fails (non-zero) when policy is violated without an exception.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Legal advice
- Scanning source trees

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
