"""Human-artistry rule enforcement.

The curated default-app set is **human-ratified, never AI-decided** (spec
Principle 8). Every curated entry must therefore carry a human-authored
``rationale`` and be ``human_signed = true``. This module enforces that
**artistry rule**: :func:`lint` walks a :class:`CuratedSet` and reports every
entry that is missing a rationale or is unsigned; :func:`lint_default` does so
against the committed ``curated.toml``; the CLI ``lint`` subcommand fails (exit
1) on any violation.

The loader (``pengyos_desktop_curation.data``) validates *structure* (required
fields, lane, ref); this module validates the *artistry rule* (a rationale is
present **and** a human signed it). The loader deliberately does not reject an
unsigned entry, so a data file can be edited with a missing signature and
caught here by ``lint`` rather than by the loader.

Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .data import CuratedEntry, CuratedError, CuratedSet, load_default


class ArtistryError(CuratedError):
    """A curated entry violates the human-artistry rule.

    Raised by :func:`assert_clean` (the strict gate) when a set is not clean;
    :func:`lint` itself never raises — it reports violations instead.
    """


@dataclass(frozen=True)
class LintViolation:
    """One artistry-rule violation against a single curated entry.

    ``reason`` is a short, stable string: ``"missing rationale"`` when the
    entry has no (non-empty) rationale, or ``"not human_signed"`` when a human
    has not ratified it.
    """

    index: int
    app: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        """Return the violation as a plain dict (stable key order)."""

        return {"index": self.index, "app": self.app, "reason": self.reason}


@dataclass(frozen=True)
class LintReport:
    """The result of :func:`lint`: every artistry-rule violation, in order.

    An empty set is clean (nothing violates the rule). :attr:`is_clean` is the
    single predicate the CLI ``lint`` subcommand gates its exit code on.
    """

    violations: tuple[LintViolation, ...] = field(default_factory=tuple)

    @property
    def is_clean(self) -> bool:
        """Whether every curated entry satisfies the artistry rule."""

        return not self.violations

    def as_dict(self) -> dict[str, object]:
        """Return the report as a plain dict (stable key order)."""

        return {
            "clean": self.is_clean,
            "violations": [v.as_dict() for v in self.violations],
        }

    def to_json(self) -> str:
        """Serialise the report to deterministic JSON (sorted keys)."""

        return json.dumps(
            self.as_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )

    def to_text(self) -> str:
        """Render the report as human-readable text.

        A clean set prints a single ``lint: OK`` line; a dirty set prints a
        ``lint: FAILED`` header followed by one ``#<i> <app>: <reason>`` line
        per violation.
        """

        if self.is_clean:
            return "lint: OK — every entry has a rationale and is human_signed"
        lines = [
            "lint: FAILED",
            f"  {len(self.violations)} artistry-rule violation(s):",
        ]
        for violation in self.violations:
            lines.append(f"  #{violation.index} {violation.app}: {violation.reason}")
        return "\n".join(lines)


def _check_entry(index: int, entry: CuratedEntry) -> LintViolation | None:
    """Check one entry against the artistry rule; return a violation or None."""

    if not entry.rationale.strip():
        return LintViolation(index, entry.app, "missing rationale")
    if not entry.is_signed:
        return LintViolation(index, entry.app, "not human_signed")
    return None


def lint(curated: CuratedSet) -> LintReport:
    """Check the human-artistry rule against ``curated``.

    Every entry must carry a non-empty ``rationale`` and be ``human_signed``.
    Returns a :class:`LintReport` with one :class:`LintViolation` per failing
    entry, in source order. An empty set is clean (nothing violates the rule).
    This never raises: a dirty set is reported, not crashed.
    """

    violations = [
        violation
        for index, entry in enumerate(curated)
        if (violation := _check_entry(index, entry)) is not None
    ]
    return LintReport(tuple(violations))


def assert_clean(curated: CuratedSet) -> CuratedSet:
    """Gate on the artistry rule: raise :class:`ArtistryError` if dirty.

    The strict form of :func:`lint` — used by the CLI ``lint`` subcommand's
    exit-1 path. Returns ``self`` when clean.
    """

    report = lint(curated)
    if not report.is_clean:
        raise ArtistryError(
            "; ".join(
                f"#{v.index} {v.app}: {v.reason}" for v in report.violations
            )
        )
    return curated


def lint_default() -> LintReport:
    """Lint the committed curated set (``curated.toml``)."""

    return lint(load_default())
