# PengyOS install-gui — the libcosmic Desktop installer wizard

A libcosmic-native graphical installer for **PengyOS Desktop**. It drives
`pengyos-install` through a pure, headless view-model plus the COSMIC installer
screens (disk, layout, user, summary, progress). The visuals are a
human-ratified artistry domain; the logic is shared, deterministic Rust.

This repository is the **`pengyos-install-gui`** Rust workspace. The wizard
*logic* lives in the `model` crate and is shared with the TUI front-end so both
agree on behaviour. The `gui` crate renders that model to COSMIC.

## Workspace layout

| Crate | Path | What it holds |
|-------|------|---------------|
| `pengyos-install-model` | `model/` | The whole wizard state machine — steps, validation, back-navigation, and the derived `install.toml` + layout plan. **Standard library only; 100% cargo-testable with no display.** |
| `pengyos-install-gui` | `gui/` | The libcosmic front-end. Owns a std-only *presenter* (`screen::ScreenView`) that snapshots each screen from the model, a pure text renderer for the headless smoke path, and the libcosmic app shell behind the `cosmic` feature (see below). |

Wizard flow:


```
Welcome → Disk → Layout → User → Network → Pengy → Summary → Progress
```

The **Pengy** screen is optional: base URL, API key and model may all be left
empty, and the plan then carries no `[pengy]` section at all — Pengy keeps the
defaults the image ships (the local endpoint, no key, no model).

## Requirements

- Rust toolchain (`cargo`, `rustc`) — the workspace builds and tests with the
  **standard library only** in its default configuration.
- No network, root, display, or hardware is needed for the test suite.

## Build & test

```sh
# Run the full workspace suite (model + gui, headless).
cargo test

# Run just the state-machine suite.
cargo test -p pengyos-install-model

# Headless smoke run: drive the whole wizard from a scripted answer set and
# print the derived plan. No window is opened.
cargo run -p pengyos-install-gui -- --answers answers.json
```

The `--answers <file>` path reads a JSON object of answer keys and walks the
whole machine to completion with no display — this is how the suite verifies
behaviour offline.

## The `cosmic` feature

The libcosmic back-end (`cosmic-desktop`, `cosmic-config`, `cosmic-apps-core`,
`cosmic-theme`) is **feature-gated behind `cosmic`, which is off by default**.
These crates are git-hosted under pop-os (not on crates.io) and pull a large
tree (iced, winit, wayland). Declaring them by default would force the whole
workspace to resolve them from git on every command and break the offline
`cargo test`. They are pinned by revision and wired into the `cosmic` feature in
the "Gui: build the libcosmic app shell" iteration, which builds where
libcosmic is available. The exact revisions are recorded in
[`COSMIC_DEPS.md`](COSMIC_DEPS.md).

```sh
# Build the actual libcosmic app (requires the libcosmic toolchain/registry).
cargo build --features cosmic
```

## Derived plan

`model` derives the exact `install.toml` and a human-readable layout plan from
the answers. `install.toml` is golden-tested for a scripted answer set so both
front-ends emit identical output.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
