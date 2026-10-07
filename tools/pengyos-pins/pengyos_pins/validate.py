"""Semantic validation of parsed ``pins.toml`` documents.

The schema layer (``pengyos_pins.schema``) guarantees the *shape* of a
component: the required fields are present, they are strings, they are
non-empty, the ``name`` matches ``[a-z0-9][a-z0-9_-]*`` and is unique, and no
unknown fields are tolerated.  This layer enforces the *semantic* rules that the
schema deliberately leaves to a dedicated validation pass:

* **SPDX license syntax** — the ``license`` field must be a well-formed SPDX
  license *expression*: a single SPDX identifier, or several joined by the
  ``AND`` / ``OR`` operators, optionally combined with ``WITH`` (license
  exceptions), parentheses for grouping, and a trailing ``+`` marking
  "or-later".
* **Hash format** — the ``sha256`` field must be a 64-character lowercase hex
  digest of the release tarball.

The ``validate`` CLI subcommand runs the whole pipeline: :func:`load_pins`
(required fields, via the schema loader) then :func:`validate_pins` (these
semantic rules).  Each public validator returns a list of human-readable error
messages — an empty list means the input is valid — so callers can report every
problem at once instead of failing on the first.
"""

from __future__ import annotations

import re
from typing import Sequence

from .schema import Component, Pins

#: A sha256 digest: exactly 64 lowercase hexadecimal characters.
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: A single SPDX license identifier.  SPDX IDs are alphanumeric with ``.`` and
#: ``-`` (e.g. ``GPL-2.0-only``, ``BSD-3-Clause``, ``Classpath-exception-2.1``).
#: The or-later ``+`` marker is *not* part of the identifier — it is stripped by
#: the parser before this is checked, so a bare ``+`` (or one in the middle) is
#: rejected.
_SPDX_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")


# --------------------------------------------------------------------------- #
# SPDX expression grammar
# --------------------------------------------------------------------------- #
#
# The grammar we accept is the well-formed-fragment subset of the SPDX
# specification:
#
#   spdx      := or_expr
#   or_expr   := and_expr ( "OR"  and_expr )*
#   and_expr  := with_expr ( "AND" with_expr )*
#   with_expr := primary ( "WITH" primary )?
#   primary   := "(" spdx ")" | license
#   license   := identifier ("+")?
#
# Operators are case-sensitive and must be whitespace-delimited, exactly as the
# SPDX spec requires.  A recursive-descent parser validates a document without
# needing a copy of the (very long) SPDX identifier list, so the check stays
# self-contained and network-free.


def _tokenize(expression: str) -> list[str]:
    """Split an SPDX expression into tokens, keeping parentheses separate.

    ``"MIT AND (GPL-2.0-or-later WITH Classpath-exception-2.1)"`` becomes
    ``["MIT", "AND", "(", "GPL-2.0-or-later", "WITH", "Classpath-exception-2.1",
    ")"]``.  The token body deliberately excludes parentheses so a trailing
    ``)`` is never swallowed into the preceding identifier.
    """
    return re.findall(r"[()]|[^()\s]+", expression)


class _SpdxParser:
    """A tiny recursive-descent parser for the SPDX expression grammar above."""

    def __init__(self, tokens: Sequence[str]) -> None:
        self._tokens = list(tokens)
        self._pos = 0

    def _peek(self) -> str | None:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _advance(self) -> str | None:
        token = self._peek()
        self._pos += 1
        return token

    def parse(self) -> bool:
        """Parse the whole token stream; succeed only if it is fully consumed."""
        if not self._parse_or():
            return False
        return self._peek() is None

    def _parse_or(self) -> bool:
        if not self._parse_and():
            return False
        while self._peek() == "OR":
            self._advance()
            if not self._parse_and():
                return False
        return True

    def _parse_and(self) -> bool:
        if not self._parse_with():
            return False
        while self._peek() == "AND":
            self._advance()
            if not self._parse_with():
                return False
        return True

    def _parse_with(self) -> bool:
        if not self._parse_primary():
            return False
        if self._peek() == "WITH":
            self._advance()
            if not self._parse_primary():
                return False
        return True

    def _parse_primary(self) -> bool:
        token = self._peek()
        if token is None:
            return False
        if token == "(":
            self._advance()
            if not self._parse_or():
                return False
            if self._peek() != ")":
                return False
            self._advance()
            return True
        # A license identifier with an optional trailing or-later "+".
        self._advance()
        body = token[:-1] if token.endswith("+") else token
        return bool(_SPDX_ID_RE.match(body))


def validate_spdx(expression: str) -> list[str]:
    """Return error messages for an invalid SPDX expression (empty = valid)."""
    if not expression or not expression.strip():
        return ["license must be a non-empty SPDX expression"]
    if not _SpdxParser(_tokenize(expression)).parse():
        return [
            f"license '{expression}' is not a valid SPDX expression "
            "(expected SPDX identifiers joined by AND/OR/WITH, with optional "
            "parentheses and a trailing + for or-later)"
        ]
    return []


# --------------------------------------------------------------------------- #
# sha256 hash format
# --------------------------------------------------------------------------- #


def validate_sha256(value: str) -> list[str]:
    """Return error messages for an invalid sha256 digest (empty = valid)."""
    if not SHA256_RE.match(value):
        return [
            f"sha256 '{value}' is not a 64-character lowercase hex digest "
            "(expected ^[0-9a-f]{64}$)"
        ]
    return []


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def validate_component(component: Component) -> list[str]:
    """Validate every semantic rule for a single :class:`Component`.

    Returns the list of error messages; an empty list means the component is
    valid.  The required-field, string-typing, non-empty, name and uniqueness
    rules are enforced earlier by the schema loader, so this covers only the
    semantic rules (SPDX + hash).
    """
    errors = validate_spdx(component.license)
    errors += validate_sha256(component.sha256)
    return errors


def validate_pins(pins: Pins) -> list[str]:
    """Validate a whole :class:`Pins` document.

    Returns the list of error messages across all components — an empty list
    means the document is valid.  Each message is prefixed with
    ``component[i]:`` so a caller can report every problem at once.
    """
    errors: list[str] = []
    for index, component in enumerate(pins.components):
        for message in validate_component(component):
            errors.append(f"component[{index}]: {message}")
    return errors
