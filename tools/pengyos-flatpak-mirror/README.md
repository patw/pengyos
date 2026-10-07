# pengyos-flatpak-mirror

Self-hosted Flatpak (OSTree) repo manager for [PengyOS](../../README.md).

`pengyos-flatpak-mirror` wraps the `flatpak build-update-repo` / `flatpak` remote
flows behind a declarative `mirror.toml` manifest so the project can **own its
supply chain** and never depend on Flathub staying up. It mirrors pinned apps,
lists refs and their commits, verifies GPG signatures, and prunes old commits.

This is a standard-library-only Python CLI. No external runtime dependencies.

## Why

PengyOS spec §2.5, Lane 3: *own the supply chain and never depend on Flathub
staying up.* A self-hosted OSTree remote gives the build and CI a deterministic,
signed, offline-capable source for the Flatpak apps PengyOS bundles.

## Install

```sh
pip install -e .
```

This installs the `pengyos-flatpak-mirror` console script and the
`pengyos_flatpak_mirror` package. The test extra pulls in pytest:

```sh
pip install -e ".[test]"
```

## Usage

The CLI surface is documented and stable:

```sh
pengyos-flatpak-mirror init --repo DIR
pengyos-flatpak-mirror sync --manifest mirror.toml [--repo DIR]
pengyos-flatpak-mirror list --repo DIR
pengyos-flatpak-mirror verify --gpg-key K --repo DIR
pengyos-flatpak-mirror prune --keep N --repo DIR
```

Run `pengyos-flatpak-mirror --help` (or `--version`) for the full flag list.
`python -m pengyos_flatpak_mirror` is equivalent to the console script.

### Subcommands

| Command | Flags | Purpose |
| --- | --- | --- |
| `init` | `--repo DIR` | Create/initialise an empty OSTree repo to mirror into. |
| `sync` | `--manifest PATH [--repo DIR]` | Mirror the pinned refs described by the manifest. |
| `list` | `--repo DIR [--json]` | List refs and their commits in the local repo. `--json` emits a deterministic machine report. |
| `verify` | `--gpg-key K --repo DIR` | Verify the repo's GPG signatures. |
| `prune` | `--keep N --repo DIR` | Keep only the N most-recent commits per ref. |

### Machine report

`list --json` emits a deterministic, machine-readable report instead of the
human summary. The `(ref, commit)` pairs are sorted by `(ref, commit)` and the
JSON is serialised with sorted keys, so two runs against the same repo produce
byte-identical output — ideal for scripting and CI diffs:

```sh
pengyos-flatpak-mirror list --repo .flatpak/repo --json
```

```json
{
  "repo": ".flatpak/repo",
  "refs": [
    { "commit": "0123456789abcdef", "ref": "org.gnome.Calculator/x86_64/stable" }
  ]
}
```

The pure builder backing this is `engine.list_report_dict(stdout, repo=None)`
(`tests/test_engine.py`).

### Manifest

The `sync` command reads a declarative `mirror.toml` describing the remote, the
apps to mirror, the pinned commit per ref, and which collections to pull.
Manifest validation errors exit non-zero
before any external tool runs.

The schema (see `pengyos_flatpak_mirror/manifest.py`):

| Key | Type | Meaning |
| --- | --- | --- |
| `remote` | string | The remote to mirror from (flatpak/ostree remote name or URL). Required. |
| `remote-name` | string | Local remote name to register; defaults to `remote`. |
| `apps[]` | list | Apps to mirror. Each entry: `id` (required), `commit` (required, hex), `arch` (default `x86_64`), `branch` (default `stable`), `collection-id` (optional). |
| `collections[]` | list | Collection IDs to pull. Omit to derive from `apps`. |

Each pinned app mirrors the OSTree ref `{id}/{arch}/{branch}`. An example lives
at `examples/mirror.example.toml`.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Ran but failed (e.g. manifest validation error, subcommand not yet implemented). |
| `2` | Usage error (bad/missing arguments). |

## Project layout

```
pengyos_flatpak_mirror/
├── __init__.py     # package root; exposes __version__
├── __main__.py     # `python -m pengyos_flatpak_mirror` entry point
├── cli.py          # argparse surface, dispatch, exit codes
├── manifest.py     # load + validate the declarative mirror.toml manifest
├── runner.py       # mockable flatpak/ostree CLI wrapper (golden argv)
└── engine.py       # sync/list orchestration tying a manifest to a Runner
```

## Runner (mockable CLI boundary)

The `flatpak`/`ostree` commands live behind `pengyos_flatpak_mirror/runner.py`, a
thin, mockable wrapper. Every operation builds a deterministic **golden argv**:

| Operation | Golden argv |
| --- | --- |
| `init` | `flatpak build-update-repo --init <repo>` |
| `sync` (remote) | `flatpak remote-add --if-not-exists <remote-name> <remote>` |
| `sync` (ref) | `flatpak update --user --noninteractive --commit=<commit> <id>@<arch>/<branch>` |
| `collection` pull | `flatpak update --user --noninteractive --collection=<collection-id> <remote-name>` |
| `list` | `ostree --repo=<repo> refs` |
| `verify` | `ostree --repo=<repo> verify --gpg-key=<key>` |
| `prune` | `flatpak update --prune` |

`Runner()` defaults to `subprocess.run`; tests inject a fake `runner` callable so
no real flatpak, network, or root is needed. A non-zero exit raises
`CommandError` carrying the `Result` (argv, return code, stdout, stderr). See
`tests/test_runner.py`.

## Engine (sync/list orchestration)

`pengyos_flatpak_mirror/engine.py` implements the two flows that tie a manifest
to the runner:

* **`sync`** registers the remote once (`flatpak remote-add --if-not-exists`),
  mirrors every pinned ref at its exact commit, then pulls every collection
  declared in the manifest (`flatpak update --user --noninteractive
  --collection=<collection-id> <remote-name>`). The golden argv is pinned to the
  remote name, the exact commit, and the collection ids, so re-running issues the
  same calls — the flow is **idempotent** (a second run makes no changes).
* **`list`** runs `ostree --repo=<repo> refs` and parses each `<ref>\t<commit>`
  line into a structured `(ref, commit)` report (blank lines and `#` comments
  are ignored).

The orchestration is unit-tested directly against a fake runner (`tests/test_engine.py`)
— no real flatpak/ostree needed.

## Testing

Tests are first-class and never require the network, root, a real display, or
real hardware:

```sh
pytest -q
```

External tools (`flatpak`, `ostree`) are mocked; real system integration is
opt-in behind `PENGYOS_IT=1`:

```sh
PENGYOS_IT=1 pytest -q
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.

## Status

Implementation status:

| Item | Status |
| --- | --- |
| Scaffold repo | done |
| Define mirror.toml | done |
| Wrap flatpak/ostree CLI behind a runnable interface | done |
| Implement sync/list/verify/prune orchestration | done |
| Pull/derive collections[] from the manifest | done |
| Derive collections from apps' collection-id when omitted | done |
| Emit a machine report; deterministic | done |
| test_github_ready | done |
| Wire `init` through the CLI | done |

The suite is `tests/test_scaffold.py` (package metadata, entry point, CLI
surface), `tests/test_manifest.py` (mirror.toml schema + validation),
`tests/test_runner.py` (mockable flatpak/ostree wrapper + golden argv),
`tests/test_engine.py` (sync flow + idempotency, `ostree refs` parsing), and
`tests/test_github_ready.py` (README/LICENSE/CHANGELOG/.gitignore and
doc/code sync).
