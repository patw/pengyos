"""``lint`` — validate a Buildroot recipe tree.

Given a Buildroot package directory (``package/<pkg>/``: ``Config.in``,
``<pkg>.mk``, ``<pkg>.hash``), produce a :class:`LintReport` enumerating every
problem found. A report with an empty ``violations`` tuple is lint-clean; the
CLI exits 0. Any violation is a *reported* condition (exit 1), never a crash.

### What it checks

Four things a reproducible Buildroot cargo recipe must pin:

* **hash present** — a ``<pkg>.hash`` file exists in the package directory
  (``support/download/check-hash`` verifies the downloaded tarball against it).
* **version pinned** — the ``<pkg>.mk``'s ``<PKG>_VERSION`` is a pinned git
  reference: a commit SHA or a version tag, never a floating branch/ref. This
  reuses :func:`pengyos_cosmic_pkg.manifest.is_pinned` — the single predicate
  the manifest validator shares, so the recipe and the manifest never disagree
  about what "pinned" means.
* **license declared** — the ``<pkg>.mk``'s ``<PKG>_LICENSE`` is present and
  non-empty (an SPDX expression).
* **no floating git ref** — the ``<pkg>.mk``'s ``<PKG>_SITE`` is not a git URL
  with a floating branch/ref. A cargo recipe normally references a local vendored
  tarball (``file://…``); when a recipe points ``*_SITE`` at a git repository
  instead, its reference must be pinned (a commit SHA), or the build is
  non-reproducible.

"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import is_pinned

#: A ``KEY = value`` assignment line in a Buildroot ``.mk`` recipe. The key is a
#: leading-uppercase identifier; the value is everything after the ``=`` (kept
#: verbatim, trimmed by the caller). ``$(eval $(cargo-package))`` and the banner
#: comments match nothing here.
_MK_LINE_RE = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>.*)$")
#: A git URL in a ``*_SITE`` value: ``git+https://…``, ``git://…`` or
#: ``git@host:path`` (the forms cargo/Buildroot accept as a git source).
_GIT_SITE_RE = re.compile(r"^git\+?(?:https?://|git://|git@[^:/]+:).*")
#: The ``#<ref>`` fragment of a git URL (the pinned commit SHA, or a branch name
#: when it floats).
_GIT_FRAGMENT_RE = re.compile(r"#(?P<ref>.*)$")


@dataclass(frozen=True)
class LintViolation:
    """One lint problem: the rule broken and what is wrong.

    ``rule`` is a short, stable identifier (one of ``hash``, ``version``,
    ``license``, ``floating-ref``) so a consumer can classify failures;
    ``message`` is the human-readable detail.
    """

    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.message}"


@dataclass(frozen=True)
class LintReport:
    """The outcome of :func:`lint_dir`: the directory linted and its violations.

    ``is_clean`` (and thus ``bool(report)``) is the single gate a caller dispatches
    on: a clean report means the recipe pins its hash, version, license and has
    no floating git ref.
    """

    directory: Path
    violations: tuple[LintViolation, ...] = field(default_factory=tuple)

    @property
    def is_clean(self) -> bool:
        """``True`` when there are no violations."""
        return not self.violations

    def __bool__(self) -> bool:
        return self.is_clean

    def __str__(self) -> str:
        if self.is_clean:
            return (
                f"{self.directory}: clean (hash present, version pinned, "
                "license declared, no floating refs)"
            )
        lines = [f"{self.directory}: {len(self.violations)} violation(s):"]
        for violation in self.violations:
            lines.append(f"  - {violation}")
        return "\n".join(lines)


def _parse_mk(text: str) -> dict[str, str]:
    """Parse a Buildroot ``.mk`` recipe into its ``KEY = value`` assignments.

    Only top-level ``KEY = value`` lines are captured (no ``$(...)`` expansion
    lines, no banner comments). Values are returned verbatim, trimmed of the
    surrounding whitespace, so a caller can decide how strictly to interpret it.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = _MK_LINE_RE.match(line)
        if match:
            out[match.group("key")] = match.group("value").strip()
    return out


