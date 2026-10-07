"""The COSMIC component manifest — the recipes lane's committed data.

``cosmic.toml`` is the single source of truth for the Buildroot recipes this
repo authors: a list of COSMIC git Rust components, each pinned to a concrete
reference with a declared license and a set of inter-component dependencies.
This module loads and validates it and exposes it grouped for the recipes-lane
CLI (``manifest`` / ``emit`` / ``lint``).

The file format is TOML, read with the standard-library ``tomllib``. The top
level is a table holding one or more ``[[component]]`` arrays-of-tables. Each
component table has:

======================  =========  ==============================================
field                   required   notes
======================  =========  ==============================================
``name``                yes        ``[a-z][a-z0-9_-]*`` (a Buildroot package name)
``group``               yes        one of ``core`` / ``login`` / ``shell`` /
                                       ``apps`` (see :data:`GROUPS`)
``git_url``             yes        a git URL (``https://``, ``http://``, ``git://``
                                       or ``git@host:``)
``tag``                 yes        a **pinned** reference (see
                                       :func:`pengyos_cosmic_pkg.manifest.is_pinned`)
``license``             yes        a non-empty SPDX expression
``features``            no         cargo feature names (default: none)
``binaries``            no         cargo binary target names (default: none)
``deps``                no         names of other components in this manifest
                                       (default: none)
======================  =========  ==============================================

``deps`` are declared by *name only*: every name must reference another
component in this manifest (the loader asserts this). The engine
(:mod:`pengyos_cosmic_pkg`) sees each component with its deps *expanded* to the
target's ``git_url`` / ``tag`` — see :func:`component_manifest` — so the emitted
recipe pins the same reference the manifest declares.

Parsing is a pure, side-effect-free operation over an already-read TOML document
(or a path), so it is safe to run in hermetic, network-free tests.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pengyos_cosmic_pkg.manifest import ManifestError, is_pinned

from .non_cargo import assert_manifest_non_cargo_free

#: The manifest groups, in the order the recipes-lane CLI lists/iterates them.
#: A component's ``group`` must be one of these (see :func:`load_manifest`).
GROUPS: tuple[str, ...] = ("core", "login", "shell", "apps")

#: Component / dependency names: a letter followed by lowercase alphanumeric,
#: underscores and dashes. Mirrors the engine's Buildroot-package naming rule
#: (Kconfig symbols may not start with a digit).
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
#: A git URL: an ``https://`` / ``http://`` / ``git://`` authority path, or an
#: SSH ``git@host:path`` form.
_GIT_URL_RE = re.compile(r"^(?:git\+)?(?:https?://|git://|git@[^:/]+:)[^\\]+$")

#: The manifest file name, relative to the repo root.
MANIFEST_NAME = "cosmic.toml"


@dataclass(frozen=True)
class ComponentEntry:
    """One validated component from the manifest.

    ``group`` classifies it for the recipes-lane CLI; ``name`` / ``git_url`` /
    ``tag`` / ``license`` are the pinning identity; ``features`` / ``binaries``
    are cargo knobs; ``deps`` are the names of the other components it depends
    on. Instances are immutable.
    """

    group: str
    name: str
    git_url: str
    tag: str
    license: str
    features: tuple[str, ...] = field(default_factory=tuple)
    binaries: tuple[str, ...] = field(default_factory=tuple)
    deps: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CosmicManifest:
    """The loaded, validated manifest.

    ``components`` is the list of :class:`ComponentEntry` in file order;
    ``groups`` maps each group to its component names (file order).
    """

    path: Path
    components: tuple[ComponentEntry, ...]
    groups: dict[str, tuple[str, ...]]

    def names(self, group: str | None = None) -> tuple[str, ...]:
        """The component names, optionally filtered to ``group`` (file order)."""
        if group is None:
            return tuple(c.name for c in self.components)
        return self.groups.get(group, ())

    def entries(self, group: str | None = None) -> tuple[ComponentEntry, ...]:
        """The component entries, optionally filtered to ``group`` (file order)."""
        if group is None:
            return self.components
        wanted = set(self.groups.get(group, ()))
        return tuple(c for c in self.components if c.name in wanted)

    def by_name(self) -> dict[str, ComponentEntry]:
        """A name -> :class:`ComponentEntry` map (for dep expansion)."""
        return {c.name: c for c in self.components}

    def group_of(self, name: str) -> str:
        """The group a component belongs to (raises :class:`KeyError` if unknown)."""
        for group, names in self.groups.items():
            if name in names:
                return group
        raise KeyError(name)


def _require_nonempty_str(where: str, value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(
            f"{where}.{key}: expected a string, got {type(value).__name__}"
        )
    text = value.strip()
    if not text:
        raise ManifestError(f"{where}.{key}: must not be empty")
    return text


def _require_array(where: str, value: Any, key: str) -> tuple[str, ...]:
    """Return ``value`` as a de-duplicated tuple of non-empty strings."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ManifestError(
            f"{where}.{key}: expected an array, got {type(value).__name__}"
        )
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ManifestError(
                f"{where}.{key}: expected strings, got {type(item).__name__}"
            )
        if not item.strip():
            raise ManifestError(f"{where}.{key}: must not contain an empty string")
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def _parse_component(entry: Any, index: int) -> ComponentEntry:
    """Validate one ``[[component]]`` table and build a :class:`ComponentEntry`.

    ``deps`` are read raw here (they are validated against the full set of
    component names in :func:`parse_manifest`, since a dep may be declared
    before or after its target).
    """
    where = f"component[{index}]"
    if not isinstance(entry, dict):
        raise ManifestError(
            f"{where}: expected a table, got {type(entry).__name__}"
        )

    for key in ("name", "group", "git_url", "tag", "license"):
        if key not in entry:
            raise ManifestError(f"{where}: missing required field '{key}'")

    unknown = sorted(
        set(entry)
        - {"name", "group", "git_url", "tag", "license", "features", "binaries", "deps"}
    )
    if unknown:
        raise ManifestError(f"{where}: unknown field(s): {', '.join(unknown)}")

    name = _require_nonempty_str(where, entry["name"], "name")
    if not _NAME_RE.match(name):
        raise ManifestError(
            f"{where}.name: '{name}' must match [a-z][a-z0-9_-]* "
            "(lowercase letters, digits, '_' or '-', starting with a letter)"
        )

    group = _require_nonempty_str(where, entry["group"], "group")
    if group not in GROUPS:
        raise ManifestError(
            f"{where}.group: '{group}' is not a known group "
            f"(expected one of {', '.join(GROUPS)})"
        )

    git_url = _require_nonempty_str(where, entry["git_url"], "git_url")
    if not _GIT_URL_RE.match(git_url):
        raise ManifestError(
            f"{where}.git_url: '{git_url}' is not a git URL "
            "(expected https://, http://, git:// or git@host:)"
        )

    tag = _require_nonempty_str(where, entry["tag"], "tag")
    if not is_pinned(tag):
        raise ManifestError(
            f"{where}.tag: '{tag}' is not a pinned reference "
            "(a commit SHA or a version tag — not a floating branch/ref)"
        )

    license_ = _require_nonempty_str(where, entry["license"], "license")

    features = _require_array(where, entry.get("features"), "features")
    binaries = _require_array(where, entry.get("binaries"), "binaries")
    deps = _require_array(where, entry.get("deps"), "deps")

    return ComponentEntry(
        group=group,
        name=name,
        git_url=git_url,
        tag=tag,
        license=license_,
        features=features,
        binaries=binaries,
        deps=deps,
    )


