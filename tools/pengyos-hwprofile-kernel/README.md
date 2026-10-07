# pengyos-hwprofile-kernel

Turn a **pengyos-hwprofile** hardware profile into a **minimal kernel fragment
set + a defconfig fragment** for [PengyOS](../../README.md).

PengyOS morphs around the hardware it runs on. Today the kernel is built from a
generic defconfig that carries every driver for every machine. This tool reads a
`pengyos-hwprofile` hardware profile (CPU, GPU, network, storage, …) and maps the
detected device classes to kernel **fragments**, emitting a deterministic
defconfig fragment that carries only the drivers and services *this* machine
needs — plus an **excluded-drivers report** that says what was intentionally left
out.

## Install

```bash
pip install -e .            # editable dev install
pip install -e '.[dev]'     # + pytest
```

Runtime dependencies: **none** (standard library only).

## Usage

```bash
# Map a profile's device classes to fragments and emit a defconfig fragment.
# (--profile is a profile.toml — the artifact pengyos-hwprofile detect emits.)
pengyos-hwprofile-kernel select --profile profile.toml --fragments DIR --out fragment.config

# Report the kernel symbols this machine intentionally leaves out
# (the "full" kernel minus what this profile carries).
pengyos-hwprofile-kernel exclude --profile profile.toml --fragments DIR --out excluded_drivers.txt

# Explain which device pulled in a CONFIG_ symbol.
pengyos-hwprofile-kernel explain --profile profile.toml --symbol CONFIG_VIRTIO_NET

# Fragment delta between two profiles = hardware-change detection.
pengyos-hwprofile-kernel diff --a p1.toml --b p2.toml [--json]
```

Or as a module:

```bash
python -m pengyos_hwprofile_kernel --version
python -m pengyos_hwprofile_kernel --help
```

## How it works

1. **Consume** a `pengyos-hwprofile` profile and validate its required device
   fields against the shared hwprofile schema.
2. **Map** each detected device (CPU vendor/flags, GPU PCI id, network driver,
   storage controller) to a kernel **fragment** name, reusing the
   `pengyos-kernel-fragments` fragment grammar. Unknown devices produce an
   explicit warning — never a silent omission.
3. **Select** the fragments (later-wins merge with conflict warnings) and emit a
   deterministic, symbol-sorted defconfig fragment.
4. **Report** the drivers intentionally excluded for this machine (`exclude`):
   the baseline is the union of every symbol the fragment library can enable
   (the "full"/generic kernel), and the report lists the symbols this profile
   does not carry — absent, or explicitly disabled (`=n`).
5. **Explain** which device pulled a `CONFIG_` symbol into the image
   (`explain`) and **diff** two profiles to detect the hardware change between
   them — the fragment delta (`diff`).

### Excluded drivers

`select` says what the machine *has*; `exclude` says what it *doesn't*. The
baseline is the union of every `CONFIG_` symbol set by every fragment in the
`--fragments` directory — the generic/default image the kernel currently is. The
report is the set difference `baseline − selected`: a symbol is excluded when the
machine's merged config does not enable it (`=y` built-in / `=m` module means it
is carried; absent or `=n` means it is omitted). The report is deterministic
(symbols sorted, each with its providing fragment) and is the inverse check on
`select` — every symbol `select` emits is carried, everything else in the library
is intentionally left out.

### Explain

`select` says what the machine *has*; `explain` traces a carried `CONFIG_` symbol
*backwards* to the device that pulled it in. It walks the profile → mapper →
fragments → merge flow: the selected fragments that set the symbol, the device(s)
that selected each carrying fragment, and the symbol's later-wins final value::

    CONFIG_VIRTIO_NET=y (from qemu):
    set by 1 selected fragment(s):
      qemu (line 5): =y
        pulled in by enp3s0 [network]

A symbol no selected fragment sets is a clean (not-found) answer, not an error.

### Diff

`diff` reports the **fragment delta** between two profiles — hardware-change
detection. The fragment delta *is* the hardware change: a fragment added (or
removed) signals a device-class change between the two profiles. Going `A -> B`,
`added` is `fragments(B) - fragments(A)` and `removed` is `fragments(A) -
fragments(B)`; unchanged fragments are not reported. Pass `--json` for machine
readable output.

Everything is driven by fixtures, so the suite needs no root, no network, and no
real hardware. Real-system integration is opt-in behind `PENGYOS_IT=1`.

## Development

```bash
pytest -q                 # run the suite
```

The suite is green with no network, no root, and no real hardware.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
