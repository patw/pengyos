# pengy-agent-package — ship the PengyR agent (pengy-cli + pengy-web) in Core

## Summary
A Buildroot external package that builds the **PengyR** agent's `pengy-cli` and
`pengy-web` (Rust, **headless** — no Qt GUI) from a pinned source, ships them
plus a default config, and validates the recipe.

## Purpose
SPEC §2.1/§3 ("builds pengy into itself") and §M3 ("the agent, built into Core"):
PengyOS is an operating system an agent can operate, so the agent ships in Core.

## Deliverable
A Buildroot external package (`package/pengy/`: `pengy.mk` + `pengy.hash`) that
builds `pengy-cli` + `pengy-web` from the pinned **PengyR** source, installs them
plus a default `/etc/pengy/settings.json`, plus a pytest suite that validates the
recipe. It ships **no systemd unit**: serving `pengy-web` is a per-user, opt-in
act (`pengyos-webui`, `tools/pengyos-webui`), not a recipe-wide enablement.

## Interface
- Buildroot: `package/pengy/pengy.mk`, `package/pengy/pengy.hash`
- shipped config: `settings.json`
- tests: pytest validating the recipe + config (and that no unit is installed)

## Verification (tests are first-class)
- recipe passes `pengyos-recipe-lint` rules (version pinned, hash present, license
  declared, no unverified downloads).
- the recipe builds **only** the `pengy-cli` and `pengy-web` Rust targets — assert
  it does **not** pull Qt6 / C++ / CMake (no GUI).
- the recipe installs **no** systemd unit and `package/pengy/` carries none: the
  UI has no authentication, so it is enabled per user by `pengyos-webui` from the
  server overlay's `pengy-web@.service` (`User=%i`, no `DynamicUser`, no
  `ProtectHome`).
- default `settings.json`: base URL points at an OpenAI-compatible endpoint
  (`http://127.0.0.1:11434/v1`), **no API key baked in**, no default model.
- `pengy.hash` is a sha256 for the pinned PengyR tarball at the pinned version, and
  the pinned version string matches `pengy.mk`.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Recipe lints clean and is pinned; the default config is valid; no unit ships.
- `pytest -q` green; test_github_ready green.

## Non-goals
- **The Qt6 GUI** (`pengy`) — Core is headless.
- **Running the web UI** — the systemd template and the on/off command are
  `pengyos-webui` (`tools/pengyos-webui`), not this package.
- **moofile** — that is memory *infrastructure* (BotTalk / LlamaHerder / tclip), not a
  Core OS component. The agent's memory is the remote BotTalk bus over HTTP, and
  local structured storage is already covered by `python3`'s `sqlite3` (both in Core).
- Bundling an inference server — the agent points at an OpenAI-compatible endpoint.
- Building / modifying the PengyR source itself (that is the agent's own repo).

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
