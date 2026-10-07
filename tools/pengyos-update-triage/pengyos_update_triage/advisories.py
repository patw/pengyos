"""Advisory (OSV JSON) input schema and loader.

Advisory fixtures are OSV-format JSON documents. A single fixture file may hold
one advisory, a JSON array of advisories, or an OSV *vulnerabilities list*
(``{"results": [...]}``); the loader normalises all three into a list of
:class:`Advisory`.

Recognised OSV fields
---------------------
``id`` (required), ``summary``, ``details``, ``aliases`` (e.g. CVE ids),
``published``/``modified``, ``severity`` (array of ``{type, score}``),
``affected`` (array of ``{package{name, ecosystem}, ranges, versions,
database_specific}``), ``references`` (array of ``{url}`` or strings), and
``database_specific``. Unknown fields are ignored so newer OSV documents still
load. Each loaded :class:`Advisory` records ``source_file`` for provenance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from .errors import AdvisoryError, AdvisoryFormatError, AdvisorySchemaError
from .models import Advisory, Affected, Range, RangeEvent, Severity

__all__ = ["load_advisories", "parse_advisory", "parse_advisories"]

_ADVISORY_SUFFIXES = {".json"}


def _iter_advisory_docs(node: object) -> List[object]:
    """Normalise a decoded JSON document into a list of advisory objects.

    Accepts a single object, a top-level array of objects, or an OSV
    vulnerabilities-list ``{"results": [...]}``.
    """
    if isinstance(node, dict):
        if isinstance(node.get("results"), list):
            return list(node["results"])
        return [node]
    if isinstance(node, list):
        return list(node)
    raise AdvisorySchemaError(
        "advisory document must be a JSON object, an array of objects, or "
        f"{{'results': [...]}}; got {type(node).__name__}"
    )


def parse_advisory(obj: object, source_file: Union[str, Path, None] = None) -> Advisory:
    """Parse a single decoded OSV advisory object into an :class:`Advisory`.

    Raises :class:`AdvisorySchemaError` if required fields are missing or
    malformed.
    """
    if not isinstance(obj, dict):
        raise AdvisorySchemaError(
            f"each advisory must be a JSON object, got {type(obj).__name__}"
        )

    adv_id = obj.get("id")
    if not isinstance(adv_id, str) or not adv_id.strip():
        raise AdvisorySchemaError("advisory is missing a non-empty string 'id'")

    return Advisory(
        id=adv_id,
        summary=_opt_str(obj, "summary"),
        details=_opt_str(obj, "details"),
        aliases=_str_list(obj.get("aliases")),
        published=_opt_str(obj, "published"),
        modified=_opt_str(obj, "modified"),
        severities=_parse_severities(obj.get("severity")),
        affected=_parse_affected(obj.get("affected")),
        references=_parse_references(obj.get("references")),
        database_specific=_dict(obj.get("database_specific")),
        source_file=str(source_file) if source_file is not None else None,
    )


def parse_advisories(
    doc: object, source_file: Union[str, Path, None] = None
) -> List[Advisory]:
    """Parse a decoded OSV JSON document (any supported shape) into Advisories."""
    out: List[Advisory] = []
    for i, obj in enumerate(_iter_advisory_docs(doc)):
        out.append(parse_advisory(obj, source_file=source_file))
    return out


# --- per-field parsers ------------------------------------------------------


def _opt_str(obj: dict, key: str) -> str:
    value = obj.get(key)
    return value if isinstance(value, str) else ""


def _str_list(value: object) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise AdvisorySchemaError(
            f"expected an array of strings, got {type(value).__name__}"
        )
    return list(value)


def _dict(value: object) -> "dict":
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AdvisorySchemaError(
            f"expected an object, got {type(value).__name__}"
        )
    return value


def _parse_severities(value: object) -> List[Severity]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AdvisorySchemaError(
            f"'severity' must be an array, got {type(value).__name__}"
        )
    out: List[Severity] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise AdvisorySchemaError(
                f"each 'severity' entry must be an object, got {type(entry).__name__}"
            )
        stype = entry.get("type")
        score = entry.get("score")
        if not isinstance(stype, str) or not stype:
            raise AdvisorySchemaError("each 'severity' entry needs a string 'type'")
        if not isinstance(score, str) or not score:
            raise AdvisorySchemaError("each 'severity' entry needs a string 'score'")
        out.append(Severity(type=stype, score=score))
    return out


def _parse_affected(value: object) -> List[Affected]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AdvisorySchemaError(f"'affected' must be an array, got {type(value).__name__}")
    out: List[Affected] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise AdvisorySchemaError(
                f"each 'affected' entry must be an object, got {type(entry).__name__}"
            )
        pkg = entry.get("package")
        if not isinstance(pkg, dict):
            raise AdvisorySchemaError(
                "each 'affected' entry needs a 'package' object"
            )
        name = pkg.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AdvisorySchemaError("each 'affected' package needs a string 'name'")
        ecosystem = pkg.get("ecosystem")
        ecosystem = ecosystem if isinstance(ecosystem, str) else ""
        out.append(
            Affected(
                package_name=name,
                ecosystem=ecosystem,
                ranges=_parse_ranges(entry.get("ranges")),
                versions=_str_list(entry.get("versions")),
                database_specific=_dict(entry.get("database_specific")),
            )
        )
    return out


def _parse_ranges(value: object) -> List[Range]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AdvisorySchemaError(f"'ranges' must be an array, got {type(value).__name__}")
    out: List[Range] = []
    for r in value:
        if not isinstance(r, dict):
            raise AdvisorySchemaError(f"each 'range' must be an object, got {type(r).__name__}")
        rtype = r.get("type")
        if not isinstance(rtype, str) or not rtype:
            raise AdvisorySchemaError("each 'range' needs a string 'type'")
        out.append(Range(type=rtype, events=_parse_events(r.get("events"))))
    return out


def _parse_events(value: object) -> List[RangeEvent]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AdvisorySchemaError(f"'events' must be an array, got {type(value).__name__}")
    out: List[RangeEvent] = []
    for ev in value:
        if not isinstance(ev, dict):
            raise AdvisorySchemaError(
                f"each 'event' must be an object, got {type(ev).__name__}"
            )
        introduced = ev.get("introduced")
        fixed = ev.get("fixed")
        last_affected = ev.get("last_affected")
        vulnerable = ev.get("vulnerable")
        if all(v is None for v in (introduced, fixed, last_affected, vulnerable)):
            raise AdvisorySchemaError(
                "a 'range' event must set one of introduced/fixed/last_affected/vulnerable"
            )
        out.append(
            RangeEvent(
                introduced=_vstr(introduced),
                fixed=_vstr(fixed),
                last_affected=_vstr(last_affected),
                vulnerable=_vstr(vulnerable),
            )
        )
    return out


def _vstr(value: object) -> "str | None":
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise AdvisorySchemaError(
        f"version event value must be a string, got {type(value).__name__}"
    )


def _parse_references(value: object) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AdvisorySchemaError(f"'references' must be an array, got {type(value).__name__}")
    out: List[str] = []
    for ref in value:
        if isinstance(ref, str):
            out.append(ref)
        elif isinstance(ref, dict) and isinstance(ref.get("url"), str):
            out.append(ref["url"])
        # Other reference shapes are ignored (forward-compatible).
    return out


# --- directory loading ------------------------------------------------------


def load_advisories(directory: Union[str, Path]) -> List[Advisory]:
    """Load every OSV advisory fixture under ``directory``.

    Scans for ``*.json`` files (recursively), loading each in a stable, sorted
    order so results are reproducible. A missing directory is a hard error
    (the caller pointed at the wrong place); an empty directory simply yields
    no advisories.

    Raises
    ------
    AdvisoryError
        if the directory is missing or a file is unreadable.
    AdvisoryFormatError
        if a file is not valid JSON.
    AdvisorySchemaError
        if a file's JSON does not match the OSV schema.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        raise AdvisoryError(
            f"advisories directory not found: {dir_path}", path=str(dir_path)
        )
    if not dir_path.is_dir():
        raise AdvisoryError(
            f"advisories path is not a directory: {dir_path}", path=str(dir_path)
        )

    files = sorted(
        p for p in dir_path.rglob("*")
        if p.is_file() and p.suffix.lower() in _ADVISORY_SUFFIXES
    )

    advisories: List[Advisory] = []
    for path in files:
        advisories.extend(_load_one_advisory_file(path))
    return advisories


def _load_one_advisory_file(path: Path) -> List[Advisory]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AdvisoryError(f"could not read advisory file {path}: {exc}", path=str(path)) from exc
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AdvisoryFormatError(
            f"invalid JSON in advisory fixture: {exc}", path=str(path)
        ) from exc
    try:
        return parse_advisories(doc, source_file=str(path))
    except AdvisorySchemaError as exc:
        # The pure parser doesn't know the file; attach provenance so the
        # caller can report exactly which fixture failed the schema.
        exc.path = str(path)
        raise
