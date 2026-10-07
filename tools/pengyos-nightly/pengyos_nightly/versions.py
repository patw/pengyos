"""Version comparison and routine-vs-major classification."""
from __future__ import annotations

import re

_PART = re.compile(r"\d+|[A-Za-z]+")


def strip_prefix(version: str) -> str:
    """'epoch-1.9.0' / 'v1.9.0' -> '1.9.0' (tags and versions mix the two)."""
    return re.sub(r"^(epoch-|v(?=\d))", "", version.strip())


def vkey(version: str) -> tuple:
    """A sort key for dotted versions: numbers compare as numbers, and a
    pre-release suffix (rc, beta, alpha) sorts before the release."""
    key = []
    for part in _PART.findall(strip_prefix(version)):
        if part.isdigit():
            key.append((1, int(part), ""))
        else:
            key.append((0, 0, part.lower()))
    key.append((1, -1, ""))  # "7.2" > "7.2-rc5": the release outranks its rcs
    return tuple(key)


def is_prerelease(version: str) -> bool:
    return bool(re.search(r"(rc|alpha|beta|pre|dev)\d*", version, re.I))


def numeric(version: str) -> list[int]:
    return [int(p) for p in re.findall(r"\d+", strip_prefix(version))]


def bump_kind(current: str, latest: str) -> str:
    """"routine" when the release series is unchanged (7.2.5 -> 7.2.9,
    1.2.16 -> 1.2.16.1: everything but the last component of the current
    version, and at least major.minor, matches), "major" otherwise, "none"
    when latest is not newer."""
    if vkey(latest) <= vkey(current):
        return "none"
    cur, new = numeric(current), numeric(latest)
    n = max(len(cur) - 1, 2)
    if len(cur) >= 2 and len(new) >= n and cur[:n] == new[:n]:
        return "routine"
    return "major"
