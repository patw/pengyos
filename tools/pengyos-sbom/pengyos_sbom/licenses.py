"""Convert Buildroot ``LICENSE`` strings into SPDX 2.3 license expressions.

Buildroot's ``_LICENSE`` variable is *not* an SPDX license expression. It is a
free-form, comma-separated list of license tokens where a top-level comma means
"one of these licenses applies" (logical OR), an ``&&`` means both are required
(logical AND), and the familiar ``+`` suffix means "-or-later". Examples::

    "GPL-2.0+"                    -> GPL-2.0-or-later
    "MIT, GPL-2.0+"               -> MIT OR GPL-2.0-or-later
    "LGPL-2.1+ && MPL-2.0"        -> (LGPL-2.1-or-later AND MPL-2.0)
    "GPL-2.0+ WITH Linux-syscall-note"-> GPL-2.0-or-later WITH
                                        Linux-syscall-note (both sides must be
                                        known SPDX ids, else a LicenseRef)
    "MegaCorp-Lic"                   -> LicenseRef-MEGACORP-LIC (declared in the
                                        document's hasExtractedLicensingInfos)

SPDX license expressions must be built from identifiers in the SPDX License
List (plus ``WITH`` / ``AND`` / ``OR`` / parentheses). Anything Buildroot names
that is not a known SPDX identifier (or a known ``<id> WITH <exception>``
pair) is emitted as an ``LicenseRef-<slug>`` and must be declared in the
document's ``hasExtractedLicensingInfos`` so the document stays self-consistent.

This module is deliberately pure (no I/O) so it is trivially testable and
deterministic: :class:`LicenseConverter` accumulates the ``LicenseRef``s it has
minted so the caller can declare them exactly once in the SPDX document.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# --- Known SPDX license identifiers (a curated, common subset) ------------
# SPDX 2.x license expressions use the ``-only`` / ``-or-later`` spellings, so
# the bare ``+`` suffix Buildroot uses is remapped by :data:`OR_LATER` below.
#: Exact identifiers accepted verbatim.
SPDX_LICENSES = frozenset(
    {
        # permissive
        "MIT",
        "MIT-0",
        "Zlib",
        "ISC",
        "Unlicense",
        "0BSD",
        "BSL-1.0",
        "BlueOak-1.0.0",
        "xinetd",
        "PostgreSQL",
        "Python-2.0",
        "PSF-2.0",
        "Ruby",
        "AFL-2.1",
        "AFL-3.0",
        "Artistic-1.0",
        "Artistic-2.0",
        "W3C",
        # BSD family
        "BSD-1-Clause",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "BSD-3-Clause-Clear",
        "BSD-4-Clause",
        "BSD-4-Clause-UC",
        # Apache / MPL / EPL / CDDL / AGPL / LGPL / GPL
        "Apache-1.0",
        "Apache-1.1",
        "Apache-2.0",
        "MPL-1.0",
        "MPL-1.1",
        "MPL-2.0",
        "EPL-1.0",
        "EPL-2.0",
        "CDDL-1.0",
        "CDDL-1.1",
        "LGPL-2.0-only",
        "LGPL-2.0-or-later",
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        "AGPL-1.0-only",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "GPL-1.0-only",
        "GPL-1.0-or-later",
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        # content / documentation
        "CC0-1.0",
        "CC-BY-3.0",
        "CC-BY-4.0",
        "CC-BY-SA-3.0",
        "CC-BY-SA-4.0",
        "OFL-1.1",
    }
)

#: Buildroot's ``+`` ("or any later version") suffix, remapped to SPDX's
#: ``-or-later`` identifier. Only entries that have a matching ``-or-later``
#: SPDX id are listed; a ``+`` token without a mapping falls through to a
#: ``LicenseRef`` (safe, self-consistent) rather than a wrong SPDX id.
OR_LATER = {
    "GPL-1.0+": "GPL-1.0-or-later",
    "GPL-2.0+": "GPL-2.0-or-later",
    "GPL-3.0+": "GPL-3.0-or-later",
    "LGPL-2.0+": "LGPL-2.0-or-later",
    "LGPL-2.1+": "LGPL-2.1-or-later",
    "LGPL-3.0+": "LGPL-3.0-or-later",
    "AGPL-1.0+": "AGPL-1.0-or-later",
    "AGPL-3.0+": "AGPL-3.0-or-later",
}

#: Known SPDX license-exception identifiers (the right-hand side of ``WITH``).
SPDX_EXCEPTIONS = frozenset(
    {
        "GCC-exception-2.0",
        "GCC-exception-3.1",
        "Autoconf-exception-2.0",
        "Autoconf-exception-3.0",
        "Bison-exception-2.2",
        "OCCT-exception-1.0",
        "Font-exception-2.0",
        "Classpath-exception-2.0",
        "Linux-man-pages-exception-2.1",
        "UPL-1.0",
        "Nokia-Qt-exception-1.1",
        "x11vnc-exception-1.0",
        "mif-exception",
    }
)

_WITH_RE = re.compile(r"^\s*(\S+)\s+WITH\s+(\S+)\s*$")
_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _split_top(s: str, delim: str) -> List[str]:
    """Split *s* on *delim* but only at parenthesis depth zero.

    Works for single- or multi-character delimiters (used for both ``","`` and
    ``"&&"``). Strips surrounding whitespace from each part.
    """
    parts: List[str] = []
    depth = 0
    last = 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth <= 0 and s.startswith(delim, i):
            parts.append(s[last:i].strip())
            i += len(delim)
            last = i
            continue
        i += 1
    parts.append(s[last:].strip())
    return parts


def _slugify(token: str) -> str:
    """Turn an arbitrary license token into a stable SPDX ref suffix.

    Uppercased, runs of non-alphanumerics collapsed to a single ``-`` (so
    ``GPL-2.0+`` -> ``GPL-2-0``), leading digit prefixed with ``L`` for
    readability.
    """
    slug = _SLUG_RE.sub("-", token.strip()).strip("-").upper()
    if not slug:
        slug = "UNKNOWN"
    if slug[0].isdigit():
        slug = "L" + slug
    return slug


class LicenseConverter:
    """Convert Buildroot license strings to SPDX expressions.

    Stateful only in that it remembers the ``LicenseRef-<slug>`` ids it has
    minted (and the original token each maps to) so the document can declare
    them in ``hasExtractedLicensingInfos`` without duplicates.
    """

    def __init__(self) -> None:
        self._refs: Dict[str, str] = {}

    def normalize_atom(self, token: str) -> str:
        """Map one license atom to an SPDX id, minting a LicenseRef if needed."""
        token = token.strip()
        norm = _normalize_atom(token)
        if norm is not None:
            return norm
        base = _slugify(token)
        ref = f"LicenseRef-{base}"
        # Disambiguate distinct tokens that slug to the same id.
        if self._refs.get(ref) != token:
            candidate = ref
            k = 2
            while candidate in self._refs and self._refs[candidate] != token:
                candidate = f"{ref}-{k}"
                k += 1
            ref = candidate
        self._refs[ref] = token
        return ref

    def convert(self, license_str: Optional[str]) -> str:
        """Convert a whole Buildroot ``LICENSE`` string to an SPDX expression.

        Empty / absent input yields ``NOASSERTION`` (SPDX's "no license
        information available" marker). Commas become ``OR`` (top-level,
        parenthesis-aware), ``&&`` becomes ``AND`` (grouped in parentheses when
        nested inside an ``OR``).
        """
        s = (license_str or "").strip()
        if not s:
            return "NOASSERTION"
        up = s.upper()
        if up in ("NONE", "NOASSERTION"):
            return up

        or_terms: List[str] = []
        for alt in _split_top(s, ","):
            if not alt:
                continue
            and_terms = [self.normalize_atom(a) for a in _split_top(alt, "&&") if a]
            if not and_terms:
                continue
            if len(and_terms) > 1:
                or_terms.append("(" + " AND ".join(and_terms) + ")")
            else:
                or_terms.append(and_terms[0])
        return " OR ".join(or_terms) if or_terms else "NOASSERTION"

    def extracted(self) -> List[Tuple[str, str]]:
        """The ``(licenseId, originalName)`` pairs minted so far, sorted."""
        return sorted(self._refs.items())


def _normalize_atom(token: str) -> Optional[str]:
    """Return the SPDX id for a token, or ``None`` if it is not recognizable.

    Handles bare known ids, ``+`` or-later suffixes, and ``<id> WITH <exception>``
    pairs. Everything else returns ``None`` so the caller mints a LicenseRef.
    """
    token = token.strip()
    if not token:
        return None
    m = _WITH_RE.match(token)
    if m:
        base = _normalize_atom(m.group(1))
        exc = m.group(2)
        if base is not None and exc in SPDX_EXCEPTIONS:
            return f"{base} WITH {exc}"
        return None
    if token in SPDX_LICENSES:
        return token
    if token in OR_LATER:
        return OR_LATER[token]
    return None
