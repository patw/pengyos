"""Plan model, target sanity checks, and the ordered install-plan builder.

This module is *pure*: it never opens, writes, or formats any device. The
``plan`` subcommand (see :mod:`pengyos_installer.cli`) uses it to compute an
explicit, ordered install plan for a target block device and render it as a
numbered list of shell-quotable command lines, or as JSON with ``--json``.

All external state the plan needs — device type/size, and the identity of the
live root / ESP — is supplied by an injected :class:`TargetProbe`, so the
whole thing is golden-testable without touching a disk. The default collector
(:func:`collect_target_probe`) reads only from ``os.stat`` and ``/sys`` and
still writes nothing.

The :func:`build_plan` function composes the full ordered step list. The
partition, format, copy, and configure steps carry their full ``parted`` /
``mkfs.*`` / ``mount`` / ``rsync`` / ``tar`` / ``blkid`` argv (see
:func:`partition_steps`, :func:`format_steps`, :func:`copy_steps`,
:func:`partuuid_steps`, and :func:`bootloader_steps`). The configure phase's
*write* steps — including the ``EFI/BOOT/grub.cfg`` fallback config — are
internal (``argv=None``, ``internal:`` note): their content embeds the
PARTUUIDs read from ``blkid`` at install time (and the live hostname when
``--hostname`` is not given), so :func:`render_fstab` / :func:`render_hostname`
/ :func:`render_installed_marker` / :func:`render_grub_cfg` produce it then.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence, Tuple

#: Documented minimum target-disk size: 4 GiB (spec.md, sanity step).
MIN_TARGET_SIZE_BYTES = 4 * 1024**3

#: The ordered phases every install plan contains (spec.md, "Steps ... in order").
PHASES: Tuple[str, ...] = (
    "sanity",
    "partition",
    "format",
    "copy",
    "configure",
    "bootloader",
    "teardown",
)

#: Partition names and filesystem labels (stable; golden-tested argv).
#: ``<esp-end>`` in the spec is the ESP end offset, i.e. the ``--esp-size``
#: value itself: the ESP occupies ``1MiB..<esp-size>`` and root starts at
#: ``<esp-size>`` (spec.md, step 2).
ESP_PART_NAME = "ESP"
ROOT_PART_NAME = "root"
#: Start offset of the ESP (leaves 1 MiB before partition 1, GPT best practice).
ESP_START_OFFSET = "1MiB"
#: mkfs.vfat volume label of the ESP.
ESP_FS_LABEL = "PENGYOS-ESP"
#: mkfs.ext4 volume label of the root partition.
ROOT_FS_LABEL = "pengyos-root"

#: Sanity-error codes (stable; golden-tested).
ERR_NOT_BLOCK_DEVICE = "not-block-device"
ERR_IS_LIVE_ROOT = "is-live-root"
ERR_IS_LIVE_ESP = "is-live-esp"
ERR_TOO_SMALL = "too-small"


@dataclass(frozen=True)
class TargetProbe:
    """A read-only snapshot of everything the installer needs to know about the
    target device and the live system.

    Injected by tests (fixtures) and populated by :func:`collect_target_probe`
    for real use. It deliberately carries no handles to any device.
    """

    #: The target device path as given, e.g. ``/dev/sdb``.
    path: str
    #: ``os.stat`` reports S_ISBLK *and* the ``/sys/block/<name>`` node exists.
    is_block_device: bool
    #: Size in bytes (from ``/sys/block/<name>/size`` × 512; best effort).
    size_bytes: int
    #: Block device of the live ``/`` mount, or ``None`` if unknown.
    #: Populated by :func:`collect_target_probe` from ``/proc/mounts``.
    root_device: "str | None" = None
    #: Block device of the live EFI System Partition mount, or ``None``.
    #: Populated by :func:`collect_target_probe` from ``/proc/mounts``.
    esp_device: "str | None" = None


@dataclass(frozen=True)
class SanityError:
    """One reason the target was (or would be) refused."""

    code: str
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class SanityResult:
    """Outcome of :func:`check_target`."""

    ok: bool
    errors: Tuple[SanityError, ...]
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "size_bytes": self.size_bytes,
            "errors": [e.to_dict() for e in self.errors],
        }


@dataclass(frozen=True)
class Step:
    """One ordered step in the install plan.

    ``argv`` is the shell-quotable command line to run (``None`` for steps
    whose command is produced by a later task or that are purely internal).
    ``note`` carries a stable freeform annotation, e.g. the name of the
    follow-on task that will fill in ``argv``.
    """

    phase: str
    description: str
    argv: Tuple[str, ...] | None = None
    note: str | None = None

    def to_dict(self, seq: int) -> dict:
        return {
            "seq": seq,
            "phase": self.phase,
            "description": self.description,
            "argv": list(self.argv) if self.argv is not None else None,
            "note": self.note,
        }


@dataclass(frozen=True)
class InstallPlan:
    """The full, ordered install plan for one target device."""

    target: str
    esp_size: str
    hostname: "str | None"
    rootfs_src: "str | None"
    min_target_size_bytes: int
    sanity: SanityResult
    steps: Tuple[Step, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "esp_size": self.esp_size,
            "hostname": self.hostname,
            "rootfs_src": self.rootfs_src,
            "minimum_target_size_bytes": self.min_target_size_bytes,
            "sanity": self.sanity.to_dict(),
            "steps": [s.to_dict(seq) for seq, s in enumerate(self.steps, start=1)],
        }

    def to_json(self, indent: int = 2) -> str:
        """Deterministic, human-stable JSON (fixed key insertion order)."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)


