# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-09-15

### Added
- **`init` wired through the CLI** (implementation.md item: "Wire `init` through
  the CLI"). The `init` subcommand now runs its golden argv
  `flatpak build-update-repo --init <repo>` via the runner instead of reporting
  "not implemented yet" (exit 1); it reports a clean success on exit 0 and a
  clean exit 1 on a non-zero external exit (`CommandError` caught in `main`). The
  golden argv and the mockable execution boundary were already in
  `pengyos_flatpak_mirror/runner.py` (`Runner.init_repo`); this subtask only
  connected the CLI body (`_cmd_init`) to it. Standard library only.

## [0.7.0] - 2026-09-15

### Added
- **Deterministic machine report** (implementation.md item: "Emit a machine
  report; deterministic"). `list` now accepts `--json`, which emits a
  machine-readable report instead of the human summary. The report is the
  JSON-ready twin of the existing `list` report: a `{"refs": [{"ref": r,
  "commit": c}, ...]}` object (plus a top-level `repo` key when given). Where
  the human `list` faithfully preserves `ostree refs` output order, the machine
  report is **deterministic** — the `(ref, commit)` pairs are sorted by
  `(ref, commit)` and serialised with `json.dumps(..., sort_keys=True)`, so two
  runs against the same repo are byte-identical regardless of the order the
  underlying tool emitted the refs in. The pure builder
  `engine.list_report_dict(stdout, repo=None)` backs it and is unit-tested
  directly; the CLI flag is exercised via a fake runner. Standard library only.

## [0.6.0] - 2026-09-15

### Added
- **Derive `collections[]` from apps when omitted** (implementation.md item 6):
  the manifest schema said "omit to derive from apps", and now it does. When a
  `mirror.toml` omits the `collections` key entirely, `load_manifest` derives the
  collection IDs to pull from each app's `collection-id`, in manifest order,
  de-duplicated with first appearance winning; apps without a `collection-id`
  contribute nothing. The loader now uses a sentinel to distinguish an *omitted*
  `collections` key (derive) from an explicit `collections = []` (pull nothing).
  `sync` pulls the derived collections unchanged — the golden argv is still pinned
  to the collection id and remote name, so the flow stays **idempotent**. The
  CLI `sync` output reports the derived collection counts. Standard library only.

## [0.5.0] - 2026-09-15

### Added
- **Collection pulling in `sync`** (implementation.md item 5): `sync` now pulls
  the `collections[]` declared in the manifest, in addition to mirroring the
  pinned apps. A new mockable runner operation,
  `Runner.pull_collection(collection_id, remote_name)`, builds the golden argv
  `flatpak update --user --noninteractive --collection=<collection-id>
  <remote-name>`; `engine.sync` calls it once per manifest collection after the
  app mirror pass, and the new `SyncResult.collections_pulled` field reports what
  was pulled. The flow stays **idempotent** (the argv is pinned to the collection
  id and remote name). The CLI `sync` output now reports both the ref and
  collection counts. Standard library only.

## [0.4.0] - 2026-09-15

### Added
- **Sync/list/verify/prune orchestration** (implementation.md item 4): the CLI
  now wires the four implemented subcommands through
  `pengyos_flatpak_mirror/engine.py` + `runner.py`. `sync` registers the remote
  once (`flatpak remote-add --if-not-exists`) then mirrors every pinned ref at
  its exact commit — the golden argv is pinned to the remote name and commit, so
  the flow is **idempotent** (a second run makes no changes). `list` parses
  `ostree --repo=<repo> refs` output into structured `(ref, commit)` pairs.
  `verify` and `prune` run their golden argv and report. A `ManifestError`
  (bad manifest) or `CommandError` (non-zero exit) is caught in `main` and
  printed as a clean exit 1. Standard library only.

## [0.3.0] - 2026-09-15

### Added
- **`flatpak`/`ostree` runner** (implementation.md item 3): a mockable
  execution boundary in `pengyos_flatpak_mirror/runner.py`. `Runner` builds a
  deterministic **golden argv** for each operation — `init` (`flatpak
  build-update-repo --init`), `sync` (`flatpak remote-add --if-not-exists` +
  `flatpak update --user --noninteractive --commit=<commit> <id>@<arch>/<branch>`),
  `list` (`ostree --repo=<repo> refs`), `verify` (`ostree --repo=<repo> verify
  --gpg-key=<key>`), and `prune` (`flatpak update --prune`) — and hands argv to
  an injectable `runner` callable (defaults to `subprocess.run`). Tests inject a
  fake so no real flatpak, network, or root is needed; a non-zero exit raises
  `CommandError` carrying the `Result`. Standard library only.

## [0.2.0] - 2026-09-15

### Added
- **`mirror.toml` manifest** (implementation.md item 2): the declarative
  manifest schema and loader in `pengyos_flatpak_mirror/manifest.py`. It defines
  the remote to mirror from, the apps to mirror, the pinned commit per ref
  (`{id}/{arch}/{branch}`), and which collections to pull. Parsing uses the
  standard-library `tomllib`; validation raises `ManifestError` with an
  actionable message for every missing/invalid key. Includes a committed
  example at `examples/mirror.example.toml`.

## [0.1.0] - 2026-09-15

### Added
- **Scaffold** (implementation.md item 1): the `pengyos_flatpak_mirror` package,
  the `pengyos-flatpak-mirror` console-script entry point, and a documented CLI
  surface (`init`, `sync`, `list`, `verify`, `prune`) wired through
  `build_parser`/`main`. Standard-library only; no external runtime
  dependencies. Includes README, LICENSE (MIT), CHANGELOG, `.gitignore`, and a
  green test suite (`test_scaffold`, `test_github_ready`).
