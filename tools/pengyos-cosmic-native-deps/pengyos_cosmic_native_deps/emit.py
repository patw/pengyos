"""``emit``: render the BR2_EXTERNAL recipes + enabling fragment from the catalogue.

The recipe catalogue (``data/recipes.json``) records, for every dependency that
``2026.08`` does not ship, the concrete metadata an emit step turns into a
``package/<dep>/{Config.in,<dep>.mk,<dep>.hash}`` trio (see
:mod:`.recipes`). This module is that *rendering + writing* step:

* :func:`render_config_in` / :func:`render_mk` / :func:`render_hash` build the
  three files for one recipe, mirroring the existing Buildroot ``BR2_EXTERNAL``
  recipe style (see ``~/build/pengyos-2608/buildroot/package/<pkg>/``).
* :func:`render_fragment` builds ``fragments/cosmic-native.fragment`` — the
  kconfig fragment that enables exactly the emitted symbols.
* :func:`emit` writes them all into a ``BR2_EXTERNAL`` tree root and returns an
  :class:`EmitReport`.

The rendering is **deterministic**: the same catalogue always produces the same
bytes, which is what makes the golden-file tests meaningful.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import catalogue as _catalogue
from .recipes import BuildSystem, Recipe, RecipeCatalogue

# The Buildroot package/ directory a recipe lives in.
_PACKAGE_DIR = "package"
# The fragments/ directory a enabling kconfig fragment lives in.
_FRAGMENTS_DIR = "fragments"
# The enabling fragment name.
_FRAGMENT_NAME = "cosmic-native.fragment"

# The tab Buildroot uses to indent a Config.in entry (kconfig is whitespace
# sensitive — a space here would be a parse error).
_TAB = "\t"
# The indentation of a Config.in help-text line: a tab followed by two spaces
# (Buildroot's ``check-package`` regex is ``^\t  .{,62}$`` — a literal tab plus
# two *space* characters, not two tabs).
_HELP_INDENT = "\t  "


# --- rendering: Config.in ---------------------------------------------------


def _config_entry_body(recipe: Recipe, description: str) -> str:
    """The indented body of one ``config`` entry (the ``bool`` / ``select`` /
    ``help`` lines), including the leading tab on every line."""
    lines: list[str] = [f"{_TAB}bool \"{recipe.name}\""]
    if recipe.build_system is BuildSystem.CARGO:
        # A cargo package needs the host Rust toolchain to build; the Buildroot
        # cargo-package infra requires the select + arch-supports dependency.
        lines.append(
            f"{_TAB}depends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS"
        )
        lines.append(f"{_TAB}select BR2_PACKAGE_HOST_RUSTC")
    lines.append(f"{_TAB}help")
    lines.append("")
    # Buildroot's Config.in help text is indented <tab><2 spaces> (the first
    # line must start with a non-space after the two spaces); the URL line is
    # conventionally indented the same way.
    lines.append(f"{_HELP_INDENT}{description or _fallback_description(recipe)}")
    lines.append("")
    lines.append(f"{_HELP_INDENT}{recipe.url}")
    return "\n".join(lines)


def _fallback_description(recipe: Recipe) -> str:
    """A one-line help text for *recipe* when the catalogue has none.

    The recipe model (``data/recipes.json``) carries no human description, so
    :func:`emit` looks one up from the candidate catalogue; when that is not
    available this derives a neutral line from the enabled symbol.
    """
    return f"Buildroot package enabling {recipe.br_symbols[0]}"


def render_config_in(recipe: Recipe, description: str | None = None) -> str:
    """Render the ``Config.in`` for *recipe*.

    The entry is keyed off the recipe's primary kconfig symbol (the ``config``
    name), with the recipe name as the human ``bool`` label. *description* is the
    optional human help line (looked up from the candidate catalogue by
    :func:`emit`); a neutral fallback is used when it is not given. Data-only
    (``none``) recipes render the same shape — a plain ``config`` with no build
    infra.
    """
    symbol = recipe.br_symbols[0]
    return (
        f"config {symbol}\n"
        f"{_config_entry_body(recipe, description or '')}\n"
    )


# --- rendering: .mk ---------------------------------------------------------


def _mk_header(name: str) -> str:
    """The standard Buildroot recipe banner for *name*."""
    return (
        "################################################################################\n"
        "#\n"
        f"# {name}\n"
        "#\n"
        "################################################################################\n"
    )


def _mk_site(recipe: Recipe) -> str:
    """The Buildroot ``<PKG>_SITE`` for *recipe*.

    The catalogue records the full archive ``download_url`` (which ends with the
    pinned ``_SOURCE`` filename) plus the ``_SOURCE`` filename separately. A
    Buildroot archive ``_SITE`` is the URL *without* the trailing filename — the
    generic download infrastructure joins ``<PKG>_SITE`` + ``/`` +
    ``<PKG>_SOURCE``, so the ``_SITE`` is the download URL with the
    ``/<source>`` suffix removed (and any now-dangling slash stripped, so the
    joined URL has exactly one slash before the source).
    """
    if not recipe.download_url.endswith(recipe.source):
        raise ValueError(
            f"recipe {recipe.name!r}: download_url "
            f"{recipe.download_url!r} must end with source {recipe.source!r}"
        )
    site = recipe.download_url[: -len(recipe.source)]
    return site.rstrip("/")


def _mk_package_eval(build_system: BuildSystem) -> str:
    """The ``$(eval $(...))`` line that wires a recipe into the right Buildroot
    build-system infrastructure."""
    return {
        BuildSystem.MESON: "$(eval $(meson-package))",
        BuildSystem.AUTOTOOLS: "$(eval $(autotools-package))",
        BuildSystem.MAKE: "$(eval $(generic-package))",
        BuildSystem.CARGO: "$(eval $(cargo-package))",
    }[build_system]


def render_mk(recipe: Recipe) -> str:
    """Render the ``<pkg>.mk`` for *recipe*.

    Mirrors the Buildroot recipe style: the banner, the pinned ``_VERSION`` /
    ``_SOURCE`` / ``_SITE`` metadata, the declared license, and the final
    ``$(eval $(...))`` that selects the correct build-system infrastructure
    (``meson-package`` / ``cargo-package`` / ...). Data-only (``none``) recipes
    carry no build step, so :func:`render_mk` returns ``None`` for them.
    """
    if recipe.build_system is BuildSystem.NONE:
        return ""

    pkg = recipe.name.replace("-", "_").upper()
    site = _mk_site(recipe)
    eval_line = _mk_package_eval(recipe.build_system)
    # The Make variable reference is `$(GREETD_VERSION)` — the parens around the
    # upper-cased name are the Make expansion delimiters, produced literally.
    version_ref = f"$({pkg}_VERSION)"
    # The banner already ends with a newline, so the first "" below is the single
    # blank line that separates the banner from the variables (Buildroot style).
    return "\n".join(
        [
            _mk_header(recipe.name).rstrip("\n"),
            "",
            f"{pkg}_VERSION = {recipe.version}",
            f"{pkg}_SOURCE = {version_ref}.tar.gz",
            f"{pkg}_SITE = {site}",
            f"{pkg}_LICENSE = {recipe.license}",
            f"{pkg}_LICENSE_FILES = LICENSE",
            "",
            eval_line,
            "",
        ]
    )


# --- rendering: .hash -------------------------------------------------------


def render_hash(recipe: Recipe) -> str:
    """Render the ``<pkg>.hash`` for *recipe*.

    A single ``sha256  <hash>  <source>`` line — exactly three whitespace
    separated fields, two spaces between each (Buildroot's ``check-package``
    enforces this). The source key is the ``_SOURCE`` filename. Data-only (``none``)
    recipes carry no pinned source, so this returns ``""`` for them.
    """
    if recipe.build_system is BuildSystem.NONE:
        return ""
    return f"sha256  {recipe.hash}  {recipe.source}\n"


# --- rendering: the fragment ------------------------------------------------


def render_fragment(recipes: Iterable[Recipe]) -> str:
    """Render the ``cosmic-native.fragment`` enabling *recipes*' symbols.

    The fragment is a kconfig fragment (the ``BR2_*`` assignments Buildroot
    merges into ``.config``). It enables every symbol each recipe enables, in
    declaration order, so selecting the package also selects its sub-options
    (e.g. every Fira/Noto font family the ``fonts`` recipe enables). The output
    is sorted for determinism.
    """
    symbols: list[str] = []
    for recipe in recipes:
        symbols.extend(recipe.br_symbols)
    enabled = sorted(set(symbols))
    if not enabled:
        return ""
    body = "\n".join(f"{symbol}=y" for symbol in enabled)
    return body + "\n"


# --- the report -------------------------------------------------------------


@dataclass(frozen=True)
class EmitFile:
    """One file written by :func:`emit` (path relative to the tree root)."""

    path: str
    """The file's path relative to the ``BR2_EXTERNAL`` tree root."""

    kind: str
    """``"recipe"`` for a ``Config.in`` / ``.mk`` / ``.hash``; ``"fragment"``."""


