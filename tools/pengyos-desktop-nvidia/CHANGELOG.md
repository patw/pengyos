# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-10-04

### Added
- **`driver-not-stubbed` rule.** `lint` fails when a `modprobe.d/*.conf` under
  `board/` has `install nvidia…` or `blacklist nvidia…`. The Desktop board's
  `60-pengyos-amdgpu.conf` routed every `nvidia*` module to `/bin/true`, and
  Desktop NVIDIA reuses that board, so 26.10.6 shipped without a working
  driver.

## [0.3.0] - 2026-10-03

### Changed
- **Two NVIDIA editions.** `lint` checks `pengyos_server_nvidia_defconfig`
  (the driver's compute half) as well as `pengyos_desktop_nvidia_defconfig`:
  both must set `BR2_PACKAGE_NVIDIA_OPEN`, the Desktop's also
  `BR2_PACKAGE_NVIDIA_OPEN_GRAPHICS` and libglvnd; both targets must be
  registered; `--buildroot` resolves each. The JSON report's
  `variant_defconfig` is now `variant_defconfigs` (a list).
- `lint` reads `<repo>/targets.toml` if the tree has one, else the repository
  root's (`<repo>/..`): `buildroot-external/targets.toml`, a duplicate of the
  root registry, is gone.

## [0.2.0] - 2026-09-29

### Changed

- **The tool is now the edition's containment lint only.** The driver moved from
  Buildroot's `nvidia-driver` (the legacy 390 branch: no Turing+, no GBM, does not
  build on kernel 7.2 — it could never drive the fleet's RTX 30/50 GPUs or a
  Wayland session) to the in-tree `package/nvidia-open` (open kernel modules +
  matching userspace from one `.run`). The edition is composed from the
  `nvidia` defconfig layer. `lint` now checks: `driver-in-variant`
  (`BR2_PACKAGE_NVIDIA_OPEN` + `BR2_PACKAGE_LIBGLVND`), `driver-not-in-default`
  (any `BR2_PACKAGE_NVIDIA_OPEN*` in another defconfig), `no-legacy-driver`,
  `package-declared`, `version-pinned` (`.hash` covers the `.mk` version),
  `target-registered`, and with `--buildroot` a kconfig `driver-resolves` run in
  a scratch `O=` dir (the old resolve wrote a scratch defconfig into the
  Buildroot checkout and reused the shared output dir).
- `requires-python` is `>=3.11` (the code uses `tomllib`).

### Removed

- `emit`, `wire`, `record`, the variant data model, the modular-Xorg gating proof
  (`nvidia_config`), the vendored `nvidia-driver` recipe fixtures, and their
  tests. The modprobe/udev/PRIME files `wire` put in the Desktop overlay were
  wrong (`nvidia-drm GSP-firmware=1` is not a parameter; the udev rule matched
  non-existent `id_vendor` attributes) and now ship, corrected, from the package.

### Added

- **Apply the corrected `wire` into `buildroot-external` and confirm `lint` + gate green**
  (implementation.md fix-pass item 4, the final fix-pass checkpoint). The
  corrected `wire` was already committed (`4fb99f2` defconfig + plumbing,
  `7a59428` target registration), but the end-to-end acceptance was never run as
  a discrete step: (1) run `wire --repo buildroot-external` against the REAL
  tree and confirm it is **idempotent** -- a re-run leaves the tree
  **byte-identical** (`git status` clean; the only `diff` noise is a pre-existing
  dangling symlink `seatd.service -> /usr/lib/systemd/system/seatd.service` that
  `cp -r` carried into a throwaway snapshot); (2) confirm
  `pengyos_desktop_nvidia`'s `lint` **PASSES** against the real tree
  (`lint: ok` -- blob-in-variant + blob-not-in-default + driver-resolves +
  target-registered all green); (3) confirm the repo-level integration gate is
  **green** -- `scripts/pengyos_gate.py run --repo . --mode fast --base main`
  reports `2/2 checks passed` (`wiring-lint:pengyos-desktop-nvidia ok` +
  `group-overlay:.../etc/group ok`); the unscoped merge-time run additionally
  FAILS `wiring-lint:pengyos-server-clis-wire` (7 violations) which is a
  **different wave's package**, out of scope for this task. The variant
  defconfig header identifies it as the Desktop NVIDIA variant and the plumbing
  (`modprobe.d/10-nvidia-drm.conf` modeset=1 + GSP-firmware=1,
  `udev/60-nvidia-runtime-pm.rules` keyed on `0x10de`, the executable
  `usr/local/bin/prime-render-offload`) all live inside the board overlay
  `board/qemu-x86_64-desktop/rootfs-overlay/`. `pytest -q` = 165 passed, 5
  skipped; `PENGYOS_IT=1` = 170 passed. No code change -- this is the
  acceptance verification that closes the fix pass.

