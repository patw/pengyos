"""``integrate`` — install a ``.desktop`` file, icon, and mime entries.

Given an *extracted* AppImage prefix (the output of ``pengyos-appimage
extract``), this module writes the freedesktop integration entries a desktop
environment needs to offer the app:

* a ``.desktop`` file in ``<dest>/applications/`` (rendered from the extracted
  payload's ``.desktop`` and validated to be well-formed),
* the app's icon in ``<dest>/icons/hicolor/<size>x<size>/apps/``, and
* a MIME declaration in ``<dest>/mime/packages/`` when the application's
  ``Exec`` line accepts URIs.

It **never** executes the AppImage's embedded ``AppRun`` hook. Integration only
*reads* the already-extracted files and *copies* them into the destination tree.
There is no code path that runs the embedded hook — integration never invokes
``AppRun`` or any other embedded script.

The destination root defaults to ``$XDG_DATA_HOME`` (falling back to
``~/.local/share``). Each entry type lives under a well-known subdirectory of
that root, so ``--dest`` selects the XDG data home, not a single file.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import desktop
from . import squashfs

# Well-known subdirectories of the XDG data home where each entry type lives.
APPLICATIONS_DIRNAME = "applications"
ICONS_DIRNAME = "icons"
MIME_DIRNAME = "mime"
MIME_PACKAGES_DIRNAME = "packages"
ICON_THEME_DIRNAME = "hicolor"

# The vendor stem used for the mime-packages XML file.
DEFAULT_MIME_VENDOR = "appimage.org"

# Marker key written into every generated .desktop file. It is ``X-`` prefixed
# so freedesktop's validator accepts it as an extension key.
GENERATED_MARKER_KEY = "X-PengyOS-Generated"
GENERATED_MARKER_VALUE = "true"

# The single section every generated document carries.
_MAIN_SECTION = "Desktop Entry"

# Exec-field magic tokens (per the Desktop Entry Spec's Exec format) that mark
# an application as accepting URIs. ``%U``/``%u`` mean "list"/"single" URIs.
_URI_EXEC_TOKENS = frozenset({"%U", "%u"})


class IntegrateError(RuntimeError):
    """Raised when an extracted app cannot be integrated."""


@dataclass
class IntegrateResult:
    """The outcome of integrating an extracted app."""

    #: The extracted app directory that was integrated.
    app_dir: Path
    #: The XDG data home the entries were written under.
    dest: Path
    #: Path to the generated ``.desktop`` file, or ``None``.
    desktop_path: Path | None = None
    #: Path to the installed icon file, or ``None``.
    icon_path: Path | None = None
    #: Path to the mime-packages XML file, or ``None``.
    mime_xml_path: Path | None = None
    #: MIME types declared in the mime-packages XML.
    mime_types: list[str] = field(default_factory=list)
    #: Human-readable notes (e.g. a skipped icon); never fatal on their own.
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view of the result."""
        return {
            "app_dir": str(self.app_dir),
            "dest": str(self.dest),
            "desktop_path": str(self.desktop_path) if self.desktop_path else None,
            "icon_path": str(self.icon_path) if self.icon_path else None,
            "mime_xml_path": str(self.mime_xml_path) if self.mime_xml_path else None,
            "mime_types": list(self.mime_types),
            "warnings": list(self.warnings),
        }


def _resolve_dest(dest: str | Path | None) -> Path:
    """Resolve the XDG data home to write integration entries under."""
    if dest is not None:
        return Path(dest)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    # Fall back to ~/.local/share per the XDG Base Directory Specification.
    return Path.home() / ".local" / "share"


def _iter_desktop_files(app_dir: Path):
    """Yield ``*.desktop`` files under *app_dir*, shallowest first."""
    matches = sorted(app_dir.rglob("*.desktop"), key=lambda p: len(p.parts))
    yield from matches


def _find_desktop(app_dir: Path) -> Path | None:
    """Find the extracted payload's ``.desktop`` file (shallowest match)."""
    return next(_iter_desktop_files(app_dir), None)


def _find_icon(app_dir: Path, icon_identifier: str | None) -> Path | None:
    """Locate the icon file referenced by *icon_identifier*.

    *Icon* may be a theme name (``Icon=sample`` -> ``sample.png``) or an
    absolute path. We search the freedesktop icon locations under the app dir
    first, then fall back to any matching file in the tree.
    """
    if not icon_identifier:
        return None

    ident = Path(icon_identifier)
    # An absolute Icon path is used verbatim if present in the payload.
    if ident.is_absolute() and (app_dir / ident).is_file():
        return app_dir / ident

    stem = ident.name
    candidates = [
        p
        for p in app_dir.rglob("*")
        if p.is_file() and p.stem == stem and p.suffix.lower() in squashfs.ICON_EXTENSIONS
    ]
    if not candidates:
        return None
    # Prefer a real freedesktop icon theme location over stray copies.
    theme_hits = [
        p for p in candidates if "icons" in p.parts and "hicolor" in p.parts
    ]
    pool = theme_hits or candidates
    # The largest file wins (a 256x256 PNG outranks a 16x16 thumbnail of the
    # same name).
    return max(pool, key=lambda p: p.stat().st_size)


def _exec_accepts_uris(exec_value: str | None) -> bool:
    """Return ``True`` when an ``Exec`` value accepts URIs.

    Per the Desktop Entry Specification, the ``%U`` and ``%u`` tokens mean the
    command accepts a list/single of URIs.
    """
    if not exec_value:
        return False
    return any(token in exec_value for token in _URI_EXEC_TOKENS)


