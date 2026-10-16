# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- The shipped dev and CUDA boxes put `~/.cargo/bin` and `~/.local/bin` on the
  PATH only for bash login shells (`/etc/profile.d`); zsh, the shell `enter`
  gives a PengyOS user, never read it. `guests.d/*.toml` now write one
  `/etc/pengyos-dev-path.sh`, sourced from `profile.d` for bash and from a
  marker-guarded line in the box's `/etc/zsh/zshenv` for zsh, and install
  `zsh` with the packages so that file is the package's own. Locked by
  `tests/test_guests_d.py`.

## [0.2.0] - 2026-09-29

The dev box: the tool now ships in the Server (and so Desktop) image and was
exercised end to end against real podman/distrobox in QEMU.

### Added

- Default guest definitions: the image's `/usr/share/pengyos/guests.d/*.toml`,
  overridden by name from `~/.config/pengyos/guests.toml`; `--config FILE`
  uses one file instead. `guests.d/dev.toml` (Ubuntu 24.04 build box) and
  `guests.d/cuda.toml` (CUDA 13.1, NVIDIA images only) ship with the tool.
- Guest fields `setup` (idempotent shell steps run as the user after the
  packages), `nvidia` (`distrobox create --nvidia`) and `description`.
- `enter NAME -- CMD...` runs one command and returns its exit code; a
  declared guest that does not exist yet is created and provisioned first.
- `apply [NAME...]` applies only the named guests.
- pacman (Arch) package manager; `list` shows image, state and description.

### Changed

- `enter` is attached to the terminal (it captured output, so interactive
  shells hung); `apply` streams pulls and installs too.
- Commands run through a login shell (`sh -lc 'exec "$@"'`), so the guest's
  `/etc/profile.d` PATH applies (cargo, uv).
- apt refreshes its package lists before installing; apk runs under sudo
  (distrobox enters as the user).

### Fixed

- The argv for real distrobox: `create --yes … --volume` (was `--mount`),
  `rm --force` (there is no `distrobox destroy`), `distrobox-export --app` run
  inside the guest (there is no `distrobox export`).
- `podman container exists` decides whether a guest exists (`podman inspect`
  also matched an image of the same name).

### Tested

- **GitHub-ready contract** (`tests/test_github_ready.py`). Strengthens the spec's
  GitHub-ready requirement (spec: Verification) from "the files exist" to a
  genuinely verified contract: the LICENSE is a complete MIT text with the
  correct copyright holder, the README documents every CLI command (spec:
  "Docs are authored — keep them in sync" — the docs must not drift from the
  code), the CHANGELOG has a current (Unreleased / versioned) section, and the
  `pengyos-guest` console script is declared in `pyproject.toml`.

### Added

- **No-host-writes sandbox guarantee** (`pengyos_guest.sandbox`). Turns the
  spec's "no host writes outside the guest's home mount and the exports you
  declare" policy into an assertable contract:
  - `declared_host_paths(guests)` — the allowlist of sanctioned host paths,
    exactly the `host:guest[:mode]` bind-mount specs declared in each guest's
    `mounts`.
  - `undeclared_host_paths(argvs, allowlist)` / `is_sandboxed(argvs, allowlist)`
    — scan every argv token for absolute host paths (`/`-prefixed) that are not
    in the allowlist. Non-path tokens (image refs, guest names, flags, package
    names) are ignored.
  - `SandboxGuard` — holds the allowlist and, via `check(argv)`, rejects any
    command that references an undeclared host path.
  - **Pre-execution wiring.** `SubprocessRunner` / `FakeRunner` take an optional
    `guard`; `run()` checks the argv *before* executing.
  - `GuestManager.apply` locks the guard to the applied guest's declared mounts
    (`_lock_sandbox`) and surfaces a guard violation as a clean `GuestError`.
  - `tests/test_sandbox.py` — module tests plus full-lifecycle coverage
    (apply/enter/export/destroy) asserting no undeclared host path ever reaches
    the runner.