- **Register the `pengyos-desktop-nvidia` target in `targets.toml` (idempotent)**
  (implementation.md fix-pass item 3). The `wire`'s `_register_target` writes the
  `pengyos-desktop-nvidia` entry into the tree's own registry
  (`repo/targets.toml`) idempotently -- `action="added"` on the first run when
  the target is absent, `action="present"` + a byte-identical file on a re-run
  when it is already declared; a file that exists but has no `[[targets]]`
  array is a loud `PengyOSError` (never silently appended into a non-registry).
  `lint`'s `RULE_TARGET_REGISTERED` is the containment counterpart (the blob is
  only *usable* once the target is declared). This run pinned the **exact
  committed** `buildroot-external/targets.toml` (`COMMITTED_BRE_TARGETS_TOML`,
  with a self-check that fails on drift) and added
  `test_wire_target_registration_idempotent_against_committed_targets_toml`
  (present / byte-identical) and
  `test_wire_target_registration_appends_to_committed_registry` (added then
  present), so the idempotency is proven against the real registry, not just a
  synthetic one. `lint --repo buildroot-external` passes against the real tree
  (blob-in-variant + blob-not-in-default + driver-resolves + target-registered).
  `pytest -q` = 165 passed, 5 skipped (was 163).

- **Fix `wire` to install the KMS/Wayland plumbing into the desktop board's
  `rootfs-overlay` (implementation.md "Fix pass", item 2).** The `wire` target
  was writing the plumbing (`modprobe.d` / `udev` / the executable PRIME
  render-offload wrapper) at the **BR2_EXTERNAL tree root** -- Buildroot ignores
  the tree root; it only copies a board's
  `board/qemu-x86_64-desktop/rootfs-overlay` into the image. So the blob's
  `nvidia-drm.modeset=1` / GSP / runtime-PM / PRIME config never landed in the
  image. `wire` now installs into the board overlay, mirroring the sibling
  `pengyos-desktop-amd-prime` layout:
  - `board/qemu-x86_64-desktop/rootfs-overlay/etc/modprobe.d/10-nvidia-drm.conf`;
  - `.../etc/udev/rules.d/60-nvidia-runtime-pm.rules`;
  - `.../usr/local/bin/prime-render-offload` (chmod 0755).
  - `_find_overlay(repo)` prefers the `qemu-x86_64-desktop` board (the bringup
    defconfig's target), falls back to the single board that ships an overlay,
    and **fails loud** (a `PengyOSError`) when there is no board, when there is
    more than one, or when the `qemu-x86_64-desktop` board is ambiguous -- a
    plumbing file can never silently land at the tree root again.
  - The fix is **idempotent** (byte-identical on a re-run) and the variant
    defconfig still installs at `configs/pengyos_desktop_nvidia_defconfig`
    (unchanged).
  - `tests/test_wire.py` gained `test_find_overlay_*` (prefer desktop board /
    single-overlay fallback / multiple-overlays-raises / no-board-raises) and the
    plumbing tests now assert the board-overlay paths (NOT the tree root); the
    CLI / grounding / package / integration suites were updated to create the
    board overlay before `wire`. `pytest -q` = 158 passed, 5 skipped (was 154).

- **Replace the base fixture with the CURRENT Desktop bringup defconfig**
  (implementation.md "Fix pass", item 1). The committed base defconfig
  `pengyos_desktop_nvidia/_fixtures/pengyos_desktop_bringup_defconfig` was the
  **SERVER** defconfig -- its header literally read "PengyOS Server" and it
  carried the Server toolset (sshd, mosh, tmux, ripgrep, jq, rootless podman,
  polkit) -- so the opt-in NVIDIA variant was built off the wrong base.
  Rewrote it as a clean **Desktop bring-up** defconfig:
  - Header now identifies it as "PengyOS Desktop bring-up (Track B) — Wayland
    graphics stack"; the Server toolset is gone (only the Core/Server shared
    systemd base stays: dbus, coreutils, util-linux-mount, kmod, iproute2, bash,
    chrony, ca-certificates, openssl);
  - The Desktop bring-up content is intact: weston/foot, Mesa (virgl + softpipe,
    iris), fontconfig/freetype/dejavu, locales, portals, seatd, the open AMD
    GPU half (pengyos-desktop-amd-prime), i915 firmware, and the modular-X.org
    recipe (`BR2_PACKAGE_XORG7` / `XSERVER_XORG_SERVER` /
    `XSERVER_XORG_SERVER_MODULAR`) with the parent-gotcha documented;
  - The blob symbol set stays ABSENT from the base (only comments mention
    `BR2_PACKAGE_NVIDIA_DRIVER*`, never with a `=`), so `lint`'s
    `blob-not-in-default` still passes, and `emit` produces a strict superset
    (every base line preserved, only the blob symbols added).
  - `pytest -q` = 154 passed, 5 skipped (unchanged; the suite already covered
    the strict-superset + leak + modular-X.org contracts, which now hold against
    the corrected base).

