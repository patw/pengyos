"""Ignore-pattern matching and known-nondeterminism heuristics.

It defines how
``--ignore PATTERN`` globs are matched against each artifact's relative POSIX
path, and provides a small catalogue of *heuristic* starter patterns for the
classic sources of non-reproducible output: build timestamps, embedded build
user, and random seeds.

Why this exists
---------------
Reproducibility diffs are noisy. Two builds of the *same* pinned inputs very
often differ in files that are nondeterministic by construction — Python
``.pyc`` bytecode embeds a timestamp, an ``.a``/``.so`` archive embeds the
building user's uid/gid, a ``random-seed`` file is, well, random. Those
differences are *expected*; hiding them behind configurable ignore patterns lets
the reproducibility score reflect the *real* divergence rather than the noise.

The matching semantics defined here are the contract ``spec.md`` leaves to this
subtask ("The exact match semantics ... are defined by the ignore-patterns
subtask"). The next subtask (report emission) filters manifests with this module
*before* classification so ignored paths leave both the report and the score.

Design
------
* **Pure and dependency-free.** Everything here is standard library; the matcher
  is a thin glob → regex translation plus a predicate. No filesystem I/O.
* **Configurable.** Patterns are plain globs. The ``heuristic:<name>`` aliases
  are just a convenient, documented way to opt into a curated set — nothing is
  forced on the caller.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Tuple

__all__ = [
    "PatternError",
    "Matcher",
    "expand",
    "filter_manifest",
    "matches_any",
    "explain_ignore",
    "strip_metadata_patterns",
    "HEURISTIC_TIMESTAMPS",
    "HEURISTIC_EMBEDDED_USER",
    "HEURISTIC_RANDOM_SEEDS",
    "HEURISTICS",
]


class PatternError(ValueError):
    """A pattern could not be compiled (e.g. an unterminated ``[``)."""


# --- glob translation -------------------------------------------------------


def _translate(pattern: str) -> str:
    """Translate a glob *pattern* into a regex fragment for a POSIX path.

    Wildcards are path-segment aware:

    * ``*`` matches any run of characters **except** ``/``.
    * ``**`` matches any run **including** ``/`` (recurses across directories).
    * ``?`` matches a single character except ``/``.
    * ``[...]`` is a character class (``!`` negates; a leading ``]`` is literal).

    The ``**`` handling follows the common glob convention (as in Python's
    ``glob``/``pathlib``): ``**/`` at the start matches zero or more leading
    directories, ``/**`` at the end matches everything beneath, and a bare
    ``**`` matches anything.
    """
    i, n = 0, len(pattern)
    out: List[str] = []
    while i < n:
        c = pattern[i]
        if c == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            stars = j - i
            prev = pattern[i - 1] if i > 0 else ""
            nxt = pattern[j] if j < n else ""
            if stars >= 2:
                if prev == "/" and nxt == "/":
                    # '/**/' -> '/( */)?'  (the leading '/' is already emitted)
                    out.append("(.*/)?")
                    j += 1  # consume the trailing slash
                elif prev == "/" and nxt == "":
                    # '/**' -> '/.*'
                    out.append(".*")
                elif prev == "" and nxt == "/":
                    # '**/' -> '( */)?'
                    out.append("(.*/)?")
                    j += 1
                else:
                    # '**' squeezed between non-slash chars — treat as "anything"
                    out.append(".*")
                i = j
            else:
                out.append("[^/]*")
                i = j
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            frag, i = _translate_class(pattern, i)
            out.append(frag)
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _translate_class(pattern: str, start: int) -> Tuple[str, int]:
    """Translate a ``[...]`` character class beginning at ``pattern[start]``.

    Returns the regex fragment and the index just past the closing ``]``.
    Raises :class:`PatternError` if the class is unterminated.
    """
    n = len(pattern)
    j = start + 1
    # optional leading negation
    if j < n and pattern[j] in "!^":
        j += 1
    # a ] right after the opening [ (or its negation) is a literal ]
    if j < n and pattern[j] == "]":
        j += 1
    while j < n and pattern[j] != "]":
        j += 1
    if j >= n:
        raise PatternError(
            f"unterminated character class in pattern {pattern!r}"
        )
    inner = pattern[start + 1 : j]
    if inner.startswith("!"):
        inner = "^" + inner[1:]
    # Inside a class only backslashes are special to the regex engine; leave
    # ranges such as ``a-z`` intact so they keep their meaning.
    inner = inner.replace("\\", "\\\\")
    return "[" + inner + "]", j + 1


