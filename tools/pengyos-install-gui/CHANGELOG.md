# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to Semantic Versioning.

## [Unreleased]

### Changed
- **"Log in automatically" now says it also skips the lock screen after
  sleep.** The image no longer locks the automatic-login user's session on
  sleep or idle (cosmic-greeter and cosmic-idle patches), so the switch's
  description says so.

### Added
- **A Pengy screen in the wizard.** The flow is now
  `Welcome → Disk → Layout → User → Network → Pengy → Summary → Progress`. The
  screen asks for the assistant's base URL, API key and model, and the derived
  `install.toml` gains a `[pengy]` section carrying exactly the keys that were
  answered. **The screen is skippable**: an empty base URL is a valid answer,
  the plan omits `[pengy]` entirely, and Pengy keeps the defaults the image
  ships. A filled answer is validated — a base URL that is not http(s), or a
  model/key containing a space, blocks Continue with a reason. The API key is
  masked in the view (a length, like the account password) and reaches the
  engine on stdin, never on argv.
### Fixed
- After flipping "Log in automatically", Continue had to be clicked twice:
  libcosmic's settings-row `.toggler()` wraps the row in a list button around
  the switch, and the row button kept the pointer after the switch's click, so
  the next click was swallowed. The row now uses the plain switch as its
  control. Checked in QEMU: switch on, one Continue reaches the summary.

### Added
- "Log in automatically" switch on the user page (off by default) and a
  Login line in the summary; passes `--autologin` to the engine.

### Added

