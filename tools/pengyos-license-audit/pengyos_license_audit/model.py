"""Core data model for the PengyOS license-audit tool.

A *pin* is one entry in the pins/license manifest: a component, the tier it
sits in (core vs. leaf) and the license *claim* the maintainer made for it.
A *policy* is the freedom posture: which license families are allowed, whether
copyleft is permitted in the core, and the list of documented exceptions.

This module defines the policy schema: the ``Policy`` document (allowed families,
core-vs-leaf copyleft rule, exceptions) plus the ``Pin`` manifest entry. The
license *family* vocabulary lives in :mod:`.families`; the SPDX-id → family
classification lives in the classification subtask.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .errors import ManifestError, PolicyError


class Verdict(str, Enum):
    """The outcome of evaluating a single component against the policy.

    * ``PASS`` — the component is allowed outright (a declared allowed family,
      or copyleft permitted in the core, or a documented exception).
    * ``WARN`` — the component is *flagged*: it is not outright allowed, but the
      violation is soft enough to warn rather than fail. The canonical case is
      a copyleft claim in the core while ``copyleft_in_core`` is false *and* the
      component is only **dynamically** linked (the freedom concern is real but
      the linking exception keeps the downstream binary from being pulled in).
    * ``FAIL`` — the component violates the policy without an exception and the
      concern is hard: a copyleft claim statically linked into the core, a
      proprietary/unknown claim in the core, or any disallowed family that is
      not a soft-flaggable copyleft-in-core case.
    """

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"

    def __str__(self) -> str:  # pragma: no cover - trivial repr
        return self.value


@dataclass
class VerdictResult:
    """The evaluation of one component: its id, the resulting family, verdict,
    and a short human-readable reason."""

    component_id: str
    family: str
    verdict: Verdict
    reason: str


@dataclass
class Pin:
    """A single pinned component in the manifest."""

    id: str
    license: str
    tier: str = "leaf"
    link: str = "dynamic"
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_core(self) -> bool:
        return self.tier == "core"

    @property
    def links_static(self) -> bool:
        return self.link == "static"


@dataclass
class Policy:
    """The freedom posture applied to the pins.

    The schema has exactly three axes (see spec.md):

    * ``allowed_families`` — SPDX id *prefixes* allowed outright (permissive
      preferred). A component whose claim starts with one of these passes.
    * ``copyleft_in_core`` — whether weak/strong copyleft is permitted in the
      ``core`` tier. When ``False`` (the default) a copyleft claim in the core
      is *flagged*; leaves follow ``allowed_families``.
    * ``exceptions`` — component ids exempt from the policy entirely.

    The license *family* vocabulary (permissive / weak-copyleft / strong-copyleft
    / proprietary / unknown) is defined in :mod:`.families`; the SPDX-id → family
    mapping is implemented in the classification subtask.
    """

    # SPDX id prefixes allowed outright (e.g. "MIT", "Apache-2.0").
    allowed_families: list[str] = field(default_factory=list)
    # Whether weak/strong copyleft is allowed in the core tier.
    copyleft_in_core: bool = False
    # Component ids exempt from the policy (documented exceptions).
    exceptions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def exception_ids(self) -> frozenset[str]:
        """The exception set as a hashable, lookup-optimized frozenset."""
        return frozenset(self.exceptions)

    def is_exception(self, component_id: str) -> bool:
        """Return ``True`` if *component_id* is a documented policy exception."""
        return component_id in self.exception_ids


def load_pins(path: str) -> list[Pin]:
    """Load and validate the pins/license manifest at *path*.

    Raises :class:`ManifestError` on missing files, unreadable TOML, or a
    manifest that fails the baseline structural check.
    """
    from . import toml_io

    try:
        data = toml_io.read_toml(path)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"pins manifest {path!r} is not valid TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"pins manifest {path!r} must be a TOML table")

    raw_components = data.get("component")
    if raw_components is None:
        raise ManifestError(f"pins manifest {path!r} has no 'component' entries")
    if isinstance(raw_components, dict):
        raw_components = [raw_components]
    if not isinstance(raw_components, list):
        raise ManifestError(
            f"pins manifest {path!r}: 'component' must be a list of tables"
        )

    pins: list[Pin] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_components):
        if not isinstance(raw, dict):
            raise ManifestError(
                f"pins manifest {path!r}: component #{idx + 1} must be a table"
            )
        cid = raw.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise ManifestError(
                f"pins manifest {path!r}: component #{idx + 1} is missing a string 'id'"
            )
        if cid in seen:
            raise ManifestError(
                f"pins manifest {path!r}: duplicate component id {cid!r}"
            )
        seen.add(cid)
        license_claim = raw.get("license")
        if not isinstance(license_claim, str) or not license_claim.strip():
            raise ManifestError(
                f"pins manifest {path!r}: component {cid!r} is missing a string 'license'"
            )
        tier = str(raw.get("tier", "leaf")).strip().lower()
        if tier not in ("core", "leaf"):
            raise ManifestError(
                f"pins manifest {path!r}: component {cid!r} has invalid tier {tier!r}"
            )
        link = str(raw.get("link", "dynamic")).strip().lower()
        if link not in ("static", "dynamic"):
            raise ManifestError(
                f"pins manifest {path!r}: component {cid!r} has invalid link {link!r}"
            )
        pins.append(Pin(id=cid, license=license_claim.strip(), tier=tier, link=link, raw=raw))

    if not pins:
        raise ManifestError(f"pins manifest {path!r} has no components")
    return pins


def load_policy(path: str) -> Policy:
    """Load and validate the policy file at *path*."""
    from . import toml_io

    try:
        data = toml_io.read_toml(path)
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"policy file {path!r} is not valid TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"policy file {path!r} must be a TOML table")

    allowed = _require_nonempty_string_list(data, "allowed_families")
    exceptions = _require_nonempty_string_list(data, "exceptions")

    copyleft = data.get("copyleft_in_core", False)
    if not isinstance(copyleft, bool):
        raise PolicyError(
            f"policy 'copyleft_in_core' must be a boolean, "
            f"got {type(copyleft).__name__}"
        )

    return Policy(
        allowed_families=allowed,
        copyleft_in_core=copyleft,
        exceptions=exceptions,
        raw=data,
    )


def _require_nonempty_string_list(data: dict[str, Any], key: str) -> list[str]:
    """Validate a policy list-of-strings field.

    Returns the trimmed, de-duplicated list. Missing key → empty list. A
    non-list, a non-string element, an empty/whitespace element, or a
    duplicate are all rejected with :class:`PolicyError`.
    """
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise PolicyError(f"policy '{key}' must be a list of strings")

    singular = key[:-1] if key.endswith("s") else key
    seen: set[str] = set()
    result: list[str] = []
    for x in value:
        s = x.strip()
        if not s:
            raise PolicyError(f"policy '{key}' contains an empty string")
        if s in seen:
            raise PolicyError(
                f"policy '{key}' contains duplicate {singular}: {s!r}"
            )
        seen.add(s)
        result.append(s)
    return result
