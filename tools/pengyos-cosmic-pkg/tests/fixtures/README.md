# fixtures

Committed, offline test fixtures. Nothing here is downloaded at test time — the
suite runs with no network, root, display, or hardware.

## `component.toml` — the fixture component manifest

A minimal manifest describing the demo component `cosmic-demo`, whose single
dependency (`smithay-clipboard`) is sourced over git. Consumed by the
`vendor`/`recipe`/`lint` tests as the hermetic input. (The hand-checkable demo
manifest lives at `examples/cosmic-text.toml`.)

## `workspace/` — the fixture git-workspace

A tiny Rust git workspace used to exercise deterministic, git-dependency-aware
vendoring:

```
fixtures/workspace/
├── Cargo.toml            # [workspace] root manifest (the demo app)
├── Cargo.lock            # pins cosmic-demo + a git dep (smithay-clipboard)
└── git-sources/          # the checked-out git crate(s) — the offline mirror
    └── smithay-clipboard/
        ├── Cargo.toml
        └── src/lib.rs
```

`fixtures/workspace/Cargo.lock` declares `smithay-clipboard` as a
`git+https://...?tag=v0.6.0#<sha>` source. The fixture ships that git crate
**checked out on disk** under `git-sources/` (keyed by repo slug —
`smithay-clipboard`, derived from the URL) so the vendored-tree rewrite
(`git = "..."` → vendored path) can be exercised fully offline: the `vendor`
command copies the on-disk source into `VENDOR/` rather than cloning over the
network.

`vendor` defaults its `
</think>.` (the mirror) to the parent of `--cargo-lock`
(`fixtures/workspace/`), so the fixture needs no extra flags.

The fixture is the golden input for the `vendor` golden test (deterministic
output + git rewrite), and for the `recipe`/`lint` end-to-end checks.
