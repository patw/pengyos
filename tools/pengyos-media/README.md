# pengyos-media

Build **PengyOS** — the live install medium (ISO or disk image) for an edition.

`pengyos-media` is a from-scratch, **stdlib-only** Python tool that turns an
edition release into a bootable install medium: the live/installer rootfs, the
installer, and a boot entry — as an **explicit, deterministic plan** plus an
**injectable executor**. Not a hand-made stick.

The live stick currently *boots*; `pengyos-media` **builds the medium that
carries it**.

## Status

This is the **fixture-grounding** iteration: the two committed fixtures — the
release the medium is built from and the golden plan it yields — are now the
single source of truth for the whole pipeline. The `plan` CLI and `build_plan`
emit the golden plan **byte-for-byte** from the committed release fixture; the
golden plan is lint-clean; and each lint rule has exactly one mutation grounded
to it. All three subcommands (`plan` / `build` / `lint`) are implemented and the
suite is green.

Once implemented, the three subcommands are:

* **`plan`** — edition + release → the medium's layout (ESP, live squashfs/
  rootfs, the embedded installer, the boot entry) and the ordered, shell-quotable
  build argv (`mksquashfs` the live rootfs, assemble the ESP/boot, `xorriso` for
  an ISO). Pure: it never runs any tool; the build steps are a deterministic
  function of the plan (`pengyos_media.steps.build_steps`). `--json` for the
  machine-readable form (the layout); the argv prints in the default text mode.
* **`build`** — drives the plan through an **injectable command runner**.
  `--dry-run` renders the argv and runs nothing (side-effect free); `--commit`
  actually runs the planned steps (a missing tool or a non-zero exit is a build
  failure). `--runner CMD` wraps each step with a command (a recorder, `sudo`,
  a tracer…).
* **`lint`** — validates a medium plan: the medium must carry the installer, a
  boot entry, the release, and an os-release that names the edition. A medium
  missing any of these is rejected.

## How it works

`plan` turns an edition + release into a **data model of the medium** — the
partition/layout (an ESP plus a live squashfs/rootfs), the embedded installer,
and the boot entry (an EFI boot entry; a hybrid MBR+GPT image so any firmware
finds a path — see the "USB boot dark arts" note on the bus). Every build step
is a deterministic, shell-quotable argv, so the plan is golden-testable and the
`build` runner is fully mockable.

## Interface

```
pengyos-media plan    --edition desktop --release rel.json --out media.json [--json]
pengyos-media build   --plan media.json [--dry-run|--commit] [--runner CMD]
pengyos-media lint    --plan media.json [--json]
```

## Development

```
python -m pytest -q          # run the suite (stdlib-only; no network/root)
python -m pengyos-media --help
```

Integration tests (real `xorriso` / `mksquashfs`) are opt-in behind
`PENGYOS_IT=1`. See [spec.md](spec.md).