def render_plan_text(plan: InstallPlan) -> str:
    """Render the plan as a deterministic, numbered, human-readable list.

    Every step shows its phase and description; a step that carries ``argv``
    also shows the shell-quoted command line, and a step with a ``note`` shows
    it on the following indented line. Byte-identical for identical inputs.
    """
    lines = [
        "PengyOS install plan",
        f"  target:   {plan.target}",
        f"  esp-size: {plan.esp_size}",
        f"  hostname: {plan.hostname if plan.hostname is not None else '(live)'}",
        f"  rootfs:   {plan.rootfs_src if plan.rootfs_src is not None else '/ (the running root)'}",
        "",
    ]
    for seq, step in enumerate(plan.steps, start=1):
        lines.append(f"  {seq}. [{step.phase}] {step.description}")
        if step.argv is not None:
            lines.append(f"       $ {shlex.join(step.argv)}")
        if step.note is not None:
            lines.append(f"       ({step.note})")
    return "\n".join(lines)


def _is_device_prefix(parent: str, child: str) -> bool:
    """True if ``child`` is a partition of the whole device ``parent``.

    i.e. ``child`` starts with ``parent`` and the next character is a
    partition index digit. ``/dev/sda1`` is a partition of ``/dev/sda``.
    This is precise: it does not misfire on ``/dev/loop0`` vs ``/dev/loop``
    (``loop0`` does not start with the shorter name followed by a digit).
    """
    return (
        child.startswith(parent)
        and len(child) > len(parent)
        and child[len(parent):].lstrip("p").startswith(tuple("0123456789"))
    )


def overlaps_device(a: str, b: str) -> bool:
    """True if installing to ``a`` would touch the device ``b`` lives on.

    ``a`` overlaps ``b`` when they are the same path, or one is the whole
    disk and the other is a partition on it. This is what makes the sanity
    check refuse a target that is the live device, the live disk, or a
    partition of the live disk (any of which would wipe the running system).
    """
    if not a or not b:
        return False
    if a == b:
        return True
    return _is_device_prefix(a, b) or _is_device_prefix(b, a)


def _device_name_needs_p_separator(target: str) -> bool:
    """True if the device *name* ends in a digit.

    Linux appends the partition number to the whole device name; when that
    name already ends in a digit, a ``p`` is inserted instead so the number is
    not ambiguous (``/dev/nvme0n1`` -> ``/dev/nvme0n1p1``, ``/dev/loop0`` ->
    ``/dev/loop0p1``, ``/dev/mmcblk0`` -> ``/dev/mmcblk0p1``). A plain disk
    such as ``/dev/sda`` needs no separator (``/dev/sda1``).
    """
    name = os.path.basename(target.rstrip("/"))
    return bool(name) and name[-1].isdigit()


def partition_device(target: str, index: int) -> str:
    """The block-device path of partition ``index`` of ``target``.

    Plain disks name their partitions ``<tgt>1``, ``<tgt>2``, ...; disks whose
    name ends in a digit use the kernel's ``p``-suffixed form ``<tgt>p1``,
    ``<tgt>p2`` (see :func:`_device_name_needs_p_separator`). This is the
    single source of the partition device names used by every
    partition/format/copy/configure step and by the install execution, so an
    NVMe/loop target is handled consistently everywhere.
    """
    suffix = f"p{index}" if _device_name_needs_p_separator(target) else str(index)
    return target + suffix


def check_target(
    probe: TargetProbe, min_size_bytes: int = MIN_TARGET_SIZE_BYTES
) -> SanityResult:
    """Run the sanity checks (spec.md step 1) against :class:`TargetProbe`.

    Checks, in order:

    * the target is a block device;
    * it is *not* the live root device;
    * it is *not* the live EFI System Partition;
    * its size is at least ``min_size_bytes`` (the documented 4 GiB minimum).

    All applicable failures are reported together (the plan is refused if any
    fail); the result never raises for a bad target.
    """
    errors: list[SanityError] = []
    if not probe.is_block_device:
        errors.append(
            SanityError(
                ERR_NOT_BLOCK_DEVICE,
                f"{probe.path} is not a block device "
                "(no S_ISBLK stat and no /sys/block node)",
            )
        )
    if probe.root_device is not None and overlaps_device(probe.path, probe.root_device):
        errors.append(
            SanityError(
                ERR_IS_LIVE_ROOT,
                f"{probe.path} is the live root device ({probe.root_device}); "
                "refusing to install over the running system",
            )
        )
    if probe.esp_device is not None and overlaps_device(probe.path, probe.esp_device):
        errors.append(
            SanityError(
                ERR_IS_LIVE_ESP,
                f"{probe.path} is the live EFI System Partition "
                f"({probe.esp_device}); refusing to install over the boot partition",
            )
        )
    if probe.size_bytes < min_size_bytes:
        errors.append(
            SanityError(
                ERR_TOO_SMALL,
                f"{probe.path} is {probe.size_bytes} bytes, below the "
                f"{min_size_bytes}-byte ({min_size_bytes // (1024**3)} GiB) minimum",
            )
        )
    return SanityResult(ok=not errors, errors=tuple(errors), size_bytes=probe.size_bytes)


