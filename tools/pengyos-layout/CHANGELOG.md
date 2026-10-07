# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] — 2026-09-25

### Added

- **`emit` subcommand + `pengyos_layout.emit` module**: the *executor-facing*
  layer. `emit(plan)` turns a computed :class:`~pengyos_layout.model.Plan` into an
  **ordered list of :class:`~pengyos_layout.emit.Step`**, each a shell-quotable
  ``argv`` the install engine runs against the target disk — no shell, so nothing
  to quote and no injection surface:
  - **sgdisk** — create each GPT partition at its byte offset
    (`sgdisk -n <num>:<start>B:<end>B -t <num>:<type_guid> <device>`, the `B`
    suffix making sgdisk take byte offsets so the layout is byte-precise to the
    plan) and stamp the planner's deterministic unique `guid`
    (`sgdisk -u u:<guid> <num> <device>`);
  - **cryptsetup** — for every LUKS-wrapped slot, `luksFormat --batch-mode` then
    `open --type luks --name <role>_crypt` (the mapper name is
    `<role>_crypt`); the filesystem is created on the *mapper*, not the raw
    partition;
  - **mkfs/mkswap** — `mkfs.<inner-fs>` on the mapper for a LUKS slot (there is
    no `mkfs.luks2`; the container is created by `luksFormat`), `mkfs.<fs>` on the
    raw partition for a plaintext slot, and `mkswap` for a swap slot (swap is
    never mkfs'd);
  - **mount** — `mount <source> <mountpoint>` for every slot with a mountpoint
    (LUKS slots mount from `/dev/mapper/<role>_crypt`; swap is never mounted).
  `PlanEmitter.emit_partition(index)` returns the steps for one partition (the
  unit the executor loops over); `emit(plan)` concatenates them in plan order.
- **`emit` CLI subcommand**: `pengyos-layout emit --plan PLAN.json [--json]`
  prints one argv per line (`shlex.join`) by default, or a structured
  `{"steps": [{"kind", "argv", "description"}, ...]}` under `--json`. Exit `0` on
  success, `2` on a usage/IO error (missing file, malformed JSON).

### Fixed

- **LUKS slots now format on the decrypted mapper, not the raw partition.** An
  early emit wrote `mkfs.<fs>` against the raw partition for a LUKS slot (and,
  via the public `fs == luks2`, would have emitted a nonexistent `mkfs.luks2`).
  A LUKS slot is now formatted with its *inner* fs (`inner_fs`) on
  `/dev/mapper/<role>_crypt`; the container itself is created by `luksFormat`.
  `Partition.inner_fs_value()` returns the inner fs name (normalising a
  JSON-loaded `inner_fs` string to an `Fs` in `__post_init__`, so a plan read
  back from `plan.json` emits correctly).

## [0.4.0] — 2026-09-25

### Added

- **`validate` CLI subcommand**: `pengyos-layout validate --plan PLAN.json
  [--json]` reads a written plan, runs the layout validator, and reports the
  result — exit `0` (`OK`) for a valid layout, exit `1` listing every invariant
  violation for an invalid one, exit `2` on a usage/IO error (missing file,
  malformed JSON). The validator refuses a too-small disk, overlapping or
  misaligned partitions, asymmetric A/B slots, a missing/non-VAT32 ESP, and the
  live boot device. `--json` emits a structured `{"valid": bool, "violations":
  [...]}` object for machine consumption.

## [0.1.0] — 2026-02-01

### Added

- Repository scaffold: `pyproject.toml` (setuptools, src layout, pytest config,
  `pengyos-layout` console script), MIT `LICENSE` (Copyright (c) 2026 Pat Wendorf),
  `README.md`, `CHANGELOG.md`, `.gitignore`, and the `pengyos_layout` package
  skeleton with a `--version` CLI.

## [0.3.0] — 2026-09-25

### Added

- **Planner** (`pengyos_layout.plan`): `plan(probe, options)` derives the golden
  layout — `esp` fixed at 256 MiB, byte-symmetric `rootA`/`rootB` roots, `data`
  as the aligned remainder, and an optional trailing `swap` slot. Every
  partition carries a deterministic GPT `type_guid` and a unique `guid` drawn
  from a fixed pool (low 32 bits = partition index). The same probe + options
  always yields the same plan, so it can be committed as a golden `plan.json`.
- **`plan` CLI subcommand**: `pengyos-layout plan --target PROBE.json
  [--edition] [--luks] [--swap-size BYTES] [--out PLAN.json]` reads a probe,
  derives the golden layout, and writes it as JSON (to `--out` or stdout),
  refusing too-small disks and the live boot device with a non-zero exit code.
  `validate` / `emit` subcommands report honestly that they land later.

### Fixed

- `plan` now refuses a too-small disk instead of looping forever in
  `_root_size` (the step-up loop had no lower bound, so a disk that could not
  hold a symmetric A/B pair spun until it overflowed).
- A requested `swap` slot is now carved out of the data remainder so the whole
  layout still fills the fixed disk exactly, instead of being appended on top
  of an already-full disk (which overflowed the target).
- The ESP is never wrapped in LUKS2 even when `--luks` is set, so it stays
  FAT32 for firmware to read before any decryption key is available.

## [0.2.0] — 2026-09-25

### Added

- Layout **as data** (`pengyos_layout.model`): `Role`/`Fs` enums, `Partition`
  (role, size, fs, mount, `luks` flag, `start`, GPT `type_guid`/`guid`,
  `inner_fs`), `TargetProbe`, `Options` and `Plan`, all serializable to/from a
  plain `dict` (hence JSON) so the `plan` command can write a golden
  `plan.json` the executor reads back. A `luks` root slot exposes public `fs`
  `luks2` with the real filesystem in `inner_fs`.
- Layout **invariants** (`pengyos_layout.invariants`): 1 MiB GPT alignment,
  ESP present / FAT32 / within the first 512 MiB / ≥100 MiB, byte-symmetric
  A/B root slots (each ≥4 GiB), data volume (≥1 GiB), optional swap (≥1 MiB),
  non-overlapping sequential layout, whole layout fits the disk, and refusal of
  the live boot device. Each invariant is a small named pure function.
- **Validator** (`pengyos_layout.validator`): runs every invariant, collects
  *all* violations (raising `ValidationError` with the full list), and exposes
  each check individually so every invariant has exactly one firing test.
- Tests: `test_model`, `test_invariants` (one firing mutation per invariant),
  `test_validator` (golden valid, collects-all, live-device, missing-ESP, swap).
