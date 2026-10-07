"""Parse and (later) generate freedesktop ``.desktop`` entries.

This module reads and renders freedesktop ``.desktop`` files. :func:`parse_desktop`
parses a document; :func:`render_entry` serialises a single ``[Desktop Entry]``
block back to text (used by the ``integrate`` subcommand to emit a validated
``.desktop`` file).

The freedesktop Desktop Entry Specification (https://specifications.freedesktop.org
/desktop-entry-spec/) groups keys into sections delimited by ``[Name]`` headers.
Keys are ``key=value`` lines; values may be localised as ``key[lang]=value`` and
lines may begin with ``#`` to comment them out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENTRY = "Desktop Entry"


@dataclass
class DesktopEntry:
    """A single ``[section]`` block of a ``.desktop`` file."""

    section: str
    keys: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str | None = None) -> str | None:
        """Return the value for *key* (case-sensitive) or *default*."""
        return self.keys.get(key, default)

    @property
    def entry_type(self) -> str | None:
        return self.get("Type")

    @property
    def name(self) -> str | None:
        return self.get("Name")

    @property
    def icon(self) -> str | None:
        return self.get("Icon")

    @property
    def exec(self) -> str | None:
        return self.get("Exec")


@dataclass
class DesktopDocument:
    """The parsed contents of a ``.desktop`` file."""

    entries: list[DesktopEntry] = field(default_factory=list)

    @property
    def main(self) -> DesktopEntry | None:
        """The primary ``[Desktop Entry]`` block, if present."""
        for entry in self.entries:
            if entry.section == DEFAULT_ENTRY:
                return entry
        return self.entries[0] if self.entries else None


def render_entry(entry: DesktopEntry) -> str:
    """Serialize *entry* back to freedesktop ``.desktop`` text.

    Keys are written in the order they were parsed (preserving localised
    ``Name[lang]=`` variants and any ``X-`` extension keys). The output ends
    with a trailing newline and contains no comments or blank lines — exactly
    the shape :func:`parse_desktop` round-trips back to.
    """
    lines = [f"[{entry.section}]"]
    lines += [f"{key}={value}" for key, value in entry.keys.items()]
    return "\n".join(lines) + "\n"


class DesktopParseError(ValueError):
    """Raised when a ``.desktop`` file cannot be parsed."""


def parse_desktop(text: str) -> DesktopDocument:
    """Parse *text* (a ``.desktop`` file's contents) into a document.

    Raises:
        DesktopParseError: If the text is empty or structurally invalid
            (e.g. a key outside of any section).
    """
    if not text.strip():
        raise DesktopParseError("empty .desktop document")

    doc = DesktopDocument()
    current: DesktopEntry | None = None
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = DesktopEntry(section=line[1:-1].strip())
            doc.entries.append(current)
            continue
        if current is None:
            raise DesktopParseError(f"line {lineno}: key outside of any section")
        key, sep, value = line.partition("=")
        if not sep:
            raise DesktopParseError(f"line {lineno}: missing '=' in {line!r}")
        current.keys[key.strip()] = value.strip()
    if not doc.entries:
        raise DesktopParseError("no sections found")
    return doc


def parse_desktop_file(path: str | Path) -> DesktopDocument:
    """Parse the ``.desktop`` file at *path*."""
    return parse_desktop(Path(path).read_text(encoding="utf-8"))
