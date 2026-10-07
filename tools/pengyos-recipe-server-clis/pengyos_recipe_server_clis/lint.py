"""Buildroot recipe linter for the PengyOS Server comfort CLIs.

Validates an emitted Buildroot ``BR2_EXTERNAL`` recipe against the four grammar
rules borrowed from ``pengyos-recipe-lint``:

===================  ================================================  ====================
rule                 what it checks                              rejects
===================  ================================================  ====================
``version_pinned``  ``<TOOL>_VERSION`` is a concrete pin            a floating ``latest``
                                             (never ``latest`` / ``HEAD`` / ``$(…)``)
``hash_present``     a sibling ``<tool>.hash`` carries a ``sha256``  a missing .hash, or one that does not
                                             covering ``<TOOL>_SOURCE``               name the pinned tarball
``license_declared`` ``<TOOL>_LICENSE`` is non-empty               an undeclared / empty license
``license_file_declared`` ``<TOOL>_LICENSE_FILES`` is non-empty       a missing license-file path
``source_url_present`` ``<TOOL>_SITE`` is an absolute ``https`` URL   a relative / non-``https`` source
===================  ================================================  ====================

The linter is the *reader* half of the emitter's contract: it parses the emitted
``<tool>.mk`` and cross-checks it against the sibling ``<tool>.hash`` and the
Buildroot recipe grammar, so a recipe that looks right but drifts from the
grammar is caught. It is deliberately self-contained (regex + stdlib) so the
rules can be reused directly in tests.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import __version__

# --------------------------------------------------------------------------- #
# Rule identifiers
# --------------------------------------------------------------------------- #

# The lint rules, in the order they are reported.  Each maps to a check function
# on :class:`Recipe`.
RULE_VERSION_PINNED = "version_pinned"
RULE_HASH_PRESENT = "hash_present"
RULE_LICENSE_DECLARED = "license_declared"
RULE_LICENSE_FILE_DECLARED = "license_file_declared"
RULE_SOURCE_URL_PRESENT = "source_url_present"

RULES: tuple[str, ...] = (
    RULE_VERSION_PINNED,
    RULE_HASH_PRESENT,
    RULE_LICENSE_DECLARED,
    RULE_LICENSE_FILE_DECLARED,
    RULE_SOURCE_URL_PRESENT,
)

# A concrete Buildroot version is a dotted release, never a moving ref.  The
# emitter pins the catalogue version verbatim; the linter rejects anything that
# is not a concrete version (the floating `latest` / `HEAD` / `$(...)` cases).
_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.\-]*$")

# A sha256 digest is exactly 64 lowercase hex characters.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# An absolute https source URL.
_ABS_HTTPS_RE = re.compile(r"^https://.+")

# A Buildroot ``$(call <macro>, …)`` download helper.  The canonical fzf /
# starship recipe shape (and every recipe this repo emits) expresses the source
# as ``$(call github, <org>, <repo>, <tag>)`` — which Buildroot resolves to an
# ``https://github.com/…`` tarball — rather than a literal URL.  These are the
# https-based download helpers; a bare ``http://`` or relative path is still
# rejected by :func:`_check_source_url_present`.
_CALL_MACRO_RE = re.compile(
    r"^\$\(call (?:github|sourceforge|launchpad|savannah|git|download),.+\)\s*$"
)


# --------------------------------------------------------------------------- #
# .mk parsing
# --------------------------------------------------------------------------- #


def _strip(value: str) -> str:
    """Trim a Buildroot ``mk`` RHS: outer whitespace and any trailing ``#`` comment."""
    value = value.strip()
    hash_pos = value.find("#")
    if hash_pos != -1:
        value = value[:hash_pos]
    return value.strip()


