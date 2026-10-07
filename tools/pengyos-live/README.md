# pengyos-live

Emit + lint the **PengyOS live/installer session overlay** — the overlay that
makes a PengyOS install medium boot into a live desktop with the installer
running by default.

`pengyos-live` produces the *live-session overlay* for an edition's install
medium: an auto-login live user, the installer launched on session start
(graphical when a session exists, console fallback otherwise), a live marker,
and a lint that the medium carries the installer and actually autostarts it.

## Install

This is a standard-library-only Python package. To run the tests without
installing:

```bash
python3 -m pytest -q
```

To install the console script (`pengyos-live`):

```bash
pip install -e .
```

## Usage

```bash
# Render the live overlay for an edition + session into DIR.
pengyos-live emit --edition desktop|core|server --session cosmic|console --out DIR [--json]

# Lint an emitted overlay (installer present + autostarted, autologin + marker,
# no pre-emptive disk write).
pengyos-live lint --overlay DIR [--json]
```

### `emit`

Renders the live-session overlay for an edition + session into `--out`:

* an **autologin drop-in** for the graphical/console getty,
* the **installer autostart** — a `cosmic`/wayland session entry **and** a
  console fallback unit that runs the installer on the live session,
* a **live-user / sudo policy**, and
* the **live marker** at `/etc/pengyos/live`.

### `lint`

Checks an emitted overlay and fails closed when any rule is violated:

* the **installer is present AND wired to autostart** for the chosen session,
* **autologin + live marker are present**,
* the **live path does not write the target disk before the user confirms**,
* the **live-user sudo policy** is present (the `live` user may run
  `pengyos-install` passwordless, so the installer can autostart and complete
  the install).

## Interface

```
pengyos-live emit --edition desktop|core|server --session cosmic|console --out DIR [--json]
pengyos-live lint --overlay DIR [--json]
```

Exit codes: `0` ok, `1` a lint rule failed / runtime error, `2` bad usage,
`3` reserved (subcommand wired but not yet implemented).

## Design

* **Standard library only.** No runtime dependencies.
* **Testable offline.** Tests need no network, root, display, or hardware — they
  drive the CLI against scratch directories and committed fixtures. System
  integration tests are opt-in behind `PENGYOS_IT=1`.

See [spec.md](spec.md) for the full behaviour contract.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
