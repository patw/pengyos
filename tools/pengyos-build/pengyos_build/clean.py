"""Clean a PengyOS target's build output, scoped to the output dir only.

The clean step runs Buildroot's own ``clean`` target against the
target's per-target ``O=`` output dir::

    make -C <source> O=<output> BR2_EXTERNAL=<tree>[ BR2_BOARD=<board>] clean

Buildroot's ``clean`` removes the built artifacts in the ``O=`` dir (built
packages, staging, images) while **keeping ``.config``** — the target stays
configured, so ``build`` can be re-run without ``configure``.  Because every
path Buildroot writes with ``O=…`` lives inside the output dir, this
invocation is **scoped to the output dir only**: it never touches the
acquired Buildroot source tree (``buildroot-<version>``) and never touches
the synced repo (the ``BR2_EXTERNAL`` tree is only read for its
Makefile/config).

What clean writes, and what it writes is all:

* ``<out>/logs/clean.log`` — make's stdout and stderr captured **merged, in
  order** (stderr redirected onto the log handle); truncated on every run.
* ``<out>/.hostbin`` — the GNU-over-uutils host shim (hostshim.py), when the
  host needs it (re-created idempotently).

Design constraints (spec):

* clean **never touches the network** and never auto-acquires or auto-
  configures: a missing source tree is a :class:`CleanError` suggesting
  ``pengyos-build acquire <target>``; a missing external tree is a
  :class:`CleanError`; a target that has not been configured (no output
  dir) is a :class:`CleanError` suggesting
  ``pengyos-build configure <target>``;
* make not found, or a non-zero make exit, is a :class:`CleanError` naming
  the exit code and the captured log; the CLI exits 1;
* the child environment goes through
  :func:`pengyos_build.hostshim.run_build_with_host_shim` so Buildroot sees
  GNU coreutils even on a uutils host (SPEC §7 gotcha);
* the process runner is injectable (a ``subprocess.run``-like callable), so
  unit tests run hermetically with a fake make.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import hostshim
from .acquire import default_build_root, source_dir
from .configure import HOSTSHIM_DIRNAME, LOGS_SUBDIR, output_dir
from .targets import Target

#: Name of the clean log (lives next to ``configure.log``/``build.log``).
CLEAN_LOG = "clean.log"


class CleanError(RuntimeError):
    """The clean step failed (not configured, missing tree, make error)."""


@dataclass(frozen=True)
class CleanResult:
    """Outcome of a successful clean step.

    ``argv`` is the exact make invocation executed (the seed for the later
    ``--dry-run``); ``log`` is where make's merged stdout/stderr was captured.
    """

    target: str
    source: Path
    output: Path
    log: Path
    argv: tuple[str, ...]

    def __str__(self) -> str:  # convenient one-line summary for the CLI
        return f"cleaned {self.target}: log {self.log}"


#: A ``subprocess.run``-like callable: ``runner(argv, env=…, stdout=…,
#: stderr=…, text=…) -> CompletedProcess``.  Injectable so tests can fake
#: make without spawning a process.
Spawner = Callable[..., subprocess.CompletedProcess]


def clean_argv(
    source: Path | str, output: Path | str, target: Target
) -> tuple[str, ...]:
    """Exact make argv for the clean step.

    ``BR2_BOARD`` is included only when the target defines a board; the
    defconfig is NOT re-passed — ``clean`` acts on the already-configured
    output dir.
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
    argv.append("clean")
    return tuple(argv)


def clean_target(
    target: Target,
    build_root: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
    gnu_dir: Path | str = hostshim.DEFAULT_GNU_DIR,
    resolver: Callable[[str], str | None] = hostshim.default_resolver,
    runner: Spawner | None = None,
    dry_run: bool = False,
) -> CleanResult:
    """Run the clean make invocation for ``target``.

    The Buildroot source tree must already be acquired, the external tree
    present, and the target already configured; otherwise a
    :class:`CleanError` names the missing piece and the command that fixes
    it.  Raises :class:`CleanError` if make is not found or exits non-zero
    (the message names the exit code and the captured log).

    With ``dry_run=True`` the step resolves the exact make invocation and
    returns it WITHOUT running make: the same precondition checks apply,
    but no log is written and no host shim is applied.
    """
    env = os.environ if env is None else env
    root = Path(build_root) if build_root is not None else default_build_root(env)

    source = source_dir(root, target.br_version)
    if not source.is_dir():
        raise CleanError(
            f"Buildroot source tree not found at {source}; "
            f"run 'pengyos-build acquire {target.name}' first"
        )
    external = target.external_tree
    if not external.is_dir():
        raise CleanError(
            f"external tree not found at {external}; "
            "fetch it before cleaning"
        )

    output = output_dir(root, target)
    if not output.is_dir():
        raise CleanError(
            f"target '{target.name}' is not configured yet (no output dir at "
            f"{output}); run 'pengyos-build configure {target.name}' first"
        )

    argv = clean_argv(source, output, target)
    log = output / LOGS_SUBDIR / CLEAN_LOG
    if dry_run:
        # Print-only: no make, no log, no host shim.
        return CleanResult(
            target=target.name, source=source, output=output, log=log, argv=argv
        )
    shim_dir = output / HOSTSHIM_DIRNAME
    spawner = runner if runner is not None else _default_runner

    def _capturing(argv_: Sequence[str], env_: dict[str, str]) -> None:
        """Hostshim runner: run make with stdout+stderr merged into ``log``."""
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
                raise CleanError(
                    f"make not found on PATH "
                    f"({getattr(exc, 'filename', '') or 'make'}); "
                    "install build-essential"
                ) from exc
        if proc.returncode != 0:
            raise CleanError(
                f"clean failed (make exited {proc.returncode}); "
                f"see log: {log}"
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
    except CleanError:
        raise
    except FileNotFoundError as exc:
        raise CleanError(
            f"make not found on PATH ({getattr(exc, 'filename', '') or 'make'}); "
            "install build-essential"
        ) from exc

    return CleanResult(
        target=target.name, source=source, output=output, log=log, argv=argv
    )


def _default_runner(argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), check=False, **kwargs)