- **Finalize the GitHub-ready contract** (`tests/test_github_ready.py`). The
  spec's GitHub-ready contract asserts the repo is publishable as-is: the four
  docs the spec names (`README.md`, `LICENSE` MIT + "Copyright (c) 2026 Pat
  Wendorf", `CHANGELOG.md`, `.gitignore`) present and non-empty, plus "a full
  green test suite" and "Docs are authored — keep them in sync".
  - `tests/test_github_ready.py` (10 tests, all green):
    - `test_readme_present_and_nonempty` / `test_changelog_present_and_nonempty`
      / `test_gitignore_present_and_nonempty` — the three files exist and have
      content;
    - `test_license_is_mit_with_copyright` — the LICENSE carries both the "MIT
      License" header and the "Copyright (c) 2026 Pat Wendorf" line;
    - `test_pyproject_present_and_parses` — `pyproject.toml` parses and names the
      `pengyos-desktop-nvidia` package + console script;
    - `test_package_importable` / `test_version_declared_and_consistent` — the
      package imports and `pyproject` version ==
      `pengyos_desktop_nvidia.__version__`;
    - `test_docs_present_and_in_sync` — all six authored docs
      (`README`/`CHANGELOG`/`AGENTS`/`spec`/`prompt`/`implementation`) are present
      and non-empty;
    - `test_gitignore_covers_build_and_packaging_artifacts` — `.gitignore`
      ignores `build/`, `*.egg-info/`, `__pycache__/`;
    - `test_suite_is_green` — runs the *whole* suite green in a hermetic
      subprocess (not a recursive self-invocation), with the
      `PENGYOS_SUITE_CHECK_RUNNING` sentinel to break the cycle and `PENGYOS_IT`
      pinned off for a deterministic default run.
  - Proven meaningful, not pass-by-default: deleting `README.md` flips
    `test_readme_present_and_nonempty` + `test_docs_present_and_in_sync` to red;
    restoring it returns to green.
  - `pytest -q` = 154 passed, 5 skipped (unchanged; the suite was already green
    from the scaffold, this iteration finalizes and proves the GitHub-ready
    contract holds).

- **Ground the three core guarantees against the committed fixtures**
  (implementation.md: "Ground with fixtures"): deterministic emit, the
  blob-leak rule firing in the default defconfigs, and idempotent wire.
  - `test_lint.py` gained `test_lint_leak_matrix_every_symbol_every_default` --
    the full leak matrix: *every* blob symbol
    (`BR2_PACKAGE_NVIDIA_DRIVER` / `_MODULE` / `_PRIVATE_LIBS`) leaking into
    *every* default (`pengyos_desktop_bringup` / `pengyos_core_m2` /
    `pengyos_server`) fires `blob-not-in-default` with exactly one clear error
    per (symbol, default) pair. The prior leak tests covered one symbol in one
    default and one symbol in all three; this closes the gap and proves the
    rule is complete. Each row is an independent repo (one leak at a time), so a
    false positive or a dropped row surfaces as a missing error rather than a
    masked failure.
  - New `tests/test_grounding.py` grounds the three guarantees *end-to-end
    through the CLI* (not just the API) against the committed base defconfig:
    `test_emit_is_deterministic_through_cli` (two full `emit` runs produce
    byte-identical artifacts), `test_wire_is_idempotent_through_cli` (two full
    `wire` runs leave the tree byte-identical via the {path: bytes} idempotency
    oracle), `test_lint_surfaces_leak_through_cli` + `test_lint_clean_through_cli_exits_zero`
    (a blob-free repo lints to exit 0, a single injected leak flips it to exit
    2), and `test_emit_wire_lint_chain_through_cli` (the whole chain holds
    together, then the leak guard fires). The committed base fixture carries
    NVIDIA *comments* (no blob symbol with an `=`), so the leak tests also
    exercise the leak rule's word-boundary logic against real fixture data --
    the comments must not fire, and only the injected `=y` line does.
  - `pytest -q` = 154 passed, 5 skipped (was 148 / 5).

- **Scaffold** the `pengyos-desktop-nvidia` Python package:
  - `pyproject.toml` (setuptools backend, no runtime deps, `test` extra with
    pytest, `pengyos-desktop-nvidia` console script).
  - Package `pengyos_desktop_nvidia` with `emit`, `wire`, `lint`, `record`,
    `cli`, `errors`, and `__main__` modules mapping to the units of work in
    `implementation.md`. Each public helper raises `NotYetImplemented` pointing
    at its task until it lands (all four are later tasks).
  - Stable identifiers pinned for the later tasks: the variant defconfig name
    (`pengyos_desktop_nvidia_defconfig`), the three blob symbols
    (`BR2_PACKAGE_NVIDIA_DRIVER` / `_MODULE` / `_PRIVATE_LIBS`) plus the optional
    CUDA/OPENCL symbols, the KMS/Wayland plumbing filenames, the
    `pengyos-desktop-nvidia` target name, the three default defconfigs the blob
    must never leak into, the three `lint` rule ids, and the Tier-2 concession
    record schema.
  - CLI: `pengyos-desktop-nvidia emit/wire/lint/record` subcommands (matching
    the spec interface), dispatching to the four modules.
  - Test suite (`pytest`) with `test_github_ready`, CLI dispatch, package-import,
    and opt-in integration tests (`PENGYOS_IT=1`).
  - GitHub-ready docs: `README.md`, `LICENSE` (MIT, Copyright (c) 2026 Pat
    Wendorf), `CHANGELOG.md`, `.gitignore`.
- **Model the NVIDIA blob variant as data** (`variant.py`, task 2): a single,
  frozen, stdlib-only data model (`NvidiaVariant`) the later tasks render
  (`emit`), check (`lint`) and record (`record`) against. It captures:
  - the **symbol set** -- the three required blob symbols
    (`BR2_PACKAGE_NVIDIA_DRIVER` / `_MODULE` / `_PRIVATE_LIBS`) and the two
    optional accelerator symbols (`BR2_PACKAGE_NVIDIA_DRIVER_CUDA` /
    `_OPENCL`);
  - the **kernel-module requirement** -- the blob is an out-of-tree module that
    must build against the pinned kernel and can never be built-in;
  - the **KMS/Wayland plumbing** -- a modprobe.d drop-in (`nvidia-drm.modeset=1`
    + GSP firmware), a runtime-PM udev rule, and a PRIME render-offload wrapper,
    each with its exact content template;
  - the **Tier-2 record schema**.
  The blob symbols are the *real* Buildroot 2026.08 names (note the `_DRIVER`
  infix declared by `package/nvidia-driver/Config.in`) -- the scaffold's
  guessed, infix-less names did not resolve against the pinned tree, so the
  model corrects them. `emit.py`'s pinned identifiers now derive from this model
  (no value drift).
- **Implement `emit`** (task 3): render the opt-in blob variant into a
  directory.
  - The variant defconfig `pengyos_desktop_nvidia_defconfig` -- the committed
    base Desktop defconfig fixture (`pengyos_desktop_nvidia/_fixtures/`) with the
    blob symbol set appended, so the variant is always a *strict superset* of the
    base (every base line preserved verbatim; only the allowed blob symbols are
    added). Deterministic, self-contained, no Buildroot checkout needed.
  - The blob block sets the three required blob symbols, the blob's X.org
    driver-path symbol (`BR2_PACKAGE_NVIDIA_DRIVER_XORG=y`, needed for
    `PRIVATE_LIBS` and the userspace driver to resolve -- the 2026.08 package
    gates them behind this symbol), and the optional CUDA/OPENCL symbols when
    opted in (`--cuda` / `--opencl`).
  - The KMS/Wayland plumbing (modprobe.d / udev / PRIME render-offload) written
    into the location each drop-in belongs in, and the Tier-2
    concession-record skeleton (driver version + license + blob hash +
    `reproducible=false`).
  - A tiny, dependency-free flat-TOML manifest reader (`nvidia.toml`:
    `driver_version` / `blob_hash` / `license`); a missing or malformed manifest
    fails with a clear `PengyOSError`.
  - Tests (`tests/test_emit.py`): strict-superset, blob-symbol presence,
    conditional optional symbols, plumbing content, record schema, determinism,
    and the manifest contract.
- **Reconcile the spec's "XORG OFF -- Wayland path" with the Buildroot 2026.08
  `nvidia-driver` recipe (task 4).** The blob's userspace GL/EGL/GLES libs -- and
  `BR2_PACKAGE_NVIDIA_DRIVER_PRIVATE_LIBS` (declared inside
  `if BR2_PACKAGE_NVIDIA_DRIVER_XORG`) -- are gated behind the blob's X.org
  driver-path symbol `BR2_PACKAGE_NVIDIA_DRIVER_XORG`, which `depends on`
  modular Xorg (`BR2_PACKAGE_XSERVER_XORG_SERVER_MODULAR`). The Wayland-native
  base ships no X.org server, so without a change the blob installs as a kernel
  module only (KMS via `nvidia-drm.modeset=1`, but no userspace libs).
  - Decided: **XORG posture is ON** (`emit` sets
    `BR2_PACKAGE_NVIDIA_DRIVER_XORG=y`), and **modular Xorg must be added to the
    base defconfig** for the blob's userspace libs to install (a base/wire
    follow-up, not an `emit` defect).
  - Added `pengyos_desktop_nvidia/nvidia_config.py`: a stdlib-only kconfig
    reader that parses the pinned `package/nvidia-driver/Config.in` +
    `.mk` and *proves* the three gating facts (XORG depends on modular Xorg;
    `PRIVATE_LIBS` inside `if XORG`; the `.mk` installs the libs only when
    XORG=y). The decision is now a grounded, verified fact (`confirm_decision`),
    not an assertion. Committed fixture copies of the pinned recipe under
    `_fixtures/nvidia-driver/` so the proof is hermetic; the opt-in suite
    (`PENGYOS_IT=1`) checks the real pinned checkout.
  - Wired the decision into the model (`variant.NvidiaVariant.xorg_gate` +
    `xorg_posture` / `xorg_requires_modular_xorg` / `adds_modular_xorg_to_base`
    / `drops_userspace_libs_without_modular_xorg`); the emitted blob block's
    XORG comment now points at the proof.
  - Tests (`tests/test_nvidia_config.py`): the parser (indentation vs.
    top-level keywords, trailing `#` comments on `endif`, line continuations,
    unclosed-if / stray-endif errors), the three gating facts, the mutation
    failures, the decision's agreement with the facts, and the opt-in
    against-the-real-tree check.
