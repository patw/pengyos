"""This machine: its image identity, root slots, ESP and boot environment.

The disk layout is docs/state-layout.md's: partition 1 the ESP (GRUB, grub.cfg,
grubenv, ``slot-a/bzImage``, ``slot-b/bzImage``), 2 and 3 the ext4 root slots
(``pengyos-root-a`` / ``-b``), 4 the data partition. Everything is found on the
disk the running root is on, never by label alone, so a plugged-in installer
stick or a second PengyOS disk is never touched.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .release import UpdateError

CONFIG = "/etc/pengyos/update.conf"
KEYS_DIR = "/usr/share/pengyos/keys"
CACHE = "/var/cache/pengyos-update"
ESP_MOUNT = "/run/pengyos-update/esp"
DEFAULT_FEED = "https://pengyos.catbee.ca/update/feed.xml"
SLOT_PART = {"a": 2, "b": 3}
ESP_PART = 1


def run(argv: list[str], what: str, ok: tuple[int, ...] = (0,),
        capture: bool = True) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(argv, capture_output=capture, text=True)
    except FileNotFoundError:
        raise UpdateError(f"{what}: {argv[0]} is not installed") from None
    if r.returncode not in ok:
        detail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines() if capture else []
        raise UpdateError(f"{what} failed (exit {r.returncode})"
                          + (f": {detail[-1]}" if detail else ""))
    return r


# --------------------------------------------------------------------------
# Identity and settings
# --------------------------------------------------------------------------

def read_os_release(path: str = "/etc/os-release") -> dict[str, str]:
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        try:
            v = " ".join(shlex.split(v))
        except ValueError:
            pass
        out[k.strip()] = v
    return out


def read_config(path: str = CONFIG) -> dict[str, str]:
    """``key = value`` lines; defaults for anything missing."""
    cfg = {"feed": DEFAULT_FEED, "channel": "stable"}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return cfg
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip().lower()] = v.strip()
    return cfg


def write_config_value(key: str, value: str, path: str = CONFIG) -> None:
    """Set one key, keeping the file's comments and other lines."""
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    done = False
    for i, line in enumerate(lines):
        body = line.split("#", 1)[0]
        if "=" in body and body.split("=", 1)[0].strip().lower() == key:
            lines[i] = f"{key} = {value}"
            done = True
    if not done:
        lines.append(f"{key} = {value}")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".new")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, p)


# --------------------------------------------------------------------------
# Disk layout
# --------------------------------------------------------------------------

def cmdline_root_partuuid(cmdline: str) -> str:
    for arg in cmdline.split():
        if arg.startswith("root=PARTUUID="):
            return arg.split("=", 2)[2].lower()
    raise UpdateError("this system was not booted from a PengyOS root slot "
                      "(no root=PARTUUID= on the kernel command line) — "
                      "updates need an installed system, not the live stick")


@dataclass(frozen=True)
class Layout:
    disk: str                 # /dev/nvme0n1
    esp: str                  # /dev/nvme0n1p1
    slots: dict[str, str]     # {"a": "/dev/nvme0n1p2", "b": ...}
    partuuids: dict[str, str]  # slot -> PARTUUID (lowercase)
    booted: str               # "a" or "b"

    @property
    def other(self) -> str:
        return "b" if self.booted == "a" else "a"


def _partition_dev(disk_name: str, number: int, sys_block: str) -> str:
    base = Path(sys_block) / disk_name
    for part in base.iterdir():
        f = part / "partition"
        if f.is_file() and f.read_text().strip() == str(number):
            return "/dev/" + part.name
    raise UpdateError(f"partition {number} of /dev/{disk_name} not found — "
                      "this disk does not have the PengyOS A/B layout")