@dataclass(frozen=True)
class Recipe:
    """The parsed fields of a single ``<tool>.mk`` recipe.

    Attributes:
        name: The tool / package name (also the directory and ``<tool>.mk`` stem).
        version: The RHS of ``<TOOL>_VERSION`` (``None`` if absent).
        site: The RHS of ``<TOOL>_SITE`` (``None`` if absent).
        license: The RHS of ``<TOOL>_LICENSE`` (``None`` if absent).
        license_file: The RHS of ``<TOOL>_LICENSE_FILES`` (``None`` if absent).
        source: The RHS of ``<TOOL>_SOURCE`` (``None`` if absent; Buildroot
            derives it from ``_SITE`` / ``_VERSION`` when omitted, so its
            presence is *not* a lint requirement).
        hash_source: The tarball name named by the sibling ``<tool>.hash``'s
            ``sha256`` line (``None`` if the .hash is missing / malformed).
    """

    name: str
    version: str | None
    site: str | None
    license: str | None
    license_file: str | None
    source: str | None
    hash_source: str | None


def parse_recipe(pkg_dir: Path) -> Recipe:
    """Parse ``<pkg_dir>/<name>.mk`` into a :class:`Recipe`.

    The recipe file is ``<pkg_dir>/<name>.mk`` where ``<name>`` is the directory
    stem (mirrors the Buildroot external-tree layout ``package/<tool>/<tool>.mk``).
    Raises ``FileNotFoundError`` if the ``.mk`` is absent.
    """
    pkg_dir = Path(pkg_dir)
    name = pkg_dir.name
    mk_path = pkg_dir / f"{name}.mk"
    if not mk_path.is_file():
        raise FileNotFoundError(f"no recipe at {mk_path} (expected <name>.mk)")

    fields: dict[str, str | None] = {
        "version": None,
        "site": None,
        "license": None,
        "license_file": None,
        "source": None,
    }
    for line in mk_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for key, var in (
            ("version", f"{name.upper()}_VERSION"),
            ("site", f"{name.upper()}_SITE"),
            ("license", f"{name.upper()}_LICENSE"),
            ("license_file", f"{name.upper()}_LICENSE_FILES"),
            ("source", f"{name.upper()}_SOURCE"),
        ):
            if stripped.startswith(var + " ="):
                fields[key] = _strip(stripped[len(var) + 2 :])

    hash_source = _hash_source_of(pkg_dir / f"{name}.hash")
    return Recipe(
        name=name,
        version=fields["version"],
        site=fields["site"],
        license=fields["license"],
        license_file=fields["license_file"],
        source=fields["source"],
        hash_source=hash_source,
    )


def _hash_source_of(hash_path: Path) -> str | None:
    """Return the tarball named by ``hash_path``'s ``sha256`` line, or ``None``.

    The Buildroot ``.hash`` format is ``sha256  <64-hex>  <source>`` (a ``#``
    comment header is allowed).  Returns the ``<source>`` token of the first
    valid ``sha256`` line.
    """
    if not hash_path.is_file():
        return None
    for line in hash_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 3 and parts[0] == "sha256" and _SHA256_RE.match(parts[1]):
            return parts[2]
    return None


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    """One lint finding: a rule, whether it passed, and a human message."""

    rule: str
    ok: bool
    message: str

    def to_dict(self) -> dict[str, object]:
        return {"rule": self.rule, "ok": self.ok, "message": self.message}


def _check_version_pinned(r: Recipe) -> Finding:
    if r.version is None:
        return Finding(
            RULE_VERSION_PINNED,
            False,
            f"{r.name}: <{r.name.upper()}_VERSION> is missing",
        )
    if not _VERSION_RE.match(r.version):
        return Finding(
            RULE_VERSION_PINNED,
            False,
            f"{r.name}: version {r.version!r} is not a concrete pin "
            "(floating 'latest', 'HEAD', '$(…)', …)",
        )
    return Finding(RULE_VERSION_PINNED, True, f"{r.name}: version {r.version} pinned")


