# nvidia profile — NVIDIA proprietary discrete-GPU reference build.
# Composes: QEMU virt (the boot machine) + systemd (init) + NVIDIA (proprietary).
#
# A desktop / workstation build that boots under the QEMU "virt" machine, uses
# systemd as init, and drives a NVIDIA proprietary discrete GPU via the
# vendor-provided DRM driver (nvidia-proprietary fragment). The NVIDIA driver
# is out-of-tree/proprietary, hence its own dedicated fragment and profile.
qemu
systemd
nvidia-proprietary
