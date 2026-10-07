"""The component manifest — the data model for one git Rust component.

A *component manifest* is the single unit of input this engine consumes: it
describes **one** git Rust component (a COSMIC crate such as ``cosmic-text`` or
``libcosmic``) well enough to vendor its source and emit a Buildroot cargo
recipe. It carries the four things a reproducible recipe needs — **where** the
component lives (``git_url`` + a pinned ``tag``), **what** it is (``name``), and
**under what license** it is distributed (``license``) — plus two pieces of
build configuration that Buildroot's cargo infra asks for explicitly: the cargo
**features** to enable and the cargo **binaries** to build — and the list of its
own **dependencies** (which, for a git workspace, are themselves git components).

The file format is TOML, read with the standard-library ``tomllib``. A manifest
is a single TOML table::

    # cosmic-text.toml
    name = "cosmic-text"
    git_url = "https://github.com/pop-os/libcosmic"
    tag = "v0.6.0"
    license = "MIT OR Apache-2.0"

    # Cargo features to enable for this component.
    features = ["default"]
    # Cargo binaries (targets) to build.
    binaries = ["cosmic-text"]

    [[deps]]
    name = "smithay-clipboard"
    git_url = "https://github.com/pop-os/smithay-clipboard"
    tag = "v0.6.0"

This module defines the :class:`Dependency` and :class:`ComponentManifest` data
models, the :class:`ManifestError` exception, and the pure, side-effect-free
loader (:func:`parse_manifest` / :func:`load_manifest`) that turns a parsed
TOML document into a :class:`ComponentManifest`.

### Field reference

======================  =========  ==============================================
field                   required   notes
======================  =========  ==============================================
``name``                yes        ``[a-z0-9][a-z0-9_-]*`` (Buildroot package name)
``git_url``             yes        a git URL (``https://``, ``http://``, ``git://``
                                       or ``git@host:``)
``tag``                 yes        a **pinned** reference (see :func:`is_pinned`)
``license``             yes        a well-formed SPDX expression
``features``            no         cargo feature names (default: none)
``binaries``            no         cargo binary target names (default: none)
``deps``                no         array of ``[[deps]]`` tables (default: none)
======================  =========  ==============================================

Each ``[[deps]]`` table has the same ``name`` / ``git_url`` / ``tag`` shape as
the root component (a dependency is itself a git component).

Parsing is a pure operation — nothing is required to exist on disk at parse
time — so it is safe to run in hermetic, network-free tests.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

#: The root component's required fields, in declaration order.
REQUIRED_FIELDS: tuple[str, ...] = ("name", "git_url", "tag", "license")
#: The root component's optional fields and their defaults.
OPTIONAL_FIELDS: tuple[str, ...] = ("features", "binaries")
#: The fields of each ``[[deps]]`` table, in declaration order.
DEP_REQUIRED_FIELDS: tuple[str, ...] = ("name", "git_url", "tag")

#: Component / dependency names: a lowercase letter followed by lowercase
#: alphanumeric, underscores and dashes. It must start with a **letter** (not a
#: digit): the manifest ``name`` becomes a Buildroot package directory and a
#: ``BR2_PACKAGE_<NAME>`` Kconfig symbol, and Kconfig symbols may not start with
#: a digit. (The sibling ``pengyos-pins`` tool allows digit-starting names
#: because it tracks arbitrary pinned components, not only Buildroot packages.)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
#: A git URL: an ``https://`` / ``http://`` / ``git://`` authority path, or an
#: SSH ``git@host:path`` form.
_GIT_URL_RE = re.compile(r"^(?:git\+)?(?:https?://|git://|git@[^:/]+:)[^\\]+$")
#: A pinned reference: a full or short commit SHA (hex), or a version tag that
#: looks like a semver-ish release (``v``-prefixed or carrying a digit). A bare
#: branch name such as ``main`` / ``master`` / ``stable`` is **not** pinned.
_PINNED_RE = re.compile(r"^[0-9a-f]{7,40}$|v?\d+(\.\d+)*[-+_.]?[a-z0-9.]*$")


class ManifestError(ValueError):
    """A component manifest is missing, malformed, or fails schema validation."""


@dataclass(frozen=True)
class Dependency:
    """One git dependency of a component.

    A dependency is itself a git component: it has a ``name``, a ``git_url`` and
    a pinned ``tag``. Instances are immutable.
    """

    name: str
    git_url: str
    tag: str


@dataclass(frozen=True)
class ComponentManifest:
    """One validated component manifest.

    The four required fields plus the optional cargo ``features`` / ``binaries``
    and the tuple of :class:`Dependency` objects. Instances are immutable, so a
    manifest can be shared between the ``vendor`` and ``recipe`` subcommands
    without a caller mutating shared state.
    """

    name: str
    git_url: str
    tag: str
    license: str
    features: tuple[str, ...] = field(default_factory=tuple)
    binaries: tuple[str, ...] = field(default_factory=tuple)
    deps: tuple[Dependency, ...] = field(default_factory=tuple)

    @property
    def version(self) -> str:
        """The pinned tag — the component's resolved version.

        This is the value the ``recipe`` subcommand pins into
        ``BR2_PACKAGE_<PKG>_VERSION`` (and the value the ``lint`` subcommand
        checks is present and non-floating).
        """
        return self.tag

    def has_features(self) -> bool:
        """``True`` when at least one cargo feature is enabled."""
        return bool(self.features)

    def has_binaries(self) -> bool:
        """``True`` when at least one cargo binary target is built."""
        return bool(self.binaries)

    def has_deps(self) -> bool:
        """``True`` when the component declares any dependencies."""
        return bool(self.deps)


def is_pinned(reference: str) -> bool:
    """Return ``True`` when ``reference`` is a pinned git reference.

    A reference is *pinned* when it names a concrete, immutable point: a full or
    short commit SHA (7-40 hex chars), or a version tag that looks like a
    release (``v``-prefixed or carrying a digit). A bare branch name such as
    ``main`` / ``master`` / ``stable`` / ``latest`` is **floating** and is
    rejected.

    This is the single predicate shared by the manifest validator and the
    ``lint`` subcommand's "no floating git ref" rule, so the two never disagree
    about what "pinned" means.
    """
    if not reference or not reference.strip():
        return False
    return bool(_PINNED_RE.match(reference))


def _require_str(where: str, value: Any, key: str) -> str:
    """Return ``value`` as a non-empty string, else raise :class:`ManifestError`."""
    if not isinstance(value, str):
        raise ManifestError(
            f"{where}.{key}: expected a string, got {type(value).__name__}"
        )
    if not value.strip():
        raise ManifestError(f"{where}.{key}: must not be empty")
    return value


def _require_git_url(where: str, value: str, key: str) -> str:
    """Return ``value`` as a non-empty git URL, else raise."""
    text = _require_str(where, value, key)
    if not _GIT_URL_RE.match(text):
        raise ManifestError(
            f"{where}.{key}: '{text}' is not a git URL "
            "(expected https://, http://, git:// or git@host:)"
        )
    return text


def _require_pinned(where: str, value: str, key: str) -> str:
    """Return ``value`` as a non-empty pinned reference, else raise."""
    text = _require_str(where, value, key)
    if not is_pinned(text):
        raise ManifestError(
            f"{where}.{key}: '{text}' is not a pinned reference "
            "(a commit SHA or a version tag — not a floating branch/ref)"
        )
    return text


def _require_str_array(where: str, value: Any, key: str) -> tuple[str, ...]:
    """Return ``value`` as a de-duplicated tuple of non-empty strings.

    Order is preserved (first occurrence) so the result is stable for a given
    input. Cargo feature/binary names are unconstrained, so there is no
    ``allowed`` set here — any non-empty string is a valid name.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ManifestError(
            f"{where}.{key}: expected an array of strings, "
            f"got {type(value).__name__}"
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


def _parse_dependency(entry: object, index: int, seen: dict[str, int]) -> Dependency:
    """Validate one ``[[deps]]`` table and build a :class:`Dependency`."""
    where = f"deps[{index}]"
    if not isinstance(entry, dict):
        raise ManifestError(
            f"{where}: expected a table, got {type(entry).__name__}"
        )

    for key in DEP_REQUIRED_FIELDS:
        if key not in entry:
            raise ManifestError(f"{where}: missing required field '{key}'")

    unknown = sorted(set(entry) - set(DEP_REQUIRED_FIELDS))
    if unknown:
        raise ManifestError(f"{where}: unknown field(s): {', '.join(unknown)}")

    name = _require_str(where, entry["name"], "name")
    if not _NAME_RE.match(name):
        raise ManifestError(
            f"{where}.name: '{name}' must match [a-z0-9][a-z0-9_-]* "
            "(lowercase letters, digits, '_' or '-', starting alphanumeric)"
        )
    if name in seen:
        raise ManifestError(
            f"deps[{seen[name]}]: duplicate dependency name '{name}'"
        )

    git_url = _require_git_url(where, entry["git_url"], "git_url")
    tag = _require_pinned(where, entry["tag"], "tag")

    seen[name] = index
    return Dependency(name=name, git_url=git_url, tag=tag)


def parse_manifest(
    data: Any,
    *,
    base_dir: Path | str = ".",
    source: str = "manifest.toml",
) -> ComponentManifest:
    """Validate a parsed component-manifest document (``tomllib.load`` output).

    ``base_dir`` is accepted for API symmetry with sibling loaders (no paths are
    resolved here — ``git_url`` is absolute); ``source`` is the file name used in
    error messages.
    """
    if not isinstance(data, dict):
        raise ManifestError(
            f"{source}: top level must be a table, got {type(data).__name__}"
        )

    for key in REQUIRED_FIELDS:
        if key not in data:
            raise ManifestError(f"{source}: missing required field '{key}'")

    unknown = sorted(set(data) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS) - {"deps"})
    if unknown:
        raise ManifestError(f"{source}: unknown field(s): {', '.join(unknown)}")

    name = _require_str(source, data["name"], "name")
    if not _NAME_RE.match(name):
        raise ManifestError(
            f"{source}.name: '{name}' must match [a-z0-9][a-z0-9_-]* "
            "(lowercase letters, digits, '_' or '-', starting alphanumeric)"
        )

    git_url = _require_git_url(source, data["git_url"], "git_url")
    tag = _require_pinned(source, data["tag"], "tag")
    license_ = _require_str(source, data["license"], "license")

    features = _require_str_array(source, data.get("features"), "features")
    binaries = _require_str_array(source, data.get("binaries"), "binaries")

    deps_raw = data.get("deps")
    if deps_raw is None:
        deps: list[Dependency] = []
    elif isinstance(deps_raw, dict):
        deps = [_parse_dependency(deps_raw, 0, {})]
    elif isinstance(deps_raw, list):
        seen: dict[str, int] = {}
        deps = [
            _parse_dependency(entry, index, seen)
            for index, entry in enumerate(deps_raw)
        ]
    else:
        raise ManifestError(
            f"{source}.deps: expected an array of tables, "
            f"got {type(deps_raw).__name__}"
        )

    return ComponentManifest(
        name=name,
        git_url=git_url,
        tag=tag,
        license=license_,
        features=features,
        binaries=binaries,
        deps=tuple(deps),
    )