- **Add modular X.org to the base defconfig so the blob's userspace libs
  install** (task 5). The blob's X.org driver-path symbol
  `BR2_PACKAGE_NVIDIA_DRIVER_XORG=y` resolves only when modular Xorg
  (`BR2_PACKAGE_XSERVER_XORG_SERVER_MODULAR=y`) is in the build, and that symbol
  is the *choice member* whose *parent* (`BR2_PACKAGE_XSERVER_XORG_SERVER`) has
  no default and nothing selects it -- so a bare `MODULAR=y` does not enable its
  parent through `make <name>_defconfig`. The base Desktop defconfig fixture
  (`pengyos_desktop_bringup_defconfig`) now names all three explicitly
  (`BR2_PACKAGE_XORG7=y`, `BR2_PACKAGE_XSERVER_XORG_SERVER=y`,
  `BR2_PACKAGE_XSERVER_XORG_SERVER_MODULAR=y`); the XORG7 stack it pulls is
  resolved by Buildroot's `select`s. Verified against the pinned 2026.08 tree:
  the emitted variant resolves `BR2_PACKAGE_NVIDIA_DRIVER_PRIVATE_LIBS=y` (rung
  (b)) and the pinned `nvidia-driver` source + sha256 verify and extract (rung
  (c)). This is the base-defconfig change the task-4 decision requires; `emit`
  does not own the base -- it only renders the blob block on top of the base.
  - Tests (`tests/test_base_xorg.py`): the base fixture carries the modular-X.org
    recipe and names the parent explicitly; the emitted variant (a strict
    superset of the base) carries it too and the recipe is a *base* line, not a
    blob addition; the fixture documents the parent gotcha; and the opt-in suite
    (`PENGYOS_IT=1`) resolves the emitted variant against the real pinned
    checkout and asserts `NVIDIA_DRIVER` / `_MODULE` / `XORG` / `PRIVATE_LIBS`
    all land in `.config`.
