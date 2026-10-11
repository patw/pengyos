"""Install PengyOS from the live medium onto a disk, in the partitioned layout.

The medium's *payload* (built by the board's ``post-image.sh``) carries the
release's artifacts, zstd-compressed, with a ``manifest.json`` of their hashes:
the root filesystem (``rootfs``) and a ready ESP (``esp``: GRUB + the slot-a
kernel). Installing lays them onto the disk in the layout of
``docs/state-layout.md``::

    1  ESP     vfat  256 MiB   PENGYOS-ESP      GRUB, grub.cfg, grubenv, slot-*/bzImage
    2  root-a  ext4    8 GiB   pengyos-root-a   the system image, booted read-only
    3  root-b  ext4    8 GiB   pengyos-root-b   empty until the first update
    4  data    ext4    rest    pengyos-data     all of this machine's state

Phases (reported as ``(phase, fraction, message)``)::

    verify     sha256 each compressed payload file against the manifest
    partition  new GPT with the four partitions (fresh GUIDs by construction)
    write      stream-decompress the ESP and the rootfs into place, hashing as it goes
    root       fsck + grow the rootfs to its slot, fresh UUID + label
    boot       grub.cfg for this disk's partitions + grubenv (slot a)
    provision  format data (btrfs, a subvolume per area), seed /var and /root from the image, then — through the
               same /etc overlay + binds the booted system uses — hostname, the
               first user (admin + seat groups; the desktop auto-logs-in as
               them), passwords (random salts), the live session's network
               profiles; no SSH host keys
    done       unmount, sync

Only build-output bytes reach the root slot and the ESP, hash-checked on the way:
"install = write the release artifact", never a copy of the live system. The
data partition is the machine; an update rewrites a root slot and never touches
it. The disk-level work runs as small argv steps through an injectable ``run``
so the sequencing is unit-testable; only the streaming writes touch devices
directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

__all__ = [
    "Disk",
    "ImageInstallError",
    "Manifest",
    "ProvisionAnswers",
    "boot_disks",
    "find_payload",
    "install_image",
    "list_disks",
    "partition_path",
    "render_grub_cfg",
]

#: Filesystem label of the payload partition on the install medium.
PAYLOAD_LABEL = "PENGYOS-PAYLOAD"
#: Where the live session mounts the payload partition.
PAYLOAD_MOUNT = "/run/pengyos/payload"
PAYLOAD_DEVICE = "/dev/disk/by-label/" + PAYLOAD_LABEL
#: Scratch mount points for the target during provisioning.
TARGET_ROOT = "/run/pengyos/target-root"
TARGET_ESP = "/run/pengyos/target-esp"
#: UEFI variables (mounted by sys-firmware-efi-efivars.mount); absent on BIOS boots.
EFIVARS = "/sys/firmware/efi/efivars"
#: Where the live session's NetworkManager keeps the profiles people created
#: (the image ships none), carried over to the installed system.
LIVE_NM_CONNECTIONS = "/etc/NetworkManager/system-connections"
#: Groups that make the created user the desktop's owner: admin (wheel: polkit +
#: sudo's pam_wheel; sudo: the sudoers group rule), the seat (seatd -g seat), and
#: devices. Only those the image defines are used.
# pipewire: the realtime-scheduling limits PipeWire's packages grant (@pipewire
# rtprio/memlock); without them it logged "RTKit error" and ran unprioritised.
# gamemode: GameMode's polkit rule lets only this group change the CPU governor
# and GPU clocks (its actions are allow_active=no otherwise), and its @gamemode
# nice limit; without it GameMode did nothing.
# The image's /usr/lib/pengyos/owner-groups is the same list (merge-accounts adds
# existing owners to groups appended later); tests/test_owner_groups.py keeps
# the two equal.
USER_GROUPS = (
    "wheel", "sudo", "seat", "video", "audio", "input", "render", "pipewire", "gamemode",
)

#: The data partition: btrfs, one subvolume per area (preinit mounts the same way).
DATA_MOUNT_OPTS = "noatime,compress=zstd:1"
DATA_SUBVOLUMES = ("etc", "var", "home", "root")
#: The installed user's login shell: the first of these the image has.
USER_SHELLS = ("/usr/bin/zsh", "/bin/zsh", "/bin/bash", "/bin/sh")

#: The layout (docs/state-layout.md).
ESP_SIZE = 256 * 1024**2
# 8 GiB, not 4: the NVIDIA Desktop image already used 3.1 GiB (2026-09-30),
# and each slot must hold a whole release.
SLOT_SIZE = 8 * 1024**3
MIN_DATA = 2 * 1024**3
MIN_DISK = ESP_SIZE + 2 * SLOT_SIZE + MIN_DATA
#: GPT type for an x86-64 root partition (discoverable-partitions spec).
ROOT_TYPE = "4F68BCE3-E8CD-4DB1-96E7-FBCAF984B709"
#: Kernel command line of an installed system (see /usr/lib/pengyos/preinit).
#: audit=0: nothing here reads kernel audit records (no auditd), and without a
#: reader the kernel prints each one (every PAM login) over the console.
#: splash: show the Plymouth boot splash (Desktop; inert where Plymouth is not
#: installed). plymouth.ignore-serial-consoles: where there is a serial console
#: it comes last, so it is /dev/console, and Plymouth would otherwise fall back
#: to text output. The serial console itself is not here: grub.cfg adds it only
#: when GRUB finds a real UART (see render_grub_cfg).
KERNEL_ARGS = ("ro rootwait init=/usr/lib/pengyos/preinit pengyos.data=4 audit=0 "
               "splash plymouth.ignore-serial-consoles console=tty0")

#: The GRUB variable render_grub_cfg ends every kernel line with: the serial
#: console, or nothing when the machine has no working serial port.
SERIAL_CONSOLE_VAR = "${pengyos_serial}"
_SERIAL_ARGS = (SERIAL_CONSOLE_VAR, "console=ttyS0,115200")

_CHUNK = 4 * 1024 * 1024
_SALT_CHARS = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_HOSTNAME_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
_SSH_KEY_RE = re.compile(r"^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-\S+|sk-\S+) [A-Za-z0-9+/=]+( .*)?$")
# Block devices that are never install targets.
_SKIP_PREFIXES = ("loop", "ram", "zram", "sr", "fd", "dm-", "md", "nbd")


class ImageInstallError(Exception):
    """A user-facing install failure (the message is shown as-is)."""


# --------------------------------------------------------------------------- #
# Payload
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PayloadFile:
    """One compressed artifact in the payload."""

    file: str  # name relative to the manifest
    sha256: str  # of the compressed file
    raw_size: int
    raw_sha256: str


@dataclass(frozen=True)
class Manifest:
    """``manifest.json`` on the payload partition (format ``pengyos-layout-1``)."""

    edition: str
    version: str
    rootfs: PayloadFile
    esp: PayloadFile

    FORMAT = "pengyos-layout-1"

    @classmethod
    def load(cls, payload_dir: str | os.PathLike) -> "Manifest":
        path = Path(payload_dir) / "manifest.json"
        try:
            data = json.loads(path.read_text())
            if data.get("format") != cls.FORMAT:
                raise ValueError(f"format {data.get('format')!r}, expected {cls.FORMAT!r}")
            files = {}
            for key in ("rootfs", "esp"):
                f = data["files"][key]
                files[key] = PayloadFile(
                    file=str(f["file"]),
                    sha256=str(f["sha256"]).lower(),
                    raw_size=int(f["raw_size"]),
                    raw_sha256=str(f["raw_sha256"]).lower(),
                )
                if "/" in files[key].file or files[key].file.startswith("."):
                    raise ValueError(f"unsafe file name {files[key].file!r}")
            m = cls(edition=str(data["edition"]), version=str(data["version"]), **files)
        except FileNotFoundError:
            raise ImageInstallError(f"no install payload manifest at {path}") from None
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ImageInstallError(f"malformed payload manifest {path}: {exc}") from None
        if m.rootfs.raw_size > SLOT_SIZE or m.esp.raw_size > ESP_SIZE:
            raise ImageInstallError("the payload does not fit the partition layout")
        return m


def find_payload(explicit: str | None = None, run: Runner | None = None) -> Path:
    """The payload directory: *explicit*, else the live session's mount —
    mounting the stick's payload partition there first if nothing has yet (the
    Desktop mounts it when its GUI starts; an SSH user can get there first)."""
    path = Path(explicit or PAYLOAD_MOUNT)
    device = Path(PAYLOAD_DEVICE)
    if explicit is None and not (path / "manifest.json").is_file() and device.exists():
        path.mkdir(parents=True, exist_ok=True)
        (run or _default_run)(["mount", "-o", "ro", str(device), str(path)], check=False)
    if not (path / "manifest.json").is_file():
        raise ImageInstallError(
            f"no install payload found at {path} — this does not look like a PengyOS "
            "install medium (expected a partition labelled " + PAYLOAD_LABEL + ")"
        )
    return path


# --------------------------------------------------------------------------- #
# Disks
# --------------------------------------------------------------------------- #
@dataclass
class Disk:
    """A candidate whole-disk install target."""

    name: str
    path: str
    size_bytes: int
    model: str = ""
    removable: bool = False
    is_boot: bool = False
    mounted: list[str] = field(default_factory=list)

    @property
    def size_label(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1000 or unit == "TB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1000
        return f"{self.size_bytes} B"  # pragma: no cover

    def to_dict(self) -> dict:
        d = asdict(self)
        d["size_label"] = self.size_label
        return d


def partition_path(disk: str, number: int) -> str:
    """``/dev/sda`` + 2 -> ``/dev/sda2``; ``/dev/nvme0n1`` + 2 -> ``/dev/nvme0n1p2``."""
    return f"{disk}p{number}" if disk[-1].isdigit() else f"{disk}{number}"


def _read(path: Path, default: str = "") -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return default


def _mount_sources(mounts_file: Path) -> list[tuple[str, str]]:
    out = []
    for line in _read(mounts_file).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out.append((parts[0], parts[1].replace("\\040", " ")))
    return out


def _dev_numbers_of_mount(point: str, mountinfo: Path) -> str | None:
    """``major:minor`` of the filesystem mounted at *point* (from mountinfo)."""
    for line in _read(mountinfo).splitlines():
        parts = line.split()
        if len(parts) > 4 and parts[4] == point:
            return parts[2]
    return None


def _disk_of_devnum(devnum: str, sys_root: Path) -> str | None:
    """Whole-disk kernel name holding the block device ``major:minor``."""
    link = sys_root / "dev" / "block" / devnum
    try:
        target = os.path.realpath(link)
    except OSError:
        return None
    p = Path(target)
    if not p.exists():
        return None
    # A partition's sysfs dir has a ``partition`` file; its parent is the disk.
    return p.parent.name if (p / "partition").exists() else p.name


def boot_disks(
    *,
    sys_root: str | os.PathLike = "/sys",
    mountinfo: str | os.PathLike = "/proc/self/mountinfo",
    extra_mounts: Iterable[str] = ("/", PAYLOAD_MOUNT),
) -> set[str]:
    """Kernel names of the disk(s) the running system (and payload) live on."""
    sys_root, mountinfo = Path(sys_root), Path(mountinfo)
    names = set()
    for point in extra_mounts:
        devnum = _dev_numbers_of_mount(point, mountinfo)
        if devnum:
            disk = _disk_of_devnum(devnum, sys_root)
            if disk:
                names.add(disk)
    return names


def list_disks(
    *,
    sys_root: str | os.PathLike = "/sys",
    dev_root: str = "/dev",
    mounts: str | os.PathLike = "/proc/mounts",
    mountinfo: str | os.PathLike = "/proc/self/mountinfo",
) -> list[Disk]:
    """Every whole disk that could be an install target (boot disk flagged)."""
    sys_root = Path(sys_root)
    boot = boot_disks(sys_root=sys_root, mountinfo=mountinfo)
    mount_list = _mount_sources(Path(mounts))
    disks = []
    block = sys_root / "block"
    for entry in sorted(block.iterdir() if block.is_dir() else []):
        name = entry.name
        if name.startswith(_SKIP_PREFIXES):
            continue
        sectors = _read(entry / "size", "0")
        size = int(sectors) * 512 if sectors.isdigit() else 0
        if size == 0:
            continue  # empty card reader / ejected medium
        path = f"{dev_root}/{name}"
        part_re = re.compile(re.escape(path) + (r"p\d+" if name[-1].isdigit() else r"\d+"))
        mounted = sorted(
            point for src, point in mount_list if src == path or part_re.fullmatch(src)
        )
        disks.append(
            Disk(
                name=name,
                path=path,
                size_bytes=size,
                model=" ".join(
                    s for s in (
                        _read(entry / "device" / "vendor"),
                        _read(entry / "device" / "model"),
                    ) if s and not re.fullmatch(r"0x[0-9a-fA-F]+", s)  # virtio: PCI ids
                ),
                removable=_read(entry / "removable") == "1",
                is_boot=name in boot,
                mounted=mounted,
            )
        )
    return disks


# --------------------------------------------------------------------------- #
# Provisioning answers
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProvisionAnswers:
    hostname: str
    username: str
    password: str
    #: SSH public keys (authorized_keys lines) for the user. The Server's sshd is
    #: key-only, so without one the user can log in only at the console.
    authorized_keys: str = ""
    #: Desktop only: start straight into this user's desktop at boot instead of
    #: the login screen (opt-in, like Ubuntu's "Log in automatically").
    autologin: bool = False

    def validate(self) -> None:
        if not _HOSTNAME_RE.match(self.hostname):
            raise ImageInstallError(
                f"invalid hostname {self.hostname!r}: letters, digits and '-' only (max 63)"
            )
        if not _USERNAME_RE.match(self.username) or self.username == "root":
            raise ImageInstallError(
                f"invalid username {self.username!r}: lowercase letters, digits, '_' or '-', "
                "starting with a letter"
            )
        if len(self.password) < 6:
            raise ImageInstallError("the password must be at least 6 characters")
        if any(c in self.password for c in ":\n\r"):
            raise ImageInstallError("the password cannot contain ':' or line breaks")
        for line in self.authorized_keys.splitlines():
            if line.strip() and not _SSH_KEY_RE.match(line.strip()):
                raise ImageInstallError(
                    f"not an SSH public key: {line.strip()[:40]!r} "
                    "(expected e.g. 'ssh-ed25519 AAAA… you@host')"
                )


# --------------------------------------------------------------------------- #
# The install
# --------------------------------------------------------------------------- #
#: Share of the whole install each phase takes (for one overall progress bar;
#: the GUI uses the same split).
PHASE_SPAN = {
    "verify": (0.0, 0.10), "partition": (0.10, 0.02), "write": (0.12, 0.73),
    "root": (0.85, 0.05), "boot": (0.90, 0.03), "provision": (0.93, 0.07),
    "done": (1.0, 0.0),
}


def overall_fraction(phase: str, fraction: float) -> float:
    """Map a phase and its own 0..1 progress onto the whole install's 0..1."""
    start, span = PHASE_SPAN.get(phase, (0.0, 0.0))
    return start + span * min(max(fraction, 0.0), 1.0)


