"""Matching of loaded OSV advisories against pinned components.

This is the core of the "advisories → proposals" pipeline: given a :class:`Pin` (``name`` + ``version``) and an :class:`Advisory`
(its ``affected[]`` entries), decide whether the pinned version is *affected*,
and always produce a human-readable piece of *evidence* explaining the
decision — the rationale chain of the later classification subtask builds on
these strings.

Rules (documented here and in spec.md)
--------------------------------------
* **Name** — an ``affected`` entry names the component if its package ``name``
  equals the pin's ``name`` *or* any of the pin's ``aliases`` (advisories often
  use the upstream project name where the distro pins the package name, or
  vice versa).
* **Ecosystem** — a pin with the default ecosystem ``"generic"`` matches any
  advisory ecosystem, and an advisory with no ecosystem matches any pin. But
  when *both* sides carry a specific (non-empty, non-``"generic"``) ecosystem
  and they differ, the entry is skipped: e.g. an Alpine advisory must not
  affect a PyPI pin of the same name.
* **Version** — a pin is affected if its version is *exactly listed* in the
  entry's ``versions[]`` **or** falls inside one of the ``ranges[]``. Range
  events follow the OSV semantics:

  * ``introduced: X`` — the vulnerable interval starts **inclusive** at X; a
    second ``introduced`` closes the previous interval just before it
    (exclusive); ``"0"`` is the conventional "from the beginning".
  * ``vulnerable: X`` — exactly version X (a point interval).
  * ``fixed: X`` — the interval ends **exclusive** of X (X is not vulnerable;
    X is also recorded as a *suggested fix*).
  * ``last_affected: X`` — the interval ends **inclusive** of X (no fixed
    release exists, or is tracked).
  * An interval with no ``introduced`` is unbounded below.

Version comparison (``compare_versions``)
-----------------------------------------
A stdlib-only dotted-version comparison. Versions are tokenised into runs of
digits (compared numerically — so ``"9.1.0444"`` == ``"9.1.444"`` and
``"9"`` < ``"10"``) and runs of letters; a leading ``v``/``V`` is ignored and
build metadata after ``+`` is dropped. A pre-release segment (after ``-``)
sorts *below* the corresponding release (``1.0-rc1`` < ``1.0`` < ``1.0.1``).
A missing component counts as ``0`` (``"1.0"`` == ``"1.0.0"``). This is a
deliberately small comparator, not a full PEP 440 implementation; exotic
version schemes that it cannot order deterministically are still compared as
plain strings, which keeps matching total and reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .models import Advisory, Affected, Pin, Range, RangeEvent

__all__ = [
    "compare_versions",
    "version_in_ranges",
    "match_advisory",
    "match_all",
    "AdvisoryMatch",
]

# The ecosystem value meaning "no specific ecosystem" on a pin (the schema
# default); such pins match advisories from any ecosystem.
GENERIC_ECOSYSTEM = "generic"

_PAD = object()  # missing version component
_SEP = object()  # pre-release separator marker


def _cmp_parts(x: object, y: object) -> int:
    """Compare two version tokens (int, str, or the _PAD/_SEP sentinels)."""
    if x is _SEP:
        return 0 if y is _SEP else -1  # pre-release sorts below everything
    if x is _PAD:
        if y is _PAD:
            return 0
        if isinstance(y, int):
            return -1 if y > 0 else (1 if y < 0 else 0)
        return 1  # a missing component is a release, above any pre-release token
    if y is _PAD:
        if x is _SEP:
            return -1  # the other side has no pre-release: it is the release
        if isinstance(x, int):
            return 1 if x > 0 else (-1 if x < 0 else 0)
        return -1  # a pre-release token sorts below the (implicit) release
    if isinstance(x, int) and isinstance(y, int):
        return -1 if x < y else (1 if x > y else 0)
    if isinstance(x, str) and isinstance(y, str):
        return -1 if x < y else (1 if x > y else 0)
    # int vs str: the letter run (e.g. the "a" in "1.0.3a") sorts below a
    # numeric component, mirroring pre-release-below-release ordering.
    return 1 if isinstance(x, int) else -1


def _tokenize(text: str) -> "list":
    """Split a version string into comparable tokens (see module docstring)."""
    t = text.strip()
    if t[:1] in ("v", "V"):
        t = t[1:]
    plus = t.find("+")
    if plus != -1:
        t = t[:plus]  # build metadata is ignored, per semver
    core, pre = t, ""
    dash = t.find("-")
    if dash != -1:
        core, pre = t[:dash], t[dash + 1 :]
    tokens: "list" = [
        int(m.group()) if m.group().isdigit() else m.group()
        for m in re.finditer(r"\d+|[A-Za-z]+", core)
    ]
    if pre:
        tokens.append(_SEP)
        tokens.extend(
            int(m.group()) if m.group().isdigit() else m.group()
            for m in re.finditer(r"\d+|[A-Za-z]+", pre)
        )
    return tokens


def compare_versions(a: str, b: str) -> int:
    """Compare two version strings: ``-1`` if a<b, ``0`` if equal, ``1`` if a>b.

    See the module docstring for the exact comparison rules. Never raises on
    odd input: whatever tokens remain are compared, letter runs as strings, so
    the ordering is total and deterministic.
    """
    ta, tb = _tokenize(a), _tokenize(b)
    for i in range(max(len(ta), len(tb))):
        x = ta[i] if i < len(ta) else _PAD
        y = tb[i] if i < len(tb) else _PAD
        c = _cmp_parts(x, y)
        if c:
            return c
    return 0


# --- Range evaluation ---------------------------------------------------------


def _event_str(ev: RangeEvent) -> str:
    for key, attr in (
        ("introduced", "introduced"),
        ("fixed", "fixed"),
        ("last_affected", "last_affected"),
        ("vulnerable", "vulnerable"),
    ):
        value = getattr(ev, attr)
        if value is not None:
            return f"{key} {value}"
    return "?"  # unreachable: the schema requires at least one event field


@dataclass
class _Interval:
    """Half-open-ish interval: (start, start_inclusive, end, end_inclusive).

    ``None`` on either side means unbounded in that direction.
    """

    start: Optional[str]
    start_inclusive: bool
    end: Optional[str]
    end_inclusive: bool

    def contains(self, version: str) -> bool:
        if self.start is not None:
            c = compare_versions(version, self.start)
            if c < 0 or (c == 0 and not self.start_inclusive):
                return False
        if self.end is not None:
            c = compare_versions(version, self.end)
            if c > 0 or (c == 0 and not self.end_inclusive):
                return False
        return True

    def describe(self) -> str:
        lo = self.start if self.start is not None else "-∞"
        hi = self.end if self.end is not None else "+∞"
        left = "[" if self.start is not None and self.start_inclusive else "("
        right = "]" if self.end is not None and self.end_inclusive else ")"
        return f"{left}{lo}, {hi}{right}"


def _intervals(rng: Range) -> List[_Interval]:
    """Fold a range's ordered events into concrete version intervals.

    Event semantics (OSV): a second ``introduced`` closes the current interval
    exclusive of the new start; ``fixed``/``last_affected`` close it exclusive/
    inclusive of their value; ``vulnerable`` is a point interval.
    """
    intervals: List[_Interval] = []
    open_start: Optional[str] = None
    open_inclusive = False
    for ev in rng.events:
        if ev.introduced is not None:
            if open_start is not None:
                intervals.append(_Interval(open_start, open_inclusive, ev.introduced, False))
            open_start, open_inclusive = ev.introduced, True
        elif ev.vulnerable is not None:
            intervals.append(_Interval(ev.vulnerable, True, ev.vulnerable, True))
        elif ev.fixed is not None:
            intervals.append(_Interval(open_start, open_inclusive, ev.fixed, False))
            open_start = None
        elif ev.last_affected is not None:
            intervals.append(_Interval(open_start, open_inclusive, ev.last_affected, True))
            open_start = None
    if open_start is not None:
        intervals.append(_Interval(open_start, open_inclusive, None, False))
    return intervals


def version_in_ranges(version: str, ranges: List[Range]) -> Optional[str]:
    """Evidence string if ``version`` falls in any range, else ``None``.

    The returned string names the range type, its events and the concrete
    interval that contains the version, e.g.
    ``"range SEMVER (introduced 0, fixed 3.0.14): interval [0, 3.0.14) covers 3.0.13"``.
    """
    for rng in ranges:
        for interval in _intervals(rng):
            if interval.contains(version):
                events = ", ".join(_event_str(ev) for ev in rng.events)
                return (
                    f"range {rng.type} ({events}): interval "
                    f"{interval.describe()} covers {version}"
                )
    return None


# --- Advisory ↔ pin matching ---------------------------------------------------


@dataclass
class AdvisoryMatch:
    """The outcome of matching one advisory against one pinned component.

    ``matched`` means the advisory is *applicable* to the pin (a package entry
    names it, in a compatible ecosystem); ``affected`` means the pinned
    version is inside the affected set. ``evidence`` is always non-empty — it
    is the rationale seed for classification (item 5) and the ``explain``
    output (item 7).
    """

    pin: Pin
    advisory: Advisory
    matched: bool
    affected: bool
    package_name: str = ""
    evidence: List[str] = field(default_factory=list)
    suggested_fixes: List[str] = field(default_factory=list)

    @property
    def identifiers(self) -> List[str]:
        """The advisory's full set of source ids (id + aliases) for evidence."""
        return self.advisory.all_identifiers()

    def reason(self) -> str:
        """A single human-readable line summarising this match outcome."""
        ids = ", ".join(self.identifiers)
        if self.affected:
            head = (
                f"pinned {self.pin.name} {self.pin.version} is affected by "
                f"{ids} (package '{self.package_name}')"
            )
            return head + ": " + "; ".join(self.evidence)
        if self.matched:
            return (
                f"pinned {self.pin.name} {self.pin.version} is NOT affected by "
                f"{ids} (package '{self.package_name}'): " + "; ".join(self.evidence)
            )
        return f"advisory {ids} does not apply to pinned component '{self.pin.name}'"


