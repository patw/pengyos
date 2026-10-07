# PengyOS hwprofile-kernel — hardware profile -> kernel fragments (US-CORE-5)

## Summary
Turn a pengyos-hwprofile hardware profile into a kernel fragment set + a defconfig fragment, so the image carries only the drivers/services the target machine needs.

## Purpose
USE-CASES US-CORE-5: fit *this* machine — only the needed kernel modules and services; the profile is version-controlled and regenerated on hardware change (today the kernel is the generic defconfig).

## Deliverable
A Python CLI `pengyos-hwprofile-kernel` that consumes a pengyos-hwprofile profile.json, maps detected cpu/gpu/nic/storage classes to kernel fragment names (composing pengyos-kernel-fragments' fragment format), and emits a deterministic defconfig fragment + an 'excluded drivers' report.

## Interface
- pengyos-hwprofile-kernel select --profile profile.toml --fragments DIR --out fragment.config
- pengyos-hwprofile-kernel exclude --profile profile.toml --fragments DIR --out excluded_drivers.txt
- pengyos-hwprofile-kernel explain --profile profile.toml --symbol CONFIG_VIRTIO_NET
- pengyos-hwprofile-kernel diff --a p1.toml --b p2.toml [--json]

The `exclude` subcommand reports the kernel symbols the generic/full kernel has
that *this* machine does not: the **baseline** is the union of every `CONFIG_`
symbol set by every fragment in `--fragments` (the generic/default image the
kernel currently is); the report is the set difference `baseline − selected`. A
symbol is excluded when the machine's merged config does not *enable* it
(`=y`/`=m` means carried; absent or `=n` means omitted). Output is deterministic
(symbols sorted, each with its providing fragment); unmapped-device warnings go
to stderr; exit 0 (ok) / 1 (bad profile) / 2 (missing fragment / usage error).

The `--profile`/`--a`/`--b` argument is a **`profile.toml`** — the deterministic
artifact emitted by the sibling `pengyos-hwprofile detect` tool and validated by
the shared `pengyos_hwprofile.schema`. (The task brief's loose `profile.json`
wording refers to this same profile document; the on-disk shape is TOML, since
that is what `pengyos-hwprofile` produces and what its schema validates.)

## Verification (tests are first-class)
- table-driven: each fixture profile yields the expected fragment set.
- deterministic output (stable ordering) across repeated runs.
- unknown device -> explicit warning, never a silent omission.
- diff reports only the changed fragments between two profiles.
- the excluded-drivers report is deterministic (sorted symbols, each with its
  providing fragment) and lists exactly the symbols the machine does not enable.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A profile deterministically yields a minimal, explainable fragment set.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Building the kernel
- Detecting hardware (that's pengyos-hwprofile)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