@dataclass(frozen=True)
class EmitReport:
    """The outcome of an :func:`emit` run."""

    out_dir: Path
    """The tree root the recipes were written into."""

    files: tuple[EmitFile, ...] = field(default_factory=tuple)
    """Every file written, in write order."""

    @property
    def count(self) -> int:
        """The number of files written."""
        return len(self.files)

    def as_dict(self) -> dict:
        """A deterministic, JSON-serialisable view."""
        return {
            "out_dir": str(self.out_dir),
            "count": self.count,
            "files": [{"path": f.path, "kind": f.kind} for f in self.files],
        }


# --- the writer -------------------------------------------------------------


def _recipe_files(
    recipe: Recipe, description: str = ""
) -> list[tuple[str, str]]:
    """The (relative path, content) pairs for one recipe's files.

    A buildable recipe yields ``Config.in`` + ``<pkg>.mk`` + ``<pkg>.hash``; a
    data-only (``none``) recipe yields only ``Config.in``. *description* is the
    optional human help line for the ``Config.in`` (looked up by :func:`emit`).
    """
    pkg_dir = f"{_PACKAGE_DIR}/{recipe.name}"
    entries: list[tuple[str, str]] = [
        (f"{pkg_dir}/Config.in", render_config_in(recipe, description)),
    ]
    mk = render_mk(recipe)
    if mk:
        entries.append((f"{pkg_dir}/{recipe.name}.mk", mk))
    hash_content = render_hash(recipe)
    if hash_content:
        entries.append((f"{pkg_dir}/{recipe.name}.hash", hash_content))
    return entries


