# pengyos-desktop-drivers

The **generic (no vendor-acceleration) driver set** for the
[PengyOS](../../README.md) Desktop live + installed image: a kernel
`linux.fragment` for KMS/input/wifi/virtio, a curated `linux-firmware` subset,
and a linter that keeps that profile lint-clean.

The goal is "just enough to configure network/wifi and run the installer" —
generic drivers only, **no** NVIDIA/AMD/Intel vendor-acceleration. This package
pins that minimal profile as **declarative, tested data** instead of a
hand-edit that drifts.

## What this is

A small Python package, `pengyos_desktop_drivers`, with two responsibilities:

| Module | Responsibility |
| ------ | -------------- |
| `profile` | The driver profile as data: the `display`/`input`/`wifi`/`storage`/`net` classes, their `CONFIG_*` symbols, and the curated `linux-firmware` prefixes. Single source of truth for `emit`/`lint`. |
| `emit` | Render the Desktop `linux.fragment` and `firmware.list` deterministically. |
| `lint` | Validate a fragment + firmware list against four rules (see below). |

## CLI

```console
$ pengyos-desktop-drivers emit --out DIR [--json]
$ pengyos-desktop-drivers lint --fragment FILE --firmware FILE [--json]
```

Exit codes: `0` success, `2` expected/user-facing failure (a lint violation or a
feature not yet implemented), `1` unexpected internal error.

Run it without installing:

```console
$ python -m pengyos_desktop_drivers --help
```

## Lint rules

`lint` enforces four rules and collects **every** failing one:

1. **Symbol existence** — every `CONFIG_*` symbol in the fragment exists in the
   vendored 2026.08 symbol list.
2. **No vendor/proprietary acceleration** — the fragment contains none of the
   forbidden symbols (NVIDIA, AMD/Intel vendor-accel toggles, ...).
3. **Firmware subset** — every line in the firmware list is an upstream
   `linux-firmware` name **and** the list is complete: at least one entry is
   present under every curated prefix, so a list missing the wifi stack (or any
   other curated prefix) is rejected.
4. **Class coverage** — the fragment enables at least one display, one input,
   and one wifi driver.

## Development

```console
$ pip install -e .[test]
$ pytest -q
```

The suite is hermetic (no network, root, display, or hardware). Integration
tests are opt-in behind `PENGYOS_IT=1`.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [`LICENSE`](LICENSE).
