"""Core rebuild-and-compare logic for PengyOS repro-report.

This module compares two pengyos-build outputs artifact-by-artifact using
SHA-256 content hashes and produces a machine-readable report the external
witness can consume.

Each input is resolved to a ``{artifact_name: sha256_hex}`` mapping via
:func:`resolve_source`: the primary source is pengyos-build's
``build-manifest.json`` (artifact name -> sha256), and the fallback is a
content-hash walk of the build's ``images/`` directory. The explicit,
data-driven classification table (identical / normalized / real) is layered on
in a later subtask; this module exposes a stable seam for it.

Standard library only.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .classify import classify_comparison, _never_loads

# Canonical report schema version. Bump only on an incompatible change.
SCHEMA_VERSION = "pengyos-repro-report/1"

# Well-known artifact status values.
STATUS_IDENTICAL = "identical"
STATUS_DIFFERS = "differs"
STATUS_MISSING_A = "missing_in_a"
STATUS_MISSING_B = "missing_in_b"
# A difference explained by a known non-deterministic field (mtime, recorded
# build timestamp, ...). Counts as matching for the reproducibility score.
STATUS_NORMALIZED = "normalized"
# A difference no rule explained: a genuine reproducibility failure.
STATUS_REAL = "real"

# The witness-conventional report filename inside a build's images/ dir.
REPORT_FILENAME = "repro-report.json"

# File size past which we hash in chunks rather than reading whole-file.
_CHUNK = 1024 * 1024

# build-manifest.json lives at the root of a pengyos-build output dir; the
# content-hash walk fallback targets the images/ subdir.
MANIFEST_FILENAME = "build-manifest.json"
IMAGES_DIRNAME = "images"


class ReproReportError(RuntimeError):
    """Raised for expected, user-facing failures (mapped to exit 1, no traceback).

    Lives in ``core`` so both the core layer and the CLI can raise it; the CLI
    maps it to a clean non-zero exit.
    """


def sha256_file(path: str) -> str:
    """Return the hex SHA-256 of a file's contents."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Artifact:
    """A single compared artifact.

    ``status`` is one of the ``STATUS_*`` constants. ``hash_a`` / ``hash_b`` are
    the SHA-256 of each side (``None`` when the artifact is absent there).
    ``normalized`` records whether a difference is only in a known
    non-deterministic field (set by the classification table in
    :mod:`pengyos_repro_report.classify`); it is ``None`` when the artifact is
    byte-identical (or absent on one side) and ``False`` for a real difference.
    """

    name: str
    status: str
    hash_a: str | None = None
    hash_b: str | None = None
    normalized: bool | None = None

    @property
    def identical(self) -> bool:
        """True when this artifact is byte-identical across both sides."""
        return self.status == STATUS_IDENTICAL

    @property
    def matching(self) -> bool:
        """True when this artifact counts toward the reproducibility score.

        Identical artifacts always match. Artifacts that differ only in a
        normalized field also match (they are reproducible modulo a known
        non-determinism) once the classification table is in place.
        """
        if self.status == STATUS_IDENTICAL:
            return True
        if self.normalized is True:
            return True
        return False


@dataclass
class Comparison:
    """The full set of artifacts compared between two build dirs."""

    artifacts: list[Artifact] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        """Sorted artifact names (deterministic ordering)."""
        return sorted(a.name for a in self.artifacts)

    def score(self) -> float:
        """Reproducibility score in ``[0, 1]``: matching artifacts / total."""
        total = len(self.artifacts)
        if total == 0:
            return 1.0
        return sum(1 for a in self.artifacts if a.matching) / total


def _walk(root: str) -> set[str]:
    """Return the set of relative file paths under ``root`` (sorted-safe)."""
    found: set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root)
            found.add(rel.replace(os.sep, "/"))
    return found


