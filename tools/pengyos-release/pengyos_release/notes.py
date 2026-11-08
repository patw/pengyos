"""Release notes: short plain text for people, never Markdown.

The notes are shown as they are: by the Updates app (one text block per
paragraph), by ``pengyos-update check`` in a terminal, in the feed's
``pengyos:notes``, and on the update page. None of them renders Markdown, so
``**bold**`` and backticks showed up literally. The format is therefore plain
text with one convention that reads well raw:

    One line that says what this release is about.

    - a short line per change people will notice
    - another one

:func:`problems` is the lint ``release.sh`` runs before it builds anything (and
``update-publish`` runs again). :func:`plain` cleans up notes published before
the lint existed, so the regenerated page and feed read cleanly too.
:func:`to_html` renders the convention for the update page and the feed: ``- ``
lines become a list, everything else a paragraph.
"""

from __future__ import annotations

import html
import re

MAX_CHARS = 600
MAX_LINE = 160

_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_STAR_BULLET = re.compile(r"^\s*[*+]\s")
_EMPHASIS = re.compile(r"\*\*|__")
_SINGLE_EMPHASIS = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")

GUIDE = (
    "Release notes are for people deciding whether to update, not a changelog:\n"
    "  - plain text, no Markdown (no **bold**, `code`, # headings or [links](url));\n"
    "  - first line: what this release is about, in one sentence;\n"
    "  - then a few '- ' lines, one per change people will notice;\n"
    f"  - at most {MAX_CHARS} characters. The details belong in CHANGELOG.md."
)


def problems(text: str) -> list[str]:
    """Why these notes would not read well where they are shown (empty = fine)."""
    out: list[str] = []
    body = text.strip()
    if not body:
        return ["the notes are empty"]
    if len(body) > MAX_CHARS:
        out.append(f"{len(body)} characters; keep it under {MAX_CHARS} (details go in CHANGELOG.md)")
    lines = body.splitlines()
    if lines[0].lstrip().startswith("- ") or (len(lines) > 1 and lines[1].strip()):
        out.append("start with a one-line summary, then a blank line")
    for n, line in enumerate(lines, 1):
        if "`" in line:
            out.append(f"line {n}: backticks (Markdown code) show up literally")
        if _EMPHASIS.search(line):
            out.append(f"line {n}: ** or __ (Markdown emphasis) show up literally")
        if _HEADING.match(line):
            out.append(f"line {n}: a # heading shows up literally")
        if _LINK.search(line):
            out.append(f"line {n}: a [text](url) link shows up literally")
        if _STAR_BULLET.match(line):
            out.append(f"line {n}: use '- ' for a list line, not '*' or '+'")
        if line.startswith((" ", "\t")) and line.strip():
            out.append(f"line {n}: indented; keep each list item on one line")
        if len(line) > MAX_LINE:
            out.append(f"line {n}: {len(line)} characters; keep a line under {MAX_LINE}")
    return out


def plain(text: str) -> str:
    """Notes as plain text: Markdown marks removed, indented continuations joined.

    Notes that pass :func:`problems` come back unchanged (apart from surrounding
    whitespace); this is for the ones published before the lint.
    """
    out: list[str] = []
    for raw in text.strip().splitlines():
        line = _LINK.sub(r"\1", raw)
        line = _EMPHASIS.sub("", line).replace("`", "")
        line = _SINGLE_EMPHASIS.sub(r"\1", line)
        line = _HEADING.sub("", line)
        line = _STAR_BULLET.sub("- ", line)
        if raw.startswith((" ", "\t")) and line.strip() and out and out[-1].strip():
            stripped = line.strip()
            if stripped.startswith("- "):           # a nested list item: its own line
                out.append(stripped)
            else:                                   # a wrapped continuation
                out[-1] = out[-1].rstrip() + " " + stripped
            continue
        out.append(line.strip())
    return "\n".join(out).strip()


def to_html(text: str) -> str:
    """The update page's and the feed's rendering: '- ' lines become a list."""
    parts: list[str] = []
    for para in plain(text).split("\n\n"):
        lines = [l.strip() for l in para.splitlines() if l.strip()]
        prose: list[str] = []
        items: list[str] = []

        def flush_prose() -> None:
            if prose:
                parts.append(f"<p>{html.escape(' '.join(prose))}</p>")
                prose.clear()

        def flush_items() -> None:
            if items:
                parts.append("<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ul>")
                items.clear()

        for line in lines:
            if line.startswith("- "):
                flush_prose()
                items.append(line[2:].strip())
            else:
                flush_items()
                prose.append(line)
        flush_prose()
        flush_items()
    return "".join(parts)
