# amd profile — AMD Ryzen Embedded V1000 (Zen APU) reference build.
# Composes: QEMU virt (the boot machine) + systemd (init) + AMD IGPU (amdgpu).
#
# The AMD Ryzen Embedded V1000 series is an APU: a Zen CPU paired with a
# Vega-class GPU driven by the kernel's amdgpu/i915-style DRM/KMS stack. This
# profile mirrors n100 but swaps the Intel i915 IGPU fragment for the AMD one.
qemu
systemd
amd-igpu
