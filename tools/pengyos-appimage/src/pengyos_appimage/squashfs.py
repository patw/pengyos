"""Extract the AppImage *payload* (a squashfs image) without executing it.

The payload is a squashfs image appended to the AppImage at
``X-AppImage-Payload-Offset``. We never run it; we only read files out of it
with an external, trusted extraction tool. Three tools are supported, tried in
order: ``bsdtar`` (libarchive, understands the AppImage offset natively),
``unsquashfs``, and ``7z``/``7za``.

This module is the seam that tests mock: :func:`default_tool` returns a tool
whose :meth:`SquashfsTool.run` method shells out via :func:`subprocess.run`.
Unit tests replace that method with canned output so no real tool, network, or
display is required.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# File extensions that count as icons (per the Icon Theme Specification).
ICON_EXTENSIONS = {".png", ".svg", ".svgz", ".xpm", ".ico"}

SQUASHFS_ROOT = "squashfs-root"


class SquashfsError(RuntimeError):
    """Raised when the payload cannot be listed or extracted."""


class SquashfsTool:
    """Base class for a payload-extraction tool.

    Subclasses set the :attr:`name` class attribute and implement
    :meth:`available`, :meth:`list_files`, and :meth:`extract_to_dir`. All
    shelling out goes through :meth:`run`, which is the single seam tests patch.
    """

    #: The executable name; overridden by each concrete tool.
    name = ""

    def available(self) -> bool:
        return shutil.which(self.name) is not None

    def list_files(self, appimage: str | Path, payload_offset: int) -> list[str]:
        raise NotImplementedError

    def extract_to_dir(
        self, appimage: str | Path, payload_offset: int, dest: str | Path
    ) -> list[str]:
        raise NotImplementedError

    def run(
        self, args: list[str], input: bytes | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run ``<self.name> <args>``. The seam tests patch."""
        return subprocess.run(
            [self.name, *args],
            input=input,
            capture_output=True,
            text=True,
            check=True,
        )


class UnsquashfsTool(SquashfsTool):
    """Extract with the upstream ``unsquashfs``."""

    name = "unsquashfs"

    def list_files(self, appimage, payload_offset) -> list[str]:
        result = self.run(
            ["-o", str(payload_offset), "-l", str(appimage)]
        )
        return _parse_unsquashfs_list(result.stdout)

    def extract_to_dir(self, appimage, payload_offset, dest) -> list[str]:
        # Extract the whole image; callers read the specific files they need.
        # (Single-file selection via ``-e`` is unreliable across unsquashfs
        # versions, and we want everything for inspection anyway.)
        self.run(
            ["-o", str(payload_offset), "-d", str(dest), str(appimage)]
        )
        return _iter_regular_files(dest)


class BsdtarTool(SquashfsTool):
    """Extract with libarchive's ``bsdtar`` (understands the offset natively)."""

    name = "bsdtar"

    def list_files(self, appimage, payload_offset) -> list[str]:
        result = self.run(["-a", "-fformat=SQUASHFS", "-t", str(appimage)])
        return _strip_squashfs_root(result.stdout.splitlines())

    def extract_to_dir(self, appimage, payload_offset, dest) -> list[str]:
        self.run(["-a", "-fformat=SQUASHFS", "-x", "-f", str(appimage), "-C", str(dest)])
        return _iter_regular_files(dest)


class SevenZipTool(SquashfsTool):
    """Extract with ``7z``/``7za``."""

    name = "7z"

    def available(self) -> bool:
        return shutil.which("7z") is not None or shutil.which("7za") is not None

    @property
    def _binary(self) -> str:
        return "7za" if shutil.which("7za") else "7z"

    def list_files(self, appimage, payload_offset) -> list[str]:
        # ``7z l`` prints the archive listing; paths are relative to the root.
        result = subprocess.run(
            [self._binary, "l", "-slt", str(appimage)],
            capture_output=True, text=True, check=True,
        )
        paths: list[str] = []
        for line in result.stdout.splitlines():
            if line.startswith("Path = "):
                paths.append(line[len("Path = "):])
        return _strip_squashfs_root(paths)

    def extract_to_dir(self, appimage, payload_offset, dest) -> list[str]:
        subprocess.run(
            [self._binary, "x", "-y", "-o" + str(dest), str(appimage)],
            check=True,
        )
        return _iter_regular_files(dest)


def default_tool() -> SquashfsTool:
    """Return the first available extraction tool, or raise if none exist."""
    for tool in (BsdtarTool(), UnsquashfsTool(), SevenZipTool()):
        if tool.available():
            return tool
    raise SquashfsError(
        "no squashfs extraction tool found (need bsdtar, unsquashfs, or 7z)"
    )


def list_files(appimage, payload_offset, tool: SquashfsTool | None = None) -> list[str]:
    """List the files inside the payload at *payload_offset*."""
    tool = tool or default_tool()
    return tool.list_files(appimage, payload_offset)


def extract_to_dir(appimage, payload_offset, dest, tool: SquashfsTool | None = None) -> list[str]:
    """Extract the payload into *dest*; return the extracted regular files."""
    tool = tool or default_tool()
    return tool.extract_to_dir(appimage, payload_offset, dest)


def _strip_squashfs_root(paths: list[str]) -> list[str]:
    """Normalise archive listings to paths relative to the squashfs root."""
    out: list[str] = []
    for path in paths:
        path = path.strip()
        if not path or path == SQUASHFS_ROOT:
            continue
        if path.startswith(SQUASHFS_ROOT + "/"):
            path = path[len(SQUASHFS_ROOT) + 1:]
        out.append(path)
    return out


def _parse_unsquashfs_list(text: str) -> list[str]:
    return _strip_squashfs_root(text.splitlines())


def _iter_regular_files(root: str | Path) -> list[str]:
    return sorted(
        str(p.relative_to(root))
        for p in Path(root).rglob("*")
        if p.is_file()
    )
