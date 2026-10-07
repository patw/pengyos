"""The wiring data model for pengyos-cosmic-build.

Two things the other lanes need, modelled as data here:

* :class:`VendorRecord` — the per-component vendored-source record: the source
  tarball name, its sha256, and the real SITE the emitted recipe will pin.
  :func:`vendor-all` produces one per component; :func:`emit` reads it back.

* :func:`symbol_for` — the ``BR2_PACKAGE_*`` symbol the desktop defconfig uses
  for a package name. This is the single transform every emitted recipe keys
  off, so the recipe symbol and the defconfig symbol can never disagree about
  which package a symbol names.

The data model is pure (no disk I/O beyond the record loader/writer) and
standard-library-only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

#: A hex sha256 digest, as written into a ``.hash`` file.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: The ``BR2_PACKAGE_`` prefix every Buildroot package symbol carries.
_BR2_PACKAGE_PREFIX = "BR2_PACKAGE_"


class VendorRecordError(ValueError):
    """A vendor record is malformed (bad sha256, missing field, …).

    Raised by :func:`validate` / :func:`load_record` / :func:`load_records` so
    a bad record is a *reported* condition (a clean CLI error), not a crash.
    """


@dataclass(frozen=True)
class VendorRecord:
    """The vendored-source record for one component.

    These five fields are exactly what :func:`emit` needs to pin a recipe:

    * ``name`` — the component's short name; also the ``package/<name>/`` dir
      and the base of the ``BR2_PACKAGE_*`` symbol (see :func:`symbol_for`).
    * ``version`` — the pinned upstream release (``<pkg>_VERSION``).
    * ``tarball`` — the vendored source tarball's filename (``<pkg>_SOURCE``).
    * ``sha256`` — the hex sha256 of that tarball (the ``.hash`` body).
    * ``site`` — the real ``<pkg>_SITE`` Buildroot resolves (never the
      placeholder).
    """

    name: str
    version: str
    tarball: str
    sha256: str
    site: str

    def validate(self) -> None:
        """Raise :class:`VendorRecordError` if this record is malformed.

        ``name`` is a Buildroot package name (``[a-z][a-z0-9_-]*``); ``sha256``
        is 64 hex digits; ``version`` / ``tarball`` / ``site`` are non-empty and
        never the documented placeholder. A placeholder SITE or hash is a
        *reported* error here, so :func:`lint` can never see one.
        """
        if not re.match(r"^[a-z][a-z0-9_-]*$", self.name):
            raise VendorRecordError(
                f"record {self.name!r}: name must match [a-z][a-z0-9_-]*"
            )
        if not _SHA256_RE.match(self.sha256):
            raise VendorRecordError(
                f"record {self.name!r}: sha256 must be 64 hex digits"
            )
        for field_name in ("version", "tarball", "site"):
            value = getattr(self, field_name)
            if not value:
                raise VendorRecordError(
                    f"record {self.name!r}: {field_name} must be non-empty"
                )
        if self.site == VENDORED_SITE_PLACEHOLDER:
            raise VendorRecordError(
                f"record {self.name!r}: site must be a real SITE, not the "
                f"placeholder {VENDORED_SITE_PLACEHOLDER!r}"
            )

    def as_dict(self) -> dict[str, str]:
        """A deterministic, JSON-serialisable view (keys sorted)."""
        return {
            "name": self.name,
            "version": self.version,
            "tarball": self.tarball,
            "sha256": self.sha256,
            "site": self.site,
        }


#: The documented placeholder SITE the recipes lane falls back to when no real
#: digest is available. :func:`emit` must never pin it; a recipe that still
#: carries it is a :func:`lint` violation.
VENDORED_SITE_PLACEHOLDER = "file://<VENDORED_SITE>"


def symbol_for(pkg: str) -> str:
    """The ``BR2_PACKAGE_*`` symbol the defconfig uses for *pkg*.

    Buildroot's Kconfig symbol transform: ``<pkg>`` -> ``BR2_PACKAGE_`` +
    uppercased, dashes -> underscores. ``cosmic-portal`` ->
    ``BR2_PACKAGE_COSMIC_PORTAL``. This is the single transform the emitted
    recipe and the defconfig must agree on, so a component's recipe symbol and
    the defconfig's reference can never silently diverge.
    """
    return f"{_BR2_PACKAGE_PREFIX}{pkg.upper().replace('-', '_')}"


def read_sha256(text: str) -> str:
    """Return the sha256 from a Buildroot-format ``.hash`` body, validated.

    The body is a single ``sha256  <hash>  <filename>`` line (Buildroot's
    ``check-hash`` skips ``#``-comments and blank lines). The returned digest is
    validated as 64 hex digits; a missing / malformed digest raises
    :class:`VendorRecordError`.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0] != "sha256":
            raise VendorRecordError(
                f"bad hash line {line!r}: expected 'sha256  <hash>  <file>'"
            )
        digest = parts[1]
        if not _SHA256_RE.match(digest):
            raise VendorRecordError(f"bad sha256 {digest!r}: not 64 hex digits")
        return digest
    raise VendorRecordError("no sha256 in hash body")


def write_record(record: VendorRecord, path: Path | str) -> None:
    """Write *record* to *path* as JSON (sorted keys, trailing newline)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record.as_dict(), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_record(path: Path | str) -> VendorRecord:
    """Load and validate the vendor record at *path*."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VendorRecordError(f"{path}: invalid JSON: {exc}") from exc
    return _record_from_dict(data, path)


def _record_from_dict(data: dict, source: Path | str) -> VendorRecord:
    """Build + validate a :class:`VendorRecord` from a parsed JSON dict."""
    required = ("name", "version", "tarball", "sha256", "site")
    missing = [key for key in required if key not in data]
    if missing:
        raise VendorRecordError(
            f"{source}: missing field(s): {', '.join(missing)}"
        )
    record = VendorRecord(
        name=str(data["name"]),
        version=str(data["version"]),
        tarball=str(data["tarball"]),
        sha256=str(data["sha256"]),
        site=str(data["site"]),
    )
    record.validate()
    return record


def load_records(dir: Path | str) -> Iterator[VendorRecord]:
    """Yield every vendor record (sorted by name) under *dir*.

    Each record is a ``<name>.json`` file. A malformed record raises
    :class:`VendorRecordError` (reported, not a crash).
    """
    dir = Path(dir)
    for path in sorted(dir.glob("*.json"), key=lambda p: p.stem):
        yield load_record(path)
