"""Merge legal-info manifest rows and show-info entries into the model.

This is the single entry point the SPDX/CycloneDX emitters will call:

    from pengyos_sbom.ingest import ingest
    result = ingest(Path("legal-info"), Path("show-info.json"))
    for component in result.components: ...

Merge rules
-----------
* The **manifest rows are authoritative** for name, scope, version and the
  license string: they come from the legal manifest, which is the document
  the SBOM legally describes. Show-info only *enriches* a component with
  data the manifest lacks: download URIs (source origin), patch lists,
  ``provides``, CPE id, redistributability and the dependency list.
* A manifest row with **no matching show-info entry** still yields a
  component (manifest data only) and a warning.
* A show-info entry of type ``target``/``host`` with **no manifest row** is
  skipped with a warning (it is not part of the legal manifest, e.g. a
  package whose sources Buildroot could not collect); virtual aliases are
  tracked separately in ``IngestResult.skipped_virtual`` because they are
  not real components.
* Rootfs targets (``type == "rootfs"``) are not components; they become
  ``IngestResult.rootfs``.
* Hashes are computed over the saved source artifacts under
  ``sources/``/``host-sources/`` (see ``pengyos_sbom.legalinfo``); license
  texts are read from ``licenses/``/``host-licenses/``.
* Everything in the result is deterministically ordered: components by
  (scope, name) with build < host < target, downloads/uris in show-info
  order, hashes by path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from . import legalinfo, showinfo
from .model import (
    SCOPE_BUILD,
    SCOPE_HOST,
    SCOPE_TARGET,
    Component,
    IngestResult,
    scope_sort_key,
)
from .showinfo import ShowInfoEntry

#: show-info key expected for a manifest row, per scope.
_SHOWINFO_KEY = {
    SCOPE_TARGET: lambda name: name,
    SCOPE_HOST: lambda name: f"host-{name}",
    SCOPE_BUILD: lambda name: f"host-{name}",  # host-buildroot
}


def _merge_component(row, si: Optional[ShowInfoEntry], license_texts, hashes) -> Component:
    if si is not None:
        downloads = si.downloads
        origin = downloads[0].uris[0] if downloads and downloads[0].uris else ""
    else:
        downloads = ()
        origin = ""
    if not origin:
        origin = row.source_site

    return Component(
        name=row.name,
        scope=row.scope,
        version=row.version,
        licenses=row.license,
        license_files=tuple(row.license_files),
        license_texts=tuple(license_texts),
        source_site=row.source_site,
        archive=row.source_archive,
        origin=origin,
        downloads=downloads,
        hashes=hashes,
        patches=si.patches if si is not None else (),
        provides=si.provides if si is not None else (),
        cpe_id=(si.cpe_id or "") if si is not None else "",
        dependencies=si.dependencies if si is not None else tuple(
            dep for dep, _ in row.dependencies
        ),
        redistributable=si.redistributable if si is not None else None,
        virtual=si.virtual if si is not None else False,
        package_dir=(si.package_dir or "") if si is not None else "",
    )


def ingest(legal_info, show_info=None) -> IngestResult:
    """Build the internal component model from Buildroot output.

    :param legal_info: path to the ``legal-info`` directory.
    :param show_info: path to the show-info JSON file, or ``None`` to ingest
        the manifests alone (a warning is recorded).
    :raises LegalInfoError: the legal-info directory/manifests are bad.
    :raises ShowInfoError: the show-info file is bad.
    """
    legal_info = Path(legal_info)
    rows, buildroot_version, warnings = legalinfo.parse_legal_info(legal_info)

    si_entries: Dict[str, ShowInfoEntry] = {}
    if show_info is None:
        warnings.append("no show-info file given: components lack source URIs, patches and dependency data")
    else:
        si_entries = showinfo.parse_show_info(show_info)

    components: List[Component] = []
    matched_keys = set()
    for row in rows:
        key = _SHOWINFO_KEY[row.scope](row.name)
        si = si_entries.get(key)
        if si is not None:
            if si.kind not in (row.scope if row.scope != SCOPE_BUILD else "host",):
                warnings.append(
                    f"{row.scope}/{row.name}: show-info entry {key!r} has type "
                    f"{si.kind!r}, expected {row.scope!r}"
                )
            matched_keys.add(key)
        else:
            warnings.append(
                f"{row.scope}/{row.name}: no matching show-info entry ({key!r}); "
                "using manifest data only"
            )

        license_texts, license_warnings = legalinfo.load_license_texts(
            legal_info, row.scope, row.name, row.version, row.license_files
        )
        warnings.extend(license_warnings)
        hashes = legalinfo.load_saved_source_hashes(
            legal_info, row.scope, row.name, row.version
        )
        if not hashes and row.source_archive:
            warnings.append(
                f"{row.scope}/{row.name}: source archive {row.source_archive!r} is "
                "listed in the manifest but was not saved under sources/"
            )
        elif not hashes and not (row.source_archive or row.source_site):
            warnings.append(
                f"{row.scope}/{row.name}: no source material saved "
                "(local/override or non-redistributable package)"
            )
        components.append(_merge_component(row, si, license_texts, hashes))

    rootfs = showinfo.rootfs_entries(si_entries)
    skipped_virtual = []
    for key, si in si_entries.items():
        if key in matched_keys:
            continue
        if si.kind == "rootfs":
            continue
        if si.virtual:
            skipped_virtual.append(key)
            continue
        warnings.append(
            f"show-info entry {key!r} (type {si.kind!r}) has no legal-manifest "
            "row; skipping it (not part of the legal manifest)"
        )

    components.sort(key=scope_sort_key)
    return IngestResult(
        components=tuple(components),
        rootfs=rootfs,
        warnings=tuple(warnings),
        buildroot_version=buildroot_version or "",
        skipped_virtual=tuple(sorted(skipped_virtual)),
    )
