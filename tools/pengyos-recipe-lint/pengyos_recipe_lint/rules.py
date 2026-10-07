"""Lint rules for Buildroot recipes (``pengyos_recipe_lint.rules``).

This layer answers a single question per rule: *does this recipe satisfy this
rule, and if not, why?*  It is deliberately free of any output formatting —
turning :class:`RuleResult` values into ``file:line`` reports and exit codes is
the next layer's job (``report``).

Four rules are implemented in this module:

* ``version_pinned`` — an explicit, non-auto ``_VERSION`` is present.
* ``hash_present`` — a ``.hash`` sibling carries a ``sha256`` for the source.
* ``license_declared`` — an ``_LICENSE`` is declared.
* ``no_unverified_downloads`` — the download site (``_SITE``) is a pinned,
  verifiable ``https`` mirror.

Layering: ``recipes`` -> ``recipe_parser`` -> ``rules`` ->
``report`` -> ``cli``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

from .recipe_parser import RecipeSource
from .recipes import Recipe


class RuleId(str, Enum):
    """Stable identifier for each lint rule.

    Subclasses as more rules are added.  Because it is a ``str`` mixin, the
    enum member serialises to its value in JSON (e.g. ``"version_pinned"``).
    """

    VERSION_PINNED = "version_pinned"
    HASH_PRESENT = "hash_present"
    LICENSE_DECLARED = "license_declared"
    NO_UNVERIFIED_DOWNLOADS = "no_unverified_downloads"


@dataclass(frozen=True)
class RuleResult:
    """The outcome of applying one rule to one recipe.

    ``message`` always describes the recipe and the outcome; ``line_no`` points
    at the offending assignment when the rule knows one (carried by the parser);
    ``hint`` offers a concrete next step.  ``ok`` is ``True`` only when the rule
    passed.
    """

    rule: RuleId
    ok: bool
    message: str
    line_no: int | None = None
    hint: str | None = None


# --- version_pinned -------------------------------------------------------- #

# A value equal to one of these (case-insensitive, whole token) is an
# auto/git-style branch reference rather than a concrete release pin.
_UNPINNED_VALUES = re.compile(
    r"^(git|head|auto|master|main|latest|trunk|stable|snapshot|current)$",
    re.IGNORECASE,
)

# A make-variable expansion such as ``$(FOO_VERSION)`` is resolved at build
# time and therefore is not a pin.
_MAKE_VAR_RE = re.compile(r"\$\(")


def version_pinned(recipe: Recipe, source: RecipeSource) -> RuleResult:
    """Rule: the recipe pins an explicit, non-auto ``_VERSION``.

    A version is *unpinned* when ``_VERSION`` is absent, empty, equal to a
    branch/alias name (``git``, ``HEAD``, ``auto``, ...), or a build-time make
    expansion (``$(...)``).
    """
    var = source.get_by_suffix("_VERSION")
    if var is None:
        return RuleResult(
            RuleId.VERSION_PINNED,
            False,
            f"{recipe.name}: _VERSION is not set (version not pinned)",
            hint="Assign a concrete release version, e.g. ``FOO_VERSION = 1.2.3``.",
        )

    value = var.value.strip()
    if value == "":
        return RuleResult(
            RuleId.VERSION_PINNED,
            False,
            f"{recipe.name}: _VERSION is empty",
            line_no=var.line_no,
            hint="Assign a concrete release version, e.g. ``FOO_VERSION = 1.2.3``.",
        )
    if _UNPINNED_VALUES.match(value):
        return RuleResult(
            RuleId.VERSION_PINNED,
            False,
            f"{recipe.name}: _VERSION is '{value}' (a branch/alias, not a concrete pin)",
            line_no=var.line_no,
            hint="Assign a concrete release version, e.g. ``FOO_VERSION = 1.2.3``.",
        )
    if _MAKE_VAR_RE.search(value):
        return RuleResult(
            RuleId.VERSION_PINNED,
            False,
            f"{recipe.name}: _VERSION '{value}' is resolved at build time (make expansion)",
            line_no=var.line_no,
            hint="Assign a concrete release version, e.g. ``FOO_VERSION = 1.2.3``.",
        )
    return RuleResult(
        RuleId.VERSION_PINNED,
        True,
        f"{recipe.name}: _VERSION pinned at {value}",
        line_no=var.line_no,
    )


# --- hash_present ---------------------------------------------------------- #


@dataclass(frozen=True)
class HashEntry:
    """One ``<algorithm>  <digest>  <filename>`` line from a ``.hash`` file."""

    algorithm: str
    digest: str
    filename: str
    line_no: int


def parse_hash_text(text: str) -> list[HashEntry]:
    """Parse ``.hash`` *text* into :class:`HashEntry` rows.

    Tolerates blank lines and ``#`` comments and lines that do not have at
    least three whitespace-separated fields.  The algorithm is lower-cased; the
    filename is the last field so trailing whitespace never leaks in.
    """
    entries: list[HashEntry] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            # Not a well-formed "<algo> <hash> <file>" line; ignore it.
            continue
        entries.append(
            HashEntry(
                algorithm=parts[0].lower(),
                digest=parts[1],
                filename=parts[-1],
                line_no=line_no,
            )
        )
    return entries


def hash_present(recipe: Recipe, source: RecipeSource) -> RuleResult:
    """Rule: a ``.hash`` sibling carries a ``sha256`` for the source.

    * Missing ``.hash`` sibling                -> fail.
    * ``.hash`` with no ``sha256`` entry       -> fail.
    * ``sha256`` present but not for the source -> fail.
    * ``sha256`` present for the source        -> pass.

    When ``_SOURCE`` is absent we cannot verify the filename match, so any
    ``sha256`` entry is accepted.
    """
    if recipe.hash_path is None:
        return RuleResult(
            RuleId.HASH_PRESENT,
            False,
            f"{recipe.name}: no .hash sibling to record the source hash",
            hint=(
                f"Create {recipe.path.with_suffix('.hash')} with a sha256 line, e.g.:\n"
                "    sha256  <64 hex chars>  <SOURCE>"
            ),
        )

    try:
        text = recipe.hash_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return RuleResult(
            RuleId.HASH_PRESENT,
            False,
            f"{recipe.name}: cannot read {recipe.hash_path} ({exc})",
            hint="Ensure the .hash file exists and is readable.",
        )

    entries = parse_hash_text(text)
    sha256_entries = [entry for entry in entries if entry.algorithm == "sha256"]
    if not sha256_entries:
        return RuleResult(
            RuleId.HASH_PRESENT,
            False,
            f"{recipe.name}: {recipe.hash_path} has no sha256 entry",
            hint="Add a sha256 line: 'sha256 <64 hex>  <SOURCE>' to the .hash file.",
        )

    source_var = source.get_by_suffix("_SOURCE")
    source_name = source_var.value.strip() if source_var is not None else None
    if source_name:
        if not any(entry.filename == source_name for entry in sha256_entries):
            return RuleResult(
                RuleId.HASH_PRESENT,
                False,
                f"{recipe.name}: sha256 entries do not cover source '{source_name}'",
                hint=f"Add 'sha256 <64 hex>  {source_name}' to {recipe.hash_path}.",
            )

    return RuleResult(
        RuleId.HASH_PRESENT,
        True,
        f"{recipe.name}: sha256 present for source",
        line_no=sha256_entries[0].line_no,
    )


# --- license_declared ------------------------------------------------------ #


def license_declared(recipe: Recipe, source: RecipeSource) -> RuleResult:
    """Rule: the recipe declares an ``_LICENSE``.

    An absent or empty ``_LICENSE`` fails; anything else passes.
    """
    var = source.get_by_suffix("_LICENSE")
    if var is None:
        return RuleResult(
            RuleId.LICENSE_DECLARED,
            False,
            f"{recipe.name}: _LICENSE is not declared",
            hint="Declare the license, e.g. ``FOO_LICENSE = MIT``.",
        )

    value = var.value.strip()
    if value == "":
        return RuleResult(
            RuleId.LICENSE_DECLARED,
            False,
            f"{recipe.name}: _LICENSE is empty",
            line_no=var.line_no,
            hint="Assign a license, e.g. ``FOO_LICENSE = MIT``.",
        )
    return RuleResult(
        RuleId.LICENSE_DECLARED,
        True,
        f"{recipe.name}: license '{value}' declared",
        line_no=var.line_no,
    )


# --- no_unverified_downloads ----------------------------------------------- #

# Schemes we treat as a *pinned, verifiable* download origin.  Only ``https``
# qualifies: it authenticates the origin over TLS, so a man-in-the-middle cannot
# silently swap the source.  ``http`` is insecure and every other scheme is
# either non-transport (``file``, ``mailto`` ...) or a non-HTTP transport
# (``git``, ``ftp`` ...) that this static linter cannot verify.
_VERIFIABLE_SCHEME = "https"


def site_is_verified(site: str) -> bool:
    """Return ``True`` when *site* is a pinned, verifiable ``https`` origin.

    A site is *verified* when it is an absolute URL whose scheme is exactly
    ``https``.  Anything else (missing, relative, ``http``, or a non-``https``
    scheme) is *unverified*.
    """
    site = site.strip()
    if not site:
        return False
    try:
        parsed = urlparse(site)
    except ValueError:
        return False
    # ``urlparse`` only assigns a ``scheme`` for absolute URLs such as
    # ``https://host/path``; a relative path like ``/foo`` yields ``scheme == ""``.
    return parsed.scheme == _VERIFIABLE_SCHEME


def no_unverified_downloads(recipe: Recipe, source: RecipeSource) -> RuleResult:
    """Rule: the download site (``_SITE``) is a pinned, verifiable ``https`` mirror.

    * ``_SITE`` absent / empty                       -> fail.
    * ``_SITE`` not an absolute URL                  -> fail.
    * ``_SITE`` uses an insecure ``http://`` scheme  -> fail.
    * ``_SITE`` uses any other (non-``https``) scheme -> fail.
    * ``_SITE`` is an absolute ``https`` URL         -> pass.
    """
    var = source.get_by_suffix("_SITE")
    if var is None:
        return RuleResult(
            RuleId.NO_UNVERIFIED_DOWNLOADS,
            False,
            f"{recipe.name}: _SITE is not set (download site not pinned)",
            hint="Set an https download site, e.g. ``FOO_SITE = https://mirror.example.org/foo``.",
        )

    site = var.value.strip()
    if not site:
        return RuleResult(
            RuleId.NO_UNVERIFIED_DOWNLOADS,
            False,
            f"{recipe.name}: _SITE is empty",
            line_no=var.line_no,
            hint="Set an https download site, e.g. ``FOO_SITE = https://mirror.example.org/foo``.",
        )

    try:
        scheme = urlparse(site).scheme.lower()
    except ValueError:
        scheme = ""

    if scheme == "":
        return RuleResult(
            RuleId.NO_UNVERIFIED_DOWNLOADS,
            False,
            f"{recipe.name}: _SITE '{site}' is not an absolute URL",
            line_no=var.line_no,
            hint="Use an absolute https URL, e.g. ``FOO_SITE = https://mirror.example.org/foo``.",
        )
    if scheme == "http":
        return RuleResult(
            RuleId.NO_UNVERIFIED_DOWNLOADS,
            False,
            f"{recipe.name}: _SITE '{site}' uses insecure http:// (pin an https mirror)",
            line_no=var.line_no,
            hint=(
                "Pin the download to an https mirror, e.g. "
                "``FOO_SITE = https://mirror.example.org/foo``."
            ),
        )
    if scheme != _VERIFIABLE_SCHEME:
        return RuleResult(
            RuleId.NO_UNVERIFIED_DOWNLOADS,
            False,
            f"{recipe.name}: _SITE '{site}' uses scheme '{scheme}' (only https mirrors are verified)",
            line_no=var.line_no,
            hint=(
                f"Pin the download to an https mirror, e.g. "
                f"``FOO_SITE = https://mirror.example.org/foo``."
            ),
        )

    return RuleResult(
        RuleId.NO_UNVERIFIED_DOWNLOADS,
        True,
        f"{recipe.name}: download site '{site}' is a pinned https mirror",
        line_no=var.line_no,
    )


# --- driver ---------------------------------------------------------------- #

# The canonical order rules are applied (and therefore reported) in.
RULE_ORDER: tuple[RuleId, ...] = (
    RuleId.VERSION_PINNED,
    RuleId.HASH_PRESENT,
    RuleId.LICENSE_DECLARED,
    RuleId.NO_UNVERIFIED_DOWNLOADS,
)


def check_recipe(recipe: Recipe, source: RecipeSource) -> list[RuleResult]:
    """Apply every implemented rule to *recipe* in stable order.

    The returned list always follows :data:`RULE_ORDER`, giving the report
    layer a deterministic base to format and sort from.
    """
    return [
        version_pinned(recipe, source),
        hash_present(recipe, source),
        license_declared(recipe, source),
        no_unverified_downloads(recipe, source),
    ]
