# PengyOS build — Buildroot driver CLI

## Summary
Drive a reproducible Buildroot build for a named target from a declarative targets.toml; stream logs; record a build manifest.

## Purpose
Turn the manual first builds into a repeatable, resumable command (SPEC §2.1/§7).

## Deliverable
A Python CLI `pengyos-build` that reads `targets.toml` (targets → defconfig + board), clones/pins Buildroot, applies our BR2_EXTERNAL, runs configure+build, streams logs, and writes a build manifest (inputs, versions, hashes, timestamps).

## Interface
- pengyos-build configure <target> [--dry-run] | build <target> [--jobs N]
  [--dry-run] | clean <target> [--dry-run] | status <target>
- pengyos-build manifest <target> --json OUT
- pengyos-build list [--targets PATH] — print each target (name, br_version,
  defconfig, board, external_tree) from targets.toml
- pengyos-build acquire <target> [--build-root DIR] [--remote URL]
  — pin the Buildroot tree (see "Buildroot acquisition" below); prints a
  one-line summary `<action>: <path> @ <tag> (<commit12>)` and exits 0.
  `--build-root` and `--remote` mirror the acquisition knobs (default build
  root `$PENGYOS_BUILD_ROOT`/`~/build/pengyos`; default remote
  `https://git.buildroot.net/buildroot.git`). A targets-file error, a
  `TargetsFileError` (unknown target), or an `AcquireError` (git failure, or a
  destination that exists but is not a git work tree) prints
  `pengyos-build: error: …` on stderr and exits 1.

## targets.toml schema
Single top-level `targets` array of tables. Per target:
- `name` (required, unique): `[a-z0-9][a-z0-9_-]*` (Buildroot defconfig/board
  names are lowercase; digit start allowed).
- `br_version` (required): Buildroot release `YYYY.MM[.N]`, e.g. `2026.02.3`.
- `external_tree` (required): `BR2_EXTERNAL` tree; relative paths resolve
  against the directory of the targets.toml file, `..` components rejected.
- `defconfig` (required): bare file name (no path separators) — Buildroot
  convention is `*_defconfig` WITHOUT a `.defconfig` extension (see the real
  Core defconfig `pengyos_core_defconfig`); searched in the external tree's
  `configs/` by make.
- `board` (optional, default ""): `BR2_BOARD` board name.
Unknown fields are rejected. Validation errors name the file, the entry
(`targets[i]` or `target '<name>'` once named), and the exact problem; the
CLI prints them on stderr and exits 1. Parsing never touches the filesystem
beyond reading the file (no path-existence checks — the tree may be fetched
later).

## dry-run

`configure`, `build`, and `clean` each accept `--dry-run`: it resolves the
EXACT make invocation (same path resolution, same argv ordering, and for
`build` the resolved `-j<jobs>` — `--jobs N` or `-j$(nproc)`) and prints it
on stdout as one shell-quoted line (each argv element `shlex.quote`d, joined
by single spaces, so the line can be pasted into a shell and reproduces the
invocation byte-for-byte), then exits 0.  The same precondition checks apply
as a real run (missing source tree / external tree / unconfigured target is
still the same exit-1 error), so the printed invocation is trustworthy — but
nothing is executed or written: make is not invoked, no output dir is
created, no log file is written, and no host shim is applied.

## Buildroot acquisition

`pengyos_build.acquire` ensures a source tree pinned to the target's
`br_version`:

- Upstream remote: `https://git.buildroot.net/buildroot.git` (overridable);
  the release tag is the bare version (`2026.02.3` → tag `2026.02.3`).
- Location: `<build-root>/buildroot-<version>` where the build root is
  `$PENGYOS_BUILD_ROOT` if set, else `~/build/pengyos` (BotTalk: build state
  stays outside the synced tree). Pinning is per *version*: a pin change is a
  fresh directory, never an in-place upgrade of a tree that may hold
  in-progress build state.
