# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-14

### Added
- **Upstream `Fetcher` abstraction** (implementation.md item 2): the
  `latest_version(component) -> str` interface, with an offline
  `MockFetcher` (mapping-backed, never touches the network) and a production
  `HttpFetcher`. `HttpFetcher` dispatches to pluggable, pure
  `LatestVersionStrategy` objects — `PyPILatestVersionStrategy`,
  `GitHubReleasesStrategy`, and a generic `RedirectLatestStrategy` — so each
  upstream's "latest" is read its own way while the single network entry point
  (`HttpFetcher.send`) stays stubbable. `build_fetcher(kind)` constructs the
  named fetcher, and `run` now selects the fetcher via `--fetcher`. Standard
  library only.
- **Semver-ish version comparison** (implementation.md item 3): a pure,
  offline-testable `compare.py` module — `parse_version` / `ParsedVersion`,
  `compare`, `is_newer`, and `classify` (with a `Drift` enum: `drift` /
  `behind` / `up-to-date`). It compares PengyOS pins on a common footing:
  LTS `x.y` and `x.y.z` cores compare equal (e.g. `1.2` == `1.2.0`),
  prereleases rank below their stable release and among themselves
  (SemVer-style), numeric cores order numerically (`1.10` > `1.9`), and build
  metadata is ignored. This is the decision core of the OBSERVE stage; the
  proposal emission that consumes it is item 4.
- **Proposal emission** (implementation.md item 4): the `run` pipeline now
  loads a **pins manifest** (`pins.py` — a TOML `component` array of tables,
  each pinning `name`/`version` and an optional `url`), fetches the latest
  release for each pin via the selected fetcher, classifies the drift with
  `compare`, and emits a :class:`Proposal` only when the latest release is
  newer. A proposal carries `(component, current, available, lts?, url)`;
  `lts` flags an LTS-style two-part (`x.y`) pinned line. Proposals are sorted
  by importance (non-LTS and larger jumps first) and written to `--out` as a
  JSON array. `detect_proposals(components, fetcher)` is pure and
  offline-testable; a missing/invalid manifest raises `PinsError` and an
  unreachable upstream raises `FetcherError` — both clean *ran-but-failed*
  (exit 1) outcomes. Standard library only.
- **GitHub-ready contract** (implementation.md item 5): `tests/test_github_ready.py`
  asserts the standing-presentable contract in one entry point — the README,
  MIT `LICENSE` ("Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md` (Keep a
  Changelog headings), `.gitignore` (ignores the emitted `proposals.json`),
  and coherent build metadata (`pyproject.toml` ↔ `__version__`) — and reports
  every missing piece at once. The opt-in full-suite gate
  (`PENGYOS_IT=1`) re-runs the whole suite in a subprocess to prove it is
  actually green. Standard library only.

## [0.1.0] - 2026-09-14

### Added
- **Package scaffold** (implementation.md item 1): `pengyos-upstream-watch`
  distribution + `pengyos_upstream_watch` package, `run` subcommand with the
  documented `--pins` / `--fetcher {http,mock}` / `--out` surface, `--version`
  and `python -m` entry points, and a `pyproject.toml` declaring the console
  script. Standard library only (TOML via `tomllib`).
- GitHub-ready contract: `README.md`, MIT `LICENSE`
  ("Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`, `.gitignore`, and a
  self-contained test suite that needs no network, root, display, or hardware.
