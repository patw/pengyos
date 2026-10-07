"""The injectable command runner behind ``build`` (spec.md,
item 4).

``build`` (item 4) drives the plan's ordered build steps through a *runner*: a
callable that takes one step's ``argv`` and executes it, returning a
:class:`RunResult`. The runner is **injectable** — :func:`build` takes a
``runner=`` argument, so tests can substitute a side-effect-free recorder and
``build --dry-run`` never touches a shell at all.

Two runners ship:

* :func:`default_runner` — the real one. Runs ``argv`` with
  :func:`subprocess.run` (captured stdout/stderr, no ``check`` — a non-zero exit
  is reported by :func:`build`, not here) in ``cwd``.
* :class:`RecordingRunner` — a side-effect-free stand-in that records every
  argv it is handed and returns a configurable :class:`RunResult`. This is what
  the test suite injects; it never spawns a process, so the suite needs no
  network, root, or the real ``xorriso``/``mksquashfs`` tools.

The ``build`` CLI's ``--runner CMD`` flag wraps a runner around a command name
or path (see :func:`wrap_runner`): each step is executed as ``CMD <step argv>``.
That lets an operator point the build at a recorder, ``sudo``, a tracer, or
whatever without touching :mod:`pengyos_media.build`.

Stdlib only.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

#: A runner is a ``subprocess.run``-like callable: it takes one build step's
#: ``argv`` and returns a :class:`RunResult`. Injectable, so tests can fake any
#: external tool (``xorriso``/``mksquashfs``/…) without spawning a process. It
#: accepts optional keyword arguments (currently only ``cwd``), like
#: :func:`subprocess.run`, so :func:`pengyos_media.build.build` can hand it a
#: working directory without the runner caring.
Runner = Callable[..., "RunResult"]


@dataclass(frozen=True)
class RunResult:
    """The outcome of running one build step's ``argv``.

    ``argv`` is the exact invocation executed; ``returncode`` is the process
    exit status; ``stdout`` / ``stderr`` are the captured output. A step that
    never ran (e.g. in ``--dry-run``) has ``returncode = 0`` and empty output.
    """

    #: The exact argv that was executed (or would be, in a dry run).
    argv: list[str]
    #: The process exit status (``0`` when the step was not actually run).
    returncode: int = 0
    #: The captured standard output.
    stdout: str = ""
    #: The captured standard error.
    stderr: str = ""

    @property
    def ok(self) -> bool:
        """Whether the step exited successfully (``returncode == 0``)."""
        return self.returncode == 0


def default_runner(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
) -> RunResult:
    """Run ``argv`` for real with :func:`subprocess.run` (captured, not checked).

    This is the runner :func:`build` uses when the operator does not pass
    ``--runner``. It captures stdout/stderr and does **not** raise on a non-zero
    exit — :func:`build` decides what a non-zero exit means (a build failure).
    A missing binary raises :class:`FileNotFoundError`, which :func:`build`
    turns into a :class:`~pengyos_media.build.BuildError`.
    """
    proc = subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return RunResult(
        argv=list(argv),
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def wrap_runner(
    command: str,
    *,
    cwd: str | None = None,
    runner: Runner = default_runner,
) -> Runner:
    """Wrap ``runner`` so each step is executed as ``command <step argv>``.

    The ``--runner CMD`` override: ``command`` is a command name or path that
    prefixes every build step (e.g. ``sudo``, a recorder, or a tracer). The
    underlying ``runner`` defaults to :func:`default_runner`; inject a different
    one (a recorder) via ``runner=``. The returned callable is a :class:`Runner`
    — it takes a step's ``argv`` and returns a :class:`RunResult` whose ``argv``
    is the *full* ``[command, *argv]`` that was actually executed.
    """

    def _wrapped(step_argv: Sequence[str], **kwargs: Any) -> RunResult:
        return runner([command, *step_argv], **kwargs)

    return _wrapped


@dataclass
class RecordingRunner:
    """A side-effect-free :class:`Runner` that records every argv it runs.

    Use this in tests instead of :func:`default_runner`: it never spawns a
    process, so the suite needs no network, root, or the real build tools. Every
    argv is appended to :attr:`calls`; each step returns a
    :class:`RunResult` built from :attr:`result` (default: a clean exit).
    """

    #: The result returned for every step (mutate per step if you want to
    #: model a failing step — see :meth:`result_for`).
    result: RunResult = field(
        default_factory=lambda: RunResult(argv=[], returncode=0)
    )
    #: The argv of every step run so far (in order).
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> RunResult:
        self.calls.append(list(argv))
        return self.result

    def result_for(self, returncode: int, *, stdout: str = "", stderr: str = "") -> None:
        """Configure the :class:`RunResult` every subsequent step returns."""
        self.result = RunResult(argv=[], returncode=returncode, stdout=stdout, stderr=stderr)
