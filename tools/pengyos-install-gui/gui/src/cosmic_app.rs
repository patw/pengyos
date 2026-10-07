//! The libcosmic app shell — the *graphical* front-end of the installer.
//!
//! This module is **build-gated behind the `cosmic` feature** (off by default),
//! so the workspace's default `cargo test` never compiles it and stays offline
//! with the standard library only. It is compiled and built *where libcosmic is
//! available* (see the `cosmic` feature in `gui/Cargo.toml` and the pinned crates
//! in `COSMIC_DEPS.md`).
//!
//! The shell owns three responsibilities:
//!
//! 1. **Render** the [`screen::ScreenView`] produced by the pure model onto
//!    COSMIC widgets — one screen per model step. The welcome and disk screens
//!    (this iteration's focus) get full, purpose-built widgets; the remaining
//!    screens are rendered from the same [`screen::ScreenView`] so both
//!    front-ends agree on what each screen shows.
//! 2. **Drive** the [`Model`] through messages emitted by those widgets
//!    (accept terms, pick a disk, choose a layout, fill the account, …), using
//!    the model's own `next`/`back`/validators for ordering, back-nav and
//!    blockers.
//! 3. **Wire the run**: once the wizard reaches [`Step::Progress`] the derived
//!    [`Model::install_toml`] plan is written to disk and the external
//!    `pengyos-install` backend is invoked to perform the install.
//!
//! The rendering below is written against the current libcosmic
//! (`cosmic-widgets` / `cosmic-app`) API; it cannot be compiled in the offline
//! test environment, which is exactly why the feature is off by default.

#[cfg(feature = "cosmic")]
mod cosmic_app_impl;

#[cfg(feature = "cosmic")]
pub use cosmic_app_impl::{AppModel, Flags, Message};
