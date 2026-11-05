# pengyos-gpu-memory

How much system RAM an AMD GPU may use, and the one command that raises it.

```sh
pengyos-gpu-memory                 # RAM, the live and configured limit, each amdgpu GPU
pengyos-gpu-memory status --json
sudo pengyos-gpu-memory set 120G   # or 90%, or max (RAM minus 4 GiB); then reboot
sudo pengyos-gpu-memory reset      # back to the kernel's default
```

An AMD APU (Strix Halo's Radeon 8060S, any Ryzen iGPU) has no memory of its own. The
BIOS reserves a fixed carve-out (what the kernel calls VRAM), and the GPU maps system
RAM on demand (GTT). The kernel caps GTT at about half of RAM, so on a 128 GB Strix
Halo machine a 70–120 GB model does not load, and the OS gets the blame.

AMD's advice, which this follows: keep the BIOS carve-out (the *UMA frame buffer*) at
its minimum, not Auto (on some boards Auto is 64 GiB, taken from the OS for good), and
raise the GTT limit instead. `set` writes `/etc/modprobe.d/60-pengyos-gpu-memory.conf`:

```
options ttm pages_limit=<4 KiB pages>
options amdgpu gttsize=<MiB>
```

`ttm.pages_limit` is the limit AMD documents; `amdgpu.gttsize` is older but still
read, and the pair is what the gufo and halogen Strix Halo hosts run. The file is on
the data partition (`/etc` is the overlay), so it survives updates; it takes effect at
the next boot. It records the RAM size it was computed for, and `status` says when the
machine's RAM no longer matches: values for 128 GB are wrong for 96 or 192 GB.

The ceiling is RAM minus 4 GiB, so the system always keeps memory of its own; `set`
refuses more. Exit codes: 0 ok, 2 usage, 3 no amdgpu GPU (without `--force`), 4 size out
of range, 5 not root.

## With a ROCm container

ROCm stays in the container (as CUDA's toolkit does on Server NVIDIA); the host
provides `/dev/kfd`, `/dev/dri`, crun and no locked-memory limit:

```sh
sudo pengyos-gpu-memory set max && sudo reboot
podman run --rm --device /dev/kfd --device /dev/dri --group-add keep-groups \
    --ulimit memlock=-1:-1 docker.io/rocm/dev-ubuntu-24.04 rocminfo
```

Tested by `tests/` against a sandbox root (`--root DIR`); no GPU needed.
