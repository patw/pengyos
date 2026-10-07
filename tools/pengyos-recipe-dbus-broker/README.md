# pengyos-recipe-dbus-broker

A [Buildroot](https://buildroot.net) recipe for
[dbus-broker](https://github.com/bus1/dbus-broker) for
[PengyOS](../../README.md): a pinned `Config.in` +
`dbus-broker.mk` + `dbus-broker.hash`, and the systemd unit that installs it, so
that dbus-broker is PengyOS's system D-Bus bus.

SPEC §1: dbus-broker as our system bus.

## What ships in `package/dbus-broker/`

| File | Purpose |
|------|---------|
| `dbus-broker.mk` | The Buildroot recipe: pinned `DBUS_BROKER_VERSION`, source URL, `LICENSE`, dependencies, meson opts, users, and the systemd-unit install step. |
| `Config.in` | The kconfig entry (`BR2_PACKAGE_DBUS_BROKER`) with its `depends on` / `select` lines. |
| `dbus-broker.hash` | The SHA-256 of the release tarball (and the license/subproject files) for reproducible fetches. |
| `dbus.socket` | The systemd socket unit for the system message bus. |
| `session.conf`, `system.conf` | D-Bus bus configuration installed to `/usr/share/dbus-1/`. |

The recipe pins the upstream release, declares the `Apache-2.0` license and the
`expat` / `systemd` (and, when selected, `dbus` / `audit` / `libcap-ng` /
`libselinux`) dependencies, and installs the systemd socket unit. See
`spec.md` for the exact contract the tests enforce.

## Verification (machine-checked by tests)

The suite asserts, against the committed recipe:

- **version pinned** — `DBUS_BROKER_VERSION` is set and matches the source tarball name;
- **hash present** — `dbus-broker.hash` carries the tarball SHA-256;
- **license + depends declared** — the recipe names its `LICENSE` and its
  `DEPENDENCIES` (which must include `systemd`);
- **systemd unit installed** — the `.mk` installs a systemd unit and the unit
  file exists in `package/dbus-broker/`.

Run the suite:

```sh
python3 -m pytest -q
```

Real system integration (building the recipe, booting a target) is out of scope
and opt-in behind `PENGYOS_IT=1`.

## Status

| Subsystem | State |
|-----------|-------|
| Project scaffold (packaging, pytest harness, GitHub-ready docs) | ✅ done |
| Recipe: `Config.in` + `dbus-broker.mk` (pinned, `Apache-2.0`, depends on systemd/dbus, systemd unit install) | ✅ done |
| Recipe: `dbus-broker.hash` | ✅ done |
| Tests: pinned, hashed, licensed, depends declared, systemd unit installed | ✅ done |
| `test_github_ready` (GitHub-ready contract) | ✅ done |

See `spec.md` for the contract.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