- Fresh: `git clone --filter=blob:none --no-checkout <remote> <dest>` then
  `git checkout <tag>` (blobless: blobs fetched lazily — the transport the
  first builds used).
- Idempotent re-entry on an existing git work tree:
  - HEAD already at the tag's commit → do nothing (`up-to-date`, zero git
    commands recorded);
  - tag known locally, HEAD elsewhere → `git checkout <tag>`;
  - tag unknown locally → `git fetch --filter=blob:none --tags` then checkout.
- Post-condition: HEAD must equal the tag's commit after acquisition,
  otherwise acquisition fails (a silent no-op checkout is treated as a bug).
- A dest that exists but is not a git work tree (plain file or non-git dir)
  is an error — acquisition never deletes or overwrites user data.
- The git runner is injectable (`subprocess.run` by default) so unit tests
  run hermetically with a fake git; a `PENGYOS_IT=1` test exercises real git
  against a local file:// remote.

## configure step

`pengyos_build.configure` runs the first make invocation for a target:

    make -C <source> O=<output> BR2_EXTERNAL=<tree>[ BR2_BOARD=<board>] <defconfig>

- argv order is golden-pinned; `BR2_BOARD=<board>` is included only when the
  target defines a board; the bare defconfig name is resolved by make inside
  `$BR2_EXTERNAL/configs/` (Buildroot convention).
- Per-target output directory: `<build-root>/output/<target name>` — one O=
  dir per target, so targets never share output.  ALL of configure's writes
  stay inside it (acceptance: never write outside the configured output dir):
  - `logs/configure.log` — make's stdout and stderr captured **merged, in
    order** (stderr is redirected onto the log handle); the file is
    truncated on every run, never appended.  The later `status` command
    reports the build stage by scanning this log.
  - `.hostbin` — the GNU-over-uutils host shim (hostshim); the dir is created
    only when the host actually needs shims.
  - `.pengyos-files-before.txt` — a copy of `build/packages-file-list.txt`
    taken before any build dir is removed, kept until a build consumes it (an
    existing copy is never overwritten: it is the older, fuller record).
- configure **never touches the network** and never auto-acquires: a missing
  source tree for the target's `br_version`, or a missing external tree, is a
  hard error suggesting `pengyos-build acquire <target>` (or fetching the
  external tree).
- make not found, or a non-zero make exit, is an error naming the exit code
  and the captured log path; the CLI exits 1.

## build step

`pengyos_build.build` runs the second make invocation for a target that has
already been configured:

    make -C <source> O=<output> BR2_EXTERNAL=<tree>[ BR2_BOARD=<board>] -j<jobs>

- `--jobs N` sets the parallelism; the default is `-j$(nproc)` (the host core
  count, `os.cpu_count()`, clamped to at least 1). Buildroot's parallel GCC
  can race, so a lower `--jobs` is the documented knob.
- The **defconfig is NOT re-passed**: `make O=…` builds the already-configured
  `.config` without re-running kconfig (passing the defconfig again would
  silently re-configure). `BR2_EXTERNAL`/`BR2_BOARD` ARE re-passed for
  symmetry with configure and so a build never depends on ambient state.
- make's stdout and stderr are **streamed, merged, in order** straight into
  `output/<target>/logs/build.log` (the child writes to the log file handle
  line-by-line — never buffered in memory, so the log grows as the build runs
  and `status` can read it mid-build). The file is truncated on every run.
- build **never touches the network** and never auto-acquires or auto-
  configures: a missing source tree is a `BuildError` suggesting
  `pengyos-build acquire <target>`; a target with no output dir (not yet
  configured) is a `BuildError` suggesting `pengyos-build configure <target>`;
  a non-zero make exit names the exit code and the log. The GNU-over-uutils
  host shim (`run_build_with_host_shim`) is applied exactly as in configure,
  and it drops the empty and `.` entries of `LD_LIBRARY_PATH` that Buildroot's
  dependency check rejects (a trailing colon is the usual one), keeping real
  directories and unsetting the variable when none are left.

