# pengyos-layout

The declarative disk/layout planner for [PengyOS](../../README.md) installs
(ESP + A/B + data, optional LUKS2).

This tool
computes an **explicit, deterministic disk layout** for an edition install as
*data the install engine executes* — the installer never hardcodes the layout.

## The layout

A single GPT table holding, in order:

| # | Role  | Filesystem | Notes                                   |
|---|-------|------------|-----------------------------------------|
| 1 | `esp` | FAT32      | EFI System Partition (required)         |
| 2 | `rootA` | root fs  | A/B slot A (mirrored size)              |
| 3 | `rootB` | root fs  | A/B slot B (mirrored size)              |
| 4 | `data` | btrfs     | user data; optional `swap` slot         |

Optionally wrapped in **LUKS2** and with a **swap** slot. Sizes are derived from
the release and the target disk: `esp` is fixed, `rootA`/`rootB` are
byte-symmetric, and `data` takes the remainder.

## CLI

```
pengyos-layout plan      --target PROBE.json --edition server --out plan.json [--json]
pengyos-layout validate    --plan plan.json [--json]
pengyos-layout emit        --plan plan.json [--json]
```

- **plan** — a target probe + edition + options → ordered partitions,
  filesystems and mountpoints (golden).
- **validate** — sizes vs disk, GPT alignment, ESP bounds, A/B slot symmetry,
  and refusal of the live device.
- **emit** — the exact `sgdisk`/`cryptsetup`/`mkfs`/`mkswap`/`mount` argv for
  the executor, shell-quotable.

`emit` (this iteration) turns a written plan into an **ordered list of steps**,
each a plain `argv` the install engine runs with no shell (so nothing to quote,
no injection surface). Per partition it emits: `sgdisk` create at the plan's byte
offset (`sgdisk -n <num>:<start>B:<end>B -t <num>:<type_guid> <device>`) + the
deterministic unique GUID; `cryptsetup luksFormat`/`open` for any LUKS-wrapped
slot (the ESP is never wrapped — firmware reads the FAT32 from the raw
partition); `mkfs.<inner-fs>` on the decrypted mapper for a LUKS slot,
`mkfs.<fs>` on the raw partition otherwise, `mkswap` for swap; and `mount` at
each mountpoint (LUKS slots mount from `/dev/mapper/<role>_crypt`). `--json`
emits a structured `{"steps": [{"kind", "argv", "description"}, ...]}` object.

The `emit` subcommand reads a written plan and emits the executor argv for
every step.

## Development

```bash
pip install -e .[dev]   # or: pip install pytest
pytest -q               # run the suite
```

Tests need no network, root, display or real hardware — they use fixtures and
mocks. Integration tests are opt-in behind `PENGYOS_IT=1`.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
