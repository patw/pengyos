"""``emit`` — emit Buildroot recipes pinning the REAL digest + SITE.

Emit ``package/<pkg>/{Config.in,<pkg>.mk,<pkg>.hash}`` for every component in a
manifest group, pinning the **real** vendored digest + SITE (never the
``file://<VENDORED_SITE>`` placeholder) and keyed off the ``BR2_PACKAGE_*``
symbol the desktop defconfig references (see :func:`symbol_for`).

The recipes lane (``pengyos-cosmic-recipes``) owns the manifest; this lane owns
the vendored-source records produced by :mod:`.vendor_all`. :func:`emit_group`
joins them: for each component it looks up the component's
:class:`~pengyos_cosmic_build.records.VendorRecord` (the tarball name, the real
sha256, and the real SITE), renders the three Buildroot recipe files pinning
those, and writes them into the ``BR2_EXTERNAL`` ``package/`` dir.

### The three files

``Config.in`` is the Kconfig menu entry, keyed off ``symbol_for(pkg)`` (the
defconfig's convention), depending on + selecting the host Rust toolchain so it
builds with the cargo infrastructure:

::

    config BR2_PACKAGE_COSMIC_COMP
	bool "cosmic-comp"
	depends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS
	select BR2_PACKAGE_HOST_RUSTC
	help

	  cosmic-comp — a COSMIC git component.

	  https://github.com/pop-os/cosmic-comp

``<pkg>.mk`` is the cargo package recipe. It pins the version, names the vendored
source tarball, declares the **real** ``*_SITE`` (the ``file://<dl>/<pkg>/``
URI :func:`vendor_all` recorded), the license (and its files), and forwards the
cargo features / binaries the manifest asks for, then hands off to
``$(eval $(cargo-package))``:

::

    ################################################################################
    #
    # cosmic-comp
    #
   ################################################################################

    COSMIC_COMP_VERSION = v0.6.0
    COSMIC_COMP_SOURCE = cosmic-comp-v0.6.0.tar.gz
    COSMIC_COMP_SITE = file:///home/ops/dl/cosmic-comp
    COSMIC_COMP_LICENSE = MIT OR Apache-2.0
    COSMIC_COMP_LICENSE_FILES = LICENSE-MIT LICENSE-APACHE
    COSMIC_COMP_CARGO_BINARIES = cosmic-comp

    $(eval $(cargo-package))

``<pkg>.hash`` is the Buildroot hash file (one ``sha256  <hash>  <tarball>``
line) that ``package/pkg-generic.mk`` auto-discovers and checks the downloaded
tarball against.

### The REAL digest + SITE

This is the whole point of the lane: the recipes lane's own ``emit`` pins the
placeholder (no vendored digest is available there). **This** ``emit`` pins the
real digest + SITE the vendoring engine recorded. The safety net is
:func:`VendorRecord.validate` — it rejects a placeholder SITE — so a placeholder
can never reach an emitted recipe even if a hand-written record tried to carry
one.

"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from pengyos_cosmic_build.records import (
    VendorRecord,
    VendorRecordError,
    load_record,
    symbol_for,
)

#: The Buildroot ``package/`` directory a recipe lives in (relative to the
#: ``BR2_EXTERNAL`` tree root).
_PACKAGE_DIR = "package"

#: The tab Buildroot uses to indent a ``Config.in`` entry (kconfig is
#: whitespace-sensitive; a space here would be a parse error).
_TAB = "\t"
#: The indentation of a ``Config.in`` help-text line: a tab followed by two
#: spaces (Buildroot's ``check-package`` regex is ``^\t  .{,62}$`` — a literal
#: tab plus two *space* characters, not two tabs).
_HELP_INDENT = "\t  "

#: The single Buildroot hash line: ``sha256  <hash>  <tarball>`` (two spaces
#: between each field, as ``check-package``'s ``HashSpaces`` requires).
_HASH_LINE_FMT = "sha256  %s  %s\n"


class EmitError(RuntimeError):
    """A component could not be emitted (no vendor record, bad record).

    Raised by :func:`emit_group` so a missing record is a *reported* condition
    (a clean CLI exit), never a crash.
    """


@dataclass(frozen=True)
class EmitResult:
    """The outcome of emitting one component: its identity + emitted files.

    ``name`` / ``symbol`` / ``version`` are the component's identity; ``tarball``
    is the pinned vendored tarball; ``files`` are the three emitted recipe files
    in ``Config.in`` / ``.mk`` / ``.hash`` order.
    """

    name: str
    symbol: str
    version: str
    tarball: str
    files: tuple[Path, ...]

    @property
    def config_path(self) -> Path:
        """The emitted ``Config.in`` file."""
        return self.files[0]

    @property
    def mk_path(self) -> Path:
        """The emitted ``<pkg>.mk`` file."""
        return self.files[1]

    @property
    def hash_path(self) -> Path:
        """The emitted ``<pkg>.hash`` file."""
        return self.files[2]


@dataclass(frozen=True)
class EmitReport:
    """The outcome of :func:`emit_group`: the recipes emitted for a group.

    ``results`` is one :class:`EmitResult` per component in the group (file
    order). ``missing_records`` lists the group components with no vendor record
    (nothing was emitted for them); an empty tuple means every component in the
    group produced a recipe.
    """

    group: str
    out_dir: Path
    results: tuple[EmitResult, ...]
    missing_records: tuple[str, ...] = field(default_factory=tuple)

    def emitted_names(self) -> tuple[str, ...]:
        """The emitted component names, in file order."""
        return tuple(r.name for r in self.results)

    @property
    def is_complete(self) -> bool:
        """``True`` when every component in the group produced a recipe."""
        return not self.missing_records


def _mk_header(name: str) -> str:
    """The standard Buildroot recipe banner for *name*."""
    return (
        "################################################################################\n"
        "#\n"
        f"# {name}\n"
        "#\n"
        "################################################################################\n"
    )


def render_config_in(symbol: str, name: str, git_url: str) -> str:
    """Render the ``Config.in`` Kconfig menu entry for one component.

    * ``symbol`` — the ``BR2_PACKAGE_*`` symbol (the defconfig's convention,
      see :func:`symbol_for`).
    * ``name`` — the component's short name (the human ``bool`` label).
    * ``git_url`` — the canonical upstream repo (the help URL).
    """
    # Buildroot's check-package (and the Kconfig parser) want:
    #   * the ``help`` keyword tab-indented (the engine's bare ``help`` fails);
    #   * help-body lines ``<tab><2 spaces>`` (``_HELP_INDENT``);
    #   * the blank lines *between* help lines truly empty (a ``<tab><2 spaces>``
    #     blank line is "trailing whitespace");
    #   * no trailing blank line at end of file.
    # This mirrors the reference recipe in
    # ``buildroot-external/package/cosmic-portal/Config.in``.
    return (
        f"config {symbol}\n"
        f"{_TAB}bool \"{name}\"\n"
        f"{_TAB}depends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS\n"
        f"{_TAB}select BR2_PACKAGE_HOST_RUSTC\n"
        f"{_TAB}help\n"
        "\n"
        f"{_HELP_INDENT}{name} — a COSMIC git component.\n"
        "\n"
        f"{_HELP_INDENT}{git_url}\n"
    )


def _license_files(license_expr: str) -> str:
    """Map an SPDX license expression to the conventional license files.

    Mirrors the engine's ``license_files``: each ``OR``-joined operand maps to
    its conventional file (``MIT`` -> ``LICENSE-MIT``, ``Apache-2.0`` ->
    ``LICENSE-APACHE``, ``GPL-*`` -> ``COPYING``, else ``LICENSE``), joined by
    spaces.
    """
    _MAP: dict[str, str] = {
        "MIT": "LICENSE-MIT",
        "Apache-2.0": "LICENSE-APACHE",
        "GPL-2.0-only": "COPYING",
        "GPL-2.0": "COPYING",
        "GPL-3.0-only": "COPYING",
        "GPL-3.0": "COPYING",
        "BSD-2-Clause": "LICENSE",
        "BSD-3-Clause": "LICENSE",
        "MPL-2.0": "LICENSE",
        "OFL-1.1": "LICENSE",
    }
    files = [_MAP.get(operand.strip(), "LICENSE") for operand in license_expr.split(" OR ")]
    return " ".join(files)


def render_mk(
    *,
    name: str,
    version: str,
    tarball: str,
    site: str,
    license_expr: str,
    features: Sequence[str] = (),
    binaries: Sequence[str] = (),
) -> str:
    """Render the ``<pkg>.mk`` cargo package recipe for one component.

    Pins ``<PKG>_VERSION`` / ``<PKG>_SOURCE`` / ``<PKG>_SITE`` (the *real*
    vendored SITE, never the placeholder), the license (+ files), the optional
    cargo features / binaries, then the ``$(eval $(cargo-package))`` line.

    The Make variable prefix is the upper-cased package name
    (``cosmic-comp`` -> ``COSMIC_COMP``) — Buildroot's cargo infra references
    ``$(COSMIC_COMP_SITE)`` etc., NOT the ``BR2_PACKAGE_``-prefixed Kconfig
    symbol (that prefix is only for the ``config`` entry in ``Config.in``).
    """
    var = name.upper().replace("-", "_")
    banner = _mk_header(name).rstrip("\n")
    lines: list[str] = [
        banner,
        "",
        f"{var}_VERSION = {version}",
        f"{var}_SOURCE = {tarball}",
        f"{var}_SITE = {site}",
        f"{var}_LICENSE = {license_expr}",
        f"{var}_LICENSE_FILES = {_license_files(license_expr)}",
    ]
    if features:
        lines.append(f"{var}_CARGO_FEATURES = {' '.join(features)}")
    if binaries:
        lines.append(f"{var}_CARGO_BINARIES = {' '.join(binaries)}")
    lines.append("")
    lines.append("$(eval $(cargo-package))")
    lines.append("")
    return "\n".join(lines)


def render_hash(tarball: str, sha256: str) -> str:
    """Render the ``<pkg>.hash`` body: ``sha256  <hash>  <tarball>``."""
    return _HASH_LINE_FMT % (sha256, tarball)


def _recipe_dir(out_dir: Path, name: str) -> Path:
    """The ``package/<name>/`` directory for *name* under *out_dir*."""
    return out_dir / _PACKAGE_DIR / name


def emit_one(
    component: object,
    record: VendorRecord,
    *,
    out_dir: Path | str,
) -> EmitResult:
    """Emit the three recipe files for one component + record into *out_dir*.

    * ``component`` — the manifest component (needs ``name`` / ``git_url`` /
      ``license`` / ``features`` / ``binaries``).
    * ``record`` — the component's :class:`~pengyos_cosmic_build.records.VendorRecord`
      (already validated: real digest + real SITE).
    * ``out_dir`` — the ``BR2_EXTERNAL`` tree root (the recipe lands in
      ``out_dir/package/<name>/``).

    Raises :class:`EmitError` when *record* is malformed. The record is
    validated here (a defensive re-check) so a placeholder can never reach an
    emitted recipe.
    """
    name = component.name
    symbol = symbol_for(name)
    try:
        record.validate()  # real sha256 + real SITE, or a reported error
    except VendorRecordError as exc:
        raise EmitError(str(exc)) from exc

    pkg_dir = _recipe_dir(Path(out_dir), name)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    config_path = pkg_dir / "Config.in"
    mk_path = pkg_dir / f"{name}.mk"
    hash_path = pkg_dir / f"{name}.hash"

    config_path.write_text(
        render_config_in(symbol, name, component.git_url), encoding="utf-8"
    )
    mk_path.write_text(
        render_mk(
            name=name,
            version=record.version,
            tarball=record.tarball,
            site=record.site,
            license_expr=component.license,
            features=component.features,
            binaries=component.binaries,
        ),
        encoding="utf-8",
    )
    hash_path.write_text(
        render_hash(record.tarball, record.sha256), encoding="utf-8"
    )

    return EmitResult(
        name=name,
        symbol=symbol,
        version=record.version,
        tarball=record.tarball,
        files=tuple(p for p in (config_path, mk_path, hash_path)),
    )


def emit_group(
    manifest: object,
    group: str,
    vendor_dir: Path | str,
    out_dir: Path | str,
) -> EmitReport:
    """Emit the recipes for every component in *group*.

    * ``manifest`` — the loaded cosmic-recipes manifest (exposes
      ``entries(group)`` returning components with ``name`` / ``git_url`` /
      ``license`` / ``features`` / ``binaries``, and ``groups``).
    * ``group`` — the group to emit (one of ``core`` / ``login`` / ``shell`` /
      ``apps``); components are emitted in file order.
    * ``vendor_dir`` — the directory of per-component ``<name>.json`` vendor
      records produced by :func:`.vendor_all.vend_all`.
    * ``out_dir`` — the ``BR2_EXTERNAL`` tree root the recipes are written into.

    Raises :class:`EmitError` when the group has no components; a component with
    no vendor record is reported in the report's ``missing_records`` (not a
    crash); a malformed record raises :class:`VendorRecordError` (reported by
    the CLI).
    """
    entries = manifest.entries(group)
    if not entries:
        raise EmitError(
            f"group '{group}' has no components to emit "
            f"(known groups: {', '.join(manifest.groups)})"
        )

    out_dir = Path(out_dir)
    vendor_dir = Path(vendor_dir)

    results: list[EmitResult] = []
    missing_records: list[str] = []
    for entry in entries:
        record_path = vendor_dir / f"{entry.name}.json"
        if not record_path.is_file():
            missing_records.append(entry.name)
            continue
        try:
            record = load_record(record_path)
        except VendorRecordError as exc:
            raise EmitError(str(exc)) from exc
        results.append(emit_one(entry, record, out_dir=out_dir))

    return EmitReport(
        group=group,
        out_dir=out_dir,
        results=tuple(results),
        missing_records=tuple(missing_records),
    )


def _report_error(args: object, message: str) -> int:
    """Report a clean, non-zero emit error (human text or the JSON envelope).

    Mirrors the CLI's uniform error reporting: the message goes to stderr (human
    summaries stay clean) or the `{command, ok:false, error}` JSON envelope with
    ``--json``, so a consumer can dispatch on ``ok`` alone. Never a traceback.
    """
    if getattr(args, "json", False):
        print(json.dumps({"command": "emit", "ok": False, "error": message}))
    else:
        print(f"pengyos-cosmic-build: emit: {message}", file=sys.stderr)
    return 1


def _emit(args, report: EmitReport) -> int:
    """Print the (complete) emit result (human or JSON) and return the exit code."""
    if getattr(args, "json", False):
        payload = {
            "command": "emit",
            "ok": True,
            "group": report.group,
            "out": str(report.out_dir),
            "count": len(report.results),
            "components": [
                {
                    "name": r.name,
                    "symbol": r.symbol,
                    "version": r.version,
                    "tarball": r.tarball,
                    "files": [str(p) for p in r.files],
                }
                for r in report.results
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"Emitted {len(report.results)} recipe(s) for group "
            f"{report.group} into {report.out_dir}"
        )
        for result in report.results:
            print(f"  - {result.name}  {result.symbol}  {result.tarball}")
    return 0


def main(args: object) -> int:
    """CLI entry point for ``emit``."""
    manifest_path = Path(args.manifest)  # type: ignore[attr-defined]
    vendor_dir = Path(args.vendor)  # type: ignore[attr-defined]
    out_dir = Path(args.out)  # type: ignore[attr-defined]
    group = args.group  # type: ignore[attr-defined]

    from pengyos_cosmic_recipes.manifest import load_manifest as _load_manifest

    manifest = _load_manifest(manifest_path)
    report = emit_group(manifest, group, vendor_dir, out_dir)
    # A group with a component that has no vendor record is an incomplete emit:
    # fail loudly (exit 1) rather than ship a recipe set that silently drops a
    # component — the operator vendored it or not, there is no half-answer here.
    if not report.is_complete:
        return _report_error(
            args,
            "incomplete emit: no vendor record for "
            f"{', '.join(report.missing_records)}",
        )
    return _emit(args, report)
