"""``lint``: validate the emitted ``BR2_EXTERNAL`` recipes under a directory.

The :mod:`.emit` step writes, for every dependency that ``2026.08`` does not
ship, a ``package/<dep>/{Config.in,<dep>.mk,<dep>.hash}`` trio (``Config.in``
only for a data-only ``none`` recipe such as the fonts) plus the
``fragments/cosmic-native.fragment`` that enables the emitted symbols. A typo in
one of those files is a *build* failure deep in Buildroot — far from where it is
cheap to fix — so this module lints the emitted recipes before they are shipped.

The linter answers the four questions the catalogue contract promises (see
``spec.md``):

* **hash present** — every buildable recipe carries a ``<pkg>.hash`` with a
  valid ``sha256  <64-hex>  <source>`` line (Buildroot's ``check-package``
  ``HashNumberOfFields`` / ``HashType`` / ``HashSpaces`` rules).
* **version pinned** — the ``<pkg>.mk`` sets ``<PKG>_VERSION`` to a concrete
  release (never ``HEAD``, a branch, or a URL).
* **license declared** — the ``<pkg>.mk`` sets ``<PKG>_LICENSE`` to a non-empty
  SPDX identifier.
* **correct infra** — the ``<pkg>.mk`` ends with a ``$(eval $(...))`` line that
  selects a real Buildroot build-system method
  (``meson-package`` / ``autotools-package`` / ``generic-package`` /
  ``cargo-package``).

The linter is deliberately file-format aware (it parses the ``.mk`` / ``.hash``
shapes :mod:`.emit` produces) rather than a thin wrapper around Buildroot's
``check-package``: the latter needs a full toolchain, ``flake8`` +
``python-magic`` (root/PIP-requiring), and a tree checked out inside Buildroot.
The linter needs none of that, so it runs in the offline unit suite and gates CI.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .recipes import RecipeCatalogue

# The ``<PKG>_VAR = value`` assignment a ``.mk`` carries (the value may be empty;
# a missing variable is reported by the caller, not here).
_VAR_ASSIGN_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*[:]?=\s*(.*?)\s*$")

# The ``$(eval $(<method>))`` line that wires a recipe into a Buildroot build
# system. The method is one of the infra macros :func:`_EVAL_METHODS` knows.
_EVAL_LINE_RE = re.compile(
    r"^\s*\$\(eval \$\((?P<method>[a-z][a-z-]*-package)\)\)\s*$"
)

# A ``.hash`` body line: ``<type>  <hex>  <source>`` (three two-space-separated
# fields, mirroring Buildroot's ``check-package`` ``Hash*`` rules).
_HASH_LINE_RE = re.compile(r"^\s*([a-z0-9]+)\s+([0-9A-Fa-f]+)\s+(\S+)\s*$")

# A ``sha256`` digest: 64 hex characters, matching :mod:`.recipes`.
_SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")

# A concrete, pinned ``<PKG>_VERSION`` value: an alphanumeric start followed by
# version separators. This rejects an empty value, ``HEAD``, a branch name, and a
# URL, while accepting the catalogue's releases (``0.10.3``, ``1.22.1``,
# ``epoch-1.8.0``).
_VERSION_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~+:-]*$")

# The build-system methods a recipe may select. Mirrors :mod:`.emit`'s
# ``_mk_package_eval`` table.
_EVAL_METHODS = frozenset(
    {"meson-package", "autotools-package", "generic-package", "cargo-package"}
)


class CheckName(str, Enum):
    """The four lint checks a buildable recipe must pass."""

    HASH_PRESENT = "hash_present"
    VERSION_PINNED = "version_pinned"
    LICENSE_DECLARED = "license_declared"
    CORRECT_INFRA = "correct_infra"


class LintStatus(str, Enum):
    """The outcome of linting one recipe."""

    OK = "ok"
    VIOLATION = "violation"


class RecipeKind(str, Enum):
    """Whether a recipe directory holds a buildable or a data-only recipe."""

    BUILDABLE = "buildable"
    DATA_ONLY = "data_only"


class LintIssue(Exception):
    """One lint problem on one file (raised by a check, collected by the runner)."""

    def __init__(self, check: CheckName, message: str) -> None:
        super().__init__(message)
        self.check = check
        self.message = message


@dataclass(frozen=True)
class Issue:
    """A single lint violation: the check that failed and why."""

    check: CheckName
    message: str


@dataclass(frozen=True)
class LintResult:
    """The outcome of linting one recipe directory."""

    name: str
    """The recipe's directory name (the dependency's short name)."""

    kind: RecipeKind
    """Buildable (``<pkg>.mk`` present) or data-only (``Config.in`` only)."""

    status: LintStatus
    """``ok`` when no check failed, ``violation`` otherwise."""

    issues: tuple[Issue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Whether this recipe passed every check."""
        return self.status is LintStatus.OK


