# PengyOS upstream-watch — detect new upstream releases

## Summary
From the pins manifest, detect new upstream releases and emit change proposals (offline-testable).

## Purpose
SPEC §3 OBSERVE stage; feeds the triage loop.

## Deliverable
A Python tool that, given pins + a fetcher abstraction, detects newer versions and emits proposals; the fetcher is mocked in tests.

## Interface
- pengyos-upstream-watch run --pins pins.toml --fetcher {http,mock} --out proposals.json

## Pins manifest
A TOML document with a `component` array of tables, one entry per pinned
upstream:

```toml
[[component]]
name = "busybox"          # required: the component name (fetcher match key)
version = "1.36.1"        # required: the currently pinned version
url = "https://busybox.net/"   # optional: explicit upstream URL for the HTTP fetcher
```

A missing/empty `component` list is valid (no pins to watch). A malformed
manifest (wrong shape, a missing `name`/`version`, a non-string field) is a
clean error (exit 1, `PinsError`).

## Proposal document
`--out` is a JSON array of proposal objects, one per drifted pin, sorted by
importance (non-LTS and larger jumps first):

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

`lts` flags an LTS-style two-part (`x.y`) pinned line; `url` is the pin's
upstream URL (or `null`). A pin equal to or older than the latest release
produces no proposal.

## Verification (tests are first-class)
- mock fetcher returning newer → proposal emitted.
- equal/older → no proposal.
- version comparison edge cases (1.2 vs 1.2.0, prereleases).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- No live network in tests.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Downloading sources

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
