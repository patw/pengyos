# PengyOS repro — reproducible-build differ

## Summary
Compare two builds' outputs and classify differences; report a reproducibility score and the offending inputs.

## Purpose
Reproducibility is how we verify rather than trust (SPEC §7 bootstrap chain).

## Definitions
* **Artifact** = a regular file under a build tree. The manifest walks each tree
  and records only regular files (relative POSIX path → byte `size` + streaming
  `sha256`). Symlinks, directories and other special files are *skipped* (they are
  not hashable content and following them risks cycles/escapes). This is the
  behaviour of `pengyos_repro/manifest.py`; the classification subtask builds on
  it.
* **Manifest** = the mapping produced by walking one tree (see above).
* **Reproducibility score** = `1.0` for byte-identical trees; lower when files
  differ or are missing (defined by the scoring subtask).

## Deliverable
A Python CLI `pengyos-repro` that diffs two image/build directories, hashes artifacts, classifies differences (identical / metadata-only / content), and emits a report.

## Interface
- pengyos-repro compare <dirA> <dirB> [--json OUT] [--ignore PATTERN]... [--strip-metadata]
- pengyos-repro score <dirA> <dirB> [--strip-metadata]  # 0.0-1.0

### CLI conventions (fixed at scaffold)
- **Exit codes:** `0` success · `1` ran but failed (e.g. a path could not be
  read) · `2` usage error (argparse) · `3` subcommand recognised but not yet
  implemented (scaffold stage). A `score`/`compare` that finds the trees differ
  is still a *success* (`0`) — a low score is a valid result, not an error.
- **`--ignore PATTERN`:** a glob matched against each path's *relative* path
  (POSIX-normalised with `/`). Repeatable. Ignored paths are excluded from both
  the report and the score (they are filtered from the manifests before
  classification; see `pengyos_repro/patterns.py`). The match semantics are:
  * **path-segment-aware globs** — `*` matches within a path segment (never
    across `/`); `**` recurses across directories; `?` matches a single char;
    `[...]` is a character class (`!` negates).
  * **slash-free** patterns are *basename* matches at any depth (e.g. `*.so`
    matches `usr/lib/foo.so`); patterns containing a `/` are *anchored* to the
    root (matched against the full relative path);
  * a **trailing `/`** marks *directory pruning* — the pattern matches that
    directory and everything beneath it (at any depth).
  * a `heuristic:<name>` alias expands to a curated set of starter globs
    (`timestamps`, `embedded-user`, `random-seeds`) for the classic
    nondeterminism sources; unknown aliases are an error.
- **`--json OUT`:** when given, `compare` additionally writes the machine-readable
  report to `OUT`. The JSON schema (`{score, identical, differing:[{path,reason}],
  missing:[...]}`) is fixed by the report-emission subtask.

## `--strip-metadata` mode (a second-pass comparison)

The raw comparison classifies by streaming `sha256`. Some nondeterminism leaks
into a file's *content* (a `.pyc` bakes the source mtime into its bytes, an
archive bakes the building uid/gid into its bytes, a `random-seed` file is
random bytes), so it shows up as a content difference that no metadata field can
explain. The curated heuristics (`timestamps`, `embedded-user`, `random-seeds`)
catch these by filename.

`--strip-metadata` runs a **second-pass comparison**:

1. **Pass 1 (raw):** compare with the user's `--ignore` patterns → raw score.
2. **Pass 2 (stripped):** compare with the user's `--ignore` patterns *plus*
   every curated heuristic → stripped score.
3. **Metadata-only differences:** a path that differs in pass 1 but is identical
   in pass 2 is *metadata-only* (explained by a known nondeterminism source).
   Each is annotated with the heuristic that explained it
   (`heuristic:<name>` if a heuristic matched, else the literal glob).

Behaviour:

* `compare --strip-metadata` prints the raw score, the stripped score, and a
  "Metadata-only (explained by nondeterminism heuristics)" section listing the
  explained paths (with the explaining heuristic). A path that differs in *both*
  passes is a genuine **content** difference and is *not* listed as metadata-only.
* `score --strip-metadata` prints the stripped (second-pass) score.
* If the raw score is already `1.0` the second pass is a no-op (nothing to
  explain); the report still notes the stripped score equals the raw score.
* `--strip-metadata` never changes the *raw* score or report — it only adds the
  second-pass analysis. It is additive to `--ignore`, not a replacement.

## Verification (tests are first-class)
- identical trees → score 1.0.
- one differing file → listed, score < 1.0.
- only-in-A/B detected.
- ignore patterns exclude a noisy file, raising the score as documented.
- large-ish synthetic tree is handled without loading files fully into memory.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Report lists exactly the differing paths with reasons.
- Score is deterministic and documented.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Fixing nondeterminism automatically

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
