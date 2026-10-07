# pengyos-initramfs

Deterministic minimal **initramfs** builder for
[PengyOS](../../README.md).

`pengyos-initramfs` builds a small, reproducible cpio initramfs from a spec +
a root directory. The resulting archive is **deterministic**: members are
written in sorted order and metadata (uid/gid/mtime) is normalized, so two
builds of the same inputs produce **byte-identical** output.

## Usage

```
pengyos-initramfs build --root DIR --spec initramfs.toml --out init.cpio.gz
```

- `--root DIR`  — the root directory whose contents get packed into the archive.
- `--spec`       — a TOML spec describing which binaries / files / modules / directories to include.
- `--out`        — where to write the resulting `.cpio.gz` archive.

```
pengyos-initramfs --version
pengyos-initramfs --help
```

## Development

Standard-library only. Run the test suite from this directory:

```
python3 -m pytest -q
```

Opt-in integration tests (validate the archive with the system `cpio`)
run only when `PENGYOS_IT=1` is set and a `cpio` binary is installed.

## Status

`build` works end to end: the TOML `initramfs.toml` is parsed and validated
into a normalized `Spec` (`pengyos_initramfs/spec.py`), and the deterministic
cpio writer + builder (`pengyos_initramfs/cpio.py`,
`pengyos_initramfs/builder.py`) pack exactly the spec'd paths **plus a
generated minimal `/init` script** (`pengyos_initramfs/initgen.py`,
path/mode from the spec's `[init]` table, default `init` / `0755`) — in
sorted order, with normalized uid/gid/mtime/modes and zeroed gzip headers —
into the `.cpio.gz` output. Two builds of the same inputs are byte-identical;
the `PENGYOS_IT=1` tests prove the archive against real GNU cpio. Nested file
paths (e.g. `etc/os-release`) extract at boot because the kernel only creates
directories that are **in** the archive — list them in the spec's
`directories` to pack explicit `0o040755` directory entries (see
`spec.md` for the full requirements, including the
archive-format details and exit codes). A sample spec lives in
`examples/initramfs.toml`. Requires Python 3.11+ (stdlib `tomllib`).

## License

MIT — see [LICENSE](./LICENSE). Copyright (c) 2026 Pat Wendorf.
