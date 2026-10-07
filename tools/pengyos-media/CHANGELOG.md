# Changelog

All notable changes to **pengyos-media** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/); the
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **The GitHub-ready contract is asserted by `test_github_ready`** (spec.md
  "GitHub-ready contract"). The repo stays publishable as a clean public project
  at all times, enforced by `tests/test_github_ready.py` (6 tests):
  `README.md` (names the tool + all three subcommands, points at `spec.md` /
  `implementation.md`), `LICENSE` (MIT, the exact `Copyright (c) 2026 Pat
  Wendorf` line, the real MIT body with the `WITHOUT WARRANTY` disclaimer),
  `CHANGELOG.md` (`[Unreleased]`, Keep a Changelog shaped), `.gitignore`
  (ignores `__pycache__/`, `*.py[cod]`, `*.egg-info/`, `.pytest_cache/`), and
  `pyproject.toml` (declares MIT as text + classifier, points `readme` at
  `README.md`). The suite itself participates in the green run, and
  `test_test_suite_collectable` runs `pytest --collect-only` in a subprocess as a
  hermetic guard that a broken test module cannot slip into a GitHub-ready
  commit. This closes the implementation.md checklist.

- **The committed fixtures are now grounded** (implementation.md: "ground with
  fixtures"). The two committed fixtures are the single source of truth for the
  whole pipeline, not two files that could drift apart:
  - `tests/fixtures/release-desktop.json` (the release the medium is built from)
    now *yields* `tests/fixtures/media-desktop.json` (the golden plan) — the
    `plan` CLI and `build_plan` both emit the golden plan **byte-for-byte** from
    the committed release fixture. `tests/test_fixtures.py` (11 tests) proves
    this round-trip, shows the golden plan is lint-clean (via `validate_plan`
    and the `lint` CLI), and grounds each lint rule to exactly one mutation on
    the committed golden fixture (mutate one field, write it out, assert the
    single code).
  - `tests/test_plan.py`: the golden-plan CLI test now points `plan` at the
    committed release fixture (its previously-dead `RELEASE` constant), so the
    release fixture is actually consumed end-to-end.
- **`lint` — reject a medium that omits a required part** (implementation.md
  item 5). `lint` is the acceptance half of the deliverable: it runs a plan
  through the same `validate_plan` that `plan` builds with, so the medium's
  required parts are checked against one source of truth.
  - The `lint` CLI subcommand is now implemented (was a `NOT_IMPLEMENTED`
    skeleton): `pengyos-media lint --plan media.json [--json]`. It loads the
    plan, runs `validate_plan`, and prints an OK verdict (exit 0) or a FAILED
    verdict naming every problem with its stable code (exit 1). `--json` emits
    the machine-readable verdict (`{valid, plan, problems}`).
  - A plan that cannot be read/parsed (missing/unreadable file, bad JSON, not a
    plan object) is a hard error (exit 1) — an unknown `kind`/`edition`/`format`
    cannot round-trip through `from_dict`, so it surfaces as a hard error rather
    than a crash.
  - `pengyos_media/cli.py` — `_run_lint` plus `verdict_to_text` /
    `verdict_to_dict` (the OK/FAILED renderers). `test_scaffold.py`: `lint` is
    no longer a skeleton.
  - `tests/test_lint.py` (19 tests): a lint-clean golden plan passes (both the
    emitted plan and the committed `media-desktop.json` fixture), every defect
    the validator knows about is reported with its stable code, every defect is
    reported in one run, the bad-input paths are hard errors, and `--json`
    emits the expected verdict shape.
- **`build` — drive a medium plan through an injectable runner** (implementation.md
  item 4). `build` is the executor half of the deliverable: it loads the
  serialisable plan `plan` emitted, turns it into the ordered, argv-per-step
  build recipe (a pure function of the plan,
  `pengyos_media.steps.build_steps`), and runs those steps through an injectable
  runner.
  - `pengyos_media/runner.py` — the injectable runner layer: `RunResult` (a
    step's `argv` / `returncode` / captured stdout+stderr), `default_runner`
    (runs a step with `subprocess.run`, captured, never raising on non-zero),
    `wrap_runner` (the `--runner CMD` prefix: each step runs as `CMD <step
    argv>`), and `RecordingRunner` (a side-effect-free stand-in that records
    every argv — what the test suite injects, so no real tool ever runs).
  - `pengyos_media/build.py` — `build(plan, *, runner=None, dry_run=True,
    cwd=None) -> BuildOutcome`. `--dry-run` (the default) renders every step's
    argv and runs **nothing** (no runner call, no file written, no loop
    attached); `--commit` runs each step, raising `BuildError` on a non-zero
    exit or a missing tool (a malformed plan is rejected before any step runs).
    The plan is validated first, so `build` can never run an invalid recipe.
  - The `build` CLI subcommand is now implemented (was a `NOT_IMPLEMENTED`
    skeleton): default is a side-effect-free dry run; `--commit` runs it; a
    missing/unreadable/invalid plan is a hard error (exit 1).
  - `tests/test_runner.py`, `tests/test_build.py`, `tests/test_build_cli.py`
    (34 new tests): the runner, the dry-run side-effect-free guarantee (proven
    by stubbing `subprocess.run` to raise), commit via a fake recorder on
    `PATH`, and every failure path. `test_scaffold.py` updated: `build` is no
    longer a skeleton; only `lint` is.
- **The disk-image build steps are now disk-image-correct** (implementation.md
  item 3's follow-up). A `DISK_IMAGE` medium is a GPT image — an **ESP**
  (FAT32, holds the EFI loader/menu) + a **live partition** (the squashfs the
  live session boots) — and its build recipe writes *onto the partitions*
  instead of staging into an ISO tree:
  - `mkfs-disk` — `truncate` the image, `sgdisk` it into a GPT layout
    (partition 1 = EFI System `C12A7328…`, partition 2 = the live squashfs
    `0FC6DADA…`).
  - `losetup` — attach the image to a loop device with partitions visible
    (`losetup -f --show -P` → `/dev/loopN` with `/dev/loopNp1`/`p2`).
  - `mkfs-esp` — format the ESP FAT32 (`mkfs.fat`, label `BOOT`).
  - `mmd-esp` — create the ESP directory tree (`mmd`, level by level) so the
    loader has a home.
  - `assemble-esp` — lay the loader/menu/installer/kernels on the ESP with
    mtools `mcopy -i` (writes straight onto the partition — no ISO tree, no
    mount).
  - `write-live` — `dd conv=notrunc` the squashfs *onto the live partition* so
    the partition *is* the squashfs.
  - `losetup-detach` — detach the loop device.
  - The 8 disk-image steps are a pure function of the plan (like the ISO's 3);
    `tests/test_steps.py` gains 14 disk-image-correct tests.
- **The deterministic build steps for a medium** (implementation.md item 3).
  `plan` now emits, alongside the plan's layout, the ordered, argv-per-step
  build recipe that turns it into a medium:
  - `pengyos_media/steps.py` — the `BuildStep` model (`name` / `description` /
    `argv`, shell-quotable via `argv_to_shell`) and `build_steps(plan)`, a pure
    function of the plan. An ISO emits three steps in order — `mksquashfs` the
    live rootfs (zstd, `-noappend`), `assemble-esp-boot` (lay the EFI loader at
    its staged path, the boot menu, the installer, and the kernels via
    `install -D -m`), and `xorriso` (a hybrid isohybrid MBR+GPT ISO so any
    firmware boots it). A `DISK_IMAGE` emits `mkfs`/`sgdisk` partition steps
    (item 3's follow-up). `render_build_steps_text()` renders them for the CLI.
  - The `plan` CLI now prints the build steps (name + argv) after the layout
    summary in text mode; `plan --json` stays the serialisable `MediumPlan`
    (the layout — build steps are a pure function of it, derivable by `build`).
  - `tests/test_steps.py` (25 tests) + `tests/test_plan.py` (the `plan` text
    output now asserts the emitted argv).

- **The medium plan as data + a validator** (implementation.md item 2). The
  plan is now a real, serialisable value instead of a placeholder:
  - `pengyos_media/model.py` — the closed-vocabulary enums `MediumKind`
    (`iso` / `disk-image`), `Edition` (`desktop` / `core` / `server`) and
    `LiveRootfsFormat` (`squashfs` / `dir`); the sub-models
    `LiveRootfs` / `Installer` (shared `pengyos-install` / `live` naming) /
    `BootEntry` (removable EFI loader) / `Release` / `OsRelease`; and the
    frozen `MediumPlan` whose `to_dict()` has a fixed key order and whose
    `to_json()` is deterministic (sorted keys, fixed indent, single trailing
    newline). `from_dict()` round-trips a plan; `make_plan()` builds a
    fully-populated plan with sensible defaults; `render_plan_text()` gives a
    human-readable summary.
  - `pengyos_media/validate.py` — `validate_plan()` / `plan_is_valid()`, the
    single source of truth for "a well-formed plan": the closed-vocabulary
    checks (`kind` / `edition` / live-rootfs `format`) plus the spec's lint
    rules (the medium carries the installer, a boot entry, the release, and an
    os-release whose `id` names the edition; the edition agrees across the
    plan, the release and the os-release). Every failure carries a stable
    machine-readable `code` + a human `message`; all problems are reported in
    one run.
  - `pengyos_media/plan.py` — `read_release_manifest()` (reads + parses the
    edition release manifest; raises `PlanBuildError` on a missing/unreadable
    file, bad JSON, or a non-object) and `build_plan()` (builds a validated
    plan from an edition + release manifest; reads `version` from the manifest
    and `channel` (default `testing`)).
  - The `plan` CLI subcommand is now implemented (was a `NOT_IMPLEMENTED`
    skeleton): it builds a validated plan, writes it to `--out` when given,
    and prints it (JSON with `--json`, otherwise the summary). `--out` is
    optional (omit to print only).
  - Golden fixtures (`tests/fixtures/release-desktop.json`,
    `tests/fixtures/media-desktop.json`) and `tests/test_model.py` /
    `tests/test_validate.py` / `tests/test_plan.py` (47 new tests): the model's
    deterministic serialisation + round-trip, every validator rule with one
    mutation, and the `plan` CLI (success, `--json`/`--out`, and the build
    failures). `test_scaffold.py` updated: `plan` is no longer a skeleton
    (`test_build_and_lint_are_skeletons`), and `--out` is no longer required.
- **Scaffold** the `pengyos-media` tool: the `pengyos_media` package (stdlib
  only), the `pengyos-media` CLI with the `plan` / `build` / `lint` subcommand
  surface from spec.md, the `ExitCode` convention (`NOT_IMPLEMENTED` == 3), and
  the refusal behaviour (unknown / missing options). The three subcommands are
  skeletons that report `NOT_IMPLEMENTED` until their bodies are filled in by
  later tasks. `README.md`, `LICENSE` (MIT), `CHANGELOG.md`, `.gitignore`, and
  the `test_github_ready` contract are in place; `pytest -q` is green.
