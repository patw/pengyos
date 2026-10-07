# PengyOS server-monitoring — metrics + alert rules (US-SRV-3)

## Summary
Node-exporter + TheWatcher systemd units, a small Prometheus-style alert-rule set, and a rule evaluator proven by fixtures where a deliberate failure fires and a healthy series stays silent.

## Purpose
USE-CASES US-SRV-3: metrics are exported; a deliberately induced failure produces an alert.

## Deliverable
A repo with `exporters/*.service`, `rules/*.toml` (alert rules as data), `alerts/` (ntfy/email sink config), and a pytest evaluator that runs sample metric series through the rules.

## Interface
- files: exporters/node-exporter.service, exporters/thewatcher.service, rules/pengyos.toml, alerts/ntfy.conf
- tests: pytest rule evaluator over fixture series

## Verification (tests are first-class)
- each failure fixture fires its rule; healthy series fire nothing.
- exporter unit files have required sections.
- a rule with a bad expr fails validation.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A simulated failure produces an alert in tests.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Standing up Prometheus/Grafana

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
