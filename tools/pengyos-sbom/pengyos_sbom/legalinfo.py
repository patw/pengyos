"""Parse a Buildroot ``legal-info`` directory into raw manifest rows.

Ground truth: this parser is written against the *actual* Buildroot 2026.02.3
output (the initial M1 pin). The generation code was diffed verbatim against
Buildroot 2026.08 (the current-line engine per the PengyOS version policy)
and is identical, so the same layout applies. Layout of
``$(O)/legal-info/`` (produced by ``make legal-info``):

* ``manifest.csv``       -- target packages (required),
* ``host-manifest.csv``  -- host (build-time) packages (optional),
* ``licenses/<name>-<ver>/<file>``    -- target license texts,
* ``host-licenses/<name>-<ver>/<file>`` -- host license texts,
* ``sources/<name>-<ver>/...``        -- saved target sources (tarball(s),
  extra downloads, applied patches, ``series``) for redistributable packages,
* ``host-sources/<name>-<ver>/...``   -- same for host packages,
* ``buildroot.config``, ``README``, ``legal-info.sha256`` -- not used.

Manifest CSV shape (see ``Makefile:legal-info-prepare`` and
``package/pkg-utils.mk:legal-manifest`` in the Buildroot tree):

* one unquoted header line::

    PACKAGE,VERSION,LICENSE,LICENSE FILES,SOURCE ARCHIVE,SOURCE SITE,DEPENDENCIES WITH LICENSES

* one data line per package, every field double-quoted; the fields are
  ``RAWNAME`` (the ``host-`` prefix is stripped, so host and target manifests
  may list the same name), ``DL_VERSION``, the license with ``" ,"`` folded to
  ``","``, space-separated license file names, the original download file
  name (``not saved`` for local/override/non-redistributable packages), the
  source site URL (or ``not saved``), and the recursive dependencies as
  space-separated ``name [license]`` pairs (licenses inside the brackets keep
  their original commas/spaces).

* the special Buildroot row in ``host-manifest.csv`` (``legal-info-prepare``)
  has only **six** quoted fields -- no dependencies column::

    "buildroot","2026.02.3","GPL-2.0+","COPYING","not saved","not saved"
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .errors import LegalInfoError

MANIFEST_HEADER = (
    "PACKAGE",
    "VERSION",
    "LICENSE",
    "LICENSE FILES",
    "SOURCE ARCHIVE",
    "SOURCE SITE",
    "DEPENDENCIES WITH LICENSES",
)

#: The literal value Buildroot writes when it could not save the material.
NOT_SAVED = "not saved"

#: Subdirectories per scope: (licenses dir, sources dir).
_SCOPE_DIRS = {
    "target": ("licenses", "sources"),
    "host": ("host-licenses", "host-sources"),
    "build": ("host-licenses", "host-sources"),  # buildroot itself builds on the host
}


@dataclass(frozen=True)
class ManifestRow:
    """One parsed data row of a legal-info manifest CSV."""

    scope: str  # "target" | "host" | "build"
    name: str  # RAWNAME (no host- prefix)
    version: str
    license: str
    license_files: Tuple[str, ...]
    source_archive: str  # "" when not saved
    source_site: str  # "" when not saved
    dependencies: Tuple[Tuple[str, str], ...]  # (dep name, dep license)


def sanitize_version(version: str) -> str:
    """Sanitize a version the way Buildroot does for paths.

    Mirrors ``support/misc/utils.mk: sanitize`` -- strip, then replace
    spaces, colons and slashes with underscores. Used to build the
    ``<name>-<version>`` directory names under ``licenses/``/``sources/``.
    """
    out = version.strip()
    for ch in (" ", ":", "/"):
        out = out.replace(ch, "_")
    return out


def _parse_deps_field(field: str) -> Tuple[Tuple[str, str], ...]:
    """Parse the ``DEPENDENCIES WITH LICENSES`` column.

    The field is ``name [license] name [license] ...`` with space
    separation, but *licenses inside the brackets may contain spaces and
    commas* (they come from the raw package license variable, un-folded),
    so splitting on whitespace is not possible. A tiny state machine walks
    the string instead. A dependency without a bracket yields an empty
    license.
    """
    deps: List[Tuple[str, str]] = []
    i, n = 0, len(field)
    while i < n:
        while i < n and field[i] in " \t":
            i += 1
        if i >= n:
            break
        # read the dependency name (stops at whitespace or '[')
        j = i
        while j < n and field[j] not in " \t[":
            j += 1
        name = field[i:j]
        if not name:  # defensive: malformed field
            raise LegalInfoError(
                f"malformed DEPENDENCIES WITH LICENSES field: {field!r}"
            )
        # allow whitespace between the name and the optional '['
        k = j
        while k < n and field[k] in " \t":
            k += 1
        license = ""
        if k < n and field[k] == "[":
            close = field.find("]", k + 1)
            if close == -1:
                raise LegalInfoError(
                    f"unbalanced '[' in DEPENDENCIES WITH LICENSES field: {field!r}"
                )
            license = field[k + 1 : close].strip()
            i = close + 1
        else:
            i = k
        deps.append((name, license))
    return tuple(deps)


def _clean(value: str) -> str:
    """Empty out ``not saved`` / empty cells (Buildroot's 'absent' marker)."""
    value = value.strip()
    return "" if value in ("", NOT_SAVED) else value


def parse_manifest(
    path: Path, scope: str
) -> Tuple[List[ManifestRow], Optional[str]]:
    """Parse one manifest CSV file.

    Returns ``(rows, buildroot_version)`` where ``buildroot_version`` is the
    version from the special 6-column buildroot row (host scope only), else
    ``None``.
    """
    path = Path(path)
    if not path.is_file():
        raise LegalInfoError(f"manifest not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            raise LegalInfoError(f"{path}: empty manifest file")
        if tuple(h.strip() for h in header) != MANIFEST_HEADER:
            raise LegalInfoError(
                f"{path}: unexpected header {header!r}; expected {list(MANIFEST_HEADER)!r}"
            )

        rows: List[ManifestRow] = []
        buildroot_version: Optional[str] = None
        seen = set()
        for lineno, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue  # tolerate blank lines
            if len(row) == 6:
                # Only the special buildroot row has six columns.
                if scope != "host":
                    raise LegalInfoError(
                        f"{path}:{lineno}: six-column row in target manifest "
                        f"(only host-manifest.csv may carry the buildroot row): {row!r}"
                    )
                row = row + [""]
            if len(row) != 7:
                raise LegalInfoError(
                    f"{path}:{lineno}: expected 7 (or 6 for buildroot) quoted "
                    f"fields, got {len(row)}: {row!r}"
                )
            name, version, license, license_files, archive, site, deps_field = (
                cell.strip() for cell in row
            )
            if not name:
                raise LegalInfoError(f"{path}:{lineno}: empty PACKAGE field: {row!r}")
            if name == "buildroot" and scope in ("host", "build"):
                buildroot_version = version or None
                row_scope = "build"
            else:
                row_scope = scope
            if (row_scope, name) in seen:
                raise LegalInfoError(
                    f"{path}:{lineno}: duplicate package {name!r}"
                )
            seen.add((row_scope, name))
            rows.append(
                ManifestRow(
                    scope=row_scope,
                    name=name,
                    version=version,
                    license=license,
                    license_files=tuple(f for f in license_files.split() if f),
                    source_archive=_clean(archive),
                    source_site=_clean(site),
                    dependencies=_parse_deps_field(deps_field),
                )
            )
    return rows, buildroot_version


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_license_texts(
    legal_dir: Path, scope: str, name: str, version: str, license_files
) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Read the saved license texts for one package.

    Returns ``(texts, warnings)`` where ``texts`` is a list of
    ``(filename, text)`` for every requested license file that exists on
    disk (sorted by filename). Missing files yield a warning each.
    """
    if not license_files:
        return [], []
    licenses_dir = Path(legal_dir) / _SCOPE_DIRS[scope][0]
    sub = f"{name}-{sanitize_version(version)}" if version else name
    base = licenses_dir / sub
    texts: List[Tuple[str, str]] = []
    warnings: List[str] = []
    for fname in sorted(set(license_files)):
        fpath = base / fname
        try:
            texts.append((fname, fpath.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            warnings.append(f"{scope}/{name}: license file not found: {fpath}")
    return texts, warnings


def load_saved_source_hashes(
    legal_dir: Path, scope: str, name: str, version: str
) -> Tuple[Tuple[str, "Hash"], ...]:
    """Hash every saved source artifact for one package.

    Returns a tuple of ``(path_relative_to_legal_dir, Hash)`` sorted by
    path, covering the original download, extra downloads, the repackaged
    ``<name>-<version>.tar.gz`` of local/override packages, applied patches
    and the ``series`` file. An empty result means no sources were saved
    (local, overridden or non-redistributable package).
    """
    from .model import Hash  # local import: avoid a cycle at module load

    sources_dir = Path(legal_dir) / _SCOPE_DIRS[scope][1]
    sub = f"{name}-{sanitize_version(version)}" if version else name
    base = sources_dir / sub
    if not base.is_dir():
        return ()
    entries = []
    for fpath in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = fpath.relative_to(legal_dir).as_posix()
        entries.append((rel, Hash("sha256", _hash_file(fpath))))
    return tuple(entries)


def parse_legal_info(legal_dir) -> Tuple[List[ManifestRow], Optional[str], List[str]]:
    """Parse a whole legal-info directory.

    Returns ``(rows, buildroot_version, warnings)``. ``manifest.csv`` is
    mandatory; ``host-manifest.csv`` is optional (its absence is warned
    about, since the SBOM would then omit all build-time packages).
    """
    legal_dir = Path(legal_dir)
    if not legal_dir.is_dir():
        raise LegalInfoError(f"legal-info directory not found: {legal_dir}")
    warnings: List[str] = []
    rows: List[ManifestRow] = []
    rows, buildroot_version = parse_manifest(legal_dir / "manifest.csv", "target")
    host_path = legal_dir / "host-manifest.csv"
    if host_path.is_file():
        host_rows, host_br_version = parse_manifest(host_path, "host")
        rows.extend(host_rows)
        if host_br_version is not None:
            buildroot_version = host_br_version
    else:
        warnings.append(
            f"host-manifest.csv not found in {legal_dir}: host (build-time) "
            "packages will be missing from the SBOM"
        )
    return rows, buildroot_version, warnings