def partition_steps(target: str, esp_size: str) -> Tuple[Step, ...]:
    """The ``parted`` steps of the *partition* phase (spec.md, step 2).

    Four steps, in order: create the GPT label, create the ESP, flag it
    ``esp``, create the root partition. ``<esp-end>`` in the spec is the
    ESP end offset, i.e. ``esp_size`` itself, so the root partition starts
    at ``esp_size`` and runs to ``100%``. The argv is golden-stable.
    """
    return (
        Step(
            phase="partition",
            description="parted: create a fresh GPT partition table",
            argv=("parted", "-s", target, "mklabel", "gpt"),
        ),
        Step(
            phase="partition",
            description=(
                f"parted: create the EFI System Partition {ESP_PART_NAME} "
                f"(fat32, {ESP_START_OFFSET}..{esp_size})"
            ),
            argv=(
                "parted", "-s", target, "mkpart", ESP_PART_NAME, "fat32",
                ESP_START_OFFSET, esp_size,
            ),
        ),
        Step(
            phase="partition",
            description="parted: set the esp flag on partition 1",
            argv=("parted", "-s", target, "set", "1", "esp", "on"),
        ),
        Step(
            phase="partition",
            description=(
                f"parted: create the root partition {ROOT_PART_NAME} "
                f"(ext4, {esp_size}..100%)"
            ),
            argv=(
                "parted", "-s", target, "mkpart", ROOT_PART_NAME, "ext4",
                esp_size, "100%",
            ),
        ),
    )


def format_steps(target: str) -> Tuple[Step, ...]:
    """The ``mkfs.*`` steps of the *format* phase (spec.md, step 3).

    Two steps: FAT32 for the ESP (partition 1) and ext4 for the root
    (partition 2), each with its stable volume label. The partition device
    names come from :func:`partition_device` (plain ``<target>1`` /
    ``<target>2`` for most disks, ``<target>p1`` / ``<target>p2`` for
    NVMe/loop-style names that end in a digit). The argv is golden-stable.
    """
    esp_part = partition_device(target, 1)
    root_part = partition_device(target, 2)
    return (
        Step(
            phase="format",
            description=(
                f"mkfs.vfat: format {esp_part} as FAT32 with label {ESP_FS_LABEL}"
            ),
            argv=("mkfs.vfat", "-F32", "-n", ESP_FS_LABEL, esp_part),
        ),
        Step(
            phase="format",
            description=(
                f"mkfs.ext4: format {root_part} as ext4 with label {ROOT_FS_LABEL}"
            ),
            argv=("mkfs.ext4", "-L", ROOT_FS_LABEL, "-F", root_part),
        ),
    )


#: The working directory where the installer mounts the freshly created root
#: partition. The ESP is mounted at ``<work>/boot/efi`` (spec.md step 6).
WORK_DIR = "/mnt/pengyos-install"

#: Virtual / live directories that are never copied into the installed
#: rootfs (spec.md step 4). The target device itself is excluded as well.
#: Order is stable and golden-tested.
VIRTUAL_DIR_EXCLUDES = (
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/mnt",
    "/media",
    "/tmp",
)

#: File-name extensions that classify a ``--rootfs-src`` value as a tarball
#: (copy kind ``"tar"``); anything else is a directory.
TAR_EXTENSIONS = (".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")

#: Copy-kind values for :func:`classify_rootfs_src`.
COPY_SRC_KIND_DIR = "dir"
COPY_SRC_KIND_TAR = "tar"


def classify_rootfs_src(path: "str | None") -> str:
    """Classify a ``--rootfs-src`` value as :data:`COPY_SRC_KIND_DIR` or
    :data:`COPY_SRC_KIND_TAR` (spec.md step 4, "source discovery").

    Pure and syntactic, so ``plan`` stays disk-free: ``None`` (no
    ``--rootfs-src``) is the running ``/`` and therefore a directory; a path
    whose *name* ends in one of :data:`TAR_EXTENSIONS` is a tarball; anything
    else is treated as a directory. A tarball whose name lacks a recognised
    extension (or a directory named ``*.tar``) is misclassified at plan time;
    the install-execution layer re-validates the source with ``stat`` before
    running the copy step (a missing source, or one whose type contradicts
    this classification, is a hard error — see
    :func:`pengyos_installer.install.validate_rootfs_src`).
    """
    if path is None:
        return COPY_SRC_KIND_DIR
    lowered = path.lower()
    if any(lowered.endswith(ext) for ext in TAR_EXTENSIONS):
        return COPY_SRC_KIND_TAR
    return COPY_SRC_KIND_DIR


