"""``recipe`` — emit a Buildroot cargo package.

Emit ``package/<pkg>/{Config.in,<pkg>.mk,<pkg>.hash}`` in Buildroot cargo form
with the version and license pinned, referencing the vendored source tarball
produced by :mod:`pengyos_cosmic_pkg.vendor`.

### The three files

``Config.in`` is the Kconfig menu entry. Buildroot's cargo infrastructure
(``package/pkg-cargo.mk``) requires a Rust host toolchain, so the entry depends
on ``BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS`` and selects
``BR2_PACKAGE_HOST_RUSTC`` (the canonical form, from
``docs/manual/adding-packages-cargo.adoc`` and real packages such as
``package/bat``):

::

    config BR2_PACKAGE_COSMIC_DEMO
        bool "cosmic-demo"
        depends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS
        select BR2_PACKAGE_HOST_RUSTC
        help
          cosmic-demo — a COSMIC git component.

          https://github.com/pop-os/cosmic-demo

``<pkg>.mk`` is the cargo package recipe. It pins the version, names the vendored
source tarball, declares the license (and its files), and forwards the cargo
features / binaries the manifest asks for, then hands off to the
``$(eval $(cargo-package))`` infrastructure:

::

    ################################################################################
    #
    # cosmic-demo
    #
    ################################################################################

    COSMIC_DEMO_VERSION = v0.1.0
    COSMIC_DEMO_SOURCE = cosmic-demo-v0.1.0.tar.gz
    COSMIC_DEMO_SITE = file://<VENDORED_SITE>
    COSMIC_DEMO_LICENSE = MIT OR Apache-2.0
    COSMIC_DEMO_LICENSE_FILES = LICENSE-MIT LICENSE-APACHE
    COSMIC_DEMO_CARGO_FEATURES = default
    COSMIC_DEMO_CARGO_BINARIES = cosmic-demo

    $(eval $(cargo-package))

``<pkg>.hash`` is the Buildroot hash file (one ``sha256  <hash>  <tarball>``
line) that ``package/pkg-generic.mk`` auto-discovers and checks the downloaded
tarball against (``support/download/check-hash``).

### The vendored source

The recipe references the tarball the ``vendor`` engine produces:
``<name>-<version>.tar.gz``. That tarball is wrapped in a single top-level
``<name>-<version>/`` directory (see :mod:`pengyos_cosmic_pkg.vendor`) so
Buildroot's cargo download post-process recognises it as *already vendored* and
skips re-running ``cargo vendor`` (which would fail offline and rewrite the
tree).

The exact on-disk location of that tarball is environment-specific, so
``*_SITE`` defaults to the documented, replaceable placeholder
``file://<VENDORED_SITE>`` (overridable with ``--site``).

"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .manifest import ComponentManifest

#: Buildroot hash line format: "<type>  <hash>  <filename>".
_HASH_LINE_FMT = "%s  %s  %s\n"
#: The documented, replaceable placeholder for the vendored-site site.
VENDORED_SITE_PLACEHOLDER = "file://<VENDORED_SITE>"
#: Kconfig tab/space indentation used by Buildroot's generated recipes.
_INDENT = "\t"
#: Help-text body indentation (two spaces under ``help``).
_HELP_INDENT = "  "

#: SPDX license identifier -> the conventional license file Buildroot looks for.
_LICENSE_FILE_BY_ID: dict[str, str] = {
    "MIT": "LICENSE-MIT",
    "Apache-2.0": "LICENSE-APACHE",
    "GPL-2.0-only": "COPYING",
    "GPL-2.0": "COPYING",
    "GPL-3.0-only": "COPYING",
    "GPL-3.0": "COPYING",
    "BSD-2-Clause": "LICENSE",
    "BSD-3-Clause": "LICENSE",
}


@dataclass(frozen=True)
class RecipeResult:
    """The outcome of a :func:`build_recipe` run: the three files written."""

    config_path: Path
    mk_path: Path
    hash_path: Path
    symbol: str
    version: str
    tarball_name: str

    def files(self) -> list[Path]:
        """The emitted recipe files, in ``Config.in`` / ``.mk`` / ``.hash`` order."""
        return [self.config_path, self.mk_path, self.hash_path]


def recipe_symbol(pkg: str) -> str:
    """Return the Buildroot Kconfig symbol for a package name.

    Buildroot uppercases the package name and renders dashes as underscores
    (``cosmic-demo`` -> ``BR2_PACKAGE_COSMIC_DEMO``), so the symbol is a valid
    Kconfig identifier and matches ``$(call UPPERCASE,$(pkgname))`` usage in the
    cargo infrastructure.
    """
    return f"BR2_PACKAGE_{pkg.upper().replace('-', '_')}"


def _kconfig_symbol(pkg: str) -> str:
    """Alias kept for readability at call sites (see :func:`recipe_symbol`)."""
    return recipe_symbol(pkg)


def license_files(license_expr: str) -> list[str]:
    """Map an SPDX license expression to the conventional license filenames.

    The manifest carries an SPDX *expression* (e.g. ``"MIT OR Apache-2.0"``),
    not the license files Buildroot needs. Each operand is mapped to its
    conventional file (``MIT`` -> ``LICENSE-MIT``, ``Apache-2.0`` ->
    ``LICENSE-APACHE``, ``GPL-*`` -> ``COPYING``, else ``LICENSE``) and the
    operands are joined in order, so a two-license expression yields
    ``["LICENSE-MIT", "LICENSE-APACHE"]``. Unknown operands fall back to
    ``LICENSE``.
    """
    files: list[str] = []
    for operand in license_expr.split(" OR "):
        key = operand.strip()
        files.append(_LICENSE_FILE_BY_ID.get(key, "LICENSE"))
    return files


def _help_body(manifest: ComponentManifest) -> list[str]:
    """The help-text body: a one-line description plus the source URL."""
    body = [
        f"{manifest.name} — a COSMIC git component.",
        "",
        manifest.git_url,
    ]
    return body


def render_config_in(manifest: ComponentManifest) -> str:
    """Render the ``Config.in`` Kconfig menu entry for the component."""
    symbol = recipe_symbol(manifest.name)
    lines: list[str] = [
        f"config {symbol}",
        f'{_INDENT}bool "{manifest.name}"',
        _INDENT + "depends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS",
        _INDENT + "select BR2_PACKAGE_HOST_RUSTC",
        "help",
    ]
    for line in _help_body(manifest):
        lines.append(f"{_HELP_INDENT}{line}")
    lines.append("")
    return "\n".join(lines)


def render_pkg_mk(
    manifest: ComponentManifest,
    *,
    tarball_name: str,
    site: str = VENDORED_SITE_PLACEHOLDER,
) -> str:
    """Render the ``<pkg>.mk`` cargo package recipe.

    * ``tarball_name`` — the vendored source tarball (``<name>-<version>.tar.gz``).
    * ``site`` — the ``*_SITE`` value (defaults to the documented placeholder).
    """
    symbol = recipe_symbol(manifest.name)
    version = manifest.version
    license_files_ = license_files(manifest.license)

    banner = (
        "################################################################################\n"
        "#\n"
        f"# {manifest.name}\n"
        "#\n"
        "################################################################################\n"
    )

    lines: list[str] = [
        banner,
        f"{symbol}_VERSION = {version}",
        f"{symbol}_SOURCE = {tarball_name}",
        f"{symbol}_SITE = {site}",
        f"{symbol}_LICENSE = {manifest.license}",
        f"{symbol}_LICENSE_FILES = {' '.join(license_files_)}",
    ]

    if manifest.has_features():
        lines.append(
            f"{symbol}_CARGO_FEATURES = {' '.join(manifest.features)}"
        )
    if manifest.has_binaries():
        lines.append(
            f"{symbol}_CARGO_BINARIES = {' '.join(manifest.binaries)}"
        )

    lines.append("")
    lines.append("$(eval $(cargo-package))")
    lines.append("")
    return "\n".join(lines)


def hash_line(hash_value: str, tarball_name: str) -> str:
    """The single Buildroot hash line: ``sha256  <hash>  <tarball>``."""
    return _HASH_LINE_FMT % ("sha256", hash_value, tarball_name)


def read_source_hash(hash_file: Path | str) -> str:
    """Return the sha256 digest from a Buildroot ``.hash`` file.

    The file is the one :mod:`pengyos_cosmic_pkg.vendor` produces (a single
    ``sha256  <hash>  <tarball>`` line, possibly with comment lines). The digest
    is the second whitespace-separated field of the first non-comment line.
    Raises :class:`ValueError` when the file is missing, empty, or has no valid
    hash line.
    """
    path = Path(hash_file)
    if not path.is_file():
        raise ValueError(f"hash file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 2 or fields[0] != "sha256":
            raise ValueError(
                f"unexpected hash line in {path.name}: {line!r} "
                "(expected 'sha256  <hash>  <tarball>')"
            )
        return fields[1]
    raise ValueError(f"no hash line in {path.name}")


def build_recipe(
    manifest: ComponentManifest,
    *,
    source_hash: str,
    out_dir: Path | str,
    site: str = VENDORED_SITE_PLACEHOLDER,
) -> RecipeResult:
    """Emit the three Buildroot recipe files for ``manifest`` into ``out_dir``.

    * ``source_hash`` — the sha256 of the vendored source tarball (the value the
      ``vendor`` engine computed).
    * ``out_dir`` — the Buildroot ``package/`` directory; the files land in
      ``out_dir/<pkg>/{Config.in,<pkg>.mk,<pkg>.hash}``.
    * ``site`` — the ``*_SITE`` value (defaults to the documented placeholder).

    Returns a :class:`RecipeResult` naming the three files.
    """
    pkg_dir = Path(out_dir) / manifest.name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    symbol = recipe_symbol(manifest.name)
    tarball_name = f"{manifest.name}-{manifest.version}.tar.gz"

    config_path = pkg_dir / "Config.in"
    mk_path = pkg_dir / f"{manifest.name}.mk"
    hash_path = pkg_dir / f"{manifest.name}.hash"

    config_path.write_text(render_config_in(manifest), encoding="utf-8")
    mk_path.write_text(
        render_pkg_mk(manifest, tarball_name=tarball_name, site=site),
        encoding="utf-8",
    )
    hash_path.write_text(
        hash_line(source_hash, tarball_name),
        encoding="utf-8",
    )

    return RecipeResult(
        config_path=config_path,
        mk_path=mk_path,
        hash_path=hash_path,
        symbol=symbol,
        version=manifest.version,
        tarball_name=tarball_name,
    )


def _emit(args, result: RecipeResult) -> int:
    """Print the recipe result (human or JSON) and return the exit code."""
    if getattr(args, "json", False):
        payload = {
            "command": args.command,
            "ok": True,
            "symbol": result.symbol,
            "version": result.version,
            "tarball": result.tarball_name,
            "files": [str(p) for p in result.files()],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Emitted Buildroot cargo package for {result.symbol}:")
        for path in result.files():
            print(f"  - {path}")
    return 0


def main(args) -> int:
    """CLI entry point for ``recipe``."""
    manifest = load_manifest(args.manifest)
    source_hash = read_source_hash(args.hash_file)
    result = build_recipe(
        manifest,
        source_hash=source_hash,
        out_dir=args.out,
        site=args.site or VENDORED_SITE_PLACEHOLDER,
    )
    return _emit(args, result)


def load_manifest(path: Path | str) -> ComponentManifest:
    """Read and validate a component manifest (imported lazily to avoid a cycle)."""
    from .manifest import load_manifest as _load

    return _load(path)
