"""Wrap the ``podman`` binary behind a :class:`Runner`.

Everything that touches the outside world goes through the mockable
:class:`~pengyos_guest.runner.Runner`. Production uses
:class:`~pengyos_guest.runner.SubprocessRunner`; tests swap in
:class:`~pengyos_guest.runner.FakeRunner` to assert on the exact argv that would
 be executed and to avoid needing a real container runtime, network, or root.

This module builds the low-level ``podman`` command line (container
inspection / lifecycle). The higher-level ``distrobox`` guest commands live in
:class:`pengyos_guest.distrobox.Distrobox`.
"""

from __future__ import annotations

from typing import Sequence

from pengyos_guest.runner import Runner, RunResult, SubprocessRunner

BINARY = "podman"


class Podman:
    """A thin, argv-building wrapper around the ``podman`` CLI.

    Every method builds the exact argv for a ``podman`` subcommand and hands it
    to the injected :class:`Runner`. The argv is the contract tests assert on;
    the :class:`Runner` is what keeps this testable offline.
    """

    def __init__(self, runner: Runner | None = None) -> None:
        # Default to a real subprocess runner when none is injected, so
        # production callers get real execution and tests always pass one in.
        self._runner = runner if runner is not None else SubprocessRunner()

    @property
    def runner(self) -> Runner:
        return self._runner

    def _run(self, args: Sequence[str], *, env: dict[str, str] | None = None) -> RunResult:
        """Build ``podman <args>`` and execute it through the runner."""
        return self._runner.run([BINARY, *args], env=env)

    def inspect(self, name: str) -> RunResult:
        """``podman inspect <name>``.

        Return code 0 means the container exists; a non-zero code means it does
        not. See :meth:`container_exists`.
        """
        return self._run(["inspect", name])

    def container_exists(self, name: str) -> bool:
        """True if a container called *name* currently exists
        (``podman container exists``; ``inspect`` would also match an image of
        the same name)."""
        return self._run(["container", "exists", name]).returncode == 0

    def stop(self, name: str) -> RunResult:
        """``podman stop --ignore <name>`` (no error if already stopped)."""
        return self._run(["stop", "--ignore", name])

    def remove(self, name: str) -> RunResult:
        """``podman rm --ignore <name>`` (no error if already removed)."""
        return self._run(["rm", "--ignore", name])

    def run(self, args: Sequence[str], *, env: dict[str, str] | None = None) -> RunResult:
        """Raw ``podman <args>`` passthrough for anything not covered above."""
        return self._run(args, env=env)
