# Changelog

## 0.1.0 — 2026-11-04

- First version: `status [--json]`, `set SIZE` (120G, 90%, max), `reset`. Writes
  `options ttm pages_limit=` and `options amdgpu gttsize=` to
  `/etc/modprobe.d/60-pengyos-gpu-memory.conf`, with a ceiling of RAM minus 4 GiB.
  For AMD APUs (Strix Halo) running ROCm inference containers.
