"""``extract`` — extract an AppImage payload to a prefix and validate structure.

This module extracts the AppImage's squashfs payload into a destination prefix
using a trusted external tool (``bsdtar``/``unsquashfs``/``7z``), then validates
that the extracted layout matches what an AppImage payload is expected to
contain. It **never** executes the AppImage's embedded hooks: extraction only
*reads* files out of the payload via a trusted tool; it never runs ``AppRun`` or
any other embedded script.

The canonical AppImage (Type 2 / Type 3) payload is a squashfs filesystem that
contains:

* an ``AppRun`` executable at the root (the entry point — **never run** here),
* a root directory (whose location may be given by
  ``X-AppImage-Root-Directory``), and
* a ``.desktop`` file (referenced by ``X-AppImage-Path``).

:func:`extract_appimage` extracts the payload to a prefix and returns an
:class:`ExtractResult` describing what was extracted and whether the structure
looks right. Structural problems are reported as *warnings*; a completely empty
or unreadable extraction is a fatal :class:`ExtractError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import squashfs, trailer

# The canonical AppImage entry point at the root of the payload. It is a plain
# file entry once the payload is extracted; we must never execute it.
APP_ENTRY = "AppRun"


class ExtractError(RuntimeError):
    """Raised when the payload cannot be extracted or is empty."""


@dataclass
class ExtractResult:
    """The outcome of extracting and validating an AppImage payload."""

    #: The prefix directory the payload was extracted into.
    prefix: Path
    #: The extracted regular files, relative to *prefix* and sorted.
    files: list[str]
    #: Path (relative to *prefix*) of the ``AppRun`` entry point, or ``None``.
    app_entry: str | None
    #: Path (relative to *prefix*) of the discovered ``.desktop`` file, or ``None``.
    desktop: str | None
    #: The ``X-AppImage-Root-Directory`` from the trailer, or ``None``.
    root_directory: str | None
    #: Human-readable structural warnings (never fatal on their own).
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view of the result."""
        return {
            "prefix": str(self.prefix),
            "app_entry": self.app_entry,
            "desktop": self.desktop,
            "root_directory": self.root_directory,
            "file_count": len(self.files),
            "files": self.files,
            "warnings": self.warnings,
        }

    @property
    def file_count(self) -> int:
        """The number of extracted regular files."""
        return len(self.files)

    @property
    def ok(self) -> bool:
        """``True`` when the extracted structure produced no warnings."""
        return not self.warnings


def extract_appimage(
    appimage: str | Path, to: str | Path, tool: squashfs.SquashfsTool | None = None
) -> ExtractResult:
    """Extract *appimage*'s payload into *to* and validate its structure.

    The AppImage's embedded hooks are never executed — extraction only reads
    files out of the payload via a trusted tool.

    Args:
        appimage: Path to the ``.AppImage`` file.
        to: Destination prefix directory. Created if it does not exist.
        tool: Optional extraction tool (defaults to the first available
            ``bsdtar``/``unsquashfs``/``7z``).

    Returns:
        An :class:`ExtractResult` describing the extraction.

    Raises:
        ExtractError: If the file is missing, the trailer cannot be parsed, the
            payload offset is absent, the destination is not a directory, or the
            extraction produces no files.
    """
    path = Path(appimage)
    if not path.is_file():
        raise ExtractError(f"not a file: {path}")

    try:
        meta = trailer.read_metadata_file(path)
    except trailer.TrailerError as exc:
        raise ExtractError(str(exc)) from exc

    if meta.payload_offset is None:
        raise ExtractError("trailer has no X-AppImage-Payload-Offset")

    dest = Path(to)
    if dest.exists() and not dest.is_dir():
        raise ExtractError(f"destination exists and is not a directory: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    try:
        files = squashfs.extract_to_dir(path, meta.payload_offset, dest, tool=tool)
    except squashfs.SquashfsError as exc:
        raise ExtractError(str(exc)) from exc

    if not files:
        raise ExtractError(f"extraction produced no files in {dest}")

    return _validate(dest, meta, files)


def _validate(
    prefix: Path, meta: trailer.TrailerMetadata, files: list[str]
) -> ExtractResult:
    """Validate the extracted *prefix* against the AppImage payload layout.

    Structural problems are reported as *warnings*; only a missing entry point
    or missing ``.desktop`` file are noted here. A completely empty prefix is a
    fatal error raised by :func:`extract_appimage`, not here.
    """
    warnings: list[str] = []

    app_entry = APP_ENTRY if APP_ENTRY in files else None

    desktop = _find_desktop(meta.path, files)

    root_directory = None
    if meta.root_directory:
        root_directory = _normalize_payload_path(meta.root_directory)
        if root_directory:
            present = any(
                f == root_directory or f.startswith(root_directory + "/")
                for f in files
            )
            if not present:
                warnings.append(
                    f"X-AppImage-Root-Directory '{root_directory}' is not "
                    f"present in the payload"
                )

    if app_entry is None:
        warnings.append("no AppRun entry point found at the prefix root")
    if desktop is None:
        expected = _normalize_payload_path(meta.path) if meta.path else "*.desktop"
        warnings.append(
            f"no .desktop file found (expected '{expected}' or any *.desktop)"
        )

    return ExtractResult(
        prefix=prefix,
        files=files,
        app_entry=app_entry,
        desktop=desktop,
        root_directory=root_directory,
        warnings=warnings,
    )


def _find_desktop(expected: str | None, files: list[str]) -> str | None:
    """Find the extracted ``.desktop`` matching *expected*, or any ``*.desktop``."""
    if expected:
        normalized = _normalize_payload_path(expected)
        for candidate in files:
            if _normalize_payload_path(candidate) == normalized:
                return candidate
    return next((f for f in files if f.lower().endswith(".desktop")), None)


def _normalize_payload_path(path: str) -> str:
    """Normalise a payload path to ``a/b/c`` relative to the squashfs root."""
    return path.replace("\\", "/").lstrip("./").rstrip("/")
