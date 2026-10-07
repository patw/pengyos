# pengyos-debx

Bounded [deb](https://en.wikipedia.org/wiki/Deb_(file_format)) /
[rpm](https://en.wikipedia.org/wiki/RPM_Package_Manager) extractor for
[PengyOS](../../README.md).

The tool is designed around a single security invariant:

> **It never executes a package's maintainer scripts.**

Deb and rpm packages bundle maintainer scripts (`preinst`, `postinst`, `prerm`,
`postrm`, … for deb; `pre`, `preun`, `post`, `uninstall`, … for rpm) that run
with the privileges of whoever installs the package. This tool only *reads*
package structure, *extracts* files to an isolated install prefix, *lists*
maintainer scripts for inspection, and *analyses* `DT_NEEDED` dependencies by
parsing ELF headers directly — it never runs a script and never shells out to
`ldd` (which can execute).

## Install

```bash
pip install -e '.[test]'
```

This installs the `pengyos-debx` console script. The tool depends only on the
Python standard library at runtime.

## Usage

```bash
# Extract a package into an isolated install prefix (scripts never run).
pengyos-debx extract pkg.deb --into ./_debx_extracted
pengyos-debx extract pkg.rpm --into ./_debx_extracted

# List maintainer scripts for inspection (never executed).
pengyos-debx list-scripts pkg.deb

# Analyse DT_NEEDED of an extracted prefix (pure ELF parsing, never ldd).
pengyos-debx analyse ./_debx_extracted --libdirs /lib /usr/lib
```

### Subcommands

| Command        | What it does                                                                                     |
|----------------|--------------------------------------------------------------------------------------------------|
| `extract`      | Extract a deb/rpm payload into an isolated install prefix (`--into`). Maintainer scripts never run. |
| `list-scripts` | List maintainer scripts contained in a package for inspection. They are never executed.           |
| `analyse`      | Analyse `DT_NEEDED` of the ELF files under an extracted prefix (or a single
                  ELF file) by pure ELF parsing, and **resolve** each object's
                  declared libraries against a `--libdirs` set into a
                  resolved-vs-missing report. Never `ldd`. |

## Security model

The extractor is a *bounded* convenience with sharp edges, not a compatibility
guarantee (see spec §2.5). Concretely:

* **Scripts are never executed.** `extract` writes payload files only.
  `list-scripts` reads each maintainer script and prints its interpreter, size
  and body for inspection — it never runs them and never writes them to disk.
* **No `ldd`.** `analyse` parses the ELF dynamic-section (`DT_NEEDED`) directly
  with `struct`. `ldd` runs the interpreter and can execute shared objects.
* **Bounded.** Extraction is intended for small, trusted packages under test.
  **Path traversal is refused**: a payload path that would resolve outside the
  install prefix (via `..` or an absolute escape) raises an error instead of
  writing anywhere it should not. There is no guarantee against other payload
  abuse yet.

## Development

Run the test suite (no network, root, or real display required):

```bash
pip install -e '.[test]'
pytest -q
```

Integration tests (which may touch real package tooling) are opt-in:

```bash
PENGYOS_IT=1 pytest -q
```

## Project layout

```
src/pengyos_debx/   the package (stdlib only)
├── __init__.py        package version + docstring
├── __main__.py        `python -m pengyos_debx`
├── cli.py             argparse dispatcher + subcommand handlers
├── formats.py         deb (ar+tar) / rpm (cpio/zstd) reading
├── extract.py         extract payload to an isolated prefix + record file list/hashes
├── scripts.py         list maintainer scripts (never executed)
├── elf.py             pure ELF header parsing (DT_NEEDED)
└── analyse.py         DT_NEEDED scan of a prefix/file (pure ELF parsing)
tests/                  smoke + format + extract + github_ready tests
├── test_smoke.py           CLI parser/dispatch + placeholder-handler smoke tests
├── test_formats.py         deb (ar+tar) / rpm (cpio/zstd) reading + never-shells-out
├── test_scripts.py         list maintainer scripts (never executed) + poison-refusal
├── test_extract.py         extract to isolated prefix, manifest, path-traversal guard,
│                           symlink/dir recreation, never-runs-scripts
├── test_elf.py             pure ELF parsing (DT_NEEDED) + never-shells-out
├── test_analyse.py         analyse command: scan + report DT_NEEDED
└── test_github_ready.py    the GitHub-ready contract (README/LICENSE/CHANGELOG/...)
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
