"""``inspect`` — what an AppImage is, without running or unpacking it.

1. :mod:`pengyos_appimage.runtime` reads the runtime ELF at the front of the
   file: the AppImage type, architecture, payload offset, update information
   and whether it carries a signature.
2. :mod:`pengyos_appimage.squashfs` reads the payload in place and returns the
   root ``.desktop`` file and the icon it names.

Nothing is executed and nothing is written to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import desktop, runtime, squashfs

# Icon file types, in the order we prefer them for one Icon= name.
ICON_EXTENSIONS = (".svg", ".png", ".svgz", ".xpm")


@dataclass
class DesktopInfo:
    """The useful fields of the payload's ``.desktop`` file."""

    path: str | None = None
    name: str | None = None
    icon: str | None = None
    exec: str | None = None
    type: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "path": self.path,
            "name": self.name,
            "icon": self.icon,
            "exec": self.exec,
            "type": self.type,
        }


@dataclass
class IconInfo:
    """The icon the ``.desktop`` file names, if the payload has it."""

    found: bool = False
    #: Path inside the payload.
    path: str | None = None
    size: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {"found": self.found, "path": self.path, "size": self.size}


@dataclass
class InspectResult:
    """The complete result of inspecting an AppImage."""

    type: int
    arch: str
    payload_offset: int
    compression: str
    update_info: str | None
    signed: bool
    desktop: DesktopInfo
    icon: IconInfo

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "arch": self.arch,
            "payload_offset": self.payload_offset,
            "compression": self.compression,
            "update_info": self.update_info,
            "signed": self.signed,
            "desktop": self.desktop.to_dict(),
            "icon": self.icon.to_dict(),
        }


class InspectionError(RuntimeError):
    """Raised when an AppImage cannot be inspected."""


def find_root_desktop(names: list[str]) -> str | None:
    """Return the ``.desktop`` file among the payload root's *names*, or None.

    The AppImage spec puts exactly one ``*.desktop`` at the root; we take the
    first in sorted order if a payload carries more.
    """
    return next(
        (n for n in sorted(names) if "/" not in n and n.lower().endswith(".desktop")),
        None,
    )


def read_icon(
    fs: squashfs.SquashFS, icon_name: str | None, root_names: list[str]
) -> tuple[str, bytes] | None:
    """Return ``(file name, bytes)`` of the icon *icon_name*, or None.

    The spec puts ``<Icon>.{png,svg,...}`` at the payload root; ``.DirIcon`` is
    the fallback every AppImage should carry (often a symlink to the former).
    """
    if icon_name:
        if Path(icon_name).suffix.lower() in ICON_EXTENSIONS and icon_name in root_names:
            candidates = [icon_name]
        else:
            candidates = [icon_name + ext for ext in ICON_EXTENSIONS]
        for name in candidates:
            if name in root_names:
                data = fs.read_path(name)
                if data:
                    return name, data
    if ".DirIcon" in root_names:
        data = fs.read_path(".DirIcon")
        if data:
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                return ".DirIcon.png", data
            if b"<svg" in data[:4096]:
                return ".DirIcon.svg", data
    return None


def inspect_appimage(appimage: str | Path) -> InspectResult:
    """Inspect *appimage*. Nothing is executed or unpacked.

    Raises:
        InspectionError: If the file is not a readable type-2 AppImage.
    """
    path = Path(appimage)
    if not path.is_file():
        raise InspectionError(f"not a file: {path}")
    try:
        meta = runtime.read_metadata_file(path)
    except runtime.AppImageFormatError as exc:
        raise InspectionError(str(exc)) from exc

    desktop_info = DesktopInfo()
    icon_info = IconInfo()
    try:
        with squashfs.SquashFS.open(path, meta.payload_offset) as fs:
            compression = fs.compression
            names = [n for n, _ in fs.listdir(fs.root())]
            desktop_name = find_root_desktop(names)
            if desktop_name is not None:
                text = fs.read_path(desktop_name)
                if text is not None:
                    try:
                        entry = desktop.parse_desktop(text.decode("utf-8", "replace")).main
                    except desktop.DesktopParseError as exc:
                        raise InspectionError(f"{desktop_name}: {exc}") from exc
                    if entry is not None:
                        desktop_info = DesktopInfo(
                            path=desktop_name,
                            name=entry.name,
                            icon=entry.icon,
                            exec=entry.exec,
                            type=entry.entry_type,
                        )
                        icon = read_icon(fs, entry.icon, names)
                        if icon is not None:
                            icon_info = IconInfo(True, icon[0], len(icon[1]))
    except squashfs.SquashfsError as exc:
        raise InspectionError(f"cannot read the payload: {exc}") from exc

    return InspectResult(
        type=meta.type,
        arch=meta.arch,
        payload_offset=meta.payload_offset,
        compression=compression,
        update_info=meta.update_info,
        signed=meta.signed,
        desktop=desktop_info,
        icon=icon_info,
    )
