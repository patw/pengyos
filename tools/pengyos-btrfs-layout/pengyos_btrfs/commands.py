"""btrfs command generation for create / snapshot / rollback.

This module is *pure*: it turns a validated :class:`~pengyos_btrfs.layout.Layout`
into an immutable :class:`Plan` — an ordered sequence of :class:`Step`s, each
carrying a human ``description`` and the exact ``btrfs`` ``argv`` a provisioner
would run. Nothing here shells out. The actual ``btrfs`` invocations go through
the mockable :class:`BtrfsRunner` seam (see below), so the argv is
golden-testable without root or a real btrfs volume.

Command shapes
--------------
Every command addresses subvolumes by absolute path relative to a staging
*mount point* (default :data:`pengyos_btrfs.constants.DEFAULT_MOUNT_POINT`,
``/mnt``): the filesystem is ``mkfs``'d and mounted there, subvolumes are
created under it, and the live mount points (``/``, ``/home``, ``/var``) are
wired up afterwards. See ``spec.md`` — "Command generation".

- **create**: one ``btrfs subvolume create <mount><path>`` per subvolume, in
  layout order. The top-level ``@`` is created explicitly — ``mkfs.btrfs``
  only creates the anonymous default subvolume, so ``@`` must be made by hand.
- **snapshot <name>**: for every subvolume *except* ``@snapshots``, one
  ``btrfs subvolume snapshot -r <mount><path> <mount>/@snapshots/<name>/<name>``.
  The whole system is captured so a rollback can restore all of it.
- **rollback <name>**: for every snapshotted subvolume, a *promote-then-swap*
  triplet. The snapshot lives under ``@snapshots/<name>`` — a *different parent*
  than the live subvolume — and ``btrfs subvolume rename`` only works within a
  single parent, so the snapshot is first re-snapshotted to a top-level sibling
  ``<live>.rollback`` (read-write, so it is a valid stand-in for the live
  subvolume), the live subvolume is deleted, and the sibling is renamed into
  place::

      btrfs subvolume snapshot  <mount>/@snapshots/<name>/<sv>  <mount>/<sv>.rollback
      btrfs subvolume delete    <mount>/<sv>
      btrfs subvolume rename    <mount>/<sv>.rollback           <mount>/<sv>

Rollback is **destructive**: the safe-by-default confirmation gate (refusing to
emit/run it without an explicit ``--yes``) lives in :mod:`.confirm`.

Pure standard library; no network, no root, no real ``btrfs`` in tests.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Optional, Sequence

from . import constants as c
from .errors import BtrfsCommandError, SnapshotError
from .layout import Layout

# A snapshot name is a single directory component under @snapshots: non-empty,
# no '/', no path separators, and only letters/digits/'.', '_' or '-'.
_SNAPSHOT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

__all__ = [
    "Step",
    "Plan",
    "RunResult",
    "BtrfsRunner",
    "RealRunner",
    "MockRunner",
    "create_commands",
    "snapshot_commands",
    "rollback_commands",
    "execute_plan",
]


# --- Plan / Step data model ---------------------------------------------
@dataclass(frozen=True)
class Step:
    """One executable step of a plan.

    Attributes:
        description: a one-line human description of what the step does.
        argv: the exact command to run (a ``tuple`` of strings, e.g.
            ``("btrfs", "subvolume", "create", "/mnt/@")``).
    """

    description: str
    argv: tuple  # tuple[str, ...]

    def as_list(self) -> "list":
        """The ``argv`` as a fresh mutable ``list`` (for golden tests / exec)."""
        return list(self.argv)


@dataclass(frozen=True)
class Plan:
    """An ordered, immutable sequence of :class:`Step`s.

    Attributes:
        kind: one of ``"create"``, ``"snapshot"`` or ``"rollback"``.
        steps: the ordered :class:`Step`s to run.
    """

    kind: str
    steps: tuple  # tuple[Step, ...]

    def argvs(self) -> "list":
        """All the steps' ``argv`` as a list of lists (golden-testable)."""
        return [list(s.argv) for s in self.steps]

    def to_dict(self) -> "dict":
        """Plain-dict form for golden tests and (later) ``--json`` output."""
        return {
            "kind": self.kind,
            "steps": [
                {"description": s.description, "argv": list(s.argv)}
                for s in self.steps
            ],
        }


# --- Path / name helpers -------------------------------------------------
def _validate_mount_point(mount_point: str) -> None:
    if not isinstance(mount_point, str) or not mount_point.strip():
        raise SnapshotError("mount point must be a non-empty string")
    if not mount_point.startswith("/"):
        raise SnapshotError(
            f"mount point must be an absolute path, got {mount_point!r}"
        )


def _validate_snapshot_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise SnapshotError("snapshot name must be a non-empty string")
    if name in (".", ".."):
        raise SnapshotError(f"invalid snapshot name {name!r}")
    if "/" in name:
        raise SnapshotError(
            f"snapshot name must not contain '/': {name!r}"
        )
    if not _SNAPSHOT_NAME_RE.match(name):
        raise SnapshotError(
            f"invalid snapshot name {name!r} — allowed characters are letters, "
            "digits, '.', '_' or '-'"
        )


def _on_disk(mount_point: str, relpath: str) -> str:
    """Absolute on-disk path for a layout-relative subvolume *relpath*."""
    base = mount_point.rstrip("/")
    rel = relpath.lstrip("/")
    return f"{base}/{rel}"


