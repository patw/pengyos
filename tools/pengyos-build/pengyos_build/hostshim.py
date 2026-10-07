"""Host-tooling hygiene: prefer GNU coreutils over uutils via a PATH shim.

Buildroot needs **GNU** coreutils semantics, but some build hosts ship
uutils/coreutils as the default ``/usr/bin/<tool>`` with the GNU versions
installed alongside as ``/usr/bin/gnu<tool>`` (Debian's
``coreutils-from-uutils`` scheme).  uutils ``install`` trips Buildroot's
host-tool dependency check (uutils/coreutils#12166), so a Buildroot build
aborts before it has even started.

The fix needs no root: for every coreutils tool whose *default* resolution
is a uutils binary, symlink the GNU ``gnu<tool>`` binary over ``<tool>``
into a private directory and **prepend** that directory to ``PATH``.  Tools
that already resolve to a genuine GNU binary are left alone — we only
shim what is actually uutils, so a host with stock GNU coreutils produces
no shims at all.

Everything here is pure Python and hermetically testable:

* :func:`discover_gnu_binaries` lists candidate ``gnu*`` binaries from a
  directory (default ``/usr/bin``);
* a *resolver* maps a bare tool name to the absolute path of the binary the
  build host currently runs for it (default: ``shutil.which`` +
  ``os.path.realpath``, i.e. the shell's ``command -v`` + ``readlink -f``);
* :func:`plan_host_shims` decides, from those two facts, which tools need a
  shim and where the GNU binary lives — **no filesystem writes**;
* :func:`apply_host_shims` materialises the plan as idempotent symlinks.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

#: Directory scanned for candidate ``gnu*`` binaries (Debian installs the
#: GNU coreutils as ``/usr/bin/gnu*``).
DEFAULT_GNU_DIR = "/usr/bin"

#: Prefix used by Debian's ``coreutils-from-uutils`` scheme for GNU tools.
GNU_PREFIX = "gnu"


def is_uutils(resolved: str) -> bool:
    """Return ``True`` if ``resolved`` is a uutils/coreutils binary.

    Faithful port of the shell guard ``case "$real" in */coreutils/*)``: a
    uutils install places each tool under a ``coreutils`` directory
    (e.g. ``/usr/lib/coreutils/install``), so we match a path with a
    ``coreutils`` *directory* component that still contains a tool below it.
    A path whose final component merely *is* ``coreutils`` does not match.
    """
    normalized = str(resolved).replace(os.sep, "/") if os.sep != "/" else str(resolved)
    return fnmatch.fnmatch(normalized, "*/coreutils/*")


def discover_gnu_binaries(directory: Path | str = DEFAULT_GNU_DIR) -> tuple[Path, ...]:
    """Return the executable ``gnu*`` files in ``directory``, sorted by name.

    Missing or non-directory ``directory`` yields an empty tuple (a host
    without the uutils scheme simply has nothing to shim).
    """
    directory = Path(directory)
    if not directory.is_dir():
        return ()
    found: list[Path] = []
    for entry in directory.iterdir():
        if not entry.name.startswith(GNU_PREFIX):
            continue
        if not entry.is_file():
            continue
        if not os.access(entry, os.X_OK):
            continue
        found.append(entry)
    return tuple(sorted(found, key=lambda p: p.name))


def default_resolver(name: str) -> str | None:
    """Resolve ``name`` the way the shell does: ``command -v`` + ``readlink -f``.

    Returns the fully-resolved absolute path of the binary currently found
    on ``PATH`` (symlinks followed), or ``None`` if it is not found.
    """
    found = shutil.which(name)
    if found is None:
        return None
    return os.path.realpath(found)


@dataclass(frozen=True)
class ShimPlan:
    """Which tools need a GNU-over-uutils shim, and where the GNU binary is.

    ``shims`` is a tuple of ``(tool_name, gnu_binary_path)`` sorted by tool
    name, so a plan is deterministic and comparable.  Empty when nothing on
    the host is a uutils coreutils binary.
    """

    shims: tuple[tuple[str, Path], ...] = ()

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(tool for tool, _ in self.shims)

    def __bool__(self) -> bool:
        return bool(self.shims)


def plan_host_shims(
    gnu_binaries: Sequence[Path | str],
    resolver: Callable[[str], str | None] = default_resolver,
) -> ShimPlan:
    """Decide which coreutils tools to shim so that GNU wins over uutils.

    For each candidate ``gnu<tool>`` binary, resolve the bare ``<tool>``
    name via ``resolver``; if it resolves to a uutils/coreutils binary,
    record a shim mapping ``<tool>`` to the GNU binary.  Non-``gnu``-prefixed
    paths and a bare ``gnu`` (no tool name) are ignored.  No filesystem
    writes are performed.
    """
    shims: dict[str, Path] = {}
    for gnu in gnu_binaries:
        gnu_path = Path(gnu)
        if not gnu_path.name.startswith(GNU_PREFIX):
            continue
        tool = gnu_path.name[len(GNU_PREFIX):]
        if not tool:
            continue
        resolved = resolver(tool)
        if resolved and is_uutils(resolved):
            shims[tool] = gnu_path
    return ShimPlan(shims=tuple(sorted(shims.items())))


def apply_host_shims(
    plan: ShimPlan, shim_dir: Path | str
) -> tuple[Path, tuple[Path, ...]]:
    """Materialise ``plan`` as symlinks in ``shim_dir`` (idempotent).

    Creates ``shim_dir/<tool> -> <gnu binary>`` for every planned tool,
    creating ``shim_dir`` if needed.  Re-applying a plan whose symlinks
    already point at the right target creates nothing; a stale symlink or
    a real file that would block the link is replaced (this is our own
    shim directory, mirroring the shell's ``ln -sf``).  Returns
    ``(shim_dir, created)`` where ``created`` lists only the links this
    call wrote (empty on an idempotent no-op).
    """
    shim_dir = Path(shim_dir)
    shim_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for tool, gnu in plan.shims:
        link = shim_dir / tool
        target = Path(gnu)
        if link.is_symlink():
            if os.path.realpath(str(link)) == os.path.realpath(str(target)):
                continue
            link.unlink()
        elif link.exists():
            if link.is_dir():
                raise FileExistsError(
                    f"{link} exists and is a directory; refusing to replace it"
                )
            link.unlink()
        link.symlink_to(target)
        created.append(link)
    return shim_dir, tuple(created)


def build_host_shims(
    shim_dir: Path | str,
    gnu_dir: Path | str = DEFAULT_GNU_DIR,
    resolver: Callable[[str], str | None] = default_resolver,
) -> tuple[Path, ShimPlan, tuple[Path, ...]]:
    """Discover, plan, and apply in one step.

    Returns ``(shim_dir, plan, created)``.  ``created`` is empty when no
    tools needed shimming (including when ``shim_dir`` was never created).
    """
    plan = plan_host_shims(discover_gnu_binaries(gnu_dir), resolver=resolver)
    if not plan:
        return Path(shim_dir), plan, ()
    shim_dir, created = apply_host_shims(plan, shim_dir)
    return shim_dir, plan, created


def shim_path(shim_dir: Path | str, existing_path: str) -> str:
    """Return ``PATH`` with ``shim_dir`` **prepended** (GNU wins).

    An empty ``existing_path`` yields just ``shim_dir``.
    """
    shim = str(shim_dir)
    if existing_path:
        return f"{shim}{os.pathsep}{existing_path}"
    return shim


def run_build_with_host_shim(
    argv: Sequence[str],
    shim_dir: Path | str,
    gnu_dir: Path | str = DEFAULT_GNU_DIR,
    resolver: Callable[[str], str | None] = default_resolver,
    env: dict[str, str] | None = None,
    runner: Callable[[Sequence[str], dict[str, str]], object] | None = None,
):
    """Run ``argv`` in a child environment with the GNU shim on ``PATH``.

    Builds the host shim into ``shim_dir`` (no-op when nothing is uutils),
    prepends it to ``env['PATH']`` (a copy — the caller's dict is not
    mutated), and runs ``argv``.  ``runner`` defaults to
    :func:`subprocess.run` with ``check=False``; it is injectable so the
    child environment can be unit-tested without spawning a process.
    """
    base_env = dict(os.environ if env is None else env)
    _, _, created = build_host_shims(shim_dir, gnu_dir, resolver)
    if created:
        base_env["PATH"] = shim_path(shim_dir, base_env.get("PATH", ""))
    runner = runner if runner is not None else _default_runner
    return runner(list(argv), base_env)


def _default_runner(argv: Sequence[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), env=env, check=False, text=True)