def _compile_one(pattern: str) -> "re.Pattern[str]":
    """Compile a single *pattern* into an anchored regex.

    A pattern with no ``/`` is a *basename* match (it matches the file's
    basename at any depth). A pattern with a ``/`` is *anchored* to the root and
    matched against the full relative path. A trailing ``/`` marks *directory
    pruning*: the pattern matches that directory and everything beneath it.
    """
    if not pattern:
        raise PatternError("empty ignore pattern")
    dir_prune = pattern.endswith("/")
    core = pattern.rstrip("/")
    if not core:
        raise PatternError(f"pattern is only slashes: {pattern!r}")
    if "/" in core:
        regex = "^" + _translate(core) + "$"
    else:
        regex = "^(?:.*/)?" + _translate(core) + "$"
    return re.compile(regex)


# --- matcher ----------------------------------------------------------------


class Matcher:
    """A compiled set of ignore patterns.

    ``Matcher(["*.so", "build/"])`` precompiles the patterns (expanding any
    ``heuristic:`` aliases) once and offers a fast :meth:`is_ignored`
    predicate. Build one per invocation, not per file.
    """

    def __init__(self, patterns: Iterable[str]) -> None:
        self._patterns: List[str] = list(expand(patterns))
        # (compiled regex, original pattern) pairs so directory-pruning
        # semantics can be recovered per rule.
        self._rules: List[Tuple["re.Pattern[str]", str]] = [
            (_compile_one(p), p) for p in self._patterns
        ]

    def is_ignored(self, relpath: str) -> bool:
        """Return ``True`` if ``relpath`` matches any ignore pattern."""
        for rx, pat in self._rules:
            if _matches(rx, relpath, pat):
                return True
        return False

    def __len__(self) -> int:
        return len(self._rules)


def _matches(rx: "re.Pattern[str]", relpath: str, pattern: str) -> bool:
    """Apply one compiled matcher, honouring directory-pruning semantics."""
    if not pattern.endswith("/"):
        return rx.match(relpath) is not None
    # Directory pruning: the pattern matches the path itself or any ancestor
    # directory of it (i.e. the path lives beneath a matched directory).
    parts = relpath.split("/")
    for k in range(1, len(parts) + 1):
        if rx.match("/".join(parts[:k])) is not None:
            return True
    return False


def matches_any(patterns: Iterable[str], relpath: str) -> bool:
    """Convenience: does ``relpath`` match any of *patterns*?"""
    return Matcher(patterns).is_ignored(relpath)


# --- expansion + filtering --------------------------------------------------


def expand(patterns: Iterable[str]) -> List[str]:
    """Expand ``heuristic:<name>`` pseudo-patterns into concrete globs.

    Recognised names live in the :data:`HEURISTICS` registry
    (``timestamps``, ``embedded-user``, ``random-seeds``). Any pattern that is
    not a ``heuristic:`` alias is returned unchanged. Raises
    :class:`PatternError` for an unknown alias.
    """
    out: List[str] = []
    for p in patterns:
        if p.startswith("heuristic:"):
            name = p[len("heuristic:") :].strip().lower()
            if name not in HEURISTICS:
                known = ", ".join(sorted(HEURISTICS)) or "(none)"
                raise PatternError(
                    f"unknown heuristic {name!r}; known: {known}"
                )
            out.extend(HEURISTICS[name])
        else:
            out.append(p)
    return out