Runner = Callable[..., subprocess.CompletedProcess]
Report = Callable[[str, float, str], None]


def _default_run(argv, *, input=None, check=True):
    return subprocess.run(
        argv, input=input, capture_output=True, text=True, check=check
    )


def _run(run: Runner, argv: list[str], what: str, *, input=None, ok=(0,)):
    result = run(argv, input=input, check=False)
    if result.returncode not in ok:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise ImageInstallError(
            f"{what} failed ({' '.join(argv[:2])}: exit {result.returncode})"
            + (f": {detail[-1]}" if detail else "")
        )
    return result


def _sgdisk_field(info: str, label: str) -> str:
    m = re.search(rf"^{re.escape(label)}:\s*(\S+)", info, re.MULTILINE)
    if not m:
        raise ImageInstallError(f"could not read '{label}' from sgdisk")
    return m.group(1)


def render_grub_cfg(slots: dict[str, str], kernel_args: str = KERNEL_ARGS) -> str:
    """The installed disk's grub.cfg: one entry per root slot (``{"a": partuuid}``).

    grubenv picks the entry: ``pengyos_slot`` is the committed slot (a when
    unset); ``pengyos_try`` names a slot to boot ONCE (pengyos-update sets it
    after writing a new image there). GRUB clears ``pengyos_try`` before booting
    it, so a new image that never comes up falls back to the committed slot on
    the next boot; once it is up, ``pengyos-update confirm`` commits it.
    pengyos-update rewrites this file with the same function.

    The serial console (``console=ttyS0,115200``) is added only when GRUB's
    serial driver finds a real UART at COM1: a phantom port there made a
    ThinkPad L14 Gen 1 AMD boot for minutes behind a black screen (2026-10-03).
    Off QEMU (SMBIOS vendor), it comes with ``loglevel=4``: at 115200 baud every
    kernel message costs ~6 ms, ~5 s of black screen per boot (2026-10-04).
    ``kernel_args`` may carry an older grub.cfg's fixed serial console or the
    variable itself; both are dropped before the variable is appended."""
    kernel_args = " ".join(a for a in kernel_args.split() if a not in _SERIAL_ARGS)
    out = [
        "# PengyOS — written by pengyos-install / pengyos-update for this disk.",
        "# grubenv (grub-editenv EFI/BOOT/grubenv): pengyos_slot=a|b is the committed",
        "# root slot; pengyos_try=a|b boots that slot once (a new update), then falls back.",
        "# Hidden menu: boot after 1 s; hold Esc (or Shift) for the slot menu.",
        'set timeout_style="hidden"',
        'set timeout="1"',
        "insmod all_video",
        "insmod gfxterm",
        "set gfxmode=auto",
        "set gfxpayload=keep",
        "if loadfont /EFI/BOOT/grub-pengyos.pf2; then",
        "    set gfxterm_font=PengyOS24",
        "    terminal_output gfxterm",
        "fi",
        "load_env",
        'set default="pengyos-a"',
        'if [ "${pengyos_slot}" = "b" ]; then set default="pengyos-b"; fi',
        'if [ -n "${pengyos_try}" ]; then',
        '    set default="pengyos-${pengyos_try}"',
        '    set pengyos_try=""',
        "    save_env pengyos_try",
        "fi",
        "# A serial console only where GRUB finds a real UART at COM1; on real",
        "# hardware (not QEMU) with loglevel=4, so kernel messages don't crawl out of it.",
        'set pengyos_serial=""',
        "if serial --unit=0 --speed=115200; then",
        '    set pengyos_serial="console=ttyS0,115200"',
        "    smbios --type 1 --get-string 4 --set pengyos_vendor",
        '    if [ "${pengyos_vendor}" != "QEMU" ]; then',
        '        set pengyos_serial="loglevel=4 console=ttyS0,115200"',
        "    fi",
        "fi",
        "",
    ]
    for slot in sorted(slots):
        out += [
            f'menuentry "PengyOS (slot {slot})" --id pengyos-{slot} {{',
            f"    linux /slot-{slot}/bzImage root=PARTUUID={slots[slot].lower()} "
            f"{kernel_args} {SERIAL_CONSOLE_VAR}",
            "}",
        ]
    return "\n".join(out) + "\n"


