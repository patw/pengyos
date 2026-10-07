# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-09-22

### Added
- **The human-artistry rule** (implementation.md item 8): `pengyos-desktop-curation
  lint [--json]` enforces that the curated default-app set is
  **human-ratified, never AI-decided** (spec Principle 8). Every curated entry
  must carry a non-empty `rationale` and be `human_signed = true`; a clean set
  passes (exit 0) and any violation fails (exit 1), printing the offending
  `#<i> <app>: <reason>`. The logic lives in
  `pengyos_desktop_curation/lint.py` (`LintViolation`, `LintReport`, `lint`,
  `assert_clean`, `lint_default`, `lint_path`) as pure, deterministic logic over
  the curated set — no I/O — so it is easy to unit-test. The loader
  (`pengyos_desktop_curation.data`) still validates *structure* (required
  fields, lane, ref); this module validates the *artistry rule* — the loader
  deliberately does not reject an unsigned entry, so a data file can be edited
  with a missing signature and caught here by `lint`. The committed
  `curated.toml` is fully compliant, so `lint` passes against it. Includes
  `tests/test_lint.py`.

## [0.6.0] - 2026-09-22

### Added
- **The .desktop/mime integration plan** (implementation.md item 7): the third
  deliverable of the curated set — *which everyday category integrates how* — as
  committed, declarative data in `mime-integration.toml`, modelled by
  `pengyos_desktop_curation/mime.py` (`IntegrationEntry`, `IntegrationPlan`,
  loader, and the emitters). `pengyos-desktop-curation emit --out DIR --mime`
  now additionally emits a real `mimeapps.list` (the freedesktop
  `xdg-mime` default-associations file — `[Default Applications]` +
  `[Added Applications]` sections, valid INI) and the machine-readable
  `integration-plan.json`, alongside the Flatpak ref list and mirror manifest.
  The plan records, per category, the `.desktop` id that becomes the default,
  the MIME types it covers, and the integration **mechanism**
  (`mimeapps_list` / `xdg_desktop_dirs` / `systemd_mime`). Categories that are
  not file handlers (calculator, screenshots) integrate by being discoverable
  rather than claiming a MIME association; the emitter omits their (empty)
  rows. The plan cross-checks against the curated set (app + ref per category).
  Pure, deterministic, standard-library-only; `emit` gained the `--mime` flag
  and `EmitResult` gained the mime payloads. Includes `tests/test_mime.py`.

## [0.5.0] - 2026-09-22

### Added
- **Commit persistence** (implementation.md item 6): `pengyos-desktop-curation
  emit --out DIR --pin --refresh` now **persists** the live Flathub commits back
  into the committed `flathub-commits.toml` database, so the committed database
  self-updates with real commits instead of requiring a manual rewrite. Before,
  `--refresh` fetched live commits only to resolve the manifest in memory (the
  committed cache stayed stale). The merge is additive — refs in the database
  that are not curated are preserved — and a ref whose fetched commit is not a
  real 64-hex commit fails loudly (exit 1) *before* anything is written, so a bad
  fetch never corrupts the committed database. `--pin` alone (without `--refresh`)
  never rewrites the file. The logic lives in
  `pengyos_desktop_curation/resolve.py` (`refresh`, `write_db`, `render`) as
  pure, deterministic, header-preserving serialisation — no network or I/O at
  resolution time — so it is easy to unit-test (the live Flathub boundary is
  faked). Includes `tests/test_refresh.py`.

## [0.4.0] - 2026-09-22

