# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-09-19

### Added

- Scaffold the repository: `pyproject.toml` (stdlib-only, pytest configured),
  `README.md`, `CHANGELOG.md`, `.gitignore`, and an MIT `LICENSE`
  (Copyright (c) 2026 Pat Wendorf).
- A green, offline pytest suite (`tests/`) that verifies the scaffold artifacts
  and the GitHub-ready contract.
- Alert rules as data: `rules/pengyos.toml` (four rules: disk-free-ratio,
  high-node-load, scrape-target-down, systemd-unit-failed) and
  `pengyos_monitoring/rules.py` — schema + grammar validation with a small,
  documented evaluator subset (`ratio(a, b) OP number` / `metric{labels} OP
  number`, Prometheus-style `for` durations).
- Hardened exporter systemd units: `exporters/node-exporter.service` and
  `exporters/thewatcher.service`, plus `pengyos_monitoring/systemd_units.py`
  (INI parser + required-section/hardening validator).
- Rule evaluator: `pengyos_monitoring/series.py` (Prometheus text-exposition
  parser → `Series`/`Sample`) and `pengyos_monitoring/evaluator.py`
  (`evaluate(rule, series) -> Alert | None`). Supports `ratio(a, b) OP number`
  and `metric{labels} OP number` with sustained-for semantics (a run of
  consecutive samples ending at the latest sample and spanning ≥ `for` fires;
  a single sample never fires). Division by zero is not evaluable.
- Induced-failure fixtures in `fixtures/series/`: the disk-filling,
  high-load, systemd-unit-failed and scrape-target-down scenarios, each with a
  healthy mirror that must stay silent (plus a `transient-disk` negative case).
  The spec acceptance contract ("each failure fixture fires its rule; healthy
  series fire nothing") is asserted explicitly in
  `tests/test_evaluator.py::test_induced_failure_contract_holds_for_all_fixtures`.
- Alert notification sinks: `alerts/ntfy.conf` and `alerts/email.conf`
  (INI-style `[ntfy]` / `[email]` sink tables) plus
  `pengyos_monitoring/alerts.py` — `load_config` validates a sink config and
  `render_alert`/`dry_run` render the notification a sink *would* deliver: an
  ntfy POST payload (topic/title/message/priority/tags, severity→priority
  mapping, optional bearer auth) or a stdlib `EmailMessage`. Rendering is pure
  and offline (no socket, no SMTP), proven by `tests/test_alerts.py`.
- A `test_github_ready` gate (`tests/test_github_ready.py`) that asserts the
  whole repo is GitHub-ready in one shot: the required artifacts (README.md,
  LICENSE, CHANGELOG.md, `.gitignore`) are present and correct (LICENSE is MIT
  with "Copyright (c) 2026 Pat Wendorf"; CHANGELOG has version entries;
  `.gitignore` covers Python artefacts; `pyproject.toml` is valid, stdlib-only,
  and pytest-configured; the `pengyos_monitoring` package imports and its
  version is pinned; every top-level entry in the README layout tree exists —
  docs kept in sync — and the offline suite is green (run via a subprocess with
  a self-recursion-guard env var so it never recurses into itself).