- **Add a `lint` rule that the blob's userspace libs actually resolve** (not
  just the kernel module). The blob installs as an out-of-tree kernel module
  **and** a set of userspace GL/EGL/GLES libraries + the `nvidia_drv.so` X.org
  module; the 2026.08 `nvidia-driver` package gates the *entire* userspace
  driver behind `BR2_PACKAGE_NVIDIA_DRIVER_XORG` (which `depends on` modular Xorg)
  and declares `BR2_PACKAGE_NVIDIA_DRIVER_PRIVATE_LIBS` *inside*
  `if BR2_PACKAGE_NVIDIA_DRIVER_XORG` -- so without modular Xorg in the base the
  blob installs as a kernel module only and `PRIVATE_LIBS` drops silently.
  - Added `RULE_USERSPACE_LIBS_RESOLVE` (`"userspace-libs-resolve"`, the 4th
    `lint` rule id) + `lint.lint_userspace_libs(repo, buildroot, *,
    variant_text, as_json)`, which raises one `LintViolation` per dropped
    userspace lib. `check_userspace_libs(config)` treats a symbol as resolved
    only when present with value `"y"`.
  - Added `lint.resolve_variant_config(...)` -- rung (b): writes the variant to
    `<buildroot>/configs/<target>_defconfig`, runs
    `make O=<output> <target>_defconfig` (~0.3s, no compile; the kconfig `conf`
    binary is already built), reads `<output>/.config` back (`parse_config`),
    and always cleans up the scratch target. `make` is injectable for unit
    tests; a missing tree / output / `.config` or a non-zero make exit raise
    `PengyOSError`. The output dir is the toolchain-built sibling
    `<buildroot>/../output`. `lint_variant` stays a `NotYetImplemented` stub
    (the base-lint orchestration is a separate task).
  - Proved the silent drop against the REAL pinned 2026.08 checkout (opt-in):
    the emitted variant resolves `NVIDIA_DRIVER` / `_MODULE` / `XORG` /
    `PRIVATE_LIBS` all `y`; strip the base's modular-X.org recipe and only
    `NVIDIA_DRIVER` / `_MODULE` survive -- and the userspace-libs rule FAILS, so
    the drop surfaces as a lint error rather than a missing lib at runtime.
  - Tests (`tests/test_lint.py`, 19 tests): `parse_config`,
    `check_userspace_libs` (present / absent / not-set / both-absent),
    `resolve_variant_config` (writes + runs make + reads + cleans up, plus
    make-fail / missing-output / missing-tree / missing-text / no-config
    failures), `lint_userspace_libs` (pass / drop-fails / needs-buildroot /
    JSON), the base-lint-still-pending check, and the two opt-in real-tree
    cases (pass + modular-X.org-stripped fail).
