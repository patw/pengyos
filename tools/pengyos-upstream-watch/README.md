# pengyos-upstream-watch

**Detect new upstream releases for [PengyOS](../../README.md) pins → change
proposals.**

This is the *detect* half of the PengyOS maintenance loop (SPEC §3, OBSERVE
stage): given a **pins manifest** (the pinned `name, version` pairs that make a
build reproducible) plus a **fetcher** abstraction that knows how to ask an
upstream for its latest release, it decides whether a pin has drifted out of
date and emits change proposals.

The fetcher is the only thing that touches the outside world, so the detection
logic stays fully **offline-testable** — a mock fetcher stands in for the real
HTTP one in tests.

## Status

| Subsystem | State |
|-----------|-------|
| Package scaffold (packaging, entry point, pytest) | ✅ done |
| `run` CLI surface (`--pins` / `--fetcher` / `--out`) | ✅ done |
| Fetcher interface (latest_version) + http + mock impls | ✅ done |
| Semver-ish comparison (LTS `x.y` vs `x.y.z`, prereleases) | ✅ done |
| Proposal emission (component, current, available, lts?, url) | ✅ done |
| `test_github_ready` (GitHub-ready contract) | ✅ done |

See `spec.md` for the contract.

## Install

Requires Python ≥ 3.11 (the pins manifest is TOML, parsed with the standard
library's `tomllib`). No third-party runtime dependencies. Install from the
repository root:

```bash
pip install -e .
```

## Usage

```bash
pengyos-upstream-watch run --pins pins.toml --fetcher {http,mock} --out proposals.json
```

Or without installing:

```bash
python -m pengyos_upstream_watch run --pins pins.toml --fetcher mock --out proposals.json
```

### `run`

| Flag | Required | Default | Meaning |
|------|----------|---------|---------|
| `--pins PATH` | yes | — | Path to the pins manifest (TOML). |
| `--fetcher {http,mock}` | no | `mock` | Which upstream fetcher to use. `mock` never touches the network. |
| `--out PATH` | yes | — | Path to write the proposals JSON document. |

`proposals.json` is a JSON array of proposal objects, one per drifted pin:

```json
[
  {
    "component": "busybox",
    "current": "1.36.1",
    "available": "1.37.0",
    "lts": false,
    "url": "https://busybox.net/"
  }
]
```

Proposals are sorted by importance (non-LTS and larger jumps first).

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success (or help printed with no subcommand). |
| `1` | Ran but failed: missing/invalid pins, or a feature not yet implemented. |
| `2` | Usage error (argparse convention). |

## Package layout

```
pengyos_upstream_watch/
├── __init__.py            # package metadata (__version__)
├── __main__.py            # python -m entry point
├── cli.py                 # argument parsing + run subcommand body (items 1–4)
├── fetcher.py             # upstream Fetcher interface + http/mock impls (item 2)
├── compare.py             # semver-ish version comparison (item 3)
├── pins.py                # pins manifest loader (item 4)
└── proposals.py           # Proposal model + detection pipeline (item 4)
```

## Pins manifest

The `run` subcommand reads a **pins manifest**: a TOML document with a
`component` array of tables, one entry per pinned upstream:

```toml
[[component]]
name = "busybox"
version = "1.36.1"
url = "https://busybox.net/"
```

* `name` — required, the component name (the key the fetcher matches).
* `version` — required, the currently pinned version.
* `url` — optional, an explicit upstream URL the `HttpFetcher` uses to find the
  latest release (omitted → the fetcher's ecosystem defaults).

A missing/empty `component` list is valid (no pins to watch). A malformed
manifest (wrong shape, a missing `name`/`version`, a non-string field) is a
clean error (exit 1).

## Detection pipeline

For each pin the tool fetches the latest upstream release (via the selected
fetcher), classifies the drift with the semver-ish comparison, and emits a
proposal only when the latest release is **newer** than the pin. Equal or older
releases produce no proposal. A proposal carries
`(component, current, available, lts?, url)`, where `lts` flags an LTS-style
two-part (`x.y`) pinned line. Proposals are sorted by importance (non-LTS and
larger jumps first), so the emitted document is deterministic.

The pipeline is pure and offline-testable: `detect_proposals(components,
fetcher)` takes a fetcher (a mock in tests) and never touches I/O itself.

## Fetcher abstraction

The only layer that touches the outside world is the `Fetcher` interface
(`fetcher.py`):

```python
latest_version(component: Component) -> str
```

* `MockFetcher` answers from a `{name: version}` mapping — never touches the
  network, and is what the test suite uses.
* `HttpFetcher` is the production implementation. Upstreams disagree on how
  "latest" is exposed, so dispatch is delegated to pluggable strategies
  (`PyPILatestVersionStrategy`, `GitHubReleasesStrategy`, and a generic
  `RedirectLatestStrategy`). Every strategy is pure; the single network entry
  point is `HttpFetcher.send`, which tests stub.

Swap the fetcher by passing `--fetcher {http,mock}` to `run`.

## Tests

The suite needs no network, root, display, or hardware — external fetches are
mocked and fixtures are committed. Run it from this directory:

```bash
pytest -q
```

| Test file | Covers |
|-----------|--------|
| `test_scaffold.py` | Package metadata, entry points, CLI argument surface, exit codes. |
| `test_fetcher.py` | `Fetcher` interface, `MockFetcher`, `HttpFetcher` (transport stubbed), every strategy, `build_fetcher`, and CLI fetcher selection. |
| `test_compare.py` | Semver-ish comparison: LTS `x.y` vs `x.y.z` equivalence, prerelease ranking, numeric core ordering, `is_newer`/`classify`. |
| `test_github_ready.py` | The GitHub-ready contract: standing docs present and coherent, MIT LICENSE with the pinned copyright, Keep a Changelog headings, `.gitignore` ignores the emitted artifact, coherent build metadata. |

An opt-in integration gate (`PENGYOS_IT=1`) re-runs the whole suite in a
subprocess and asserts it exits 0.

## License

MIT — "Copyright (c) 2026 Pat Wendorf". See `LICENSE`.
