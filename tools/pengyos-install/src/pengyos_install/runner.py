"""A tiny, mockable process runner.

Everything that touches the outside world (``parted``, ``mkfs``, ``dd``, ``efibootmgr``
...) goes through a :class:`Runner`. Production uses :class:`SubprocessRunner`;
tests swap in :class:`FakeRunner` to assert on the exact argv that would be
executed and to avoid needing root, real hardware, or a shell.

The install engine is defined entirely in terms of this interface, so the whole
plan can be executed, inspected, and torn down without a single syscall to the
host.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class RunResult:
    """The outcome of a single command invocation."""

    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner:
    """Interface for executing external commands.

    Subclasses implement :meth:`run`. Tests provide a fake that records argv and
    returns canned results.
    """

    #: Optional callable checked (raising on violation) before each command
    #: runs. ``None`` (the default) disables the check.
    guard: object | None = None

    def run(self, argv: Sequence[str], *, env: dict[str, str] | None = None) -> RunResult:  # pragma: no cover - interface
        raise NotImplementedError


class SubprocessRunner(Runner):
    """Execute *argv* via :func:`subprocess.run` (no shell)."""

    def __init__(self, guard: object | None = None) -> None:
        self.guard = guard

    def run(self, argv: Sequence[str], *, env: dict[str, str] | None = None) -> RunResult:  # pragma: no cover - needs a shell
        self._check(list(argv))
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError:
            # The binary is not installed / not on PATH. Return a clean, non-zero
            # result instead of crashing with a traceback so callers can report
            # it as a normal command failure.
            return RunResult(
                argv=list(argv),
                returncode=127,
                stderr=f"{argv[0]!r} not found: install it and put it on PATH",
            )
        return RunResult(
            argv=list(argv),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def _check(self, argv: list[str]) -> None:
        """Run the guard (if any) before executing *argv*."""
        guard = self.guard
        if guard is not None:
            guard.check(argv)


@dataclass
class FakeRunner(Runner):
    """In-memory runner for tests.

    Records every invocation in :attr:`calls` and returns results from a scripted
    :attr:`script` (matched by argv prefix) or a default result.
    """

    calls: list[list[str]] = field(default_factory=list)
    envs: list[dict[str, str] | None] = field(default_factory=list)
    script: list[tuple[list[str], RunResult]] = field(default_factory=list)
    default: RunResult = field(
        default_factory=lambda: RunResult(argv=[], returncode=0)
    )
    guard: object | None = None

    def respond(self, argv_prefix: Sequence[str], result: RunResult) -> None:
        """Queue *result* for any call whose argv starts with *argv_prefix*."""
        self.script.append((list(argv_prefix), result))

    @property
    def last_env(self) -> dict[str, str] | None:
        """The ``env`` passed to the most recent :meth:`run`, or ``None``."""
        return self.envs[-1] if self.envs else None

    @property
    def last_call(self) -> list[str] | None:
        """The argv of the most recent :meth:`run`, or ``None``."""
        return self.calls[-1] if self.calls else None

    def run(self, argv: Sequence[str], *, env: dict[str, str] | None = None) -> RunResult:
        argv = list(argv)
        if self.guard is not None:
            self.guard.check(argv)
        self.calls.append(argv)
        self.envs.append(env)
        for prefix, result in self.script:
            prefix = list(prefix)
            if argv[: len(prefix)] == prefix:
                return result
        return self.default


class WrapperRunner(Runner):
    """Run each argv through an external *prefix* command (the ``--runner`` CLI).

    ``--runner CMD`` injects a runner that prefixes every plan step with *CMD* —
    e.g. ``--runner echo`` traces the commands that *would* run (a dry run), and
    ``--runner sudo`` runs each step elevated. The prefix is split on whitespace
    (no shell), so the wrapped argv is ``[*prefix, *argv``] and is still executed
    without a shell by the underlying :class:`SubprocessRunner`.
    """

    def __init__(self, prefix: Sequence[str], runner: Runner | None = None) -> None:
        self.prefix = list(prefix)
        self.runner = runner or SubprocessRunner()

    def run(self, argv: Sequence[str], *, env: dict[str, str] | None = None) -> RunResult:
        wrapped = [*self.prefix, *argv]
        return self.runner.run(wrapped, env=env)
