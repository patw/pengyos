"""PengyOS media — build the live install medium for an edition.

Assembles a bootable install medium (ISO or disk image) from an edition
release: the live/installer rootfs, the installer, and a boot entry, as an
explicit, deterministic *plan* plus an injectable *executor*.

The plan turns an edition + release into the medium's layout (ESP, live
squashfs/rootfs, the embedded installer, the boot entry) and the ordered,
shell-quotable argv to build it (mksquashfs, the ESP/boot assembly, xorriso
for an ISO). ``build`` drives that plan through an injectable runner (with a
side-effect-free ``--dry-run``). ``lint`` rejects a medium that omits the
installer, a boot entry, the release, or a correct os-release edition id.

Stdlib only.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
