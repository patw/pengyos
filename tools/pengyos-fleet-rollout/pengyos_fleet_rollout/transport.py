"""The transport abstraction — how a rollout step reaches a host.

This is the transport subtask. It owns *how a single host is moved between two
artifacts*: the side-effect-free :class:`NoopTransport` (the default, the path
the test suite asserts on) and the :class:`SubprocessTransport` (shells out to a
command per host — production).

Every step goes through the same :class:`Transport` protocol, so ``apply`` and
``rollback`` (later subtasks) stay transport-agnostic: they build a
:class:`TransportStep` per host and call :meth:`Transport.run`, without knowing
whether the step was rendered as argv, executed by a subprocess, or dropped on
the floor by a fake.

The one thing the suite asserts on is the *dry-run* path: a transport renders the
exact argv it would run without ever invoking anything. That is the
side-effect-free contract the tests check.

Standard library only.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from .errors import RolloutTransportError

#: The action that rolls a host forward to the new artifact (apply).
ACTION_APPLY = "apply"
#: The action that rolls a host back to its prior artifact (rollback).
ACTION_ROLLED_BACK = "rollback"

#: The env var a :class:`SubprocessTransport` reads for the command to run per
#: host. An operator points it at their real transport; the test suite points it
#: at a fake script. When unset the transport falls back to
#: :data:`DEFAULT_TRANSPORT_CMD`.
TRANSPORT_CMD_ENV = "PENGYOS_ROLLOUT_TRANSPORT_CMD"

#: The command a :class:`SubprocessTransport` runs when :data:`TRANSPORT_CMD_ENV`
#: is unset. A deployment swaps this for its real transport binary.
DEFAULT_TRANSPORT_CMD = "pengyos-fleet-rollout-transport"

#: The env var a :class:`SubprocessTransport` reads for the working directory the
#: command should run in. Optional; unset means "inherit the parent's".
TRANSPORT_CWD_ENV = "PENGYOS_ROLLOUT_TRANSPORT_CWD"


@dataclass(frozen=True)
class TransportStep:
    """One transport operation: move a single host between two artifacts.

    Attributes:
        action: ``"apply"`` (move to ``new``) or ``"rollback"`` (restore
            ``prior``).
        host: The host name — the rollout target id.
        prior: The artifact the host currently has.
        new: The artifact the host should have after the step.
        address: The reachability target forwarded from the inventory, or
            ``None`` when unset.
    """

    action: str
    host: str
    prior: str
    new: str
    address: str | None = None


@dataclass(frozen=True)
class TransportResult:
    """The outcome of a :meth:`Transport.run` call.

    Attributes:
        ok: Whether the step succeeded. A failed step (non-zero exit, or a
            transport that reports failure) has ``ok=False`` — that is what a
            mid-rollout gate failure surfaces as.
        argv: The exact argv the step rendered (dry-run or otherwise). Always
            populated, so a dry-run and a real run are compared on the same axis.
        returncode: The subprocess exit code, or ``None`` when no subprocess was
            run (the dry-run / noop path).
        output: Captured stdout/stderr from the subprocess, or ``""``.
        prior: The artifact the transport observed the host already having,
            reported back to ``apply`` so it can record the *real* prior in the
            journal. ``None`` means the transport did not report one (the dry-run
            path); ``apply`` then falls back to the step's ``prior``.
    """

    ok: bool
    argv: tuple[str, ...]
    returncode: int | None = None
    output: str = ""
    prior: str | None = None


@runtime_checkable
class Transport(Protocol):
    """The injectable transport contract.

    A transport moves one :class:`TransportStep` and reports the result. ``apply``
    and ``rollback`` build a step per host and call :meth:`run` — they never
    touch a machine directly, so the transport is swappable (noop / subprocess /
    a fake in a test) behind the same interface.
    """

    def run(self, step: TransportStep) -> TransportResult:
        """Move ``step``'s host and report the :class:`TransportResult`."""
        ...


def build_argv(
    step: TransportStep,
    command: str = DEFAULT_TRANSPORT_CMD,
    *,
    include_address: bool = True,
) -> tuple[str, ...]:
    """Render the exact argv a subprocess transport would run for ``step``.

    The argv is deterministic and inspectable — the dry-run path renders it and
    the suite asserts on it:

    ``[command, action, host, prior, new]`` with ``--address <address>`` appended
    when ``step.address`` is set (and ``include_address`` is true).

    Args:
        step: The step to render.
        command: The transport command / binary the argv is built around.
        include_address: Whether to append ``--address <address>`` when the step
            carries an address.
    """

    argv: list[str] = [command, step.action, step.host, step.prior, step.new]
    if include_address and step.address:
        argv += ["--address", step.address]
    return tuple(argv)


class NoopTransport:
    """The side-effect-free transport (the default, and the test path).

    It renders the argv a subprocess transport would run and reports success
    without ever invoking anything. ``apply``/``rollback`` default to this, so a
    dry run performs no transport call.
    """

    def __init__(
        self,
        command: str = DEFAULT_TRANSPORT_CMD,
        *,
        include_address: bool = True,
    ) -> None:
        self.command = command
        self.include_address = include_address

    def run(self, step: TransportStep) -> TransportResult:
        """Render ``step``'s argv and report a successful, no-op result.

        The noop transport has no host to read, so it reports the step's own
        ``prior`` as the observed prior — the dry-run path echoes what it was
        asked to move from. A real transport reports the artifact it observed.
        """

        argv = build_argv(step, self.command, include_address=self.include_address)
        return TransportResult(
            ok=True, argv=argv, returncode=None, output="", prior=step.prior
        )


class SubprocessTransport:
    """A subprocess-backed transport: run a command per host.

    The command is read from :data:`TRANSPORT_CMD_ENV`
    (``PENGYOS_ROLLOUT_TRANSPORT_CMD``) when set, else
    :data:`DEFAULT_TRANSPORT_CMD`. The argv is :func:`build_argv` — the command,
    the action, the host, the prior and new artifacts, and ``--address`` when
    present — and it is run in the directory given by
    :data:`TRANSPORT_CWD_ENV` (``PENGYOS_ROLLOUT_TRANSPORT_CWD``) when set.

    A non-zero exit code surfaces as a failed :class:`TransportResult` (``ok``
    false) — the mid-rollout gate failure a rollout halts on.
    """

    def __init__(self, env: Mapping | None = None) -> None:
        self._env = os.environ if env is None else env

    def _command(self) -> str:
        return self._env.get(TRANSPORT_CMD_ENV, DEFAULT_TRANSPORT_CMD)

    def _cwd(self) -> str | None:
        return self._env.get(TRANSPORT_CWD_ENV)

    def run(self, step: TransportStep) -> TransportResult:
        """Run the transport command for ``step`` and report the result."""

        argv = build_argv(step, self._command())
        try:
            completed = subprocess.run(
                argv,
                cwd=self._cwd(),
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise RolloutTransportError(
                f"cannot run transport {argv[0]!r}: {exc}"
            ) from exc

        ok = completed.returncode == 0
        return TransportResult(
            ok=ok,
            argv=argv,
            returncode=completed.returncode,
            output=(completed.stdout or "") + (completed.stderr or ""),
        )


def make_transport(name: str) -> Transport:
    """Build a transport by name — the factory the CLI dispatches on.

    ``"noop"`` (the default) and ``"subprocess"`` are the two transports;
    anything else raises :class:`RolloutTransportError`.
    """

    if name == "noop":
        return NoopTransport()
    if name == "subprocess":
        return SubprocessTransport()
    raise RolloutTransportError(f"unknown transport {name!r}")