def parse_manifest(data: Any, *, source: str = MANIFEST_NAME) -> CosmicManifest:
    """Validate a parsed manifest document (``tomllib.load`` output).

    ``source`` is the file name used in error messages. Nothing is required to
    exist on disk at parse time, so this is safe in hermetic tests.
    """
    if not isinstance(data, dict):
        raise ManifestError(
            f"{source}: top level must be a table, got {type(data).__name__}"
        )

    components_raw = data.get("component")
    if components_raw is None:
        raise ManifestError(
            f"{source}: no 'component' entries (expected at least one [[component]])"
        )
    if not isinstance(components_raw, list):
        raise ManifestError(
            f"{source}.component: expected an array of tables, "
            f"got {type(components_raw).__name__}"
        )
    if not components_raw:
        raise ManifestError(f"{source}: no 'component' entries")

    entries: list[ComponentEntry] = []
    index_of: dict[str, int] = {}
    for index, component in enumerate(components_raw):
        entry = _parse_component(component, index)
        if entry.name in index_of:
            raise ManifestError(
                f"component[{index_of[entry.name]}]: "
                f"duplicate component name '{entry.name}'"
            )
        index_of[entry.name] = index
        entries.append(entry)

    names = {c.name for c in entries}
    for entry in entries:
        for dep in entry.deps:
            if dep not in names:
                raise ManifestError(
                    f"{entry.name}: undeclared dependency '{dep}' "
                    "(must reference another component in this manifest)"
                )

    # A non-cargo component (e.g. cosmic-workspaces, a GNOME Shell extension)
    # has no place in the cargo recipes manifest: reject it loudly so the
    # exclusion is structural, not an accident of absence. See
    # ``pengyos_cosmic_recipes.non_cargo``.
    assert_manifest_non_cargo_free(entries)

    groups: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        groups.setdefault(entry.group, ())
        groups[entry.group] = groups[entry.group] + (entry.name,)

    return CosmicManifest(
        path=Path(source), components=tuple(entries), groups=groups
    )


def load_manifest(path: Path | str | None = None) -> CosmicManifest:
    """Read and validate the manifest at ``path`` (defaults to the repo's).

    A missing file, a TOML syntax error, or a schema/semantic validation failure
    all surface as :class:`ManifestError`.
    """
    if path is None:
        path = Path(__file__).resolve().parent.parent / MANIFEST_NAME
    path = Path(path)
    if not path.is_file():
        raise ManifestError(f"manifest file not found: {path}")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path}: malformed TOML: {exc}") from exc
    return parse_manifest(data, source=str(path))


def component_manifest(
    entry: ComponentEntry, by_name: dict[str, ComponentEntry]
) -> dict[str, Any]:
    """Build an engine-compatible single-component manifest table for ``entry``.

    ``entry``'s name-only ``deps`` are expanded to the target components'
    ``git_url`` / ``tag`` (looked up from ``by_name``); the recipes-lane
    ``group`` field is dropped; the result is ready for
    :func:`pengyos_cosmic_pkg.manifest.parse_manifest`.
    """
    from pengyos_cosmic_pkg.manifest import dependency_table

    table: dict[str, Any] = {
        "name": entry.name,
        "git_url": entry.git_url,
        "tag": entry.tag,
        "license": entry.license,
    }
    if entry.features:
        table["features"] = list(entry.features)
    if entry.binaries:
        table["binaries"] = list(entry.binaries)
    table["deps"] = [
        dependency_table(
            name=dep,
            git_url=by_name[dep].git_url,
            tag=by_name[dep].tag,
        )
        for dep in entry.deps
    ]
    return table
