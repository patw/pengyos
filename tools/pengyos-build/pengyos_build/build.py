"""Build a PengyOS target against a configured Buildroot tree, and report
the build's stage by scanning its log.

The build step is the second make invocation of a Buildroot build.  Once a
target has been configured (see :mod:`pengyos_build.configure`), building is
just a plain recursive build in the same per-target ``O=`` output dir::

    make -C <source> O=<output> BR2_EXTERNAL=<tree>[ BR2_BOARD=<board>] -j<jobs>

The defconfig is deliberately NOT re-passed: buildroot's ``make O=…`` builds
the already-configured ``.config`` without re-running kconfig, so omitting
the defconfig keeps the build from silently re-configuring.  ``BR2_EXTERNAL``
(and ``BR2_BOARD``) *are* re-passed for symmetry with configure and so a
build never depends on ambient state.

* ``-j<jobs>`` defaults to the host core count (``-j$(nproc)``) and is
  configurable via the CLI ``--jobs`` flag (lower it on hosts where Buildroot's
  parallel GCC races, e.g. 8).
* make's stdout and stderr are **streamed, merged and in order**, straight
  into ``<out>/logs/build.log`` (never buffered in memory): the child writes
  to the log file handle line-by-line, so the log grows as the build runs and
  the ``status`` subcommand can report the current stage by scanning it —
  even mid-build.  The file is truncated on every run, never appended.
* the process runner is injectable (a ``subprocess.run``-like callable), so
  unit tests run hermetically with a fake make.

Design constraints (spec):

* build **never touches the network** and never auto-acquires or auto-
  configures: a missing source tree is a :class:`BuildError` suggesting
  ``pengyos-build acquire <target>``; a target that has not been configured
  is a :class:`BuildError` suggesting ``pengyos-build configure <target>``;
* the child environment goes through
  :func:`pengyos_build.hostshim.run_build_with_host_shim` so Buildroot sees
  GNU coreutils even on a uutils host (SPEC §7 gotcha).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import hostshim
from .acquire import default_build_root, source_dir
from .configure import HOSTSHIM_DIRNAME, LOGS_SUBDIR, output_dir
from .targets import Target

#: Name of the build log (lives next to ``configure.log`` in the output dir).
BUILD_LOG = "build.log"


class BuildError(RuntimeError):
    """The build step failed (not configured, missing tree, make error)."""


@dataclass(frozen=True)
class BuildResult:
    """Outcome of a successful build step.

    ``argv`` is the exact make invocation executed (the seed for the later
    ``--dry-run``); ``log`` is where make's merged stdout/stderr was streamed;
    ``jobs`` is the resolved parallelism.
    """

    target: str
    source: Path
    output: Path
    log: Path
    jobs: int
    argv: tuple[str, ...]

    def __str__(self) -> str:  # convenient one-line summary for the CLI
        return f"built {self.target} ({self.jobs} jobs): log {self.log}"


#: A ``subprocess.run``-like callable: ``runner(argv, env=…, stdout=…,
#: stderr=…, text=…) -> CompletedProcess``.  Injectable so tests can fake
#: make without spawning a process.
Spawner = Callable[..., subprocess.CompletedProcess]


def default_jobs() -> int:
    """Parallelism for a build with no explicit ``--jobs``: the host core
    count, i.e. ``-j$(nproc)`` (clamped to at least 1)."""
    return os.cpu_count() or 1


def build_argv(
    source: Path | str,
    output: Path | str,
    target: Target,
    jobs: int,
) -> tuple[str, ...]:
    """Exact make argv for the build step.

    ``BR2_BOARD`` is included only when the target defines a board; the
    defconfig is NOT re-passed (buildroot builds the configured ``.config``).
    ``jobs`` must be a positive integer.
    """
    argv = [
        "make",
        "-C",
        str(source),
        f"O={output}",
        f"BR2_EXTERNAL={target.external_tree}",
    ]
    if target.board:
        argv.append(f"BR2_BOARD={target.board}")
    argv.append(f"-j{jobs}")
    return tuple(argv)


def build_target(
    target: Target,
    build_root: Path | str | None = None,
    *,
    jobs: int | None = None,
    env: dict[str, str] | None = None,
    gnu_dir: Path | str = hostshim.DEFAULT_GNU_DIR,
    resolver: Callable[[str], str | None] = hostshim.default_resolver,
    runner: Spawner | None = None,
    dry_run: bool = False,
) -> BuildResult:
    """Run the build make invocation for ``target``.

    The Buildroot source tree must already be acquired and the target already
    configured; a missing source tree is a :class:`BuildError` suggesting
    ``pengyos-build acquire``, and an unconfigured target (no output dir) is a
    :class:`BuildError` suggesting ``pengyos-build configure``.  ``jobs``
    defaults to :func:`default_jobs` when ``None``.  Raises
    :class:`BuildError` if make is not found or exits non-zero (the message
    names the exit code and the captured log).

    With ``dry_run=True`` the step resolves the exact make invocation (with
    the resolved ``-j<jobs>``) and returns it WITHOUT running make: the same
    precondition checks apply, but no log is written and no host shim is
    applied.
    """
    env = os.environ if env is None else env
    root = Path(build_root) if build_root is not None else default_build_root(env)
    resolved_jobs = default_jobs() if jobs is None else jobs
    if resolved_jobs < 1:
        raise BuildError(f"--jobs must be a positive integer, got {resolved_jobs}")

    source = source_dir(root, target.br_version)
    if not source.is_dir():
        raise BuildError(
            f"Buildroot source tree not found at {source}; "
            f"run 'pengyos-build acquire {target.name}' first"
        )

    output = output_dir(root, target)
    if not output.is_dir():
        raise BuildError(
            f"target '{target.name}' is not configured yet (no output dir at "
            f"{output}); run 'pengyos-build configure {target.name}' first"
        )

    argv = build_argv(source, output, target, resolved_jobs)
    log = output / LOGS_SUBDIR / BUILD_LOG
    if dry_run:
        # Print-only: no make, no log, no host shim.
        return BuildResult(
            target=target.name,
            source=source,
            output=output,
            log=log,
            jobs=resolved_jobs,
            argv=argv,
        )
    shim_dir = output / HOSTSHIM_DIRNAME
    spawner = runner if runner is not None else _default_runner

    def _capturing(argv_: Sequence[str], env_: dict[str, str]) -> None:
        """Hostshim runner: stream make's stdout+stderr, merged, into ``log``."""
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as log_file:
            try:
                proc = spawner(
                    list(argv_),
                    env=env_,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except FileNotFoundError as exc:
                raise BuildError(
                    f"make not found on PATH "
                    f"({getattr(exc, 'filename', '') or 'make'}); "
                    "install build-essential"
                ) from exc
        if proc.returncode != 0:
            raise BuildError(
                f"build failed (make exited {proc.returncode}); see log: {log}"
            )

    try:
        hostshim.run_build_with_host_shim(
            argv,
            shim_dir,
            gnu_dir=gnu_dir,
            resolver=resolver,
            env=env,
            runner=_capturing,
        )
    except BuildError:
        raise
    except FileNotFoundError as exc:
        raise BuildError(
            f"make not found on PATH ({getattr(exc, 'filename', '') or 'make'}); "
            "install build-essential"
        ) from exc

    return BuildResult(
        target=target.name,
        source=source,
        output=output,
        log=log,
        jobs=resolved_jobs,
        argv=argv,
    )


def _default_runner(argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), check=False, **kwargs)