- **Implement `wire`** (task 4): install the emitted blob variant into a
  `BR2_EXTERNAL` tree and register the `pengyos-desktop-nvidia` target,
  idempotently.
  - `wire.wire_variant(repo, *, as_json=False)` does four things: (1) ensures
    the tree skeleton (`external.desc` / `external.mk` / `Config.in`) exists --
    Buildroot 2026.08 rejects a tree without all three, and they *name* the tree
    "PENGYOS" (`BR2_EXTERNAL_PENGYOS_PATH`), shared with the other pengyOS
    defconfigs, so they are written only if absent and never clobbered; (2)
    installs the variant defconfig
    `configs/pengyos_desktop_nvidia_defconfig` (the base Desktop defconfig + the
    blob symbol set -- the *same bytes* `emit` produces, via the shared
    `emit.variant_defconfig_text()` helper, so the installed target and the
    emitted one can never drift); (3) installs the KMS/Wayland plumbing
    (modprobe.d / udev / the executable PRIME render-offload wrapper) into the
    tree subdirs `emit` mirrors; (4) registers the target -- automatic, since
    the defconfig now lives in a valid tree's `configs/`, so
    `make pengyos_desktop_nvidia_defconfig` resolves.
  - Idempotent: a re-run writes byte-identical files and leaves the skeleton
    untouched; a missing repo dir is created (parents included); a repo path
    that exists but is a file raises a clean `PengyOSError`. The report dict
    (JSON-serialisable) names the installed defconfig, plumbing, and whether the
    skeleton was created / the target registered.
  - Verified against the REAL pinned 2026.08 checkout (rung b): the wired tree
    resolves `make pengyos_desktop_nvidia_defconfig` and lands
    `NVIDIA_DRIVER` / `_MODULE` / `XORG` / `PRIVATE_LIBS` all `y`; the base
    `pengyos_desktop_bringup` defconfig is owned by the desktop tool, not this
    one, so `wire` installs only the NVIDIA variant (the blob-free default is a
    `lint` concern, task 5).
  - Tests (`tests/test_wire.py`): installed defconfig byte-identical to `emit`,
    strict superset of the base, all blob symbols set, plumbing content +
    executable wrapper, tree skeleton created / not-clobbered, idempotency
    (byte-identical re-run + skeleton not recreated), missing-parent-dir
    creation, JSON report, and the repo-is-a-file error. `test_package.py` and
    `test_cli.py` were updated: `wire` is implemented (no longer
    `NotYetImplemented`), and the opt-in integration suite points `wire` at a
    throwaway `tmp_path` repo instead of the tree root.
