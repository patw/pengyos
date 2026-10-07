# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-09-22

### Added
- The **GitHub-ready contract** (implementation.md `test_github_ready` item): a
  `tests/test_github_ready.py` suite that asserts the repository is ready to push
  to GitHub at any commit.
  * The four GitHub-ready files (`README.md`, `LICENSE`, `CHANGELOG.md`,
    `.gitignore`) exist and are non-empty;
  * `LICENSE` is MIT, "Copyright (c) 2026 Pat Wendorf", with the standard
    `AS IS` warranty disclaimer;
  * the README starts with a top-level `# ` heading, names the tool, documents
    every subcommand (`emit`, `check`), and cross-links `spec.md` /
    `implementation.md`;
  * `CHANGELOG.md` is Keep-a-Changelog style, newest-first, and its newest
    `## [x.y.z] - YYYY-MM-DD` section matches the package `__version__` (a
    version bump without a matching changelog section fails here — docs stay in
    sync);
  * `.gitignore` covers the Python build/test artifacts;
  * `pyproject.toml`'s `readme` field points at a file that actually exists (the
    sdist/wheel must build on GitHub Actions);
  * the FULL test suite is green — asserted by an opt-in `PENGYOS_IT=1` test that
    re-runs `pytest -q` in a subprocess (off by default; the child clears
    `PENGYOS_IT` so it can never recurse).

### Changed
- Version bumped 0.6.0 -> 0.7.0 (closing the GitHub-ready contract item = minor
  bump); `__init__`, `pyproject.toml`, `CHANGELOG.md` bumped together.
  `pytest -q` green: 134 passed, 2 skipped (the `test_github_ready.py` suite was
  authored in an earlier iteration and simply closed here).

## [0.6.0] - 2026-09-22

### Added
- The **explicitly-out-of-scope facts** (implementation.md item 6): the
  real-hardware classes the VM-testable smoke matrix does **not** verify — GPU
  acceleration, audio devices, Bluetooth, and suspend/resume — encoded as a
  documented list in the new module `pengyos_desktop_session.oos`.
  * `oos.OUT_OF_SCOPE` is a frozen, canonically ordered `tuple[OutOfScopeFact,
    ...]` — one `OutOfScopeFact(category, what, why)` per real-hardware class
    (`GPU`, `audio`, `Bluetooth`, `suspend/resume`).
  * `oos.render_out_of_scope()` renders them as the "real-hardware, not
    verified here" report section (header naming the four categories + one
    bullet per fact). `is not None` (not `or`) fall-back: an explicit empty
    list is a valid override; only a missing arg uses the default.
  * The check engine now threads these onto `CheckOutcome.out_of_scope`
    (default = `OUT_OF_SCOPE`, overridable via `check_boot_log(...,
    out_of_scope=...)`); `render_findings` appends the section on **every**
    verdict — PASS, FAIL, and SKIP — so the facts are reported, never silently
    omitted; `outcome_to_dict` includes them in the JSON surface.

### Changed
- The `check` report (human-readable and `--json`) now always lists the
  out-of-scope facts beneath the verdict.
- Version bumped 0.5.0 -> 0.6.0 (new feature = minor bump); `__init__`,
  `pyproject.toml`, `CHANGELOG.md` bumped together. `pytest -q` green: 115 ->
  134 passed, 2 skipped.

## [0.5.0] - 2026-09-22

### Added
- The **opt-in ssh probe** (implementation.md item 5): `pengyos-desktop-session
  check --ssh HOST` now collects the same VM-testable session facts over ssh
  from a running target and checks them with the same present/absent engine
  (same facts, same no-vacuous-pass semantics).
  * The probe is gated behind `PENGYOS_IT=1` (`check.SSH_PROBE_ENABLED_ENV`);
    without it the CLI skips honestly (`EXIT_SKIP`, exit code `4`) and never
    invokes ssh.
  * `check.collect_ssh(host, *, ssh_runner=...)` returns a
    `(probe_output, skip_reason)` tuple and skips honestly on: the opt-in gate
    being off, a blank host, a non-zero ssh exit (unreachable host / auth
    failure / no ssh), or an empty probe output. `ssh_runner` is injectable, so
    the unit suite never touches the network, ssh, or a real target.
  * `check.check_ssh(host, ...)` runs the engine over the captured output and,
    on a skip, returns a `CheckOutcome` with `ok=False`, an empty findings
    tuple, and a populated `skip` reason — reported honestly, never a bogus
    PASS or a findings-based FAIL. `render_findings` gains a `RESULT: SKIP`
    report and `outcome_to_dict` a `skip` field.
  * A new `EXIT_SKIP` (4) exit code for "the check could not run".
  * Committed fixtures `tests/fixtures/boot_ssh.log` (all facts present) and
    `tests/fixtures/boot_ssh_missing_pipewire.log` (pipewire absent), plus
    `tests/test_ssh.py`.

