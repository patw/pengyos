# pengyos-build

Reproducible Buildroot build driver for [PengyOS](../../README.md)
targets: drive a named target declared in `targets.toml` through a pinned
Buildroot tree, apply our `BR2_EXTERNAL`, stream logs, and record a build
manifest (inputs, versions, hashes, timestamps).

Status: **in progress** — targets.toml parsing/validation, the `list`
command, Buildroot acquisition (blobless clone + pinned tag, idempotent
re-entry), the `configure` step, the `build` step (`-j$(nproc)`, configurable
`--jobs`, streamed build log), `clean` (scoped to the per-target output
dir, keeps `.config`), `status` (stage detection by scanning the build
log), the `manifest` step (deterministic build manifest: inputs,
versions, hashes, timestamps), and the `acquire` subcommand (pin the
Buildroot tree before `configure`/`build`) are in, as is `--dry-run` on
`configure`/`build`/`clean` (print the exact make invocation without running
it).

## Usage

```console
$ pengyos-build --help
usage: pengyos-build [-h] [--version] COMMAND ...

Drive a reproducible Buildroot build for a named PengyOS target.

positional arguments:
  COMMAND
    version    print the pengyos-build version and exit
    acquire    acquire (clone + pin) the Buildroot source tree for a target
               before configure/build
    list       list targets defined in targets.toml
    configure  run the configure make step for a target (requires the tree
               to be acquired first)
```

Full surface (all shipped):

```
pengyos-build acquire <target> [--build-root DIR] [--remote URL]   (in)
pengyos-build build <target> [--jobs N] [--dry-run]                (in)
pengyos-build status <target>                                      (in)
pengyos-build clean <target> [--dry-run]                           (in)
pengyos-build manifest <target> --json OUT                         (in)
pengyos-build sbom <target> [--embed] [--sign-key KEY] [--jobs N]  (in)
pengyos-build staleness <target>                                     (in)
```

`configure`, `build`, and `clean` all accept `--dry-run`: it prints the exact
make invocation (shell-quoted, one line — for `build`, with the resolved
`-j<jobs>`) and exits without running make or writing anything; the same
precondition checks apply, so a missing tree is still an error.

Re-running `configure` on an existing output dir compares the old and new
`.config`: any already-built package whose options changed has its build dir
removed (like `make <pkg>-dirclean`) so `build` rebuilds it — Buildroot itself
never does. The packages affected are printed; packages the new config turns
off are listed too (their files stay in `target/` until a clean build).

It also removes a built package whose **recipe** changed after Buildroot built it
(`recipe.py`). A `.mk`, a `Config.in` select or a patch moves no symbol of its own
package, so the `.config` diff cannot see it and the stamp stays green — the shape
of every `br2-external` override. `staleness <target>` reports the same finding
without removing anything, for when you want to know before you launch a build.

Rebuilding a package never deletes what it installed before, and turning a package
off leaves all of its files in `target/`, so they go into the image
(`leftovers.py`). On 2026-10-10 the Desktop image kept `systemd-timesyncd` enabled
after `BR2_PACKAGE_SYSTEMD_TIMESYNCD` was turned off and systemd was rebuilt
without it. `configure` keeps a copy of Buildroot's `build/packages-file-list.txt`.
After the build, any file still in `target/` that no enabled package lists any more
is printed as `CLEAN BUILD NEEDED: …` by `build`, `configure` and `staleness`, and
recorded in `<out>/.pengyos-leftovers.txt` until a clean build (or removing those
files) makes it go away.

`build` runs `make -C <src> O=<out> BR2_EXTERNAL=<ext> [BR2_BOARD=<board>]
-j<N>` (N defaults to `-j$(nproc)`; lower `--jobs` to dodge Buildroot's
parallel GCC races), streaming make's merged stdout+stderr into
`<out>/logs/build.log`. `clean` runs Buildroot's own clean target against
the per-target output dir (`make … O=<out> … clean`): artifacts are removed,
`.config` is kept, and nothing outside the output dir is touched. `status`
scans that log and reports the stage (`fetching` → `building` → `images` →
`done`, or `not_started`/`error`). `manifest` records a reproducible build
receipt as deterministic JSON (default `<out>/build-manifest.json`): the
inputs (pinned `br_version`, defconfig name + SHA-256, board, external tree
+ its `git describe`), the SHA-256 of every output image under `<out>/images`,
and a start/end timing window + `generated_at` timestamp.

`sbom <target>` turns Buildroot's own manifests into a verifiable bill of
materials (SPEC provenance / US-CORE-6): it runs `make … legal-info` and
`make … show-info` (through the same GNU host shim as the build), then
`pengyos-sbom generate` → `<out>/images/<target>.spdx.json` (+ `.cdx.json`),
optionally `--sign-key KEY` for a detached ed25519 signature. With `--embed`
the build make is re-run afterwards so the board post-build script bakes the
SBOM into the rootfs (`/etc/pengyos/`) — the image then carries its own
ingredient list. Requires an acquired tree + configured target (run `build`
first).

`acquire <target>` pins the Buildroot source tree before building: it
ensures `<build-root>/buildroot-<br_version>` holds a blobless
(`--filter=blob:none`) clone checked out at the release tag, with idempotent
re-entry (an already-pinned tree is a no-op `up-to-date`). It prints a one-line
summary (`<action>: <path> @ <tag> (<commit12>)`) and exits 0; a git failure
or a non-git destination is an exit-1 error. `--remote` overrides the
upstream remote. `configure`/`build` never auto-acquire — run `acquire` first.

## targets.toml

Targets are declared in `targets.toml` (a committed example lives at the repo
root; see `spec.md` → “targets.toml schema” for the full rules):

```toml
[[targets]]
name = "pengyos-core"                  # [a-z0-9][a-z0-9_-]*, unique
br_version = "2026.08"               # Buildroot release YYYY.MM[.N]
external_tree = "buildroot-external"   # BR2_EXTERNAL, relative to this file
defconfig = "pengyos_core_defconfig"    # bare file name, no path
# board = "core"                       # optional BR2_BOARD
```

`pengyos-build list --targets my-targets.toml` prints the parsed targets;
validation errors name the file, the entry, and the problem, and exit 1.

## Development

Requires Python ≥ 3.11 (standard library only at runtime; `tomllib` is used
for `targets.toml`).

```sh
python3 -m pytest -q        # run the test suite
python3 -m pengyos_build    # run the CLI without installing
```

To install the `pengyos-build` entry point: `pip install .` (or `pipx`).

## Layout

```
pengyos_build/   package (cli.py, targets.py, acquire.py, hostshim.py,
                 configure.py, build.py, clean.py, later: manifest, ...)
tests/           pytest suite (no network, no root; PENGYOS_IT=1 opts into
                 real integration tests)
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
