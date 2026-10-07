# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`test_github_ready` refinement (item 10):** the GitHub-ready contract now
  asserts the docs are *in sync* with the code (spec: "keep them in sync"), not
  just present. New cross-consistency checks in `tests/test_github_ready.py`:
  the LICENSE is the full canonical MIT body (not a bare "MIT" stub); the
  LICENSE copyright holder matches the author(s) declared in `pyproject.toml`;
  the CHANGELOG documents `pyproject.toml`'s current version; the README
  documents every CLI subcommand (driven by the real argparse parser, so an
  undocumented command is caught); and `.gitignore` ignores the emitted
  `.quadlet` units. Suite: 151 passed.

- **`check` — rootless-podman host prerequisite report** (item 7, US-SRV-2): a
  new `check` subcommand that probes the host for the three prerequisites
  rootless podman needs — a subordinate **subuid** range, a subordinate
  **subgid** range, and a container **storage.conf** — and prints an aligned
  report. It is **report-only**: it never runs podman/systemd and never modifies
  the host, and it always exits `0` (a missing prerequisite is a reported
  finding, not a code error). Every probe is injectable (a `read_file` callable,
  a command `run` callable, and the path lists) so it is fully testable with no
  root / network / real podman; the optional `getent` fallback is tolerated when
  `getent`/its database is unavailable. New module `pengyos_podman/hostcheck.py`;
  tests in `tests/test_hostcheck.py`.

- **`--strict` mode** (item 6): `validate` and `gen` accept a `--strict` flag
  that makes the volume-persistence policy rule fatal. Reachability is always
  fatal; by default volume-persistence is advisory (`policy warning:`, exit 0,
  `gen` still emits the unit), so `--strict` opts into treating a
  container-source mount as a hard error (`policy error:`, exit 1).

- **`reachable: false` escape hatch** (item 5, US-SRV-2): a new optional
  `reachable` field (default `true`) lets non-network services (workers, cron,
  batch jobs) omit published ports without tripping the reachability policy.
  `validate`/`gen` skip the reachability rule for `reachable: false` services,
  but volume-persistence is never exempted. The field is parsed by the loader
  (`_as_bool`, accepting `true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off` and
  rejecting anything ambiguous with a clean load error) and roundtrips through
  `render --json`. Resolves the "where declared" ambiguity tracked in item 4.

### Changed

- The generated unit is now a rootless podman **Quadlet** `.container` file
  (`<name>.quadlet`) instead of an inline `ExecStart=podman run …` `.service`.
  The podman options now live on the documented `PodmanArgs=` key inside a
  `[Container]` section (with `Image=`), and the `[Service]` section holds the
  `ExecStart` / `ExecStop` / `Restart=` lifecycle. `PodmanArgs=` is a real,
  loadable podman systemd directive *only* inside a `.container` Quadlet, so this
  is the correct, reboot-safe rootless user-unit representation (see
  implementation.md item 3). Port/volume/env args are emitted sorted for
  deterministic output.

### Added

- Semantic **policy** validation (`pengyos_podman/validate.py::validate_semantic`,
  item 4): the USE-CASES US-SRV-2 guarantees, raised as a distinct
  `PolicyError` (separate from the structural `SchemaError`).
  - **Reachability** — a service must publish at least one port; a service with
    no published ports is rejected (it cannot be reached on the LAN).
  - **Volume persistence** — the *host* side of every volume must be a real host
    data directory; a bind mount whose source is a known image/system directory
    (`/usr/share`, `/etc`, `/lib`, `/bin`, … — `IMAGE_SYSTEM_PATHS`) is flagged,
    because such a source is replaced when the image is updated and the data does
    not survive. Advisory by default (a `policy warning:`); fatal under `--strict`.
  Both rules have their own failing fixtures. `validate`/`gen` run the policy
  layer after the structural layer. Reachability is always fatal (exit 1 with a
  clean "policy error" line, nothing emitted); volume-persistence is advisory by
  default (a "policy warning:" line, exit 0, `gen` still emits the unit).

- Structural schema validation (`pengyos_podman/validate.py::validate_structure`):
  every `ports`/`volumes` entry must be shaped as `"<host>:<container>"` (host
  side optional for ports; an optional `:options` suffix such as `:Z`/`:ro` is
  allowed for volumes) and every `env` entry must be `"key=value"`. Malformed
  shapes are reported as clean validation errors (exit code 1) before any unit
  is emitted, so typos like `volumes: ['/data']` or `ports: ['8080:']` fail
  loudly instead of producing a broken unit.

## [0.1.0]

### Added

- Scaffold of the `pengyos-podman` CLI (US-SRV-2: rootless podman + declarative
  services).
- `pengyos_podman` package with a working CLI:
  - `gen --service service.yaml --out units/` — emit a rootless podman
    `systemd --user` unit for a `service.yaml`.
  - `validate --service service.yaml` — validate a `service.yaml` against the
    schema and report clean errors (no tracebacks).
  - `render --service service.yaml --json` — print the parsed service as JSON.
- Schema model (`model.py`) and YAML loader (`loader.py`) for the
  `name` / `image` / `user` / `restart` / `ports` / `volumes` / `env` fields.
- Validation of required fields and the systemd `Restart=` enum.
- Deterministic golden unit renderer (`render.py`).
- Sample `service.yaml` under `samples/`.
- Test suite (`pytest`) covering loader, validate, render, CLI and the
  GitHub-ready contract.
- GitHub-ready docs: `README.md`, `LICENSE` (MIT, Copyright (c) 2026 Pat Wendorf),
  `CHANGELOG.md`, `.gitignore`, `pyproject.toml`.
