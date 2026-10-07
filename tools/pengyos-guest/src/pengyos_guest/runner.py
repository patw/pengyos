"""A tiny, mockable process runner.

Everything that touches the outside world (``podman``, ``distrobox``) goes
through a :class:`Runner`. Production uses :class:`SubprocessRunner`; tests swap
in :class:`FakeRunner` to assert on the exact argv that would be executed and
to avoid needing a real container runtime, network, or root.
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

    Subclasses implement :meth:`run`. Tests provide a fake that records argv
    and returns canned results.
    """

    #: Optional :class:`~pengyos_guest.sandbox.SandboxGuard` checked before each
    #: command runs. ``None`` (the default) disables the check.
    guard: object | None = None

    def run(
        self,
        argv: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        stream: bool = False,
    ) -> RunResult:  # pragma: no cover - interface
        """Run *argv*. ``stream=True`` inherits the terminal (stdin/stdout/
        stderr) instead of capturing: interactive shells and long
        provisioning runs need it."""
        raise NotImplementedError


class SubprocessRunner(Runner):
    """Execute *argv* via :func:`subprocess.run`."""

    def __init__(self, guard: object | None = None) -> None:
        self.guard = guard

    def run(
        self,
        argv: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        stream: bool = False,
    ) -> RunResult:  # pragma: no cover - needs a shell
        self._check(argv)
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=not stream,
                text=True,
                env=env,
            )
        except FileNotFoundError as exc:
            # The binary (podman/distrobox) is not installed / not on PATH.
            # Return a clean, non-zero result instead of crashing with a
            # traceback so callers can report it as a normal command failure.
            result = RunResult(
                argv=list(argv),
                returncode=127,
                stderr=f"{argv[0]!r} not found: install it and put it on PATH",
            )
            return result
        return RunResult(
            argv=list(argv),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def _check(self, argv: Sequence[str]) -> None:
        """Run the sandbox guard (if any) before executing *argv*."""
        guard = self.guard
        if guard is not None:
            guard.check(list(argv))


@dataclass
class FakeRunner(Runner):
    """In-memory runner for tests.

    Records every invocation in :attr:`calls` and returns results from a
    scripted :attr:`script` (matched by argv prefix) or a default result.
    """

    calls: list[list[str]] = field(default_factory=list)
    envs: list[dict[str, str] | None] = field(default_factory=list)
    script: list[RunResult] = field(default_factory=list)
    default: RunResult = field(default_factory=lambda: RunResult(argv=[], returncode=0))
    guard: object | None = None

    def respond(self, argv_prefix: Sequence[str], result: RunResult) -> None:
        """Queue *result* for any call whose argv starts with *argv_prefix*."""
        self.script.append((list(argv_prefix), result))

    @property
    def last_env(self) -> dict[str, str] | None:
        """The ``env`` passed to the most recent :meth:`run`, or ``None``."""
        return self.envs[-1] if self.envs else None

    #: ``stream`` flag of each call, parallel to :attr:`calls`.
    streams: list[bool] = field(default_factory=list)

    def run(
        self,
        argv: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        stream: bool = False,
    ) -> RunResult:
        argv = list(argv)
        self.streams.append(stream)
        if self.guard is not None:
            self.guard.check(argv)
        self.calls.append(argv)
        self.envs.append(env)
        for prefix, result in self.script:
            prefix = list(prefix)
            if argv[: len(prefix)] == prefix:
                return result
        return self.default
