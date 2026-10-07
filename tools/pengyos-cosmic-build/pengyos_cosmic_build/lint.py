"""``lint`` — assert the wired ``BR2_EXTERNAL`` tree is buildable-shaped.

Lint the tree :mod:`.wire` installs: no leftover placeholder ``SITE`` / hash,
every ``BR2_PACKAGE_*`` the desktop defconfig references resolves to a recipe,
the native deps the defconfig references are present, and the ``pengyos-desktop``
target is registered in ``targets.toml``.

This is the gate that closes the gap the recipes lane leaves open: the recipes
lane emits placeholder recipes (`SITE=file://<VENDORED_SITE>`, `hash=0x64`) and a
defconfig whose symbols a different symbol prefix than the emitted recipes use,
so nothing the Desktop target references resolves. :func:`lint` catches exactly
those four classes of breakage *before* a multi-hour build is attempted.

The four checks (one mutation each, see ``tests/test_lint.py``):

1. **no placeholder SITE/hash** — every emitted ``<pkg>.mk`` pins a real
   ``<PKG>_SITE`` (never the ``file://<VENDORED_SITE>`` placeholder) and every
   ``<pkg>.hash`` carries a real 64-hex sha256. The emitted recipe owns this
   (``emit`` rejects a placeholder record), but a hand-edited recipe could
   reintroduce the placeholder, so the tree linter re-checks it.
2. **every defconfig symbol resolves** — every ``BR2_PACKAGE_*`` the defconfig
   references is either enabled by a recipe in the tree (a ``config <sym>`` line
   in some ``package/<pkg>/Config.in``) or exists in the vendored Buildroot
   ``2026.08`` kconfig symbol list (a core package PengyOS gets for free). A
   symbol that is neither is unbuildable: Buildroot silently drops it, so the
   feature it stands for is a silent no-op. The canonical case is
   ``BR2_PACKAGE_COSMIC_PORTAL`` — the Desktop defconfig references it, but the
   ``cosmic-portal`` recipe enables ``BR2_PACKAGE_XDG_DESKTOP_PORTAL_BACKEND_COSMIC``
   instead, so it resolves to no recipe and is *not* a 2026.08 core symbol.
3. **native deps present** — every native dep the defconfig references (via its
   catalogue ``br_symbols``) is installed as a recipe in the tree. This is the
   ``2026.08``-missing half of check 2 made explicit against the committed
   native-dep catalogue: a dep whose symbol is *not* in 2026.08 must have a
   recipe, or the symbol can never resolve.
4. **target registered** — the ``pengyos-desktop`` target is registered in
   ``targets.toml`` (the tree is wired but not a buildable target otherwise).

Runtime depends only on the Python standard library; the vendored 2026.08 symbol
list and the native-dep catalogue are imported lazily (they live on ``sys.path``
only under pytest / an installed user), so a bare ``python -m pengyos_cosmic_build``
works on a bare checkout.

"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

# The documented placeholder SITE the recipes lane falls back to when no real
# digest is available. A recipe that still pins it is a lint violation.
from pengyos_cosmic_build.records import VENDORED_SITE_PLACEHOLDER

# The ``<PKG>_SITE = <value>`` assignment a ``.mk`` carries.
_SITE_RE = re.compile(r"^\s*[A-Z][A-Z0-9_]*_SITE\s*=\s*(.*?)\s*$")

# A Buildroot defconfig symbol assignment: ``BR2_PACKAGE_FOO=y`` / ``=m`` /
# ``=y # comment``. Only ``BR2_PACKAGE_*`` (package) symbols are relevant to the
# recipe-resolution checks; architecture / toolchain / board assignments are
# ignored.
_DEFCONFIG_SYMBOL_RE = re.compile(r"^\s*BR2_PACKAGE_[A-Z0-9_]+\s*=")

# A ``config <SYMBOL>`` line opening a recipe's ``Config.in`` entry.
_CONFIG_SYMBOL_RE = re.compile(r"^\s*config\s+([A-Z0-9_]+)\s*$")

# A ``[[targets]]`` block that registers *name* (the ``name = "..."`` key).
_TARGET_NAME_KEY_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"\s*$')

#: The desktop target ``wire`` registers in ``targets.toml``.
_TARGET_NAME = "pengyos-desktop"

#: The ``package/`` dir recipes live in (relative to the tree root).
_PACKAGE_DIR = "package"
#: The ``configs/`` dir a defconfig lives in (relative to the tree root).
_CONFIGS_DIR = "configs"
#: The ``targets.toml`` the desktop target is registered in.
_TARGETS_TOML = "targets.toml"


class LintCategory(str, Enum):
    """The class of a lint violation (one per mutation in the test suite)."""

    PLACEHOLDER_SITE = "placeholder_site"
    PLACEHOLDER_HASH = "placeholder_hash"
    UNRESOLVED_SYMBOL = "unresolved_symbol"
    MISSING_NATIVE_DEP = "missing_native_dep"
    UNREGISTERED_TARGET = "unregistered_target"


@dataclass(frozen=True)
class Issue:
    """One lint violation: the category that failed and why."""

    category: LintCategory
    message: str


@dataclass(frozen=True)
class LintReport:
    """The outcome of :func:`lint_repo`: the violations found (if any)."""

    repo: Path
    issues: tuple[Issue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Whether the tree is buildable-shaped (no violations)."""
        return not self.issues

    def categories(self) -> tuple[LintCategory, ...]:
        """The distinct violation categories, in first-seen order."""
        seen: list[LintCategory] = []
        for issue in self.issues:
            if issue.category not in seen:
                seen.append(issue.category)
        return tuple(seen)

    def as_dict(self) -> dict:
        """A deterministic, JSON-serialisable view."""
        return {
            "repo": str(self.repo),
            "ok": self.ok,
            "counts": {"violations": len(self.issues)},
            "issues": [
                {"category": i.category.value, "message": i.message}
                for i in self.issues
            ],
        }


