"""PengyOS installer — install PengyOS Core from the live stick to a target disk.

Computes an explicit, ordered install plan (GPT: EFI System Partition + ext4
root), copies the running rootfs, and wires GRUB to the new disk. Stdlib only.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
