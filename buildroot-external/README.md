# `buildroot-external/` — our Buildroot `BR2_EXTERNAL` tree

PengyOS is built with **[Buildroot](https://buildroot.org/)**. Buildroot lets a
project own its configuration *outside* of Buildroot's own tree via a
`BR2_EXTERNAL` directory — and this is ours. Everything that makes PengyOS
*PengyOS* (rather than stock Buildroot) lives here: our board definitions, our
target defconfigs, and the handful of package recipes we author ourselves.

The rule from [`../README.md`](../README.md) applies literally here: **we own the
recipes.** We consume upstream sources; we do not inherit another distro's
packaging. This tree *is* that ownership.

```
buildroot-external/
├── external.desc        # BR2_EXTERNAL identity: name "PENGYOS" + description
├── external.mk          # auto-includes every package/<name>/<name>.mk we author
├── Config.in            # the kconfig menu; `source`s each authored package
├── targets.toml         # this tree's OWN target registry (see note below)
├── board/               # per-board glue: image layout, bootloader, overlay, kernel fragment
├── configs/             # the *_defconfig files (one per buildable target)
└── package/             # the package recipes we author (not from Buildroot)
```

## How a build uses this

`tools/pengyos-build` reads the **repo-root** [`../targets.toml`](../targets.toml),
which maps a target name → a `configs/<name>_defconfig` + this tree. Buildroot
then resolves every `BR2_PACKAGE_*` symbol against the defconfig, the base
Buildroot 2026.08 tree, and the recipes sourced from `Config.in`. A target /
edition builds by *layers*: **Core ⊆ Server ⊆ Desktop** (see [`../SPEC.md`](../SPEC.md)).

## Targets

Buildable targets are registered in [`../targets.toml`](../targets.toml) at the
repository root (`external_tree = "buildroot-external"`). Tools that lint this
tree (e.g. `pengyos-desktop-nvidia lint`) read that file too.

## Subdirectory indexes

| Directory | README |
|---|---|
| `board/` | [`board/README.md`](board/README.md) — the per-machine/board glue (image layout, GRUB, kernel fragment, rootfs overlay). |
| `configs/` | [`configs/README.md`](configs/README.md) — the target defconfigs (one per edition/milestone). |
| `package/` | [`package/README.md`](package/README.md) — the recipes we author because Buildroot 2026.08 lacks them. |