@dataclass(frozen=True)
class LintReport:
    """The outcome of linting a whole recipe directory."""

    root: Path
    """The directory that was linted."""

    results: tuple[LintResult, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Whether every linted recipe passed."""
        return all(result.status is LintStatus.OK for result in self.results)

    @property
    def violations(self) -> tuple[LintResult, ...]:
        """The recipes that failed (in scan order)."""
        return tuple(r for r in self.results if r.status is LintStatus.VIOLATION)

    def as_dict(self) -> dict:
        """A deterministic, JSON-serialisable view."""
        return {
            "root": str(self.root),
            "ok": self.ok,
            "counts": {
                "recipes": len(self.results),
                "violations": len(self.violations),
            },
            "results": [
                {
                    "name": result.name,
                    "kind": result.kind.value,
                    "status": result.status.value,
                    "issues": [
                        {"check": i.check.value, "message": i.message}
                        for i in result.issues
                    ],
                }
                for result in self.results
            ],
        }


# --- parsing ----------------------------------------------------------------


def parse_mk(text: str) -> dict[str, str]:
    """Parse the ``<PKG>_VAR = value`` assignments from a ``.mk`` body.

    The last assignment to a given variable wins (Buildroot semantics). Comments
    and blank lines are ignored, as is the ``$(eval $(...))`` line (it is not a
    variable assignment). Keys are the upper-cased variable names exactly as they
    appear (``GREETD_VERSION``, ``GREETD_LICENSE`` ...).
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _VAR_ASSIGN_RE.match(line)
        if match is not None:
            values[match.group(1)] = match.group(2)
    return values


def parse_hash_lines(text: str) -> list[tuple[str, str, str]]:
    """Parse the ``<type>  <hex>  <source>`` lines from a ``.hash`` body.

    Returns each line's ``(type, hex, source)`` triple, in file order, skipping
    blank lines and comments.
    """
    lines: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _HASH_LINE_RE.match(line)
        if match is not None:
            lines.append((match.group(1), match.group(2), match.group(3)))
    return lines


def _is_pinned_version(value: str) -> bool:
    """Whether *value* is a concrete, pinned ``<PKG>_VERSION`` (not HEAD/branch)."""
    if not value or value.upper() == "HEAD":
        return False
    if "://" in value:
        return False
    return bool(_VERSION_VALUE_RE.match(value))


# --- the four checks --------------------------------------------------------


def _check_hash_present(recipe_dir: Path) -> None:
    """A buildable recipe must carry a ``.hash`` with a valid sha256 line."""
    hash_path = recipe_dir / f"{recipe_dir.name}.hash"
    if not hash_path.is_file():
        raise LintIssue(
            CheckName.HASH_PRESENT,
            f"{hash_path.name} is missing",
        )
    lines = parse_hash_lines(hash_path.read_text(encoding="utf-8"))
    if not lines:
        raise LintIssue(
            CheckName.HASH_PRESENT,
            f"{hash_path.name} has no hash lines",
        )
    for entry in lines:
        htype, hexa, _source = entry
        if htype != "sha256":
            raise LintIssue(
                CheckName.HASH_PRESENT,
                f"{hash_path.name}: expected a sha256 line, got type {htype!r}",
            )
        if not _SHA256_RE.match(hexa):
            raise LintIssue(
                CheckName.HASH_PRESENT,
                f"{hash_path.name}: {hexa!r} is not a 64-hex sha256 digest",
            )


def _check_version_pinned(mk_values: dict[str, str], pkg: str) -> None:
    """The ``.mk`` must pin a concrete ``<PKG>_VERSION`` (never HEAD/branch)."""
    if f"{pkg}_VERSION" not in mk_values:
        raise LintIssue(
            CheckName.VERSION_PINNED,
            f"{pkg}_VERSION is not set",
        )
    value = mk_values[f"{pkg}_VERSION"]
    if not _is_pinned_version(value):
        raise LintIssue(
            CheckName.VERSION_PINNED,
            f"{pkg}_VERSION = {value!r} is not a pinned release (HEAD/branch/URL)",
        )


def _check_license_declared(mk_values: dict[str, str], pkg: str) -> None:
    """The ``.mk`` must declare a non-empty ``<PKG>_LICENSE``."""
    if f"{pkg}_LICENSE" not in mk_values:
        raise LintIssue(
            CheckName.LICENSE_DECLARED,
            f"{pkg}_LICENSE is not set",
        )
    value = mk_values[f"{pkg}_LICENSE"]
    if not value:
        raise LintIssue(
            CheckName.LICENSE_DECLARED,
            f"{pkg}_LICENSE is empty",
        )


def _check_correct_infra(mk_text: str) -> None:
    """The ``.mk`` must carry a valid ``$(eval $(...))`` build-system line."""
    matches = [_EVAL_LINE_RE.match(line) for line in mk_text.splitlines()]
    matching = [m for m in matches if m is not None]
    if not matching:
        raise LintIssue(
            CheckName.CORRECT_INFRA,
            "no $(eval $(...)) build-system line found",
        )
    methods = {m.group("method") for m in matching}
    unknown = methods - _EVAL_METHODS
    if unknown:
        raise LintIssue(
            CheckName.CORRECT_INFRA,
            f"unknown build-system method(s): {', '.join(sorted(unknown))}",
        )


def _lint_buildable(recipe_dir: Path) -> LintResult:
    """Lint a buildable recipe (``<pkg>.mk`` present)."""
    pkg = recipe_dir.name.replace("-", "_").upper()
    issues: list[Issue] = []

    mk_path = recipe_dir / f"{recipe_dir.name}.mk"
    mk_text = ""
    if mk_path.is_file():
        mk_text = mk_path.read_text(encoding="utf-8")
    mk_values = parse_mk(mk_text) if mk_text else {}

    def _run(check: CheckName, fn: object) -> None:
        try:
            fn()  # type: ignore[operator]
        except LintIssue as exc:
            issues.append(Issue(exc.check, exc.message))

    _run(CheckName.HASH_PRESENT, lambda: _check_hash_present(recipe_dir))
    _run(CheckName.VERSION_PINNED, lambda: _check_version_pinned(mk_values, pkg))
    _run(CheckName.LICENSE_DECLARED, lambda: _check_license_declared(mk_values, pkg))
    _run(CheckName.CORRECT_INFRA, lambda: _check_correct_infra(mk_text))

    return LintResult(
        name=recipe_dir.name,
        kind=RecipeKind.BUILDABLE,
        status=LintStatus.OK if not issues else LintStatus.VIOLATION,
        issues=tuple(issues),
    )


def _lint_data_only(recipe_dir: Path) -> LintResult:
    """Lint a data-only recipe (``Config.in`` only, e.g. the fonts)."""
    issues: list[Issue] = []
    config_path = recipe_dir / "Config.in"
    if not config_path.is_file():
        issues.append(
            Issue(
                CheckName.HASH_PRESENT,
                "no Config.in found (a recipe directory must not be empty)",
            )
        )
    return LintResult(
        name=recipe_dir.name,
        kind=RecipeKind.DATA_ONLY,
        status=LintStatus.OK if not issues else LintStatus.VIOLATION,
        issues=tuple(issues),
    )


def lint_recipe(recipe_dir: Path) -> LintResult:
    """Lint one recipe directory (``package/<dep>/``).

    A directory holding a ``<dep>.mk`` is treated as a buildable recipe and
    checked for hash / version / license / infra; a directory with only a
    ``Config.in`` is treated as a data-only recipe.
    """
    recipe_dir = Path(recipe_dir)
    if (recipe_dir / f"{recipe_dir.name}.mk").is_file():
        return _lint_buildable(recipe_dir)
    return _lint_data_only(recipe_dir)


def _iter_recipe_dirs(root: Path) -> list[Path]:
    """The recipe directories under *root*, in sorted (deterministic) order.

    A recipe directory is any *direct* subdirectory of *root* that is not a
    Buildroot meta-directory (``Config.in`` / ``Makefile.in`` are files, not
    dirs, so they never appear here). Hidden directories are skipped.
    """
    if not root.is_dir():
        return []
    dirs = sorted(
        child
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )
    return dirs


def lint_dir(root: str | Path) -> LintReport:
    """Lint every recipe directory under *root*.

    Scans the direct subdirectories of *root* (each is a ``package/<dep>/``),
    linting each as a buildable or data-only recipe, and returns a
    :class:`LintReport` in scan order. An empty or missing directory yields an
    empty (``ok``) report.
    """
    resolved = Path(root)
    results = [lint_recipe(d) for d in _iter_recipe_dirs(resolved)]
    return LintReport(root=resolved, results=tuple(results))


def lint_catalogue(
    catalogue: "RecipeCatalogue",
    out_dir: str | Path,
) -> LintReport:
    """Emit *catalogue* into a temp tree and lint the result.

    Convenience wrapper used by the CLI and tests: it reuses :func:`.emit.emit`
    to write the recipes into a throwaway directory, then runs :func:`lint_dir`
    over them, so the committed catalogue is proven lint-clean without a manual
    emit step.
    """
    from .emit import emit

    scratch = Path(out_dir)
    emit(catalogue, scratch)
    return lint_dir(scratch / "package")