def _floating_site(value: str) -> LintViolation | None:
    """Return a violation when a ``*_SITE`` value is a floating git ref.

    A git URL whose ``#`` fragment is missing or is not a pinned reference (a
    commit SHA or version tag) floats: the build would resolve whatever the
    repository's default branch points at, which is non-reproducible.
    """
    if not value or not _GIT_SITE_RE.match(value):
        return None
    ref = ""
    match = _GIT_FRAGMENT_RE.search(value)
    if match:
        ref = match.group("ref").strip()
    if not ref or not is_pinned(ref):
        return LintViolation(
            "floating-ref",
            f"{value!r} is a git URL with a floating branch/ref "
            "(expected a pinned commit SHA or version tag)",
        )
    return None


def lint_dir(directory: Path | str) -> LintReport:
    """Lint a Buildroot package directory (``package/<pkg>/``).

    Returns a :class:`LintReport`. The four rules, in the order checked:

    1. **hash present** — a ``<pkg>.hash`` file exists in ``directory``.
    2. **version pinned** — the ``<pkg>.mk``'s ``<PKG>_VERSION`` is a pinned
       reference (via :func:`is_pinned`).
    3. **license declared** — the ``<pkg>.mk``'s ``<PKG>_LICENSE`` is present
       and non-empty.
    4. **no floating git ref** — the ``<pkg>.mk``'s ``<PKG>_SITE`` is not a
       floating git URL.

    A missing ``.mk`` is itself a violation (there is no recipe to pin).
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    violations: list[LintViolation] = []

    mk_files = sorted(directory.glob("*.mk"))
    hash_files = sorted(directory.glob("*.hash"))

    # 1. hash present — the downloaded tarball must be verified against it.
    if not hash_files:
        violations.append(
            LintViolation(
                "hash",
                f"{directory.name}: no <pkg>.hash file in {directory}",
            )
        )

    # There is no recipe to pin anything.
    if not mk_files:
        violations.append(
            LintViolation(
                "version",
                f"{directory.name}: no <pkg>.mk recipe in {directory}",
            )
        )
        return LintReport(directory, tuple(violations))

    mk_path = mk_files[0]
    pkg = mk_path.stem
    # Buildroot renders the recipe keys against the full Kconfig symbol
    # (``BR2_PACKAGE_<UPPER>``), so the ``.mk`` carries ``BR2_PACKAGE_<PKG>_``
    # prefixes, not the bare package name.
    symbol = f"BR2_PACKAGE_{pkg.upper().replace('-', '_')}"
    keys = _parse_mk(mk_path.read_text(encoding="utf-8"))

    # 2. version pinned — the recipe must pin a concrete reference.
    version = keys.get(f"{symbol}_VERSION", "")
    if not version:
        violations.append(
            LintViolation(
                "version",
                f"{pkg}: {symbol}_VERSION is not set in {mk_path.name}",
            )
        )
    elif not is_pinned(version):
        violations.append(
            LintViolation(
                "version",
                f"{pkg}: {symbol}_VERSION = {version!r} is not pinned "
                "(a commit SHA or version tag — not a floating branch/ref)",
            )
        )

    # 3. license declared — an SPDX expression must be present.
    license_ = keys.get(f"{symbol}_LICENSE", "")
    if not license_:
        violations.append(
            LintViolation(
                "license",
                f"{pkg}: {symbol}_LICENSE is not declared in {mk_path.name}",
            )
        )

    # 4. no floating git ref — the source URL, if git, must be pinned.
    site = keys.get(f"{symbol}_SITE", "")
    site_violation = _floating_site(site)
    if site_violation is not None:
        violations.append(site_violation)

    return LintReport(directory, tuple(violations))


def _emit(args, report: LintReport) -> int:
    """Print the lint result (human or JSON) and return the exit code."""
    if getattr(args, "json", False):
        import json

        payload = {
            "command": args.command,
            "ok": report.is_clean,
            "dir": str(report.directory),
            "violations": [
                {"rule": v.rule, "message": str(v)} for v in report.violations
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif report.is_clean:
        print(str(report))
    else:
        print(str(report), file=sys.stderr)
    return 0 if report.is_clean else 1


def main(args) -> int:
    """CLI entry point for ``lint``."""
    report = lint_dir(args.dir)
    return _emit(args, report)
