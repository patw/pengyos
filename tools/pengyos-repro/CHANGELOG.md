# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Two-tree manifest (implementation.md item 2): `pengyos_repro/manifest.py` walks
  a build tree and maps each file's POSIX-relative path to a `FileEntry` of its
  byte `size` and streaming `sha256` digest.
  - Streaming / large-file safe: files are hashed in 1 MiB chunks, so memory use
    is constant regardless of artifact size.
  - `iter_manifest()` yields entries lazily (streaming); `manifest()` materialises
    the full mapping. Symlinks, directories and other special files are skipped.
  - `ManifestError` is raised when a file inside the tree cannot be read (the CLI
    layer maps this to exit code 1).

- Path classification (implementation.md item 3): `pengyos_repro/classify.py`
  compares two manifests and buckets every relative path into exactly one of
  `identical` / `differs` / `only-in-A` / `only-in-B`.
  - `classify(manifest_a, manifest_b)` is pure and deterministic: it takes two
    `{relpath: FileEntry}` dicts and returns a `Diff` with four sorted lists.
  - `DiffEntry(path, reason)` records *why* a differing path differs (a size
    mismatch is named first, then a same-size content mismatch).
  - `Diff.missing` aggregates `only_in_a` + `only_in_b` (the `missing` list the
    report-emission subtask writes to JSON); `Diff.counts()` gives a fixed-order
    `{status: count}` map.
  - `classify_trees(root_a, root_b)` composes `manifest()` + `classify()` for the
    two-directory convenience path.

- Ignore-pattern matching + known-nondeterminism heuristics (implementation.md
  item 4): `pengyos_repro/patterns.py` defines how `--ignore PATTERN` globs are
  matched against each artifact's relative POSIX path and how to hide
  known-nondeterministic artifacts *before* classification (so they leave both
  the report and the score).
  - **Matching semantics** (the contract `spec.md` deferred to this subtask):
    path-segment-aware globs — `*` matches within a segment (never across `/`),
    `**` recurses across directories, `?` matches a single char, `[...]` is a
    character class. A slash-free pattern is a *basename* match at any depth; a
    pattern with a `/` is *anchored* to the root; a trailing `/` marks
    *directory pruning* (matches the directory and everything beneath it).
  - `Matcher` precompiles a pattern set once and offers a fast `is_ignored`
    predicate; `matches_any` is the one-shot convenience form.
  - `expand()` turns `heuristic:<name>` aliases into concrete globs;
    `filter_manifest()` drops ignored paths from a manifest (pure — never
    mutates its input).
  - **Heuristic starter patterns** (opt-in, configurable): `timestamps`
    (`.pyc`/`__pycache__` embed source mtimes), `embedded-user` (ar/ELF/zip
    archives embed uid/gid+mtime), `random-seeds` (seed files). Registered under
    `HEURISTICS`.

- Reproducibility score + report emission (implementation.md item 5):
  `pengyos_repro/report.py` turns a `Diff` into a score and a report.
  - **Score** = `len(identical) / total`, where `total` is every path that
    survived the ignore filter. Identical trees score `1.0`; a differing or
    missing path lowers it (by `1/total` each); empty trees score `1.0`
    (vacuously reproducible, no divide-by-zero); ignore patterns raise the score
    as documented (they remove a noisy path from both `total` and the
    differing/missing sets). Rounded to 4 decimals for stable output. A low
    score is a valid result, not an error.
  - **Report dict** — the fixed JSON schema
    `{score, identical, differing:[{path,reason}], missing:[...]}`. `missing` is
    `only-in-A` (sorted) then `only-in-B` (sorted); `differing` carries the
    classify `reason` (a size mismatch is named first, then a same-size content
    mismatch).
  - **Human report** — `format_report()` prints `REPRODUCIBLE`/`NOT REPRODUCIBLE`
    with the score, then the differing paths (with reasons) and missing paths
    (labelled `only in A` / `only in B`), then a summary counts line.
  - **CLI wired.** `compare` now walks both trees, applies `--ignore`,
    classifies, scores, and prints the human report (or writes JSON to `--json`);
    `score` prints the single score. An unreadable file is exit `1`; an invalid
    `--ignore` glob is exit `2` (usage). The full manifest → filter → classify →
    score → report pipeline lives in `cli._run_compare`.

