# PengyOS install-tui — the console installer for Core and Server

## Summary
The installer the Core/Server install stick runs on tty1: a full-screen (curses)
installer with a line-by-line fallback, driving the `pengyos-install image`
engine.

## Purpose
US-CORE-1 + "a normal way to install". A Server is a homelab machine: desktop
PCs, mini PCs, an old laptop. It is installed with a monitor and keyboard
attached, then run headless and reached over SSH, by its owner or by an agent
(Pengy, Claude Code). So the installer must:

- work on the bare Linux console, where nothing can be pasted, and on old
  1024x768/1366x768 panels;
- leave the machine reachable over SSH, which is key-only, without anyone typing
  a public key by hand;
- install exactly what the GUI installs (same engine, same validation).

Unattended / fleet installs are a non-goal (see below).

## Deliverable
The Python package `pengyos_install_tui`:

- `session`: the pages (disk, name, account, ssh, pengy, [login], review),
  their fields, and the rules. Pure: no terminal, no disks, no network (the
  key fetcher is injected).
- `screen`: the full-screen front-end. `App` (normalised key names in, `Frame`s
  out) holds all the behaviour and never imports curses; `run_screen` is the
  thin curses driver.
- `plain`: the line-by-line front-end over the same session.
- `keys`: import keys from GitHub (`https://github.com/<user>.keys`), an
  `http(s)://` URL, or `user@host[:port]` (that machine's `~/.ssh/authorized_keys`
  over SSH with a password).
- `install`: load the payload manifest and disks, pick a front-end, install.

## Interface
- `pengyos-install-tui install [--payload DIR] [--plain] [--reboot]`
- Exit 0 installed; 1 not installed (quit, not confirmed, or failed); 2 usage.

Front-end choice: full screen when stdin and stdout are TTYs, `TERM` has a
terminfo entry and the terminal is at least 80x24; otherwise plain. `--plain`
forces plain.

## Rules
- Every field is validated by `pengyos_install.image.ProvisionAnswers.validate`
  (hostname, username, password ≥ 6 chars, SSH key lines, Pengy URL/key/model).
- Disks: the boot stick, mounted disks and disks under `MIN_DISK` are listed with
  the reason and cannot be chosen. One usable disk is preselected.
- SSH: imported keys and a pasted key combine. GitHub keys are tagged
  `<user>@github`; other sources keep their comments. Only lines the engine
  accepts as keys are kept (options lines are skipped and counted). For
  `user@host` the password goes to ssh through `SSH_ASKPASS` (never argv, never
  disk), ssh runs without a controlling terminal, password logins only, and the
  host key is trusted on first use into a throwaway known_hosts. An import error
  (no such user, refused password, no network, no file) stays on the page with
  the reason; a refused password puts the cursor on the password. No key at all
  needs a second Enter after a warning.
- Pengy: optional. A key or model without a base URL is refused.
- Nothing is written before the user types `erase`. A failed install returns to
  the review page with the answers kept.

## Verification (tests are first-class)
- `session`: every page's rules, offline.
- `screen.App`: whole installs driven by key names, asserted on rendered frames,
  including back navigation, quit, failure and every screen fitting 80x24.
- `test_terminal`: the real curses driver in a pty (keys in, a logged frame out).
- `plain`: whole installs over fake stdin.
- Under QEMU (docs/qemu-testing.md §5b): the stick's tty1 path installs a disk
  that boots, with the chosen hostname, user, keys and Pengy settings.

**GitHub-ready contract** (asserted by `test_github_ready`): README.md, LICENSE
(MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md, `.gitignore`, and a full
green test suite.

## Acceptance criteria
- `pytest -q` green; test_github_ready green.
- An installer stick boots to the full-screen installer on tty1 and installs.

## Non-goals
- Unattended installs (`install.toml`, answer files). The `run` / `auto` answer-set
  commands were removed in 0.2.0: they described layouts and editions the engine
  cannot install, and nothing consumed them.
- Wi-Fi setup, RAID, custom partitioning: the engine's only layout is the whole
  disk.
- The GUI (`tools/pengyos-install-gui`).

## Constraints
- Standard library only; Python ≥ 3.11.
- Tests run as a non-root user with no network.
