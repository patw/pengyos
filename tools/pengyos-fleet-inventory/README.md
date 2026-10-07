# pengyos-fleet

Collect **hardware facts** for a fleet of hosts and emit a **declarative,
version-controllable per-machine profile** for [PengyOS](../../README.md).

PengyOS morphs around the hardware it runs on. This tool captures *what* each
fleet machine is (CPU, memory, GPUs, storage, network, peripherals) as a
human-readable, diff-friendly `profile.toml` that belongs in version control.
Pair it with `pengyos-fleet diff` to see exactly what changed between two
machines or two points in time.

## Install

```bash
pip install -e .            # editable dev install
pip install -e '.[dev]'     # + pytest
```

Runtime dependencies: **none** (standard library only).

## Usage

```bash
# Collect a profile for every host in the list and write one profile.toml per
# host into the output directory.
pengyos-fleet collect --hosts hosts.toml --out profiles/

# Use the mock runner (offline, deterministic) — the default in tests.
pengyos-fleet collect --hosts hosts.toml --out profiles/ --runner mock

# Compare two collected profiles (added / removed / changed devices).
pengyos-fleet diff profiles/a/profile.toml profiles/b/profile.toml
```

Or as a module:

```bash
python -m pengyos_fleet --version
python -m pengyos_fleet --help
```

### Host list

`--hosts` points at a TOML host list (see `spec.md` → *Host list* for the
schema; `hosts.example.toml` is a ready-to-copy sample). Each entry names a
machine via `name` (also the output directory), `address` (the SSH target), and
an optional `fixtures` directory consumed by the mock runner.

### Runners

Fact collection runs through an **injectable runner**:

- `--runner ssh` — runs the host's hardware commands over SSH (production).
- `--runner mock` — returns committed fixture output, so the whole flow runs
  offline with no SSH, no root, and no real hardware.

Real integration (live SSH, real hardware commands) is opt-in; the default
behaviour in tests is the deterministic mock runner.

### Diff

`pengyos-fleet diff` compares two collected `profile.toml` files and reports the
added (`+`), removed (`-`), and changed (`~`) hardware between them:

```bash
pengyos-fleet diff profiles/a/profile.toml profiles/b/profile.toml
```

```text
~ memory: System
~ gpu: 00:02.0
+ storage: S6G0NFA0712345/nvme/Samsung SSD 980 PRO 2TB
+ storage: WD-WMC3T0123456/sata/WDC WD20EZEX-00DC0B0
- storage: S123N0M2162001/nvme/Samsung SSD 970 EVO Plus 500GB
+ network: enp4s0
+ network: wlan0
- network: enp3s0
```

Only the hardware *list* sections (`memory`, `gpu`, `storage`, `network`,
`peripherals`) are compared — `metadata` / `cpu` are machine/tool identity and
are not diffed. Each section is keyed by the physical identity of its devices,
so a device present on both sides but with a changed attribute is reported as
`~ changed`, while a device that appears on only one side is `+ added` /
`- removed`. The interface *name* identifies a network device, so a MAC change
is a `~ changed` (a config change), not a removal plus a fresh addition.

`diff` exits `0` when the profiles are identical and `1` when they differ — so
it doubles as a "did the hardware change?" check. A missing file, invalid TOML,
or a profile that violates the hwprofile schema is a hard error (exit `2`).

## How it works

1. **Collect** hardware facts for each host from a *runner* (SSH in
   production, a deterministic mock in tests).
2. **Emit** a deterministic, sorted `profile.toml` per host, shaped by the
   PengyOS hwprofile schema.
3. **Diff** two profiles to surface added, removed, and changed devices.

Because the emitter is deterministic, a re-collect is idempotent: `collect`
writes only the hosts whose profile has changed since the last run and reports
`wrote <path>` / `unchanged <path>` per host, so re-running it makes no
unnecessary filesystem changes.

Everything is driven by fixtures in tests, so the suite needs no root, no
network, and no real hardware. Real-system integration is opt-in behind
`PENGYOS_IT=1`.

## Development

```bash
pytest -q                 # run the suite
```

The suite is green with no network, no root, no real display, and no real
hardware.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
