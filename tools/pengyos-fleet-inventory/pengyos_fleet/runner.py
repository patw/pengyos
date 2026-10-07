"""The injectable runner seam: how ``pengyos-fleet`` reaches a host.

Fact collection never talks to a machine directly. Instead it goes through a
:class:`Runner`, which runs a command on a host and returns its captured output.
Two implementations are provided:

* :class:`SSHRunner` — the production transport. It shells out to ``ssh`` and
  runs the command on the remote host.
* :class:`MockRunner` — the deterministic, offline transport used by the test
  suite. It returns committed fixture output (see ``tests/fixtures/hosts``)
  instead of dialing anywhere.

The choice is made by name via :func:`make_runner`, which is what the CLI wires
to ``--runner ssh`` / ``--runner mock``. Everything is standard library only.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .hosts import Host


class RunnerError(RuntimeError):
    """Raised when a command cannot be run or its fixture cannot be read.

    Carries an actionable message (the offending command, the missing fixture,
    a timeout, a missing ``ssh`` binary, ...) so CLI errors are user-facing.
    """


@dataclass(frozen=True)
class RunResult:
    """The captured outcome of a single remote command.

    Attributes:
        args: The command that was run (the exact argv, minus the transport
            prefix). Recorded for diagnostics and for the mock runner's
            fixture lookup key.
        returncode: The command's exit status.
        stdout: Captured standard output.
        stderr: Captured standard error.
    """

    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(ABC):
    """Abstract transport: run a command on a host and capture its output."""

    #: Short name, used by :func:`make_runner` and surfaced in errors.
    name: str = "runner"

    @abstractmethod
    def run(self, host: Host, args: list[str]) -> RunResult:
        """Run ``args`` on ``host`` and return the captured :class:`RunResult`."""

        raise NotImplementedError


class SSHRunner(Runner):
    """Run commands on a host over SSH (the production transport).

    Wraps the ``ssh`` binary. The remote command is ``ssh <options>
    <address> -- <args...>``; the ``--`` guards against a remote command that
    begins with a dash being parsed as a local option.

    Args:
        ssh_bin: The SSH executable to invoke (default ``"ssh"``).
        timeout: Per-command wall-clock timeout in seconds.
        ssh_options: Extra ``ssh`` options (e.g. ``["-o", "StrictHostKeyChecking=no"]``).
        user: Remote user; when given the target is ``user@address``.
    """

    name = "ssh"

    def __init__(
        self,
        *,
        ssh_bin: str = "ssh",
        timeout: float = 30.0,
        ssh_options: list[str] | None = None,
        user: str | None = None,
    ) -> None:
        self.ssh_bin = ssh_bin
        self.timeout = timeout
        self.ssh_options = list(ssh_options or [])
        self.user = user

    def build_argv(self, host: Host, args: list[str]) -> list[str]:
        """Return the argv that would be executed, without running it.

        Exposed separately so the SSH-specific argument construction is unit
        testable without shelling out or touching the network.
        """

        argv: list[str] = [self.ssh_bin, *self.ssh_options, "--"]
        if self.user:
            argv.append(f"{self.user}@{host.address}")
        else:
            argv.append(host.address)
        argv.extend(args)
        return argv

    def run(self, host: Host, args: list[str]) -> RunResult:
        """Execute the command over SSH and capture its output."""

        argv = self.build_argv(host, args)
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise RunnerError(
                f"ssh binary {self.ssh_bin!r} not found; is SSH installed?"
            ) from exc
        except OSError as exc:
            raise RunnerError(f"failed to run ssh for {host.name!r}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RunnerError(
                f"command timed out after {self.timeout}s: {' '.join(args)}"
            ) from exc
        return RunResult(args, completed.returncode, completed.stdout, completed.stderr)


class MockRunner(Runner):
    """Return committed fixture output instead of dialing a host (offline).

    Each command maps to a fixture file named after the command's basename,
    inside the host's ``fixtures`` directory (e.g. ``lscpu`` ->
    ``<fixtures>/lscpu``). Missing fixtures are a hard error so a fixture drift
    fails loudly rather than silently producing an empty profile.

    Args:
        fixtures_root: Fallback directory for fixtures. Used only when a host
            has no ``fixtures`` path of its own; otherwise the host's own path
            wins.
    """

    name = "mock"

    def __init__(self, *, fixtures_root: str | None = None) -> None:
        self.fixtures_root = Path(fixtures_root) if fixtures_root else None

    def _fixture_dir(self, host: Host) -> Path:
        """Resolve the fixture directory for ``host``.

        Prefers the host's own ``fixtures`` path; falls back to a runner-level
        ``fixtures_root``. Raises :class:`RunnerError` when neither is set.
        """

        base = host.fixtures
        if not base and self.fixtures_root is not None:
            base = str(self.fixtures_root)
        if not base:
            raise RunnerError(
                f"mock runner: host {host.name!r} has no fixtures path "
                "(set 'fixtures' in the host list or pass fixtures_root)"
            )

        path = Path(base)
        if not path.is_absolute():
            path = path.resolve()
        return path

    def fixture_path(self, host: Host, args: list[str]) -> Path:
        """Return the fixture file a command maps to (``<dir>/<basename>``)."""

        if not args:
            raise RunnerError("mock runner: empty command")
        return self._fixture_dir(host) / Path(args[0]).name

    def run(self, host: Host, args: list[str]) -> RunResult:
        """Read the matching fixture file and return it as stdout."""

        path = self.fixture_path(host, args)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise RunnerError(
                f"mock runner: no fixture for {path} "
                f"(command: {' '.join(args)})"
            ) from exc
        except OSError as exc:
            raise RunnerError(f"mock runner: cannot read {path}: {exc}") from exc
        return RunResult(args, 0, text, "")


def make_runner(name: str, **kwargs) -> Runner:
    """Build a runner by name — ``ssh`` or ``mock``.

    Extra ``**kwargs`` are forwarded to the concrete runner's constructor.
    Unknown names raise :class:`RunnerError`.
    """

    key = name.strip().lower()
    if key == "ssh":
        return SSHRunner(**kwargs)
    if key == "mock":
        return MockRunner(**kwargs)
    raise RunnerError(
        f"unknown runner {name!r} (expected 'ssh' or 'mock')"
    )