def _verify_payload(payload: Path, manifest: Manifest, report: Report) -> None:
    files = (manifest.esp, manifest.rootfs)
    try:
        total = sum((payload / f.file).stat().st_size for f in files)
    except OSError as exc:
        raise ImageInstallError(f"payload file missing: {exc.filename}") from None
    done = 0
    for f in files:
        digest = hashlib.sha256()
        with open(payload / f.file, "rb") as fh:
            while chunk := fh.read(_CHUNK):
                digest.update(chunk)
                done += len(chunk)
                report("verify", done / total, "Checking the installer image")
        if digest.hexdigest() != f.sha256:
            raise ImageInstallError(
                "the installer image is corrupt (checksum mismatch) — re-flash the USB stick"
            )


def _stream_write(source: Path, target: str, f: PayloadFile, progress: Callable[[int], None]) -> None:
    proc = subprocess.Popen(
        ["zstd", "-dc", "--no-progress", str(source)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    digest, done = hashlib.sha256(), 0
    fd = os.open(target, os.O_WRONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        assert proc.stdout is not None
        while chunk := proc.stdout.read(_CHUNK):
            view = memoryview(chunk)
            while view:
                view = view[os.write(fd, view):]
            digest.update(chunk)
            done += len(chunk)
            progress(len(chunk))
        os.fsync(fd)
    finally:
        os.close(fd)
        proc.wait()
    if proc.returncode != 0:
        err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
        raise ImageInstallError(f"decompressing the installer image failed: {err}")
    if done != f.raw_size or digest.hexdigest() != f.raw_sha256:
        raise ImageInstallError(
            "the data written does not match the installer image "
            "(checksum mismatch after decompression) — the disk is NOT bootable"
        )


def _wait_for(path: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        if time.monotonic() > deadline:
            raise ImageInstallError(f"{path} did not appear after partitioning")
        time.sleep(0.2)


def install_image(
    target: str,
    answers: ProvisionAnswers,
    *,
    payload_dir: str | None = None,
    confirm: bool = False,
    report: Report | None = None,
    run: Runner = _default_run,
    disks: list[Disk] | None = None,
    nm_connections: str = LIVE_NM_CONNECTIONS,
) -> None:
    """Install the payload onto *target* in the partitioned layout, and provision it.

    Refuses without *confirm*, refuses the disk the live system runs from, a disk
    with mounted filesystems, and a disk smaller than :data:`MIN_DISK`.
    """
    report = report or (lambda phase, frac, msg: None)
    if not confirm:
        raise ImageInstallError("refusing to erase a disk without confirmation (pass --yes)")
    answers.validate()

    payload = find_payload(payload_dir)
    manifest = Manifest.load(payload)

    by_path = {d.path: d for d in (disks if disks is not None else list_disks())}
    disk = by_path.get(target)
    if disk is None:
        raise ImageInstallError(f"{target} is not a disk this installer can use")
    if disk.is_boot:
        raise ImageInstallError(f"{target} is the disk PengyOS is running from")
    if disk.mounted:
        raise ImageInstallError(f"{target} has mounted filesystems: {', '.join(disk.mounted)}")
    if disk.size_bytes < MIN_DISK:
        raise ImageInstallError(
            f"{target} is too small ({disk.size_label}); PengyOS needs at least "
            f"{Disk('', '', MIN_DISK).size_label}"
        )

    _verify_payload(payload, manifest, report)

    report("partition", 0.0, "Partitioning the disk")
    _run(run, ["sgdisk", "--zap-all", target], "erasing the old partition table", ok=(0, 2))
    _run(
        run,
        [
            "sgdisk", "-o",
            "-n", f"1:0:+{ESP_SIZE // 1024**2}M", "-t", "1:EF00", "-c", "1:PENGYOS-ESP",
            "-n", f"2:0:+{SLOT_SIZE // 1024**2}M", "-t", f"2:{ROOT_TYPE}", "-c", "2:pengyos-root-a",
            "-n", f"3:0:+{SLOT_SIZE // 1024**2}M", "-t", f"3:{ROOT_TYPE}", "-c", "3:pengyos-root-b",
            "-n", "4:0:0", "-t", "4:8300", "-c", "4:pengyos-data",
            target,
        ],
        "partitioning the disk",
    )
    part = {n: partition_path(target, n) for n in (1, 2, 3, 4)}
    for p in part.values():
        _wait_for(p)
    # root-b stays empty until the first update, but the new partition can sit
    # where an old install kept a filesystem (the 4 -> 8 GiB slot change put it
    # over the old data partition): COSMIC Files offered that stale
    # "pengyos-data" and could not mount it. Clear any signature there.
    _run(run, ["wipefs", "-a", part[3]], "clearing the second system partition")
    info = _run(run, ["sgdisk", "-i", "2", target], "reading the root partition").stdout
    partuuid_a = _sgdisk_field(info, "Partition unique GUID").lower()
    info = _run(run, ["sgdisk", "-i", "1", target], "reading the boot partition").stdout
    partuuid_esp = _sgdisk_field(info, "Partition unique GUID").lower()

    total = manifest.esp.raw_size + manifest.rootfs.raw_size
    written = [0, 0.0]

    def progress(n: int) -> None:
        written[0] += n
        now = time.monotonic()
        if now - written[1] > 0.25:
            written[1] = now
            report("write", min(written[0] / total, 1.0), "Writing PengyOS to the disk")

    _stream_write(payload / manifest.esp.file, part[1], manifest.esp, progress)
    _stream_write(payload / manifest.rootfs.file, part[2], manifest.rootfs, progress)
    report("write", 1.0, "Writing PengyOS to the disk")

    report("root", 0.1, "Preparing the system partition")
    # e2fsck: 0 = clean, 1 = errors corrected.
    _run(run, ["e2fsck", "-fy", part[2]], "checking the system filesystem", ok=(0, 1))
    _run(run, ["resize2fs", part[2]], "resizing the system filesystem")
    _run(run, ["tune2fs", "-U", "random", "-L", "pengyos-root-a", part[2]],
         "labelling the system filesystem")

    os.makedirs(TARGET_ROOT, exist_ok=True)
    os.makedirs(TARGET_ESP, exist_ok=True)
    mounted: list[str] = []
    try:
        report("boot", 0.2, "Configuring the boot loader")
        _run(run, ["mount", "-t", "vfat", part[1], TARGET_ESP], "mounting the boot partition")
        mounted.append(TARGET_ESP)
        boot_dir = Path(TARGET_ESP) / "EFI" / "BOOT"
        (boot_dir / "grub.cfg").write_text(render_grub_cfg({"a": partuuid_a}))
        grubenv = str(boot_dir / "grubenv")
        _run(run, ["grub-editenv", grubenv, "create"], "creating the boot environment")
        _run(run, ["grub-editenv", grubenv, "set", "pengyos_slot=a"], "selecting root slot a")
        # A firmware boot entry, first in BootOrder (efibootmgr --create does
        # that). Without one the machine relies on the removable-media fallback
        # path and tries any older entry (e.g. the OS that was on the disk) first.
        if os.path.isdir(EFIVARS):
            _run(run, ["efibootmgr", "--create", "--disk", target, "--part", "1",
                       "--label", "PengyOS", "--loader", "\\EFI\\BOOT\\BOOTX64.EFI"],
                 "adding the PengyOS firmware boot entry")

        report("provision", 0.05, "Setting up this computer")
        # btrfs: the machine's state gets cheap snapshots (docs/state-layout.md,
        # SPEC §1.5 — Tier-1 changes snapshotted and revertible).
        _run(run, ["mkfs.btrfs", "-f", "-q", "-L", "pengyos-data", part[4]],
             "formatting the data partition")
        root = Path(TARGET_ROOT)
        _run(run, ["mount", "-t", "ext4", "-o", "ro", part[2], str(root)],
             "mounting the system partition")
        mounted.append(str(root))
        data = root / "data"
        _run(run, ["mount", "-t", "btrfs", "-o", DATA_MOUNT_OPTS, part[4], str(data)],
             "mounting the data partition")
        mounted.append(str(data))
        # One subvolume per area, so each can be snapshotted / rolled back alone.
        for sub in DATA_SUBVOLUMES:
            _run(run, ["btrfs", "subvolume", "create", str(data / sub)],
                 f"creating the {sub} subvolume")
        for d in ("etc/upper", "etc/work"):
            (data / d).mkdir(parents=True, exist_ok=True)
        # /var and /root start as the image's; the machine owns them from here on.
        _run(run, ["cp", "-a", f"{root}/var/.", f"{data}/var/"], "seeding /var")
        _run(run, ["cp", "-a", f"{root}/root/.", f"{data}/root/"], "seeding /root")
        # Assemble exactly what preinit assembles at boot, then provision through it,
        # so every change lands on the data partition.
        _run(run, ["mount", "-t", "overlay", "overlay", "-o",
                   f"lowerdir={root}/etc,upperdir={data}/etc/upper,workdir={data}/etc/work",
                   f"{root}/etc"], "mounting the /etc overlay")
        mounted.append(f"{root}/etc")
        for sub in ("var", "home", "root"):
            _run(run, ["mount", "--bind", f"{data}/{sub}", f"{root}/{sub}"], f"mounting /{sub}")
            mounted.append(f"{root}/{sub}")
        # The image's /dev is an empty directory: useradd and friends want the real one.
        _run(run, ["mount", "--bind", "/dev", f"{root}/dev"], "mounting /dev for the chroot")
        mounted.append(f"{root}/dev")
        _provision(root, answers, run, nm_connections, esp_partuuid=partuuid_esp)
    finally:
        for point in reversed(mounted):
            run(["umount", point], check=False)
        run(["sync"], check=False)
    report("done", 1.0, "PengyOS is installed")


def _provision(root: Path, answers: ProvisionAnswers, run: Runner,
               nm_connections: str = LIVE_NM_CONNECTIONS, esp_partuuid: str = "") -> None:
    # SSH host keys are per machine: drop any the image carries (images built
    # before sshd-keygen.service baked one shared set); first boot makes new ones.
    for key in (root / "etc" / "ssh").glob("ssh_host_*"):
        key.unlink()
    (root / "etc" / "hostname").write_text(answers.hostname + "\n")
    hosts = root / "etc" / "hosts"
    try:
        lines = [
            ln for ln in hosts.read_text().splitlines() if not ln.startswith("127.0.1.1")
        ]
    except OSError:
        lines = ["127.0.0.1\tlocalhost"]
    lines.append(f"127.0.1.1\t{answers.hostname}")
    hosts.write_text("\n".join(lines) + "\n")

    try:
        defined = {ln.split(":", 1)[0] for ln in (root / "etc" / "group").read_text().splitlines()}
    except OSError:
        defined = set()
    groups = [g for g in USER_GROUPS if g in defined]
    # shadow-utils useradd (busybox addgroup here cannot add a user to a group):
    # own group (-U), home from /etc/skel (-m), the admin/seat/device groups (-G).
    shell = next((s for s in USER_SHELLS if (root / s.lstrip("/")).exists()), "/bin/sh")
    argv = ["chroot", str(root), "useradd", "-m", "-U", "-s", shell]
    if groups:
        argv += ["-G", ",".join(groups)]
    _run(run, argv + [answers.username], f"creating the user {answers.username}")

    # The desktop starts at its login screen (greetd); automatic login for this
    # user only when chosen. /etc/pengyos/autologin.conf is what greetd-config
    # reads at boot and `pengyos-autologin` / AccountsService change later.
    # Only where there is a login screen (not on the Server).
    if answers.autologin and (root / "etc" / "systemd" / "system" / "greetd.service").exists():
        conf = root / "etc" / "pengyos" / "autologin.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(
            "# Written by pengyos-install (\"Log in automatically\"); change it with\n"
            "# `sudo pengyos-autologin on|off`.\n"
            f"[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin={answers.username}\n"
        )

    # SSH keys for the user (sshd is key-only). Owned by the user, via chroot:
    # the uid exists only in the target's passwd.
    keys = [ln.strip() for ln in answers.authorized_keys.splitlines() if ln.strip()]
    if keys:
        ssh = root / "home" / answers.username / ".ssh"
        ssh.mkdir(parents=True, exist_ok=True)
        os.chmod(ssh, 0o700)
        (ssh / "authorized_keys").write_text("\n".join(keys) + "\n")
        os.chmod(ssh / "authorized_keys", 0o600)
        _run(run, ["chroot", str(root), "chown", "-R", f"{answers.username}:{answers.username}",
                   f"/home/{answers.username}/.ssh"], "giving the user their SSH keys")
    # The same password for root: the image ships the build's default root
    # password, which every copy of the image would otherwise share.
    # Hash here with our own random salt per account: the image's shadow
    # chpasswd derives both salts from the clock (identical salts, predictable
    # across machines). busybox mkpasswd takes the salt; the password goes in
    # on stdin (-P 0), never on argv.
    lines = []
    for user in (answers.username, "root"):
        salt = "".join(secrets.choice(_SALT_CHARS) for _ in range(16))
        hashed = _run(
            run,
            ["mkpasswd", "-m", "sha512", "-S", salt, "-P", "0"],
            "hashing the password",
            input=answers.password + "\n",
        ).stdout.strip()
        if not hashed.startswith(f"$6${salt}$"):
            raise ImageInstallError("hashing the password produced an unexpected result")
        lines.append(f"{user}:{hashed}\n")
    _run(
        run,
        ["chroot", str(root), "chpasswd", "-e"],  # -e: already hashed
        "setting the passwords",
        input="".join(lines),
    )

    # Carry over the network profiles made in the live session (Wi-Fi joined from
    # the panel), so the installed system is online on first boot. NetworkManager
    # keeps secrets in these files: root-only, like NetworkManager writes them.
    src = Path(nm_connections)
    profiles = sorted(src.glob("*.nmconnection")) if src.is_dir() else []
    if profiles:
        dest = root / "etc" / "NetworkManager" / "system-connections"
        dest.mkdir(parents=True, exist_ok=True)
        os.chmod(dest, 0o700)
        for profile in profiles:
            target = dest / profile.name
            target.write_bytes(profile.read_bytes())
            os.chmod(target, 0o600)

    # The ESP on /efi, mounted on first access: fwupd stages UEFI capsule
    # updates there (and a future updater puts kernels there). Root-only.
    if esp_partuuid:
        fstab = root / "etc" / "fstab"
        try:
            lines = [ln for ln in fstab.read_text().splitlines() if " /efi " not in ln]
        except OSError:
            lines = []
        lines.append(
            f"PARTUUID={esp_partuuid} /efi vfat "
            "umask=0077,noauto,x-systemd.automount,x-systemd.idle-timeout=60 0 2"
        )
        fstab.write_text("\n".join(lines) + "\n")
