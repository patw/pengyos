# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Tests

- `test_github_ready` GitHub-ready contract: hardened the README docs/code sync
  checks (the `implementation.md` item *"test_github_ready"*). The README layout
  is now verified by parsing its box-drawing `├──`/`└──` *tree entries* rather
  than a loose substring match, so a module/test that is only a *substring* of
  some other documented name (e.g. `analyse.py` inside `test_analyse.py`) can no
  longer slip past as "documented". Added unit tests for the tree parser
  (`test_readme_layout_tree_names`). The full suite is green (135 passed) and the
  opt-in `PENGYOS_IT=1` integration gate re-runs the whole suite in a subprocess
  and asserts exit 0.

### Added

- `analyse` now **resolves** each ELF object's declared `DT_NEEDED` libraries
  against the `--libdirs` set into a resolved-vs-missing report (the
  `implementation.md` item *"Report resolved vs missing libs against a libdir
  set"*). Until now `analyse` only listed declared dependencies.
  - `analyse.LibraryResolver` classifies a lib as **resolved** iff a file of
    that exact soname name exists in a libdir (a pure `os.path.lexists` check —
    a symlinked soname counts as present, matching how the loader resolves by
    name) and **missing** otherwise. Nothing is executed: resolution only stats
    the filesystem, never runs `ldd` or the package's interpreter.
  - A lib is matched **exactly** (case-sensitive, no version resolution —
    resolution of versioned sonames is a non-goal per spec §Non-goals).
  - `AnalysisReport.resolution()` returns one `Resolution(path, resolved,
    missing, libdirs)` per scanned object; `AnalysisReport.missing_libs()`
    returns the sorted union of every lib that is missing anywhere (the
    "critical" list). When no `--libdirs` are given, every declared lib is
    reported as missing and `missing_libs()` is empty (use `unique_needed`).
  - `report.format()` now shows a per-object resolved/missing breakdown plus a
    summary ("Resolved: N occurrence(s). Missing: N occurrence(s)." and the
    missing list) whenever `--libdirs` is supplied, and flags any libdir that
    is not present on disk (so a "missing" there is understood to be a false
    positive).
  - New tests in `test_analyse.py`: single-file and prefix resolution,
    per-object breakdown, missing-lib reporting, symlinked-soname presence,
    exact-name/case-sensitivity, absent-libdir flagging, multi-libdir union,
    formatting, and a never-shells-out guard.
- `list-scripts` subcommand: lists a package's maintainer scripts for
  inspection and **explicitly refuses to run them** (the core security
  invariant). Scripts are read, never executed.
  - New `scripts.py`: `list_scripts()` / `list_scripts_from_path()` read the
    scripts, `ScriptListing` collects them, and `format_listing()` renders a
    human-readable listing that ends with a "NOT executed" refusal notice.
  - `formats.ScriptEntry` (name, body, interpreter, size, sha256) +
    `DebPackage.scripts` (reads `preinst`/`postinst`/`prerm`/`postrm` from
    `control.tar.*`, handling the real `./` layout) and
    `RpmPackage.scripts` (reads the RPM `PREIN`/`POSTIN`/`PREUN`/`POSTUN`
    header tags — `rpmtag.h` 1023–1026 — by size, not by NUL, so the full
    script body is preserved). The interpreter is derived from each script's
    `#!` shebang (leading `/` dropped, an appended argument ignored).
  - The RPM header now carries a per-tag *size* array so script bodies (which
    are not NUL-terminated) parse correctly; existing string tags are read by
    size too.
- `extract` subcommand: writes a deb/rpm payload to an isolated install prefix,
  recreating regular files, directories and symlinks, and records a manifest of
  every written entry (path + size + SHA-256). Maintainer scripts are never
  read or executed — only the payload tarball is touched.
  - Payload paths are resolved against the prefix and **path traversal is
    refused**: a `..`/absolute escape raises `PathEscapeError` (CLI exit code
    `5`) instead of writing outside the prefix.
  - `on_conflict="fail"` (default) refuses to overwrite an existing path;
    `on_conflict="replace"` overwrites files and resets stale symlinks.
  - New `formats.PayloadMember` + `DebPayload.iter_members()` /
    `RpmPayload.iter_members()` expose the tar member *kind* so symlinks and
    directories are recreated on disk (the existing `files()` view still hides
    the kind for the uniform `(path -> bytes)` mapping).
- Deb reading (`ar` + tar) and rpm reading (`cpio newc` + zstd/gzip/bz2/xz
  payloads) via pure stdlib parsing — never invokes `dpkg-deb`/`rpm`.
  - `ArReader` validates the `ar` archive eagerly (a truncated archive raises
    on construction, not on first member access).
  - `DebPackage`/`RpmPackage` expose the payload as `(path, bytes)` pairs and
    parse the `control` file, handling the real `./control` / `./postinst`
    layout emitted by Debian tooling.
  - RPM header parsing emits and reads only the present string tags, so the
    tag/offset arrays stay aligned when fewer than four tags are present.

## [0.1.0] — 2026-09-15

### Added

- Scaffold the repository: src-layout Python package `pengyos_debx`,
  `pyproject.toml` (stdlib-only, `pengyos-debx` console entry point),
  README, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG, and
  `.gitignore`.
- `pengyos-debx` CLI with `extract` / `analyse` / `list-scripts` subcommands
  wired through an explicit dispatch table. Handlers are honest placeholders
  that raise `NotYetImplementedError` until implemented.
- Smoke + github_ready test suite (`pytest -q` green).
