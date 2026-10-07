# pengyos-hwprofile

Detect hardware and emit a **declarative, version-controllable per-machine
profile** for [PengyOS](../../README.md).

PengyOS morphs around the hardware it runs on. This tool captures *what* the
hardware is (CPU, memory, GPUs, storage, network, peripherals) as a
human-readable, diff-friendly `profile.toml` that belongs in version control.
Pair it with `pengyos-hwprofile diff A B` to see exactly what changed between
two machines or two points in time.

## Install

```bash
pip install -e .            # editable dev install
pip install -e '.[dev]'     # + pytest
```

Runtime dependencies: **none** (standard library only).

## Usage

```bash
# Emit a profile from a source tree (real /sys, or committed fixtures).
pengyos-hwprofile detect --from /path/to/source --out profile.toml

# Omit --from to read the live system (opt-in behind PENGYOS_IT=1).
PENGYOS_IT=1 pengyos-hwprofile detect --out profile.toml

# Compare two profiles (added / removed / changed devices).
pengyos-hwprofile diff profileA.toml profileB.toml
```

Or as a module:

```bash
python -m pengyos_hwprofile --version
python -m pengyos_hwprofile --help
```

## How it works

1. **Collect** hardware facts from a *source*: the live system (`/sys`,
   `lspci`, `lsusb`, `dmidecode`) or a committed fixture directory.
2. **Emit** a deterministic, sorted `profile.toml`.
3. **Diff** two profiles to surface added, removed, and changed devices.

With `detect`, omit `--from` to read the live machine (opt-in behind
`PENGYOS_IT=1`); pass `--from DIR` to read a directory instead.

Everything is driven by fixtures in tests, so the suite needs no root, no
network, and no real hardware. Real-system integration is opt-in behind
`PENGYOS_IT=1`.

## Development

```bash
pytest -q                 # run the suite
```

The suite is green with no network, no root, and no real hardware.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