def load_manifest(path: Path | str) -> ComponentManifest:
    """Read and validate a component manifest at ``path`` (the file must exist).

    A missing file, a TOML syntax error, or a schema/semantic validation failure
    all surface as :class:`ManifestError` — the loader owns the whole
    "this manifest is no good" contract, so callers get a single exception type.
    """
    path = Path(path)
    if not path.is_file():
        raise ManifestError(f"manifest file not found: {path}")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path.name}: malformed TOML: {exc}") from exc
    return parse_manifest(data, base_dir=path.parent, source=path.name)


def component_table(
    name: str = "cosmic-text",
    *,
    git_url: str = "https://github.com/pop-os/libcosmic",
    tag: str = "v0.6.0",
    license: str = "MIT OR Apache-2.0",
    features: Sequence[str] | None = None,
    binaries: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal valid manifest table for tests and callers.

    Every field is overridable; the defaults describe a plausible COSMIC
    component so a test can start from a valid table and override only what it
    wants to exercise.
    """
    return {
        "name": name,
        "git_url": git_url,
        "tag": tag,
        "license": license,
        **(
            {"features": list(features)}
            if features is not None
            else {}
        ),
        **(
            {"binaries": list(binaries)}
            if binaries is not None
            else {}
        ),
    }


def dependency_table(
    name: str = "smithay-clipboard",
    *,
    git_url: str = "https://github.com/pop-os/smithay-clipboard",
    tag: str = "v0.6.0",
) -> dict[str, str]:
    """Build a minimal valid ``[[deps]]`` table for tests and callers."""
    return {
        "name": name,
        "git_url": git_url,
        "tag": tag,
    }
