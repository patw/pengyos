"""Parse a Buildroot ``show-info`` JSON document.

Ground truth: written against the ``make show-info`` / ``make show-info-all``
output of Buildroot 2026.02.3; the ``json-info`` generation macro is
verbatim-identical in Buildroot 2026.08 (the current-line engine), so the
same schema applies there. The document is a single JSON object
whose keys are package *names in lower case* -- exactly ``<pkg>`` for target
packages, ``host-<pkg>`` for host packages, and the rootfs target names
(e.g. ``rootfs-ext4``). Example::

    {
      "busybox": {
        "type": "target",
        "name": "busybox",
        "virtual": false,
        "version": "1.37.0",
        "licenses": "GPL-2.0+",
        "license_files": ["COPYING"],
        "redistributable": true,
        "dl_dir": "",
        "downloads": [
          {"source": "busybox-1.37.0.tar.bz2",
           "uris": ["https://www.busybox.net/downloads/busybox-1.37.0.tar.bz2"]}
        ],
        "hashes": ["package/busybox/busybox.hash"],
        "patches": ["package/busybox/0001-....patch"],
        "package_dir": "package/busybox",
        "stamp_dir": "build/busybox",
        "source_dir": "build/busybox",
        "build_dir": "build/busybox",
        "install_target": true,
        "install_staging": false,
        "install_images": false,
        "dependencies": ["host-pkgconf"],
        "reverse_dependencies": [],
        "cpe-id": "cpe:2.3:a:busybox:busybox:1.37.0:*:*:*:*:*:*:*"
      },
      "rootfs-ext4": {"type": "rootfs", "image_name": "rootfs.ext4",
                      "dependencies": ["busybox", "linux"]}
    }

Field notes (all verified against the 2026.02.3 tree):

* ``name`` is the RAWNAME: the ``host-`` prefix is *stripped*, so the host
  version of a package reports the same ``name`` as its target version.
* virtual packages (Kconfig aliases) carry only ``type``, ``name``,
  ``virtual: true`` and directory/dependency fields -- no version/licenses.
* ``hashes`` are paths to Buildroot ``.hash`` *files* (e.g.
  ``package/busybox/busybox.hash``), **not** digests; the digests themselves
  only exist inside the Buildroot source tree. The real content digests for
  the SBOM therefore come from hashing the saved ``legal-info`` source
  artifacts (see ``pengyos_sbom.legalinfo``).
* ``rootfs`` entries carry only ``type``, ``image_name`` (nullable) and
  ``dependencies``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

from .errors import ShowInfoError
from .model import Download, Rootfs, ShowInfoEntry


def _str_list(value, field: str, key: str) -> Tuple[str, ...]:
    """Extract a list-of-strings field, tolerating a missing (null) value."""
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ShowInfoError(
            f"show-info {key!r}: field {field!r} must be a list of strings, "
            f"got {value!r}"
        )
    return tuple(value)


def _opt_str(value, field: str, key: str):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ShowInfoError(
            f"show-info {key!r}: field {field!r} must be a string or null, "
            f"got {value!r}"
        )
    return value


def _opt_bool(value, field: str, key: str):
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ShowInfoError(
            f"show-info {key!r}: field {field!r} must be a boolean or null, "
            f"got {value!r}"
        )
    return value


def _parse_entry(key: str, raw) -> ShowInfoEntry:
    if not isinstance(raw, dict):
        raise ShowInfoError(f"show-info entry {key!r} must be an object, got {raw!r}")
    kind = raw.get("type")
    if not isinstance(kind, str) or not kind:
        raise ShowInfoError(
            f"show-info entry {key!r}: missing or invalid 'type' field: {kind!r}"
        )

    downloads = []
    raw_downloads = raw.get("downloads")
    if raw_downloads is not None:
        if not isinstance(raw_downloads, list):
            raise ShowInfoError(
                f"show-info {key!r}: field 'downloads' must be a list or null, "
                f"got {raw_downloads!r}"
            )
        for i, dl in enumerate(raw_downloads):
            if not isinstance(dl, dict):
                raise ShowInfoError(
                    f"show-info {key!r}: downloads[{i}] must be an object, got {dl!r}"
                )
            source = dl.get("source")
            if not isinstance(source, str):
                raise ShowInfoError(
                    f"show-info {key!r}: downloads[{i}].source must be a string"
                )
            uris = dl.get("uris")
            if uris is None:
                uris = []
            if not isinstance(uris, list) or any(not isinstance(u, str) for u in uris):
                raise ShowInfoError(
                    f"show-info {key!r}: downloads[{i}].uris must be a list of strings"
                )
            downloads.append(Download(source=source, uris=tuple(uris)))

    virtual = raw.get("virtual", False)
    if not isinstance(virtual, bool):
        raise ShowInfoError(
            f"show-info {key!r}: field 'virtual' must be a boolean, got {virtual!r}"
        )

    return ShowInfoEntry(
        key=key,
        kind=kind,
        virtual=virtual,
        version=_opt_str(raw.get("version"), "version", key),
        licenses=_opt_str(raw.get("licenses"), "licenses", key),
        license_files=_str_list(raw.get("license_files"), "license_files", key),
        redistributable=_opt_bool(raw.get("redistributable"), "redistributable", key),
        downloads=tuple(downloads),
        hash_files=_str_list(raw.get("hashes"), "hashes", key),
        patches=_str_list(raw.get("patches"), "patches", key),
        provides=_str_list(raw.get("provides"), "provides", key),
        cpe_id=_opt_str(raw.get("cpe-id"), "cpe-id", key),
        dependencies=_str_list(raw.get("dependencies"), "dependencies", key),
        reverse_dependencies=_str_list(
            raw.get("reverse_dependencies"), "reverse_dependencies", key
        ),
        image_name=_opt_str(raw.get("image_name"), "image_name", key),
        package_dir=_opt_str(raw.get("package_dir"), "package_dir", key),
    )


def parse_show_info(path) -> Dict[str, ShowInfoEntry]:
    """Parse a show-info JSON file into ``{key: ShowInfoEntry}``."""
    path = Path(path)
    if not path.is_file():
        raise ShowInfoError(f"show-info file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShowInfoError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ShowInfoError(
            f"{path}: top-level show-info document must be a JSON object, "
            f"got {type(raw).__name__}"
        )
    return {key: _parse_entry(key, entry) for key, entry in raw.items()}


def rootfs_entries(entries: Dict[str, ShowInfoEntry]) -> Tuple[Rootfs, ...]:
    """Extract the rootfs targets (in key order) as :class:`Rootfs` objects."""
    out = []
    for key, e in entries.items():
        if e.kind == "rootfs":
            out.append(Rootfs(name=key, image_name=e.image_name, dependencies=e.dependencies))
    return tuple(out)
