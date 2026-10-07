# pengyos-repro-report

Rebuild-and-compare reproducibility reporting for PengyOS build outputs, written
in the witness layout so the external witness can find a machine-readable
reproducibility score at a conventional path.

This is part of the [PengyOS](../../README.md) project. Its purpose is signed,
measured boot that PengyOS controls; this tool **measures** reproducibility
across two pengyos-build outputs, it does not run a build or fix
non-determinism.

## What it does

Given two pengyos-build outputs, it diffs every artifact by SHA-256,
classifies each difference, and writes `repro-report.json` (score 0..1) at the
conventional witness path `<build>/images/repro-report.json`.

Each input is a build directory or a `build-manifest.json` file. A directory is
resolved via its `build-manifest.json` first (the primary source: artifact name
→ sha256), falling back to a content-hash walk of its `images/` directory.

- Two identical inputs → score `1.0`, every artifact `identical`.
- One artifact differing in bytes → classified `differs`, score `< 1`.
- Missing/unreadable manifests or dirs → a clean non-zero exit (no traceback).

## Install

```bash
pip install -e ".[dev]"
```

This exposes the `pengyos-repro-report` console script. The runtime depends
only on the Python standard library; `pytest` is a dev-only dependency.

## Usage

```bash
# 1. Compare two build outputs and write the report to the witness path.
#    With no --out the report is written to the conventional witness path
#    <build-a>/images/repro-report.json automatically:
pengyos-repro-report diff --a build-a --b build-b
#    ...or choose the location explicitly (this wins over the default):
pengyos-repro-report diff --a build-a --b build-b \
    --out build-a/images/repro-report.json

# 2. Read a report back and print its reproducibility score.
pengyos-repro-report score --report build-a/images/repro-report.json
```

### Gating CI on reproducibility

Both `diff` and `score` accept `--strict`. With it, the CLI exits non-zero (a
clean one-line error, no traceback) whenever the reproducibility score is below
`1.0`, so a CI job can fail a build on a genuine reproducibility failure. A
`normalized` difference (one explained by a known non-determinism) still scores
`1.0` and passes; only a `real` difference trips the gate. The report is always
written to the witness path first, so the witness can read it even on a failed
(strict) run.

```bash
# Fails (exit 1) if any artifact is a real difference:
pengyos-repro-report diff --a build-a --b build-b --strict
pengyos-repro-report score --report build-a/images/repro-report.json --strict
```

Run `pengyos-repro-report --help` (or `<command> --help`) for the full
interface.

## Report schema

`repro-report.json` is a JSON object with a pinned `schema` version, a `score`
in `[0, 1]`, a deterministically ordered `artifacts` list (each with a `name`,
`status`, per-side SHA-256, and a `normalized` flag), and a `summary` with
counts. See `pengyos_repro_report/core.py`.

Each artifact's `status` is one of:

- `identical` — byte-identical across both builds (counts toward the score);
- `normalized` — bytes differ only in a known non-deterministic field (a
  recorded build timestamp, unix timestamp, tar mtime, or build-id). Classified
  by the data-driven rule table in `pengyos_repro_report/classify.py`; still
  counts toward the score because the artifact is reproducible modulo that
  field;
- `real` — bytes differ in a way no rule explains: a genuine reproducibility
  failure (does not count toward the score);
- `missing_in_a` / `missing_in_b` — present on only one side.

The score is `matching / total`, where `matching` counts `identical` and
`normalized` artifacts.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Integration tests (real system calls) are opt-in behind `PENGYOS_IT=1`; unit
tests need no network, root, display, or hardware.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
