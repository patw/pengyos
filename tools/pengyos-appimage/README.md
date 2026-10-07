# pengyos-appimage

Inspect, extract, integrate, and sandbox [AppImages](https://www.appimage.org)
under [PengyOS](../../README.md).

The tool is designed around a single security invariant:

> **It never executes an AppImage's embedded hooks.**

AppImages bundle executable `AppRun` scripts and other embedded hooks that run
with the privileges of whoever launches them. This tool only *reads* static
metadata, *extracts* files to an isolated prefix, *generates* desktop/mime
integration, and *launches* the extracted application under a
[bubblewrap](https://github.com/containers/bubblewrap) sandbox.

## Install

```bash
pip install -e .
```

This installs the `pengyos-appimage` console script. The tool depends only on
the Python standard library at runtime.

## Usage

```bash
# Read the type + .desktop + icon WITHOUT executing the AppImage.
pengyos-appimage inspect MyApp.AppImage

# Extract the AppImage to a prefix and validate its structure.
pengyos-appimage extract MyApp.AppImage --to ./_extracted

# Generate a .desktop file, icon, and mime entries.
pengyos-appimage integrate ./_extracted

# Run the extracted app under a bubblewrap sandbox.
pengyos-appimage run ./_extracted --sandbox
```

### Subcommands

| Command    | What it does                                                                 |
|------------|-------------------------------------------------------------------------------|
| `inspect`  | Read the AppImage type and extract the `.desktop` + icon **without executing** it (squashfs/offset parsing or `--appimage-extract` into a temp dir). |
| `extract`  | Extract the AppImage to a prefix and validate its structure.                  |
| `integrate`| Generate a `.desktop` file, icon, and mime entries. Never runs embedded `AppRun` hooks. |
| `run`      | Launch the extracted application under a bubblewrap sandbox (`--sandbox`, the default). |

## Development

Run the test suite (no network, root, or real display required):

```bash
pip install -e '.[test]'
pytest -q
```

Integration tests (which may touch real system tooling) are opt-in:

```bash
PENGYOS_IT=1 pytest -q
```

## Project layout

```
src/pengyos_appimage/   the package (stdlib only)
├── __init__.py
├── __main__.py         # `python -m pengyos_appimage`
├── cli.py              # argparse dispatcher + subcommand handlers
├── trailer.py          # parse the ELF trailer metadata (no execution)
├── desktop.py          # parse freedesktop .desktop files
├── squashfs.py         # extract the payload behind a mockable tool seam
├── inspect.py          # orchestrate inspect (never executes hooks)
├── extract.py          # extract the payload to a prefix + validate layout
├── integrate.py        # generate .desktop + icon + mime (never runs AppRun)
└── run.py              # launch under bubblewrap (never runs AppRun)
tests/                  smoke + unit tests (+ committed sample fixture)
├── test_smoke.py       # package / CLI / entry-point smoke tests
├── test_trailer.py     # ELF trailer metadata parsing
├── test_desktop.py     # .desktop parsing + render_entry
├── test_squashfs.py    # squashfs extraction seam (mocked tools)
├── test_inspect.py     # inspect end-to-end (poison AppRun never runs)
├── test_extract.py     # extract + validation (poison AppRun never runs)
├── test_integrate.py   # integrate .desktop/icon/mime (poison never runs)
├── test_run.py         # run under bubblewrap (poison AppRun never runs)
└── test_github_ready.py # GitHub-ready contract: files, doc sync, suite gate
fixtures/               committed sample AppImage (+ poison AppRun)
spec.md                 the specification
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
