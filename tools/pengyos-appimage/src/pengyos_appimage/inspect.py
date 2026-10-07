"""``inspect`` — read an AppImage's type + ``.desktop`` + icon without running it.

The inspection pipeline is deliberately read-only and never touches the
AppImage's embedded hooks:

1. :func:`pengyos_appimage.trailer.read_metadata` parses the ELF trailer to learn
   the AppImage ``Format``/``Type`` and where the ``.desktop`` file lives.
2. :func:`pengyos_appimage.squashfs.extract_to_dir` pulls the payload out with a
   trusted external tool (``unsquashfs``/``bsdtar``/``7z``) into a temp dir.
3. :func:`pengyos_appimage.desktop.parse_desktop` reads the ``.desktop`` file.
4. We locate the icon referenced by the ``.desktop`` file.

At no point do we execute ``AppRun`` or any other embedded script.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import desktop, squashfs, trailer

# Icon filenames we accept, matched against the ``Icon=`` identifier.
_ICON_EXTENSIONS = frozenset(squashfs.ICON_EXTENSIONS)


@dataclass
class DesktopInfo:
    """The useful fields of the inspected ``.desktop`` file."""

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
    """The inspected icon, if found."""

    found: bool = False
    path: str | None = None
    size: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {"found": self.found, "path": self.path, "size": self.size}


@dataclass
class InspectResult:
    """The complete result of inspecting an AppImage."""

    format: str | None
    type: str | None
    arch: str | None
    payload_offset: int | None
    desktop: DesktopInfo
    icon: IconInfo

    def to_dict(self) -> dict[str, object]:
        return {
            "format": self.format,
            "type": self.type,
            "arch": self.arch,
            "payload_offset": self.payload_offset,
            "desktop": self.desktop.to_dict(),
            "icon": self.icon.to_dict(),
        }


class InspectionError(RuntimeError):
    """Raised when an AppImage cannot be inspected."""


def _normalize_payload_path(path: str | None) -> str | None:
    """Normalise a payload path to ``a/b/c`` relative to the squashfs root."""
    if not path:
        return None
    return path.replace("\\", "/").lstrip("./").rstrip("/")


def _find_in_extracted(normalized: str | None, files: list[str]) -> str | None:
    """Return the extracted path matching *normalized*, or None."""
    if normalized is None:
        return None
    for candidate in files:
        if _normalize_payload_path(candidate) == normalized:
            return candidate
    return None


def _find_icon(icon_name: str | None, files: list[str]) -> str | None:
    """Find an icon file whose basename matches *icon_name* + icon extension."""
    if not icon_name:
        return None
    for candidate in files:
        stem = Path(candidate).stem
        suffix = Path(candidate).suffix.lower()
        if stem == icon_name and suffix in _ICON_EXTENSIONS:
            return candidate
    return None


def inspect_appimage(
    appimage: str | Path,
    tool: squashfs.SquashfsTool | None = None,
) -> InspectResult:
    """Inspect *appimage*, returning a :class:`InspectResult`.

    The AppImage's embedded hooks are never executed.

    Raises:
        InspectionError: If the trailer, ``.desktop`` file, or payload cannot be
            read.
    """
    path = Path(appimage)
    if not path.is_file():
        raise InspectionError(f"not a file: {path}")

    try:
        data = path.read_bytes()
        meta = trailer.read_metadata(data)
    except trailer.TrailerError as exc:
        raise InspectionError(str(exc)) from exc

    if meta.payload_offset is None:
        raise InspectionError("trailer has no X-AppImage-Payload-Offset")

    desktop_info = DesktopInfo()
    icon_info = IconInfo()

    with tempfile.TemporaryDirectory(prefix="pengyos-appimage-") as dest:
        try:
            files = squashfs.extract_to_dir(
                path, meta.payload_offset, dest, tool=tool
            )
        except squashfs.SquashfsError as exc:
            raise InspectionError(str(exc)) from exc

        # Locate the .desktop file named in the trailer.
        extracted_desktop = _find_in_extracted(meta.path, files)
        if extracted_desktop is None:
            # Fall back to the first *.desktop in the payload.
            extracted_desktop = next(
                (f for f in files if f.lower().endswith(".desktop")), None
            )

        if extracted_desktop is not None:
            try:
                doc = desktop.parse_desktop_file(Path(dest) / extracted_desktop)
                entry = doc.main
                if entry is not None:
                    desktop_info = DesktopInfo(
                        path=_normalize_payload_path(meta.path)
                        or extracted_desktop,
                        name=entry.name,
                        icon=entry.icon,
                        exec=entry.exec,
                        type=entry.entry_type,
                    )
                    icon_info = _load_icon(
                        Path(dest) / _find_icon(entry.icon, files), files
                    )
            except (desktop.DesktopParseError, OSError) as exc:
                raise InspectionError(str(exc)) from exc

    return InspectResult(
        format=meta.format,
        type=meta.type,
        arch=meta.arch,
        payload_offset=meta.payload_offset,
        desktop=desktop_info,
        icon=icon_info,
    )


def _load_icon(extracted: Path | None, files: list[str]) -> IconInfo:
    if extracted is None:
        return IconInfo(found=False)
    if not extracted.is_file():
        return IconInfo(found=False)
    return IconInfo(found=True, path=str(extracted), size=extracted.stat().st_size)
