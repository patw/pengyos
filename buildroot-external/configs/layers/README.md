# Defconfig layers

PengyOS editions nest — **Core ⊆ Server ⊆ Desktop** — so their Buildroot
configuration is written once per layer and stacked:

| Edition defconfig (generated) | Layers |
|---|---|
| `pengyos_core_defconfig` | core |
| `pengyos_server_defconfig` | core + server |
| `pengyos_desktop_defconfig` | core + server + desktop |
| `pengyos_server_nvidia_defconfig` | core + server + nvidia-compute |
| `pengyos_desktop_nvidia_defconfig` | core + server + desktop + nvidia-compute + nvidia |

`nvidia-compute` is the driver's headless half (CUDA, NVML, persistence, CDI for
podman); `nvidia` adds its graphics half for the Desktop and switches the
persistence daemon back off. Both are the only places `BR2_PACKAGE_NVIDIA_OPEN*`
may appear (`pengyos-desktop-nvidia lint`).

(`editions.toml` is the source of that table.)

**Edit the layer, never the generated defconfig**, then run:

```sh
scripts/compose-defconfigs.py          # rewrite ../pengyos_*_defconfig
scripts/compose-defconfigs.py --check  # exit 1 if any is stale (a test runs this)
```

Rules:

- A symbol set in a later layer overrides the earlier setting (the earlier line
  becomes a `# BR2_X: set by the <layer> layer` comment in the output). Use this
  for edition-specific values: hostname, rootfs size, the post-image script.
- Three lists **accumulate** instead — each layer names its own board's entry
  and Buildroot applies them in layer order:
  - `BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES` — later fragment wins per option;
  - `BR2_ROOTFS_OVERLAY` — later overlay's file wins per path;
  - `BR2_ROOTFS_POST_BUILD_SCRIPT` — all run, in order.
  So a Desktop image has Core's and Server's kernel options, files and
  post-build steps, plus its own.
- Put a package in the **lowest** layer that needs it. Something every machine
  needs (firmware, disk tools, Python) belongs in core; remote-work and
  services tooling in server; graphics, audio, desktop hardware in desktop.

The generated files keep their names and paths, so `targets.toml`,
`pengyos-build` and the tools that read them are unchanged.

### Writing overlay files and post-build steps

- A file belongs in the **lowest** layer's overlay that needs it; a higher
  layer only adds files or overrides a path on purpose (Desktop masks
  `getty@tty1.service`, so Core's autologin drop-in for it is inert there).
- `board/core/post-build.sh` is the **core layer's** script and runs
  first in every edition: SSH key injection, no baked host keys, provenance, the
  SBOM, ensurepip, and the "not installed" tool stubs. Edition scripts only do
  what makes the image *that* edition (hostname, os-release, GRUB menu, …).
- Never ship a stub for a tool in an overlay: overlays land after the packages
  (and the rootfs merges /bin, /sbin, /usr/sbin into /usr/bin), so the stub
  would replace the real tool in any edition that ships it. Stubs live in
  `board/core/tool-stubs/` and are installed only where the real tool
  is missing.
- Post-image scripts are per edition (each assembles its own image); what they
  share lives in `board/common/` — `installer-image.sh` builds the install
  payload and the installer stick for both Server and Desktop.