- **Implement `lint`** (task 5): prove the blob is *contained* -- present in the
  opt-in variant, absent from every default, and resolves. `lint.lint_variant(
  repo, *, buildroot=None, as_json=False)` orchestrates four rules, one clear
  error each:
  - **blob-in-variant** -- every blob symbol
    (`BR2_PACKAGE_NVIDIA_DRIVER` / `_MODULE` / `_PRIVATE_LIBS`) is set to `y` in
    `configs/pengyos_desktop_nvidia_defconfig`;
  - **blob-not-in-default** -- no blob symbol *appears* (any value) in any of the
    default Desktop/Core/Server defconfigs (`pengyos_desktop_bringup` /
    `pengyos_core_m2` / `pengyos_server`); the word-boundary regex
    (`\bBR2_PACKAGE_NVIDIA_DRIVER\b`) deliberately does not match
    `BR2_PACKAGE_NVIDIA_DRIVER_MODULE`;
  - **driver-resolves** -- the blob symbols the variant sets are declared by the
    vendored `nvidia-driver` package (offline symbol resolution against the
    real pinned recipe when present, else the committed fixture copy);
  - **userspace-libs-resolve** -- when `--buildroot` is passed, the rung-(b)
    userspace-libs rule (XORG + PRIVATE_LIBS land in `.config`) is run too.
  - `repo` is the BR2_EXTERNAL tree that ships both the variant and the defaults
    (in `configs/`); a missing tree / variant / default raises a clean
    `PengyOSError`. The blob-leak mutation now fires the leak rule (one error
    per leaking default) instead of `NotYetImplemented`.
  - Tests (`tests/test_lint.py`, +9): `lint_variant` passes for a clean repo,
    fails on a blob leak into one/every default (one error per default), fails
    when a blob symbol is missing from the variant, fails when the driver does
    not resolve (a typo the package does not declare), needs a tree / configs
    dir, JSON report, and the userspace-libs rule via a fake `make` (pass +
    drop-fails). `test_package.py` gained `test_lint_is_implemented`;
    `test_cli.py` replaced the "surfaces pending" lint test with pass + leak;
    `test_public_helpers_raise_not_yet_implemented` no longer lists `lint`.