def _render_desktop(entry: desktop.DesktopEntry) -> str:
    """Render *entry* to a validated ``.desktop`` document text.

    The entry is normalised to a single ``[Desktop Entry]`` block with the
    required keys present. A provenance marker is appended (an ``X-`` extension
    key, which the freedesktop validator accepts).
    """
    entry.section = _MAIN_SECTION
    entry.keys.setdefault("Version", "1.0")
    # Provenance marker: X- prefixed so it passes desktop-file-validate.
    entry.keys[GENERATED_MARKER_KEY] = GENERATED_MARKER_VALUE
    return desktop.render_entry(entry)


def _validate_entry(entry: desktop.DesktopEntry) -> None:
    """Ensure the entry carries the keys required for its ``Type``.

    Raises:
        IntegrateError: If a required key is missing.
    """
    entry_type = (entry.get("Type") or "Application").lower()
    required = ["Name"]
    if entry_type == "application":
        required.append("Exec")
    missing = [key for key in required if not entry.get(key)]
    if missing:
        raise IntegrateError(
            "generated .desktop is missing required key(s): "
            + ", ".join(missing)
        )


def _render_mime_xml(vendor: str, mime_types: list[str]) -> str:
    """Render a freedesktop mime-packages XML document.

    The file lists the declared MIME types so ``update-mime-database`` can fold
    them into the system cache. Elements are sorted for deterministic output.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<mime-type-vendor name="{vendor}"/>',
        "<mime-types>",
    ]
    for mime_type in sorted(mime_types):
        lines.append(f'  <mime-type type="{mime_type}"/>')
    lines.append("</mime-types>")
    return "\n".join(lines) + "\n"


def _icon_size(path: Path) -> str:
    """Best-effort icon pixel size from its path (``256`` for ``256x256``)."""
    for part in path.parts:
        if "x" in part and part.replace("x", "").isdigit():
            # "256x256" -> "256"
            return part.split("x")[0]
    # Fall back to a common default size when the path carries no hint.
    return "256"


def integrate_app(
    app_dir: str | Path,
    dest: str | Path | None = None,
) -> IntegrateResult:
    """Integrate the extracted app at *app_dir* into *dest*.

    Reads the extracted ``.desktop`` file and icon (never running the embedded
    ``AppRun`` hook) and writes the freedesktop integration entries under the
    resolved XDG data home.

    Args:
        app_dir: The extracted application directory.
        dest: Optional XDG data home root. Defaults to ``$XDG_DATA_HOME`` or
            ``~/.local/share``.

    Returns:
        An :class:`IntegrateResult` describing what was written.

    Raises:
        IntegrateError: If no ``.desktop`` file is found, or the generated
            file is missing a key required for its ``Type``.
    """
    app_path = Path(app_dir)
    if not app_path.is_dir():
        raise IntegrateError(f"not a directory: {app_path}")

    desktop_file = _find_desktop(app_path)
    if desktop_file is None:
        raise IntegrateError(
            f"no .desktop file found in extracted app {app_path}"
        )

    try:
        doc = desktop.parse_desktop_file(desktop_file)
    except desktop.DesktopParseError as exc:
        raise IntegrateError(str(exc)) from exc

    entry = doc.main
    if entry is None:
        raise IntegrateError("no [Desktop Entry] block found")

    _validate_entry(entry)
    document = _render_desktop(entry)

    dest_path = _resolve_dest(dest)
    warnings: list[str] = []

    # 1. .desktop file.
    name = entry.name or "app"
    applications_dir = dest_path / APPLICATIONS_DIRNAME
    applications_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = applications_dir / f"{name}.desktop"
    desktop_path.write_text(document, encoding="utf-8")

    # 2. icon.
    icon_identifier = entry.icon
    icon_source = _find_icon(app_path, icon_identifier)
    icon_path: Path | None = None
    if icon_source is not None:
        ext = icon_source.suffix.lstrip(".").lower() or "png"
        size = _icon_size(icon_source)
        theme_dir = (
            dest_path / ICONS_DIRNAME / ICON_THEME_DIRNAME / f"{size}x{size}" / "apps"
        )
        theme_dir.mkdir(parents=True, exist_ok=True)
        icon_path = theme_dir / f"{icon_identifier or name}.{ext}"
        shutil.copy2(icon_source, icon_path)
    else:
        warnings.append(
            f"icon '{icon_identifier or '(unset)'}' not found; skipping icon install"
        )

    # 3. mime entries.
    mime_types: list[str] = []
    if _exec_accepts_uris(entry.get("Exec")):
        mime_types.append("text/uri-list")
    if mime_types:
        mime_packages_dir = dest_path / MIME_DIRNAME / MIME_PACKAGES_DIRNAME
        mime_packages_dir.mkdir(parents=True, exist_ok=True)
        mime_xml_path = mime_packages_dir / f"{DEFAULT_MIME_VENDOR}.xml"
        mime_xml_path.write_text(
            _render_mime_xml(DEFAULT_MIME_VENDOR, mime_types), encoding="utf-8"
        )
    else:
        mime_xml_path = None

    return IntegrateResult(
        app_dir=app_path,
        dest=dest_path,
        desktop_path=desktop_path,
        icon_path=icon_path,
        mime_xml_path=mime_xml_path,
        mime_types=mime_types,
        warnings=warnings,
    )
