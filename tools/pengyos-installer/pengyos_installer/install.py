"""Install execution: run an :class:`~pengyos_installer.plan.InstallPlan`.

The plan (see :mod:`pengyos_installer.plan`) is a *pure* value. This module is
the part that actually *acts* on the target, and it is the only place that
runs a command or writes a file. Everything external goes through one seam so
tests can stay hermetic:

* **Commands** (``parted``, ``mkfs.*``, ``mount``, ``rsync``/``tar``, ``blkid``,
  ``grub-install``, ``efibootmgr``, and the teardown ``umount``/``sync``) are
  run through an injected :data:`CommandRunner` — a callable
  ``runner(argv) -> CommandResult``. The default :class:`SubprocessRunner`
  shells out; the tests replace it with a *recording fake* so **no test ever
  touches a disk**.
* **File writes** (``/etc/fstab``, ``/etc/hostname``,
  ``/etc/pengyos/installed`` and the ESP ``EFI/BOOT/grub.cfg``) land under an
  injectable *work root* (default :data:`WORK_DIR`), the mount point of the
  freshly formatted root partition. Tests point it at a temporary directory.

The :func:`execute_install` function walks the plan in order:

* it refuses to run unless the target passed sanity (re-checked, per spec) and
  ``confirm`` (``--yes``) is true — refusing *before any command runs*;
* an ``argv`` step is run through the runner; a non-zero exit on a required
  step is a hard :class:`InstallError` (the install aborts);
* a step whose note starts with ``optional:`` (the ``efibootmgr`` NVRAM entry)
  is run, but a non-zero exit is *tolerated* — recorded as a warning, the
  install continues (the ``grub-install --removable`` fallback path already
  boots);
* the two ``blkid`` steps' stdout is parsed with
  :func:`~pengyos_installer.plan.parse_partuuid` (a missing/ambiguous
  PARTUUID is a hard error, no file is written);
* the internal ``configure`` steps perform the file writes via the
  ``render_*`` functions;
* on any failure the already-mounted partitions are unmounted (ESP first, then
  root) in a best-effort teardown before the error propagates; on success the
  teardown (``sync`` + unmounts + a summary) runs as the final step.
"""

from __future__ import annotations

import os
import shlex
import socket
import stat as _os_stat
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Sequence, Tuple

from pengyos_installer.plan import (
    COPY_SRC_KIND_DIR,
    COPY_SRC_KIND_TAR,
    ConfigError,
    ETC_FSTAB,
    ETC_HOSTNAME,
    ESP_MOUNTPOINT,
    FALLBACK_GRUB_CFG,
    INSTALL_MARKER,
    InstallPlan,
    Step,
    WORK_DIR,
    classify_rootfs_src,
    parse_partuuid,
    partition_device,
    render_fstab,
    render_grub_cfg,
    render_hostname,
    render_installed_marker,
)

#: Error codes raised by the execution layer (stable; golden-tested).
ERR_NOT_CONFIRMED = "not-confirmed"
ERR_SANITY_REFUSED = "sanity-refused"
ERR_STEP_FAILED = "step-failed"
ERR_UNMOUNT_FAILED = "umount-failed"
ERR_INTERNAL_STEP = "unknown-internal-step"
#: Aliased from the configure phase: a missing PARTUUID stops the file writes.
ERR_MISSING_PARTUUID = "missing-partuuid"
#: The ``--rootfs-src`` source did not exist when re-validated before copy.
ERR_ROOTFS_SRC_MISSING = "rootfs-src-missing"
#: The source's actual type contradicted the plan-time extension
#: classification (a "tarball" that is a directory, a "directory" that is a
#: regular file).
ERR_ROOTFS_SRC_TYPE_MISMATCH = "rootfs-src-type-mismatch"


@dataclass(frozen=True)
class CommandResult:
    """The outcome of running one command line through a runner."""

    argv: Tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


#: The injectable command runner: ``runner(argv) -> CommandResult``.
CommandRunner = Callable[[Sequence[str]], CommandResult]


