"""PengyOS desktop-nvidia.

Containment lint for the **opt-in NVIDIA Desktop edition** of
[PengyOS](https://pengyos.catbee.ca). The edition is composed from the defconfig layers
(``configs/layers/nvidia.config``) and ships the in-tree ``nvidia-open`` package
(NVIDIA's open GPU kernel modules + matching proprietary userspace).

The design rule (SPEC §2.5) requires the proprietary driver to be *contained*:
an opt-in edition, never in the default Desktop/Core/Server image.
:mod:`pengyos_desktop_nvidia.lint` proves that; the CLI in
:mod:`pengyos_desktop_nvidia.cli` is the user-facing surface.
"""

__version__ = "0.4.0"

__all__ = ["__version__"]