- **`test_github_ready` — the GitHub-ready contract, asserted.** A new
  workspace-level integration test (`model/tests/github_ready.rs`) verifies the
  contract from the spec: `README.md`, `LICENSE` (MIT, *"Copyright (c) 2026 Pat
  Wendorf"*), `CHANGELOG.md`, `.gitignore` all exist and are non-empty; the
  workspace `Cargo.toml` declares `[workspace]` and both members; and the `model`
  crate is std-only (no third-party dependencies). It locates the workspace root
  via `CARGO_MANIFEST_DIR` (one level up from `model/`), so it runs in the
  default `cargo test` for the model crate — std-only, no network, no display,
  never gated behind the `cosmic` feature. The `gui` crate renders the model;
  the model suite is green (76 tests across the workspace).

### Fixed

- **The cosmic app shell now compiles (`cargo build --features cosmic`).**
  Against the pinned libcosmic rev `03d7dcb` (pop-os/libcosmic + its iced
  submodule at `b2419520`), the app now builds to a finished binary with **zero
  warnings and zero errors**. Two things made this possible: the environment now
  has **github access** (the iced submodule + pop-os git deps resolve from git —
  the earlier "cannot compile here" note no longer holds), and the remaining
  lifetime/ownership bugs in the renderers were fixed. `text_input`/`secure_input`
  tie their lifetime to the `Cow` value, so the value args must be owned
  (`Option<String>` fields are passed via `.clone().unwrap_or_default()`); the
  per-screen renderers now take their `ScreenView` by reference and build owned
  (`'static`) widgets so they escape the borrow; and `cosmic_app` was made a
  `pub mod` so the bin can reach `AppModel`/`Flags`. Running the app still needs a
  display server (not available here), so this is **compile-verified, not run**.
  The default (std-only) `cargo test` is unchanged and green.

- **libcosmic 1.0.0 (rev `03d7dcb`) widget API drift in the cosmic app shell.**
  Every widget call in `gui/src/cosmic_app/cosmic_app_impl.rs` was audited
  against the pinned cosmic source and corrected: `text::heading2`/`text::body1`
  → `text::heading`/`text::body`; `text_input(..).password(true)` →
  `widget::secure_input(placeholder, value, None, true)` (no visibility toggle,
  `hidden = true`); the 3-arg `radio(label, selected: bool, on_select)` → the
  4-arg `radio(label, value: V, selected: Option<V>, on_select)` with a `Copy`
  value — the disk `String` is not `Copy`, so the disk/layout radios now use the
  option **index** as the `Copy` value and map it back to the disk/layout in the
  closure; the removed `button::text(..).disabled(bool)` →
  `button::text(..).on_press_maybe(if enabled { Some(msg) } else { None })`; and
  the old `widget::checkbox(label, Option<bool>, msg)` free fn →
  `widget::checkbox(is_checked).label(v.hint.clone()).on_toggle(Message::AcceptTerms)`
  (cosmic's `checkbox` now takes only the checked state; the label/toggle are
  configured on the returned builder). The app's `update` now routes through the
  shared [`crate::router`] (`MessageRouter` trait + pure `route` fn) as the single
  source of truth. All signatures verified against the pinned cosmic source; the
  offline suite is green (74 tests, std-only, no display). The cosmic build
  (`cargo build --features cosmic`) now compiles against the pinned rev — see the
  top entry — but still needs a display to *run*.

### Fixed

- **cosmic app shell module resolution.** `gui/src/cosmic_app.rs` declared
  `mod cosmic_app_impl;` but the file lived at `gui/src/cosmic_app_impl.rs` (a
  sibling). Rust resolves a submodule of a nested module from the module's own
  directory, so the compiler wanted `gui/src/cosmic_app/cosmic_app_impl.rs`. The
  file was moved into `gui/src/cosmic_app/`. With `--features cosmic` the build
  now gets past module resolution (it only fails on the expected missing `cosmic`
  crate in the offline env); the offline suite is unchanged (feature off by
  default), 65 tests, all green.

### Added

- **Presenter layer (`screen::ScreenView`).** Each wizard step is now turned
  into a plain, std-only `ScreenView` snapshot — title, heading, field values
  (the password only as a length, never plaintext), whether "Continue" is
  enabled, and the blocker message — via `screen::view_of(model, step)`. This is
  the single source of truth for what a screen shows: the text renderer formats
  it for the headless smoke path, and the libcosmic app maps it onto COSMIC
  widgets. The model's private state is never reached. Covered by 14 new gui
  unit tests; the workspace suite is now 65 tests, all green, std-only, no
  display/network.

### Changed

- **Text renderers now consume the `ScreenView`.** `render`/`render_all` derive
  the view first and format it, so the renderer and the cosmic front-end share
  one representation. Output is byte-for-byte unchanged (all existing golden
  tests pass).

### Added

- **libcosmic app shell (`gui/src/cosmic_app.rs` + `cosmic_app_impl.rs`).**
  Build-gated behind the `cosmic` feature (off by default). `AppModel` wraps the
  pure [`Model`] as the single source of truth and maps widget messages onto
  model mutations via its own `next`/`back`/validators; `main.rs` launches it
  with `cosmic::app::run`. The welcome screen renders a terms checkbox; the disk
  screen renders a purpose-built radio disk-selector; the remaining screens
  (layout, user, network, summary, progress) render from the same `ScreenView`.
  On reaching `Progress` the app writes the derived `install.toml` and spawns the
  external `pengyos-install` backend. **Not compiled by the offline, std-only
  build** (the `cosmic` feature is off by default); it compiles where libcosmic
  is available — github access + the wgpu/Vulkan stack — against the pinned rev
  in `COSMIC_DEPS.md`. Running it still needs a display server.

### Added

- **`COSMIC_DEPS.md`** — pins the libcosmic crates (umbrella `libcosmic` at
  `pop-os/libcosmic` rev `03d7dcb`, plus `iced`, `winit`, `smithay-clipboard`)
  and documents how to wire them into `gui/Cargo.toml` behind the `cosmic`
  feature where libcosmic is available.

## [0.1.0] — 2026-09-26

### Added

- **Workspace scaffold** — the `pengyos-install-gui` Rust workspace with two
  crates:
  - `pengyos-install-model`: the pure, headless wizard state machine (steps,
    validation, back-navigation, derived `install.toml` + layout plan). Standard
    library only; 100% cargo-testable with no display.
  - `pengyos-install-gui`: the libcosmic front-end. Renders the model to text
    (pure, display-free) by default, with a `--answers <file>` headless smoke
    path that walks the whole wizard from a scripted JSON answer set.
  - The libcosmic app shell is feature-gated behind `cosmic` (off by default)
    so the workspace builds and tests offline with the standard library only.
- GitHub-ready metadata: MIT LICENSE (Copyright (c) 2026 Pat Wendorf), README,
  CHANGELOG, and `.gitignore`.

### Added

- **Wizard state machine** (`pengyos-install-model`): the full wizard as a pure,
  headless state machine — steps `Welcome → Disk → Layout → User → Network →
  Summary → Progress` — with per-step validators, message-based blockers, and
  back-navigation. The step order is the single source of truth for ordering,
  back-nav, and completion, mirroring `pengyos-install-tui`'s model so both
  front-ends agree on behaviour. Covered by 13 model unit tests plus 5 headless
  integration tests (43 tests across the workspace, all green, std-only, no
  display/network).

### Fixed

- **`install.toml` escaping.** The derived plan interpolated answer values
  straight into TOML basic strings, so a hostname or SSID containing `"`,
  `\`, or a control character produced *malformed* `install.toml` (the closing
  quote landed early). Values are now escaped per the TOML 1.0 basic-string
  rules (`\\`, `\"`, `\b`, `\t`, `\n`, `\f`, `\r`, `\uXXXX`), verified to
  round-trip and to parse cleanly with an external TOML reader. The
  `layout_plan` summary now also includes the chosen layout's human-readable
  description. Golden-tested across all layouts and special-character answer
  sets (48 tests across the workspace, all green).

### Added

- **`--answers` scripted mode verified + hardened.** The spec interface
  `pengyos-install-gui --answers a.json` renders the whole wizard from a
  scripted JSON answer set and completes with no window. Verified end-to-end
  against a real file: the wizard walks to `Progress`, derives the golden
  `install.toml`, and masks the password. The escaping path was checked through
  the CLI and the generated plan was confirmed to parse cleanly with an
  external reader (`tomllib`) with correct round-tripping. Added unit tests for
  the CLI `--answers` branch — a complete scripted run succeeds, a missing file
  and an incomplete answer set both fail with a clear message — plus committed
  fixtures in `gui/tests/fixtures/`. The workspace suite is now 51 tests, all
  green, std-only, no display/network.