def filter_manifest(
    manifest: Mapping[str, Any], patterns: Iterable[str]
) -> Dict[str, Any]:
    """Return ``manifest`` with every ignored path removed.

    ``manifest`` maps a relative POSIX path to anything (typically a
    :class:`~pengyos_repro.manifest.FileEntry`). Paths matching any *patterns*
    (after ``heuristic:`` expansion) are dropped. Call this on the manifests
    *before* classification so ignored paths leave both the report and the score.
    """
    matcher = Matcher(patterns)
    return {p: v for p, v in manifest.items() if not matcher.is_ignored(p)}


def explain_ignore(relpath: str, patterns: Iterable[str]) -> str | None:
    """Return the name of the first ignore pattern that matches ``relpath``.

    This is the inverse predicate used by the ``--strip-metadata`` second pass to
    annotate *why* a path was explained away:

    * a ``heuristic:<name>`` alias that matches reports as ``"heuristic:<name>"``
      (the alias, not the expanded glob), so the report reads
      ``heuristic:timestamps`` rather than ``*.pyc``;
    * a plain glob reports as the literal pattern (e.g. ``"*.tmp"``).

    Returns ``None`` when nothing matches. Raises :class:`PatternError` for an
    unknown ``heuristic:`` alias — the same validation as :func:`expand`.

    A heuristic alias short-circuits on its *first* matching glob: the moment
    ``heuristic:timestamps`` explains a path, that is the answer, before any
    later pattern is consulted.
    """
    for p in patterns:
        if p.startswith("heuristic:"):
            name = p[len("heuristic:") :].strip().lower()
            if name not in HEURISTICS:
                known = ", ".join(sorted(HEURISTICS)) or "(none)"
                raise PatternError(
                    f"unknown heuristic {name!r}; known: {known}"
                )
            for glob in HEURISTICS[name]:
                if matches_any([glob], relpath):
                    return f"heuristic:{name}"
        elif matches_any([p], relpath):
            return p
    return None


def strip_metadata_patterns(
    extra: Iterable[str] | None = None,
) -> List[str]:
    """Return the user's ``--ignore`` patterns plus every curated heuristic.

    This is the effective ignore list for the ``--strip-metadata`` second pass:
    the caller's patterns (which may themselves contain ``heuristic:`` aliases)
    on top of the full set of nondeterminism starter patterns. The curated
    heuristics catch metadata that leaks into a file's *content* (bytecode
    timestamps, embedded uid/gid, random seed bytes) — differences the raw
    content-hash comparison cannot otherwise explain.

    ``extra`` is prepended (so the caller's patterns are consulted first); it is
    never mutated.
    """
    base: List[str] = []
    base.extend(expand(extra or ()))
    for name in ("timestamps", "embedded-user", "random-seeds"):
        base.extend(HEURISTICS[name])
    return base


# --- heuristic starter patterns ---------------------------------------------
#
# These are *opt-in* starting points, not defaults. They target the classic
# sources of non-reproducible output and are expanded from ``heuristic:<name>``
# ignore patterns. Tune them to your build — the whole point is that they are
# configurable. The embedded-user set deliberately targets archives/object
# files (ar/ELF/zip), which are where uid/gid + mtime metadata tends to leak.

HEURISTIC_TIMESTAMPS: List[str] = [
    "*.pyc",  # Python bytecode embeds the source mtime
    "**/*.pyc",
    "**/__pycache__/**",
]

HEURISTIC_EMBEDDED_USER: List[str] = [
    "**/*.o",  # object files / ar archives embed uid + gid + mtime
    "**/*.a",
    "**/*.so",
    "**/*.ko",
    "**/*.jar",  # zip-based: embeds timestamps + uid/gid
    "**/*.zip",
    "**/*.tar",
]

HEURISTIC_RANDOM_SEEDS: List[str] = [
    "**/random-seed*",
    "**/urandom-seed*",
    "**/.random-seed*",
]

# Registry keyed by the name usable as ``heuristic:<name>``.
HEURISTICS: Dict[str, List[str]] = {
    "timestamps": HEURISTIC_TIMESTAMPS,
    "embedded-user": HEURISTIC_EMBEDDED_USER,
    "random-seeds": HEURISTIC_RANDOM_SEEDS,
}
