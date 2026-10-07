# PengyOS flatpak-mirror — self-hosted Flatpak repo manager

## Summary
Manage a self-hosted Flatpak (OSTree) remote: mirror apps, pin refs, verify signatures, prune.

## Purpose
SPEC §2.5 Lane 3: own the supply chain and never depend on Flathub staying up.

## Deliverable
A Python CLI `pengyos-flatpak-mirror` that wraps `flatpak build-update-repo`/`flatpakrepo` flows behind a declarative mirror manifest, with a mockable runner.

## Interface
- pengyos-flatpak-mirror init --repo DIR | sync --manifest mirror.toml | list [--json] | verify --gpg-key K | prune --keep N

## Runner (mockable CLI boundary)
The `flatpak`/`ostree` commands are wrapped in `pengyos_flatpak_mirror/runner.py`.
`Runner` builds a deterministic **golden argv** per operation and hands argv to an
injectable `runner` callable (default `subprocess.run`); tests inject a fake so no
real flatpak/network/root is needed. Golden argv:

- `init`  → `flatpak build-update-repo --init <repo>`
- `sync` remote → `flatpak remote-add --if-not-exists <remote-name> <remote>`
- `sync` ref  → `flatpak update --user --noninteractive --commit=<commit> <id>@<arch>/<branch>`
- `collection` pull → `flatpak update --user --noninteractive --collection=<collection-id> <remote-name>`
- `list`  → `ostree --repo=<repo> refs`
- `verify` → `ostree --repo=<repo> verify --gpg-key=<key>`
- `prune` → `flatpak update --prune` (records the requested `keep` on the result)

A non-zero exit raises `CommandError` carrying the `Result` (argv, return code,
stdout, stderr). `prune` has no real per-ref `--keep` flag in `flatpak`/`ostree`,
so `keep` is recorded on the result for later enforcement.

**Collections.** `sync` pulls every collection in `manifest.collections`. When the manifest omits `collections` entirely, the loader derives them from each app's `collection-id` — manifest order, de-duplicated (first appearance wins); apps without a `collection-id` contribute nothing. An explicit `collections = []` pulls nothing. Each pull is `flatpak update --user --noninteractive --collection=<collection-id> <remote-name>` — the golden argv is pinned to the collection id and remote name, so `sync` stays idempotent.

## Machine report

`list --json` emits a deterministic, machine-readable report instead of the
human summary. `engine.list_report_dict(stdout, repo=None)` builds a
`{"refs": [{"ref": r, "commit": c}, ...]}` dict (plus a top-level `repo` key
when given). The `(ref, commit)` pairs are **sorted by `(ref, commit)`** and the
report is serialised with `json.dumps(..., sort_keys=True)` — so two runs
against the same repo are byte-identical regardless of the order `ostree refs`
happened to emit them in. The human `list` still preserves `ostree refs` output
order (faithful, lossless); only the machine report is sorted.

## Verification (tests are first-class)
- command construction for init/sync/list/verify/prune (golden argv).
- machine report: `list_report_dict` is deterministic (sorted refs/keys) and
  matches the CLI `list --json` output.
- manifest validation errors.
- sync is idempotent (second run makes no changes) against a mocked runner.
- command construction for init/sync/list/verify/prune (golden argv).
- manifest validation errors.
- sync is idempotent (second run makes no changes) against a mocked runner.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- No real flatpak needed in unit tests.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Uploading to Flathub
- Building Flatpaks from source

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
