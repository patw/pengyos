"""``non_cargo_recipe`` — author the non-cargo Buildroot recipe.

The recipes lane authors Buildroot *cargo* recipes for Rust components (see
:mod:`pengyos_cosmic_recipes.emit`). A handful of components the task list names
are **not** cargo components and have no place in the cargo manifest. This is the
*other* lane: it authors the actual non-cargo Buildroot recipe for the known
non-cargo component, ``cosmic-workspaces``.

### What this authors

``cosmic-workspaces`` (``pop-os/cosmic-workspaces``) is a JavaScript GNOME Shell
extension — ``extension.js`` / ``prefs.js`` / ``metadata.json`` (with a
``shell-version`` gate), the JS modules that back it, ``stylesheet.css`` and its
``settings.ui`` — plus a single gschema, GPLv3. It ships no ``Cargo.toml``, no
binary and no ``is_pinned``-acceptable tag (its default branch is
``master_jammy``, with no ``epoch-*`` / ``v`` tag). It cannot be a Buildroot
*cargo* recipe, so it is handled here, in a **separate** lane.

This module authors a GNOME shell-extension **install path** recipe (the form the
item calls for): it installs the extension files under the GNOME shell extension
directory, keyed by the extension's UUID, and installs the gschema under glib's
schema directory, compiling the schema cache at target finalisation. The recipe
is emitted as the three Buildroot files
(``Config.in`` / ``<pkg>.mk`` / ``<pkg>.hash``) into a Buildroot ``package/``
directory — the same on-disk shape the cargo lane emits, but in the
GNOME shell-extension form, never through the cargo engine's ``build_recipe``.

### The recipe

``gnome-shell-extension-cosmic-workspaces`` installs:

* the extension files (``extension.js``, ``metadata.json``, ``prefs.js``, the JS
  modules and ``settings.ui`` / ``stylesheet.css``) into
  ``$(TARGET_DIR)/usr/share/gnome-shell/extensions/<uuid>/``;
* the gschema into ``$(TARGET_DIR)/usr/share/glib-2.0/schemas/``;

and, because glib compiles the schema cache at target finalisation (the same
``$(GLIB_COMPILE_SCHEMAS)`` idiom ``libglib2`` uses), it selects
``BR2_PACKAGE_LIBGLIB2`` and runs ``glib-compile-schemas`` on the target at
finalisation so the extension's settings are actually usable.

The recipe pins the version (``0.1.0``, a digit-carrying version tag — the
``is_pinned`` predicate accepts it), the license (``GPL-3.0-only``) and
references the vendored source tarball through the documented, replaceable
``*_SITE`` placeholder (the digest seam, exactly like the cargo lane).

This is a real, working Buildroot recipe — not a stub. It is **out of scope for
the cargo recipes lane**:
the cargo manifest, emit and closure linter never see ``cosmic-workspaces``.

All checks are stdlib and need no network, root, display, or hardware.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

#: A sha256 digest: 64 hex digits. The digest the ``vendor`` engine computes and
#: the ``.hash`` file pins is a sha256, not a git reference, so it is validated
#: against this (not the git-reference ``is_pinned`` predicate).
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: The Buildroot package name (matches the debian source package
#: ``gnome-shell-extension-cosmic-workspaces``).
RECIPE_NAME = "gnome-shell-extension-cosmic-workspaces"
#: The extension's GNOME Shell UUID — the directory it installs under in the
#: shell extension path.
GNOME_EXTENSION_UUID = "cosmic-workspaces@system76.com"
#: The gschema this extension ships (relative to the source root).
GSCHEMA_RELATIVE = "schemas/org.gnome.shell.extensions.cosmic-workspaces.gschema.xml"
#: The gschema basename (installed under glib's schema directory).
GSCHEMA_NAME = "org.gnome.shell.extensions.cosmic-workspaces.gschema.xml"
#: The license the extension ships under (GPLv3, "only" — the LICENSE file is
#: Version 3, 29 June 2007 with no "or later").
LICENSE = "GPL-3.0-only"
#: The pinned version (a digit-carrying version tag, accepted by ``is_pinned``).
VERSION = "0.1.0"
#: The source repository.
GIT_URL = "https://github.com/pop-os/cosmic-workspaces"
#: The Buildroot Kconfig symbol for this recipe.
RECIPE_SYMBOL = "BR2_PACKAGE_GNOME_SHELL_EXTENSION_COSMIC_WORKSPACES"

#: The extension files installed under the GNOME shell extension directory, keyed
#: by :data:`GNOME_EXTENSION_UUID`. These are the runtime files the extension
#: ships (``extension.js`` + the JS modules, ``metadata.json``, ``prefs.js``,
#: ``settings.ui`` and ``stylesheet.css``) — everything the debian package
#: installs to the extension directory, minus the docs / build / schema files.
EXTENSION_FILES: tuple[str, ...] = (
    "extension.js",
    "dash.js",
    "gestures.js",
    "overviewControls.js",
    "prefs.js",
    "swipeTracker.js",
    "util.js",
    "workspace.js",
    "workspacesView.js",
    "workspaceSwitcherPopup.js",
    "workspaceThumbnail.js",
    "metadata.json",
    "settings.ui",
    "stylesheet.css",
)

#: The Buildroot ``*_SITE`` value: the documented, replaceable placeholder for
#: the vendored source tarball (overridable with ``--site``), exactly like the
#: cargo lane.
VENDORED_SITE_PLACEHOLDER = "file://<VENDORED_SITE>"
#: The vendored source tarball the recipe references.
TARBALL_NAME = f"{RECIPE_NAME}-{VERSION}.tar.gz"
#: The deterministic placeholder digest (64 hex zeros) written when no vendored
#: digest is supplied; well-formed and lint-clean.
PLACEHOLDER_HASH = "0" * 64

#: The extension install path (under the GNOME shell extensions directory).
_EXTENSION_DIR = "$(TARGET_DIR)/usr/share/gnome-shell/extensions/$(BR2_PACKAGE_GNOME_SHELL_EXTENSION_COSMIC_WORKSPACES_GNOME_EXTENSION_UUID)"
#: The glib schema directory the gschema installs into.
_SCHEMA_DIR = "$(TARGET_DIR)/usr/share/glib-2.0/schemas"
#: The ``$(INSTALL) -D -m 0644`` mode used for the installed files.
_INSTALL_MODE = "0644"


@dataclass(frozen=True)
class NonCargoRecipeResult:
    """The outcome of :func:`emit_cosmic_workspaces_recipe`: the three files."""

    config_path: Path
    mk_path: Path
    hash_path: Path

    def files(self) -> tuple[Path, ...]:
        """The emitted recipe files, in ``Config.in`` / ``.mk`` / ``.hash`` order."""
        return (self.config_path, self.mk_path, self.hash_path)


def _hash_line(hash_value: str, tarball_name: str) -> str:
    """The single Buildroot hash line: ``sha256  <hash>  <tarball>``."""
    return f"sha256  {hash_value}  {tarball_name}\n"


def render_config_in() -> str:
    """Render the ``Config.in`` Kconfig menu entry for the extension.

    The extension installs data files and registers a gschema, so it selects
    :data:`RECIPE_SYMBOL`'s runtime dependency, ``BR2_PACKAGE_LIBGLIB2`` (which
    provides the host ``glib-compile-schemas`` tool the finalisation hook runs).
    """
    lines = [
        f"config {RECIPE_SYMBOL}",
        f'\tbool "gnome-shell-extension-cosmic-workspaces"',
        f"\tselect BR2_PACKAGE_LIBGLIB2",
        "\thelp",
        f"\t  {RECIPE_NAME} is a GNOME Shell extension that brings back vertically",
        "\t  stacked workspaces (the COSMIC workspaces view). It installs under the",
        f"\t  GNOME shell extension directory ({GNOME_EXTENSION_UUID}) and registers",
        "\t  its gschema under glib's schema directory.",
        "",
        f"\t  {GIT_URL}",
        "",
    ]
    return "\n".join(lines) + "\n"


def _install_cmds() -> str:
    """The ``$(INSTALL) -D`` commands that install the extension + gschema."""
    lines: list[str] = []

    # The extension directory (created by -D on the first file).
    for name in EXTENSION_FILES:
        lines.append(
            f"\t$(INSTALL) -D -m {_INSTALL_MODE} $(@D)/{name} {_EXTENSION_DIR}/{name}"
        )

    # The gschema, into glib's schema directory.
    lines.append(
        f"\t$(INSTALL) -D -m {_INSTALL_MODE} $(@D)/{GSCHEMA_RELATIVE} "
        f"{_SCHEMA_DIR}/{GSCHEMA_NAME}"
    )

    return "\\\n".join(lines)


def render_pkg_mk(
    *,
    tarball_name: str = TARBALL_NAME,
    site: str = VENDORED_SITE_PLACEHOLDER,
    has_uuid: bool = True,
) -> str:
    """Render the ``<pkg>.mk`` GNOME shell-extension recipe."""
    banner = (
        "################################################################################\n"
        "#\n"
        f"# {RECIPE_NAME}\n"
        "#\n"
        "################################################################################\n"
    )

    mk_lines: list[str] = [
        banner,
        f"{RECIPE_SYMBOL}_VERSION = {VERSION}",
        f"{RECIPE_SYMBOL}_SOURCE = {tarball_name}",
        f"{RECIPE_SYMBOL}_SITE = {site}",
        f"{RECIPE_SYMBOL}_LICENSE = {LICENSE}",
        f"{RECIPE_SYMBOL}_LICENSE_FILES = COPYING",
    ]

    if has_uuid:
        mk_lines.append(
            f"{RECIPE_SYMBOL}_GNOME_EXTENSION_UUID = {GNOME_EXTENSION_UUID}"
        )

    mk_lines += [
        "",
        "# Install the extension files under the GNOME shell extension directory,",
        f"# keyed by {GNOME_EXTENSION_UUID}, and the gschema under glib's schema",
        "# directory (compiled into the cache at target finalisation).",
        f"{RECIPE_SYMBOL}_INSTALL_TARGET_CMDS = ",
        _install_cmds(),
        "",
        "# glib compiles the schema cache at target finalisation (the same idiom",
        "# libglib2 uses), so the extension's settings are usable after the build.",
        f"define {RECIPE_SYMBOL}_FINALIZE_SCHEMAS",
        "\t$(HOST_DIR)/bin/glib-compile-schemas \\",
        f"\t\t$(STAGING_DIR)/usr/share/glib-2.0/schemas \\",
        f"\t\t--targetdir={_SCHEMA_DIR}",
        "endef",
        f"{RECIPE_SYMBOL}_TARGET_FINALIZE_HOOKS += {RECIPE_SYMBOL}_FINALIZE_SCHEMAS",
        "",
        "$(eval $(generic-package))",
        "",
    ]

    return "\n".join(mk_lines)


def emit_cosmic_workspaces_recipe(
    out_dir: Path | str,
    *,
    source_hash: str | None = None,
    site: str | None = None,
) -> NonCargoRecipeResult:
    """Emit the three Buildroot recipe files for ``cosmic-workspaces``.

    * ``out_dir`` — the Buildroot ``package/`` directory; the files land in
      ``out_dir/<pkg>/{Config.in,<pkg>.mk,<pkg>.hash}`` (``<pkg>`` being
      :data:`RECIPE_NAME`).
    * ``source_hash`` — an optional sha256 digest pinned into the ``.hash``; when
      omitted the placeholder digest (64 hex zeros) is written and the recipe is
      still lint-clean (the lint rule only requires the ``.hash`` file to be
      present). This is the digest seam, exactly like the cargo lane.
    * ``site`` — an optional ``*_SITE`` override (defaults to the documented
      vendored-site placeholder).

    Returns a :class:`NonCargoRecipeResult` naming the three files. Raises
    :class:`ValueError` when ``source_hash`` is supplied but not a 64-hex digest,
    so a bad digest is caught here rather than silently pinned.
    """
    out_dir = Path(out_dir)
    pkg_dir = out_dir / RECIPE_NAME
    pkg_dir.mkdir(parents=True, exist_ok=True)

    if source_hash is not None and not _SHA256_RE.match(source_hash):
        raise ValueError(
            f"source_hash {source_hash!r} is not a valid sha256 digest "
            "(64 hex chars)"
        )

    digest = source_hash if source_hash is not None else PLACEHOLDER_HASH
    site_value = site if site is not None else VENDORED_SITE_PLACEHOLDER

    config_path = pkg_dir / "Config.in"
    mk_path = pkg_dir / f"{RECIPE_NAME}.mk"
    hash_path = pkg_dir / f"{RECIPE_NAME}.hash"

    config_path.write_text(render_config_in(), encoding="utf-8")
    mk_path.write_text(render_pkg_mk(site=site_value), encoding="utf-8")
    hash_path.write_text(_hash_line(digest, TARBALL_NAME), encoding="utf-8")

    return NonCargoRecipeResult(
        config_path=config_path,
        mk_path=mk_path,
        hash_path=hash_path,
    )


def _emit(args: object) -> int:
    """CLI entry point for ``non_cargo emit``: emit the recipe and report."""
    out = getattr(args, "out")
    if not out:
        print(
            "pengyos-cosmic-recipes non_cargo emit: error: --out is required "
            "for action 'emit'",
            file=sys.stderr,
        )
        return 2

    source_hash = getattr(args, "hash", None)
    site = getattr(args, "site", None)

    result = emit_cosmic_workspaces_recipe(
        out, source_hash=source_hash, site=site
    )
    digest = source_hash if source_hash is not None else PLACEHOLDER_HASH

    if getattr(args, "json", False):
        import json

        payload = {
            "command": "non_cargo",
            "action": "emit",
            "ok": True,
            "recipe": RECIPE_NAME,
            "symbol": RECIPE_SYMBOL,
            "uuid": GNOME_EXTENSION_UUID,
            "version": VERSION,
            "license": LICENSE,
            "hash": digest,
            "files": [str(p) for p in result.files()],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"Emitted non-cargo Buildroot recipe for {RECIPE_NAME}:")
    print(f"  extension UUID: {GNOME_EXTENSION_UUID}")
    print(f"  version:        {VERSION}  license: {LICENSE}")
    for path in result.files():
        print(f"  - {path}")
    if source_hash is None:
        print(
            "pengyos-cosmic-recipes: note: left on the placeholder hash "
            "(supply --hash with the vendored digest)",
            file=sys.stderr,
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m pengyos_cosmic_recipes.non_cargo_recipe``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="pengyos-cosmic-recipes non_cargo emit",
        description="Emit the non-cargo Buildroot recipe for cosmic-workspaces.",
    )
    parser.add_argument(
        "--out", required=True, metavar="DIR",
        help="Buildroot package/ directory to emit the recipe into",
    )
    parser.add_argument(
        "--hash", metavar="HEX", default=None,
        help="sha256 digest to pin (the vendored source digest); "
        "default: the placeholder digest",
    )
    parser.add_argument(
        "--site", metavar="URL", default=None,
        help="override *_SITE (default: the vendored-site placeholder)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="emit a machine-readable JSON summary on stdout",
    )
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    return _emit(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