# --- recipe scanning ---------------------------------------------------------


def _recipe_dirs(pkg_dir: Path) -> list[Path]:
    """The recipe directories under *pkg_dir*, in sorted (deterministic) order.

    A recipe directory is any *direct* subdirectory holding a ``Config.in``.
    Buildroot meta-files (``Config.in`` / ``Makefile.in`` at the dir root) and
    hidden directories are skipped.
    """
    if not pkg_dir.is_dir():
        return []
    dirs = sorted(
        child
        for child in pkg_dir.iterdir()
        if child.is_dir()
        and not child.name.startswith(".")
        and (child / "Config.in").is_file()
    )
    return dirs


def _parse_config_symbols(config_in: str) -> set[str]:
    """The ``BR2_PACKAGE_*`` symbols a ``Config.in`` declares (its ``config`` lines)."""
    return {
        match.group(1)
        for line in config_in.splitlines()
        if (match := _CONFIG_SYMBOL_RE.match(line))
    }


def _recipe_symbols(recipe_dirs: Iterable[Path]) -> set[str]:
    """Every ``BR2_PACKAGE_*`` symbol enabled by the recipes in *recipe_dirs*."""
    symbols: set[str] = set()
    for recipe_dir in recipe_dirs:
        try:
            text = (recipe_dir / "Config.in").read_text(encoding="utf-8")
        except OSError:
            continue
        symbols |= _parse_config_symbols(text)
    return symbols


def _placeholder_site_violations(recipe_dirs: Iterable[Path]) -> list[Issue]:
    """A ``.mk`` that still pins the ``file://<VENDORED_SITE>`` placeholder SITE."""
    issues: list[Issue] = []
    for recipe_dir in recipe_dirs:
        mk_path = recipe_dir / f"{recipe_dir.name}.mk"
        if not mk_path.is_file():
            continue
        for line in mk_path.read_text(encoding="utf-8").splitlines():
            match = _SITE_RE.match(line)
            if match is None:
                continue
            if match.group(1) == VENDORED_SITE_PLACEHOLDER:
                issues.append(
                    Issue(
                        LintCategory.PLACEHOLDER_SITE,
                        f"{recipe_dir.name}.mk pins the placeholder SITE "
                        f"{VENDORED_SITE_PLACEHOLDER!r}",
                    )
                )
    return issues