### Added
- **Commit resolution** (implementation.md item 5): `pengyos-desktop-curation
  emit --out DIR --pin [--refresh]` resolves the `UNPINNED` commit placeholder in
  the emitted mirror manifest to a **real Flathub commit**, producing a
  sync-ready manifest the `pengyos-flatpak-mirror` loader accepts (that loader
  rejects the `UNPINNED` marker: `commit must be a non-empty hex string`). By
  default `--pin` resolves against the committed
  `flathub-commits.toml` database; `--refresh` fetches each ref's current commit
  live from Flathub instead. Resolving a ref with no known commit is a hard
  failure (exit 1) — the tool never writes an unresolved manifest to the
  mirror. The logic lives in `pengyos_desktop_curation/resolve.py`
  (`CommitDatabase`, `resolve_manifest`, `pin_manifest`, `fetch_commit`) as pure,
  deterministic logic over the seed manifest — no network or I/O at resolution
  time — so it is easy to unit-test (the live Flathub boundary is faked).
  Includes `tests/test_resolve.py`.

## [0.3.0] - 2026-09-22

### Added
- **`emit`** (implementation.md item 4): `pengyos-desktop-curation emit --out
  DIR [--json]` writes two deterministic artifacts for `pengyos-flatpak-mirror`
  to consume. `flatpak-refs.txt` is the Flatpak ref list (one ref per line —
  the `ref` of every `flatpak`-lane curated entry, in source order), and
  `mirror.toml` is the declarative mirror manifest (`remote = "flathub"` /
  `remote-name = "pengyos"`, each `flatpak`-lane entry as an `apps[]` entry with
  a `collection-id` derived from the ref). The curated set carries Flathub refs
  but **no pinned commits**, so every app is pinned to the placeholder commit
  `UNPINNED` — a marker the mirror tool resolves to the real commit at sync
  time. Output is byte-identical across runs. The logic lives in
  `pengyos_desktop_curation/emit.py` (`flatpak_refs`, `mirror_manifest`,
  `emit`, `EmitResult`) as pure, deterministic logic over the curated set — no
  I/O — so it is easy to unit-test. Includes `tests/test_emit.py`.

## [0.2.0] - 2026-09-22

### Added
- **Coverage report** (implementation.md item 3): `pengyos-desktop-curation
  coverage [--json]` reports gaps against the US-DSK-2 "everyday apps covered"
  list. A missing everyday category is a **reported gap**, not a crash; a set
  that covers every everyday category passes. The report lives in
  `pengyos_desktop_curation/coverage.py` (`CoverageReport` / `CategoryStatus`,
  `build_report`) as pure, deterministic logic over the curated set — no I/O —
  so it is easy to unit-test. The CLI renders it as human-readable text or
  sorted-key JSON and exits `0` on a complete set, `1` on a gap set (a gap is a
  failure per the README exit-code contract). Includes `tests/test_coverage.py`.

## [0.1.0] - 2026-09-22

### Added
- **Scaffold** (implementation.md item 1): the `pengyos_desktop_curation`
  package, the `pengyos-desktop-curation` console-script entry point, and a
  documented CLI surface (`list`, `coverage`, `emit`) wired through
  `build_parser`/`main`. The three subcommands are registered with their
  documented flags (`--json`, `emit --out DIR`) even though their bodies are not
  yet implemented — an unimplemented body reports a clean "not implemented yet"
  (exit 1) rather than a usage error or a crash. Standard library only; no
  external runtime dependencies. Includes README, LICENSE (MIT), CHANGELOG,
  `.gitignore`, and a green test suite (`test_scaffold`, `test_github_ready`).
- **Curated set as declarative data** (implementation.md item 2): the curated
  default-app set is committed as data in `curated.toml` (category → app → lane
  → ref → rationale → `human_signed`) and modelled by
  `pengyos_desktop_curation/data.py` — an immutable `CuratedEntry`, an ordered
  `CuratedSet` (by-category queries, flatpak refs, validation), and a
  standard-library TOML/JSON loader. Every committed entry has a rationale and
  `human_signed = true` (spec Principle 8); the loader validates required
  fields, the lane (`flatpak`/`appimage`/`recipe`), and that a `flatpak` entry
  carries a ref. `EVERYDAY_CATEGORIES` (browser, terminal, files, editor, media,
  office, PDF, archive, calculator, screenshots) is the acceptance horizon the
  later coverage report checks against. Includes `tests/test_data.py`.