- **Implement `record`** (task 6): write the machine-readable Tier-2 concession.
  - `record.record_concession(repo, *, manifest, reproducible=False,
    as_json=False)` reads the operator-pinned driver version / license / blob
    hash from the same `nvidia.toml` manifest `emit` pins, and writes two files
    into the BR2_EXTERNAL tree: the machine-record
    (`nvidia-tier2-concession.json` -- `tier` / `driver_version` / `license` /
    `blob_hash` / `reproducible=False`, plus an embedded note) and the
    human-readable note (`nvidia-tier2-concession.md` -- the same concession in
    prose, enumerating the machine-record fields). The machine record and the
    note carry the *same* fields, so a reviewer who does not parse JSON still
    sees the whole concession.
  - The record is deterministic: a re-run produces byte-identical files, so the
    concession is a stable artifact of the pinned manifest. The blob is
    proprietary, so the concession is *always* `reproducible=False`; passing
    `reproducible=True` to the API is refused (the whole point of a Tier-2
    record).
  - `record.TIER == 2`, `record.RECORD_FILENAME == emit.RECORD_FILENAME`
    (shared with `emit` so the two never drift), plus
    `record.HUMAN_NOTE_FILENAME` and `record.RECORD_FIELDS`
    (`tier` / `driver_version` / `license` / `blob_hash` / `reproducible`).
  - Tests (`tests/test_record.py`): machine-record schema (exactly the fields
    + note), tier + `reproducible=False`, manifest flow-through, the human note
    (markdown + covers every machine field), determinism, parity with `emit`'s
    machine record, the manifest contract (missing / missing-key / invalid),
    the `reproducible=True` refusal, and repo-parent creation. `test_package.py`
    gained `test_record_is_implemented` and no longer lists `record` among the
    `NotYetImplemented` helpers (renamed to `test_public_helpers_are_implemented`
    over all four public entry points); `test_cli.py` replaced the "surfaces
    pending" record test with pass + missing-manifest; the opt-in integration
    suite (`test_full_flow_dispatch`) now runs the full emit->wire->lint->record
    flow to green.

## [0.1.0] - 2026-09-27

### Added

- Scaffold the `pengyos-desktop-nvidia` tool for the opt-in NVIDIA blob Desktop
  variant (Tier-2 concession): the `emit`/`wire`/`lint`/`record` CLI surface,
  the pinned blob-symbol / variant / target identifiers, and a hermetic test
  suite.