- **Leftovers.** Buildroot never deletes from `target/`: a rebuilt package
  leaves what it no longer installs, and a package turned off leaves all of its
  files. After a successful build, a file that still exists in `target/` is a
  leftover when it is listed in the configure-time snapshot and for no package
  in the new `build/packages-file-list.txt` (configure first sets the mtime of
  every file a to-be-rebuilt package listed to 0, because Buildroot lists only
  what an install changed and meson/cmake skip up-to-date files), or listed now
  only for packages
  `.config` marks `# BR2_PACKAGE_<X> is not set`. Files no package ever listed
  (overlays, post-build scripts) are never reported. Leftovers are recorded in
  `<output>/.pengyos-leftovers.txt` (`pkg,./path`), re-checked against
  `target/` whenever read, and printed by `build`, `configure` and `staleness`
  as `CLEAN BUILD NEEDED: …` with a count per package. Nothing is deleted, and
  the exit code stays 0. The record clears itself once the files are gone
  (a clean build or removal by hand) or a package installs them again.

## clean step

`pengyos_build.clean` runs Buildroot's own `clean` target, scoped to the
target's per-target `O=` output dir:

    make -C <source> O=<output> BR2_EXTERNAL=<tree>[ BR2_BOARD=<board>] clean

- Buildroot's `clean` removes the built artifacts in the `O=` dir (built
  packages, staging, images) while **keeping `.config`** — the target stays
  configured, so `build` can be re-run without `configure`. With `O=…` every
  path Buildroot writes lives inside the output dir, so the invocation is
  **scoped to the output dir only**: it never touches the acquired source
  tree (`buildroot-<version>`) and only reads the `BR2_EXTERNAL` tree.
- argv order is golden-pinned; `BR2_BOARD` only when non-empty; the
  **defconfig is NOT re-passed** (clean acts on the configured output dir).
- clean's own writes stay inside the output dir, exactly as configure's:
  `logs/clean.log` (make's stdout+stderr merged, in order; truncated every
  run) and `.hostbin` (the host shim, re-created idempotently when the host
  needs it).
- clean **never touches the network** and never auto-acquires or auto-
  configures: a missing source tree is a `CleanError` suggesting `pengyos-
  build acquire <target>`; a missing external tree is a `CleanError`; a
  target with no output dir (not yet configured) is a `CleanError`
  suggesting `pengyos-build configure <target>`; a non-zero make exit names
  the exit code and the log. The CLI exits 1 on all of these.

## status step

`pengyos_build.build.detect_stage`/`status_for` report the build stage for a
target by scanning `output/<target>/logs/build.log` (no build run, no make,
no network — a pure file read):

- Stages, in build order: `fetching` → `building` → `images` → `done`. A
  missing/empty log is `not_started`; a make-failure marker (`*** [ … ]
  Error` or a compiler `error:` diagnostic) is `error` and **wins** over the
  furthest stage (an aborted build is in the error state no matter how far it
  got).
- A log accumulates markers as it progresses, so the reported stage is the
  **furthest** one whose marker is present (a log that has reached `images`
  is not still `fetching`). Markers are matched against real Buildroot log
  lines (see the sample logs in `tests/test_build.py`).
- `pengyos-build status <target>` prints one line `<target>: <stage>` and
  exits 0 (a `not_started`/`error` report is still a successful query).

## manifest step

`pengyos_build.manifest` records a declarative, reproducible receipt of what
went into a target's build and what came out. It is a **pure read** of the
external tree and the per-target output dir — it never runs make, never
touches the network, and writes only the manifest file itself.

    pengyos-build manifest <target> --json OUT

- `--json OUT` writes the manifest to `OUT`; when omitted the default is
  `<output>/build-manifest.json` (parents created as needed). The CLI prints
  the path written and exits 0.
