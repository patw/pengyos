# pengyos-repro

**Reproducible-build differ** for
[PengyOS](../../README.md).

Compare two builds' outputs and classify differences; report a reproducibility
score and the offending inputs. Reproducibility is how we verify rather than
trust (SPEC §7 bootstrap chain): two builds of the same pinned inputs must
produce byte-identical outputs, and any difference points at a nondeterministic
input.

Given two image/build directories the tool:

* walks both trees and hashes every artifact (streaming, large-file safe),
* classifies each path as `identical` / `differs` / `only-in-A` / `only-in-B`,
* tolerates known-nondeterministic inputs via configurable ignore patterns,
* reports a reproducibility score in `[0.0, 1.0]`, and
* emits a human report plus a machine-readable JSON report.

## Status

| Subsystem | State |
|-----------|-------|
| Project scaffold (packaging, entry point, pytest) | ✅ done |
| CLI argument surface (`--version`, `compare`, `score`) | ✅ defined (parsing real; logic scheduled) |
| Walk two trees; build a manifest of relative paths → size + sha256 (streaming) | ✅ done (`pengyos_repro/manifest.py`) |
| Classify each path: identical / differs / only-in-A / only-in-B | ✅ done (`pengyos_repro/classify.py`) |
| Heuristic classifiers for known nondeterminism sources (ignore patterns) | ✅ done (`pengyos_repro/patterns.py`) |
| Reproducibility score + human/JSON report | ✅ done (`pengyos_repro/report.py`) |
| `--strip-metadata` mode for a second-pass comparison | ✅ done (`pengyos_repro/patterns.py`, `report.py`) |
| `test_github_ready` | ✅ done (`tests/test_github_ready.py`) |

See `spec.md` for the contract.

## Install

Requires Python ≥ 3.9. No third-party runtime dependencies (standard library
only). Install from the repository root:

```sh
pip install -e .
```

That provides the `pengyos-repro` console script. You can also run it directly:

```sh
python -m pengyos_repro --version
```

## Usage

```
pengyos-repro [--version] {compare,score} ...

pengyos-repro compare DIRA DIRB
    [--json OUT]
    [--ignore PATTERN]...
    [--strip-metadata]

pengyos-repro score DIRA DIRB
    [--ignore PATTERN]...
    [--strip-metadata]
```

* `compare DIRA DIRB` — diff two build directories and emit a human report
  (identical / differing / only-in-A / only-in-B paths with reasons). Pass
  `--json OUT` to also write the machine-readable report to `OUT`. Pass one or
  more `--ignore PATTERN` to exclude noisy paths (matched as globs against the
  relative path) from both the report and the score. Pass `--strip-metadata` to
  additionally run a second-pass comparison that hides the classic
  nondeterminism heuristics and reports which differences were explained away as
  metadata-only.
* `score DIRA DIRB` — print a single reproducibility score in `[0.0, 1.0]` to
  stdout: `1.0` means the two trees are identical; lower scores reflect differing
  or missing artifacts. `--ignore PATTERN` is accepted for parity with `compare`;
  `--strip-metadata` prints the second-pass (metadata-stripped) score instead.

### Exit codes

| Code | Meaning |
|:----:|---------|
| `0` | Success |
| `1` | The command ran but produced a failure result |
| `2` | Usage error (bad/missing arguments) |
| `3` | Subcommand recognised but not yet implemented |

### Ignoring nondeterministic files

Some files differ between builds *by construction* even when the inputs are
identical — Python bytecode embeds a timestamp, an archive embeds the building
user's uid/gid, a `random-seed` file is random. Those differences are expected;
hide them with `--ignore` so the score reflects real divergence.

```sh
# glob against the relative path (repeatable)
pengyos-repro compare build-a build-b --ignore '**/*.pyc'

# curated starter patterns for the classic nondeterminism sources
pengyos-repro compare build-a build-b \
    --ignore 'heuristic:timestamps' \
    --ignore 'heuristic:embedded-user' \
    --ignore 'heuristic:random-seeds'
```

Globs are path-segment aware: `*` stays within a path segment, `**` recurses,
and a trailing `/` matches a directory and everything beneath it. See
`pengyos_repro/patterns.py` for the exact semantics and the full set of
heuristics.

### Stripping metadata (a second-pass comparison)

Some nondeterminism does not live in a file's metadata — it is baked into the
file's *content*: a `.pyc` embeds the source mtime in its bytes, an archive
embeds the building uid/gid, a `random-seed` file is random bytes. The raw
content-hash comparison sees these as real content differences. `--strip-metadata`
runs a **second pass** that additionally hides the classic nondeterminism
heuristics (`timestamps`, `embedded-user`, `random-seeds`) and reports which
differences were explained away as *metadata-only* — expected noise rather than a
real divergence.

```sh
# raw score, then a second pass that explains the noise away
pengyos-repro compare build-a build-b --strip-metadata
```

The report shows both scores and lists each explained path with the heuristic
that explained it (`heuristic:timestamps`, `heuristic:embedded-user`,
`heuristic:random-seeds`). A path that differs in *both* passes is a genuine
content difference and is left in the differing list. `score --strip-metadata`
prints the second-pass score. `--strip-metadata` is additive to `--ignore` and
never changes the raw score.

## Scoring

The reproducibility score is the fraction of compared paths that are
byte-identical::

    score = identical / (identical + differs + only-in-A + only-in-B)

i.e. `len(identical) / total`, where `total` is every path that survived the
`--ignore` filter.

* **Identical trees score `1.0`.**
* **A differing or missing path lowers the score** (by `1/total` each).
* **Empty trees score `1.0`** (vacuously reproducible — no divide-by-zero).
* **`--ignore` patterns raise the score**, because a noisy path is removed from
  both `total` and the differing/missing sets — so the score reflects *real*
  divergence, not expected nondeterminism.

The score is rounded to 4 decimals for stable output. A low score is a valid
*result* — `compare`/`score` still exit `0`; only an unreadable file (exit `1`)
or a bad `--ignore` glob (exit `2`) is a non-zero exit. See
`pengyos_repro/report.py` for the exact definition.

## Testing

The test suite needs no network, root, display, or hardware — it uses committed
fixtures and pure functions. Real system integration is opt-in behind
`PENGYOS_IT=1`.

```sh
# full unit suite (must be green)
pytest -q
```

## Project layout

```
pengyos-repro/
├── pengyos_repro/
│   ├── __init__.py      # package metadata (__version__)
│   ├── __main__.py      # python -m entry point
│   ├── cli.py           # argparse surface + dispatch
│   ├── manifest.py      # two-tree manifest: rel path -> (size, sha256)
│   ├── classify.py      # classify paths: identical / differs / only-in-A / only-in-B
│   ├── patterns.py      # --ignore glob matching + known-nondeterminism heuristics
│   └── report.py        # reproducibility score + human/JSON report
├── tests/
│   ├── conftest.py      # makes the package importable without install
│   ├── test_scaffold.py # scaffold + CLI (compare/score) tests
│   ├── test_manifest.py # manifest (walk/hash) tests
│   ├── test_classify.py # path classification tests
│   ├── test_patterns.py # --ignore glob matching + heuristics tests
│   └── test_report.py   # score + report emission tests
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── LICENSE              # MIT
└── .gitignore
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