def rsync_source_arg(rootfs_src: "str | None") -> str:
    """The rsync source argument for a directory-kind source.

    ``None`` (the running root) is ``"/"``; an explicit directory is normalised
    to a single trailing slash (``/srv/rootfs`` -> ``/srv/rootfs/``) so rsync
    copies the directory's *contents*, not the directory itself, into the work
    dir.
    """
    if rootfs_src is None:
        return "/"
    return rootfs_src.rstrip("/") + "/"


def copy_steps(target: str, rootfs_src: "str | None") -> Tuple[Step, ...]:
    """The steps of the *copy* phase (spec.md, step 4).

    Two steps: mount the freshly formatted root partition at
    :data:`WORK_DIR`, then copy the source rootfs into it.

    * DIR source (the running ``/`` by default, or ``--rootfs-src DIR``):
      ``rsync -aHAX --numeric-ids`` with ``--exclude`` for every virtual dir
      in :data:`VIRTUAL_DIR_EXCLUDES` plus the target device, then the
      normalised source and the work dir.
    * TAR source (``--rootfs-src <file.tar[.gz|.xz|.bz2]>``):
      ``tar -xpf <src> -C <work>``.

    Pure: depends only on the target path and the (classified) source path.
    The argv is golden-stable.
    """
    root_part = partition_device(target, 2)
    steps: list[Step] = [
        Step(
            phase="copy",
            description=f"mount: mount the root partition {root_part} at the work dir {WORK_DIR}",
            argv=("mount", root_part, WORK_DIR),
        ),
    ]
    if classify_rootfs_src(rootfs_src) == COPY_SRC_KIND_TAR:
        steps.append(
            Step(
                phase="copy",
                description=(
                    f"tar: extract the rootfs tarball {rootfs_src} into the work dir {WORK_DIR}"
                ),
                argv=("tar", "-xpf", rootfs_src, "-C", WORK_DIR),
            )
        )
    else:
        src_arg = rsync_source_arg(rootfs_src)
        excludes = tuple(f"--exclude={d}" for d in VIRTUAL_DIR_EXCLUDES) + (f"--exclude={target}",)
        steps.append(
            Step(
                phase="copy",
                description=(
                    f"rsync: copy the source rootfs ({src_arg}) into the work dir {WORK_DIR} "
                    "with -aHAX --numeric-ids, excluding the virtual dirs and the target device"
                ),
                argv=("rsync", "-aHAX", "--numeric-ids", *excludes, src_arg, WORK_DIR),
            )
        )
    return tuple(steps)


# --- configure phase --------------------------------------------------------

#: Paths of the files the *configure* phase writes into the installed rootfs
#: (spec.md, step 5). Stable; golden-tested.
ETC_FSTAB = "/etc/fstab"
ETC_HOSTNAME = "/etc/hostname"
INSTALL_MARKER = "/etc/pengyos/installed"

#: Mount point of the ESP in the installed system (the fstab entry).
ESP_MOUNTPOINT = "/boot/efi"
#: Mount options for the ESP (protect the fallback boot path).
ESP_MOUNT_OPTIONS = "umask=0077"

#: The fallback (removable) GRUB config written to the ESP, relative to the
#: ESP mount point (spec.md, step 6).
FALLBACK_GRUB_CFG = "EFI/BOOT/grub.cfg"

#: Default boot timeout in seconds of the generated grub.cfg.
GRUB_TIMEOUT = 5

#: Configure-phase error codes (stable; golden-tested).
ERR_MISSING_PARTUUID = "missing-partuuid"
ERR_INVALID_HOSTNAME = "invalid-hostname"