class SubprocessRunner:
    """The real :data:`CommandRunner`: runs each argv via ``subprocess.run``.

    Captures stdout/stderr; a missing binary (``FileNotFoundError``) or other
    ``OSError`` is reported as a non-zero :class:`CommandResult` rather than an
    exception, so the executor's single "non-zero exit" error path handles it.
    """

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        argv = tuple(argv)
        try:
            proc = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            return CommandResult(argv, proc.returncode, proc.stdout, proc.stderr)
        except FileNotFoundError as exc:
            return CommandResult(argv, 127, "", f"command not found: {exc}")
        except OSError as exc:
            return CommandResult(argv, 1, "", str(exc))


@dataclass(frozen=True)
class SourceInfo:
    """The type of a copy-source path, from a single ``stat``.

    ``exists`` is false when the ``stat`` failed (no such path); ``is_dir`` /
    ``is_file`` are the ``S_ISDIR`` / ``S_ISREG`` flags (both false for a
    special file, and all false when the path does not exist). Symlinks are
    followed (a link to a directory reports ``is_dir=True``).
    """

    path: str
    exists: bool
    is_dir: bool
    is_file: bool


def default_source_inspector(path: str) -> SourceInfo:
    """The production source inspector: one ``os.stat`` on ``path``.

    Follows symlinks; a path that cannot be stat'ed (missing or an ``OSError``)
    reports ``exists=False``. This is the only ``stat`` the installer performs
    on the source, and it is injected into :func:`execute_install` so tests
    never touch the real filesystem.
    """
    try:
        st = os.stat(path)
    except OSError:
        return SourceInfo(path=path, exists=False, is_dir=False, is_file=False)
    return SourceInfo(
        path=path,
        exists=True,
        is_dir=_os_stat.S_ISDIR(st.st_mode),
        is_file=_os_stat.S_ISREG(st.st_mode),
    )


#: The injectable source inspector: ``inspect(path) -> SourceInfo``.
SourceInspector = Callable[[str], SourceInfo]


