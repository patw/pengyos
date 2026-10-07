"""PengyOS kernel config fragment file format + a tolerant parser.

This module is the single source of truth for the *fragment file format*
(spec.md "Fragment format") and the :func:`parse_fragment_text` /
:func:`parse_fragment_file` parsers that read it.

Fragment format
---------------
A fragment is a plain-text file of kernel ``CONFIG`` assignments, one per
line::

    CONFIG_VIRTIO=y
    CONFIG_EXT4_FS=m
    CONFIG_FOO="bar"
    # a comment line
    CONFIG_BAR=y   # inline comment

Rules enforced by the parser:

* Each assignment is ``CONFIG_<SYMBOL>=<value>``. The symbol must match
  :data:`SYMBOL_RE` (``CONFIG_`` followed by ``[A-Z0-9_]+``).
* Values come in three flavours:
  * ``y`` — built-in (boolean true)
  * ``n`` — disabled (boolean false)
  * ``m`` — loadable module
  * ``"..."`` — a quoted string; the inner text is the value.
* ``#`` starts a comment. A ``#`` that is not inside a quoted string starts an
  inline comment; everything after it is ignored.
* Blank lines and lines whose first non-whitespace character is ``#`` are
  skipped.
* Surrounding whitespace around the symbol / ``=`` / value is tolerated.

Tolerance
---------
The parser never raises on a malformed line: instead it records a
:class:`ParseError` (line number, raw text, and a human reason) and continues.
This lets the ``validate`` / ``compose`` layers surface bad lines without the
parser itself being brittle.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ValueKind(str, Enum):
    """The kind of a parsed ``CONFIG`` value.

    Members compare equal to their string value (``ValueKind.YES == "y"``), so
    callers may check ``entry.kind == ValueKind.YES`` or ``== "y"``.
    """

    YES = "y"
    NO = "n"
    MODULE = "m"
    STRING = "s"


# A kernel config symbol: the CONFIG_ prefix followed by uppercase letters,
# digits and underscores. Lowercase or other characters are rejected.
SYMBOL_RE = re.compile(r"^CONFIG_[A-Z0-9_]+$")

# A bareword value (y / n / m).
BAREWORD_RE = re.compile(r"^[ynm]$")


class ParseError(Exception):
    """Raised for a line that cannot be parsed at all.

    :func:`parse_fragment_text` does *not* raise this — malformed lines are
    collected into :class:`Fragment.errors`. This exception is offered for the
    strict callers (and tests) that want a hard failure on the first bad line.
    """


@dataclass(frozen=True)
class ConfigEntry:
    """A single parsed ``CONFIG_X=value`` assignment."""

    symbol: str
    value: str
    kind: ValueKind
    line_no: int
    source: str

    def __str__(self) -> str:
        if self.kind is ValueKind.STRING:
            return f"{self.symbol}=\"{self.value}\""
        return f"{self.symbol}={self.value}"


@dataclass
class ParseErrorRecord:
    """A malformed line, recorded rather than raised by the tolerant parser."""

    line_no: int
    raw: str
    reason: str


@dataclass
class Fragment:
    """The parsed contents of one fragment file.

    Attributes:
        name: The fragment's file name (basename), used as ``source`` on each
            entry and in error messages.
        entries: Every valid assignment, in file order. A symbol may appear
            more than once (e.g. an intra-file conflict); the *last* entry wins
            when reading :attr:`values`.
        errors: Malformed lines that were skipped. Empty when the fragment is
            clean.
    """

    name: str
    entries: list[ConfigEntry] = field(default_factory=list)
    errors: list[ParseErrorRecord] = field(default_factory=list)

    @property
    def values(self) -> dict[str, ConfigEntry]:
        """Map of symbol -> winning entry (last assignment in the file wins)."""
        result: dict[str, ConfigEntry] = {}
        for entry in self.entries:
            result[entry.symbol] = entry
        return result

    @property
    def is_clean(self) -> bool:
        """``True`` when no malformed lines were recorded."""
        return not self.errors


def _parse_value(
    symbol: str, token: str, line_no: int, source: str
) -> ConfigEntry:
    """Turn a bareword / quoted *token* into a :class:`ConfigEntry`.

    Raises :class:`ParseError` if the token is not a recognised value.
    """
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return ConfigEntry(
            symbol=symbol,
            value=token[1:-1],
            kind=ValueKind.STRING,
            line_no=line_no,
            source=source,
        )
    if BAREWORD_RE.match(token):
        return ConfigEntry(
            symbol=symbol,
            value=token,
            kind=ValueKind(token),
            line_no=line_no,
            source=source,
        )
    raise ParseError(
        f"line {line_no}: unrecognized value {token!r} "
        "(expected y, n, m, or a quoted string)"
    )


def _strip_inline_comment(text: str) -> str:
    """Remove an inline ``#`` comment, preserving ``#`` inside quotes."""
    in_string = False
    for i, ch in enumerate(text):
        if ch == '"':
            in_string = not in_string
        elif ch == "#" and not in_string:
            return text[:i]
    return text


def parse_fragment_text(
    text: str, name: str = "<text>"
) -> Fragment:
    """Parse fragment *text* into a :class:`Fragment`.

    This is the tolerant entry point: it never raises on a bad line, collecting
    them into :attr:`Fragment.errors` instead.
    """
    fragment = Fragment(name=name)
    reader = io.StringIO(text)
    for line_no, raw_line in enumerate(reader, start=1):
        line = _strip_inline_comment(raw_line.rstrip("\n")).strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            fragment.errors.append(
                ParseErrorRecord(
                    line_no=line_no,
                    raw=raw_line.rstrip("\n"),
                    reason="no '=' separating symbol and value",
                )
            )
            continue

        symbol, _, raw_value = line.partition("=")
        symbol = symbol.strip()
        value_token = raw_value.strip()

        if not SYMBOL_RE.match(symbol):
            fragment.errors.append(
                ParseErrorRecord(
                    line_no=line_no,
                    raw=raw_line.rstrip("\n"),
                    reason="symbol is not a valid CONFIG_ symbol",
                )
            )
            continue

        try:
            entry = _parse_value(symbol, value_token, line_no, name)
        except ParseError as exc:
            fragment.errors.append(
                ParseErrorRecord(
                    line_no=line_no,
                    raw=raw_line.rstrip("\n"),
                    reason=str(exc),
                )
            )
            continue

        fragment.entries.append(entry)
    return fragment


def parse_fragment_file(path: Path | str) -> Fragment:
    """Parse a fragment *file* on disk into a :class:`Fragment`."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return parse_fragment_text(text, name=path.name)


def load_directory(directory: Path | str) -> list[Fragment]:
    """Load every ``*.fragment`` file in *directory*, sorted by name.

    Unknown files are ignored so a fragments directory may coexist with other
    material (READMEs, profiles, ...) without the parser choking on them.
    """
    directory = Path(directory)
    fragments = []
    for path in sorted(directory.rglob("*.fragment")):
        if path.is_file():
            fragments.append(parse_fragment_file(path))
    return fragments