def compare_dirs(dir_a: str, dir_b: str) -> Comparison:
    """Compare every file under ``dir_a`` and ``dir_b`` by SHA-256.

    Returns a :class:`Comparison`. Artifacts present on only one side are
    reported with the corresponding ``STATUS_MISSING_*`` status.
    """
    names_a = _walk(dir_a)
    names_b = _walk(dir_b)
    all_names = names_a | names_b

    artifacts: list[Artifact] = []
    for name in all_names:
        in_a = name in names_a
        in_b = name in names_b
        if in_a and in_b:
            hash_a = sha256_file(os.path.join(dir_a, name))
            hash_b = sha256_file(os.path.join(dir_b, name))
            status = STATUS_IDENTICAL if hash_a == hash_b else STATUS_DIFFERS
            artifacts.append(Artifact(name, status, hash_a, hash_b))
        elif in_a:
            artifacts.append(Artifact(name, STATUS_MISSING_A, sha256_file(os.path.join(dir_a, name)), None))
        else:
            artifacts.append(Artifact(name, STATUS_MISSING_B, None, sha256_file(os.path.join(dir_b, name))))

    comparison = Comparison(artifacts=artifacts)
    _classify(comparison, dir_a, dir_b)
    return comparison


def load_manifest(path: str) -> dict[str, str]:
    """Load a ``build-manifest.json`` into ``{artifact_name: sha256_hex}``.

    Supports the canonical ``pengyos-build`` shape (a JSON object mapping an
    artifact name to its SHA-256) as well as a list of ``{"name", "sha256"}``
    records. Raises :class:`ReproReportError` on malformed input.
    """
    import json

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ReproReportError(f"invalid JSON in manifest {path}: {exc}") from exc
    except OSError as exc:
        # The manifest exists but cannot be read (permissions, it is a
        # directory, ...). Surface it as a clean, user-facing failure instead of
        # letting the raw OSError escape as a traceback.
        raise ReproReportError(f"cannot read manifest {path}: {exc}") from exc

    return _normalize_manifest(data, path)


def _normalize_manifest(data, source: str) -> dict[str, str]:
    """Coerce a parsed manifest into ``{name: sha256}``, validating its shape."""
    if isinstance(data, dict):
        pairs = data.items()
    elif isinstance(data, list):
        pairs = []
        for entry in data:
            if not isinstance(entry, dict):
                raise ReproReportError(f"manifest entry is not an object: {source}")
            if "name" not in entry or "sha256" not in entry:
                raise ReproReportError(f"manifest entry missing 'name'/'sha256': {source}")
            pairs.append((entry["name"], entry["sha256"]))
    else:
        raise ReproReportError(f"unsupported manifest shape in {source}")

    artifacts: dict[str, str] = {}
    for name, digest in pairs:
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ReproReportError(f"manifest keys and values must be strings: {source}")
        artifacts[name] = digest
    return artifacts


def manifest_from_build_dir(build_dir: str) -> dict[str, str] | None:
    """Return ``{name: sha256}`` from ``build_dir/build-manifest.json`` if present."""
    manifest_path = os.path.join(build_dir, MANIFEST_FILENAME)
    if os.path.isfile(manifest_path):
        return load_manifest(manifest_path)
    return None


def walk_images(build_dir: str) -> dict[str, str] | None:
    """Content-hash walk of ``build_dir/images/`` -> ``{name: sha256}``.

    Returns ``None`` when there is no ``images/`` directory to walk.

    Raises :class:`ReproReportError` when the ``images/`` directory exists but
    cannot be read (e.g. permissions). Without this, an unreadable directory
    would silently yield an empty mapping and an all-``identical`` score — a
    wrong answer. We prefer a clean, honest error instead.
    """
    images_dir = os.path.join(build_dir, IMAGES_DIRNAME)
    if not os.path.isdir(images_dir):
        return None
    result: dict[str, str] = {}

    def _record_walk_error(exc: OSError) -> None:
        raise ReproReportError(f"cannot read {images_dir}: {exc}")

    for dirpath, _dirnames, filenames in os.walk(images_dir, onerror=_record_walk_error):
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            # Name artifacts relative to the build-dir root so walk results use
            # the same keys as a build-manifest.json (e.g. "images/f.bin").
            rel = os.path.relpath(full, build_dir).replace(os.sep, "/")
            result[rel] = sha256_file(full)
    return result


