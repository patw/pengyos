# PengyOS kernel-fragments — fragment library + composer

## Summary
A library of kernel config fragments (qemu, systemd, intel, amd, nvidia) plus a tool to compose/validate them.

## Purpose
SPEC §2.1: one fragment per concern; per-hardware profiles compose them.

## Deliverable
A Python tool `pengyos-kernel-fragments` with `validate|compose|explain`: fragments live in a directory, profiles reference a list, and the tool detects conflicts and prints the merged CONFIG set.

## Interface
- pengyos-kernel-fragments validate fragments/
- pengyos-kernel-fragments compose <dir> --profile n100 --out merged.fragment
- pengyos-kernel-fragments explain CONFIG_VIRTIO

## Profiles
A **profile** is a `*.profile` file that lists fragment basenames, one per line
(blank lines and `#` comments ignored). Profiles live in the **same directory**
as the fragments (the directory passed to `compose`); the parser ignores
non-`*.fragment` files, so a directory may hold both. A profile may reference a
fragment as `qemu` or `qemu.fragment` (both resolve to the same file).

`compose` merges the profile's fragment list in order with **later-wins**
precedence. A later fragment that assigns a *different* value to an already-set
symbol emits a conflict warning (the later value wins; compose proceeds). The
merged `CONFIG` set is printed deterministically, sorted by symbol. Conflict
warnings and usage errors go to stderr; the CONFIG set goes to stdout (or
`--out`). Exit codes: `0` clean, `2` usage error (missing `--profile`, missing
profile file, or an unresolved referenced fragment).

The library ships three documented per-hardware profiles, each = QEMU virt
(boot machine) + systemd (init) + one GPU fragment:

| Profile      | Fragments                   | Target                                   |
| ------------ | --------------------------- | ---------------------------------------- |
| `n100`       | `qemu` + `systemd` + `intel-igpu`     | Intel N100 (Gracemont) mini-PC      |
| `amdv1000`   | `qemu` + `systemd` + `amd-igpu`       | AMD Ryzen Embedded V1000 (Vega APU) |
| `nvidia`     | `qemu` + `systemd` + `nvidia-proprietary` | NVIDIA proprietary discrete GPU |

Each shipped profile composes clean (no conflict warnings) and satisfies the
`systemd` required-symbol rule.

## Verification (tests are first-class)
- conflict detection (same symbol =y and =n) reported.
- compose order/precedence + deterministic output.
- systemd required-pairs rule fires when a needed symbol is missing.
- explain traces a symbol to its source fragment.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Fragments validated + composed deterministically.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Parsing the real kernel Kconfig

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