def _placeholder_hash_violations(recipe_dirs: Iterable[Path]) -> list[Issue]:
    """A ``.hash`` that is missing, empty, or carries a non-64-hex sha256.

    The recipes lane's placeholder hash is a short / non-hex digest (the
    recipes lane pins ``hash=0x64``); a real recipe pins a 64-hex sha256. A
    missing ``.hash`` is reported the same way (nothing to verify against).
    """
    issues: list[Issue] = []
    for recipe_dir in recipe_dirs:
        # A data-only (``none``) recipe — e.g. the fonts, which ship font files
        # with no build step — carries no ``.mk`` and therefore no ``.hash``;
        # only buildable recipes (a ``.mk`` is present) must pin a digest.
        if not (recipe_dir / f"{recipe_dir.name}.mk").is_file():
            continue
        hash_path = recipe_dir / f"{recipe_dir.name}.hash"
        if not hash_path.is_file():
            issues.append(
                Issue(
                    LintCategory.PLACEHOLDER_HASH,
                    f"{recipe_dir.name}.hash is missing (no digest to verify)",
                )
            )
            continue
        body = hash_path.read_text(encoding="utf-8")
        digest = None
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) >= 2 and parts[0] == "sha256":
                digest = parts[1]
                break
        if digest is None:
            issues.append(
                Issue(
                    LintCategory.PLACEHOLDER_HASH,
                    f"{recipe_dir.name}.hash has no sha256 line",
                )
            )
        elif not re.match(r"^[0-9a-fA-F]{64}$", digest):
            issues.append(
                Issue(
                    LintCategory.PLACEHOLDER_HASH,
                    f"{recipe_dir.name}.hash digest {digest!r} is not 64 hex "
                    f"digits (a placeholder digest)",
                )
            )
    return issues


# --- the vendored 2026.08 symbol set ----------------------------------------


def _buildroot_2026_08_symbols() -> set[str]:
    """The Buildroot 2026.08 kconfig symbols, vendored by the native-deps lane.

    Imported lazily: the list lives in the sibling ``pengyos-cosmic-native-deps``
    tool (on ``sys.path`` only under pytest / an installed user), so importing it
    at module load would break ``python -m pengyos_cosmic_build`` on a bare
    checkout. A symbol that exists here is a core 2026.08 package PengyOS gets
    for free, so it needs no recipe.
    """
    from pengyos_cosmic_native_deps.symbols import (
        default_symbol_list_path,
        SymbolList,
    )

    path = default_symbol_list_path()
    if not path.is_file():
        return set()
    return SymbolList.load(path).symbols


# --- the native-dep catalogue ------------------------------------------------


def _native_dep_symbols() -> dict[str, tuple[str, ...]]:
    """The native-dep catalogue keyed by recipe name -> its ``br_symbols``.

    Imported lazily (see :func:`_buildroot_2026_08_symbols`). A native dep is a
    dependency ``2026.08`` does not ship, so its symbol is *not* in the 2026.08
    symbol list — it must have a recipe in the tree for the defconfig's
    reference to resolve.
    """
    from pengyos_cosmic_native_deps.recipes import default_recipe_catalogue

    catalogue = default_recipe_catalogue()
    return {recipe.name: recipe.br_symbols for recipe in catalogue.recipes}


# --- the defconfig -----------------------------------------------------------


def _defconfig_referenced_symbols(defconfig_text: str) -> list[str]:
    """The ``BR2_PACKAGE_*`` symbols a defconfig references, in file order.

    A reference is a ``BR2_PACKAGE_FOO=y`` / ``=m`` assignment (Buildroot's
    defconfig form). Duplicates collapse to first occurrence; non-package
    assignments (``BR2_x86_64``, ``BR2_TARGET_GENERIC_HOSTNAME``) are ignored.
    """
    seen: list[str] = []
    for line in defconfig_text.splitlines():
        stripped = line.strip()
        if not _DEFCONFIG_SYMBOL_RE.match(stripped):
            continue
        symbol = stripped.split("=", 1)[0].strip()
        if symbol not in seen:
            seen.append(symbol)
    return seen


def _read_defconfig(configs_dir: Path) -> str | None:
    """The desktop defconfig text, or ``None`` if the tree has none.

    The Desktop defconfig is a bare filename (no ``.defconfig`` suffix) under
    ``configs/``. The first non-hidden file there is returned; a tree with no
    defconfig yields ``None`` (the symbol checks are then vacuous).
    """
    if not configs_dir.is_dir():
        return None
    for child in sorted(configs_dir.iterdir()):
        if child.is_file() and not child.name.startswith("."):
            return child.read_text(encoding="utf-8")
    return None