# --- Generators ----------------------------------------------------------
def create_commands(
    layout: Layout, mount_point: str = c.DEFAULT_MOUNT_POINT
) -> Plan:
    """Generate the commands to create every subvolume of *layout*.

    One ``btrfs subvolume create`` per subvolume, in layout order (the
    top-level ``@`` included — see module docstring).
    """
    _validate_mount_point(mount_point)
    steps = []
    for s in layout.subvolumes:
        target = _on_disk(mount_point, s.path)
        steps.append(
            Step(
                description=f"create subvolume {s.name!r} at {target}",
                argv=(c.BTRFS_BIN, "subvolume", "create", target),
            )
        )
    return Plan("create", tuple(steps))


def snapshot_commands(
    layout: Layout, name: str, mount_point: str = c.DEFAULT_MOUNT_POINT
) -> Plan:
    """Generate the commands to create the read-only snapshot *name*.

    Every subvolume except the snapshot directory itself is snapshotted into
    ``@snapshots/<name>/<subvolume>`` so a rollback can restore the whole
    system.
    """
    _validate_mount_point(mount_point)
    _validate_snapshot_name(name)
    snap = layout.snapshot_subvolume()
    dest_root = _on_disk(mount_point, snap.path) + "/" + name
    steps = []
    for s in layout.subvolumes:
        if s.name == snap.name:
            continue
        src = _on_disk(mount_point, s.path)
        dest = f"{dest_root}/{s.name}"
        steps.append(
            Step(
                description=f"snapshot {s.name!r} -> {dest}",
                argv=(
                    c.BTRFS_BIN,
                    "subvolume",
                    "snapshot",
                    "-r",
                    src,
                    dest,
                ),
            )
        )
    return Plan("snapshot", tuple(steps))


def rollback_commands(
    layout: Layout, name: str, mount_point: str = c.DEFAULT_MOUNT_POINT
) -> Plan:
    """Generate the destructive commands to roll back to snapshot *name*.

    For each snapshotted subvolume the snapshot is promoted to a top-level
    read-write sibling ``<live>.rollback``, the live subvolume is deleted, and
    the sibling is renamed into place (see module docstring for why the
    promote step is needed).
    """
    _validate_mount_point(mount_point)
    _validate_snapshot_name(name)
    snap = layout.snapshot_subvolume()
    src_root = _on_disk(mount_point, snap.path) + "/" + name
    steps = []
    for s in layout.subvolumes:
        if s.name == snap.name:
            continue
        live = _on_disk(mount_point, s.path)
        snap_src = f"{src_root}/{s.name}"
        temp = f"{live}.rollback"
        steps.append(
            Step(
                description=(
                    f"promote snapshot {name!r} of {s.name!r} to {temp}"
                ),
                argv=(c.BTRFS_BIN, "subvolume", "snapshot", snap_src, temp),
            )
        )
        steps.append(
            Step(
                description=f"delete live subvolume {s.name!r} at {live}",
                argv=(c.BTRFS_BIN, "subvolume", "delete", live),
            )
        )
        steps.append(
            Step(
                description=f"rename {temp} -> {live}",
                argv=(c.BTRFS_BIN, "subvolume", "rename", temp, live),
            )
        )
    return Plan("rollback", tuple(steps))


# --- The mockable runner seam -------------------------------------------
@dataclass(frozen=True)
class RunResult:
    """The outcome of running one ``btrfs`` ``argv``."""

    argv: tuple  # tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class BtrfsRunner:
    """Interface for executing a ``btrfs`` ``argv``.

    Tests substitute a mock (see :class:`MockRunner`); real (opt-in)
    integration runs use :class:`RealRunner`.
    """

    def run(self, argv: Sequence[str]) -> RunResult:  # pragma: no cover - abstract
        raise NotImplementedError("run() must be implemented by a subclass")


class RealRunner(BtrfsRunner):
    """Execute ``btrfs`` for real via :mod:`subprocess`.

    Only used by opt-in integration tests (``PENGYOS_IT=1``) that run against
    a real, root-owned btrfs volume. Never used by the unit suite.
    """

    def run(self, argv: Sequence[str]) -> RunResult:
        proc = subprocess.run(list(argv), capture_output=True, text=True)
        return RunResult(
            tuple(argv), proc.returncode, proc.stdout, proc.stderr
        )


class MockRunner(BtrfsRunner):
    """In-memory runner that records every ``argv`` and reports success.

    By default every command "succeeds" (return code 0). Configure
    *fail_on* (an iterable of ``argv`` tuples that must exit non-zero) or
    *results* (a mapping of ``argv`` tuple -> :class:`RunResult`) to simulate
    specific failures.
    """

    def __init__(self, results=None, fail_on=None):
        self.calls = []
        self._results = {tuple(k): v for k, v in (results or {}).items()}
        self._fail_on = {tuple(a) for a in (fail_on or ())}

    def run(self, argv: Sequence[str]) -> RunResult:
        key = tuple(argv)
        self.calls.append(key)
        if key in self._fail_on:
            return RunResult(key, 1, "", "mock failure (configured)")
        if key in self._results:
            return self._results[key]
        return RunResult(key, 0, "", "")


def execute_plan(runner: BtrfsRunner, plan: Plan) -> "list":
    """Run every step of *plan* through *runner*, in order.

    Stops at the first non-zero exit, raising :class:`BtrfsCommandError` (the
    failing ``argv``/exit/output are attached to the exception). Returns the
    list of :class:`RunResult`s on success.
    """
    results = []
    for step in plan.steps:
        result = runner.run(step.argv)
        if result.returncode != 0:
            raise BtrfsCommandError(
                f"command failed (exit {result.returncode}): "
                f"{' '.join(step.argv)}"
                + (f"\n{result.stderr.strip()}" if result.stderr.strip() else ""),
                argv=step.argv,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        results.append(result)
    return results