def _check_hash_present(r: Recipe) -> Finding:
    if r.hash_source is None:
        return Finding(
            RULE_HASH_PRESENT,
            False,
            f"{r.name}: sibling <{r.name}.hash> is missing or has no sha256 line",
        )
    expected = f"{r.name}-{r.version}.tar.gz" if r.version else None
    if expected is not None and r.hash_source != expected:
        return Finding(
            RULE_HASH_PRESENT,
            False,
            f"{r.name}: .hash names {r.hash_source!r}, not the pinned "
            f"{expected!r}",
        )
    return Finding(
        RULE_HASH_PRESENT, True, f"{r.name}: .hash covers {r.hash_source}"
    )


def _check_license_declared(r: Recipe) -> Finding:
    if r.license is None or not r.license:
        return Finding(
            RULE_LICENSE_DECLARED,
            False,
            f"{r.name}: <{r.name.upper()}_LICENSE> is missing / empty",
        )
    return Finding(
        RULE_LICENSE_DECLARED, True, f"{r.name}: license {r.license}"
    )


def _check_license_file_declared(r: Recipe) -> Finding:
    if r.license_file is None or not r.license_file:
        return Finding(
            RULE_LICENSE_FILE_DECLARED,
            False,
            f"{r.name}: <{r.name.upper()}_LICENSE_FILES> is missing / empty",
        )
    return Finding(
        RULE_LICENSE_FILE_DECLARED,
        True,
        f"{r.name}: license file {r.license_file}",
    )


def _check_source_url_present(r: Recipe) -> Finding:
    if r.site is None:
        return Finding(
            RULE_SOURCE_URL_PRESENT,
            False,
            f"{r.name}: <{r.name.upper()}_SITE> is missing",
        )
    if not (_ABS_HTTPS_RE.match(r.site) or _CALL_MACRO_RE.match(r.site)):
        return Finding(
            RULE_SOURCE_URL_PRESENT,
            False,
            f"{r.name}: {r.site!r} is not an absolute https source URL "
            "(or a $(call github, …) download helper)",
        )
    return Finding(
        RULE_SOURCE_URL_PRESENT, True, f"{r.name}: source {r.site}"
    )


_CHECKS: dict[str, object] = {
    RULE_VERSION_PINNED: _check_version_pinned,
    RULE_HASH_PRESENT: _check_hash_present,
    RULE_LICENSE_DECLARED: _check_license_declared,
    RULE_LICENSE_FILE_DECLARED: _check_license_file_declared,
    RULE_SOURCE_URL_PRESENT: _check_source_url_present,
}


def lint_recipe(recipe: Recipe, rules: Iterable[str] = RULES) -> list[Finding]:
    """Run the requested *rules* against *recipe*, in rule order."""
    wanted = list(rules)
    findings: list[Finding] = []
    for rule in RULES:
        if rule not in wanted:
            continue
        finding = _CHECKS[rule](recipe)  # type: ignore[call-overload]
        assert isinstance(finding, Finding)
        findings.append(finding)
    return findings


# --------------------------------------------------------------------------- #
# Per-tool result
# --------------------------------------------------------------------------- #