- `--strip-metadata` second-pass comparison (implementation.md item 6):
  `pengyos_repro/patterns.py` gains `strip_metadata_patterns()` (the caller's
  `--ignore` patterns plus every curated heuristic — the effective ignore list
  for the second pass) and `explain_ignore()` (returns the first pattern that
  matches a path: the `heuristic:<name>` alias that explained it, or the literal
  glob). `pengyos_repro/report.py` gains `metadata_analysis()`, which compares
  the raw (pass 1) and metadata-stripped (pass 2) diffs and classifies each
  differing path as *metadata-only* (differs in the raw pass, identical once a
  heuristic hides it) or a genuine *content* difference (differs in both passes).
  The result is a `MetadataAnalysis` (`stripped_score` + `explained:` list). The
  second pass catches metadata that leaks into a file's *content* (bytecode
  timestamps, embedded uid/gid, random seed bytes) — differences the raw
  content-hash comparison cannot otherwise explain.
  - `compare --strip-metadata` prints the raw score, the stripped score, and a
    "Metadata-only (explained by nondeterminism heuristics)" section listing the
    explained paths (with the explaining heuristic). `score --strip-metadata`
    prints the stripped score. Identical raw trees are a no-op second pass.
  - `report_dict` / `format_report` / `emit` / `dumps_report` gain an optional
    `analysis=` argument that adds `stripped_score` and `metadata_only` to the
    JSON view and the "Metadata-stripped score" / "Metadata-only" section to the
    human view. `--strip-metadata` is additive to `--ignore`, never a
    replacement, and never changes the raw score.

- GitHub-ready contract (implementation.md item 7): `tests/test_github_ready.py`
  asserts the repository is GitHub-ready — `README.md`, `LICENSE` (MIT,
  "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`, `.gitignore` all exist and
  are coherent, the package and every module import cleanly, and the test suite
  is present and complete (every implemented module has a dedicated test file).
  Docs are kept in sync with the code: the README documents the real CLI
  surface (`compare` / `score` / `--strip-metadata` / `--ignore`), and the
  `test_github_ready` smoke test asserts the whole contract at once.

## [0.1.0] - 2026-09-14

### Added
- Project scaffold (implementation.md item 1): `pyproject.toml` (PEP 621, MIT,
  `setuptools` build backend, standard-library-only runtime), the
  `pengyos_repro` package, and the `pengyos-repro` console script.
- CLI argument surface in `pengyos_repro/cli.py`: `--version`, plus the `compare`
  and `score` subcommands with the documented flags (`compare DIRA DIRB
  [--json OUT] [--ignore PATTERN]...`, `score DIRA DIRB [--ignore PATTERN]...`).
  The argument parsing is real and tested; the subcommand *logic* (tree walk,
  streaming sha256, path classification, scoring, report/JSON emission) is
  tracked as its own subtasks in `implementation.md` and currently returns a
  clear `not implemented yet` message with a distinct exit code (`3`).
- Exit-code contract: `0` success · `1` ran-but-failed · `2` usage (argparse) ·
  `3` subcommand recognised but not yet implemented.
- `python -m pengyos_repro` entry point alongside the console script.
- `README.md`, `CHANGELOG.md`, `LICENSE` (MIT, Copyright (c) 2026 Pat Wendorf),
  and `.gitignore`.
- Scaffold test suite (`tests/test_scaffold.py`) covering package metadata,
  entry-point behaviour, the CLI argument surface, and the declared entry point.
