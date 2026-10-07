# PengyOS recipe-server-clis — Buildroot recipes for the Server comfort CLIs (US-SRV-1/3)

## Summary
Author pinned Buildroot recipes (Config.in + <pkg>.mk + <pkg>.hash) for the Server toolset CLIs that Buildroot 2026.08 does not ship — zoxide, sd, node_exporter, btop, helix, neovim — each with a pinned version + hash, a declared license, and a recipe lint.

## Purpose
USE-CASES US-SRV-1 (comfortable remote shell) and US-SRV-3 (monitoring): Buildroot 2026.08 has tmux/ripgrep/jq/fd/bat/eza/yq but lacks zoxide/sd/helix/neovim/btop/node_exporter, so the US-SRV toolset cannot be fully expressed until we own those recipes — 'recipes are our data' (SPEC 2.1).

## Deliverable
A Python package `pengyos_recipe_server_clis` that emits and lints our BR2_EXTERNAL recipes for the missing CLIs: for each tool a Config.in, <tool>.mk (pinned version + source URL + license file), <tool>.hash, plus a `lint` subcommand; it also emits fragments/server-clis.fragment enabling the new symbols. Mirrors the fzf/starship recipe shape already in pengyos-server-toolset.

## Interface
- pengyos-recipe-server-clis list [--json]
- pengyos-recipe-server-clis emit --out DIR [--json]
- pengyos-recipe-server-clis lint --dir DIR [--json]

## Verification (tests are first-class)
- table-driven: the catalogue covers the six tools and each emits a golden, deterministic recipe set.
- lint rejects a missing .hash, an unpinned version, and an undeclared license (one mutation each).
- the fragment lists exactly the emitted symbols (no more, no less).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Recipes are deterministic and lint clean against the recipe grammar.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Building the packages / invoking Buildroot
- Adding tools Buildroot 2026.08 already ships

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