def resolve_source(source: str) -> dict[str, str]:
    """Resolve a build input to ``{artifact_name: sha256_hex}``.

    A ``.json`` file is treated as a manifest directly. A directory is resolved
    by its ``build-manifest.json`` first (the primary source) and then by a
    content-hash walk of its ``images/`` directory (the fallback). Raises
    :class:`ReproReportError` when neither source is available, or when the
    ``images/`` directory exists but cannot be read.
    """
    if os.path.isfile(source):
        return load_manifest(source)
    if os.path.isdir(source):
        manifest = manifest_from_build_dir(source)
        if manifest is not None:
            return manifest
        walked = walk_images(source)
        if walked is None:
            raise ReproReportError(f"no {MANIFEST_FILENAME} and no images/ dir in: {source}")
        return walked
    raise ReproReportError(f"build input not found: {source}")


def compare_sources(source_a: str, source_b: str) -> Comparison:
    """Compare two build inputs (dirs or manifest files) by SHA-256.

    Each input is resolved via :func:`resolve_source` to a
    ``{artifact_name: sha256}`` mapping; the intersection/union of artifact
    names is then compared. Artifacts present on only one side are reported
    with the corresponding ``STATUS_MISSING_*`` status.
    """
    mapping_a = resolve_source(source_a)
    mapping_b = resolve_source(source_b)
    all_names = set(mapping_a) | set(mapping_b)

    artifacts: list[Artifact] = []
    for name in all_names:
        in_a = name in mapping_a
        in_b = name in mapping_b
        if in_a and in_b:
            hash_a, hash_b = mapping_a[name], mapping_b[name]
            status = STATUS_IDENTICAL if hash_a == hash_b else STATUS_DIFFERS
            artifacts.append(Artifact(name, status, hash_a, hash_b))
        elif in_a:
            artifacts.append(Artifact(name, STATUS_MISSING_A, mapping_a[name], None))
        else:
            artifacts.append(Artifact(name, STATUS_MISSING_B, None, mapping_b[name]))

    comparison = Comparison(artifacts=artifacts)
    # Classify byte-level differences when the raw payloads are reachable. When
    # an input is a manifest file (name -> sha256) we have no bytes to inspect,
    # so a differing hash is reported as a real difference (normalized cannot be
    # inferred from a digest alone).
    _classify(comparison, source_a, source_b)
    return comparison


def _read_payload(directory: str | None, name: str) -> bytes | None:
    """Return the raw bytes of ``name`` under ``directory`` (or ``None``).

    ``name`` is relative to the build-dir root, matching manifest/walk keys
    (e.g. ``"images/f.bin"``), so this reads ``<directory>/images/f.bin``.
    """
    if directory is None:
        return None
    path = os.path.join(directory, name)
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            return fh.read()
    return None


def _classify(comparison: Comparison, source_a: str, source_b: str) -> None:
    """Run the data-driven classification table over ``comparison`` in place.

    When both sources are directories the raw payloads are read and each
    differing artifact is classified as ``normalized`` (a known
    non-determinism) or ``real``. When a source is not a directory (e.g. a
    manifest file) no bytes are available, so differing artifacts are left as
    a real difference.
    """
    dir_a = source_a if os.path.isdir(source_a) else None
    dir_b = source_b if os.path.isdir(source_b) else None
    if dir_a is None and dir_b is None:
        # Manifest-only inputs: no bytes to inspect, so a differing digest is a
        # real difference (normalized cannot be inferred from a digest alone).
        classify_comparison(comparison, _never_loads)
        return

    def load_pair(name: str) -> tuple[bytes | None, bytes | None]:
        return _read_payload(dir_a, name), _read_payload(dir_b, name)

    classify_comparison(comparison, load_pair)