def detect_layout(cmdline: str | None = None, sys_block: str = "/sys/block") -> Layout:
    if cmdline is None:
        cmdline = Path("/proc/cmdline").read_text()
    booted_uuid = cmdline_root_partuuid(cmdline)
    by_uuid = Path("/dev/disk/by-partuuid") / booted_uuid
    if not by_uuid.exists():
        raise UpdateError(f"cannot find the running root partition ({booted_uuid})")
    root_part = os.path.basename(os.path.realpath(by_uuid))
    disk_name = None
    for d in Path(sys_block).iterdir():
        if (d / root_part).is_dir():
            disk_name = d.name
            break
    if disk_name is None:
        raise UpdateError(f"cannot find the disk that {root_part} is on")
    slots = {s: _partition_dev(disk_name, n, sys_block) for s, n in SLOT_PART.items()}
    esp = _partition_dev(disk_name, ESP_PART, sys_block)
    partuuids, booted = {}, None
    for s, dev in slots.items():
        info = run(["lsblk", "-dno", "PARTUUID,PARTLABEL", dev], f"reading {dev}").stdout.split()
        if len(info) < 2 or info[1] != f"pengyos-root-{s}":
            raise UpdateError(f"{dev} is not labelled pengyos-root-{s} — not an A/B PengyOS disk")
        partuuids[s] = info[0].lower()
        if partuuids[s] == booted_uuid:
            booted = s
    if booted is None:
        raise UpdateError("the running root is neither root slot of this disk")
    return Layout("/dev/" + disk_name, esp, slots, partuuids, booted)


def device_size(dev: str) -> int:
    return int(run(["blockdev", "--getsize64", dev], f"reading the size of {dev}").stdout.strip())


# --------------------------------------------------------------------------
# ESP / GRUB
# --------------------------------------------------------------------------

_LINUX_RE = re.compile(r"^\s*linux\s+/slot-[ab]/bzImage\s+root=PARTUUID=\S+\s+(.*?)\s*$", re.M)


def kernel_args_from_grub_cfg(text: str) -> str | None:
    """The kernel arguments the installed grub.cfg boots with (after root=)."""
    m = _LINUX_RE.search(text)
    return m.group(1) if m else None


def mount_of(dev: str, mounts: str = "/proc/self/mounts") -> tuple[str, list[str]] | None:
    """Where ``dev`` is mounted (mountpoint, options), or None."""
    real = os.path.realpath(dev)
    with open(mounts, encoding="utf-8") as fh:
        for line in fh:
            f = line.split()
            if len(f) >= 4 and f[0].startswith("/dev/") and os.path.realpath(f[0]) == real:
                return f[1].replace("\\040", " "), f[3].split(",")
    return None


class Esp:
    """The ESP, read-write, for the duration of a ``with`` block.

    systemd's GPT auto-generator usually has it on ``/efi`` already (an
    automount); a second vfat mount of the same device with other flags is
    refused, so an existing mount is used as it is (made read-write if needed)
    and left in place."""

    def __init__(self, dev: str, mountpoint: str = ESP_MOUNT) -> None:
        self.dev, self.path = dev, Path(mountpoint)
        self._mounted = False

    def __enter__(self) -> "Esp":
        existing = mount_of(self.dev)
        if existing:
            self.path = Path(existing[0])
            if "ro" in existing[1]:
                run(["mount", "-o", "remount,rw", str(self.path)], "making the boot partition writable")
            return self
        self.path.mkdir(parents=True, exist_ok=True)
        run(["mount", "-t", "vfat", "-o", "rw,noatime", self.dev, str(self.path)],
            "mounting the boot partition")
        self._mounted = True
        return self

    def __exit__(self, *exc) -> None:
        os.sync()
        if self._mounted:
            run(["umount", str(self.path)], "unmounting the boot partition", ok=(0, 1, 32))

    @property
    def boot_dir(self) -> Path:
        return self.path / "EFI" / "BOOT"

    @property
    def grubenv(self) -> str:
        return str(self.boot_dir / "grubenv")

    def env(self) -> dict[str, str]:
        out = run(["grub-editenv", self.grubenv, "list"], "reading the boot environment").stdout
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def set_env(self, **values: str) -> None:
        run(["grub-editenv", self.grubenv, "set", *(f"{k}={v}" for k, v in values.items())],
            "writing the boot environment")


def read_env(esp_dev: str) -> dict[str, str]:
    """grubenv (reading only; the ESP is mounted as Esp does it)."""
    with Esp(esp_dev) as esp:
        return esp.env()