- `pengyos_guest.podman.Podman` — wraps the `podman` CLI behind the mockable
  `Runner`. Builds the exact argv for `inspect` / `container_exists` / `stop` /
  `remove` and a raw `run` passthrough; production uses `SubprocessRunner`,
  tests use `FakeRunner`.
- `pengyos_guest.distrobox.Distrobox` — wraps the `distrobox` CLI behind the
  mockable `Runner`. Builds argv for `create` (with per-mount `--mount` flags),
  `enter` (interactive shell or `-- <cmd...>` for provisioning), `destroy`
  (`--yes`), `list`, and `export` (`--add`).
- `FakeRunner` now records the `env` passed to each call (`envs` / `last_env`),
  so env-passing through the engines is assertable.
- `tests/test_podman.py` and `tests/test_distrobox.py` — argv golden + scripting
  + env passthrough for both engines.

- `pengyos_guest.pkgspec` — resolves the package manager from a guest's image
  name (apk/dnf/apt/nix-env, `apk` default) and builds the exact install argv
  (`apk add --no-cache`, `sudo dnf install -y`, `sudo apt-get install -y`,
  `nix-env -iA nixpkgs.<pkg>`), plus `provision_argv` for the full
  `distrobox enter <name> -- <install>` command.
- `pengyos_guest.engines.GuestManager` — orchestrates the two engines for the
  four CLI operations:
  - `apply` — idempotent create + provision. Skips `distrobox create` when the
    container already exists (checked via `podman inspect`); always (re)provisions
    the declared packages. Raises `GuestError` on a non-zero create/provision.
  - `enter` — `distrobox enter <name>` (shell) or `-- <cmd...>`.
  - `export` — registers the app with distrobox and writes a host-side
    `sh` launcher (`~/.local/bin/<app>`, overridable via `$PENGYOS_LAUNCHER_DIR`
    / `$XDG_BIN_HOME`) that execs `distrobox enter <name> -- <app>`.
  - `destroy` — idempotent teardown (`podman stop --ignore`, `podman rm
    --ignore`, `distrobox destroy --yes`).
- CLI handlers for `apply`/`enter`/`export`/`destroy` (previously dispatch-only):
  each prints a one-line summary; `apply` reports create/provision per guest.
  Missing binaries (`podman`/`distrobox` not on PATH) now yield a clean
  `error:` message and exit code 2 instead of a traceback.
- `FakeRunner`/`GuestManager` are injectable via a `manager=` kwarg on the CLI
  handlers and a `GuestManager(runner=...)` constructor, so end-to-end CLI
  dispatch is testable offline.
- `tests/test_pkgspec.py`, `tests/test_engines.py`, and CLI end-to-end tests
  for `apply`/`enter`/`export`/`destroy` (argv golden, idempotency, launcher
  contents, and the "no undeclared host paths in argv" contract).
- `SubprocessRunner` catches `FileNotFoundError` and returns a clean
  `returncode=127` result so a missing tool is a normal command failure.

- A concrete, valid `guests.toml` at the repo root declaring two guests
  (`wolfi`, `nixos`), each with all five declarative fields: `name` (the table
  key), `image`, `packages`, `exports`, and `mounts`. This is the file consumed
  by `pengyos-guest apply guests.toml`.
- `tests/test_guests_toml.py` — verifies the shipped file parses and that every
  guest exposes all five fields, plus that `list` reads it.

## [0.1.0] - 2026-02-19

### Added

- Repository scaffold for `pengyos-guest`: src-layout package,
  `pyproject.toml` with a `pengyos-guest` console entry point, MIT `LICENSE`
  (Copyright (c) 2026 Pat Wendorf), `README.md`, `CHANGELOG.md`, `.gitignore`.
- Standard-library-only TOML config loader (`pengyos_guest.config`) with a
  `Guest`/`Guestbook` model and a `tomllib` backfill for Python 3.10.
- Mockable `Runner` abstraction (`pengyos_guest.runner`) — a subprocess runner
  for production and an in-memory fake for tests.
- CLI (`pengyos_guest.cli`) with `list` fully implemented and dispatch for
  `apply`/`enter`/`export`/`destroy`.
- Test suite covering config, runner, CLI, and the GitHub-ready contract.
