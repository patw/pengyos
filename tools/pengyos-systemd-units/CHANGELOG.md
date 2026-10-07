# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-XX-XX

### Added

- Scaffold repository: README, LICENSE (MIT), CHANGELOG, `.gitignore`, and
  `pyproject.toml`.
- GitHub-ready contract test (`test_github_ready`) asserting the repo ships with
  README, a correct MIT LICENSE, CHANGELOG, `.gitignore`, and a green suite.
- Standard-library-only Python package layout (`pengyos_systemd_units`).
- Core systemd unit set for Core: a marker synchronization target
  (`pengyos-core.target`), socket-activated OpenSSH (`sshd.socket` +
  `sshd.service`), and the PengyOS agent (`pengy-agent.service`).
- `tests/test_units.py` — verifies the Core set is well-formed and correct
  (correct section names, `ExecStart` where units run, `WantedBy` on any
  `[Install]`, correct socket activation), with one failing fixture per rule.
- `pengyos_systemd_units.validator` — the INI-style unit validator (standard
  library only). Parses unit files with case-sensitive keys, enforces a
  data-driven rule table (`[Unit]` required, correctly spelled `[Section]`
  names, `ExecStart` for `.service`, no `ExecStart` on `.target`,
  `ListenStream`/`Service` on `.socket`, `WantedBy` sanity on `[Install]`),
  and returns structured findings (`Finding`/`Validation`) with
  `validate_unit` / `validate_dir` / `validate_iter` helpers plus
  `summary` / `report` / `to_json` reporting.
- `tests/test_validator.py` — exercises the validator API, the `Unit`/`UnitKind`
  model, and every rule in the table (one failing fixture per rule), plus
  `summary`/`report`/`to_json` and rule-table shape checks.
- Three optional Core units — `networkd.service` (systemd-networkd),
  `resolved.service` (systemd-resolved), `journald.service` (systemd-journald)
  — each a plain, hardened service with `[Install]` `WantedBy=multi-user.target`.
  Exercised by `tests/test_units.py` (`OPTIONAL_UNITS` + dedicated presence /
  validity / `WantedBy` / hardening checks); the whole Core set now totals 7
  units and validates clean.
