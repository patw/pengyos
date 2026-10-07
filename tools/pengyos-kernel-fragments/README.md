# pengyos-kernel-fragments

Kernel config fragment library + composer for
[PengyOS](../../README.md).

A library of small, single-concern kernel config **fragments** (qemu, systemd,
intel-igpu, amd-igpu, nvidia-proprietary) plus a standard-library-only Python
tool that composes them into a single deterministic `CONFIG` set, validates
them for conflicts, and explains where each symbol came from.

## What it does

Fragments are plain-text files of `CONFIG_X=y` / `CONFIG_X=m` /
`CONFIG_X="s"` lines (plus comments). A **profile** lists the fragments it wants
composed. The tool:

- **validate** — checks fragment syntax, flags value conflicts (the same
  symbol set to different values), and fires required-symbol rules (e.g. a
  systemd profile must enable `CGROUPS`/`EPOLL`/`INOTIFY`).
- **compose** — merges a profile's fragment list with later-wins precedence,
  emitting conflict warnings and a deterministic, sorted `CONFIG` set.
- **explain** — traces a single `CONFIG` symbol back to the fragment(s) that
  set it and reports its final (merged) value, optionally within a profile's
  merge order.

## Profiles

A **profile** is a `*.profile` file that lists the fragments it wants composed,
one fragment basename per line. Blank lines and `#` comment lines are ignored::

    # n100 profile — Intel N100 (Gracemont) mini-PC reference build.
    qemu
    systemd
    intel-igpu

Profiles live in the **same directory** as the fragments (the directory passed
to `compose`). The parser ignores non-`*.fragment` files, so a directory may
hold both `*.fragment` and `*.profile` files side by side. A profile may
reference a fragment as either `qemu` or `qemu.fragment` — both resolve to the
same file.

`compose` merges the profile's fragment list in order with **later-wins**
precedence: a later fragment that assigns a *different* value to an already-set
symbol emits a conflict warning to stderr (the later value wins; compose
proceeds). The merged `CONFIG` set is printed to stdout (or `--out`), sorted by
symbol.

### Shipped profiles

The library ships three documented per-hardware profiles, each = QEMU virt
(boot machine) + systemd (init) + one GPU fragment:

| Profile      | Fragments              | Target                                   |
| ------------ | ---------------------- | ---------------------------------------- |
| `n100`       | qemu + systemd + intel-igpu | Intel N100 (Gracemont) mini-PC      |
| `amdv1000`   | qemu + systemd + amd-igpu     | AMD Ryzen Embedded V1000 (Vega APU) |
| `nvidia`     | qemu + systemd + nvidia-proprietary | NVIDIA proprietary discrete GPU |

Compose one with:

```console
$ pengyos-kernel-fragments compose fragments/ -p n100
$ pengyos-kernel-fragments compose fragments/ -p amdv1000
$ pengyos-kernel-fragments compose fragments/ -p nvidia -o merged.fragment
```

## Explain

`explain` answers a read-only question — *which fragment(s) set a given
`CONFIG` symbol, and what is its final (merged) value?* It scans the
`*.fragment` files in a directory and reports every fragment that assigns the
symbol (with its value and line number), plus the symbol's final value using
the same later-wins precedence as `compose`.

```console
$ pengyos-kernel-fragments explain CONFIG_DRM fragments/
CONFIG_DRM:
  set by 3 fragment(s):
    amd-igpu.fragment (line 3): =y
    intel-igpu.fragment (line 3): =y
    nvidia-proprietary.fragment (line 5): =y
  final value: =y (from nvidia-proprietary.fragment)
```

The symbol argument is required and must be a `CONFIG_` symbol; the directory
is optional and defaults to `./fragments`. Pass `--profile NAME` to report the
final value within that profile's fragment merge order (the final value then
reflects the profile's later-wins precedence rather than the whole library):

```console
$ pengyos-kernel-fragments explain CONFIG_VIRTIO fragments/ -p n100
```

## Usage

```console
$ pengyos-kernel-fragments --help
usage: pengyos-kernel-fragments [-h] [--version] COMMAND ...

Validate, compose, and explain PengyOS kernel config fragments.

positional arguments:
  COMMAND
    version   print the version and exit
    validate  validate a fragments directory (syntax, conflicts, required
              rules)
    compose   compose a profile's fragment list into a merged CONFIG set
    explain   explain which fragment(s) set a CONFIG_ symbol and its final
              value
```

All three commands — `validate`, `compose`, and `explain` — are shipped.

```console
$ pengyos-kernel-fragments validate fragments/
OK: 5 fragment(s) clean.

$ pengyos-kernel-fragments validate <fragments-dir>        # syntax + conflicts + required rules
$ pengyos-kernel-fragments validate <fragments-dir> -p systemd   # check only the systemd rule

$ pengyos-kernel-fragments compose <dir> -p n100           # merge n100's fragments, print CONFIG set
$ pengyos-kernel-fragments compose <dir> -p n100 -o merged.fragment   # write the merged set to a file

$ pengyos-kernel-fragments explain CONFIG_<SYMBOL> <fragments-dir>   # trace a symbol to its fragment(s)
$ pengyos-kernel-fragments explain CONFIG_<SYMBOL> <fragments-dir> -p n100   # ...within a profile
```

## Development

Requires Python ≥ 3.11 (standard library only at runtime).

```sh
python3 -m pytest -q        # run the test suite
python3 -m pengyos_kernel_fragments   # run the CLI without installing
```

To install the `pengyos-kernel-fragments` entry point: `pip install .` (or
`pipx`).

Tests need no network, root, display, or hardware; fixtures and mocks keep them
hermetic. Integration tests are opt-in behind `PENGYOS_IT=1`.

## Layout

```
pengyos_kernel_fragments/   package (cli.py, fragments, validate, compose,
                              explain)
tests/                      pytest suite (no network, no root)
fragments/                  committed kernel config fragments + profiles
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
