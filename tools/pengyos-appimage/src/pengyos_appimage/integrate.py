"""``integrate`` — put an AppImage in the app launcher, where it already is.

An AppImage runs in place: the runtime mounts its payload with FUSE. What the
desktop lacks is an entry for it. ``integrate`` reads the payload's root
``.desktop`` file and icon (pure Python, nothing run, nothing unpacked) and
writes, under the XDG data home (``$XDG_DATA_HOME``, else ``~/.local/share``):

* ``applications/appimage-<id>.desktop`` — the app's own entry, with every
  ``Exec`` pointed at the AppImage's absolute path and a
  ``X-PengyOS-AppImage=<path>`` marker that ``--remove`` finds it by;
* ``icons/hicolor/<size>/apps/appimage-<id>.<ext>`` — its icon, at a size the
  hicolor theme declares (an icon in an undeclared size directory is ignored).

``<id>`` is the payload ``.desktop`` file's name, so integrating a newer build
of the same app replaces the older entry instead of adding a second one.

File associations come from the entry's ``MimeType=`` line; no MIME types are
declared, because an app does not get to define them.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import desktop, inspect, runtime, squashfs

MARKER_KEY = "X-PengyOS-AppImage"
PREFIX = "appimage-"

# The sizes hicolor's index.theme declares; anything else is not looked up.
HICOLOR_SIZES = (16, 22, 24, 32, 36, 48, 64, 72, 96, 128, 192, 256, 512)

# Keys dropped from the copied entry: TryExec is replaced; DBusActivatable
# would make the launcher ask D-Bus for a service that is not installed.
_DROP_KEYS = ("TryExec", "DBusActivatable")

# Characters that force quoting of an Exec argument (Desktop Entry Spec).
_EXEC_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")

_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class IntegrateError(RuntimeError):
    """Raised when an AppImage cannot be integrated."""


@dataclass
class IntegrateResult:
    """What ``integrate`` wrote."""

    appimage: Path
    desktop_path: Path
    icon_path: Path | None
    replaced: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "appimage": str(self.appimage),
            "desktop_path": str(self.desktop_path),
            "icon_path": str(self.icon_path) if self.icon_path else None,
            "replaced": self.replaced,
            "warnings": self.warnings,
        }


def resolve_dest(dest: str | Path | None) -> Path:
    """The XDG data home entries are written under."""
    if dest is not None:
        return Path(dest)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def integrate_appimage(
    appimage: str | Path, dest: str | Path | None = None
) -> IntegrateResult:
    """Write the launcher entry and icon for *appimage*.

    Raises:
        IntegrateError: If the file is not a readable AppImage or its payload
            has no usable root ``.desktop`` file.
    """
    path = Path(appimage).absolute()
    if not path.is_file():
        raise IntegrateError(f"not a file: {path}")
    try:
        meta = runtime.read_metadata_file(path)
        with squashfs.SquashFS.open(path, meta.payload_offset) as fs:
            names = [n for n, _ in fs.listdir(fs.root())]
            desktop_name = inspect.find_root_desktop(names)
            if desktop_name is None:
                raise IntegrateError("the payload has no .desktop file at its root")
            text = fs.read_path(desktop_name)
            if text is None:
                raise IntegrateError(f"cannot read {desktop_name} from the payload")
            doc = desktop.parse_desktop(text.decode("utf-8", "replace"))
            main = doc.main
            if main is None or main.section != desktop.DEFAULT_ENTRY:
                raise IntegrateError("the .desktop file has no [Desktop Entry] group")
            if not main.get("Name") or not main.get("Exec"):
                raise IntegrateError("the .desktop file lacks Name= or Exec=")
            icon = inspect.read_icon(fs, main.icon, names)
    except runtime.AppImageFormatError as exc:
        raise IntegrateError(str(exc)) from exc
    except squashfs.SquashfsError as exc:
        raise IntegrateError(f"cannot read the payload: {exc}") from exc
    except desktop.DesktopParseError as exc:
        raise IntegrateError(f"{desktop_name}: {exc}") from exc

    app_id = _ID_UNSAFE.sub("-", Path(desktop_name).stem).strip("-.") or "app"
    stem = PREFIX + app_id
    root = resolve_dest(dest)
    warnings: list[str] = []

    icon_path = None
    if icon is not None:
        icon_name, data = icon
        subdir = _icon_dir(icon_name, data)
        if subdir is None:
            warnings.append(f"icon {icon_name} is not PNG or SVG; using the app's own icon name")
        else:
            _remove_icons(root, stem)
            target = root / "icons" / "hicolor" / subdir / "apps"
            target.mkdir(parents=True, exist_ok=True)
            icon_path = target / (stem + Path(icon_name).suffix.lower())
            icon_path.write_bytes(data)
    else:
        warnings.append(f"icon {main.icon or '(unset)'} not found in the payload")

    exec_path = _quote_exec_arg(str(path))
    for entry in doc.entries:
        if entry.section != desktop.DEFAULT_ENTRY and not entry.section.startswith(
            "Desktop Action "
        ):
            continue
        for key in _DROP_KEYS:
            entry.keys.pop(key, None)
        if entry.get("Exec"):
            entry.keys["Exec"] = exec_path + _exec_args(entry.keys["Exec"])
    main.keys["TryExec"] = _escape_value(str(path))
    if icon_path is not None:
        main.keys["Icon"] = stem
    main.keys[MARKER_KEY] = _escape_value(str(path))

    apps = root / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    desktop_path = apps / f"{stem}.desktop"
    replaced = desktop_path.exists()
    desktop_path.write_text(desktop.render_document(doc), encoding="utf-8")

    if not os.access(path, os.X_OK):
        warnings.append(f"{path.name} is not executable; the entry will not start it until: chmod +x {path}")
    return IntegrateResult(path, desktop_path, icon_path, replaced, warnings)


def remove_integration(appimage: str | Path, dest: str | Path | None = None) -> list[Path]:
    """Remove the entries ``integrate`` wrote for *appimage*; return what went.

    Matches on the marker, so it works after the AppImage itself is deleted.
    """
    path = str(Path(appimage).absolute())
    root = resolve_dest(dest)
    removed: list[Path] = []
    for entry_file in sorted((root / "applications").glob(PREFIX + "*.desktop")):
        try:
            doc = desktop.parse_desktop_file(entry_file)
        except (OSError, desktop.DesktopParseError):
            continue
        main = doc.main
        if main is None or main.get(MARKER_KEY) != _escape_value(path):
            continue
        entry_file.unlink()
        removed.append(entry_file)
        removed += _remove_icons(root, entry_file.stem)
    return removed


def _remove_icons(root: Path, stem: str) -> list[Path]:
    gone = []
    for icon in (root / "icons" / "hicolor").glob(f"*/apps/{stem}.*"):
        icon.unlink()
        gone.append(icon)
    return gone


def _icon_dir(name: str, data: bytes) -> str | None:
    """The hicolor subdirectory for an icon, or None if we cannot place it."""
    suffix = Path(name).suffix.lower()
    if suffix in (".svg", ".svgz"):
        return "scalable"
    if suffix == ".png" and data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        side = max(width, height)
        fits = [s for s in HICOLOR_SIZES if s <= side]
        size = fits[-1] if fits else HICOLOR_SIZES[0]
        return f"{size}x{size}"
    return None


def _exec_args(value: str) -> str:
    """Everything in an Exec value after the program (including the space)."""
    value = value.lstrip()
    if value.startswith('"'):
        i = 1
        while i < len(value):
            if value[i] == "\\":
                i += 2
                continue
            if value[i] == '"':
                return value[i + 1:]
            i += 1
        return ""
    head, sep, rest = value.partition(" ")
    return sep + rest if sep else ""


def _quote_exec_arg(arg: str) -> str:
    """Quote one Exec argument, then escape it as a desktop string value."""
    arg = arg.replace("%", "%%")
    if any(c in _EXEC_RESERVED for c in arg):
        inner = re.sub(r'(["`$\\])', r"\\\1", arg)
        arg = f'"{inner}"'
    return _escape_value(arg)


def _escape_value(value: str) -> str:
    """Escape a desktop-entry string value (backslash, newline, tab)."""
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