def _native_dep_reference_symbols(
    native_dep_symbols: dict[str, tuple[str, ...]],
) -> set[str]:
    """Every symbol any native dep enables (the union of all ``br_symbols``)."""
    symbols: set[str] = set()
    for symbols_in in native_dep_symbols.values():
        symbols |= set(symbols_in)
    return symbols


def _unresolved_symbols(
    referenced: list[str],
    recipe_symbols: set[str],
    buildroot_symbols: set[str],
    native_dep_reference_symbols: set[str],
) -> list[Issue]:
    """A referenced symbol that is not explained by any known mechanism.

    A symbol is *explained* (not unresolved) if it is enabled by a recipe in the
    tree, exists in the vendored 2026.08 symbol list (a core package), or is a
    native-dep symbol (handled by :func:`_missing_native_deps`, which names the
    dep). The remaining symbols are genuinely unexplained: Buildroot silently
    drops them, so the feature they stand for is a silent no-op. The canonical
    case is ``BR2_PACKAGE_COSMIC_PORTAL`` — the Desktop defconfig references it,
    but no recipe enables that exact symbol and it is not a 2026.08 core symbol
    (the ``cosmic-portal`` recipe enables
    ``BR2_PACKAGE_XDG_DESKTOP_PORTAL_BACKEND_COSMIC`` instead).
    """
    issues: list[Issue] = []
    for symbol in referenced:
        if (
            symbol in recipe_symbols
            or symbol in buildroot_symbols
            or symbol in native_dep_reference_symbols
        ):
            continue
        issues.append(
            Issue(
                LintCategory.UNRESOLVED_SYMBOL,
                f"defconfig references {symbol}, which no recipe enables, "
                f"which is not a native dep, and which is not a Buildroot "
                f"2026.08 core symbol",
            )
        )
    return issues


def _missing_native_deps(
    referenced: list[str],
    recipe_symbols: set[str],
    native_dep_symbols: dict[str, tuple[str, ...]],
) -> list[Issue]:
    """A native dep the defconfig references but that has no recipe in the tree.

    A native dep is one the catalogue says ``2026.08`` does not ship (its symbol
    is not in the 2026.08 list). This check *owns* the native-dep symbols: a
    native dep whose symbol the defconfig references but that no recipe enables
    is reported here (naming the dep, so the operator knows which recipe to
    author/install) and is deliberately NOT also reported as an unresolved
    symbol — the two would otherwise double-count the same breakage. A native
    dep whose symbol the defconfig does not reference is left alone (PengyOS
    need not author a recipe for a dep the desktop does not enable).
    """
    issues: list[Issue] = []
    for dep_name, symbols in sorted(native_dep_symbols.items()):
        referenced_by_defconfig = [s for s in symbols if s in referenced]
        if not referenced_by_defconfig:
            continue
        enabled = any(symbol in recipe_symbols for symbol in symbols)
        if enabled:
            continue
        issues.append(
            Issue(
                LintCategory.MISSING_NATIVE_DEP,
                f"native dep {dep_name!r} is referenced by the defconfig "
                f"({', '.join(referenced_by_defconfig)}) but no recipe in the "
                f"tree enables it",
            )
        )
    return issues


# --- the registered target ---------------------------------------------------


def _target_registered(targets_text: str, name: str = _TARGET_NAME) -> bool:
    """Whether *targets_text* registers *name* (a ``[[targets]]`` ``name`` key)."""
    for line in targets_text.splitlines():
        match = _TARGET_NAME_KEY_RE.match(line)
        if match is not None and match.group(1) == name:
            return True
    return False


def _unregistered_target(targets_path: Path) -> Issue | None:
    """The unregistered-target issue, or ``None`` when the target is registered.

    A ``targets.toml`` that does not exist is *not* a violation here: the tree
    may be wired without a target (``wire`` supports a bare ``--repo`` run), so
    a missing file is vacuous. Only a present-but-missing registration fires.
    """
    if not targets_path.is_file():
        return None
    targets_text = targets_path.read_text(encoding="utf-8")
    if _target_registered(targets_text):
        return None
    return Issue(
        LintCategory.UNREGISTERED_TARGET,
        f"{targets_path.name} does not register the {_TARGET_NAME} target",
    )


# --- the orchestrator --------------------------------------------------------


