"""The deterministic build steps for a medium (spec.md, item 3).

``plan`` (item 3) turns the edition + release into the medium's *layout* (the
:class:`~pengyos_media.model.MediumPlan` — an ESP, the live squashfs/rootfs, the
embedded installer, the boot entry). Before the plan can be driven by a runner
(item 4) the layout has to be turned into an **ordered, deterministic list of
build steps**, each a plain, shell-quotable ``argv``. This module is that
derivation.

The steps are a *pure function* of the plan: given a plan,
:func:`build_steps` returns exactly the same ordered argv every time. That is
what makes the plan golden-testable and the runner (item 4) fully mockable —
``build --dry-run`` just renders these argv and runs nothing.

The steps reference paths **relative to a staging root** (the build tree the
runner creates and chdir's into): the live rootfs source, the squashfs image it
produces, the staged ESP/boot, and the ISO it burns. That keeps the argv
portable (no absolute, host-specific paths) and deterministic (the same plan
always yields the same argv). The runner supplies the inputs — the live rootfs
directory and the boot/asset files — and runs each step with the staging root
as its working directory.

The ISO steps, in order:

1. **mksquashfs the live rootfs** — compress the live rootfs directory into the
   squashfs image the live session boots from (``<name>``).
2. **assemble the ESP/boot** — lay the EFI loader, the boot menu, the installer,
   and the boot kernels/images onto the staged image so any firmware finds a
   path.
3. **xorriso the ISO** — burn the staged tree into a hybrid (isohybrid MBR+GPT)
   ISO so picky UEFI/CSM firmware boots it (the "USB boot dark arts" note on the
   bus).

A ``DISK_IMAGE`` medium is a GPT disk image: an **ESP** (FAT32, holds the EFI
loader/menu) + a **live partition** (the squashfs the live session boots). Its
steps are disk-image-correct — they write the squashfs *onto the live
partition* and lay the loader *on the ESP partition* (never stage into an ISO
tree):

1. **mkfs-disk** — ``truncate`` the image to a fixed size and ``sgdisk`` it into
   a GPT layout: partition 1 is the EFI System Partition (GUID
   ``C12A7328…``), partition 2 the live partition (GUID the Linux root x86-64
   value).
2. **losetup** — attach the image to a loop device with partitions visible
   (``losetup -f --show -P`` → ``/dev/loopN`` with ``/dev/loopNp1`` /
   ``/dev/loopNp2``).
3. **mkfs-esp** — format the ESP partition (partition 1) FAT32 with ``mkfs.fat``
   (label ``BOOT``).
4. **mmd-esp** — create the ESP directory tree (the loader's ancestor dirs,
   level by level, plus the staged boot dir) with ``mmd`` (mtools ``mcopy`` does
   not create parent directories).
5. **mksquashfs the live rootfs** — compress the live rootfs into the squashfs
   image (``<name>``).
6. **assemble-esp** — lay the EFI loader, boot menu, installer, and kernels onto
   the **ESP partition** (partition 1) with ``mcopy -i <loop>p1`` (mtools write
   straight onto the partition — no ISO tree, no mount).
7. **write-live** — ``dd`` the squashfs onto the **live partition** (partition
   2) with ``conv=notrunc`` so the partition *is* the squashfs.
8. **losetup-detach** — detach the loop device.

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pengyos_media.model import (
    LiveRootfsFormat,
    MediumKind,
    MediumPlan,
)

#: The compression the live squashfs is built with (deterministic, reproducible).
SQUASHFS_COMPRESSOR = "zstd"
#: The live squashfs volume id (matches the live partition label).
SQUASHFS_VOLUME_ID = "PENGYOS_LIVE"
#: The ISO volume id / label (must be <= 32 chars for ISO9660).
ISO_VOLUME_ID = "PENGYOS_LIVE"
#: The ISO version stamp xorriso/mkisofs writes (ISO9660 revision 2022).
ISO_VERSION = "20220101-350000"
#: xorriso flag that makes the ISO an isohybrid (MBR + GPT) so any firmware boots it.
ISOHYBRID_FLAG = "-isohybrid-gpt-basdat"

# Staged layout (all paths are relative to the build/staging root). The live
# rootfs source, the squashfs image it produces, the installer, and the boot
# kernels/images all live under the staging root; the ISO is burned from there.
LIVE_ROOTFS = "live/rootfs"
LIVE_IMAGE = "live/{name}"
BOOT_DIR = "boot"
INSTALLER_PATH = "boot/{binary}"
KERNEL_PATH = "boot/vmlinuz"
INITRD_PATH = "boot/initrd.img"
ISO_OUTPUT = "{edition}-install.iso"

# --- Disk-image layout -----------------------------------------------------------
# A disk image is a GPT image: an **ESP** (FAT32, holds the EFI loader/menu) and a
# **live partition** (the squashfs the live session boots — the partition *is* the
# squashfs, so it is left raw rather than formatted). These constants describe that
# layout. The squashfs is written *onto the live partition* and the loader laid
# *on the ESP partition* — never staged into an ISO tree (see the disk-image item).
DISK_IMAGE_OUTPUT = "{edition}-install.img"
#: The fixed size of the disk image (ESP + live partition).
DISK_IMAGE_SIZE = "512M"
#: GPT partition-type GUIDs (the canonical EFI System + Linux root x86-64 values).
GPT_EFI_SYSTEM = "C12A7328-F81F-11D2-BA4B-00AA00BC3331"
GPT_LINUX_ROOT_X86_64 = "0FC6DADA-F63A-11D0-A536-F80D18B59718"
#: The FAT32 label of the ESP (an 8.3 FAT label, <= 11 chars).
ESP_FAT_LABEL = "BOOT"
#: The partition numbers inside the disk image: 1 = ESP (EFI), 2 = live.
ESP_PARTITION = 1
LIVE_PARTITION = 2


@dataclass(frozen=True)
class BuildStep:
    """One ordered build step: a name, what it does, and its argv.

    ``argv`` is a plain list of arguments — the first element is the command,
    the rest its arguments. It is shell-quotable (see :func:`argv_to_shell`) and
    side-effect free to render (that is what ``build --dry-run`` does).
    """

    #: A short, stable identifier for the step (e.g. ``mksquashfs``).
    name: str
    #: What the step does (human / CLI summary).
    description: str
    #: The command + arguments for the step.
    argv: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, str]:
        """Render the step as a plain ``{"name", "description", "argv"}`` dict."""
        return {
            "name": self.name,
            "description": self.description,
            "argv": " ".join(shell_quote(a) for a in self.argv),
        }


def shell_quote(arg: str) -> str:
    """Quota ``arg`` for a POSIX shell so it round-trips through ``sh -c``.

    Uses single quotes and escapes an embedded single quote as ``'\\'`` (the
    safe ANSI-C style). An empty argument becomes ``''`` (a real empty string,
    not nothing). This is what makes an argv printable and re-runnable.
    """
    if "'" not in arg:
        return f"'{arg}'"
    escaped = arg.replace("'", "'\\''")
    return f"'{escaped}'"


def argv_to_shell(argv: list[str]) -> str:
    """Render an argv as a single shell-quotable command string."""
    return " ".join(shell_quote(a) for a in argv)


def render_build_steps_text(steps: list[BuildStep]) -> str:
    """A human-readable summary of the build steps (name + argv per step)."""
    lines = [f"build steps ({len(steps)}):"]
    for i, step in enumerate(steps, start=1):
        lines.append(f"  {i}. {step.name}: {step.description}")
        if step.argv:
            lines.append(f"      argv: {argv_to_shell(step.argv)}")
        else:  # pragma: no cover - a step always has a command
            lines.append("      argv: (none)")
    return "\n".join(lines)


def _step_mksquashfs(plan: MediumPlan) -> BuildStep:
    name = plan.live_rootfs.name
    argv = [
        "mksquashfs",
        LIVE_ROOTFS,
        LIVE_IMAGE.format(name=name),
        "-comp",
        SQUASHFS_COMPRESSOR,
        "-vol_id",
        SQUASHFS_VOLUME_ID,
        "-noappend",
    ]
    # A packed rootfs is built directly; an overlay rootfs keeps the lower
    # (packed) + upper (overlay) split the live session expects.
    if plan.live_rootfs.format == LiveRootfsFormat.DIR:
        argv += ["-packed-ews"]
    return BuildStep(
        name="mksquashfs",
        description=(
            f"Compress the live rootfs into the squashfs image "
            f"'{name}' ({SQUASHFS_COMPRESSOR} compression)."
        ),
        argv=argv,
    )


def _step_esp_boot(plan: MediumPlan) -> BuildStep:
    installer = plan.installer.binary
    loader = plan.boot_entry.loader
    # The loader path (``/EFI/BOOT/bootx64.efi``) is the path *inside* the ISO;
    # staged for xorriso it is that path with the leading slash stripped (the
    # ISO root is the staging root). ``install -D -m`` copies the source to the
    # staged destination, creating the leading directories. The boot menu sits
    # next to the loader; the installer and kernels under the staged boot dir.
    staged_loader = loader.lstrip("/")
    staged_menu_dir = staged_loader.rsplit("/", 1)[0]
    argv = [
        "install", "-D", "-m", "0755", "boot/bootx64.efi", staged_loader,
        "install", "-D", "-m", "0644", "assets/loader.conf",
        f"{staged_menu_dir}/loader.conf",
        "install", "-D", "-m", "0755", "assets/installer",
        INSTALLER_PATH.format(binary=installer),
        "install", "-D", "-m", "0644", "assets/vmlinuz", KERNEL_PATH,
        "install", "-D", "-m", "0644", "assets/initrd.img", INITRD_PATH,
    ]
    return BuildStep(
        name="assemble-esp-boot",
        description=(
            "Lay the EFI loader, boot menu, installer, and boot kernels onto "
            "the staged ESP/boot so any firmware finds a path."
        ),
        argv=argv,
    )


def _step_xorriso(plan: MediumPlan) -> BuildStep:
    iso = ISO_OUTPUT.format(edition=plan.edition.value)
    # The boot image the firmware loads — the staged loader path (the loader's
    # ISO-internal path with the leading slash stripped, matching where
    # assemble-esp-boot staged it). A hybrid (isohybrid MBR+GPT) ISO so picky
    # UEFI/CSM firmware finds a boot path — the "USB boot dark arts" note on the
    # bus. The staged tree is the ISO's contents.
    staged_boot_image = plan.boot_entry.loader.lstrip("/")
    argv = [
        "xorriso",
        "-as",
        "mkisofs",
        "-r",
        "-J",
        "-b",
        staged_boot_image,
        ISOHYBRID_FLAG,
        "-iso-version",
        ISO_VERSION,
        "-volid",
        ISO_VOLUME_ID,
        "-o",
        iso,
        ".",
    ]
    return BuildStep(
        name="xorriso",
        description=(
            "Burn the staged tree into a hybrid (isohybrid MBR+GPT) ISO so any "
            "firmware can boot it."
        ),
        argv=argv,
    )


def _disk_image_path(plan: MediumPlan) -> str:
    """The disk-image output path for ``plan`` (``<edition>-install.img``)."""
    return DISK_IMAGE_OUTPUT.format(edition=plan.edition.value)


def _step_mkfs_disk(plan: MediumPlan) -> BuildStep:
    image = _disk_image_path(plan)
    # A disk-image medium is a GPT image: an ESP (partition 1) + a live
    # partition (partition 2, the raw squashfs). Partition the image with
    # ``sgdisk`` (partition 1 = EFI System, partition 2 = the live squashfs).
    # The ESP is *formatted* FAT32 by :func:`_step_mkfs_esp` once the loop
    # device exposes the partition; the live partition is left raw — it *is* the
    # squashfs written onto it by :func:`_step_write_live`.
    argv = [
        "truncate", "--size", DISK_IMAGE_SIZE, image,
        "sgdisk", "-n", f"{ESP_PARTITION}:0:+63MiB",
        "-t", f"{ESP_PARTITION}:{GPT_EFI_SYSTEM}", image,
        "sgdisk", "-n", f"{LIVE_PARTITION}:0:0",
        "-t", f"{LIVE_PARTITION}:{GPT_LINUX_ROOT_X86_64}", image,
    ]
    return BuildStep(
        name="mkfs-disk",
        description=(
            "Create the disk image (GPT: an ESP + a live partition) with "
            "sgdisk."
        ),
        argv=argv,
    )


def _step_mkfs_esp(plan: MediumPlan) -> BuildStep:
    # Format the ESP (partition 1) FAT32 with the ``BOOT`` label. Runs after the
    # loop device exposes the partition (see :func:`_step_losetup`).
    return BuildStep(
        name="mkfs-esp",
        description=(
            "Format the ESP partition FAT32 (label 'BOOT')."
        ),
        argv=[
            "mkfs.fat", "-n", ESP_FAT_LABEL, "-F", "32",
            f"<loop>-p{ESP_PARTITION}",
        ],
    )


def _loop_dev(plan: MediumPlan) -> str:
    """The loop device path the image is attached to (``/dev/loopN``)."""
    # The runner picks the free loop device; the placeholder is filled in by the
    # runner (item 4) with the actual ``/dev/loopN`` after ``losetup --show``.
    return "/dev/loopN"


def _step_losetup(plan: MediumPlan) -> BuildStep:
    image = _disk_image_path(plan)
    # Attach the (already partitioned) image to a loop device with its
    # partitions visible (``-P``): ``/dev/loopN`` with ``/dev/loopNp1`` (ESP) and
    # ``/dev/loopNp2`` (live). The ``--show`` prints the device; the runner
    # substitutes it for the ``<loop>`` placeholder in the following steps.
    return BuildStep(
        name="losetup",
        description=(
            "Attach the disk image to a loop device with partitions visible "
            "(/dev/loopNp1 ESP, /dev/loopNp2 live)."
        ),
        argv=["losetup", "-f", "--show", "-P", image],
    )


def _esp_dir_paths(plan: MediumPlan) -> list[str]:
    """The MS-DOS directory paths to create on the ESP before ``mcopy``.

    mtools ``mcopy`` does **not** create parent directories, and ``mmd`` creates
    one level at a time (a nested path fails unless its parents exist), so the
    loader's ancestor directories are returned level by level (root first) plus
    the staged boot dir (installer + kernels). Duplicates are dropped while the
    order is kept, so they can be created in one pass.
    """
    staged_loader = plan.boot_entry.loader.lstrip("/")
    parts = staged_loader.split("/")
    dirs: list[str] = []
    seen: set[str] = set()
    # The loader's own ancestor directories, from the root down.
    for i in range(1, len(parts)):
        d = "/" + "/".join(parts[:i])
        if d not in seen:
            seen.add(d)
            dirs.append(d)
    # The installer + kernels live under the staged boot dir.
    boot_dir = "/" + BOOT_DIR.lstrip("/")
    if boot_dir not in seen:
        dirs.append(boot_dir)
    return dirs


def _step_mmd_esp(plan: MediumPlan) -> BuildStep:
    loop = f"<loop>-p{ESP_PARTITION}"
    # Create the ESP directory tree (loader ancestors + boot dir), level by
    # level, so the following ``mcopy``s have a home.
    argv: list[str] = []
    for d in _esp_dir_paths(plan):
        argv += ["mmd", "-i", loop, f"::{d}"]
    return BuildStep(
        name="mmd-esp",
        description="Create the ESP directory tree (loader + boot dirs).",
        argv=argv,
    )


def _step_assemble_esp(plan: MediumPlan) -> BuildStep:
    loop = f"<loop>-p{ESP_PARTITION}"
    installer = plan.installer.binary
    # Lay the EFI loader, boot menu, installer, and kernels onto the **ESP
    # partition** (partition 1). mtools ``mcopy -i`` writes straight onto the
    # partition — no ISO tree, no mount — at the loader's real path inside the
    # ESP (``/EFI/BOOT/bootx64.efi``). The boot menu sits *next to* the loader
    # (in the loader's directory, not nested under the loader file); the
    # installer and kernels under the staged boot dir. The directories are
    # created by :func:`_step_mmd_esp` first (mcopy does not create them).
    staged_loader = plan.boot_entry.loader.lstrip("/")
    staged_loader_dir = staged_loader.rsplit("/", 1)[0]
    argv = [
        "mcopy", "-i", loop, "-o", "boot/bootx64.efi",
        f"::/{staged_loader}",
        "mcopy", "-i", loop, "-o", "assets/loader.conf",
        f"::/{staged_loader_dir}/loader.conf",
        "mcopy", "-i", loop, "-o", "assets/installer",
        f"::/{INSTALLER_PATH.format(binary=installer).lstrip('/')}",
        "mcopy", "-i", loop, "-o", "assets/vmlinuz",
        f"::/{KERNEL_PATH.lstrip('/')}",
        "mcopy", "-i", loop, "-o", "assets/initrd.img",
        f"::/{INITRD_PATH.lstrip('/')}",
    ]
    return BuildStep(
        name="assemble-esp",
        description=(
            "Lay the EFI loader, boot menu, installer, and boot kernels onto "
            "the ESP partition (mtools mmd + mcopy -i)."
        ),
        argv=argv,
    )


def _step_write_live(plan: MediumPlan) -> BuildStep:
    loop = f"<loop>-p{LIVE_PARTITION}"
    name = plan.live_rootfs.name
    # Write the squashfs **onto the live partition** (partition 2) with ``dd
    # conv=notrunc`` so the partition *is* the squashfs (the partition is left
    # raw by ``mkfs-disk``). The squashfs volume id (-vol_id) marks it at boot.
    return BuildStep(
        name="write-live",
        description=(
            f"dd the squashfs image '{name}' onto the live partition so the "
            "partition is the squashfs."
        ),
        argv=[
            "dd",
            f"if={LIVE_IMAGE.format(name=name)}",
            f"of={loop}",
            "conv=notrunc",
            "status=none",
        ],
    )


def _step_losetup_detach(plan: MediumPlan) -> BuildStep:
    return BuildStep(
        name="losetup-detach",
        description="Detach the loop device.",
        argv=["losetup", "-d", "<loop>"],
    )


def build_steps(plan: MediumPlan) -> list[BuildStep]:
    """Return the ordered, deterministic build steps for ``plan``.

    The steps are a pure function of the plan: the same plan always yields the
    same ordered argv. :func:`build_steps` dispatches on the plan's
    :class:`~pengyos_media.model.MediumKind` — an ISO emits the ``mksquashfs`` /
    ``assemble-esp-boot`` / ``xorriso`` steps; a disk image emits the ``mkfs``
    steps (item 3's follow-up).
    """
    if plan.kind == MediumKind.ISO:
        return [
            _step_mksquashfs(plan),
            _step_esp_boot(plan),
            _step_xorriso(plan),
        ]
    if plan.kind == MediumKind.DISK_IMAGE:
        # Disk-image-correct: partition the image (sgdisk), attach a loop device,
        # format the ESP, build the squashfs, lay the loader on the ESP
        # partition, write the squashfs onto the live partition, then detach the
        # loop. The ESP is formatted only after the loop exposes the partition.
        return [
            _step_mkfs_disk(plan),
            _step_losetup(plan),
            _step_mkfs_esp(plan),
            _step_mmd_esp(plan),
            _step_mksquashfs(plan),
            _step_assemble_esp(plan),
            _step_write_live(plan),
            _step_losetup_detach(plan),
        ]
    raise ValueError(f"unsupported medium kind: {plan.kind!r}")
