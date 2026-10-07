# PengyOS server-toolset — the comfort CLI/shell pack (US-SRV-1)

## Summary
The Server edition's remote-admin comfort set: Buildroot package enables + a curated shell profile for mosh, tmux, ripgrep, fd, bat, eza, zoxide, jq (and friends), with drift-proof tests.

> `fzf` and `starship` have no *upstream* Buildroot package, so this repo ships
> its own well-formed Buildroot packages for them (`package/fzf/` — a
> `golang-package`; `package/starship/` — a `cargo-package`). Their symbols (`BR2_PACKAGE_FZF` /
> `BR2_PACKAGE_STARSHIP`) are therefore defined in the tree and all ten tools are
> enabled by the Buildroot *fragment* (the deliverable). The eight core tools
> additionally ship upstream.

## Purpose
USE-CASES US-SRV-1: a fresh SSH login has the expected tools; a session survives disconnection (tmux); scrollback/search are pleasant by default.

## Deliverable
A repo with `fragments/server-toolset.fragment` (Buildroot BR2_ package enables), `shell/` (bash/zsh profile, tmux.conf, starship.toml, fzf/zoxide init) and a pytest validator asserting the promised tool set and the emitted fragment cannot drift, that shell snippets pass `sh -n`, and that the configs declare no telemetry.

## Interface
- files: fragments/server-toolset.fragment, shell/profile, shell/tmux.conf, shell/zoxide.sh
- tests: pytest fragment + config validator

## Verification (tests are first-class)
- tool table <-> fragment consistency (bijective).
- shell snippets pass `sh -n`; tmux.conf parses.
- no-telemetry scan flags a planted phone-home URL (test has teeth).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- The promised tool set and the emitted fragment cannot drift.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Building the Server image

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
