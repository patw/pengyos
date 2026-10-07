"""Parsing a ``Cargo.lock`` and detecting git-sourced crates.

The ``vendor`` engine must rewrite every crate whose source is a git repository
into a path into the vendored tree. To do that it first has to *read* the
workspace's ``Cargo.lock`` and find those crates. This module is the pure,
side-effect-free parser that turns a parsed ``Cargo.lock`` (the output of the
standard-library ``tomllib`` reader) into a small structured model and answers
the one question ``vendor`` cares about: **which packages are git-sourced?**

### Cargo.lock shape

A ``Cargo.lock`` is a TOML document::

    [package]
    name = "fixture-app"
    version = "0.1.0"
    edition = "2021"

    [[package]]
    name = "serde"
    version = "1.0.200"
    source = "registry+https://github.com/rust-lang/crates.io-index"
    checksum = "..."

    [[package]]
    name = "smithay-clipboard"
    version = "0.6.0"
    source = "git+https://github.com/pop-os/smithay-clipboard?tag=v0.6.0#abc1234..."

The root is a single ``[package]`` table (no ``source``). Every dependency is a
``[[package]]`` array-of-tables. A dependency's ``source`` is one of:

* ``registry+https://...`` — a crates.io registry package (not vendored by this
  engine; Buildroot's crates.io infra handles it).
* ``git+<url>#<sha>`` — a git package (the ones this engine rewrites).
* nothing / a ``path`` — a local/workspace path package (already on disk).

The git ``source`` string is ``git+`` + the repo URL + an optional ``?query``
(``tag``/``branch``/``subdir``) + ``#`` + the pinned commit SHA (the *fragment*).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .manifest import ManifestError

#: A crate's source kind.
SourceKind = Literal["root", "registry", "git", "path"]


class CargoLockError(ManifestError):
    """A Cargo.lock is missing, malformed, or has an unexpected shape."""


@dataclass(frozen=True)
class GitSource:
    """A git source URL as it appears in a ``Cargo.lock`` ``source`` field.

    ``url`` is the bare repository URL (``https://github.com/pop-os/...``),
    ``query`` holds the optional ``tag`` / ``branch`` / ``subdir`` params, and
    ``ref`` is the pinned commit SHA (the ``#`` fragment) — the immutable point
    the vendored copy is checked out at.
    """

    url: str
    query: dict[str, str]
    ref: str
    source_id: str

    @property
    def tag(self) -> str | None:
        """The ``tag`` query param, if the git source pinned a tag."""
        return self.query.get("tag")

    @property
    def branch(self) -> str | None:
        """The ``branch`` query param, if the git source floats a branch."""
        return self.query.get("branch")


@dataclass(frozen=True)
class CargoPackage:
    """One package entry from a ``Cargo.lock``.

    ``kind`` classifies the source. For a git package, ``git`` is the parsed
    :class:`GitSource`; for a registry package, ``registry_index`` is the
    index URL; everything else is a path/root package (``git``/``registry_index``
    are ``None``).
    """

    name: str
    version: str
    kind: SourceKind
    checksum: str | None = None
    git: GitSource | None = None
    registry_index: str | None = None

    @property
    def is_git(self) -> bool:
        """``True`` when this package is sourced from git."""
        return self.kind == "git"


@dataclass(frozen=True)
class CargoLock:
    """A parsed ``Cargo.lock``.

    ``packages`` is the list of ``[[package]]`` entries (the root ``[package]``
    is included, as a ``kind == "root"`` entry with no source). ``version`` is
    the lockfile format version (``3`` for modern cargo).
    """

    version: str
    packages: tuple[CargoPackage, ...]

    def git_packages(self) -> list[CargoPackage]:
        """The git-sourced packages, in lockfile order."""
        return [p for p in self.packages if p.is_git]

    def registry_packages(self) -> list[CargoPackage]:
        """The crates.io-registry packages, in lockfile order."""
        return [p for p in self.packages if p.kind == "registry"]


def parse_git_source(source: str) -> GitSource:
    """Parse a ``git+...`` source string into a :class:`GitSource`.

    ``git+https://github.com/pop-os/foo?tag=v0.6.0#abc1234`` becomes
    ``GitSource(url="https://github.com/pop-os/foo", query={"tag": "v0.6.0"},
    ref="abc1234")``. Raises :class:`CargoLockError` on a malformed string.
    """
    if not source.startswith("git+"):
        raise CargoLockError(f"not a git source: {source!r}")
    rest = source[len("git+") :]

    # Split the fragment (the pinned commit SHA) off the end.
    if "#" in rest:
        url_part, ref = rest.rsplit("#", 1)
    else:
        url_part, ref = rest, ""

    # Split the optional query string.
    if "?" in url_part:
        url, query_str = url_part.split("?", 1)
    else:
        url, query_str = url_part, ""
    # A well-formed git URL has a non-empty scheme before "://" (rejects
    # "git://" and a bare fragment). Cargo git sources use https/http/ssh/git.
    scheme_end = url.find("://")
    if scheme_end <= 0 or not re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*", url[:scheme_end]):
        raise CargoLockError(f"git source missing URL: {source!r}")

    query: dict[str, str] = {}
    for pair in query_str.split("&"):
        if not pair:
            continue
        if "=" not in pair:
            raise CargoLockError(f"malformed git query in {source!r}")
        key, value = pair.split("=", 1)
        query[key] = value
    return GitSource(url=url, query=query, ref=ref, source_id=source)


def _classify_source(source: str | None) -> tuple[SourceKind, GitSource | None, str | None]:
    """Classify a ``source`` field into (kind, git, registry_index)."""
    if source is None:
        return "root", None, None
    if source.startswith("git+"):
        return "git", parse_git_source(source), None
    if source.startswith("registry+"):
        return "registry", None, source[len("registry+") :]
    # A plain path (no source) or an unknown scheme: treat as a path package.
    return "path", None, None


def _parse_package(entry: Any, index: int) -> CargoPackage:
    """Validate and build one ``[[package]]`` (or root ``[package]``) entry."""
    where = "package" if index == 0 else f"package[{index - 1}]"
    if not isinstance(entry, dict):
        raise CargoLockError(f"{where}: expected a table, got {type(entry).__name__}")

    if "name" not in entry:
        raise CargoLockError(f"{where}: missing required field 'name'")
    name = entry["name"]
    if not isinstance(name, str) or not name.strip():
        raise CargoLockError(f"{where}.name: must be a non-empty string")

    if "version" not in entry:
        raise CargoLockError(f"{where}: missing required field 'version'")
    version = entry["version"]
    if not isinstance(version, str) or not version.strip():
        raise CargoLockError(f"{where}.version: must be a non-empty string")

    source = entry.get("source")
    kind, git, registry_index = _classify_source(source)
    checksum = entry.get("checksum")
    if checksum is not None and not isinstance(checksum, str):
        raise CargoLockError(f"{where}.checksum: must be a string")

    return CargoPackage(
        name=name,
        version=version,
        kind=kind,
        checksum=checksum,
        git=git,
        registry_index=registry_index,
    )


def parse_lock(data: Any) -> CargoLock:
    """Validate a parsed ``Cargo.lock`` document (``tomllib.load`` output).

    ``data`` is the already-parsed TOML document (no disk I/O), so this is
    trivially hermetic. Only the top-level ``[[package]]`` array and the root
    ``[package]`` table are modelled; ``[patch.*]`` tables are ignored (a patch
    is just a ``[[package]]`` entry with an extra ``replace`` relationship, and
    its source is still classified normally).
    """
    if not isinstance(data, dict):
        raise CargoLockError(
            f"top level must be a table, got {type(data).__name__}"
        )

    packages_raw = data.get("package")
    if packages_raw is None:
        raise CargoLockError("Cargo.lock has no 'package' entries")
    if not isinstance(packages_raw, list):
        raise CargoLockError(
            f"package: expected an array of tables, "
            f"got {type(packages_raw).__name__}"
        )

    if not packages_raw:
        raise CargoLockError("Cargo.lock has no 'package' entries")

    # The root is always the first element of the `package` array; the rest are
    # dependencies. A Cargo.lock always has a root, so an empty array is invalid.
    version = str(data.get("version", "3"))
    packages = [_parse_package(packages_raw[0], 0)]
    for entry in packages_raw[1:]:
        packages.append(_parse_package(entry, len(packages) + 1))

    return CargoLock(version=version, packages=tuple(packages))


def load_lock(path: Path | str) -> CargoLock:
    """Read and parse a ``Cargo.lock`` at ``path`` (the file must exist)."""
    path = Path(path)
    if not path.is_file():
        raise CargoLockError(f"Cargo.lock file not found: {path}")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise CargoLockError(f"{path.name}: malformed TOML: {exc}") from exc
    return parse_lock(data)


def git_slug(url: str) -> str:
    """Derive the on-disk directory slug for a git URL.

    Buildroot names a downloaded git crate after its repository basename (e.g.
    ``https://github.com/pop-os/smithay-clipboard`` -> ``smithay-clipboard``),
    so this mirrors that: strip a trailing ``.git``, take the last path
    component. It is used both to *find* the checked-out source on disk and to
    name the vendored copy, so the two always agree.
    """
    stripped = url.rstrip("/")
    if stripped.endswith(".git"):
        stripped = stripped[: -len(".git")]
    slug = stripped.rsplit("/", 1)[-1]
    if not slug:
        raise CargoLockError(f"cannot derive a slug from git URL: {url!r}")
    return slug
