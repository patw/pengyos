"""Parse and validate ``targets.toml`` — the declarative target definitions.

A ``targets.toml`` file has a single top-level ``targets`` array.  Each
element defines one build target with these fields:

======================  ======  ==================================================
field                   type    notes
======================  ======  ==================================================
``name``                str     required; unique within the file; ``[a-z0-9_-]+``
``br_version``          str     required; Buildroot release, ``YYYY.MM[.N]``
``external_tree``       str     required; ``BR2_EXTERNAL`` tree, relative to the
                                ``targets.toml`` file or absolute
``defconfig``           str     required; Buildroot config file name (no path
                                separators), e.g. ``pengyos_core_defconfig``
``board``               str     optional, default ``""``; BR2_BOARD board name
======================  ======  ==================================================

Validation errors raise :class:`TargetsFileError` whose message states the
target (or the whole file) and the exact problem, e.g.::

    targets.toml: targets[1]: missing required field 'defconfig'
    targets.toml: target 'a': duplicate target name
    targets.toml: targets[0].br_version: '26.02.3' is not a valid Buildroot
    version (expected YYYY.MM[.N])

Resolution: ``external_tree`` is resolved relative to the directory of the
``targets.toml`` file (absolute paths pass through).  ``..`` path components
are rejected.  No path is required to exist at parse time — the tree may be
fetched by a later step — so parsing is a pure, side-effect-free operation.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

#: Required fields (in declaration order) for every target entry.
REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "br_version",
    "external_tree",
    "defconfig",
)
#: Optional fields with their defaults.
OPTIONAL_FIELDS: dict[str, str] = {"board": ""}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_BR_VERSION_RE = re.compile(r"^\d{4}\.\d{2}(\.\d+)?$")


class TargetsFileError(ValueError):
    """A ``targets.toml`` file is missing, malformed, or fails validation."""


@dataclass(frozen=True)
class Target:
    """One validated build target."""

    name: str
    br_version: str
    external_tree: Path
    defconfig: str
    board: str = ""

    @property
    def br_external_env(self) -> str:
        """``BR2_EXTERNAL=...`` value for make invocations."""
        return str(self.external_tree)


def _check_str(target: dict, field: str, where: str) -> str:
    """Return ``target[field]`` if it is a non-empty string, else raise."""
    value = target[field]
    if not isinstance(value, str):
        raise TargetsFileError(
            f"{where}.{field}: expected a string, got {type(value).__name__}"
        )
    if not value.strip():
        raise TargetsFileError(f"{where}.{field}: must not be empty")
    return value


def _validate_entry(entry: object, index: int, seen: dict[str, int]) -> Target:
    where = f"targets[{index}]"
    if not isinstance(entry, dict):
        raise TargetsFileError(f"{where}: expected a table, got {type(entry).__name__}")

    for field in REQUIRED_FIELDS:
        if field not in entry:
            raise TargetsFileError(f"{where}: missing required field '{field}'")

    unknown = sorted(set(entry) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        raise TargetsFileError(
            f"{where}: unknown field(s): {', '.join(unknown)}"
        )

    name = _check_str(entry, "name", where)
    where = f"target '{name}'"
    if not _NAME_RE.match(name):
        raise TargetsFileError(
            f"{where}: name '{name}' must match [a-z0-9][a-z0-9_-]* "
            "(lowercase letters, digits, '_' or '-', starting alphanumeric)"
        )
    if name in seen:
        raise TargetsFileError(
            f"{where}: duplicate target name (first defined at targets[{seen[name]}])"
        )

    br_version = _check_str(entry, "br_version", where)
    if not _BR_VERSION_RE.match(br_version):
        raise TargetsFileError(
            f"{where}.br_version: '{br_version}' is not a valid Buildroot "
            "version (expected YYYY.MM[.N])"
        )

    external_tree_raw = _check_str(entry, "external_tree", where)
    if ".." in Path(external_tree_raw).parts:
        raise TargetsFileError(
            f"{where}.external_tree: must not contain '..' path components"
        )

    defconfig = _check_str(entry, "defconfig", where)
    if "/" in defconfig or ".." in Path(defconfig).parts:
        raise TargetsFileError(
            f"{where}.defconfig: '{defconfig}' must be a bare file name "
            "(no path separators)"
        )

    board = entry.get("board", OPTIONAL_FIELDS["board"])
    if not isinstance(board, str):
        raise TargetsFileError(
            f"{where}.board: expected a string, got {type(board).__name__}"
        )

    seen[name] = index
    return Target(
        name=name,
        br_version=br_version,
        external_tree=Path(external_tree_raw),
        defconfig=defconfig,
        board=board,
    )


def parse_targets(
    data: object,
    base_dir: Path | str = ".",
    source: str = "targets.toml",
) -> list[Target]:
    """Validate a parsed TOML document (``tomllib.load`` output).

    ``base_dir`` is used to resolve relative ``external_tree`` paths;
    ``source`` is the file name used in error messages.
    """
    if not isinstance(data, dict):
        raise TargetsFileError(
            f"{source}: top level must be a table, got {type(data).__name__}"
        )
    if "targets" not in data:
        raise TargetsFileError(f"{source}: missing top-level 'targets' array")
    entries = data["targets"]
    if not isinstance(entries, list):
        raise TargetsFileError(
            f"{source}: 'targets' must be an array, got {type(entries).__name__}"
        )
    if not entries:
        raise TargetsFileError(f"{source}: 'targets' must not be empty")

    seen: dict[str, int] = {}
    targets: list[Target] = []
    for index, entry in enumerate(entries):
        target = _validate_entry(entry, index, seen)
        tree = target.external_tree
        if not tree.is_absolute():
            tree = Path(base_dir) / tree
        targets.append(
            Target(
                name=target.name,
                br_version=target.br_version,
                external_tree=tree,
                defconfig=target.defconfig,
                board=target.board,
            )
        )
    return targets


def load_targets(path: Path | str) -> list[Target]:
    """Read and validate ``targets.toml`` at ``path`` (file must exist)."""
    path = Path(path)
    if not path.is_file():
        raise TargetsFileError(f"targets file not found: {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return parse_targets(data, base_dir=path.resolve().parent, source=path.name)


def get_target(targets: Sequence[Target], name: str) -> Target:
    """Return the target called ``name`` or raise :class:`TargetsFileError`."""
    for target in targets:
        if target.name == name:
            return target
    known = ", ".join(t.name for t in targets) or "<none>"
    raise TargetsFileError(f"unknown target '{name}' (known targets: {known})")


def load_target(path: Path | str, name: str) -> Target:
    """Convenience: :func:`load_targets` + :func:`get_target`."""
    return get_target(load_targets(path), name)