@dataclass
class LintResult:
    """The lint outcome for one tool: its findings and an overall pass/fail."""

    tool: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every finding passed (no violations)."""
        return all(finding.ok for finding in self.findings)

    @property
    def violations(self) -> list[Finding]:
        """The findings that failed (``ok is False``)."""
        return [f for f in self.findings if not f.ok]

    def to_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "violations": [v.to_dict() for v in self.violations],
        }


def lint_tool(pkg_dir: Path, rules: Iterable[str] = RULES) -> LintResult:
    """Lint a single tool directory ``<pkg_dir>`` (``<name>/<name>.mk``)."""
    recipe = parse_recipe(pkg_dir)
    return LintResult(tool=recipe.name, findings=lint_recipe(recipe, rules))


# --------------------------------------------------------------------------- #
# Directory discovery + bulk lint
# --------------------------------------------------------------------------- #


def discover_tool_dirs(root: Path) -> list[Path]:
    """Return the tool directories under *root*, in sorted order.

    Three layouts are accepted, so ``lint --dir`` works on the emit output root,
    the ``package/`` directory, or a single ``<tool>`` directory:

    * ``<root>/package/<tool>/<tool>.mk`` — the emit output root or
      ``package/`` (recurses one level into ``package/`` if present).
    * ``<root>/<tool>/<tool>.mk`` — tool dirs directly under *root*.
    * ``<root>/<name>.mk`` — a single tool directory.
    """
    root = Path(root)
    if not root.is_dir():
        return []

    # Single tool dir: a ``<name>.mk`` sits directly under root.
    if any(p.name.endswith(".mk") for p in root.iterdir()):
        return [root]

    # Emit output root: recipes live under <root>/package/<tool>/.
    pkg = root / "package"
    if pkg.is_dir():
        return sorted(d for d in pkg.iterdir() if d.is_dir())

    # Tool dirs directly under root: <root>/<tool>/<tool>.mk.
    found = [
        d for d in sorted(root.iterdir()) if d.is_dir() and (d / f"{d.name}.mk").is_file()
    ]
    return found


def lint_dir(root: Path, rules: Iterable[str] = RULES) -> list[LintResult]:
    """Lint every tool directory under *root*; returns one :class:`LintResult` each."""
    dirs = discover_tool_dirs(root)
    return [lint_tool(d, rules) for d in dirs]


def lint_anything(target: Path, rules: Iterable[str] = RULES) -> list[LintResult]:
    """Lint *target*, whichever of the accepted layouts it is.

    Raises ``ValueError`` when no tool directory can be found under *target*.
    """
    results = lint_dir(target, rules)
    if not results:
        raise ValueError(f"no recipes found under {target}")
    return results


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _cmd_lint(args: argparse.Namespace) -> int:
    results = lint_anything(Path(args.dir), args.rules)
    if args.json:
        payload = {
            "version": __version__,
            "dir": str(args.dir),
            "rules": list(RULES),
            "results": [r.to_dict() for r in results],
            "all_ok": all(r.ok for r in results),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["all_ok"] else 1

    for r in results:
        status = "PASS" if r.ok else "FAIL"
        print(f"{status}  {r.tool}")
        for finding in r.findings:
            mark = "ok" if finding.ok else "!!"
            print(f"    [{mark}] {finding.rule}: {finding.message}")
        for v in r.violations:
            print(f"    violation: {v.message}")
    ok = all(r.ok for r in results)
    print(f"\n{len(results)} recipe(s), {sum(r.ok for r in results)} clean"
          f" — {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-recipe-server-clis lint",
        description="Lint an emitted pengyos-recipe-server-clis recipe directory "
        "(hash present, version pinned, license + license file declared, "
        "absolute https source URL).",
    )
    p_lint = parser.add_subparsers(dest="command", required=True).add_parser(
        "lint", help="lint an emitted recipe directory"
    )
    p_lint.add_argument(
        "--dir",
        required=True,
        help="recipe directory to lint (output root, package/, or a <tool> dir)",
    )
    p_lint.add_argument(
        "--rules",
        default=",".join(RULES),
        help="comma-separated subset of " + ", ".join(RULES)
        + " (default: all)",
    )
    p_lint.add_argument("--json", action="store_true", help="machine-readable output")
    p_lint.set_defaults(func=_cmd_lint)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    # ``--rules`` is a comma-separated list; split, trim, and validate against
    # the known rule set.
    requested = [r.strip() for r in getattr(args, "rules", "").split(",") if r.strip()]
    unknown = [r for r in requested if r not in RULES]
    if unknown:
        raise SystemExit(f"unknown rule(s): {', '.join(unknown)}")
    args.rules = requested or list(RULES)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
