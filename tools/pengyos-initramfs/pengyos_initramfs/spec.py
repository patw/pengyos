"""TOML spec parsing for ``pengyos-initramfs``.

This module turns the operator's ``initramfs.toml`` into a validated,
normalized :class:`Spec` value describing *which* binaries / files / kernel
modules get packed, plus the metadata-normalization settings the deterministic
cpio writer uses later.

The on-disk format (documented in ``spec.md``) is::

    # --- which things to include (paths are RELATIVE to --root) --------------
    binaries    = ["bin/busybox"]              # statically-linked executables
    files       = ["etc/os-release"]           # plain data files
    modules     = ["lib/modules/6.1/.../loop.ko"]  # kernel modules (.ko)
    directories = ["etc"]                       # explicit directory entries

    # --- determinism / normalization (all optional) --------------------------
    [build]
    mtime = "2000-01-01T00:00:00Z"   # ISO-8601; default 2000-01-01T00:00:00Z
    uid = 0
    gid = 0
    user = "root"
    group = "root"

    # --- the /init entry (generated into the archive by initgen.py) ---------
    [init]
    path = "init"         # archive-internal path
    mode = "0755"         # octal string (or integer)

Design goals:

* **Standard library only.** Parsing uses :mod:`tomllib` (Python 3.11+).
* **Fail loudly.** Anything ambiguous, absolute, or escaping the root (``..``)
  is rejected with a :class:`SpecError` rather than silently packed.
* **Deterministic output.** The parsed values are normalized (paths cleaned,
  mtime as a timezone-aware UTC :class:`datetime`), so downstream writers get
  stable inputs regardless of how the operator wrote the file.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

__all__ = [
    "Spec",
    "SpecError",
    "DEFAULT_MTIME",
    "load_spec",
    "parse_spec",
]

# Deterministic default metadata. The mtime default is a fixed, well-known
# instant so that builds never depend on the wall clock.
DEFAULT_MTIME = datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class SpecError(ValueError):
    """Raised when a spec file is malformed or contains invalid values."""


@dataclass(frozen=True)
class Spec:
    """A fully-validated, normalized description of what to build."""

    # Which things to include, as archive-relative POSIX paths (no leading '/').
    binaries: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    # Explicit directory entries (packed as cpio directory entries so the
    # kernel's initramfs populate creates them during extraction).
    directories: tuple[str, ...] = ()

    # Normalization settings for the cpio writer.
    mtime: datetime = DEFAULT_MTIME
    uid: int = 0
    gid: int = 0
    user: str = "root"
    group: str = "root"

    # /init generator settings (see initgen.py).
    init_path: str = "init"
    init_mode: int = 0o755

    def all_paths(self) -> tuple[str, ...]:
        """Return every included path, de-duplicated and sorted.

        This is the canonical, deterministic order the writer should walk.
        """
        seen: set[str] = set()
        out: list[str] = []
        for p in (*self.binaries, *self.files, *self.modules, *self.directories):
            if p not in seen:
                seen.add(p)
                out.append(p)
        return tuple(sorted(out))

    def paths_for(self, kind: str) -> tuple[str, ...]:
        """Return the normalized paths for a single kind
        ('binaries'/'files'/'modules'/'directories')."""
        return getattr(self, kind)


def _read_toml(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SpecError(f"cannot read spec file {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SpecError(f"invalid TOML in {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SpecError(f"spec {path} is not valid UTF-8: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError("spec top-level must be a TOML table")
    return data


def _clean_path(value: object, key: str) -> str:
    """Validate + normalize a single include path.

    Paths must be relative POSIX paths that do not escape the root (no ``..``).
    A leading ``./`` is stripped. Empty or absolute paths are rejected.
    """
    if not isinstance(value, str):
        raise SpecError(f"{key}: path must be a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        raise SpecError(f"{key}: path is empty")
    if "\\" in cleaned:
        raise SpecError(f"{key}: backslashes are not allowed in path {value!r}")
    if cleaned.startswith("/"):
        raise SpecError(f"{key}: path {value!r} must be relative to the root (no leading '/')")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    parts = PurePosixPath(cleaned).parts
    if ".." in parts:
        raise SpecError(f"{key}: path {value!r} must not contain '..'")
    # Normalize to a clean posix string (PurePosixPath drops redundant separators).
    normalized = str(PurePosixPath(cleaned))
    if not normalized or normalized == ".":
        raise SpecError(f"{key}: path {value!r} is empty after normalization")
    return normalized


def _string_list(raw: object, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SpecError(f"{key}: must be an array of strings")
    return tuple(_clean_path(v, f"{key}[{i}]") for i, v in enumerate(raw))


def _int(raw: object, key: str, lo: int, hi: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SpecError(f"{key}: must be an integer, got {type(raw).__name__}")
    if not (lo <= raw <= hi):
        raise SpecError(f"{key}: must be in range [{lo}, {hi}], got {raw}")
    return raw


def _parse_mode(raw: object, key: str) -> int:
    """Parse a file mode given as an octal string ('0755') or an integer."""
    if isinstance(raw, bool) or isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        text = raw.strip()
        try:
            value = int(text, 8) if text else None
        except ValueError as exc:
            raise SpecError(f"{key}: {raw!r} is not a valid octal mode") from exc
    else:
        raise SpecError(f"{key}: mode must be an integer or octal string")
    if value is None or not (0 <= value <= 0o7777):
        raise SpecError(f"{key}: mode out of range (must be 0..07777), got {raw!r}")
    return int(value)


def _parse_mtime(raw: object, key: str) -> datetime:
    if not isinstance(raw, str):
        raise SpecError(f"{key}: must be an ISO-8601 string, got {type(raw).__name__}")
    text = raw.strip()
    # fromisoformat on 3.11+ accepts a trailing 'Z'.
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SpecError(f"{key}: {raw!r} is not a valid ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_table(data: dict, key: str) -> dict:
    raw = data.get(key, {})
    if not isinstance(raw, dict):
        raise SpecError(f"[{key}] must be a table")
    return raw


def parse_spec(data: dict) -> Spec:
    """Parse an already-decoded TOML table into a validated :class:`Spec`."""
    if not isinstance(data, dict):
        raise SpecError("spec top-level must be a TOML table")

    binaries = _string_list(data.get("binaries"), "binaries")
    files = _string_list(data.get("files"), "files")
    modules = _string_list(data.get("modules"), "modules")
    directories = _string_list(data.get("directories"), "directories")

    build = _build_table(data, "build")
    mtime = _parse_mtime(build["mtime"], "build.mtime") if "mtime" in build else DEFAULT_MTIME
    uid = _int(build.get("uid", 0), "build.uid", 0, 0xFFFFFFFF)
    gid = _int(build.get("gid", 0), "build.gid", 0, 0xFFFFFFFF)
    user = build.get("user", "root")
    group = build.get("group", "root")
    if not isinstance(user, str) or not user:
        raise SpecError("build.user: must be a non-empty string")
    if not isinstance(group, str) or not group:
        raise SpecError("build.group: must be a non-empty string")

    init = _build_table(data, "init")
    init_path = init.get("path", "init")
    if not isinstance(init_path, str) or not init_path.strip():
        raise SpecError("init.path: must be a non-empty string")
    init_path = _clean_path(init_path, "init.path")
    init_mode = _parse_mode(init.get("mode", "0755"), "init.mode")

    return Spec(
        binaries=binaries,
        files=files,
        modules=modules,
        directories=directories,
        mtime=mtime,
        uid=uid,
        gid=gid,
        user=user,
        group=group,
        init_path=init_path,
        init_mode=init_mode,
    )


def load_spec(path: str | Path) -> Spec:
    """Read, parse and validate the spec file at *path*."""
    p = Path(path)
    if not p.is_file():
        raise SpecError(f"spec file not found: {p}")
    return parse_spec(_read_toml(p))
