# Pinned libcosmic crates

The GUI crate (`gui/`) renders the installer onto COSMIC widgets. Those widgets
live in the libcosmic crates, which are **git-hosted under `pop-os`** (not on
crates.io) and pull a large tree (`iced`, `winit`, `wayland`, `smithay`, …).

This file pins the exact revisions the `cosmic` feature builds against. They are
**not** placed in `gui/Cargo.toml` by default: git-hosted crates force the whole
workspace to resolve them from git on every `cargo` command, which breaks the
offline, std-only `cargo test`. Instead, the "Gui: build the libcosmic app
shell" wiring step copies the `[dependencies.libcosmic]` block below into
`gui/Cargo.toml` (behind the `cosmic` feature) and builds where libcosmic is
available.

## Direct dependency

```toml
[dependencies.libcosmic]
git = "https://github.com/pop-os/libcosmic.git"
rev = "03d7dcb832bc6d15bc1bb07327a1aa426b2d5bc0"  # HEAD of pop-os/libcosmic
features = [
    "about",        # About widget (summary screen)
    "single-instance",
    "markdown",
    "qr_code",
    "wgpu",         # GPU-accelerated rendering
    "xdg-portal",   # xdg-portal dialogs on Linux
]
```

The crate is imported as `cosmic` (the libcosmic umbrella re-exports
`cosmic-desktop`, `cosmic-config`, `cosmic-widgets`, `cosmic-theme`,
`cosmic-apps-core`). The `cosmic-app-template` is the canonical reference for
the current `cosmic::Application` API.

## Transitive pop-os crates (resolved by `libcosmic`'s own manifest)

Pinned for reference / reproducibility; they are pulled in transitively and do
not need to be declared directly.

| Crate | Git | Pinned |
|-------|-----|--------|
| `iced` | pop-os/iced | `2157f9d` (HEAD) |
| `winit` | pop-os/winit | `bdc6610` (HEAD) |
| `smithay-clipboard` | pop-os/smithay-clipboard | `eebb028` (HEAD) |
| `libcosmic` (umbrella) | pop-os/libcosmic | `03d7dcb` (HEAD) |

> Re-pin to a fresh `rev = "<sha>"` before shipping; the values above are the
> HEADs at the time of writing and will drift.

## Build (where libcosmic is available)

```sh
cargo build -p pengyos-install-gui --features cosmic
```

The default `cargo test` (feature off) never touches these crates.