# ---------------------------------------------------------------------------
# status: report the build stage by scanning the build log
# ---------------------------------------------------------------------------

#: The ordered build stages a scan can detect (furthest marker wins).
STAGE_ORDER: tuple[str, ...] = ("fetching", "building", "images", "done")

#: Stage names that mean "no build activity seen in the log yet".
NOT_STARTED = "not_started"
#: Stage names that mean the build hit an error (build aborted).
ERROR = "error"

# Per-stage markers, matched case-insensitively against the whole log.  The
# stage reported is the *furthest* one whose marker appears (a build that has
# reached "images" is not still "fetching").  Markers are chosen against real
# Buildroot log lines (see tests/test_build.py sample logs).
_STAGE_MARKERS: dict[str, tuple[re.Pattern[str], ...]] = {
    "fetching": (
        re.compile(r"\bdownload", re.IGNORECASE),
        re.compile(r"\bfetching\b", re.IGNORECASE),
    ),
    "building": (
        re.compile(r"\bbuilding\b", re.IGNORECASE),
        re.compile(r"\bcompiling\b", re.IGNORECASE),
    ),
    "images": (
        re.compile(r"generating rootfs", re.IGNORECASE),
        re.compile(r"rootfs\.[\w.-]+\.(?:img|ext4|tar\.xz|squashfs)", re.IGNORECASE),
        re.compile(r"\bimages\b", re.IGNORECASE),
    ),
    "done": (
        re.compile(r"build (?:done|completed|finished|succeeded)", re.IGNORECASE),
    ),
}

# Error markers: a make failure prints a line like
# ``make[2]: *** [host-foo 1.0: build] Error 1`` (the ``***`` is NOT at the
# start of the line), and compilers print ``cc1: error: ...`` diagnostics.
# Either one means the make run aborted, so it wins over the furthest stage —
# a build that aborted is in the error state regardless of how far it got.
_ERROR_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\*\*\* \[.*\] Error"),
    re.compile(r"(?i)\berror:\s"),
)


def detect_stage(log_text: str | None) -> str:
    """Classify a build log into one of the :data:`STAGE_ORDER` stages.

    An empty/absent log is :data:`NOT_STARTED`.  An error marker anywhere in
    the log means :data:`ERROR` (the make run aborted) — this takes
    precedence over the furthest stage.  Otherwise the furthest stage whose
    marker appears in the log is returned (log lines are ordered, so the last
    stage reached is the current one).
    """
    if not log_text:
        return NOT_STARTED
    if any(marker.search(log_text) for marker in _ERROR_MARKERS):
        return ERROR
    stage = NOT_STARTED
    for name in STAGE_ORDER:
        if any(marker.search(log_text) for marker in _STAGE_MARKERS[name]):
            stage = name
    return stage


@dataclass(frozen=True)
class StatusReport:
    """Result of a ``status`` query: the target, its detected stage, and the
    build log that was scanned."""

    target: str
    stage: str
    log: Path

    def __str__(self) -> str:  # one line: ``<target>: <stage>``
        return f"{self.target}: {self.stage}"


def build_log_path(build_root: Path | str, target: Target) -> Path:
    """Path of the build log for ``target`` under ``build_root``."""
    return output_dir(build_root, target) / LOGS_SUBDIR / BUILD_LOG


def status_for(
    target: Target,
    build_root: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> StatusReport:
    """Report the build stage for ``target`` by scanning its build log.

    A missing log means the build has not run yet: the stage is
    :data:`NOT_STARTED` (not an error — the target simply hasn't been
    built).  The build root resolves like the build step (``$PENGYOS_BUILD_ROOT``
    or ``~/build/pengyos`` when ``build_root`` is ``None``).
    """
    env = os.environ if env is None else env
    root = Path(build_root) if build_root is not None else default_build_root(env)
    log = build_log_path(root, target)
    text = log.read_text(encoding="utf-8") if log.is_file() else ""
    return StatusReport(target=target.name, stage=detect_stage(text), log=log)
