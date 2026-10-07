# pengyos-hardening

A declarative **PengyOS hardening baseline** plus an **overlay/defconfig
linter** — the tool that audits Buildroot `BR2_EXTERNAL` rootfs-overlays and
defconfigs against the hardening posture, so drift fails a test instead of
reaching a machine.

PengyOS is built with
[Buildroot](https://buildroot.org) using an external (`BR2_EXTERNAL`) tree.
Each edition is a Buildroot *defconfig* plus a rootfs-overlay, and today the
editions hand-write an `sshd_config`, an `nftables` ruleset and defconfigs with
nothing that asserts the posture is actually hardened — a reopened
`PermitRootLogin` or a policy-accept firewall would ship silently.

This package turns the hardening posture into **data the build is checked
against**.

## What it does

The hardening posture is committed as **data** (rules with a stable id,
severity, domain, and matcher). The code is the reader, not the authority:

- **`baseline`** — loads and structurally validates the committed baseline
  (every rule has a stable `id`, a `severity`, a `domain`, and a `matcher`).
- **`check`** — audits a repo tree: every `BR2_EXTERNAL */rootfs-overlay/` and
  the `*_defconfig`s — into per-rule findings with `file:line` evidence.
  Readers are added one at a time (sshd_config first); a rule whose matcher is
  not yet implemented is reported as skipped, never failed.
- **`report`** — renders the audit as stable, deterministic text or JSON.

See `spec.md` for the full contract.

## Layout

```
.
├── pyproject.toml              # packaging, console script, pytest config
├── README.md
├── CHANGELOG.md
├── LICENSE                     # MIT, Copyright (c) 2026 Pat Wendorf
├── .gitignore
├── pengyos_hardening/          # the shipped, stdlib-only package
│   ├── __init__.py             # version + public surface
│   ├── __main__.py             # python -m pengyos_hardening
│   ├── cli.py                  # argparse dispatch: baseline / check
│   ├── baseline.py             # load + structurally validate the baseline
│   ├── errors.py               # error hierarchy (PengyOSHardeningError…)
│   ├── data/baseline.json      # the committed baseline (the contract)
│   └── check/                  # the linter core (added per-subtask)
│       ├── readers.py          # sshd_config / sysctl.d / fstab / defconfig parsers
│       ├── matchers.py         # one evaluator per matcher kind
│       ├── audit.py            # walk the tree, evaluate rules
│       └── report.py           # stable text + JSON output

├── tests/
│   ├── conftest.py             # shared fixtures (stdlib-only, hermetic)
│   ├── test_scaffold.py        # target directory tree exists
│   ├── test_cli.py             # CLI dispatch surface
│   ├── test_baseline.py        # committed-baseline loader + structural validation
│   ├── test_readers.py         # sshd_config / sysctl.d / fstab / defconfig readers
│   ├── test_check.py           # matchers, auditor, check CLI
│   ├── test_report.py          # stable text + JSON golden layouts
│   ├── test_grounding.py       # baseline-driven: each rule fires its mutation
│   └── test_github_ready.py    # GitHub-ready contract (README/LICENSE/suite)
└── tests/fixtures/             # committed fixture trees (hardened + mutations)
```

## Install

The package is stdlib-only. From the repo root:

```sh
pip install -e .            # provides the `pengyos-hardening` console script
# or run without installing:
python3 -m pengyos_hardening --help
```

## Usage

```sh
pengyos-hardening baseline [--json] [--path FILE]
pengyos-hardening check --tree REPO_ROOT [--json OUT]
pengyos-hardening check --tree REPO_ROOT --rule ID [--json]
```

`check` walks `REPO_ROOT`, finds every `*/rootfs-overlay/` directory and every
`*_defconfig`, evaluates each baseline rule against the files it can see, and
emits findings sorted by severity (critical first), then path, then rule id so
the worst deviations surface at the top. `--rule ID` restricts the audit to a
single rule; `--json` emits machine-readable findings (to `OUT`, or stdout
when `OUT` is omitted or `-`).

## Testing

The test-suite is the objective function. It is hermetic — no network, no
root, no real hardware — and opt-in integration tests are gated behind
`PENGYOS_IT=1`:

```sh
pytest -q                       # the full suite
PENGYOS_IT=1 pytest -q          # include opt-in integration tests
```

## License

MIT — see `LICENSE`. Copyright (c) 2026 Pat Wendorf.