def build_report(comparison: Comparison) -> dict:
    """Turn a :class:`Comparison` into the canonical ``repro-report.json`` dict.

    The shape is pinned by a golden test (``tests/test_report.py``) and read by
    the external witness:

    * ``schema``  — the pinned :data:`SCHEMA_VERSION` string;
    * ``score``   — :meth:`Comparison.score` (matching artifacts / total);
    * ``artifacts`` — one entry per artifact, deterministically sorted by name,
      each with ``name``, ``status``, per-side ``hash_a``/``hash_b`` (``None``
      when absent), and ``normalized`` (``None`` for a byte-identical artifact,
      a bool only for a differing one);
    * ``summary`` — ``total``, ``matching``, and a ``counts`` map bucketing the
      *final* statuses (``differs`` is an intermediate state the classification
      table resolves onto ``normalized``/``real`` before this runs, so it never
      appears here).
    """
    artifacts = sorted(comparison.artifacts, key=lambda a: a.name)
    total = len(artifacts)
    # The canonical counts bucket the *final* statuses. ``differs`` is an
    # intermediate state the classification table resolves onto ``normalized``
    # or ``real`` before the report is built, so it never appears here; the
    # buckets are fixed (not a dynamic ``dict.get``) so the schema is stable.
    counts = {
        STATUS_IDENTICAL: 0,
        STATUS_NORMALIZED: 0,
        STATUS_REAL: 0,
        STATUS_MISSING_A: 0,
        STATUS_MISSING_B: 0,
    }
    for artifact in artifacts:
        counts[artifact.status] += 1

    return {
        "schema": SCHEMA_VERSION,
        "score": comparison.score(),
        "artifacts": [
            {
                "name": a.name,
                "status": a.status,
                "hash_a": a.hash_a,
                "hash_b": a.hash_b,
                "normalized": a.normalized,
            }
            for a in artifacts
        ],
        "summary": {
            "total": total,
            "matching": sum(1 for a in artifacts if a.matching),
            "counts": counts,
        },
    }


def write_report(report: dict, path: str) -> str:
    """Write ``report`` as pretty JSON to ``path``, creating parent dirs."""
    import json

    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return path


def witness_path(build_dir: str) -> str:
    """The conventional witness path for ``build_dir``'s repro report.

    The external witness looks for ``repro-report.json`` under a build's
    ``images/`` directory, so this is ``<build_dir>/images/repro-report.json``.
    It is the path a ``diff`` with no explicit ``--out`` writes to, so the
    report lands exactly where the witness reads it.
    """
    return os.path.join(build_dir, IMAGES_DIRNAME, REPORT_FILENAME)


def load_report(path: str) -> dict:
    """Load a report dict written by :func:`build_report`.

    Raises :class:`ReproReportError` when the file is missing/unreadable or is
    not valid JSON, so the CLI can map it to a clean non-zero exit (no
    traceback) instead of letting the raw exception escape.
    """
    import json

    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ReproReportError(f"invalid JSON in report {path}: {exc}") from exc
    except OSError as exc:
        raise ReproReportError(f"cannot read report {path}: {exc}") from exc


def compute_score(report: dict) -> float:
    """Extract the reproducibility score from a loaded report dict."""
    return float(report["score"])


def summarize(comparison: Comparison) -> str:
    """Human-readable one-line summary of a comparison."""
    counts = {
        STATUS_IDENTICAL: 0,
        STATUS_NORMALIZED: 0,
        STATUS_REAL: 0,
        STATUS_MISSING_A: 0,
        STATUS_MISSING_B: 0,
    }
    for artifact in comparison.artifacts:
        counts[artifact.status] += 1
    return (
        f"score={comparison.score():.4f} "
        f"total={len(comparison.artifacts)} "
        f"identical={counts[STATUS_IDENTICAL]} "
        f"normalized={counts[STATUS_NORMALIZED]} "
        f"real={counts[STATUS_REAL]} "
        f"missing_a={counts[STATUS_MISSING_A]} "
        f"missing_b={counts[STATUS_MISSING_B]}"
    )


def valid_artifacts(artifacts: Iterable[dict]) -> bool:
    """Sanity-check that a loaded report's artifact list is well-formed."""
    for entry in artifacts:
        if not isinstance(entry, dict):
            return False
        if "name" not in entry or "status" not in entry:
            return False
    return True