class InstallError(Exception):
    """A hard error of install execution (the install was not completed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def validate_rootfs_src(
    source: str,
    kind: str,
    *,
    inspect: SourceInspector = default_source_inspector,
) -> None:
    """Re-validate a copy source against its plan-time classification.

    ``source`` is the path to check (``/`` for the running root, or the
    ``--rootfs-src`` value); ``kind`` is the plan-time classification from
    :func:`~pengyos_installer.plan.classify_rootfs_src` (``COPY_SRC_KIND_DIR``
    or ``COPY_SRC_KIND_TAR``). The plan classifies the source *syntactically*
    (by name), so a misnamed path can slip through — this re-checks it against
    the real filesystem before the (destructive) copy step runs.

    Raises :class:`InstallError` with :data:`ERR_ROOTFS_SRC_MISSING` when the
    source does not exist, or :data:`ERR_ROOTFS_SRC_TYPE_MISMATCH` when its
    actual type contradicts the classification (a name that classified as a
    tarball but is a directory, or a directory-kind source that is not a
    directory). Returns ``None`` on success. The source ``inspect`` is
    injected so tests never ``stat`` a real path.
    """
    info = inspect(source)
    if not info.exists:
        raise InstallError(
            ERR_ROOTFS_SRC_MISSING,
            f"rootfs source {source} does not exist (stat failed); point "
            "--rootfs-src at an existing directory or tarball",
        )
    if kind == COPY_SRC_KIND_TAR and info.is_dir:
        raise InstallError(
            ERR_ROOTFS_SRC_TYPE_MISMATCH,
            f"rootfs source {source} was classified as a tarball by its name "
            "but is a directory; point --rootfs-src at the archive file",
        )
    if kind == COPY_SRC_KIND_DIR and not info.is_dir:
        raise InstallError(
            ERR_ROOTFS_SRC_TYPE_MISMATCH,
            f"rootfs source {source} was classified as a directory but is a "
            "regular file; point --rootfs-src at a directory, or name the "
            "tarball with a recognised .tar/.tar.gz/.tgz/.tar.xz/.txz/"
            ".tar.bz2/.tbz2 extension",
        )


@dataclass
class InstallResult:
    """What :func:`execute_install` did.

    ``executed`` is the ordered list of external command lines that were run
    (the plan's ``argv`` steps in order, then the teardown ``umount``/``sync``
    lines); ``files_written`` maps each written path (under the work root) to
    its exact content; ``mounts`` / ``unmounts`` record the mount points (the
    unmounts in the order they were torn down); ``warnings`` collects the
    tolerated optional-step failures and best-effort unmount failures.
    """

    target: str
    ok: bool
    executed: list = field(default_factory=list)
    files_written: dict = field(default_factory=dict)
    mounts: list = field(default_factory=list)
    unmounts: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    summary: str = ""


def _default_write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _is_optional(step: Step) -> bool:
    return step.note is not None and step.note.startswith("optional:")


def _is_mount_step(step: Step) -> bool:
    return step.argv is not None and step.argv[0] == "mount"


def execute_install(
    plan: InstallPlan,
    runner: CommandRunner,
    *,
    confirm: bool = True,
    hostname_provider: Callable[[], str] = socket.gethostname,
    work_root: str = WORK_DIR,
    write_file: Callable[[str, str], None] | None = None,
    source_inspector: SourceInspector = default_source_inspector,
) -> InstallResult:
    """Execute ``plan`` (the ordered install plan for a target device).

    Runs every planned command through ``runner`` and performs the internal
    file writes under ``work_root``. Refuses (before running anything) when
    ``confirm`` is false or the target failed sanity, and re-validates the copy
    source (:func:`validate_rootfs_src`) before the copy step — aborting before
    any destructive step if it is missing or its type contradicts the plan-time
    classification. On success the mounted partitions are torn down (``umount``
    ESP then root, then ``sync``) and an :class:`InstallResult` with
    ``ok=True`` is returned. Any failure aborts the install, tears down what
    was mounted (best effort), and raises :class:`InstallError` (a
    :class:`~pengyos_installer.plan.ConfigError` from a malformed PARTUUID
    propagates as-is).

    ``source_inspector`` is the seam for the source ``stat`` (default
    :func:`default_source_inspector`); tests inject a fake so they never touch
    the real filesystem.
    """
    write_file = write_file if write_file is not None else _default_write_file
    result = InstallResult(target=plan.target, ok=False)

    if not confirm:
        raise InstallError(
            ERR_NOT_CONFIRMED,
            "install not confirmed: refusing to run without --yes",
        )
    if not plan.sanity.ok:
        raise InstallError(
            ERR_SANITY_REFUSED,
            "target failed sanity; refusing to install: "
            + "; ".join(f"[{e.code}] {e.message}" for e in plan.sanity.errors),
        )

    # Re-validate the copy source before any destructive step touches the
    # target: the plan classified it syntactically (by name), so confirm it
    # actually exists and its type matches, or abort before parted/mkfs/mount.
    source_path = plan.rootfs_src if plan.rootfs_src is not None else "/"
    validate_rootfs_src(
        source_path, classify_rootfs_src(plan.rootfs_src),
        inspect=source_inspector,
    )

    target = plan.target
    # The partition device names must match the plan's steps exactly: plain
    # ``<target>1`` / ``<target>2`` for most disks, ``<target>p1`` /
    # ``<target>p2`` for NVMe/loop-style names (see plan.partition_device).
    esp_part = partition_device(target, 1)
    root_part = partition_device(target, 2)
    root_mount = work_root
    esp_mount = work_root + ESP_MOUNTPOINT

    esp_partuuid: "str | None" = None
    root_partuuid: "str | None" = None
    mounted: list[str] = []

    def unmount(point: str, *, best_effort: bool) -> None:
        r = runner(("umount", point))
        result.executed.append(r.argv)
        result.unmounts.append(point)
        if not r.ok:
            msg = f"umount {point} failed (exit {r.returncode}): {r.stderr.strip()}"
            if best_effort:
                result.warnings.append(msg)
            else:
                raise InstallError(ERR_UNMOUNT_FAILED, msg)

    def teardown(*, best_effort: bool) -> None:
        # Unmount in reverse mount order: the ESP (mounted second) comes off
        # before the root partition (mounted first).
        for point in reversed(mounted):
            unmount(point, best_effort=best_effort)
        sync = runner(("sync",))
        result.executed.append(sync.argv)

    def planned_hostname() -> str:
        return plan.hostname if plan.hostname is not None else hostname_provider()

    def do_internal(step: Step) -> None:
        desc = step.description
        if step.phase == "sanity":
            # Performed by check_target() before any step runs; nothing to do.
            return
        if step.phase == "teardown":
            # The actual sync/unmount/summary is performed by teardown() at
            # the end of execution, not here.
            return
        if desc.startswith(f"write {ETC_FSTAB}"):
            if esp_partuuid is None or root_partuuid is None:
                raise InstallError(
                    ERR_MISSING_PARTUUID,
                    "cannot write /etc/fstab: the PARTUUIDs from the blkid "
                    "steps are not available",
                )
            content = render_fstab(root_partuuid, esp_partuuid)
            write_file(root_mount + ETC_FSTAB, content)
            result.files_written[root_mount + ETC_FSTAB] = content
        elif desc.startswith(f"write {ETC_HOSTNAME}"):
            content = render_hostname(planned_hostname())
            write_file(root_mount + ETC_HOSTNAME, content)
            result.files_written[root_mount + ETC_HOSTNAME] = content
        elif desc.startswith(f"write the install marker {INSTALL_MARKER}"):
            content = render_installed_marker(planned_hostname(), target)
            write_file(root_mount + INSTALL_MARKER, content)
            result.files_written[root_mount + INSTALL_MARKER] = content
        elif desc.startswith(f"write the fallback GRUB config {ESP_MOUNTPOINT}/{FALLBACK_GRUB_CFG}"):
            if root_partuuid is None:
                raise InstallError(
                    ERR_MISSING_PARTUUID,
                    "cannot write the fallback GRUB config: the root PARTUUID "
                    "from the blkid step is not available",
                )
            content = render_grub_cfg(root_partuuid)
            write_file(esp_mount + "/" + FALLBACK_GRUB_CFG, content)
            result.files_written[esp_mount + "/" + FALLBACK_GRUB_CFG] = content
        else:
            raise InstallError(ERR_INTERNAL_STEP, f"unrecognised internal step: {desc!r}")

    try:
        for step in plan.steps:
            if step.argv is None:
                do_internal(step)
                continue

            r = runner(step.argv)
            result.executed.append(r.argv)
            if not r.ok:
                if _is_optional(step):
                    result.warnings.append(
                        f"optional step failed (continuing): "
                        f"{shlex.join(step.argv)} (exit {r.returncode}): "
                        f"{r.stderr.strip()}"
                    )
                    continue
                raise InstallError(
                    ERR_STEP_FAILED,
                    f"step failed: {shlex.join(step.argv)} (exit {r.returncode}): "
                    f"{r.stderr.strip()}",
                )

            if _is_mount_step(step):
                mounted.append(step.argv[-1])
                result.mounts.append(step.argv[-1])

            if step.argv[0] == "blkid":
                device = step.argv[-1]
                uuid = parse_partuuid(r.stdout, device)
                if device == esp_part:
                    esp_partuuid = uuid
                elif device == root_part:
                    root_partuuid = uuid

        # Teardown on the success path: a failed unmount here is a real error.
        teardown(best_effort=False)
        result.ok = True
        result.summary = _summary(plan, result, root_partuuid, esp_partuuid)
        return result
    except (InstallError, ConfigError):
        # Best-effort teardown so we never leave partitions mounted, then let
        # the original error propagate to the caller.
        teardown(best_effort=True)
        raise


def _summary(plan: InstallPlan, result: InstallResult,
             root_partuuid, esp_partuuid) -> str:
    lines = [
        "PengyOS install complete",
        f"  target:   {plan.target}",
        f"  hostname: {plan.hostname if plan.hostname is not None else '(live)'}",
        f"  root:     PARTUUID={root_partuuid}",
        f"  esp:      PARTUUID={esp_partuuid}",
    ]
    for w in result.warnings:
        lines.append(f"  warning:  {w}")
    return "\n".join(lines)