- The manifest records:
  - **inputs** — the target name; the pinned `br_version`; the `defconfig`
    name and its **SHA-256** (the file in `$BR2_EXTERNAL/configs/`, exactly
    the one `make` resolves); the `BR2_BOARD` board (only when the target
    defines one); the `BR2_EXTERNAL` tree path and its `git describe
    --always --dirty` (so an external-tree change is visible).
  - **outputs** — the **SHA-256 of every output image** under
    `<output>/images` (Buildroot's default `BINARIES_DIR` with `O=…`), keyed
    by path relative to the images dir and sorted, so a rebuilt image whose
    bytes changed is visible.
  - **timing** — a `started_at`/`finished_at` window derived from the
    artifacts' mtimes (earliest to latest of the build log + images; `null`
    when there is nothing yet), plus a wall-clock `generated_at` for when the
    manifest itself was written.
  - **versioning** — the `pengyos-build` tool version and a `schema_version`
    for forward compatibility.
- **Deterministic**: JSON is emitted with sorted keys and a fixed indent, so
  two runs over identical artifacts produce byte-identical files (the only
  time-dependent fields are the mtime-derived window and `generated_at`).
- A missing external tree, or a missing `configs/<defconfig>`, is a
  `ManifestError` (the manifest must hash a real config to be meaningful);
  the CLI exits 1. A non-git external tree is NOT an error — its `git
  describe` field is simply `null` (the tree is still pinned by the defconfig
  hash). The `git describe` git runner is injectable so tests run without git.

## Verification (tests are first-class)
- targets.toml parsing: valid, missing field, unknown target → clear errors
  (plus: unknown fields, bad name/br_version formats, duplicate names, path
  escaping via `..`, relative external_tree resolution).
- command construction (configure/build/clean) as golden argv with a fake `make`/BR tree.
- host-shim builder picks GNU over uutils given a fixture PATH layout.
- the build environment's `LD_LIBRARY_PATH` loses empty and `.` entries only.
- recipe staleness (`recipe.py`): a `.stamp_built` older than the newest commit
  touching that package's recipe is a rebuild, a newer stamp is fresh, an unbuilt
  package is skipped, a non-git external tree is a note and not an error, and a
  commit touching nothing in the external tree rebuilds nothing. Attribution
  charges only the identifiers on the left of the first `=`/`:` of a changed
  `external.mk` line — never a prerequisite on the right, which names a package
  without changing it — and the longest enabled stem wins
  (`PIPEWIRE_PULSE_CONF_OPTS` is pipewire-pulse, not pipewire). A bare-number diff
  context line is never read as a commit epoch. The cost is asserted as a contract:
  three git invocations, all scoped to the external tree, and no scan of the tree.
- leftovers (`leftovers.py`): a file a rebuilt package stopped installing and a
  file of a package `.config` marks not set are reported; a file another enabled
  package still lists, an overlay file in neither list, and a file already gone
  from `target/` are not (a dangling symlink counts as present). The snapshot is
  never overwritten before a build consumes it. `prepare_rebuild` ages only the
  rebuilt packages' files (`pipewire-1.6.6` is pipewire, not pipewire-pulse) and
  skips missing files and lists. End to end with fake make: the
  timesyncd case (sub-option off, package dircleaned and rebuilt) is named after
  the build, stays named on the next configure and build, and clears once the
  files are removed.
- Buildroot acquisition: golden argv for clone/checkout/fetch, idempotent
  re-entry, tag-not-local fetch path, non-git-dest and git-failure errors
  (fake git runner; real git opt-in via `PENGYOS_IT=1`).
- status stage-detection against sample Buildroot logs (fetching→building→images).
- manifest hashes match written files; deterministic field ordering.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Runs end-to-end against a stub Buildroot dir without touching the network.
- Never writes outside the configured output dir.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Being a general build system
- Managing multiple hosts

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