class ConfigError(Exception):
    """A hard error of the *configure* phase.

    Raised when a value read at install time (e.g. a PARTUUID from
    ``blkid``) is missing or ambiguous, or when a planned value (the
    hostname) is invalid. Never silently produces a file.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def partuuid_steps(target: str) -> Tuple[Step, ...]:
    """The ``blkid`` steps of the *configure* phase (spec.md, step 5).

    Two steps, in order: read the PARTUUID of the ESP partition (partition
    1) and of the root partition (partition 2), each with
    ``blkid -s PARTUUID -o value`` (a single token on stdout). The partition
    device names come from :func:`partition_device`. The argv is
    golden-stable; the stdout is parsed with :func:`parse_partuuid` at
    install time.
    """
    esp_part = partition_device(target, 1)
    root_part = partition_device(target, 2)
    return (
        Step(
            phase="configure",
            description=f"blkid: read the PARTUUID of the ESP partition {esp_part}",
            argv=("blkid", "-s", "PARTUUID", "-o", "value", esp_part),
        ),
        Step(
            phase="configure",
            description=f"blkid: read the PARTUUID of the root partition {root_part}",
            argv=("blkid", "-s", "PARTUUID", "-o", "value", root_part),
        ),
    )


def parse_partuuid(output: str, device: str) -> str:
    """Parse the stdout of ``blkid -s PARTUUID -o value <device>`` into the
    PARTUUID of ``device``.

    The command prints exactly one token on stdout when the partition
    carries a PARTUUID. Anything else — empty output (e.g. the tool not
    seeing a fresh filesystem) or a multi-token answer — is a hard error:
    :class:`ConfigError` with code :data:`ERR_MISSING_PARTUUID` (spec.md:
    "a missing PARTUUID is a hard error").
    """
    tokens = output.split()
    if len(tokens) != 1:
        raise ConfigError(
            ERR_MISSING_PARTUUID,
            f"{device}: expected exactly one PARTUUID token on blkid stdout, "
            f"got {len(tokens)} ({output!r})",
        )
    return tokens[0]


def render_fstab(root_partuuid: str, esp_partuuid: str) -> str:
    """The exact content of the installed ``/etc/fstab`` (spec.md, step 5).

    Deterministic and golden-stable: a root ext4 entry and an ESP vfat
    entry, both keyed by PARTUUID (stable across reboots), in that order.
    Either PARTUUID empty is a hard error (:data:`ERR_MISSING_PARTUUID`).
    """
    if not root_partuuid or not esp_partuuid:
        raise ConfigError(
            ERR_MISSING_PARTUUID,
            "fstab: both PARTUUIDs must be non-empty "
            f"(root={root_partuuid!r}, esp={esp_partuuid!r})",
        )
    return (
        "# /etc/fstab - generated by pengyos-installer (do not edit by hand)\n"
        f"PARTUUID={root_partuuid} / ext4 defaults 0 1\n"
        f"PARTUUID={esp_partuuid} {ESP_MOUNTPOINT} vfat {ESP_MOUNT_OPTIONS} 0 1\n"
    )


def render_hostname(hostname: str) -> str:
    """The exact content of the installed ``/etc/hostname`` (spec.md,
    step 5): the hostname on a single line with one trailing newline.

    Surrounding whitespace is stripped; an empty/whitespace-only hostname
    is a hard error (:data:`ERR_INVALID_HOSTNAME`).
    """
    name = hostname.strip()
    if not name:
        raise ConfigError(
            ERR_INVALID_HOSTNAME,
            f"hostname: {hostname!r} is not a usable hostname "
            "(need --hostname H, or the live system's hostname at install time)",
        )
    return f"{name}\n"


def render_installed_marker(hostname: str, target: str) -> str:
    """The content of the install marker ``/etc/pengyos/installed``
    (spec.md, step 5: "drop /etc/pengyos/installed").

    Deterministic, golden-stable; presence of the file is what marks the
    system as *installed* (as opposed to the live stick).
    """
    name = hostname.strip()
    if not name:
        raise ConfigError(
            ERR_INVALID_HOSTNAME,
            f"hostname: {hostname!r} is not a usable hostname",
        )
    return (
        "installed-by: pengyos-installer\n"
        f"hostname: {name}\n"
        f"target: {target}\n"
    )


def render_grub_cfg(root_partuuid: str, timeout: int = GRUB_TIMEOUT) -> str:
    """The content of the fallback (removable) GRUB config written to the
    ESP (spec.md, step 6: ``EFI/BOOT/grub.cfg`` with
    ``root=PARTUUID=<root>``).

    Deterministic and golden-stable: it locates the root partition by
    PARTUUID and boots the PengyOS kernel with the same identity. A
    missing/empty root PARTUUID is a hard error
    (:data:`ERR_MISSING_PARTUUID`).
    """
    if not root_partuuid:
        raise ConfigError(
            ERR_MISSING_PARTUUID,
            f"grub.cfg: the root PARTUUID is missing ({root_partuuid!r})",
        )
    return (
        "# generated by pengyos-installer (fallback EFI boot path, EFI/BOOT)\n"
        "set default=0\n"
        f"set timeout={timeout}\n"
        "\n"
        f"search --no-floppy --set=root --partuuid {root_partuuid}\n"
        "menuentry 'PengyOS' {\n"
        f"    linux /vmlinuz root=PARTUUID={root_partuuid} ro quiet\n"
        "    initrd /initrd.img\n"
        "}\n"
    )


def configure_file_steps(
    hostname: "str | None", target: str
) -> Tuple[Step, ...]:
    """The *write* steps of the configure phase (spec.md, step 5 + the
    fallback grub.cfg of step 6).

    Four internal steps (``argv=None``, stable ``internal:`` notes), in
    order: ``/etc/fstab`` (from the blkid output), ``/etc/hostname`` (the
    ``--hostname`` value if given, else the live system's hostname at
    install time), the ``/etc/pengyos/installed`` marker, and the ESP
    fallback GRUB config (written after the ESP is mounted, in the
    bootloader phase). Their content is produced at install time by
    :func:`render_fstab`, :func:`render_hostname`,
    :func:`render_installed_marker`, and :func:`render_grub_cfg` — the
    install-execution task owns the file writes.
    """
    host_desc = hostname if hostname is not None else "the live system's hostname"
    return (
        Step(
            phase="configure",
            description=f"write {ETC_FSTAB} (PARTUUID= lines for root and ESP, from the blkid output)",
            argv=None,
            note=(
                "internal: written at install time with render_fstab() from the "
                "two blkid steps above"
            ),
        ),
        Step(
            phase="configure",
            description=f"write {ETC_HOSTNAME} ({host_desc})",
            argv=None,
            note="internal: written at install time with render_hostname() (one line)",
        ),
        Step(
            phase="configure",
            description=f"write the install marker {INSTALL_MARKER}",
            argv=None,
            note="internal: written at install time with render_installed_marker()",
        ),
        Step(
            phase="configure",
            description=(
                f"write the fallback GRUB config {ESP_MOUNTPOINT}/{FALLBACK_GRUB_CFG} "
                f"on the ESP (root=PARTUUID=<root-partuuid>)"
            ),
            argv=None,
            note=(
                "internal: written at install time with render_grub_cfg() after the "
                "ESP is mounted (bootloader phase)"
            ),
        ),
    )


# --- bootloader phase -------------------------------------------------------

#: Bootloader-phase constants (spec.md, step 6). Stable; golden-tested.
#: The GRUB target arch for the installed x86_64 EFI system.
GRUB_TARGET = "x86_64-efi"
#: The bootloader id for the removable (fallback) EFI boot path. This is the
#: id ``grub-install --bootloader-id`` writes under ``EFI/<id>/`` and the
#: label the ``efibootmgr`` entry carries. Mirrors ``cli.BOOTLOADER_ID``.
BOOTLOADER_ID = "PengyOS"
#: Where the ESP is mounted, under the work dir (spec.md step 6: "mount the
#: ESP at <work>/boot/efi"). This is GRUB's ``--efi-directory``.
ESP_EFI_DIR = WORK_DIR + "/boot/efi"
#: GRUB's ``--boot-directory`` (the installed ``/boot``), under the work dir.
GRUB_BOOT_DIR = WORK_DIR + "/boot"
#: The loader path ``grub-install --removable`` installs. ``--removable`` puts
#: the loader in the standard, firmware-independent ``EFI/BOOT`` location, so
#: the system boots even with no NVRAM entry — which is what makes the
#: ``efibootmgr`` step optional.
EFI_LOADER_PATH = "/EFI/BOOT/bootx64.efi"


def bootloader_steps(target: str) -> Tuple[Step, ...]:
    """The steps of the *bootloader* phase (spec.md, step 6).

    Three steps, in order:

    * mount the ESP partition (partition 1) at :data:`ESP_EFI_DIR`;
    * ``grub-install --target=x86_64-efi --efi-directory=<work>/boot/efi
      --boot-directory=<work>/boot --bootloader-id=PengyOS --removable``;
    * an **optional** ``efibootmgr`` entry pointing at the removable loader
      (``EFI/BOOT/bootx64.efi``). Because ``--removable`` installs the loader
      in the firmware-independent ``EFI/BOOT`` location, the system already
      boots without a UEFI NVRAM entry — the ``efibootmgr`` step is a
      best-effort convenience (an explicit, named entry) and a failure to
      create it must not abort the install. That is why the step carries an
      ``optional:`` note rather than a ``pending:`` one: it is a real command
      whose failure is tolerated, not a placeholder for a later task.

    The ``EFI/BOOT/grub.cfg`` fallback config itself is an internal write step
    planned in the *configure* phase (see :func:`configure_file_steps`); it is
    written once the ESP is mounted — by this first step. Pure; the argv is
    golden-stable.
    """
    esp_part = partition_device(target, 1)
    return (
        Step(
            phase="bootloader",
            description=f"mount: mount the ESP partition {esp_part} at {ESP_EFI_DIR}",
            argv=("mount", esp_part, ESP_EFI_DIR),
        ),
        Step(
            phase="bootloader",
            description=(
                f"grub-install: install GRUB for {GRUB_TARGET} with "
                f"--bootloader-id={BOOTLOADER_ID} --removable"
            ),
            argv=(
                "grub-install",
                f"--target={GRUB_TARGET}",
                f"--efi-directory={ESP_EFI_DIR}",
                f"--boot-directory={GRUB_BOOT_DIR}",
                f"--bootloader-id={BOOTLOADER_ID}",
                "--removable",
            ),
        ),
        Step(
            phase="bootloader",
            description=(
                "efibootmgr: create the firmware boot entry pointing at the "
                "removable loader (optional; the fallback path boots without it)"
            ),
            argv=(
                "efibootmgr",
                "-c",
                "-d", target,
                "-p", "1",
                "-L", BOOTLOADER_ID,
                "-l", EFI_LOADER_PATH,
            ),
            note=(
                "optional: best-effort NVRAM entry; a failure here does not "
                "abort the install (the --removable fallback path already boots)"
            ),
        ),
    )


def _build_steps(
    *,
    probe: TargetProbe,
    esp_size: str,
    hostname: "str | None",
    rootfs_src: "str | None",
) -> Tuple[Step, ...]:
    """Compose the full ordered step list.

    Every phase from :data:`PHASES` is present, in order, with a stable
    description. The ``sanity`` step records the checks already performed.
    The partition, format, copy, configure, and bootloader steps carry their
    full ``parted`` / ``mkfs.*`` / ``mount`` / ``rsync`` / ``tar`` / ``blkid``
    / ``grub-install`` / ``efibootmgr`` argv (:func:`partition_steps`,
    :func:`format_steps`, :func:`copy_steps`, :func:`partuuid_steps`,
    :func:`bootloader_steps`); the configure phase's file-write steps are
    internal (content is produced at install time by the ``render_*``
    functions, see :func:`configure_file_steps`). The one exception to
    "argv on every external step" is the final, ``optional:`` ``efibootmgr``
    step in the bootloader phase: it is a real command whose failure is
    tolerated (see :func:`bootloader_steps`), not a placeholder.
    """
    steps: list[Step] = [
        Step(
            phase="sanity",
            description=(
                f"Verify target {probe.path} is a block device, is not the "
                "live root/ESP device, and is at least 4 GiB"
            ),
            argv=None,
            note="internal: performed by check_target() before any step runs",
        ),
        *partition_steps(probe.path, esp_size),
        *format_steps(probe.path),
        *copy_steps(probe.path, rootfs_src),
        *partuuid_steps(probe.path),
        *configure_file_steps(hostname, probe.path),
        *bootloader_steps(probe.path),
        Step(
            phase="teardown",
            description="sync, unmount ESP then root, print a summary",
            argv=None,
            note="internal: performed during install execution",
        ),
    ]
    return tuple(steps)


def build_plan(
    *,
    target: str,
    esp_size: str,
    hostname: "str | None" = None,
    rootfs_src: "str | None" = None,
    probe: "TargetProbe | None" = None,
    min_target_size_bytes: int = MIN_TARGET_SIZE_BYTES,
) -> InstallPlan:
    """Compute the ordered install plan for ``target`` (pure).

    ``probe`` supplies the target/live-device facts; when omitted it is
    collected via :func:`collect_target_probe`. The returned plan carries the
    sanity result and the full ordered step list, ready to render or execute.
    """
    if probe is None:
        probe = collect_target_probe(target)
    sanity = check_target(probe, min_target_size_bytes)
    steps = _build_steps(
        probe=probe, esp_size=esp_size, hostname=hostname, rootfs_src=rootfs_src
    )
    return InstallPlan(
        target=target,
        esp_size=esp_size,
        hostname=hostname,
        rootfs_src=rootfs_src,
        min_target_size_bytes=min_target_size_bytes,
        sanity=sanity,
        steps=steps,
    )


# --- live mount discovery (parsing /proc/mounts) ----------------------------

#: The path of the kernel's mount table — the single source of truth for what
#: is mounted on the running system. Overridable for tests via the
#: ``read_mounts`` seam (see :func:`collect_target_probe`).
PROC_MOUNTS_PATH = "/proc/mounts"

#: Mountpoints (compared case-insensitively) recognised as the EFI System
#: Partition. The ESP is a FAT/vfat partition; on the PengyOS live stick (and
#: most distros) it is mounted at one of these well-known locations. A FAT
#: *data* partition mounted somewhere else (e.g. a USB drive) is deliberately
#: **not** treated as the ESP — a false ESP would spuriously refuse valid
#: targets on that disk.
ESP_MOUNTPOINTS = ("/boot/efi", "/efi")

#: fstype names (compared case-insensitively) that identify a FAT-formatted
#: EFI System Partition.
FAT_FSTYPES = ("vfat", "msdos", "fat", "fat32")


def _unescape_mount_field(field: str) -> str:
    """Decode one ``/proc/mounts`` field.

    The kernel escapes whitespace in mountpoints with octal sequences:
    ``\\040`` is a space, ``\\011`` a tab, ``\\012`` a newline, and ``\\\\`` a
    literal backslash. The two-phase form below (replace to private sentinels
    first, then to the real characters) is immune to a field that itself
    contains an escaped backslash.
    """
    return (
        field.replace("\\\\", "\x00")
        .replace("\\040", "\x01")
        .replace("\\011", "\x02")
        .replace("\\012", "\x03")
        .replace("\x00", "\\")
        .replace("\x01", " ")
        .replace("\x02", "\t")
        .replace("\x03", "\n")
    )


def _is_device_path(device: str) -> bool:
    """True when a mount *device* is a real block-device path (``/dev/...``).

    Pseudo-filesystems name their "device" by type (``proc``, ``sysfs``,
    ``cgroup2``, ``tmpfs``, ``overlay``) or ``none``; none of those lives on a
    block device, so only ``/dev/`` paths can be the live root/ESP device.
    """
    return device.startswith("/dev/")


@dataclass(frozen=True)
class MountEntry:
    """One parsed line of ``/proc/mounts`` (the first four fields)."""

    device: str
    mountpoint: str
    fstype: str
    options: str


def parse_mounts(mounts_text: str) -> Tuple[MountEntry, ...]:
    """Parse the contents of ``/proc/mounts`` into :class:`MountEntry` rows.

    Each non-blank line is whitespace-delimited: ``device mountpoint fstype
    options dump pass``. Whitespace *inside* the device/mountpoint is escaped
    octally by the kernel (``\\040`` = a space), so a plain whitespace split
    recovers the fields and :func:`_unescape_mount_field` restores the embedded
    characters. Blank lines and lines that are not in the (at least) four-field
    form are skipped rather than raised on — a partially-readable mount table
    should degrade to "unknown", never crash the installer.
    """
    entries: list[MountEntry] = []
    for line in mounts_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        entries.append(
            MountEntry(
                device=_unescape_mount_field(parts[0]),
                mountpoint=_unescape_mount_field(parts[1]),
                fstype=parts[2],
                options=parts[3],
            )
        )
    return tuple(entries)


def live_mount_devices(
    entries: Sequence[MountEntry]
) -> Tuple[Optional[str], Optional[str]]:
    """Find the block devices backing the live ``/`` and the ESP.

    Scans the parsed :class:`MountEntry` rows and returns ``(root_device,
    esp_device)``:

    * ``root_device`` is the device of the mount whose mountpoint is ``/`` and
      whose device is a real block device (``/dev/...``);
    * ``esp_device`` is the device of the first FAT-formatted mount (see
      :data:`FAT_FSTYPES`) at one of :data:`ESP_MOUNTPOINTS` (case-insensitive)
      on a block device.

    Either (or both) is ``None`` when no such mount is present — e.g. an
    overlay ``/`` (a chroot/container) has no block-device root, and a system
    whose ESP is unmounted has no ESP entry. The caller
    (:func:`collect_target_probe`) feeds the result into
    :class:`TargetProbe`, and :func:`check_target` then refuses any target that
    overlaps either device.
    """
    root_device: Optional[str] = None
    esp_device: Optional[str] = None
    for entry in entries:
        if not _is_device_path(entry.device):
            continue
        if entry.mountpoint == "/" and root_device is None:
            root_device = entry.device
        if (
            esp_device is None
            and entry.fstype.lower() in FAT_FSTYPES
            and entry.mountpoint.lower() in ESP_MOUNTPOINTS
        ):
            esp_device = entry.device
    return root_device, esp_device


# --- default probe collector (still read-only) ------------------------------

_ProbeCollector = Callable[[str], TargetProbe]


def _read_sys_size_bytes(name: str) -> int:
    """Size in bytes from ``/sys/block/<name>/size`` (sector count × 512)."""
    try:
        with open(f"/sys/block/{name}/size", "r", encoding="ascii") as fh:
            sectors = int(fh.read().strip())
    except (OSError, ValueError):
        return 0
    return sectors * 512


def _read_mounts() -> str:
    """The default mount reader: the text of :data:`PROC_MOUNTS_PATH`."""
    with open(PROC_MOUNTS_PATH, "r", encoding="ascii", errors="replace") as fh:
        return fh.read()


def collect_target_probe(
    path: str,
    read_mounts: "Callable[[], str] | None" = None,
) -> TargetProbe:
    """Build a :class:`TargetProbe` for ``path`` using only ``os.stat``,
    ``/sys``, and the (injectable) mount table. Read-only: it stats the path,
    reads small sysfs files, and reads :data:`PROC_MOUNTS_PATH` — it never
    writes.

    The live root and ESP block devices are discovered from the mount table
    (via :func:`parse_mounts` + :func:`live_mount_devices`) and populated into
    the probe, so :func:`check_target` can actually refuse installing over the
    running system (see the spec's *Live-device refusal*). ``read_mounts`` is
    the injection seam: tests pass a fixture string and never touch the real
    ``/proc/mounts``. It defaults to :func:`_read_mounts`, resolved at *call*
    time (so the module attribute is a monkeypatchable seam, not a value
    frozen at ``def`` time). If the mount table cannot be read the discovery
    degrades gracefully (both devices stay ``None``) — the other sanity checks
    (block-device, size) still apply and a fully-populated probe can be
    injected via the ``probe=`` argument of :func:`build_plan`.
    """
    reader = _read_mounts if read_mounts is None else read_mounts
    name = os.path.basename(path.rstrip("/"))
    is_block = False
    try:
        st = os.stat(path)
        import stat as _stat

        is_block = _stat.S_ISBLK(st.st_mode)
    except OSError:
        is_block = False
    if is_block:
        try:
            os.stat(f"/sys/block/{name}")
        except OSError:
            is_block = False
    size_bytes = _read_sys_size_bytes(name) if name else 0
    try:
        root_device, esp_device = live_mount_devices(parse_mounts(reader()))
    except OSError:
        root_device, esp_device = None, None
    return TargetProbe(
        path=path,
        is_block_device=is_block,
        size_bytes=size_bytes,
        root_device=root_device,
        esp_device=esp_device,
    )
