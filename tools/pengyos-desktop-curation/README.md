# pengyos-desktop-curation

Curated PengyOS Desktop default-application set as **declarative data**, for
[PengyOS](../../README.md).

The curated Desktop default-application set is stored as *data*
(category → app → lane → ref → rationale → `human_signed`) and emitted as:

* a **Flatpak ref list**,
* a **self-hosted-mirror manifest** (consumed by
  [`pengyos-flatpak-mirror`](../pengyos-flatpak-mirror/)), and
* a **coverage report** against the US-DSK-2 "everyday apps covered" list
  (browser, terminal, files, editor, media, office, PDF, archive, calculator,
  screenshots).

The app set is a curated, reproducible, human-ratified decision (spec Principle
8): it is never chosen unaided by the tool.

This is a standard-library-only Python CLI. No external runtime dependencies.

## Why

USE-CASES US-DSK-2 (Flatpak primary lane + curated defaults) and US-DSK-5
(human taste): the default app set is a curated, declarative, reproducible
decision, and the everyday-coverage list is a testable acceptance criterion.

## Install

```sh
pip install -e .
```

This installs the `pengyos-desktop-curation` console script and the
`pengyos_desktop_curation` package. The test extra pulls in pytest:

```sh
pip install -e ".[test]"
```

## Usage

The CLI surface is documented and stable:

```sh
pengyos-desktop-curation list [--json]
pengyos-desktop-curation coverage [--json]
pengyos-desktop-curation emit --out DIR [--json]
pengyos-desktop-curation emit --out DIR --mime [--json]
pengyos-desktop-curation lint [--json]
```

Run `pengyos-desktop-curation --help` (or `--version`) for the full flag list.
`python -m pengyos_desktop_curation` is equivalent to the console script.

### Subcommands

| Command | Flags | Purpose |
| --- | --- | --- |
| `list` | `--json` | Print the curated default-app set. `--json` emits a machine report. |
| `coverage` | `--json` | Report gaps against the US-DSK-2 everyday list. `--json` emits a machine report. |
| `emit` | `--out DIR` `--pin` `--refresh` `--mime` `--json` | Write the Flatpak ref list + mirror manifest into `DIR`. `--pin` resolves the commit placeholder to a real Flathub commit (sync-ready); `--refresh` fetches live from Flathub; `--mime` also emits the .desktop/mime integration plan (`mimeapps.list` + `integration-plan.json`). |
| `lint` | `--json` | Enforce the human-artistry rule: every curated entry must carry a rationale and be `human_signed` (spec Principle 8). Passes (exit 0) on a clean set; fails (exit 1) on any violation. |

### Data model

The curated set is committed declarative data (see
`pengyos_desktop_curation/data.py`). Every entry carries:

* `category` — the everyday category (browser, terminal, files, editor, media,
  office, PDF, archive, calculator, screenshots).
* `app` — the application name.
* `lane` — the delivery lane: `flatpak`, `appimage`, or `recipe`.
* `ref` — the Flatpak ref (for the `flatpak` lane).
* `rationale` — why this app is curated (human-authored).
* `human_signed` — `true` when a human ratified the entry (spec Principle 8).

### Coverage report

`coverage` reports gaps against the US-DSK-2 everyday list. A missing everyday
category is a **reported gap**, not a crash; a complete set passes. The
everyday categories are: browser, terminal, files, editor, media, office, PDF,
archive, calculator, screenshots.

### Emit

`emit --out DIR` writes two deterministic artifacts for
`pengyos-flatpak-mirror` to consume:

* a **Flatpak ref list** (`flatpak-refs.txt`, one ref per line — the `ref` of
  every `flatpak`-lane curated entry, in source order), and
* a **mirror manifest** (`mirror.toml`, the declarative manifest the mirror
  reads).

The mirror manifest sets `remote = "flathub"` / `remote-name = "pengyos"` and
turns each `flatpak`-lane entry into an `apps[]` entry (`id = <ref>`, with a
`collection-id` derived from the ref). The curated set carries Flathub refs but
**no pinned commits**, so every app is pinned to the placeholder commit
`UNPINNED` — a marker the mirror tool resolves to the real commit at sync time
(the curated set is human-ratified *which apps*, not *which commit of each*).
Output is deterministic: two runs against the same committed data are
byte-identical.

### Commit resolution (`emit --pin`)

The `pengyos-flatpak-mirror` loader **rejects** the `UNPINNED` placeholder
(`commit must be a non-empty hex string`), so before it can sync the placeholder
must be resolved to a real Flathub commit. `emit --pin` does exactly that: it
resolves each `UNPINNED` to the real commit from the committed
`flathub-commits.toml` database and writes a **sync-ready** manifest (every
`commit` is a real 64-hex OSTree commit). `--refresh` fetches each ref's current
commit live from Flathub instead of the committed database. Without `--pin`,
`emit` writes the seed manifest (`UNPINNED`), which the mirror tool resolves at
sync time. Resolving a ref with no known commit
is a hard failure (exit 1) — the tool never writes an unresolved manifest to the
mirror. The resolution logic lives in
`pengyos_desktop_curation/resolve.py` (`CommitDatabase`, `resolve_manifest`,
`pin_manifest`, `fetch_commit`).

### Commit persistence (`emit --refresh`)

The committed `flathub-commits.toml` is a **cache** of the current Flathub
stable commit per ref — and that commit changes with every Flathub rebuild.
`emit --refresh` does more than resolve: it fetches **every curated ref's**
current Flathub commit live, merges those fresh values into the committed
`flathub-commits.toml`, and **writes it back** — so
the committed database self-updates with real commits instead of requiring a
manual rewrite. The merged (fresh) database is then what resolves the manifest,
and the refreshed `last_verified` date is reported. The merge is additive: refs
in the database that are not curated are preserved. A ref whose fetched commit is
not a real 64-hex commit fails loudly (exit 1) *before* anything is written, so a
bad fetch never corrupts the committed database. `--pin` alone (without
`--refresh`) never rewrites the file — the committed database stays the source of
truth for a plain `--pin`.