def emit(catalogue: RecipeCatalogue, out_dir: str | Path) -> EmitReport:
    """Write every recipe in *catalogue* + the fragment into *out_dir*.

    Creates the ``package/<dep>/`` and ``fragments/`` directories as needed
    (idempotent — re-running overwrites the files with identical bytes). Returns
    an :class:`EmitReport` listing every file written, in write order: each
    recipe's files (``Config.in`` first, then ``.mk`` / ``.hash``) in catalogue
    order, then the fragment last.
    """
    root = Path(out_dir)
    written: list[EmitFile] = []

    # The recipe model carries no human description, so look one up from the
    # candidate catalogue (keyed by name) for the Config.in help line.
    descriptions = {c.name: c.description for c in _catalogue.candidates()}

    for recipe in catalogue.recipes:
        desc = descriptions.get(recipe.name, "")
        for rel_path, content in _recipe_files(recipe, desc):
            target = root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(EmitFile(rel_path, "recipe"))

    fragment_path = f"{_FRAGMENTS_DIR}/{_FRAGMENT_NAME}"
    fragment_content = render_fragment(catalogue.recipes)
    (root / _FRAGMENTS_DIR).mkdir(parents=True, exist_ok=True)
    (root / fragment_path).write_text(fragment_content, encoding="utf-8")
    written.append(EmitFile(fragment_path, "fragment"))

    return EmitReport(out_dir=root, files=tuple(written))
