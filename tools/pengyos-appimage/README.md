# pengyos-appimage

Inspect [AppImages](https://appimage.org), add them to the app launcher, and
find the libraries they need, under [PengyOS](../../README.md).

An AppImage already runs on PengyOS as it is: its static runtime mounts the
payload with FUSE. What the desktop lacks is the rest: a launcher entry, and a
straight answer when an app will not start. This tool provides both.

It never runs or unpacks an AppImage. It reads the runtime's ELF header and the
squashfs payload in place, in pure Python, so it starts no process at all
(the `AppRun` hook included) and writes nothing except the launcher entry and
icon `integrate` is asked for.

## Install

```bash
pip install -e .
```

This installs the `pengyos-appimage` console script. It depends only on the
Python standard library. Reading a zstd payload (what current AppImage tools
produce) needs Python 3.14+ (`compression.zstd`); gzip, xz and lzma payloads
work on 3.10+. lzo and lz4 payloads are reported, not read.

## Usage

```bash
# What is it? Type, arch, compression, update info, signature, .desktop, icon.
pengyos-appimage inspect MyApp.AppImage

# Add it to the app launcher, running from where it is now.
pengyos-appimage integrate ~/Applications/MyApp.AppImage

# Take it out again (works even after the AppImage is deleted).
pengyos-appimage integrate --remove ~/Applications/MyApp.AppImage

# It will not start: which libraries does it need that this system lacks?
pengyos-appimage check MyApp.AppImage
```

Every command takes `--json`.

### Subcommands

| Command     | What it does |
|-------------|--------------|
| `inspect`   | Read the runtime header (type, architecture, payload offset, `.upd_info` update information, whether `.sha256_sig` holds a signature) and the payload's root `.desktop` entry and icon. |
| `integrate` | Write `appimage-<id>.desktop` under `$XDG_DATA_HOME/applications` (default `~/.local/share`) with every `Exec` pointed at the AppImage's absolute path, and its icon under `icons/hicolor/<size>/apps` at a size the hicolor theme declares. `<id>` is the payload's `.desktop` name, so a newer build of the same app replaces the old entry. `--remove` deletes what it wrote for that path; `--dest DIR` picks another data home. |
| `check`     | Read `DT_NEEDED` from every ELF object in the payload and list the libraries neither the AppImage nor the host provides, which objects need them, and a hint. Libraries a bundled library loads with `dlopen()` (e.g. Qt 6's xcb plugin and `libxcb-cursor`) come from a small table. `--libdirs` replaces the host search path (default: `/usr/lib` and friends plus `/etc/ld.so.conf`). Exit status `4` when something is missing. |

Exit codes: `0` success, `1` error (not an AppImage, unreadable payload),
`2` usage, `4` `check` found missing libraries.

### What `check` does not see

It approximates the dynamic loader. It does not compare symbol versions (an
app built against a newer glibc than the host's still fails), and it counts
every library in the payload as on the search path, as AppImages arrange with
`LD_LIBRARY_PATH` or `RPATH`. A library loaded with `dlopen()` is reported
only if it is in the table in `check.py`; add a row when a real AppImage
fails that way.

## How it reads an AppImage

A type-2 AppImage is the runtime ELF followed by a squashfs image:

```
offset 0                          payload offset
| runtime ELF  ...  section table | squashfs ("hsqs") ...  |
```

* `runtime.py` reads the AppImage magic (`AI\x02` in `e_ident`) and finds the
  payload where the runtime does (`appimage_get_elf_size` in type2-runtime:
  the end of the section table or of the last section, whichever is later).
* `squashfs.py` reads squashfs 4.0 directly (superblock, inode and directory
  tables, data blocks, fragments), with every read bounded, because the payload
  is untrusted input.
* `elf.py` reads `DT_NEEDED` and `DT_SONAME`, mapping `DT_STRTAB`'s virtual
  address through the `PT_LOAD` segments (it is not a file offset in a non-PIE
  executable). It never uses `ldd`, which can execute code.

## Development

Run the test suite (no network, root, or display required):

```bash
pip install -e '.[test]'
pytest -q
```

The fixtures are real tool output, not hand-made layouts: the sample AppImage
is the upstream type2-runtime plus a `mksquashfs` payload with small `gcc`-built
ELF objects, and the squashfs fixtures are `mksquashfs` output for each
compressor and layout. Rebuild them with the scripts in `tests/fixtures/`
(they need `gcc`, `mksquashfs` and any real AppImage to take the runtime from).

Integration tests are opt-in. With real AppImages they also compare the parser
with the runtime's own `--appimage-offset`, the reader with `unsquashfs`, and
the ELF parser with `objdump`:

```bash
PENGYOS_IT=1 PENGYOS_APPIMAGE_REAL=/path/a.AppImage:/path/b.AppImage pytest -q
```

`--appimage-offset` runs each AppImage's runtime (not its payload); only list
AppImages you already trust to run.

## Project layout

```
src/pengyos_appimage/   the package (stdlib only)
├── __init__.py
├── __main__.py         # `python -m pengyos_appimage`
├── cli.py              # argparse dispatcher + subcommand handlers
├── runtime.py          # parse the runtime ELF header: type, payload offset (no execution)
├── squashfs.py         # read-only squashfs 4.0 reader (gzip, xz, lzma, zstd)
├── elf.py              # DT_NEEDED / DT_SONAME from ELF headers (never ldd)
├── desktop.py          # parse and render freedesktop .desktop files
├── inspect.py          # inspect: runtime header + root .desktop + icon
├── integrate.py        # integrate: launcher entry + icon, and --remove
└── check.py            # check: libraries neither the payload nor the host has
tests/
├── test_smoke.py       # package / CLI wiring; no command starts a process
├── test_runtime.py     # runtime header parsing (real type2-runtime fixture)
├── test_squashfs.py    # squashfs reader against mksquashfs fixtures
├── test_elf.py         # ELF parsing (PIE, non-PIE, shared library)
├── test_desktop.py     # .desktop parsing + render_entry
├── test_inspect.py     # inspect and its CLI
├── test_integrate.py   # integrate / --remove and its CLI
├── test_check.py       # check, the dlopen table, and its CLI
└── test_github_ready.py # GitHub-ready contract: files, doc sync, suite gate
fixtures/               sample AppImage (real type2-runtime + squashfs) and
                        squashfs fixtures, with the scripts that build them
spec.md                 the specification
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