def lint_repo(
    repo: Path | str,
    *,
    referenced_symbols: list[str] | None = None,
    recipe_symbols: set[str] | None = None,
    buildroot_symbols: set[str] | None = None,
    native_dep_symbols: dict[str, tuple[str, ...]] | None = None,
    targets_text: str | None = None,
) -> LintReport:
    """Lint the ``BR2_EXTERNAL`` tree at *repo* (buildable-shaped).

    The six checks run in order and their issues are concatenated, so a single
    ``lint`` run reports every class of breakage at once (placeholder SITE,
    placeholder hash, unresolved symbol, missing native dep, unregistered
    target). Returns a :class:`LintReport`; :attr:`LintReport.ok` is whether the
    tree is clean.

    Every input is injectable so the checks are unit-tested in isolation (one
    mutation each) without a full tree:

    * ``referenced_symbols`` — the ``BR2_PACKAGE_*`` symbols the defconfig
      references (defaults to reading ``configs/`` from the tree).
    * ``recipe_symbols`` — the symbols enabled by the tree's recipes (defaults
      to scanning ``package/<pkg>/Config.in``).
    * ``buildroot_symbols`` — the 2026.08 core-symbol set (defaults to the
      vendored list).
    * ``native_dep_symbols`` — the catalogue keyed by name (defaults to the
      committed catalogue).
    * ``targets_text`` — the ``targets.toml`` body (defaults to reading the
      tree's ``targets.toml``; a missing file is vacuous, not a violation).
    """
    repo = Path(repo)
    pkg_dir = repo / _PACKAGE_DIR

    recipe_dirs = _recipe_dirs(pkg_dir)
    recipe_symbols = (
        recipe_symbols
        if recipe_symbols is not None
        else _recipe_symbols(recipe_dirs)
    )

    placeholder_site = _placeholder_site_violations(recipe_dirs)
    placeholder_hash = _placeholder_hash_violations(recipe_dirs)

    if referenced_symbols is not None:
        referenced = referenced_symbols
    else:
        defconfig_text = _read_defconfig(repo / _CONFIGS_DIR)
        referenced = (
            _defconfig_referenced_symbols(defconfig_text)
            if defconfig_text is not None
            else []
        )

    buildroot_symbols = (
        buildroot_symbols
        if buildroot_symbols is not None
        else _buildroot_2026_08_symbols()
    )
    native_dep_symbols = (
        native_dep_symbols
        if native_dep_symbols is not None
        else _native_dep_symbols()
    )
    native_dep_reference_symbols = _native_dep_reference_symbols(native_dep_symbols)

    unresolved = _unresolved_symbols(
        referenced, recipe_symbols, buildroot_symbols, native_dep_reference_symbols
    )
    missing_native = _missing_native_deps(
        referenced, recipe_symbols, native_dep_symbols
    )

    issues: list[Issue] = [
        *placeholder_site,
        *placeholder_hash,
        *unresolved,
        *missing_native,
    ]

    if targets_text is not None:
        issue = _unregistered_target_from_text(targets_text)
        if issue is not None:
            issues.append(issue)
    else:
        targets_path = repo / _TARGETS_TOML
        issue = _unregistered_target(targets_path)
        if issue is not None:
            issues.append(issue)

    return LintReport(repo=repo, issues=tuple(issues))


def _unregistered_target_from_text(targets_text: str) -> Issue | None:
    """The unregistered-target issue for an explicit *targets_text*."""
    if _target_registered(targets_text):
        return None
    return Issue(
        LintCategory.UNREGISTERED_TARGET,
        f"the {_TARGET_NAME} target is not registered in targets.toml",
    )


# --- CLI entry point ---------------------------------------------------------


def main(args: object) -> int:
    """CLI entry point for ``lint``."""
    import json

    repo = Path(args.repo)  # type: ignore[attr-defined]

    try:
        report = lint_repo(repo)
    except OSError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"command": "lint", "ok": False, "error": str(exc)}))
        else:
            print(f"pengyos-cosmic-build: lint: {exc}", file=sys.stderr)
        return 1

    ok = report.ok
    if getattr(args, "json", False):
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    elif ok:
        print(f"{repo} is buildable-shaped (no violations)")
    else:
        print(
            f"{repo}: {len(report.issues)} violation(s) — not buildable-shaped:",
            file=sys.stderr,
        )
        for issue in report.issues:
            print(f"  [{issue.category.value}] {issue.message}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
