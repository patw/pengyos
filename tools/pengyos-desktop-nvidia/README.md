# pengyos-desktop-nvidia

The **containment lint** for the opt-in NVIDIA editions of
[PengyOS](../../README.md) — a Tier-2 concession (SPEC §2.5): the proprietary
NVIDIA driver ships only in `pengyos_server_nvidia_defconfig` (compute) and
`pengyos_desktop_nvidia_defconfig` (compute + graphics), never in the default
Desktop, Core or Server image. (The tool's name predates the Server NVIDIA.)

## Why this exists

The NVIDIA machines in the fleet (RTX 30 + RTX 50; gaming boxes and the
inference server) need the proprietary driver — nouveau lacks the features, and
there is no Mesa NVIDIA stack in the image. The driver is the in-tree
[`buildroot-external/package/nvidia-open`](../../buildroot-external/package/nvidia-open/)
recipe: NVIDIA's **open GPU kernel modules** plus the matching proprietary
userspace (CUDA, NVENC/NVDEC, GSP firmware, and with its graphics option
EGL/GBM/Wayland), both from one `.run`. The editions are composed from the
defconfig layers (`configs/layers/nvidia-compute.config` for both,
`nvidia.config` on top for the Desktop; see `scripts/compose-defconfigs.py`).

Buildroot 2026.08's own `nvidia-driver` package is the **legacy 390 branch**: it
stops at Pascal, does not build against our kernel, and has no GBM, so it cannot
run a Wayland desktop. The lint rejects it anywhere.

## The rules

| Rule | Fails when |
| ---- | ---------- |
| `driver-in-variant` | `BR2_PACKAGE_NVIDIA_OPEN=y` is missing from either NVIDIA defconfig, or `BR2_PACKAGE_NVIDIA_OPEN_GRAPHICS=y` / `BR2_PACKAGE_LIBGLVND=y` from the Desktop's (without libglvnd kconfig silently drops graphics). |
| `driver-not-in-default` | any `BR2_PACKAGE_NVIDIA_OPEN*` symbol is assigned in another `configs/*_defconfig`. |
| `no-legacy-driver` | `BR2_PACKAGE_NVIDIA_DRIVER*` is set in any defconfig. |
| `package-declared` | `package/nvidia-open/Config.in` does not declare the symbol, or the tree's `Config.in` does not source it. |
| `version-pinned` | the `.hash` has no sha256 for the `.run` of the version in `nvidia-open.mk`. |
| `target-registered` | `pengyos-server-nvidia` or `pengyos-desktop-nvidia` is not in the tree's `targets.toml`. |
| `driver-resolves` | *(with `--buildroot`)* an NVIDIA defconfig, resolved by Buildroot's kconfig into a scratch output dir, loses one of its required symbols (an unmet `depends on`). |
| `driver-not-stubbed` | a `modprobe.d/*.conf` under `board/` has `install nvidia…` or `blacklist nvidia…`. Boards are shared with the NVIDIA editions (Desktop NVIDIA reuses `board/desktop`), so such a line keeps the driver from loading there. |

## CLI

```console
$ pengyos-desktop-nvidia lint --repo BR2_EXTERNAL_DIR [--buildroot DIR] [--targets FILE] [--json]
```

Exit codes: `0` clean, `2` a lint violation or a user-facing error, `1` an
unexpected internal error. The repo gate (`scripts/pengyos_gate.py`) runs it
against `buildroot-external`.

Run it without installing:

```console
$ python -m pengyos_desktop_nvidia lint --repo ../../buildroot-external
```

## Development

```console
$ pip install -e .[test]
$ pytest -q
```

The suite is hermetic (no network, root, display or hardware). The
kconfig-resolve test against a real Buildroot checkout is opt-in:
`PENGYOS_IT=1 PENGYOS_BUILDROOT=<checkout> pytest -q`.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [`LICENSE`](LICENSE).