### .desktop/mime integration plan (`emit --mime`)

The curated set says *which* apps are the defaults; the **.desktop/mime
integration plan** says *how* each one is wired into the freedesktop
default-application machinery system-wide — the third deliverable of the curated
set. It is committed declarative data in
`mime-integration.toml` and modelled by `pengyos_desktop_curation/mime.py`
(`IntegrationEntry`, `IntegrationPlan`, loader, emitters): every entry records
the `.desktop` id that becomes the default, the MIME types it covers, and the
integration **mechanism** (`mimeapps_list` / `xdg_desktop_dirs` / `systemd_mime`).

`emit --out DIR --mime` additionally writes two artifacts alongside the Flatpak
ref list and mirror manifest:

* a **`mimeapps.list`** — the freedesktop `xdg-mime` default-associations file,
  with `[Default Applications]` (`mime-type = app.desktop`) and
  `[Added Applications]` (`app.desktop = type1;type2;`) sections. It is a real,
  valid INI file (parseable by Python's stdlib `configparser`) that a system
  integrator can drop into `/etc/xdg/mimeapps.list`, and
* **`integration-plan.json`** — the machine-readable plan (one object per
  category).

Categories that are not file handlers (calculator, screenshots) are
discoverable-only: they integrate by being advertised in the app grid rather
than claiming a MIME association, so the emitter omits their (empty) rows. The
plan cross-checks against the curated set — every category's `app` and `ref`
match the curated entry. `--json` reports the mime payloads.

### Human-artistry rule (`lint`)

The curated set is **human-ratified, never AI-decided** (spec Principle 8), so
`lint` enforces that no entry slips through un-ratified: every curated entry
must carry a non-empty `rationale` **and** be `human_signed = true`.
`pengyos-desktop-curation lint [--json]` walks the committed `curated.toml` and
reports every violation as `#<i> <app>: <reason>` (`missing rationale` /
`not human_signed`). A clean set passes (exit 0); any violation fails (exit 1) —
a dirty curated set is a build failure, never a silent crash.

The loader (`pengyos_desktop_curation/data.py`) validates *structure* (required
fields, lane, ref); `lint` validates the *artistry rule*. The loader
deliberately does not reject an unsigned entry, so a data file can be edited
with a missing signature and caught here by `lint` rather than by the loader.
The logic lives in `pengyos_desktop_curation/lint.py` (`LintViolation`,
`LintReport`, `lint`, `assert_clean`, `lint_default`). The committed set is
fully compliant, so `lint` passes against it today.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Ran but failed (e.g. a subcommand not yet implemented, or a coverage gap). |
| `2` | Usage error (bad/missing arguments). |

## Project layout

```
.
├── curated.toml              # committed declarative data
├── pengyos_desktop_curation/
│   ├── __init__.py           # package root; exposes __version__
│   ├── __main__.py           # `python -m pengyos_desktop_curation` entry point
│   ├── cli.py                # argparse surface, dispatch, exit codes
│   ├── data.py               # curated-set model + loader
│   ├── coverage.py           # coverage report vs the US-DSK-2 everyday list (#3)
│   ├── emit.py               # Flatpak ref list + mirror manifest (#4) + emit --mime (#7)
│   ├── resolve.py            # resolve the UNPINNED commit placeholder (#5) + persist --refresh fetches (#6)
│   ├── mime.py               # .desktop/mime integration plan (model, loader, emit) (#7)
│   └── lint.py               # human-artistry rule: rationale + human_signed (#8)
├── flathub-commits.toml      # committed ref -> commit database (#5 reads, #6 refreshes)
├── mime-integration.toml     # committed .desktop/mime integration plan (#7)
├── tests/
│   ├── conftest.py           # shared fixtures (repo_root, integration)
│   ├── test_data.py          # curated-set model, loader, validators
│   ├── test_coverage.py      # coverage report: gaps vs complete, JSON/text, exit codes
│   ├── test_emit.py          # emit: ref list + mirror manifest, determinism, CLI
│   ├── test_resolve.py       # resolve UNPINNED -> real Flathub commit, CLI --pin
│   ├── test_refresh.py       # emit --refresh persists live commits back to flathub-commits.toml (#6)
│   ├── test_mime.py          # .desktop/mime integration plan: model, loader, emit --mime (#7)
│   ├── test_lint.py          # human-artistry rule: rationale + human_signed (#8)
│   ├── test_github_ready.py  # README/LICENSE/CHANGELOG/.gitignore + doc/code sync
│   └── test_scaffold.py      # package metadata, entry point, CLI surface
```

The curated set itself is committed as declarative data in `curated.toml`
(category → app → lane → ref → rationale → `human_signed`) and modelled by
`pengyos_desktop_curation/data.py`.

## Testing

Tests are first-class and never require the network, root, a real display, or
real hardware:

```sh
pytest -q
```

External tools are mocked; real system integration is opt-in behind
`PENGYOS_IT=1`:

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
| Model the curated set as committed data | done |
| Implement coverage | done |
| Implement emit | done |
| Resolve the UNPINNED commit placeholder | done |
| Persist `emit --refresh` fetches back into flathub-commits.toml | done |
| Emit the .desktop/mime integration plan | done |
| Enforce the human-artistry rule | done |
| test_github_ready | done |

The suite is `tests/test_scaffold.py` (package metadata, entry point, CLI
surface), and `tests/test_github_ready.py` (README/LICENSE/CHANGELOG/.gitignore
and doc/code sync).
