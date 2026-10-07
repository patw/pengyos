"""Tolerant parser for Buildroot ``.mk`` recipe files.

A Buildroot recipe is a GNU Makefile fragment.  We parse it *tolerantly*:
comments, blank lines, ``include``/``ifeq`` directives and any other line that
is not a plain variable assignment are ignored rather than raising.

The parser's job is deliberately narrow — it turns the raw text into a list of
named assignments, each carrying the 1-based line number it started on.  The
lint *rules* (next subtask) decide what "version pinned" or "license declared"
mean; this module only answers "what variables are assigned, with what value,
on which line".

Layering: ``recipes`` (discovery) -> ``recipe_parser`` ->
``rules`` -> ``report`` -> ``cli``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

RECIPE_SUFFIX = ".mk"

# The variables the linter cares about.  A recipe variable "counts" for a given
# check when its name *ends with* one of these suffixes, so both
# ``PENGYOS_SIGNIFY_LICENSE`` and ``FOO_LICENSE`` satisfy the "_LICENSE" check.
# Note the order matters only for callers that iterate; ``_LICENSE`` and
# ``_LICENSE_FILES`` never collide because neither name ends in the other.
INTERESTED_SUFFIXES: tuple[str, ...] = (
    "_VERSION",
    "_SITE",
    "_SOURCE",
    "_LICENSE",
    "_LICENSE_FILES",
)

# A single assignment:  NAME <op> value
# Supports the operators Buildroot recipes actually use.  ``:=`` is listed
# before ``=`` so it wins when both could match at the same position.
_ASSIGN_RE = re.compile(
    r"""
    ^\s*(?:override\s+|export\s+)?
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*
    (?P<op>=|:=|\?=|\+=|!=)\s*
    (?P<value>.*)$
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class ParsedVar:
    """A single variable assignment found in a recipe."""

    name: str
    """The variable name exactly as written (e.g. ``PENGYOS_SIGNIFY_VERSION``)."""

    value: str
    """The assignment's value with surrounding quotes and trailing comment removed."""

    line_no: int
    """1-based line where the assignment starts (continuations counted once)."""

    operator: str
    """The assignment operator as written (``=``, ``:=``, ``?=``, ``+=`` ...)."""


@dataclass(frozen=True)
class RecipeSource:
    """The parsed contents of a single ``.mk`` recipe."""

    path: Path
    """Path to the recipe file (``"<string>"`` for :meth:`from_text`)."""

    variables: tuple[ParsedVar, ...] = ()
    """Every assignment found, in source order."""

    @classmethod
    def from_text(cls, text: str, path: str | Path = "<string>") -> RecipeSource:
        """Parse *text* into a :class:`RecipeSource` without touching disk."""
        return cls(path=Path(path), variables=_parse_text(text))

    @classmethod
    def from_file(cls, path: str | Path) -> RecipeSource:
        """Parse the recipe at *path* into a :class:`RecipeSource`.

        Raises :class:`FileNotFoundError` if the file does not exist.
        """
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"recipe file does not exist: {resolved}")
        return cls.from_file_text(resolved.read_text(encoding="utf-8"), resolved)

    @classmethod
    def from_file_text(cls, text: str, path: Path) -> RecipeSource:
        """Like :meth:`from_text`, but stamps *path* onto the result."""
        return cls(path=path, variables=_parse_text(text))

    def get(self, name: str) -> str | None:
        """Return the last value assigned to *name* exactly, or ``None``."""
        var = self.get_var(name)
        return var.value if var is not None else None

    def get_var(self, name: str) -> ParsedVar | None:
        """Return the last :class:`ParsedVar` whose name equals *name*."""
        matches = [v for v in self.variables if v.name == name]
        return matches[-1] if matches else None

    def get_by_suffix(self, suffix: str) -> ParsedVar | None:
        """Return the last :class:`ParsedVar` whose name ends with *suffix*."""
        matches = [v for v in self.variables if v.name.endswith(suffix)]
        return matches[-1] if matches else None

    def line_by_suffix(self, suffix: str) -> int | None:
        """Return the line number of the last variable ending in *suffix*."""
        var = self.get_by_suffix(suffix)
        return var.line_no if var is not None else None

    def interested(self) -> dict[str, ParsedVar]:
        """Map each interested suffix to its last assignment (if present).

        Missing suffixes map to ``None`` so rules can report "absent" cleanly.
        """
        return {suffix: self.get_by_suffix(suffix) for suffix in INTERESTED_SUFFIXES}


def _parse_text(text: str) -> tuple[ParsedVar, ...]:
    """Parse *text* into a tuple of :class:`ParsedVar` (in source order)."""
    parsed: list[ParsedVar] = []
    for line_no, logical in _logical_lines(text.splitlines()):
        var = _parse_line(line_no, logical)
        if var is not None:
            parsed.append(var)
    return tuple(parsed)


def _logical_lines(physical: list[str]) -> list[tuple[int, str]]:
    """Join make continuation lines into logical ``(line_no, text)`` pairs.

    A trailing backslash continues the *value* onto the next physical line.
    Standalone comment lines never continue (their trailing backslash, if any,
    is part of the comment).  A continuation whose next line is itself a comment
    stops the value rather than swallowing the comment.
    """
    result: list[tuple[int, str]] = []
    i = 0
    n = len(physical)
    while i < n:
        start = i + 1
        line = physical[i]
        if _is_comment(line):
            result.append((start, line))
            i += 1
            continue
        # Strip any trailing comment before looking for a continuation backslash:
        # a backslash inside a comment does not continue the value.
        buf = _strip_comment(line).rstrip()
        while buf.endswith("\\"):
            buf = buf[:-1]  # drop the trailing backslash
            i += 1
            if i >= n:
                break
            nxt = physical[i]
            if _is_comment(nxt):
                i -= 1  # leave the comment line for the next iteration
                break
            buf = f"{buf} {nxt.strip()}"
        result.append((start, buf))
        i += 1
    return result


def _is_comment(line: str) -> bool:
    """True if *line* is blank or its first non-whitespace character is ``#``."""
    stripped = line.lstrip()
    return stripped == "" or stripped[0] == "#"


def _parse_line(line_no: int, text: str) -> ParsedVar | None:
    """Parse one logical line into a :class:`ParsedVar`, or ``None`` if ignored."""
    if _is_comment(text):
        return None
    match = _ASSIGN_RE.match(text)
    if match is None:
        return None
    return ParsedVar(
        name=match.group("name"),
        value=_strip_quotes(_strip_comment(match.group("value"))).strip(),
        line_no=line_no,
        operator=match.group("op"),
    )


def _strip_comment(value: str) -> str:
    """Remove a trailing GNU-Make comment from *value*.

    A ``#`` begins a comment only when it is at the start of the value or
    preceded by whitespace; a ``#`` embedded in a URL/word (no preceding space)
    is preserved so ``https://h/f#frag`` survives intact.
    """
    for idx, ch in enumerate(value):
        if ch == "#" and (idx == 0 or value[idx - 1].isspace()):
            return value[:idx].rstrip()
    return value


def _strip_quotes(value: str) -> str:
    """Remove a single matching pair of surrounding quotes, if present."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value