def _ecosystem_compatible(pin: Pin, entry: Affected) -> bool:
    pin_eco = pin.ecosystem or ""
    adv_eco = entry.ecosystem or ""
    if pin_eco in ("", GENERIC_ECOSYSTEM) or adv_eco in ("", GENERIC_ECOSYSTEM):
        return True
    return pin_eco.lower() == adv_eco.lower()


def match_advisory(pin: Pin, advisory: Advisory) -> AdvisoryMatch:
    """Match one advisory against one pin (name + ecosystem + version range)."""
    applicable: List[Affected] = []
    skipped_eco: List[str] = []
    for entry in advisory.affected:
        if not pin.matches_name(entry.package_name):
            continue
        if _ecosystem_compatible(pin, entry):
            applicable.append(entry)
        else:
            skipped_eco.append(entry.package_name)

    if not applicable:
        evidence = [
            f"no affected entry names '{pin.name}' or any of its aliases"
            + (
                f" (entries for {sorted(set(skipped_eco))} skipped: "
                "ecosystem differs from pinned ecosystem "
                f"'{pin.ecosystem}')"
                if skipped_eco
                else ""
            )
        ]
        return AdvisoryMatch(
            pin=pin,
            advisory=advisory,
            matched=False,
            affected=False,
            evidence=evidence,
        )

    entry = applicable[0]
    evidence: List[str] = []
    version_evidence: List[str] = []

    # (a) Explicit version listings: exact string match.
    if pin.version in entry.versions:
        version_evidence.append(
            f"pinned version {pin.version} is explicitly listed in "
            f"affected.versions"
        )

    # (b) Range intervals.
    range_evidence = version_in_ranges(pin.version, entry.ranges)
    if range_evidence is not None:
        version_evidence.append(range_evidence)

    if version_evidence:
        evidence.append(
            f"package '{entry.package_name}' matches pinned component "
            f"'{pin.name}' (name or alias)"
        )
        evidence.extend(version_evidence)
        fixes: List[str] = []
        for rng in entry.ranges:
            for ev in rng.events:
                if ev.fixed is not None and ev.fixed not in fixes:
                    fixes.append(ev.fixed)
        return AdvisoryMatch(
            pin=pin,
            advisory=advisory,
            matched=True,
            affected=True,
            package_name=entry.package_name,
            evidence=evidence,
            suggested_fixes=fixes,
        )

    # Not affected: explain exactly what was checked.
    evidence.append(
        f"package '{entry.package_name}' matches pinned component "
        f"'{pin.name}' (name or alias), but pinned version {pin.version} "
        "is outside the affected set"
    )
    if entry.ranges:
        described = "; ".join(
            f"range {r.type}: "
            + ", ".join(iv.describe() for iv in _intervals(r))
            for r in entry.ranges
        )
        evidence.append(described)
    if entry.versions:
        evidence.append(
            "explicitly affected versions: " + ", ".join(entry.versions)
        )
    return AdvisoryMatch(
        pin=pin,
        advisory=advisory,
        matched=True,
        affected=False,
        package_name=entry.package_name,
        evidence=evidence,
    )


def match_all(pins: List[Pin], advisories: List[Advisory]) -> List[AdvisoryMatch]:
    """Cross-match every advisory against every pin.

    The result order is deterministic: advisories in the order given, pins in
    the order given — reproducible output is part of the contract.
    """
    out: List[AdvisoryMatch] = []
    for advisory in advisories:
        for pin in pins:
            out.append(match_advisory(pin, advisory))
    return out
