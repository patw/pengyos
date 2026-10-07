# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **GitHub-ready contract** (`tests/test_repo.py`): the repo must ship the
  required docs/metadata files AND have a full green test suite (spec.md
  Verification). `test_github_ready` composes focused per-file checks —
  `test_readme_authored` (README documents all three subcommands, not a stub),
  `test_license_is_full_mit` (canonical MIT permission notice, not just the
  copyright line), `test_changelog_recent` (`[Unreleased]` + dated `0.1.0`),
  `test_gitignore_covers_noise` (`__pycache__/`, `.pytest_cache/`, `/units/`),
  `test_version_consistent` (`__version__` == `pyproject.toml` version and it
  shows up in the shipped docs), and `test_cli_help_runs_in_fresh_subprocess`
  (a cloning reviewer can run the CLI from a checkout). The README now states
  its version so the consistency check is meaningful. 153 tests green (was 151;
  +2 net).
- `verify-restore` subcommand + `pengyos_backup.restorer`: proves a documented
  restore reproduces a known tree (US-SRV-4). `pengyos-backup
  verify-restore --repo DIR --target tmp/ [--dry-run] [--snapshot ID]
  [--binary {restic,rustic}]` restores a snapshot from a fixture repository and
  asserts the restored tree hashes to the snapshot's golden `tree_hash` —
  recovery is verified, not hoped for. Missing/corrupt snapshots fail cleanly
  (exit 1, `error:` on stderr), so the proof has teeth.
  - **Mockable runner** (`restorer.Runner`): `SimulatedRunner` performs the
    offline restore over a committed fixture repo (no restic installed, no
    network, no root); `SubprocessRunner` runs a real `restic`/`rustic restore`
    (integration, opt-in via `PENGYOS_IT=1`); `FakeRunner` records argv and
    scripts pass/fail for tests. Production/test swap is by constructor, so the
    argv that would run is the contract tests assert on.
  - **Content-addressed fixture repo** `fixtures/restic-repo/` (a faithful
    restic subset): `config` marker, `snapshot.json` manifest
    (`{path,size,hash}` entries + golden `tree_hash`), and `content/<sha256>`
    blobs. The golden `tree_hash` is computed by `tree_hash()` over the manifest
    entries, so the fixture round-trips.
  - `tree_hash()` (path+size+content digest) and `scan_tree()` (scan a restored
    target into `TreeEntry`s) are the single source of truth both sides hash by.
  - `RepoError` (in `pengyos_backup.errors`) for a missing/unreadable/invalid
    fixture repo.
  - **Uniform proof teeth.** `verify_restore` now reports a corrupt/invalid/
    missing fixture repo as a clean `VerifyResult(ok=False, error=...)` (it
    catches the `RepoError` from `load_snapshot`) instead of raising, so every
    failure mode — corrupt snapshot metadata, missing repo, missing snapshot id,
    failed restore, tree-hash mismatch — returns a `VerifyResult` to inspect and
    never raises. The CLI's `verify-restore` exits 1 with `error:` on stderr for
    all of them.
- Retention/prune policy encoded as data + validated (`pengyos_backup.config`):
  `Retention.prune_flags()` returns the restic/rustic `forget` keep flags
  (`--keep-last`, `--keep-daily`, `--keep-weekly`, `--keep-monthly`,
  `--keep-yearly`, `--keep-within`) as argv tokens — the single source of truth
  the plan's `forget --prune` argv now consumes (so the emitted service and the
  validator can never disagree). The validator enforces the "never zero kept
  snapshots" semantic rule: a policy whose keep *counts* are all `0` (and at
  least one is named) is rejected with a `ConfigError` naming the offending
  field; an absent/empty retention is still a valid keep-all `forget --prune`
  no-op. `units._forget_argv` refactored to build its keep flags from
  `Retention.prune_flags()`.
- `plan` subcommand: emits the systemd timer + oneshot service that runs
  restic/rustic from a `backup.yaml` into an output directory
  (`pengyos-backup plan --config backup.yaml --out units/`). Emits
  `pengyos-backup.timer` (OnCalendar from the schedule, `RandomizedDelaySec`,
  `Persistent=yes`, `WantedBy=timers.target`), `pengyos-backup.service`
  (`Type=oneshot`, the golden argv — one `backup` + one `forget --prune` per
  target, `OnFailure=pengyos-backup-alert.service`, PengyOS service hardening,
  `WantedBy=multi-user.target`), the `pengyos-backup-alert.service` hook, and
  the executable `pengyos-backup-alert` shell script. New module
  `pengyos_backup.units` owns the rendering and the single-source-of-truth
  golden-argv builders. `--binary {restic,rustic}` selects the backend.
- `render` subcommand: parses a `backup.yaml` and dumps the resulting plan as
  JSON to stdout (`pengyos-backup render --config backup.yaml --json`). The
  `BackupPlan` data model gains `to_dict()` serializers (`_clean` drops unset
  optional fields so the JSON has no `null`s).
- `backup.yaml` planner schema + validator (`pengyos_backup.config`): a
  `BackupPlan` data model over `sources`, `local`/`remote` `targets`,
  `schedule`, and `retention`, validated by `load_config` / `validate`.
  Missing/invalid documents raise `ConfigError` with a message pointing at the
  offending field. PyYAML is used when importable, with a bundled minimal
  fallback reader for bare environments.
- `example.backup.yaml` documenting the schema.

## [0.1.0] - 2026-09-19

### Added
- Initial repo scaffold: `pyproject.toml` (setuptools, `pengyos-backup`
  console script, PyYAML runtime dep, pytest config), `pengyos_backup/` package
  with an argparse CLI (`--version`, `version` subcommand) and `__main__`.
- pytest harness (no network, no root; integration tests opt-in via
  `PENGYOS_IT=1`).
- README, MIT LICENSE, CHANGELOG, .gitignore.
