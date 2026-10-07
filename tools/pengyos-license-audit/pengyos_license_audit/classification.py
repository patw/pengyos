"""SPDX-ish license expression parsing + family classification.

This module implements the SPDX-id → family mapping that the freedom policy
reasons about. It:

* **parses** *SPDX-ish* license expressions — a single SPDX identifier,
  optionally followed by a ``WITH <exception>`` clause, or combined with the
  boolean operators ``OR`` / ``AND`` and (for grouping) parentheses; and
* **classifies** each claim into a :class:`LicenseFamily` from
  :mod:`pengyos_license_audit.families` (permissive / weak-copyleft /
  strong-copyleft / proprietary / unknown).

The family *vocabulary* lives in :mod:`.families`; the SPDX-id → family
*mapping* lives here. Keeping the two apart means the policy schema has a
fixed vocabulary to operate over while the mapping stays in one place.

The parser is deliberately "SPDX-ish": it does not validate identifiers against
the full SPDX registry, but it does understand the grammar that real manifests
use (a single id, a ``WITH`` exception, and ``OR``/``AND`` grouping). See
:mod:`pengyos_license_audit.families` and ``fixtures/pins.toml`` for the shapes
in the wild (e.g. ``GPL-2.0-only WITH Linux-syscall-note``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence, Union

from .errors import ValidationError
from .families import FAMILY_ORDER, LicenseFamily

__all__ = [
    "classify",
    "classify_expression",
    "LicenseExpression",
    "parse_expression",
    "SPDX_FAMILY_BY_ID",
]

# ---------------------------------------------------------------------------
# SPDX id → family mapping
# ---------------------------------------------------------------------------
#
# The canonical SPDX identifiers we recognise, grouped by family. Any id that is
# not present maps to ``UNKNOWN`` (see :func:`classify`). Keep this table
# alphabetised per family and de-duplicated; it is the single source of truth
# for classification.

_PERMISSIVE: tuple[str, ...] = (
    "0BSD",
    "AFL-3.0",
    "Apache-2.0",
    "Artistic-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD-4-Clause",
    "ISC",
    "MIT",
    "MIT-0",
    "Unlicense",
    "WTFPL",
    "X11",
    "Zlib",
)

_WEAK_COPYLEFT: tuple[str, ...] = (
    # LGPL family — a *linking* exception keeps downstream code from being
    # pulled in, so it is weak copyleft. EUPL likewise permits parallel
    # licensing.
    "EUPL-1.1",
    "EUPL-1.2",
    "LGPL-2.0-only",
    "LGPL-2.0-later",
    "LGPL-2.1-only",
    "LGPL-2.1-later",
    "LGPL-3.0-only",
    "LGPL-3.0-later",
)

_STRONG_COPYLEFT: tuple[str, ...] = (
    # GPL family — derivative works must carry the same license. AGPL adds a
    # network-use clause, so it is at least as strong as plain GPL.
    "AGPL-3.0-only",
    "AGPL-3.0-later",
    "GPL-1.0-only",
    "GPL-1.0-later",
    "GPL-2.0-only",
    "GPL-2.0-later",
    "GPL-3.0-only",
    "GPL-3.0-later",
    "OSL-3.0",
)

# Proprietary / closed-source markers. Note that the SPDX id for a proprietary
# license is literally "Proprietary"; we also accept a few common synonyms so a
# manifest that writes "Commercial" or "All-Rights-Reserved" classifies the same
# way rather than silently landing in UNKNOWN.
_PROPRIETARY: tuple[str, ...] = (
    "All-Rights-Reserved",
    "Commercial",
    "Proprietary",
)

# The canonical family for each recognised id. Keys are upper-cased so lookup
# is case-insensitive (SPDX ids are technically case-sensitive, but a
# "SPDX-ish" classifier should treat "apache-2.0" == "Apache-2.0").
_SPDX_FAMILY_BY_ID: dict[str, LicenseFamily] = {
    id_.upper(): LicenseFamily.PERMISSIVE for id_ in _PERMISSIVE
}
_SPDX_FAMILY_BY_ID.update(
    {id_.upper(): LicenseFamily.WEAK_COPYLEFT for id_ in _WEAK_COPYLEFT}
)
_SPDX_FAMILY_BY_ID.update(
    {id_.upper(): LicenseFamily.STRONG_COPYLEFT for id_ in _STRONG_COPYLEFT}
)
_SPDX_FAMILY_BY_ID.update(
    {id_.upper(): LicenseFamily.PROPRIETARY for id_ in _PROPRIETARY}
)

# The public, read-only view of the mapping (canonical SPDX id → family value
# string). Built from the source tuples so ids keep their canonical casing.
SPDX_FAMILY_BY_ID: dict[str, str] = {
    id_: LicenseFamily.PERMISSIVE.value for id_ in _PERMISSIVE
}
SPDX_FAMILY_BY_ID.update(
    {id_: LicenseFamily.WEAK_COPYLEFT.value for id_ in _WEAK_COPYLEFT}
)
SPDX_FAMILY_BY_ID.update(
    {id_: LicenseFamily.STRONG_COPYLEFT.value for id_ in _STRONG_COPYLEFT}
)
SPDX_FAMILY_BY_ID.update(
    {id_: LicenseFamily.PROPRIETARY.value for id_ in _PROPRIETARY}
)

# A single SPDX identifier: letters, digits, dots and hyphens (e.g.
# "Apache-2.0", "LGPL-2.1-only"). The WITH exception clause is a separate token.
_ID_RE = re.compile(r"^[A-Za-z0-9.\-]+$")


# ---------------------------------------------------------------------------
# Parsed expression AST
# ---------------------------------------------------------------------------
#
# These are the internal nodes of a parsed expression. They are exposed (but
# not exported in ``__all__``) so tests and callers can inspect a parse tree if
# they need to; treat them as an implementation detail.

@dataclass(frozen=True)
class _License:
    """A single SPDX identifier, optionally with a ``WITH <exception>`` clause."""

    id: str
    exception: str | None = None


@dataclass(frozen=True)
class _BinaryOp:
    """A binary boolean combination (``OR`` / ``AND``) of two sub-expressions."""

    op: str  # "OR" | "AND"
    left: "LicenseExpression"
    right: "LicenseExpression"


# The public alias for a parsed expression: either a leaf license or a binary
# combination of them.
LicenseExpression = Union["_License", "_BinaryOp"]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _tokenize(expr: str) -> list[str]:
    """Split *expr* into SPDX tokens, validating each one.

    Parentheses may be glued to neighbouring tokens (``(MIT``, ``2.0)``), so we
    first surround them with spaces before splitting. Raises
    :class:`ValidationError` on a malformed token (not an identifier and not a
    recognised operator / parenthesis).
    """
    spaced = re.sub(r"([()])", r" \1 ", expr)
    tokens = spaced.split()
    validated: list[str] = []
    for tok in tokens:
        if tok in ("OR", "AND", "WITH", "(", ")"):
            validated.append(tok)
        elif _ID_RE.match(tok):
            validated.append(tok)
        else:
            raise ValidationError(
                f"invalid SPDX token {tok!r} in license expression {expr!r}"
            )
    return validated


def parse_expression(expr: str) -> LicenseExpression:
    """Parse an SPDX-ish license expression into an AST.

    Understands:

    * a single identifier, e.g. ``"MIT"``;
    * an identifier with a ``WITH`` exception, e.g.
      ``"GPL-2.0-only WITH Linux-syscall-note"``;
    * ``OR`` / ``AND`` combinations, e.g. ``"MIT OR Apache-2.0"``;
    * parentheses for grouping, e.g. ``"(MIT OR Apache-2.0) AND ISC"``.

    Returns a :class:`_License` leaf or nested :class:`_BinaryOp` nodes. Raises
    :class:`ValidationError` on malformed input (bad token, unbalanced
    parentheses, or a dangling operator).
    """
    tokens = _tokenize(expr)
    value, rest = _parse_or(tokens, 0)
    if rest != len(tokens):
        raise ValidationError(
            f"unexpected trailing token in license expression {expr!r}"
        )
    return value


def _parse_or(tokens: Sequence[str], i: int) -> tuple[LicenseExpression, int]:
    node, i = _parse_and(tokens, i)
    while i < len(tokens) and tokens[i] == "OR":
        right, i = _parse_and(tokens, i + 1)
        node = _BinaryOp("OR", node, right)
    return node, i


def _parse_and(tokens: Sequence[str], i: int) -> tuple[LicenseExpression, int]:
    node, i = _parse_atom(tokens, i)
    while i < len(tokens) and tokens[i] == "AND":
        right, i = _parse_atom(tokens, i + 1)
        node = _BinaryOp("AND", node, right)
    return node, i


def _parse_atom(tokens: Sequence[str], i: int) -> tuple[LicenseExpression, int]:
    if i >= len(tokens):
        raise ValidationError("unexpected end of license expression")
    tok = tokens[i]
    if tok == "(":
        inner, j = _parse_or(tokens, i + 1)
        if j >= len(tokens) or tokens[j] != ")":
            raise ValidationError(
                f"unbalanced parentheses in license expression"
            )
        return inner, j + 1
    if tok in ("OR", "AND", ")"):
        raise ValidationError(
            f"unexpected {tok!r} in license expression"
        )
    # A single identifier.
    ident = tok
    exception = None
    j = i + 1
    if j < len(tokens) and tokens[j] == "WITH":
        if j + 1 >= len(tokens):
            raise ValidationError(
                f"WITH clause is missing its exception in license expression"
            )
        exception = tokens[j + 1]
        j += 2
    return _License(id=ident, exception=exception), j


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _family_of_id(ident: str) -> LicenseFamily:
    """Return the family for a single SPDX identifier (case-insensitive)."""
    key = ident.strip().upper()
    family = _SPDX_FAMILY_BY_ID.get(key)
    return family if family is not None else LicenseFamily.UNKNOWN


def _combine(op: str, families: Sequence[LicenseFamily]) -> LicenseFamily:
    """Combine operand families under an ``OR`` / ``AND`` operator.

    * ``OR`` — the *freest* family wins (you may pick the most permissive
      operand), i.e. the minimum over the freedom ordering.
    * ``AND`` — the *least free* family wins (you must satisfy every operand),
      i.e. the maximum over the freedom ordering.
    """
    if not families:
        return LicenseFamily.UNKNOWN
    ordered = sorted(families, key=FAMILY_ORDER.index)
    return ordered[0] if op == "OR" else ordered[-1]


def _classify_node(node: LicenseExpression) -> LicenseFamily:
    if isinstance(node, _BinaryOp):
        families = [
            _classify_node(node.left),
            _classify_node(node.right),
        ]
        return _combine(node.op, families)
    # Leaf license: classify the base id (ignore the WITH exception clause —
    # the exception does not change the freedom family).
    assert isinstance(node, _License)
    return _family_of_id(node.id)


def classify_expression(claim: str) -> LicenseFamily:
    """Classify a full SPDX-ish license *expression* into a single family.

    The expression is parsed and each operand classified; ``OR``/``AND`` are
    combined over the freedom ordering (see :func:`_combine`). Raises
    :class:`ValidationError` on malformed input.
    """
    node = parse_expression(claim)
    return _classify_node(node)


def classify(claim: str) -> LicenseFamily:
    """Classify a single license *claim* into a :class:`LicenseFamily`.

    This is the convenience entry point used by the CLI: it normalises the
    claim (trim, collapse whitespace, drop a parenthetical note) and then
    classifies it. A claim that is not a valid SPDX expression raises
    :class:`ValidationError`; an unmapped but well-formed claim maps to
    :class:`LicenseFamily.UNKNOWN`.
    """
    if not isinstance(claim, str):
        raise ValidationError("license claim must be a string")
    normalised = _normalise(claim)
    if not normalised:
        raise ValidationError("license claim is empty")
    return classify_expression(normalised)


def _normalise(claim: str) -> str:
    """Normalise a raw license claim for classification.

    Trims, collapses internal whitespace, and strips a trailing
    parenthetical note (``"MIT (MIT License)"`` → ``"MIT"``).
    """
    text = claim.strip()
    # Drop a parenthetical note, e.g. "GPL-2.0-only WITH Linux-syscall-note (with exceptions)".
    if "(" in text:
        text = text.split("(", 1)[0].strip()
    text = re.sub(r"\s+", " ", text)
    return text
