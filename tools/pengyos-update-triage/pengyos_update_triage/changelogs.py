"""Changelog input schema and loader.

Changelog fixtures are plain-text upstream changelogs, one file per component,
living in a directory. This module defines *how* those files are attributed to a
component and how their version structure is parsed into
:class:`Changelog` objects. The *keyword scanning* that decides whether a line
is security-relevant is a later subtask — here we
only establish the shape that scanning will operate on.

Component attribution
---------------------
A changelog is attributed to a component by, in order of precedence:

1. A ``component:`` directive on the first non-blank line, in comment form
   (e.g. ``# component: glibc`` or ``@component: glibc``).
2. Otherwise, the file-name stem (``glibc.txt`` → ``glibc``).

This keeps fixtures trivial to author (drop ``<component>.txt``) while allowing
files whose name does not match the component.

Version sections
----------------
A *section* begins at a line that introduces a version. Recognised header forms
(include, not exhaust): a Markdown heading with a version (``## 2.40.0``), an
explicit label (``Version 2.40.0`` / ``Release 2.40.0`` / ``== 2.40.0``), or a
bare leading version token. Lines up to the next header are that section's
``entries``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Union

from .errors import ChangelogError
from .models import Changelog, ChangelogSection

__all__ = ["load_changelogs", "parse_changelog_text", "parse_version_sections"]

# A version token: optional leading 'v', one or more dotted numbers, optional
# pre-release / build suffix. Anchored at the start of the (cleaned) line.
_VERSION_TOKEN = re.compile(r"^v?(\d+(?:\.\d+)*)([.\-+][0-9A-Za-z.\-+]*)?")

# ``component:`` directive in comment form, on its own line.
_COMPONENT_DIRECTIVE = re.compile(r"^\s*[#@;]?\s*component\s*:\s*(.+?)\s*$", re.IGNORECASE)

# An explicit label preceding a version on a header line.
_LABEL_PREFIX = re.compile(
    r"^\s*(?:v(?:ersion|release)?|==?|==)\s*", re.IGNORECASE
)


def _is_version_header(line: str) -> "re.Match | None":
    """Return the version match if ``line`` introduces a version, else ``None``.

    Only lines whose *entire* content is a version (with an optional label,
    Markdown hashes, bullets, and ``==`` decoration stripped) count as headers,
    so a bullet that merely *mentions* a version is not mistaken for a section
    break. Recognised header forms include ``## 2.40.0``, ``Version 3.0.14``,
    and Debian-style ``== 1.3.1 ==``.
    """
    cleaned = line.strip()
    if not cleaned:
        return None
    # Strip Markdown heading hashes / bullet markers, then an explicit label.
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned)
    cleaned = re.sub(r"^[*+\-]\s+", "", cleaned)
    cleaned = _LABEL_PREFIX.sub("", cleaned)
    # Strip a trailing " (date)" or "(year)" annotation.
    cleaned = re.sub(r"\s*\(.*\)\s*$", "", cleaned).strip()
    # Strip trailing decoration (e.g. the closing "==", or trailing bullets).
    cleaned = re.sub(r"[=*\s]+$", "", cleaned).strip()
    match = _VERSION_TOKEN.match(cleaned)
    if match and match.end() >= len(cleaned):
        return match
    return None


def parse_version_sections(text: str) -> List[ChangelogSection]:
    """Parse ``text`` into a list of :class:`ChangelogSection`.

    Lines before the first version header (a preamble) are ignored. Blank lines
    are not recorded as entries.
    """
    sections: List[ChangelogSection] = []
    current: "ChangelogSection | None" = None
    for raw_line in text.splitlines():
        header = _is_version_header(raw_line)
        if header:
            version = header.group(1) + (header.group(2) or "")
            current = ChangelogSection(
                version=version, header_line=raw_line, entries=[]
            )
            sections.append(current)
            continue
        if current is None:
            continue  # preamble
        if raw_line.strip():
            current.entries.append(raw_line.strip())
    return sections


def parse_changelog_text(
    text: str,
    default_component: str,
    path: Union[str, Path] = "",
) -> Changelog:
    """Parse changelog ``text`` into a :class:`Changelog`.

    ``default_component`` is used when the text has no ``component:`` directive
    (typically the file-name stem).
    """
    component = default_component
    body_lines: List[str] = []
    directive_taken = False
    for line in text.splitlines():
        if not directive_taken and line.strip():
            m = _COMPONENT_DIRECTIVE.match(line)
            if m and not _is_version_header(line):
                component = m.group(1).strip()
                directive_taken = True
                continue
        body_lines.append(line)
    body = "\n".join(body_lines)
    return Changelog(
        component=component,
        path=str(path),
        text=text,
        sections=parse_version_sections(body),
    )


def load_changelogs(directory: Union[str, Path]) -> List[Changelog]:
    """Load every changelog fixture under ``directory`` (recursively).

    Files are loaded in a stable sorted order for reproducibility. The component
    for each file is its name stem unless the file declares a ``component:``
    directive. A missing directory is a hard error; an empty one yields no
    changelogs.

    Raises
    ------
    ChangelogError
        if the directory is missing / not a directory / a file is unreadable.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        raise ChangelogError(f"changelogs directory not found: {dir_path}", path=str(dir_path))
    if not dir_path.is_dir():
        raise ChangelogError(f"changelogs path is not a directory: {dir_path}", path=str(dir_path))

    files = sorted(p for p in dir_path.rglob("*") if p.is_file())
    changelogs: List[Changelog] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ChangelogError(
                f"could not read changelog {path}: {exc}", path=str(path)
            ) from exc
        except UnicodeDecodeError as exc:
            raise ChangelogError(
                f"changelog {path} is not valid UTF-8: {exc}", path=str(path)
            ) from exc
        changelogs.append(parse_changelog_text(text, default_component=path.stem, path=path))
    return changelogs