### Changed
- The `check --ssh` CLI returns `EXIT_SKIP` (was `EXIT_NOT_IMPLEMENTED`) when
  the probe cannot run, `EXIT_OK` / `EXIT_FAIL` when it runs.
- Version bumped 0.4.0 -> 0.5.0 (new feature = minor bump); `__init__`,
  `pyproject.toml`, `CHANGELOG.md` bumped together. `pytest -q` green: 97 ->
  115 passed, 2 skipped.

## [0.4.0] - 2026-09-22

### Added
- The **VM-testable session-fact check engine** (`pengyos_desktop_session.check`,
  implementation.md item 4). The `check --log FILE` subcommand now verifies the
  subset of session facts QEMU+OVMF can actually observe, over a boot log:
  * `cosmic-comp` started,
  * a wayland socket exists,
  * `cosmic-session` / panel up,
  * `pipewire` / `wireplumber` running,
  * portals responding.
* Each fact carries its **present patterns** (derived from the emitted session
  model, so the wayland-socket pattern tracks the operator-decided socket
  path); the engine **fails if a present pattern is missing** — a fact cannot
  pass on a log that does not actually show it (no vacuous pass). An empty or
  irrelevant log fails every fact.
* `check_boot_log` (pure, no I/O), `render_findings` (PASS/FAIL report in
  canonical order + verdict line), and `outcome_to_dict` (JSON), plus the
  `--json` output for the CLI. The `check --ssh` probe stays pending
  (implementation.md item 5).
* Committed boot-log fixtures under `tests/fixtures/` (`boot_pass.log`,
  `boot_missing_portal.log`, `boot_empty.log`).

### Changed
- The `check` CLI returns `EXIT_OK` when all facts pass and `EXIT_FAIL` when any
  present pattern is missing; a missing boot-log file is a usage error
  (`EXIT_USAGE`). The `check --ssh` handler still returns
  `EXIT_NOT_IMPLEMENTED`.
* Tests: 71 -> 97 passed, 2 skipped.

## [0.3.0] - 2026-09-22

### Added
- The `emit` CLI (implementation.md item 3): `pengyos-desktop-session emit
  --out DIR [--json]` now writes the COSMIC session definition to disk — the
  greetd config, cosmic-greeter session entry, systemd unit/launcher, and
  environment — under `--out`, in canonical (stable) order, byte-for-byte
  deterministic. Re-emitting is idempotent and leaves unrelated files in the
  overlay untouched.
- `pengyos_desktop_session.session.write_session` — the I/O layer that renders
  the artifacts (via the pure emitter in `pengyos_desktop_session.emit`) and
  writes them under a target directory; plus `written_paths` as a convenience
  path resolver.
- Tests for the emit I/O layer + CLI: files land on disk with the expected
  content, canonical ordering, determinism across runs, idempotent re-emit,
  unrelated-file preservation, override flow-through, and the human-readable /
  `--json` CLI output.

## [0.2.0] - 2026-09-22

### Added
- The COSMIC session **data model** (`pengyos_desktop_session.model`): a frozen
  `CosmicSession` dataclass capturing the operator-decided Desktop session
  facts — the greetd config (command `cosmic-greeter`, user, wayland-socket
  env var), the cosmic-greeter session entry (compositor = `cosmic-comp`,
  exec = `cosmic-session`, per SPEC 6.9), the systemd unit name
  (`cosmic-session@.service`), the wayland socket path, and the session
  environment.
- The **deterministic emitter** (`pengyos_desktop_session.emit`): renders the
  five session-definition artifacts — `greetd.toml`, `cosmic.desktop`,
  `cosmic-session@.service`, `cosmic-session-launch.sh`, `cosmic-session.env`
  — as byte-for-byte reproducible strings (sorted keys, single trailing
  newline, tool-identification header, canonical ordering). Pure, no I/O.
- Tests for the model + emitter (defaults, determinism, per-artifact content,
  overrides, golden content, valid-TOML greetd config).

## [0.1.0] - 2026-09-22

### Added
- Scaffold for `pengyos-desktop-session` — the COSMIC session + a VM-testable
  smoke matrix (US-DSK-1) for PengyOS Desktop:
  * package `pengyos_desktop_session` with `__init__`, `__main__`, and a
    documented `cli` surface (`emit`, `check`, `--version`);
  * `pyproject.toml` with the `pengyos-desktop-session` console-script entry
    point and an offline pytest config;
  * GitHub-ready docs: `README.md`, `LICENSE` (MIT, "Copyright (c) 2026 Pat
    Wendorf"), `CHANGELOG.md`, `.gitignore`;
  * scaffold + GitHub-ready + CLI-surface tests (all green, no root, no
    network, no display).
