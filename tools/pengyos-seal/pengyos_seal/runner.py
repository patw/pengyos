"""The injected runner seam for ``pengyos-seal``.

Every external tool call the seal makes — ``pengyos-sign``, ``pengyos-witness-
bundle``, ``pengyos-verifier``, ``pengyos-repro-report`` — and every filesystem
side effect it performs inside the build directory (symlinking the SBOM to its
conventional root name, writing signatures, materialising the witness view) goes
through a *runner* injected into the seal driver. This is what keeps the suite
offline and root-free: tests inject a recording in-memory runner instead of
touching a real build tree, invoking a real tool, or needing network or root.

Two runners are shipped:

* :class:`FileSystemRunner` — the production runner. It writes real files,
  creates real symlinks, and runs real commands via :mod:`subprocess`. It is
  only ever used on a real target, never in the ordinary suite.
* :class:`MemoryRunner` — a fully in-memory runner for tests. It records every
  call and can be pre-seeded with command results and a virtual build tree, so
  no subprocess, no real filesystem and no root are required.

Both implement :class:`Runner`, the protocol the seal driver depends on.

Stdlib only: this module imports nothing outside the standard library.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class RunResult:
    """The outcome of running an external command through the runner."""

    args: tuple[str, ...]
    rc: int
    stdout: str = ""
    stderr: str = ""


@runtime_checkable
class Runner(Protocol):
    """The seam the seal driver depends on.

    Implementations must provide :meth:`run_command` plus the filesystem
    primitives the seal performs inside the build directory: :meth:`exists`,
    :meth:`is_dir`, :meth:`read_bytes`, :meth:`write_bytes`, :meth:`symlink` and
    :meth:`readlink`. Filesystem methods resolve ``path`` against whatever root
    the caller passes in (the seal always passes build-dir-relative paths), so a
    single runner can drive a real or a virtual build tree.
    """

    def run_command(self, args: Sequence[str]) -> RunResult: ...

    def exists(self, path: Path) -> bool: ...

    def is_dir(self, path: Path) -> bool: ...

    def read_bytes(self, path: Path) -> bytes: ...

    def write_bytes(self, path: Path, data: bytes) -> None: ...

    def symlink(self, target: Path, link: Path) -> None: ...

    def readlink(self, link: Path) -> Path: ...

    def walk(self, path: Path) -> list[str]: ...


@dataclass
class FileSystemRunner:
    """Production runner: writes real files, creates real symlinks, runs real commands.

    Only used on a real target. The ordinary suite never constructs one.

    ``overrides`` maps a tool name (``pengyos-sign`` etc.) to an explicit binary
    path; when present it replaces that tool's argv[0], so a caller can point the
    seal at a specific build of a tool without shadowing ``PATH``.

    ``build_dir`` is the build directory the seal operates in. Relative paths
    handed to the filesystem methods (and the cwd of ``run_command``) resolve
    against it; absolute paths are used as-is. Defaults to ``"."`` (the current
    working directory), so existing callers that pass absolute paths are
    unaffected.
    """

    overrides: dict[str, str] = field(default_factory=dict)
    build_dir: str = "."

    def _resolve(self, args: Sequence[str]) -> list[str]:
        """Replace argv[0] for any tool with a registered binary override.

        A ``None`` (or empty) override value means "no override" and is left
        alone: substituting ``None`` into the argv would make ``subprocess``
        raise ``TypeError`` instead of running the tool by name from ``PATH``.
        """
        resolved = [str(a) for a in args]
        if resolved and self.overrides.get(resolved[0]):
            resolved[0] = self.overrides[resolved[0]]
        return resolved

    def _abs(self, path: "os.PathLike[str] | str") -> str:
        """Resolve ``path`` against ``build_dir`` unless it is already absolute."""
        path = os.fspath(path)
        if os.path.isabs(path):
            return path
        return os.path.join(self.build_dir, path)

    def run_command(self, args: Sequence[str]) -> RunResult:
        argv = self._resolve(args)
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                cwd=self.build_dir,
            )
        except FileNotFoundError as exc:
            # A missing binary (e.g. on a host without pengyos tools installed)
            # fails closed with rc=127 rather than raising.
            return RunResult(
                args=tuple(str(a) for a in args),
                rc=127,
                stderr=f"command not found: {exc.filename}",
            )
        return RunResult(
            args=tuple(str(a) for a in args),
            rc=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def exists(self, path: Path) -> bool:
        return Path(self._abs(path)).exists()

    def is_dir(self, path: Path) -> bool:
        return Path(self._abs(path)).is_dir()

    def read_bytes(self, path: Path) -> bytes:
        return Path(self._abs(path)).read_bytes()

    def write_bytes(self, path: Path, data: bytes) -> None:
        path = Path(self._abs(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def symlink(self, target: Path, link: Path) -> None:
        target = Path(self._abs(target))
        link = Path(self._abs(link))
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)

    def readlink(self, link: Path) -> Path:
        return Path(os.readlink(self._abs(link)))

    def walk(self, path: Path) -> list[str]:
        path = Path(path)
        if not path.is_dir():
            return []
        # Build-relative POSIX paths of every regular file under ``path``,
        # sorted — the deterministic order discovery relies on.
        return [
            rel.relative_to(path).as_posix()
            for rel in sorted(path.rglob("*"))
            if rel.is_file()
        ]


@dataclass
class MemoryRunner:
    """In-memory runner for tests: records every call, no real side effects.

    ``results`` maps a command-args prefix (joined by spaces) to the
    :class:`RunResult` to return, so a test can pre-seed what ``pengyos-sign``
    (or any external tool) would report. :meth:`run_command` returns the first
    seeded result whose prefix matches the start of the command; unmatched
    commands fail closed (rc=127, "command not found") rather than invoking
    anything.

    The virtual build tree is held in ``files`` (path -> bytes), ``dirs`` (a set
    of directory paths) and ``symlinks`` (link -> target). ``exists`` and
    ``read_bytes`` follow symlinks, so a test can model the SBOM normalisation
    the seal performs.
    """

    results: dict[str, RunResult] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    dirs: set[str] = field(default_factory=set)
    symlinks: dict[str, Path] = field(default_factory=dict)
    written: list[tuple[str, bytes]] = field(default_factory=list)
    links_created: list[tuple[str, str]] = field(default_factory=list)
    commands: list[tuple[str, ...]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The build dir itself always exists in the virtual tree.
        self.dirs.add(".")

    def run_command(self, args: Sequence[str]) -> RunResult:
        cmd = tuple(str(a) for a in args)
        self.commands.append(cmd)
        needle = " ".join(cmd)
        for prefix, result in self.results.items():
            if needle.startswith(prefix):
                return result
        return RunResult(args=cmd, rc=127, stderr="command not found")

    def exists(self, path: Path) -> bool:
        p = str(path)
        if p in self.symlinks:
            return True
        return p in self.files or p in self.dirs

    def is_dir(self, path: Path) -> bool:
        p = str(path)
        return p in self.dirs

    def read_bytes(self, path: Path) -> bytes:
        p = str(path)
        if p in self.symlinks:
            return self.read_bytes(self.symlinks[p])
        if p not in self.files:
            raise FileNotFoundError(p)
        return self.files[p]

    def write_bytes(self, path: Path, data: bytes) -> None:
        p = str(path)
        self.files[p] = data
        self.written.append((p, data))

    def symlink(self, target: Path, link: Path) -> None:
        self.symlinks[str(link)] = target
        self.links_created.append((str(link), str(target)))

    def readlink(self, link: Path) -> Path:
        p = str(link)
        if p not in self.symlinks:
            raise NotADirectoryError(p)
        return self.symlinks[p]

    def walk(self, path: Path) -> list[str]:
        root = str(path).rstrip("/")
        if root in ("", "."):
            root, prefix = "", ""
        else:
            prefix = root + "/"
        # Build-relative POSIX paths of the virtual files under ``path``, sorted.
        rel = [
            key[len(prefix):] if prefix else key
            for key in self.files
            if key != root and key.startswith(prefix)
        ]
        return sorted(rel)

    # ---- query helpers -----------------------------------------------------

    @property
    def written_paths(self) -> list[str]:
        return [p for p, _ in self.written]

    @property
    def written_by_name(self) -> dict[str, bytes]:
        """The written files keyed by their final path component."""
        return {Path(p).name: data for p, data in self.written}

    @property
    def signed(self) -> list[str]:
        """The signature paths the seal wrote (``*.sig``), in write order."""
        return [p for p in self.written_paths if p.endswith(".sig")]

    def seed_file(self, path: Path, data: bytes) -> None:
        """Pre-seed a file the seal will read back through ``read_bytes``."""
        self.files[str(path)] = data

    def seed_dir(self, path: Path) -> None:
        self.dirs.add(str(path))

    def seed_symlink(self, target: Path, link: Path) -> None:
        """Pre-seed a symlink the seal will read back through ``read_bytes``."""
        self.symlinks[str(link)] = target


__all__ = [
    "RunResult",
    "Runner",
    "FileSystemRunner",
    "MemoryRunner",
]
